"""Records every screen's messages from one demo match with stub agents, to hold the single match path still (#149).

uv run python -m tools.golden_log ../out.json.gz

It uses only what develop's Session already had (the constructor, subscribe and run), so the same file runs on
an older tree to record the log a change must keep. tests/test_golden_log.py compares this tree's log with the
one recorded from develop, tests/golden/demo_log.json.gz.
"""

import asyncio
import gzip
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from watchparty.agents.host import true_factors
from watchparty.app.session import Session
from watchparty.sim import DEMO_SEED

SCREENS = ("group", *(f"phone:mgr_{n}" for n in range(1, 6)))


class StubManager:
    """Answers at once from the stake's own swing; declines a moment worth 0 IP, as the real agent may (#88)."""

    async def bid(self, stake: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        if not stake["delta_points"]:
            return {"status": "declined"}
        delta = next(e for e in stake["evidence"] if e["id"].endswith("_delta"))
        return {"urgency": 0.7, "explanation": f"That is {delta['value']:g} IP for you.", "explanation_en": "",
                "pitch_to_host": "Show my moment.", "evidence_ids": [delta["id"]]}  # fmt: skip


class StubHost:
    """Overrules the scorer at once: the last candidate with a fact true of it."""

    async def decide(self, moment: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
        chosen = [c["manager"] for c in candidates if true_factors(c["manager"], candidates)][-1]
        because = sorted(true_factors(chosen, candidates))[0]
        return {"chosen_manager": chosen, "because": because, "reason": "Close call; fair turn."}


async def settle(_: float) -> None:
    """Stands in for waiting out match time. Lets every task that can run do so until none can, so each key
    moment is decided before play goes on, as at real speed: moments are 10 s apart and a decision takes at most
    4.5 s. The stubs answer at once, so nothing is left waiting on a timer."""
    loop = asyncio.get_running_loop()
    await asyncio.sleep(0)
    while loop._ready:  # type: ignore[attr-defined]  # CPython's queue of callbacks ready to run
        await asyncio.sleep(0)


def _steady(message: dict[str, Any]) -> dict[str, Any]:
    """Timings differ from run to run; everything else must not."""
    if message["type"] != "decision":
        return message
    data = message["data"] | {"latency_ms": 0}
    data["scores"] = [row | {"latency_ms": 0} for row in data["scores"]]
    return message | {"data": data}


async def record(seed: int = DEMO_SEED) -> dict[str, list[dict[str, Any]]]:
    """Every screen's messages, in the order the screen got them."""
    session = Session(seed, "mgr_1", "casual", "en", sleep=settle, manager_agent=StubManager(),
                      host_agent=StubHost())  # fmt: skip
    queues = {screen: session.subscribe(screen) for screen in SCREENS}
    await session.run()
    out: dict[str, list[dict[str, Any]]] = {}
    for screen, q in queues.items():
        out[screen] = []
        while not q.empty():
            message = q.get_nowait()
            if message is not None:
                out[screen].append(_steady(message))
    return out


def compact(log: dict[str, list[dict[str, Any]]]) -> dict[str, list[Any]]:
    """The log as stored: every frame by its digest (they are most of the bytes), every other message whole."""
    return {screen: [hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()[:16] if m["type"] == "frame"
                     else m for m in messages] for screen, messages in log.items()}  # fmt: skip


async def main(out: Path) -> None:
    log = compact(await record())
    out.write_bytes(gzip.compress(json.dumps(log, sort_keys=True).encode(), mtime=0))
    counts = {screen: len(messages) for screen, messages in log.items()}
    print(f"wrote {out} ({out.stat().st_size // 1024} KB): {counts}")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
