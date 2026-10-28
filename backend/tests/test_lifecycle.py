"""Session lifecycle on a public demo (#22, #25, #33): quotas, abandoned and crashed matches, screens that
leave or join late, and who may press "Simulate failure"."""

import asyncio
import gc
import json
import time
import weakref
from collections import deque
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import drain
from tests.test_session import Host, Manager
from watchparty.app.main import MAX_CONNECTIONS_PER_SESSION, create_app
from watchparty.app.session import MomentState, Session, SessionStore
from watchparty.sim import DEMO_SEED, simulate

pytestmark = pytest.mark.anyio


async def no_sleep(_: float) -> None:
    await asyncio.sleep(0)


def store(**kw: Any) -> SessionStore:
    return SessionStore(session_kwargs={"sleep": no_sleep, **kw.pop("session_kwargs", {})}, **kw)


def create(s: SessionStore, client: str = "1.2.3.4") -> Session | None:
    return s.create(seed=1, manager="mgr_1", style="casual", lang="en", client=client)


# --- quotas (#22) --------------------------------------------------------------------------------------


def started(s: SessionStore, client: str = "1.2.3.4") -> Session:
    """A session whose first screen has connected, so its match counts against the hour."""
    session = create(s, client)
    assert session is not None
    s.start(session)
    assert session.task is not None
    session.task.cancel()  # only the start matters here, not the match
    return session


async def test_a_client_gets_a_few_matches_an_hour_then_a_busy_answer() -> None:
    s = store(per_client=10, per_client_hour=4)
    for _ in range(4):
        started(s)
    assert create(s) is None
    assert create(s, client="5.6.7.8") is not None  # everyone else is unaffected


async def test_sessions_nobody_opens_never_use_up_the_hour() -> None:
    s = store(per_client=20, max_sessions=20, per_client_hour=4)
    made = [create(s) for _ in range(9)]  # #39: a lobby opened and left, nine times
    assert all(m is not None for m in made)
    assert s.started == deque()
    for _ in range(4):
        started(s)
    assert create(s) is None


async def test_past_the_global_hourly_quota_matches_still_run_on_code_alone() -> None:
    agent = object()
    s = store(global_agent_hour=2, per_client=10, session_kwargs={"manager_agent": agent, "host_agent": agent})
    sessions = [started(s, client=f"10.0.0.{n}") for n in range(3)]
    assert [x.agents_on for x in sessions] == [True, True, False]
    assert sessions[2].workflow is not sessions[0].workflow


async def test_unopened_sessions_cannot_spend_the_agent_quota() -> None:
    agent = object()
    s = store(global_agent_hour=2, per_client=100, max_sessions=100,
              session_kwargs={"manager_agent": agent, "host_agent": agent})  # fmt: skip
    for n in range(10):
        assert create(s, client=f"10.0.1.{n}") is not None  # #39: created from many addresses, none opened
    assert started(s, client="10.0.2.1").agents_on  # the next judge still gets the agents


async def test_a_burst_of_sessions_started_together_still_meets_the_hourly_limit() -> None:
    agent = object()
    s = store(per_client=10, per_client_hour=2, session_kwargs={"manager_agent": agent})
    burst = []
    for _ in range(3):  # all created before any started, each passed the check alone, each with a screen
        session = create(s)
        assert session is not None
        session.subscribe("group")
        burst.append(session)
    for session in burst:
        assert session is not None
        s.start(session)
        assert session.task is not None
        session.task.cancel()
    assert [x.agents_on for x in burst if x] == [True, True, False]  # the third runs, but on code alone


async def test_an_unstarted_session_frees_its_seat_after_30_s() -> None:
    s = store(max_sessions=1)
    first = create(s)
    assert first is not None
    assert create(s, client="5.6.7.8") is None
    s.sessions[first.id] = (first, "1.2.3.4", time.monotonic() - 31)
    assert create(s, client="5.6.7.8") is not None


# --- abandoned and crashed matches (#22) -------------------------------------------------------------


async def test_a_match_nobody_watches_is_cancelled_without_a_recap() -> None:
    recaps: list[Session] = []

    async def slow(_: float) -> None:
        await asyncio.sleep(0.01)

    async def on_full_time(session: Session) -> None:
        recaps.append(session)

    s = Session(seed=1, manager="mgr_1", style="casual", lang="en", sleep=slow, abandon_after_s=0.05,
                on_full_time=on_full_time)  # fmt: skip
    q = s.subscribe("group")
    s.start()
    await asyncio.sleep(0.02)
    s.unsubscribe("group", q)  # the last screen leaves
    assert s.task is not None
    await asyncio.wait_for(asyncio.shield(s.task), 2)
    assert s.finished_at is not None
    assert s.abandoned
    assert recaps == []


async def test_one_failed_decision_does_not_stop_the_rest_of_the_match(monkeypatch: pytest.MonkeyPatch) -> None:
    recaps: list[Session] = []

    async def on_full_time(session: Session) -> None:
        recaps.append(session)

    s = Session(seed=267, manager="mgr_1", style="casual", lang="en", sleep=no_sleep, on_full_time=on_full_time)
    real, calls = s._decided, []

    async def first_fails(moment: MomentState) -> dict[str, Any] | None:
        calls.append(moment.moment_id)
        if len(calls) == 1:
            raise RuntimeError("a transient fault")  # #49: it used to end every later decision, and the recap
        return await real(moment)

    monkeypatch.setattr(s, "_decided", first_fails)
    await s.run()
    later = [m for m in s.log if m.get("moment_id") and m.get("moment_id") != calls[0] and "chosen_manager" in m]
    assert later  # the next moments were still decided
    assert recaps == [s]  # and the match still completed, with its recap


async def test_a_crash_mid_match_frees_the_seat_and_ends_every_screen_with_a_whistle_they_keep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = Session(seed=267, manager="mgr_1", style="casual", lang="en", sleep=no_sleep)
    q = s.subscribe("group")
    real, fed = s.engine.feed, []

    def breaks_mid_play(event: dict[str, Any]) -> Any:
        fed.append(event)
        if len(fed) == 700:  # between key moments, so the last key event is well behind
            raise RuntimeError("the engine broke")
        return real(event)

    monkeypatch.setattr(s.engine, "feed", breaks_mid_play)
    await s.run()
    assert s.finished_at is not None  # the seat is freed
    events = [m["data"] for m in drain(q) if m["type"] == "event"]
    seqs = [e["seq"] for e in events]
    assert events[-1]["type"] == "full_time"
    assert seqs.count(events[-1]["seq"]) == 1  # #49: a seq already sent is dropped by the screens as a repeat


# --- who may press "Simulate failure" (#22) ------------------------------------------------------------


def test_simulate_failure_needs_the_session_token() -> None:
    app = create_app(session_kwargs={"sleep": no_sleep})
    with TestClient(app) as client:
        made = client.post("/api/sessions", json={"seed": "demo", "manager": "mgr_1"}).json()
        url = f"/api/sessions/{made['session_id']}/chaos"
        assert client.post(url).status_code == 404
        assert client.post(url, headers={"Authorization": "Bearer wrong"}).status_code == 404
        assert client.post(url, headers={"Authorization": f"Bearer {made['phone_token']}"}).status_code == 202


def test_a_knockout_names_a_friend_and_needs_the_session_token() -> None:
    app = create_app(session_kwargs={"sleep": no_sleep})
    with TestClient(app) as client:
        made = client.post("/api/sessions", json={"seed": "demo", "manager": "mgr_1"}).json()
        session = app.state.store.get(made["session_id"])
        url = f"/api/sessions/{made['session_id']}/chaos"
        auth = {"Authorization": f"Bearer {made['phone_token']}"}
        assert client.post(url, json={"manager": "mgr_2"}).status_code == 404
        assert client.post(url, json={"manager": "mgr_2"}, headers={"Authorization": "Bearer wrong"}).status_code == 404
        for bad in ({"manager": "mgr_9"}, {"manager": "x"}, {"manager": "mgr_2", "everyone": True}):
            assert client.post(url, json=bad, headers=auth).status_code == 422, bad
        assert session.chaos is None
        pressed = client.post(url, json={"manager": "mgr_2"}, headers=auth)
        assert (pressed.status_code, pressed.json()["arm"]) == (202, 1)
        assert session.chaos == "mgr_2"
        again = client.post(url, json={}, headers=auth)  # no manager: every agent, as before
        assert (again.status_code, again.json()["arm"]) == (202, 2)
        assert session.chaos == "all"


def test_a_knockout_the_session_refuses_is_a_422(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(session_kwargs={"sleep": no_sleep})
    with TestClient(app) as client:
        made = client.post("/api/sessions", json={"seed": "demo", "manager": "mgr_1"}).json()
        session = app.state.store.get(made["session_id"])
        session.managers.pop("mgr_5")  # a league without that manager: the session itself says no
        url = f"/api/sessions/{made['session_id']}/chaos"
        auth = {"Authorization": f"Bearer {made['phone_token']}"}
        refused = client.post(url, json={"manager": "mgr_5"}, headers=auth)
        assert (refused.status_code, refused.json()["detail"]) == (422, "no such manager in this league")
        assert session.chaos is None


async def test_a_failure_pressed_while_a_moment_is_decided_is_kept_for_the_next_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = Session(seed=267, manager="mgr_1", style="casual", lang="en", sleep=no_sleep)
    engine_stakes = [st for e in s.match.events if (st := s.engine.feed(e))]
    real = s.workflow.run

    async def press_during(moment: Any) -> Any:
        s.simulate_failure()  # pressed while this moment, which started without it, is being decided
        return await real(moment)

    monkeypatch.setattr(s.workflow, "run", press_during)
    first = next(st for st in engine_stakes if any(x["above_threshold"] for x in st))
    await s.decide(first)
    assert s.chaos  # not spent: that moment did not use it


# --- screens that leave (#33) -------------------------------------------------------------------------
# Driven at the ASGI level, as uvicorn does: the test client cancels a handler on close, which hides a
# handler that never notices the disconnect.


class Screen:
    """One WebSocket connection to the app: the messages it sends, and a disconnect when told."""

    def __init__(
        self, app: Any, path: str, accept_delay_s: float = 0, answers_close: bool = True, reads: bool = True
    ) -> None:
        self.inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.inbox.put_nowait({"type": "websocket.connect"})
        self.sent: list[dict[str, Any]] = []
        self.accept_delay_s, self.answers_close = accept_delay_s, answers_close  # False: a half-open client
        self.reads = reads  # False: a client that stops reading, so every send and close waits for ever (#69)
        scope = {"type": "websocket", "path": path, "raw_path": path.encode(), "query_string": b"", "headers": [],
                 "client": ("1.2.3.4", 1), "server": ("test", 80), "scheme": "ws", "root_path": "",
                 "subprotocols": [], "asgi": {"version": "3.0"}}  # fmt: skip
        self.task = asyncio.create_task(app(scope, self.inbox.get, self.send))

    async def send(self, message: dict[str, Any]) -> None:
        if message["type"] == "websocket.accept":
            await asyncio.sleep(self.accept_delay_s)
        if message["type"] in ("websocket.send", "websocket.close") and not self.reads:
            await asyncio.Event().wait()
        self.sent.append(message)
        if message["type"] == "websocket.close" and self.answers_close:
            self.inbox.put_nowait({"type": "websocket.disconnect", "code": message.get("code", 1000)})

    def accepted(self) -> bool:
        return any(m["type"] == "websocket.accept" for m in self.sent)

    def leave(self) -> None:
        self.inbox.put_nowait({"type": "websocket.disconnect", "code": 1001})

    def stop_reading(self) -> None:
        self.reads = False

    def say(self, frame: dict[str, Any]) -> None:
        self.inbox.put_nowait({"type": "websocket.receive", **frame})

    def frames(self) -> int:
        return sum(m["type"] == "websocket.send" and json.loads(m["text"])["type"] == "frame" for m in self.sent)

    def got_full_time(self) -> bool:
        return any(m["type"] == "websocket.send" and json.loads(m["text"])["data"].get("type") == "full_time"
                   for m in self.sent)  # fmt: skip


async def finished(session: Session) -> None:
    while session.finished_at is None:
        await asyncio.sleep(0.01)


async def test_a_screen_that_closes_after_full_time_is_let_go_at_once() -> None:
    app = create_app(session_kwargs={"sleep": no_sleep})
    session = create(app.state.store)
    assert session is not None
    screen = Screen(app, f"/ws/{session.id}/group")
    await asyncio.wait_for(finished(session), 10)  # full time: nothing more will be sent
    screen.leave()
    await asyncio.wait_for(screen.task, 1)
    assert session.connections() == 0


async def test_a_session_is_freed_once_reaped_with_its_screens_gone() -> None:
    app = create_app(session_kwargs={"sleep": no_sleep})
    app.state.store.linger_s = 0
    session = create(app.state.store)
    assert session is not None
    ref = weakref.ref(session)
    screen = Screen(app, f"/ws/{session.id}/group")
    await asyncio.wait_for(finished(session), 10)
    screen.leave()
    await asyncio.wait_for(screen.task, 1)
    del session
    await asyncio.sleep(0.01)
    app.state.store._reap()
    gc.collect()
    assert ref() is None


async def test_a_dropped_session_closes_its_screens_with_1000() -> None:
    app = create_app(session_kwargs={"sleep": no_sleep})
    app.state.store.linger_s = 0
    session = create(app.state.store)
    assert session is not None
    screen = Screen(app, f"/ws/{session.id}/group")
    await asyncio.wait_for(finished(session), 10)
    app.state.store._reap()
    await asyncio.wait_for(screen.task, 1)
    assert screen.sent[-1] == {"type": "websocket.close", "code": 1000, "reason": ""}


async def test_simultaneous_handshakes_never_exceed_the_connection_cap() -> None:
    app = create_app(session_kwargs={"sleep": no_sleep})
    session = create(app.state.store)
    assert session is not None
    for _ in range(MAX_CONNECTIONS_PER_SESSION - 1):
        session.subscribe("group")  # one seat left
    screens = [Screen(app, f"/ws/{session.id}/group", accept_delay_s=0.05) for _ in range(13)]
    await asyncio.sleep(0.3)
    assert session.connections() <= MAX_CONNECTIONS_PER_SESSION
    assert sum(s.accepted() for s in screens) == 1
    for s in screens:
        s.leave()
    await asyncio.wait_for(asyncio.gather(*(s.task for s in screens)), 2)


# --- screens that join late (#25) -----------------------------------------------------------------------


@pytest.mark.parametrize("seed", [267, 9])  # seed 9 scores 3 of its 4 goals outside key moments (#41)
async def test_a_screen_joining_after_full_time_gets_the_match_state_and_the_end(seed: int) -> None:
    s = Session(seed=seed, manager="mgr_1", style="casual", lang="en", sleep=no_sleep)
    await s.run()
    q = s.subscribe("phone:mgr_1")
    got = drain(q)
    events = [m["data"] for m in got if m["type"] == "event"]
    frames = [m["data"] for m in got if m["type"] == "frame"]
    assert [e["type"] for e in events if e["type"] in ("half_time", "full_time")] == ["half_time", "full_time"]
    goals = [e for e in events if e["type"] == "shot" and e.get("outcome") == "goal"]
    assert len(goals) == sum(e["type"] == "shot" and e.get("outcome") == "goal" for e in s.match.events)
    cards = [e for e in events if e["type"] == "card"]
    assert len(cards) == sum(e["type"] == "card" for e in s.match.events)
    assert frames == [simulate(seed).frames[-1]]  # where everyone stands now, though the session let its frames go


def xg(messages: list[dict[str, Any]]) -> dict[str, float]:
    """Each side's xG as the scoreboard works it out (frontend/src/lib/match.ts): the shots' xG, summed."""
    shots = [m["data"] for m in messages if m["type"] == "event" and m["data"]["type"] == "shot"]
    return {side: round(sum(e["shot"]["xg"] for e in shots if e["team"] == side), 2) for side in ("home", "away")}


async def test_a_reloaded_demo_screen_shows_the_same_xg_as_one_watching_live() -> None:
    probes, compared = [30, 60], []  # match minutes

    async def reload_at_the_probes(_: float) -> None:
        if probes and s.latest_frame and s.latest_frame["t"] >= probes[0] * 60_000:
            live.extend(drain(q))
            fresh = s.subscribe("group")  # a reload: a new screen, from the replay alone
            compared.append((probes.pop(0), xg(live), xg(drain(fresh))))
            s.unsubscribe("group", fresh)
        await asyncio.sleep(0)

    s = Session(seed=DEMO_SEED, manager="mgr_1", style="casual", lang="en", sleep=reload_at_the_probes)
    q, live = s.subscribe("group"), list[dict[str, Any]]()
    await s.run()
    live.extend(drain(q))
    compared.append((90, xg(live), xg(drain(s.subscribe("group")))))
    assert [minute for minute, _, _ in compared] == [30, 60, 90]
    assert all(reloaded == watched for _, watched, reloaded in compared), compared  # #70: 1.65 against 1.77
    assert compared[-1][1]["home"] + compared[-1][1]["away"] > 0


@pytest.mark.parametrize("seed", range(1, 21))
async def test_a_screen_joining_at_full_time_shows_the_same_xg_as_one_watching_live(seed: int) -> None:
    s = Session(seed=seed, manager="mgr_1", style="casual", lang="en", sleep=no_sleep)
    q = s.subscribe("group")
    await s.run()
    assert xg(drain(s.subscribe("group"))) == xg(drain(q))


async def test_a_screen_returning_to_an_abandoned_match_is_told_it_ended() -> None:
    async def slow(_: float) -> None:
        await asyncio.sleep(0.01)

    s = Session(seed=DEMO_SEED, manager="mgr_1", style="casual", lang="en", sleep=slow, abandon_after_s=0.05)
    q = s.subscribe("group")
    s.start()
    await asyncio.sleep(0.02)
    s.unsubscribe("group", q)  # the laptop sleeps
    assert s.task is not None
    await asyncio.wait_for(asyncio.shield(s.task), 2)
    assert s.abandoned
    events = [m["data"] for m in drain(s.subscribe("group")) if m["type"] == "event"]  # it wakes and reconnects
    assert events[-1]["type"] == "full_time"  # #70: a partial match and no more frames froze the pitch
    assert events[-1]["seq"] > s.last_seq
    assert [e["type"] for e in events].count("full_time") == 1


# --- edges (#44) ---------------------------------------------------------------------------------------


async def test_a_first_screen_joining_after_30_s_starts_the_match() -> None:
    app = create_app(session_kwargs={"sleep": no_sleep})
    session = create(app.state.store)
    assert session is not None
    s = app.state.store
    s.sessions[session.id] = (session, "1.2.3.4", time.monotonic() - 31)  # the judge waited in the lobby
    screen = Screen(app, f"/ws/{session.id}/group")
    await asyncio.wait_for(finished(session), 10)  # it started and played: no KeyError, no 500
    assert screen.accepted()
    screen.leave()
    await asyncio.wait_for(screen.task, 1)


async def test_viewers_leaving_during_full_time_summaries_cancel_them_and_the_recap() -> None:
    from tests.test_summary import ToolUser, fake_connect

    class Slow(ToolUser):
        async def summarise(self, *args: Any) -> dict[str, Any]:
            await asyncio.sleep(5)
            self.called += 1  # only reached if it was not cancelled
            return await super().summarise(*args)

    recaps: list[Session] = []

    async def on_full_time(session: Session) -> None:
        recaps.append(session)

    agent = Slow()
    s = Session(seed=267, manager="mgr_1", style="casual", lang="en", sleep=no_sleep, summary_agent=agent,
                mcp_connect=fake_connect, abandon_after_s=0.05, on_full_time=on_full_time)  # fmt: skip
    q = s.subscribe("group")
    s.start()
    assert s.task is not None
    while "full_time" not in s.views:  # the match plays at once; the summaries are what is slow
        await asyncio.sleep(0.01)
    s.unsubscribe("group", q)  # the last viewer leaves while full-time summaries are running
    start = time.perf_counter()
    await asyncio.wait_for(asyncio.shield(s.task), 2)
    assert time.perf_counter() - start < 1
    assert s.abandoned
    assert (recaps, agent.called) == ([], 0)  # no recap job, and the summary calls were cancelled
    events = [m["data"] for m in drain(s.subscribe("group")) if m["type"] == "event"]
    assert [e["type"] for e in events].count("full_time") == 1  # it had its whistle: no second one (#70)


async def test_a_half_open_screen_does_not_keep_a_dropped_session() -> None:
    app = create_app(session_kwargs={"sleep": no_sleep})
    app.state.store.linger_s = 0
    session = create(app.state.store)
    assert session is not None
    screen = Screen(app, f"/ws/{session.id}/group", answers_close=False)  # never answers the close
    await asyncio.wait_for(finished(session), 10)
    app.state.store._reap()
    await asyncio.wait_for(screen.task, 1)  # the handler returns once it has closed the socket
    assert screen.sent[-1] == {"type": "websocket.close", "code": 1000, "reason": ""}
    assert session.connections() == 0


# --- screens that send, or stop reading (#69) ----------------------------------------------------------


async def paced(_: float) -> None:
    await asyncio.sleep(0.0002)  # a match of about a second, so a screen can drop out while it plays


@pytest.mark.parametrize("frame", [{"text": "hello"}, {"bytes": b"\x00" * 100}])
async def test_a_screen_that_sends_anything_is_closed_with_1008_and_the_others_keep_watching(
    frame: dict[str, Any],
) -> None:
    app = create_app(session_kwargs={"sleep": paced})
    session = create(app.state.store)
    assert session is not None
    honest = Screen(app, f"/ws/{session.id}/group")
    talker = Screen(app, f"/ws/{session.id}/group")
    talker.say(frame)
    await asyncio.wait_for(talker.task, 1)
    assert talker.sent[-1] == {"type": "websocket.close", "code": 1008, "reason": ""}
    assert session.connections() == 1
    frames_then = honest.frames()
    await asyncio.wait_for(finished(session), 10)
    assert honest.frames() > frames_then
    assert honest.got_full_time()
    honest.leave()
    await asyncio.wait_for(honest.task, 1)


async def test_a_screen_that_never_sends_and_leaves_mid_match_is_let_go_normally() -> None:
    app = create_app(session_kwargs={"sleep": paced})
    session = create(app.state.store)
    assert session is not None
    screen = Screen(app, f"/ws/{session.id}/group")
    while screen.frames() == 0:
        await asyncio.sleep(0.01)
    screen.leave()
    await asyncio.wait_for(screen.task, 1)
    assert session.connections() == 0
    assert not [m for m in screen.sent if m["type"] == "websocket.close"]  # left on its own: nothing to close
    assert session.finished_at is None  # it left while the match was still playing


async def test_a_screen_that_stops_reading_is_dropped_within_the_deadline_and_the_others_keep_watching() -> None:
    app = create_app(session_kwargs={"sleep": paced}, send_deadline_s=0.1)
    session = create(app.state.store)
    assert session is not None
    honest = Screen(app, f"/ws/{session.id}/group")
    while honest.frames() == 0:
        await asyncio.sleep(0.01)
    before = set(session.subscribers["group"])
    stuck = Screen(app, f"/ws/{session.id}/group", reads=False)
    while session.connections() < 2:
        await asyncio.sleep(0)
    (queue,) = session.subscribers["group"] - before
    released = weakref.ref(queue)
    del queue
    start = time.perf_counter()
    await asyncio.wait_for(stuck.task, 1)
    assert time.perf_counter() - start < 0.5
    assert session.connections() == 1
    gc.collect()
    assert released() is None  # its queue, and every message waiting in it, is let go
    assert session.finished_at is None  # dropped while the match was still playing
    frames_then = honest.frames()
    await asyncio.wait_for(finished(session), 10)
    assert honest.frames() > frames_then
    assert honest.got_full_time()
    honest.leave()
    await asyncio.wait_for(honest.task, 1)


# Once the match is quiet nothing more is sent, so only the close's own deadline can end the relay.


async def test_a_talker_that_stops_reading_once_the_match_is_quiet_is_still_let_go() -> None:
    app = create_app(session_kwargs={"sleep": no_sleep}, send_deadline_s=0.1)
    session = create(app.state.store)
    assert session is not None
    talker = Screen(app, f"/ws/{session.id}/group")
    await asyncio.wait_for(finished(session), 10)
    await asyncio.sleep(0.05)  # everything queued has been sent
    talker.stop_reading()
    talker.say({"text": "hello"})
    await asyncio.wait_for(talker.task, 1)  # the 1008 close never completes, and the handler returns anyway
    assert session.connections() == 0


async def test_a_dropped_session_lets_go_of_a_screen_that_stopped_reading() -> None:
    app = create_app(session_kwargs={"sleep": no_sleep}, send_deadline_s=0.1)
    app.state.store.linger_s = 0
    session = create(app.state.store)
    assert session is not None
    ref = weakref.ref(session)
    screen = Screen(app, f"/ws/{session.id}/group")
    await asyncio.wait_for(finished(session), 10)
    await asyncio.sleep(0.05)
    screen.stop_reading()
    app.state.store._reap()
    await asyncio.wait_for(screen.task, 1)  # the 1000 close never completes, and the handler returns anyway
    assert session.connections() == 0
    del session
    gc.collect()
    assert ref() is None  # the dropped session is not kept alive by the stuck close


# --- memory (#49, #50) -------------------------------------------------------------------------------


async def test_demo_sessions_share_one_simulated_match_and_never_change_it() -> None:
    import hashlib
    import json

    a = Session(seed=267, manager="mgr_1", style="casual", lang="en", sleep=no_sleep)
    b = Session(seed=267, manager="mgr_2", style="analyst", lang="es", sleep=no_sleep)
    assert a.match.events is b.match.events  # one copy of the match, not one per session
    before = hashlib.sha256(json.dumps(a.match.events).encode()).hexdigest()
    await a.run()
    assert hashlib.sha256(json.dumps(b.match.events).encode()).hexdigest() == before  # read-only: safe to share


async def test_a_finished_match_lets_go_of_its_frames_after_the_recap_is_handed_off() -> None:
    frames_at_full_time: list[int] = []

    async def on_full_time(session: Session) -> None:
        frames_at_full_time.append(len(session.match.frames))  # the recap bundle still gets them

    s = Session(seed=267, manager="mgr_1", style="casual", lang="en", sleep=no_sleep, on_full_time=on_full_time)
    await s.run()
    assert frames_at_full_time[0] > 1000
    assert s.match.frames == []  # a lingering session keeps its link, not 2 MB of frames
    assert s.latest_frame is not None  # a late screen still gets where everyone stands


async def test_a_cues_only_screen_gets_its_own_cues_and_nothing_else() -> None:
    s = Session(seed=267, manager="mgr_1", style="casual", lang="en", sleep=no_sleep)
    full, cues_only = s.subscribe("phone:mgr_1"), s.subscribe("phone:mgr_1", cues_only=True)
    await s.run()
    lean, rich = drain(cues_only), drain(full)
    assert lean
    assert {m["type"] for m in lean} == {"cue"}  # #49: the judge's page took every frame six times
    assert [m for m in rich if m["type"] == "cue"] == lean
    late = s.subscribe("phone:mgr_1", cues_only=True)
    assert {m["type"] for m in drain(late)} == {"cue"}  # a late join replays its cues only, too


def test_two_judge_tabs_and_five_phones_fit_one_session() -> None:
    assert MAX_CONNECTIONS_PER_SESSION >= 2 * 6 + 5  # #49: a refused socket turned a whole page into "ended"


# --- abuse (#50) --------------------------------------------------------------------------------------


async def test_a_client_holds_at_most_one_unopened_session() -> None:
    s = store(per_client=4, max_sessions=20)
    first = create(s)
    second = create(s)  # a judge who refreshed the lobby is never refused
    assert first is not None
    assert second is not None
    assert first.id not in s.sessions  # the unopened one it replaced is gone: one seat per address, not four
    squatters = [create(s, client=f"10.9.0.{n}") for n in range(5) for _ in range(4)]
    assert sum(x is not None and x.id in s.sessions for x in squatters) == 5
    assert create(s, client="5.6.7.8") is not None  # the judge still gets a seat (#50)


async def test_creating_sessions_is_capped_per_hour_too() -> None:
    s = store(per_client=4, created_per_client_hour=5)
    assert all(create(s) is not None for _ in range(5))
    assert create(s) is None


async def abandoned(s: SessionStore, client: str, agent: Any = None) -> Session:
    """A match its only screen leaves, abandoned by its own watchdog: at once, or, given one of the store's stub
    agents (anything that counts its `calls`), once that has been called for this match. Nothing here calls refund."""
    session = create(s, client)
    assert session is not None
    q, calls = session.subscribe("group"), agent.calls if agent else 0

    async def watched(_: float) -> None:
        leave = agent is None or agent.calls > calls
        if leave:
            session.unsubscribe("group", q)
        await asyncio.sleep(0.01 if leave else 0)

    session.sleep = watched
    s.start(session)
    assert session.task is not None
    await asyncio.wait_for(asyncio.shield(session.task), 5)
    assert session.abandoned
    return session


async def test_a_match_abandoned_before_any_agent_call_gives_back_its_agent_slot() -> None:
    manager = Manager()
    s = store(global_agent_hour=1, per_client=10,
              session_kwargs={"manager_agent": manager, "abandon_after_s": 0.05})  # fmt: skip
    gone = await abandoned(s, client="10.0.3.1")
    assert (manager.calls, gone.paid) == (0, False)  # nobody watched: it cost nothing, and must not cost the hour
    assert started(s, client="10.0.3.2").agents_on  # #50, through the session's own watchdog (#72)


async def test_a_match_abandoned_after_a_manager_call_keeps_its_agent_slot() -> None:
    manager = Manager()
    s = store(global_agent_hour=1, per_client=10,
              session_kwargs={"manager_agent": manager, "abandon_after_s": 0.05})  # fmt: skip
    gone = await abandoned(s, client="10.0.4.1", agent=manager)
    assert manager.calls >= 1
    assert gone.paid
    assert not started(s, client="10.0.4.2").agents_on  # #72: it spent on the model, so the hour is spent


async def test_a_match_abandoned_after_a_host_call_keeps_its_agent_slot() -> None:
    host = Host()  # the only agent: the bids are plain, and a close call still goes to the host
    s = store(per_client=10, per_client_agent_hour=1, session_kwargs={"host_agent": host, "abandon_after_s": 0.05})
    gone = await abandoned(s, client="10.0.6.1", agent=host)
    assert host.calls >= 1
    assert gone.paid
    assert not started(s, client="10.0.6.1").agents_on  # #72: the host call is paid too


class Summariser:
    """A stub summary agent that counts its calls and answers nothing usable: the plain summary goes out."""

    def __init__(self) -> None:
        self.calls = 0

    async def summarise(self, *args: Any) -> dict[str, Any]:
        self.calls += 1
        return {}


async def test_a_match_abandoned_after_a_summary_call_keeps_its_agent_slot() -> None:
    from tests.test_summary import fake_connect

    summariser = Summariser()  # the only agent, first called at half time
    kwargs = {"summary_agent": summariser, "mcp_connect": fake_connect, "abandon_after_s": 0.05}
    s = store(per_client=10, per_client_agent_hour=1, session_kwargs=kwargs)
    gone = await abandoned(s, client="10.0.7.1", agent=summariser)
    assert summariser.calls >= 1
    assert (gone.paid, gone.lookups.used) == (True, 0)  # paid by the summary call, not a rulebook lookup
    assert not started(s, client="10.0.7.1").agents_on  # #72


async def test_a_client_who_leaves_each_match_after_a_paid_call_still_meets_its_agent_limit() -> None:
    manager = Manager()
    s = store(per_client=10, per_client_hour=8, per_client_agent_hour=4,
              session_kwargs={"manager_agent": manager, "abandon_after_s": 0.05})  # fmt: skip
    for _ in range(4):
        assert (await abandoned(s, client="10.0.5.1", agent=manager)).paid
    assert not started(s, client="10.0.5.1").agents_on  # #72: 8 of 8 matches had agents, refunded each time


async def test_a_client_gets_a_few_agent_matches_an_hour_then_code() -> None:
    agent = object()
    s = store(per_client=10, per_client_hour=8, per_client_agent_hour=2, session_kwargs={"manager_agent": agent})
    assert [started(s).agents_on for _ in range(3)] == [True, True, False]


async def test_only_a_match_with_agents_queues_a_recap() -> None:
    from tests.test_recap import FakeRecaps
    from watchparty.recap.dispatch import publish_recaps

    recaps = FakeRecaps()
    s = Session(seed=267, manager="mgr_1", style="casual", lang="en", sleep=no_sleep,
                on_full_time=lambda session: publish_recaps(recaps, session, poll_s=0))  # fmt: skip
    await s.run()  # no agents: code alone
    assert recaps.submitted == []  # a render per code-only match had no cap at all (#50)


def test_a_phone_screen_with_a_non_ascii_token_is_refused_not_a_500() -> None:
    from starlette.websockets import WebSocketDisconnect

    app = create_app(session_kwargs={"sleep": no_sleep})
    with TestClient(app) as client:
        made = client.post("/api/sessions", json={"seed": "demo", "manager": "mgr_1"}).json()
        url = f"/ws/{made['session_id']}/phone:mgr_1?token=%C3%A9"
        with pytest.raises(WebSocketDisconnect) as refused, client.websocket_connect(url) as ws:
            ws.receive_json()
    assert refused.value.code == 1008  # #50
