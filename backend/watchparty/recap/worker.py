"""The recap job (#15): renders all five managers' recaps for one session.

On Azure a Container Apps Job runs this whenever a message is on the `recaps` queue: it takes one message,
renders the five videos in parallel, uploads them, writes done.json last, then deletes the message. A
message that keeps failing is dropped after a few tries rather than retried forever.

  python -m watchparty.recap.worker                       # Azure: RECAP_STORAGE_ACCOUNT, managed identity
  python -m watchparty.recap.worker --local in.json.gz out/   # a recorded session, no Azure
"""

import gzip
import json
import multiprocessing
import os
import sys
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any

from opentelemetry import trace

from watchparty import telemetry
from watchparty.recap.bundle import check
from watchparty.recap.plan import plan
from watchparty.recap.render import render
from watchparty.recap.storage import CONTAINER, QUEUE, done_blob, input_blob, video_blob

MAX_TRIES = 3
# If this run dies, the message comes back for another try after this long. A run takes 60 to 100 s, and the
# app waits 600 s for the links (dispatch.py), so one retry still reaches the phones (#29). The job's
# replicaTimeout is the same, so a run that hangs stops as its retry becomes visible (infra/main.bicep).
VISIBLE_AFTER_S = 240
tracer = trace.get_tracer("watchparty.recap")


def _render_one(bundle: dict[str, Any], manager: str, out: Path) -> Path:
    path = out / f"{manager}.mp4"
    render(bundle, plan(bundle, manager), path)
    return path


def render_all(bundle: dict[str, Any], out: Path) -> dict[str, Path]:
    check(bundle)  # 1 a single match, 2 a gameweek (#94); a damaged one fails here, saying what is wrong
    managers = [m["id"] for m in bundle["league"]["managers"]]
    # spawn, as on macOS already: Linux's default fork would copy a process whose trace exporter has threads
    # running, which can deadlock the child (#111).
    spawn = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=min(len(managers), os.cpu_count() or 1), mp_context=spawn) as pool:
        paths = list(pool.map(_render_one, [bundle] * len(managers), managers, [out] * len(managers)))
    return dict(zip(managers, paths, strict=True))


def run_azure(account: str) -> int:
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient, ContentSettings
    from azure.storage.queue import QueueClient

    credential = DefaultAzureCredential()
    queue = QueueClient(f"https://{account}.queue.core.windows.net", QUEUE, credential=credential)
    blobs = BlobServiceClient(f"https://{account}.blob.core.windows.net", credential=credential)
    upload = partial(ContentSettings, content_type="video/mp4")
    return process(queue, blobs.get_container_client(CONTAINER), upload)


def process(queue: Any, container: Any, video_settings: Callable[[], Any]) -> int:
    """Take one message off the queue and render its session's recaps. Returns the job's exit code."""
    message = queue.receive_message(visibility_timeout=VISIBLE_AFTER_S)
    if message is None:
        print("no recap waiting")
        return 0
    if (message.dequeue_count or 0) > MAX_TRIES:  # before parsing: a message that cannot parse must still go
        queue.delete_message(message)
        print(f"dropped a message after {MAX_TRIES} tries", file=sys.stderr)
        return 1
    try:
        session_id = json.loads(message.content)["session_id"]
        input_blob(session_id)  # a session id, or ValueError
    except (ValueError, KeyError, TypeError):  # no retry will ever parse it (#29)
        queue.delete_message(message)
        print("dropped a malformed message", file=sys.stderr)
        return 1
    # One span for the whole render (#111): the storage and queue calls are not traced on their own. Its error,
    # if any, carries the exception's type only.
    attributes = {"session_id": session_id, "try": message.dequeue_count or 1}
    with telemetry.span(tracer, "recap_render", attributes) as span:
        done = _render_and_upload(container, video_settings, session_id)
        span.set_attributes({"managers": len(done["managers"]), "render_s": done["render_s"],
                             "total_s": done["total_s"]})  # fmt: skip
    queue.delete_message(message)
    print(f"recaps for {session_id}: {done}")
    return 0


def _render_and_upload(container: Any, video_settings: Callable[[], Any], session_id: str) -> dict[str, Any]:
    start = time.perf_counter()
    bundle = json.loads(gzip.decompress(container.download_blob(input_blob(session_id)).readall()))
    if bundle["session_id"] != session_id:
        raise ValueError("the bundle is for another session")
    with tempfile.TemporaryDirectory() as tmp:
        paths = render_all(bundle, Path(tmp))
        rendered = time.perf_counter() - start
        for manager, path in paths.items():
            with path.open("rb") as video:
                container.upload_blob(video_blob(session_id, manager), video, overwrite=True,
                                      content_settings=video_settings())  # fmt: skip
    done = {"managers": list(paths), "render_s": round(rendered, 1), "total_s": round(time.perf_counter() - start, 1)}
    container.upload_blob(done_blob(session_id), json.dumps(done), overwrite=True)  # last: the app waits for it
    return done


def main(argv: list[str]) -> int:
    telemetry.configure("watchparty-recap", agents=False)
    try:
        return _run(argv)
    finally:
        telemetry.flush()  # the job exits right after: send the render's span first


def _run(argv: list[str]) -> int:
    if argv[:1] == ["--local"] and len(argv) == 3:
        bundle = json.loads(gzip.decompress(Path(argv[1]).read_bytes()))
        out = Path(argv[2])
        out.mkdir(parents=True, exist_ok=True)
        start = time.perf_counter()
        paths = render_all(bundle, out)
        print(f"rendered {len(paths)} recaps in {time.perf_counter() - start:.1f} s into {out}")
        return 0
    return run_azure(os.environ["RECAP_STORAGE_ACCOUNT"])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
