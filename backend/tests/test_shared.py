"""Shared live state for several replicas (#95), against an in-memory Redis: the limits counted once across
servers, actions reaching the owner, and screens on another server ended when the owner goes away."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import fakeredis
import pytest
from fastapi.testclient import TestClient
from redis.exceptions import RedisError
from starlette.websockets import WebSocketDisconnect

from watchparty.app.main import create_app, same_token
from watchparty.app.session import Message, Session, SessionStore
from watchparty.app.shared import ALIVE, STARTED, Remote, Shared, inbox, registry, serving

pytestmark = pytest.mark.anyio
AGENT = object()


async def stalled(_: float) -> None:
    """The match sends what comes before its first pause, then waits: these tests need it live, not played."""
    await asyncio.Event().wait()


Pair = tuple[Shared, Shared]


@pytest.fixture
async def pair() -> AsyncIterator[Pair]:
    """Two servers, A and B, on one Redis, each with its own store; fast heartbeats."""
    server = fakeredis.FakeServer()
    servers = []
    for _ in range(2):

        def connect(_: float | None) -> fakeredis.FakeAsyncRedis:
            return fakeredis.FakeAsyncRedis(server=server, decode_responses=True)

        shared = Shared(
            "redis://fake",
            connect=connect,
            heartbeat_s=0.05,
            owner_ttl_ms=400,
            grace_ms=200,
            command_timeout_s=0.1,
            retry_s=0.02,
            margin_s=0.05,
        )  # confirm_s 0.2
        store = SessionStore(per_client=10, max_sessions=10, session_kwargs={"sleep": stalled})
        shared.attach(store, max_connections=18)
        await shared.start()
        servers.append(shared)
    await until(lambda: all(s.listening for s in servers))
    yield servers[0], servers[1]
    for shared in servers:
        for session, _, _ in shared.store.sessions.values():
            if session.task:
                session.task.cancel()
        await shared.close()


async def until(check: Any, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not check():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.01)


async def create(shared: Shared, client: str = "1.2.3.4") -> Session | None:
    return await shared.create(seed=1, manager="mgr_1", style="casual", lang="en", client=client)


async def started(shared: Shared, client: str = "1.2.3.4") -> Session:
    session = await create(shared, client)
    assert session is not None
    await shared.start_match(session)
    assert session.task is not None
    return session


def limits(pair: Pair, **kw: int) -> None:
    for shared in pair:
        for name, value in kw.items():
            setattr(shared.store, name, value)


# --- the limits, once across servers (plan case 3) -----------------------------------------------------


async def test_the_per_client_seat_limit_counts_both_servers(pair: Pair) -> None:
    a, b = pair
    limits(pair, per_client=2)
    await started(a)
    await started(b)
    assert await create(a) is None
    assert await create(b) is None
    assert await create(b, client="5.6.7.8") is not None  # everyone else is unaffected


async def test_the_seat_limit_counts_both_servers(pair: Pair) -> None:
    a, b = pair
    limits(pair, max_sessions=2)
    await started(a, "10.0.0.1")
    await started(b, "10.0.0.2")
    assert await create(a, "10.0.0.3") is None


async def test_the_hourly_limit_counts_matches_started_on_either_server(pair: Pair) -> None:
    a, b = pair
    limits(pair, per_client_hour=2)
    (await started(a)).task.cancel()  # type: ignore[union-attr]
    (await started(b)).task.cancel()  # type: ignore[union-attr]
    await a.beat()  # the finished matches leave the seats; the hour keeps them
    assert await create(a) is None


async def test_the_agent_quota_is_shared(pair: Pair) -> None:
    a, b = pair
    limits(pair, global_agent_hour=1)
    for shared in pair:
        shared.store.session_kwargs |= {"manager_agent": AGENT, "host_agent": AGENT}
    first, second = await started(a, "10.0.0.1"), await started(b, "10.0.0.2")
    assert (first.agents_on, second.agents_on) == (True, False)


async def test_an_abandoned_unpaid_match_gives_its_agent_slot_back_everywhere(pair: Pair) -> None:
    a, b = pair
    limits(pair, global_agent_hour=1)
    for shared in pair:
        shared.store.session_kwargs |= {"manager_agent": AGENT, "host_agent": AGENT}
    first = await started(a, "10.0.0.1")
    first.abandoned = True
    assert first.on_abandoned is not None
    first.on_abandoned()
    await asyncio.sleep(0.1)
    assert str(await a.redis.hget(STARTED, first.id)).endswith("\t0")
    assert (await started(b, "10.0.0.2")).agents_on


async def test_a_new_session_replaces_the_clients_unopened_one_on_the_other_server(pair: Pair) -> None:
    a, b = pair
    limits(pair, per_client=1)
    old = await create(a)
    assert old is not None
    assert await create(b) is not None  # a judge who refreshes the lobby is never refused (#50)
    await until(lambda: a.store.get(old.id) is None)


async def test_a_dead_servers_sessions_stop_counting(pair: Pair) -> None:
    a, b = pair
    limits(pair, per_client=1)
    await started(a)
    for task in list(a.tasks):
        task.cancel()  # A crashes: no goodbye, its heartbeat stops
    await asyncio.sleep(0.5)  # past owner_ttl_ms
    assert await create(b) is not None


# --- screens and actions on another server (plan cases 2, 4, 5, 6) -------------------------------------


class Socket:
    def __init__(self) -> None:
        self.accepted, self.closed = False, 0

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int) -> None:
        self.closed = code


Watching = tuple[Socket, list[Message], asyncio.Task[None]]


def watch(shared: Shared, sid: str, screen: str = "group", token: str | None = None) -> Watching:
    """A screen on `shared` for session `sid`: what it was sent, until the server closes it."""
    socket, got = Socket(), []

    async def relay(queue: asyncio.Queue[Message | None], close_code: Any = None) -> None:
        while (message := await queue.get()) is not None:
            got.append(message)
        socket.closed = close_code() if close_code else 1000  # as main.relay closes it

    task = asyncio.ensure_future(shared.serve(socket, sid, screen, token, False, relay, same_token))
    return socket, got, task


async def test_a_screen_on_the_other_server_gets_the_match_and_its_replay(pair: Pair) -> None:
    a, b = pair
    session = await create(a)
    assert session is not None
    session.key_events.append({"seq": 1, "t": 0, "type": "kickoff"})
    session.latest_frame = {"t": 1234, "ball": {}}
    socket, got, task = watch(b, session.id)
    await until(lambda: len(got) >= 4)
    assert socket.accepted
    assert [m["type"] for m in got[:4]] == ["meta", "league", "event", "frame"]  # the late joiner's replay
    assert got[3]["data"]["t"] == 1234
    assert session.connections() == 1  # the owner counts it: the match is not abandoned
    assert session.task is not None  # and its first screen started it
    task.cancel()


async def test_a_phone_needs_the_token_and_an_unknown_session_is_refused(pair: Pair) -> None:
    a, b = pair
    session = await create(a)
    assert session is not None
    for sid, screen, token in ((session.id, "phone:mgr_1", "wrong"), (session.id, "phone:mgr_1", None),
                               ("nope", "group", None)):  # fmt: skip
        socket, _, task = watch(b, sid, screen, token)
        await task
        assert (socket.accepted, socket.closed) == (False, 1008)
    socket, _, task = watch(b, session.id, "phone:mgr_1", session.phone_token)
    await until(lambda: socket.accepted)
    task.cancel()


async def test_the_screen_cap_is_the_owners(pair: Pair) -> None:
    a, b = pair
    a.max_connections = 1
    session = await create(a)
    assert session is not None
    first, _, t1 = watch(b, session.id)
    await until(lambda: first.accepted)
    second, _, t2 = watch(b, session.id)
    await t2
    assert second.closed == 1008
    t1.cancel()


async def test_the_judges_style_and_failure_button_reach_the_owner(pair: Pair) -> None:
    a, b = pair
    session = await create(a)
    assert session is not None
    assert await b.forward(session.id, session.phone_token, {"op": "style", "style": "analyst"}, same_token)
    assert await b.forward(session.id, session.phone_token, {"op": "chaos"}, same_token)
    await until(lambda: session.chaos)
    assert session.managers["mgr_1"]["style"] == "analyst"
    assert not await b.forward(session.id, "wrong", {"op": "chaos"}, same_token)
    assert not await b.forward("nope", session.phone_token, {"op": "chaos"}, same_token)


async def test_a_press_while_the_owners_inbox_reconnects_is_busy_not_unknown(pair: Pair) -> None:
    """The owner is alive but between inbox connections (_listen's 1 s retry): a connection error, which the caller
    answers 503, not None, which it answers 404 (#134)."""
    a, b = pair
    session = await create(a)
    assert session is not None
    real = b.redis.publish

    async def publish(channel: str, payload: str) -> Any:
        if channel == inbox(a.id):
            return 0  # what Redis answers for a channel nobody is subscribed to
        return await real(channel, payload)

    b.redis.publish = publish  # type: ignore[method-assign, assignment]
    assert await b.alive(a.id)  # the owner's heartbeat is current
    with pytest.raises(RedisError):
        await b.forward(session.id, session.phone_token, {"op": "chaos"}, same_token)
    assert not session.chaos


async def test_screens_elsewhere_see_the_match_end_when_its_owner_crashes(pair: Pair) -> None:
    a, b = pair
    session = await create(a)
    assert session is not None
    session.key_events.append({"seq": 7, "t": 100, "type": "kickoff"})
    session.latest_frame = {"t": 2000}
    _, got, task = watch(b, session.id)
    await until(lambda: len(got) >= 4)
    a._spawn = lambda work: work.close()  # type: ignore[method-assign, attr-defined]
    for t in list(a.tasks):
        t.cancel()  # heartbeat, inbox and pumps: A is gone without a word, and runs nothing more
    await asyncio.wait_for(task, 3)  # the screen is closed, not left hanging
    whistle = got[-1]["data"]
    assert (got[-1]["type"], whistle["type"], whistle["seq"], whistle["t"]) == ("event", "full_time", 8, 2000)


async def test_a_gameweek_elsewhere_starts_with_the_gameweek_and_ends_each_fixture_when_its_owner_crashes(
    pair: Pair,
) -> None:
    # #151: the screens file anything before `gameweek` as a single match, and keep an event without a fixture
    # for none, so a crashed owner's gameweek gets a whistle per fixture that had not blown
    a, b = pair
    session = await a.create(seed=1, manager="mgr_1", style="casual", lang="en", client="1.2.3.4", gameweek=True)
    assert session is not None
    assert session.gameweek is not None
    session.key_events.append({"seq": 7, "t": 100, "type": "full_time", "fixture_id": "fx_2"})
    session.latest_frame = {"t": 2000, "fixture_id": "fx_1"}
    session.latest_frames = {"fx_1": session.latest_frame}
    _, got, task = watch(b, session.id)
    await until(lambda: len(got) >= 4)
    assert [m["type"] for m in got[:4]] == ["gameweek", "league", "event", "frame"]
    a._spawn = lambda work: work.close()  # type: ignore[method-assign, attr-defined]
    for t in list(a.tasks):
        t.cancel()
    await asyncio.wait_for(task, 3)
    events = [m["data"] for m in got if m["type"] == "event"]  # the match had started: kickoffs came first
    whistles = [e for e in events if e["type"] == "full_time" and e["seq"] != 7]
    assert [w["fixture_id"] for w in whistles] == ["fx_1", "fx_3"]  # fx_2 had blown
    assert all(w["t"] >= 2000 for w in whistles)
    assert len({e["seq"] for e in events}) == len(events)


async def test_screens_elsewhere_close_when_the_owner_drops_the_session(pair: Pair) -> None:
    a, b = pair
    session = await create(a)
    assert session is not None
    _, got, task = watch(b, session.id)
    await until(lambda: len(got) >= 2)
    a.store._drop(session.id)
    await asyncio.wait_for(task, 3)
    assert got[-1]["type"] != "event" or got[-1]["data"]["type"] != "full_time"  # a plain close, as on one server


async def test_a_screen_that_leaves_frees_its_seat_at_the_owner(pair: Pair) -> None:
    a, b = pair
    session = await create(a)
    assert session is not None
    _, got, task = watch(b, session.id)
    await until(lambda: session.connections() == 1 and len(got) >= 2)
    task.cancel()
    await until(lambda: session.connections() == 0)


# --- Redis unavailable (plan case 7) ---------------------------------------------------------------------


def test_with_redis_unreachable_the_server_is_not_ready_and_takes_no_match() -> None:
    shared = Shared("redis://127.0.0.1:1/0")  # nothing listens on port 1
    with TestClient(create_app(shared=shared)) as client:
        ready = client.get("/ready")
        assert ready.status_code == 503
        assert ready.json()["status"] == "unavailable"
        # Still alive: a liveness probe that asked Redis would restart every replica, and end every match
        assert client.get("/health").json() == {"status": "ok", "agents": False, "recaps": False}
        body = {"manager": "mgr_1"}
        assert client.post("/api/sessions", json=body).status_code == 503  # never this server's count alone
        with pytest.raises(WebSocketDisconnect) as closed, client.websocket_connect("/ws/unknown/group"):
            pass
        assert closed.value.code == 1011
        auth = {"authorization": "Bearer x"}
        assert client.post("/api/sessions/unknown/chaos", headers=auth).status_code == 503


async def test_without_redis_url_there_is_no_shared_state(monkeypatch: pytest.MonkeyPatch) -> None:
    from watchparty.app.shared import shared_from_env

    monkeypatch.delenv("REDIS_URL", raising=False)
    assert shared_from_env() is None
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    assert isinstance(shared_from_env(), Shared)


# --- review fixes: seats freed, admissions racing a heartbeat, pumps that stop, bad messages -------------


@pytest.mark.parametrize("how", ["finished", "dropped"])
async def test_a_match_that_ends_on_one_server_frees_its_seat_on_the_other(pair: Pair, how: str) -> None:
    a, b = pair
    limits(pair, max_sessions=1)
    session = await started(a, "10.0.0.1")
    assert await create(b, "10.0.0.2") is None
    session.task.cancel()  # type: ignore[union-attr]
    if how == "finished":
        session.finished_at = time.monotonic()
    else:
        a.store._drop(session.id)
    await asyncio.sleep(0.25)  # past the heartbeat's grace for an entry it did not list
    await a.beat()
    assert await create(b, "10.0.0.2") is not None


async def test_a_session_admitted_while_a_heartbeat_is_under_way_keeps_its_seat(pair: Pair) -> None:
    """The heartbeat lists this server's sessions, then runs its script: a session admitted in between is not on
    the list, and must not be taken for one that finished."""
    a, b = pair
    limits(pair, max_sessions=1)
    real, made = a.scripts["beat"], list[Session | None]()

    async def admit_first(**kw: Any) -> Any:
        if not made:
            made.append(await create(a, "10.0.0.1"))
        return await real(**kw)

    a.scripts["beat"] = admit_first
    try:
        await a.beat()
    finally:
        a.scripts["beat"] = real
    assert made[0] is not None
    assert await create(b, "10.0.0.2") is None  # still one seat, and it is taken


def failing_publish(shared: Shared, when: Any) -> None:
    """shared's publishes for which when(payload) is true raise, as on a Redis blip or the 2 s timeout."""
    real = shared.redis.publish

    async def publish(channel: str, payload: str) -> Any:
        if when(payload):
            raise RedisError("blip")
        return await real(channel, payload)

    shared.redis.publish = publish  # type: ignore[method-assign, assignment]


async def live_remote(pair: Pair) -> tuple[Session, Socket, list[Message], asyncio.Task[None]]:
    a, b = pair
    session = await create(a)
    assert session is not None
    socket, got, task = watch(b, session.id)
    await until(lambda: socket.accepted and len(got) >= 2)
    return session, socket, got, task


async def test_a_failed_publish_closes_the_remote_screen_at_once(pair: Pair) -> None:
    a, b = pair
    session, socket, got, task = await live_remote(pair)
    b.heartbeat_s = 30  # the serving server's own check is far off: only the owner's word can close it now
    await asyncio.sleep(0.2)  # its watcher finishes the short wait it was in, and starts the long one
    failing_publish(a, lambda payload: '"msgs"' in payload)
    session._send({"v": 1, "type": "frame", "data": {"t": 5}})
    await asyncio.wait_for(task, 3)  # closed, not frozen
    assert got[-1]["type"] != "event"  # a plain close: the match goes on, so no full-time whistle
    assert socket.closed == 1012  # which the screen's client retries; 1000 would be final
    assert session.connections() == 0


async def test_a_screen_whose_pump_died_silently_is_closed_by_the_serving_server(pair: Pair) -> None:
    a, _ = pair
    session, socket, _, task = await live_remote(pair)
    failing_publish(a, lambda _: True)  # every publish fails, the owner's word that the screen is lost too
    session._send({"v": 1, "type": "frame", "data": {"t": 5}})
    await asyncio.wait_for(task, 3)
    assert socket.closed == 1012


async def test_a_serving_set_redis_lost_closes_no_healthy_screen(pair: Pair) -> None:
    a, _ = pair
    _, socket, _, task = await live_remote(pair)
    sub = next(iter(a.pumps))
    await a.redis.delete(serving(a.id))  # a Redis restart, flush or failover
    await asyncio.sleep(0.5)  # several heartbeats on both sides
    assert not task.done(), f"closed with {socket.closed}"
    assert await a.redis.sismember(serving(a.id), sub)
    task.cancel()


async def test_a_match_started_while_a_heartbeat_is_under_way_keeps_its_seat(pair: Pair) -> None:
    a, b = pair
    limits(pair, max_sessions=1)
    real, made = a.scripts["beat"], list[Session]()

    async def start_first(**kw: Any) -> Any:
        if not made:
            made.append(await started(a, "10.0.0.1"))
        return await real(**kw)

    a.scripts["beat"] = start_first
    try:
        await a.beat()
    finally:
        a.scripts["beat"] = real
    assert await create(b, "10.0.0.2") is None


async def test_a_start_that_fails_closes_the_remote_screen(pair: Pair) -> None:
    a, b = pair
    session = await create(a)
    assert session is not None
    b.heartbeat_s = 30

    async def broken(_: Session) -> None:
        raise RedisError("blip")

    a.start_match = broken  # type: ignore[method-assign, assignment]
    socket, _, task = watch(b, session.id)
    await asyncio.wait_for(task, 3)
    assert socket.accepted
    assert session.connections() == 0


async def test_a_bad_inbox_message_is_skipped_and_the_inbox_goes_on(pair: Pair) -> None:
    a, b = pair
    session = await create(a)
    assert session is not None
    await b.redis.publish(inbox(a.id), "not json")
    await b.redis.publish(inbox(a.id), json.dumps({"op": "style", "sid": session.id, "style": "loud"}))
    assert await b.forward(session.id, session.phone_token, {"op": "chaos"}, same_token)
    await until(lambda: session.chaos)


async def test_a_session_whose_registration_fails_is_not_held(pair: Pair) -> None:
    a, _ = pair

    async def down(*_: Any, **__: Any) -> Any:
        raise RedisError("blip")

    a.redis.hset = down  # type: ignore[method-assign]
    with pytest.raises(RedisError):
        await create(a)
    assert a.store.sessions == {}


async def test_a_refund_that_cannot_reach_redis_does_not_raise(pair: Pair) -> None:
    a, _ = pair
    session = await started(a)
    session.abandoned = True

    async def down(**_: Any) -> Any:
        raise RedisError("blip")

    a.scripts["refund"] = down
    await a.refund(session)


async def test_a_drop_that_arrives_before_the_admission_answer_still_drops_the_session(pair: Pair) -> None:
    """Another server replaced this session and its drop reached the inbox before Redis's answer to the admission
    did: the session must not stay held once it is."""
    a, _ = pair
    real = a.scripts["admit"]

    async def answered_late(keys: list[str], args: list[Any]) -> Any:
        result = await real(keys=keys, args=args)
        a._on_message({"op": "drop", "sid": args[0]})  # the drop, ahead of the answer
        return result

    a.scripts["admit"] = answered_late
    session = await create(a)
    assert session is not None
    assert a.store.get(session.id) is None
    assert a.early_drops == {}


@pytest.mark.parametrize(("listed", "closed"), [([False, True], False), ([False, False], True)])
async def test_the_serving_side_closes_a_screen_after_two_misses_in_a_row(
    pair: Pair, listed: list[bool], closed: bool
) -> None:
    """One miss can be a set Redis lost before the owner's next heartbeat rewrote it; two in a row cannot."""
    a, b = pair
    answers = iter(listed)

    async def sismember(*_: Any) -> bool:
        return next(answers, True)

    b.redis.sismember = sismember  # type: ignore[method-assign, assignment]
    remote = Remote(cues_only=False)
    watch = asyncio.ensure_future(b._watch_owner(a.id, "sub", remote))
    await asyncio.sleep(0.4)  # several checks
    watch.cancel()
    assert remote.ended is closed
    assert remote.close_code == (1012 if closed else 1000)


# --- a lost heartbeat record is not a crash (#129) --------------------------------------------------------


def readings(shared: Shared, answers: Any) -> None:
    """shared reads its owners' heartbeat through answers(elapsed seconds since this call)."""
    start = asyncio.get_running_loop().time()

    async def alive(_: str) -> bool:
        return bool(answers(asyncio.get_running_loop().time() - start))

    shared.alive = alive  # type: ignore[method-assign, assignment]


@pytest.mark.parametrize(("back_after", "ended"), [(0.225, False), (None, True)])
async def test_a_dead_reading_is_confirmed_more_than_a_heartbeat_later(
    pair: Pair, back_after: float | None, ended: bool
) -> None:
    """Checks every 0.1 s, confirm_s 0.25 s. A heartbeat record lost at the start and back at 0.225 s: read dead
    at 0.1, then alive at 0.35, so the screen stays. Never back: full time and 1000 by the second read."""
    a, b = pair
    b.heartbeat_s = 0.1
    readings(b, lambda t: back_after is not None and t >= back_after)
    remote = Remote(cues_only=False)
    watch = asyncio.ensure_future(b._watch_owner(a.id, "sub", remote))
    await asyncio.sleep(0.6)
    watch.cancel()
    assert remote.ended is ended
    if ended:  # the crash path: a full-time whistle, then the close, with 1000
        whistle, close = remote.queue.get_nowait(), remote.queue.get_nowait()
        assert whistle is not None
        assert (whistle["data"]["type"], close, remote.close_code) == ("full_time", None, 1000)


async def test_a_heartbeat_record_redis_lost_ends_no_healthy_screen(pair: Pair) -> None:
    """B checks every 0.1 s. The record is gone for 0.22 s, so B reads A dead at least once by 0.2 s, and a plain
    one-period recheck would read it dead again; B's confirming check comes confirm_s (0.35 s) after the first
    reading, past A's next heartbeat."""
    a, b = pair
    b.heartbeat_s, b.command_timeout_s = 0.1, 0.2
    _, socket, got, task = await live_remote(pair)
    await asyncio.sleep(0.15)  # B's watcher is on its 0.1 s rhythm
    pause_heartbeat(a)
    await a.redis.delete(ALIVE, serving(a.id))  # a restart or failover without the data
    await asyncio.sleep(0.22)
    await a.beat()  # the owner's next heartbeat
    a._spawn(a._heartbeat())
    await asyncio.sleep(0.5)
    assert not task.done(), f"closed with {socket.closed}"
    assert all(m["data"].get("type") != "full_time" for m in got if m["type"] == "event")
    task.cancel()


def pause_heartbeat(shared: Shared) -> None:
    for task in list(shared.tasks):
        if task.get_coro().__name__ == "_heartbeat":  # type: ignore[union-attr]
            task.cancel()


async def test_a_screen_connecting_while_redis_has_lost_the_session_waits_for_the_owner(pair: Pair) -> None:
    a, b = pair
    session = await create(a)
    assert session is not None
    pause_heartbeat(a)
    pause_heartbeat(b)  # B's own record stays missing too: the sign that Redis has just lost its data
    await a.redis.delete(ALIVE, registry(session.id))  # lost with the rest of Redis's data
    socket, _, task = watch(b, session.id)
    await asyncio.sleep(0.03)
    await a.beat()  # the owner's next heartbeat writes them back
    await until(lambda: socket.accepted or task.done())
    assert socket.accepted, f"refused with {socket.closed}"
    task.cancel()


async def test_an_action_sent_while_redis_has_lost_the_session_still_reaches_the_owner(pair: Pair) -> None:
    a, b = pair
    session = await create(a)
    assert session is not None
    pause_heartbeat(a)
    pause_heartbeat(b)
    await a.redis.delete(ALIVE, registry(session.id))
    forwarded = asyncio.ensure_future(b.forward(session.id, session.phone_token, {"op": "chaos"}, same_token))
    await asyncio.sleep(0.03)
    await a.beat()
    assert await forwarded
    await until(lambda: session.chaos)


async def test_a_lookup_for_an_unknown_session_is_answered_at_once(pair: Pair) -> None:
    """Redis has not lost anything (this server's own heartbeat record is there): no waiting, for anyone."""
    _, b = pair
    started_at = asyncio.get_running_loop().time()
    socket, _, task = watch(b, "nope")
    await task
    assert not await b.forward("nope", "token", {"op": "chaos"}, same_token)
    elapsed = asyncio.get_running_loop().time() - started_at
    assert socket.closed == 1008
    assert elapsed < b.confirm_s / 2, f"took {elapsed:.3f} s"


async def test_a_failed_heartbeat_is_retried_soon(pair: Pair) -> None:
    a, _ = pair
    pause_heartbeat(a)
    a.heartbeat_s = 1.0  # a whole period would be far longer than the retry
    real, calls = a.beat, list[float]()

    async def fails_once() -> None:
        calls.append(asyncio.get_running_loop().time())
        if len(calls) == 1:
            raise RedisError("blip")
        await real()

    a.beat = fails_once  # type: ignore[method-assign]
    heartbeat = a._spawn(a._heartbeat())
    await asyncio.sleep(0.2)
    heartbeat.cancel()
    assert len(calls) == 2, calls
    assert calls[1] - calls[0] < 0.1


async def test_a_slow_failed_heartbeat_does_not_end_a_healthy_screen(pair: Pair) -> None:
    """Redis loses its data. The owner's next beat hangs for the whole command timeout (0.4 s) and fails, then
    retries. B reads the owner dead 0.2 s in; its confirming read waits confirm_s (0.2 + 0.4 + 0.05 s), past the
    owner's retry at about 0.57 s. One plain wait of 1.5 heartbeats (0.3 s) would confirm at 0.5 s, before it."""
    a, b = pair
    a.heartbeat_s = b.heartbeat_s = 0.2
    a.command_timeout_s = b.command_timeout_s = 0.4

    async def listed(*_: Any) -> bool:
        return True

    b.redis.sismember = listed  # type: ignore[method-assign, assignment]
    pause_heartbeat(a)
    await a.redis.delete(ALIVE)
    remote = Remote(cues_only=False)
    watch_task = asyncio.ensure_future(b._watch_owner(a.id, "sub", remote))
    real, calls = a.beat, list[float]()

    async def slow_then_fine() -> None:
        calls.append(asyncio.get_running_loop().time())
        if len(calls) == 1:
            await asyncio.sleep(a.command_timeout_s)  # the socket timeout on a command that never answers
            raise RedisError("Timeout reading from socket")
        await real()

    a.beat = slow_then_fine  # type: ignore[method-assign]
    await asyncio.sleep(0.15)
    a._spawn(a._heartbeat())
    await asyncio.sleep(0.85)
    watch_task.cancel()
    assert len(calls) >= 2
    assert not remote.ended


async def test_a_knockout_pressed_on_another_server_arms_the_owner_and_its_arm_comes_back(pair: Pair) -> None:
    """One manager's agent knocked out (#90), pressed on B for a match on A: armed on A, with A's arm id."""
    a, b = pair
    session = await create(a)
    assert session is not None
    one = await b.forward(session.id, session.phone_token, {"op": "chaos", "manager": "mgr_2"}, same_token)
    assert one is not None
    assert one["arm"] == 1
    assert (session.chaos, session.arms) == ("mgr_2", 1)
    every = await b.forward(session.id, session.phone_token, {"op": "chaos", "manager": None}, same_token)
    assert every is not None
    assert (every["arm"], session.chaos) == (2, "all")
    refused = await b.forward(session.id, session.phone_token, {"op": "chaos", "manager": "mgr_9"}, same_token)
    assert refused is not None
    assert "error" in refused
    assert session.arms == 2  # nothing armed by the refused press
    style = await b.forward(session.id, session.phone_token, {"op": "style", "style": "analyst"}, same_token)
    assert style is not None
    assert style.get("ok") is True
