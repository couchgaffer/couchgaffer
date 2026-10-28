import asyncio
import copy
import gzip
import inspect
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from watchparty.app.session import Session
from watchparty.recap import worker
from watchparty.recap.bundle import make_bundle
from watchparty.recap.plan import MAX_CLIPS, plan

pytestmark = pytest.mark.anyio


async def no_sleep(_: float) -> None:
    await asyncio.sleep(0)


BUNDLES: dict[str, dict[str, Any]] = {}


@pytest.fixture(scope="module")
def played() -> Session:
    async def at_full_time(session: Session) -> None:
        BUNDLES[session.id] = make_bundle(session)  # where the app makes it: frames go once a match is over (#49)

    s = Session(seed=267, manager="mgr_3", style="casual", lang="en", speed=30, sleep=no_sleep,
                on_full_time=at_full_time)  # fmt: skip
    asyncio.run(s.run())
    return s


@pytest.fixture(scope="module")
def bundle(played: Session) -> dict[str, Any]:
    return BUNDLES[played.id]


def test_the_bundle_is_plain_json_with_everything_the_job_needs(bundle: dict[str, Any]) -> None:
    again = json.loads(gzip.decompress(gzip.compress(json.dumps(bundle).encode())))
    assert again == bundle
    assert {"v", "session_id", "meta", "league", "frames", "climaxes", "stakes", "log", "table"} <= set(bundle)
    assert len(bundle["frames"]) > 1000
    assert all(e["climax"] for e in bundle["climaxes"])


@pytest.mark.parametrize("manager", [f"mgr_{n}" for n in range(1, 6)])
def test_each_recap_holds_only_that_managers_moments_in_match_order(bundle: dict[str, Any], manager: str) -> None:
    recap = plan(bundle, manager)
    mattered = {s["moment_id"] for m in bundle["stakes"] for s in m if s["manager"] == manager and s["above_threshold"]}
    won = {d["moment_id"] for d in bundle["log"] if d.get("chosen_manager") == manager}
    assert 1 <= len(recap.clips) <= MAX_CLIPS
    assert {c.moment_id for c in recap.clips} <= mattered | won
    assert [c.climax_t for c in recap.clips] == sorted(c.climax_t for c in recap.clips)


def test_the_biggest_swings_come_first_then_the_group_screen_wins(bundle: dict[str, Any]) -> None:
    for manager in (f"mgr_{n}" for n in range(1, 6)):
        stakes = [s for m in bundle["stakes"] for s in m if s["manager"] == manager and s["above_threshold"]]
        kept = {c.moment_id for c in plan(bundle, manager).clips}
        biggest = sorted(stakes, key=lambda s: -abs(s["delta_points"]))[:MAX_CLIPS]
        assert {s["moment_id"] for s in biggest} <= kept


def test_captions_are_that_managers_own_phone_text_and_the_end_card_is_their_summary(
    bundle: dict[str, Any], played: Session
) -> None:
    recap = plan(bundle, "mgr_3")
    phone = [c for c in played.log if c.get("screen") == "phone:mgr_3"]
    for clip in recap.clips:
        moment = [c for c in phone if c.get("moment_id") == clip.moment_id]
        texts = {c["payload"].get("body") or c["payload"]["headline"] for c in moment}
        assert clip.caption in texts
    summary = next(c for c in phone if c.get("kind") == "summary" and c["payload"]["headline"] == "Full time")
    assert recap.end_card == summary["payload"]["body"]
    assert (recap.name, recap.league_name) == ("You", "Sunday Sofa League")
    final = next(r for r in bundle["table"] if r["manager"] == "mgr_3")
    assert (recap.rank, recap.points) == (final["rank"], final["points"])


@pytest.mark.parametrize(("manager", "lang"), [("mgr_4", "es"), ("mgr_1", "en")])
def test_the_end_card_is_the_full_time_summary_in_every_language(
    bundle: dict[str, Any], played: Session, manager: str, lang: str
) -> None:
    # The phone translates the summary's title (#119); the backend keeps "Full time", which is how plan() finds it.
    recap = plan(bundle, manager)
    phone = [c for c in played.log if c.get("screen") == f"phone:{manager}" and c.get("kind") == "summary"]
    summary = next(c for c in phone if c["payload"]["headline"] == "Full time")
    assert (recap.lang, summary["payload"]["lang"]) == (lang, lang)
    assert recap.end_card == summary["payload"]["body"]


@pytest.mark.parametrize(("manager", "shown"), [("mgr_4", "+2,5 IP"), ("mgr_1", "+2.5 IP")])
def test_a_clips_points_are_written_in_the_managers_language(bundle: dict[str, Any], manager: str, shown: str) -> None:
    halves = copy.deepcopy(bundle)
    stakes = [s for m in halves["stakes"] for s in m if s["manager"] == manager and s["above_threshold"]]
    for stake in stakes:
        stake["delta_points"] = 2.5
    staked = [c for c in plan(halves, manager).clips if c.moment_id in {s["moment_id"] for s in stakes}]
    assert staked
    assert {c.points for c in staked} == {shown}


def test_each_clip_is_a_window_around_its_climax(bundle: dict[str, Any]) -> None:
    for clip in plan(bundle, "mgr_2").clips:
        assert clip.start_t < clip.climax_t < clip.end_t
        assert clip.headline
        assert clip.points.endswith("IP")


# Skipped on a laptop without ffmpeg, never in CI: there a missing ffmpeg fails the render tests (#29).
ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None and not os.environ.get("CI"), reason="no ffmpeg")


@ffmpeg
def test_a_recap_renders_to_a_vertical_h264_mp4_of_twenty_to_thirty_seconds(
    bundle: dict[str, Any], tmp_path: Path
) -> None:
    from watchparty.recap.render import render

    out = tmp_path / "mgr_2.mp4"
    render(bundle, plan(bundle, "mgr_2"), out)
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    probe = json.loads(subprocess.run(  # noqa: S603
        [ffprobe, "-v", "error", "-show_entries", "stream=codec_name,width,height:format=duration", "-of", "json",
         str(out)], check=True, capture_output=True, text=True).stdout)  # fmt: skip
    stream = probe["streams"][0]
    assert (stream["codec_name"], stream["width"], stream["height"]) == ("h264", 1080, 1920)
    assert 20 <= float(probe["format"]["duration"]) <= 30


# What the video drew in English before #71; none of it may reach a Spanish recap ("Final: 2nd" is the ordinal).
ENGLISH = ["Your night", "'s night", "Moment ", "Your recap", "mattered to you", "Full time", "Invented clubs"]
ORDINAL_EN = re.compile(r"\b\d+(st|nd|rd|th)\b")


def drawn(monkeypatch: pytest.MonkeyPatch, bundle: dict[str, Any], manager: str) -> list[str]:
    """Every string the title card, each moment card and the end card draw for one manager."""
    from PIL import ImageDraw

    from watchparty.recap import render

    texts: list[str] = []
    real = ImageDraw.ImageDraw.text

    def text(self: ImageDraw.ImageDraw, xy: Any, s: str, *args: Any, **kwargs: Any) -> None:
        texts.append(s)
        real(self, xy, s, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", text)
    recap = plan(bundle, manager)
    render.title_card(recap)
    pitch = render._pitch()
    for n, clip in enumerate(recap.clips, start=1):
        render._clip_base(pitch, recap, clip, n)
    render.end_card(recap)
    return texts


def test_a_spanish_managers_recap_is_drawn_in_spanish(bundle: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    recap = plan(bundle, "mgr_4")
    assert (recap.name, recap.lang) == ("Leo", "es")
    texts = drawn(monkeypatch, bundle, "mgr_4")
    total = len(recap.clips)
    assert texts.count("La noche de Leo") == total + 2
    assert [t for t in texts if t.startswith("Momento ")] == [
        f"Momento {n} de {total} · {c.minute}'" for n, c in enumerate(recap.clips, start=1)]  # fmt: skip
    points = f"{recap.points:g}".replace(".", ",")  # 283,5 as the Spanish summaries write it
    assert texts.count(f"Final: {recap.rank}.º puesto · {points} IP") == 2
    assert {"Tu resumen", "Final del partido", "Clubes, jugadores y partido inventados. Juego gratuito."} <= set(texts)
    assert f"{total} momentos que te importaron." in texts
    # Exempt: each moment card's grey headline. It is the group screen's stat card, one text shared by all five
    # managers and English by design, not a label of this video. Everything else drawn is checked.
    from PIL import Image, ImageDraw

    from watchparty.recap import render

    draw = ImageDraw.Draw(Image.new("RGB", (render.W, render.H)))
    headlines = [c.headline for c in recap.clips if c.headline]  # an empty one is never exempt
    assert headlines
    exempt = {line for h in headlines for line in render._lines(draw, h, 40, render.W - 160, False, 2)}  # as drawn
    assert "" not in exempt
    assert exempt <= set(texts)  # every headline line was drawn
    ours = [t for t in texts if t not in exempt]
    assert not [t for t in ours if any(e in t for e in ENGLISH) or ORDINAL_EN.search(t)]
    assert not [t for t in ours if re.search(r"\d\.\d", t)]  # 2,5 IP, never 2.5 IP


def test_an_english_managers_recap_is_drawn_as_before(bundle: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    recap = plan(bundle, "mgr_1")
    texts = drawn(monkeypatch, bundle, "mgr_1")
    total = len(recap.clips)
    place = f"{recap.rank}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(recap.rank, 'th') }"
    assert texts.count("Jo's night") == total + 2
    assert [t for t in texts if t.startswith("Moment ")] == [
        f"Moment {n} of {total} · {c.minute}'" for n, c in enumerate(recap.clips, start=1)]  # fmt: skip
    assert texts.count(f"Final: {place} · {recap.points:g} IP") == 2
    assert {"Your recap", "Full time", "Invented clubs, players and match. Free to play."} <= set(texts)
    assert f"{total} moment{'s' if total != 1 else ''} that mattered to you." in texts


@pytest.mark.parametrize(("size", "max_lines"), [(46, 8), (52, 12)])  # a moment caption, an end card
def test_a_caption_at_the_length_limit_stays_inside_its_box(size: int, max_lines: int) -> None:
    from PIL import Image, ImageDraw

    from watchparty.recap import render

    draw = ImageDraw.Draw(Image.new("RGB", (render.W, render.H)))
    width = render.W - 160
    words = "Quickstrike Wanderers grabbed a late equaliser and the keeper's double save swung it "
    cases = [((words * 4)[:280], max_lines),  # the analyst limit
             (words * 12, max_lines),  # far past it: the last line is cut
             ("w w " + "Z" * 300, 2),  # one word wider than the box, on the cut line
             ("Z" * 300 + " end", 40)]  # and on lines that are never cut  # fmt: skip
    for text, most in cases:
        lines = render._lines(draw, text, size, width, False, most)
        assert len(lines) <= most
        assert max(draw.textlength(line, font=render.font(size)) for line in lines) <= width, text[:20]
    assert render._lines(draw, words * 12, size, width, False, max_lines)[-1].endswith("…")
    broken = render._lines(draw, "Z" * 300 + " end", size, width, False, 40)
    assert " ".join(broken).replace(" ", "") == "Z" * 300 + "end"  # broken by characters, nothing lost


class FakeRecaps:
    """Stands in for blob storage and the job: links appear after `ready_after` polls. The first `broken` polls
    raise, or every one with -1; with a `session`, each poll records how many frames it still held."""

    def __init__(self, ready_after: int = 2, fail: bool = False, broken: int = 0) -> None:
        self.ready_after, self.fail, self.broken, self.polls, self.tries = ready_after, fail, broken, 0, 0
        self.submitted: list[tuple[str, dict[str, Any]]] = []
        self.session: Session | None = None
        self.frames: list[int] = []

    async def submit(self, session_id: str, bundle: dict[str, Any]) -> None:
        self.tries += 1
        if self.fail:
            raise ConnectionError("storage is down")
        self.submitted.append((session_id, bundle))

    async def links(self, session_id: str, managers: list[str]) -> dict[str, str] | None:
        self.polls += 1
        if self.session:
            self.frames.append(len(self.session.match.frames))
        if self.broken == -1 or self.polls <= self.broken:
            raise TimeoutError("blob storage timed out")
        if self.polls < self.ready_after:
            return None
        return {m: f"https://example.blob.core.windows.net/recaps/{session_id}/{m}.mp4?sig=x" for m in managers}


async def test_at_full_time_the_bundle_goes_out_and_each_phone_gets_its_own_link(
    contract_errors: Any,
) -> None:
    from tests.test_session import Host, Manager
    from watchparty.recap.dispatch import publish_recaps

    recaps = FakeRecaps()
    s = Session(seed=267, manager="mgr_1", style="casual", lang="en", speed=30, sleep=no_sleep,
                manager_agent=Manager(), host_agent=Host(),  # recaps are for matches with agents (#50)
                on_full_time=lambda session: publish_recaps(recaps, session, poll_s=0))  # fmt: skip
    await s.run()
    [(session_id, bundle)] = recaps.submitted
    assert session_id == s.id
    assert sum(c.get("kind") == "summary" for c in bundle["log"]) == 10  # the end cards were in before it went
    ready = [c for c in s.log if c.get("kind") == "recap_ready"]
    assert sorted(c["screen"] for c in ready) == [f"phone:mgr_{n}" for n in range(1, 6)]
    for cue in ready:
        assert cue["payload"]["recap_url"].endswith(f"/{cue['screen'].split(':')[1]}.mp4?sig=x")
        assert contract_errors("cue", cue) == []


@pytest.mark.parametrize("recaps", [FakeRecaps(fail=True), FakeRecaps(ready_after=10_000)])
async def test_a_recap_that_fails_or_never_arrives_costs_nothing_but_the_link(recaps: FakeRecaps) -> None:
    from tests.test_session import Host, Manager
    from watchparty.recap.dispatch import publish_recaps

    s = Session(seed=267, manager="mgr_1", style="casual", lang="en", speed=30, sleep=no_sleep,
                manager_agent=Manager(), host_agent=Host(),  # a code-only match never asks for one (#57)
                on_full_time=lambda session: publish_recaps(recaps, session, poll_s=0, timeout_s=0.05))  # fmt: skip
    await s.run()
    assert recaps.tries == 1
    assert (recaps.polls > 0) is not recaps.fail  # never ready: polled until the timeout
    assert not [c for c in s.log if c.get("kind") == "recap_ready"]
    assert s.finished_at is not None


def agent_match(recaps: FakeRecaps, timeout_s: float = 600) -> Session:
    from tests.test_session import Host, Manager
    from watchparty.recap.dispatch import publish_recaps

    return Session(seed=267, manager="mgr_1", style="casual", lang="en", speed=30, sleep=no_sleep,
                   manager_agent=Manager(), host_agent=Host(),  # recaps are for matches with agents (#50)
                   on_full_time=lambda s: publish_recaps(recaps, s, poll_s=0, timeout_s=timeout_s))  # fmt: skip


async def test_one_failed_poll_still_gets_the_links_to_the_phones(caplog: pytest.LogCaptureFixture) -> None:
    recaps = FakeRecaps(ready_after=3, broken=1)
    s = agent_match(recaps)
    with caplog.at_level(logging.WARNING, logger="watchparty.recap.dispatch"):
        await s.run()
    assert recaps.polls == 3
    ready = [c for c in s.log if c.get("kind") == "recap_ready"]
    assert sorted(c["screen"] for c in ready) == [f"phone:mgr_{n}" for n in range(1, 6)]
    [record] = [r for r in caplog.records if r.name == "watchparty.recap.dispatch"]
    assert (record.levelno, record.getMessage()) == (logging.ERROR, f"recap links for {s.id} failed, polling again")
    assert record.exc_info
    assert isinstance(record.exc_info[1], TimeoutError)


async def test_links_that_always_fail_give_up_at_the_timeout_with_one_error_then_debug_lines(
    caplog: pytest.LogCaptureFixture,
) -> None:
    recaps = FakeRecaps(broken=-1)
    s = agent_match(recaps, timeout_s=0.05)
    with caplog.at_level(logging.DEBUG, logger="watchparty.recap.dispatch"):
        await s.run()
    records = [r for r in caplog.records if r.name == "watchparty.recap.dispatch"]
    failed = [r.levelno for r in records if r.getMessage().endswith("failed, polling again")]
    assert recaps.polls > 1
    assert failed == [logging.ERROR] + [logging.DEBUG] * (recaps.polls - 1)  # one traceback, not one per poll
    assert records[-1].getMessage() == f"recaps for {s.id} not ready after 0 s, {recaps.polls} polls failed"
    assert not [c for c in s.log if c.get("kind") == "recap_ready"]
    assert s.finished_at is not None


async def test_the_frames_go_once_the_bundle_is_submitted_not_after_the_wait() -> None:
    recaps = FakeRecaps(ready_after=3)
    s = agent_match(recaps)
    recaps.session = s
    await s.run()
    [(_, bundle)] = recaps.submitted
    assert len(bundle["frames"]) > 1000  # the job got them
    assert recaps.frames == [0, 0, 0]  # and the session held none while it waited (#71)


class FakeBlob:
    def __init__(self, store: dict[str, bytes], name: str) -> None:
        self.store, self.name = store, name
        self.url = f"https://stwp.blob.core.windows.net/recaps/{name}"

    def exists(self) -> bool:
        return self.name in self.store


class FakeContainer:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    def upload_blob(self, name: str, data: bytes, overwrite: bool = False) -> None:
        self.store[name] = data

    def get_blob_client(self, name: str) -> FakeBlob:
        return FakeBlob(self.store, name)


class FakeBlobService:
    account_name = "stwp"

    def __init__(self) -> None:
        self.container, self.keys = FakeContainer(), 0

    def get_container_client(self, name: str) -> FakeContainer:
        assert name == "recaps"
        return self.container

    def get_user_delegation_key(self, start: Any, expiry: Any) -> Any:
        from azure.storage.blob import UserDelegationKey

        self.keys += 1
        key = UserDelegationKey()  # type: ignore[no-untyped-call]
        key.signed_oid, key.signed_tid, key.signed_service, key.signed_version = "oid", "tid", "b", "2025-01-05"
        key.signed_start, key.signed_expiry = start.isoformat(), expiry.isoformat()
        key.value = "c2VjcmV0c2VjcmV0c2VjcmV0c2VjcmV0"  # base64 of a fake key
        return key


class FakeQueue:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send_message(self, content: str) -> None:
        self.sent.append(content)


SID = "Ab3_dEf-GhIjKlMnOp"


async def test_storage_gets_the_bundle_and_one_queue_message_per_session() -> None:
    from watchparty.recap.storage import AzureRecaps

    blobs, queue = FakeBlobService(), FakeQueue()
    await AzureRecaps(blobs, queue).submit(SID, {"v": 1, "session_id": SID})
    assert json.loads(gzip.decompress(blobs.container.store[f"{SID}/input.json.gz"])) == {"v": 1, "session_id": SID}
    assert [json.loads(m) for m in queue.sent] == [{"session_id": SID}]


async def test_the_bundle_is_packed_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    from watchparty.recap import storage

    off_loop: list[str] = []
    real = asyncio.to_thread

    async def to_thread(fn: Any, *args: Any, **kwargs: Any) -> Any:
        off_loop.append(getattr(fn, "__name__", ""))
        return await real(fn, *args, **kwargs)

    monkeypatch.setattr("watchparty.recap.storage.asyncio.to_thread", to_thread)
    await storage.AzureRecaps(FakeBlobService(), FakeQueue()).submit(SID, {"v": 1, "session_id": SID})
    assert off_loop[0] == "pack"  # #49: gzip of a whole match stalled every live match on the loop


async def test_links_appear_only_when_the_job_is_done_and_are_read_only_signed_links() -> None:
    from urllib.parse import parse_qs, urlparse

    from watchparty.recap.storage import AzureRecaps

    blobs = FakeBlobService()
    recaps = AzureRecaps(blobs, FakeQueue())
    assert await recaps.links(SID, ["mgr_1", "mgr_2"]) is None
    blobs.container.store[f"{SID}/done.json"] = b"{}"
    links = await recaps.links(SID, ["mgr_1", "mgr_2"])
    assert links is not None
    url = urlparse(links["mgr_2"])
    query = parse_qs(url.query)
    assert url.path == f"/recaps/{SID}/mgr_2.mp4"
    assert (query["sp"], query["spr"], query["sr"]) == (["r"], ["https"], ["b"])  # read one blob, HTTPS only
    assert "skoid" in query  # signed with a user delegation key, not an account key
    await recaps.links(SID, ["mgr_1"])
    assert blobs.keys == 1  # the delegation key is reused


@pytest.mark.parametrize("bad", ["../x", "short", "a/b" * 10])
def test_blob_names_only_take_session_ids(bad: str) -> None:
    from watchparty.recap.storage import input_blob

    with pytest.raises(ValueError, match="session"):
        input_blob(bad)


@ffmpeg
def test_the_job_renders_all_five_from_a_recorded_session(bundle: dict[str, Any], tmp_path: Path) -> None:
    from watchparty.recap.worker import main

    recorded = tmp_path / "input.json.gz"
    recorded.write_bytes(gzip.compress(json.dumps(bundle).encode()))
    assert main(["--local", str(recorded), str(tmp_path / "out")]) == 0
    assert sorted(p.name for p in (tmp_path / "out").glob("*.mp4")) == [f"mgr_{n}.mp4" for n in range(1, 6)]


class Message:
    def __init__(self, content: str, dequeue_count: int = 1) -> None:
        self.content, self.dequeue_count = content, dequeue_count


class Queue:
    def __init__(self, message: Message | None) -> None:
        self.message, self.deleted = message, False

    def receive_message(self, visibility_timeout: int) -> Message | None:
        self.visibility_timeout = visibility_timeout
        return self.message

    def delete_message(self, message: Message) -> None:
        self.deleted = True


@pytest.mark.parametrize("content", ["not json", "{}", '{"session_id": 7}', '{"session_id": "../etc"}'])
def test_a_message_that_cannot_parse_is_dropped_at_once(content: str) -> None:
    queue = Queue(Message(content))
    assert worker.process(queue, None, dict) == 1  # never reaches storage
    assert queue.deleted  # #29: it used to fail on every try and never leave the queue


def test_a_message_tried_too_often_is_dropped_before_parsing() -> None:
    message = Message(json.dumps({"session_id": "s" * 16}), dequeue_count=worker.MAX_TRIES + 1)  # valid (#57)
    queue = Queue(message)
    assert worker.process(queue, None, dict) == 1  # no storage: reaching it would raise
    assert queue.deleted


def test_a_failed_run_is_retried_while_the_app_still_waits_for_the_links() -> None:
    from watchparty.recap.dispatch import publish_recaps

    queue = Queue(None)
    assert worker.process(queue, None, dict) == 0
    slowest_run_s = 100  # measured on Azure, 24 Oct: 60 to 100 s for all five
    assert queue.visibility_timeout > slowest_run_s  # a healthy run never sees its own message come back
    wait = inspect.signature(publish_recaps).parameters["timeout_s"].default
    assert queue.visibility_timeout + slowest_run_s < wait  # one retry still reaches the phones (#29)
