"""Two app servers and one real Redis (#95): the test plan end to end over HTTP and WebSockets. Needs Docker;
skipped without it (pytest -m "not redis" leaves it out on purpose). Every port is picked at run time, so two runs
at once do not collide (#134)."""

import asyncio
import json
import shutil
import socket
import subprocess
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
import uvicorn
import websockets
from fastapi import FastAPI
from redis.asyncio import Redis
from redis.exceptions import RedisError

from watchparty.app.main import create_app
from watchparty.app.session import Session
from watchparty.app.shared import Shared, inbox

pytestmark = [pytest.mark.anyio, pytest.mark.redis]


URL = ""  # redis://127.0.0.1:<port>/0, once redis_container has picked the port


def free_port() -> int:
    """A port nothing listens on now. Chosen here, not left to Docker, which gives a restarted container a new one:
    the restart test needs Redis back where the servers look for it."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def redis_container() -> Iterator[str]:
    """A throwaway Redis 7 for this module, on a free port, stopped (and removed) at the end."""
    global URL
    if shutil.which("docker") is None:
        pytest.skip("no docker")
    for _ in range(5):  # another run may take the port between free_port and Docker binding it: pick another
        port = free_port()
        # No persistence: a restart loses everything, as a failover without the data would (#129)
        run = ["docker", "run", "-d", "--rm", "-p", f"127.0.0.1:{port}:6379", "redis:7", "redis-server",
               "--save", "", "--appendonly", "no"]  # fmt: skip
        started = subprocess.run(run, capture_output=True, text=True, check=False)  # noqa: S603
        if started.returncode == 0 or "already" not in started.stderr:
            break
    if started.returncode != 0:  # Docker is installed: a failure to run it is a failure, never a silent skip
        pytest.fail(f"docker could not start redis: {started.stderr.strip()[:200]}")
    URL = f"redis://127.0.0.1:{port}/0"
    container = started.stdout.strip()
    try:
        for _ in range(100):
            ping = ["docker", "exec", container, "redis-cli", "ping"]
            if subprocess.run(ping, capture_output=True, text=True, check=False).stdout.strip() == "PONG":  # noqa: S603
                break
            subprocess.run(["sleep", "0.1"], check=True)  # noqa: S607
        yield container
    finally:
        subprocess.run(["docker", "stop", container], capture_output=True, check=False)  # noqa: S603, S607


class Server:
    def __init__(self) -> None:
        self.port, self.base = 0, ""  # the port the OS gave it, once started
        self.shared = Shared(URL, heartbeat_s=0.2, owner_ttl_ms=1500)
        self.app: FastAPI = create_app(session_kwargs={"speed": 120}, shared=self.shared)
        self.server = uvicorn.Server(uvicorn.Config(self.app, port=0, log_level="warning", lifespan="on"))
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.task = asyncio.create_task(self.server.serve())
        while not self.server.started:
            await asyncio.sleep(0.01)
        self.port = int(self.server.servers[0].sockets[0].getsockname()[1])
        self.base = f"http://127.0.0.1:{self.port}"
        async with httpx.AsyncClient() as client:
            for _ in range(100):
                if (await client.get(f"{self.base}/health")).status_code == 200:
                    return
                await asyncio.sleep(0.05)
        raise AssertionError("never healthy")

    async def stop(self) -> None:
        if self.task is None:
            return
        self.server.should_exit = True
        await self.task
        self.task = None

    def session(self, sid: str) -> Session:
        session = self.app.state.store.get(sid)
        assert session is not None
        return session  # type: ignore[no-any-return]

    def ws(self, sid: str, screen: str = "group", token: str | None = None) -> str:
        return f"ws://127.0.0.1:{self.port}/ws/{sid}/{screen}" + (f"?token={token}" if token else "")


@pytest.fixture
async def servers(redis_container: str) -> AsyncIterator[tuple[Server, Server]]:
    redis = Redis.from_url(URL)
    await redis.flushall()
    await redis.aclose()
    a, b = Server(), Server()
    await a.start()
    await b.start()
    try:
        yield a, b
    finally:
        for server in (a, b):
            for session, _, _ in list(server.app.state.store.sessions.values()):
                if session.task:
                    session.task.cancel()
            await server.stop()


async def create(server: Server) -> dict[str, str]:
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{server.base}/api/sessions", json={"manager": "mgr_1"})
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


async def status_of_create(server: Server) -> int:
    async with httpx.AsyncClient() as client:
        return (await client.post(f"{server.base}/api/sessions", json={"manager": "mgr_1"})).status_code


async def receive(ws: Any, count: int, timeout: float = 15) -> list[dict[str, Any]]:
    got: list[dict[str, Any]] = []
    async with asyncio.timeout(timeout):
        while len(got) < count:
            got.append(json.loads(await ws.recv()))
    return got


@asynccontextmanager
async def screen(url: str) -> AsyncIterator[Any]:
    async with websockets.connect(url, open_timeout=10) as ws:
        yield ws


async def until(check: Any, timeout: float = 10) -> None:
    async with asyncio.timeout(timeout):
        while not check():
            await asyncio.sleep(0.05)


async def test_a_screen_on_b_watches_a_match_created_on_a(servers: tuple[Server, Server]) -> None:  # plan case 2
    a, b = servers
    sid = (await create(a))["session_id"]
    assert b.app.state.store.get(sid) is None  # the match runs on A only
    async with screen(b.ws(sid)) as ws:
        got = await receive(ws, 60)
    assert [m["type"] for m in got[:2]] == ["meta", "league"]
    times = [m["data"]["t"] for m in got if m["type"] == "frame"]
    assert len(times) > 20
    assert times == sorted(times)
    assert times[-1] > times[0]  # live, not a replay
    assert a.session(sid).task is not None  # B's screen started A's match


async def test_the_per_client_limits_count_once_across_a_and_b(servers: tuple[Server, Server]) -> None:  # case 3
    a, b = servers
    for server in servers:
        server.app.state.store.per_client = 2
    # Each opened before the next is made: a client's unopened session is replaced by its next one (#50)
    async with screen(a.ws((await create(a))["session_id"])) as one:
        await receive(one, 2)
        async with screen(b.ws((await create(b))["session_id"])) as two:
            await receive(two, 2)
            assert await status_of_create(a) == 503  # two running: one on each server
            assert await status_of_create(b) == 503


async def test_a_late_joiner_on_b_gets_the_latest_frame_and_key_events(servers: tuple[Server, Server]) -> None:  # 4
    a, b = servers
    made = await create(a)
    sid, token = made["session_id"], made["phone_token"]
    async with screen(a.ws(sid)) as group:
        await receive(group, 80)  # the match is well under way
        session = a.session(sid)
        await until(lambda: session.latest_frame is not None and session.latest_frame["t"] > 0)
        async with screen(b.ws(sid, "phone:mgr_1", token)) as phone:
            got = await receive(phone, 3 + len(session.key_events))
    types = [m["type"] for m in got]
    assert types[:2] == ["meta", "league"]
    events = [m["data"] for m in got if m["type"] == "event"]
    assert events[0]["type"] == "kickoff"
    first_frame = next(m["data"] for m in got if m["type"] == "frame")
    assert first_frame["t"] > 0  # where the match is, not kick-off


async def test_the_judges_style_and_failure_button_on_b_reach_owner_a(servers: tuple[Server, Server]) -> None:  # 5
    a, b = servers
    made = await create(a)
    sid, auth = made["session_id"], {"authorization": f"Bearer {made['phone_token']}"}
    async with httpx.AsyncClient(base_url=b.base) as client:
        style = await client.post(f"/api/sessions/{sid}/style", json={"style": "analyst"}, headers=auth)
        chaos = await client.post(f"/api/sessions/{sid}/chaos", headers=auth)
        wrong = await client.post(f"/api/sessions/{sid}/chaos", headers={"authorization": "Bearer nope"})
        unknown = await client.post("/api/sessions/nope/chaos", headers=auth)
    assert (style.status_code, chaos.status_code, wrong.status_code, unknown.status_code) == (204, 202, 404, 404)
    session = a.session(sid)
    await until(lambda: session.chaos and session.managers["mgr_1"]["style"] == "analyst")


async def test_when_owner_a_stops_screens_on_b_see_the_match_end(servers: tuple[Server, Server]) -> None:  # case 6
    a, b = servers
    sid = (await create(a))["session_id"]
    async with screen(b.ws(sid)) as ws:
        await receive(ws, 40)
        await a.stop()
        last: dict[str, Any] = {}
        async with asyncio.timeout(10):  # no hang
            async for raw in ws:
                last = json.loads(raw)
        assert ws.close_code == 1000
    assert (last["type"], last["data"]["type"]) == ("event", "full_time")


async def test_when_redis_goes_away_the_servers_are_not_ready(
    servers: tuple[Server, Server], redis_container: str
) -> None:  # case 7, mid-run
    a, _ = servers
    subprocess.run(["docker", "pause", redis_container], capture_output=True, check=True)  # noqa: S603, S607
    try:
        async with httpx.AsyncClient() as client:
            ready = await client.get(f"{a.base}/ready", timeout=10)
            health = await client.get(f"{a.base}/health", timeout=10)
            created = await client.post(f"{a.base}/api/sessions", json={"manager": "mgr_1"}, timeout=10)
        assert (ready.status_code, ready.json()["status"]) == (503, "unavailable")
        assert health.status_code == 200  # liveness never asks Redis
        assert created.status_code == 503
    finally:
        subprocess.run(["docker", "unpause", redis_container], capture_output=True, check=True)  # noqa: S603, S607


@pytest.mark.parametrize("where", ["one server", "both servers"])
async def test_concurrent_creates_from_one_client_hold_only_what_is_counted(
    servers: tuple[Server, Server], where: str
) -> None:
    """Each create replaces the client's unopened session. Every one held must be counted, and only one survives:
    a session held after its replacement's drop went by would play outside every limit."""
    a, b = servers
    for server in servers:
        server.app.state.store.per_client = 1
    targets = [a, a, a, a] if where == "one server" else [a, b, a, b]
    async with httpx.AsyncClient() as client:
        answers = await asyncio.gather(*(client.post(f"{s.base}/api/sessions", json={"manager": "mgr_1"})
                                         for s in targets))  # fmt: skip
    assert all(r.status_code == 201 for r in answers)
    await asyncio.sleep(0.5)  # the drops sent between servers arrive
    held = [sid for s in servers for sid in s.app.state.store.sessions]
    redis = Redis.from_url(URL, decode_responses=True)
    counted = await redis.hkeys("{wp}:running")
    await redis.aclose()
    assert sorted(held) == sorted(counted)
    assert len(held) == 1


async def test_a_screen_the_owner_stops_serving_reconnects(servers: tuple[Server, Server]) -> None:
    """Closed with 1012, which the screen's client retries (frontend/src/lib/socket.ts), not 1000, which is final."""
    a, b = servers
    sid = (await create(a))["session_id"]
    async with screen(b.ws(sid)) as ws:
        await receive(ws, 10)
        real = a.shared.redis.publish

        async def failing(channel: str, payload: str) -> int:
            if '"msgs"' in payload:
                raise RedisError("blip")
            return await real(channel, payload)

        a.shared.redis.publish = failing  # type: ignore[method-assign, assignment]
        with pytest.raises(websockets.ConnectionClosedError):  # the client library raises on any close but 1000
            await drain(ws)
        assert ws.close_code == 1012


async def drain(ws: Any) -> None:
    """Reads until the server closes the socket, for at most 10 s."""
    async with asyncio.timeout(10):
        async for _ in ws:
            pass


async def test_a_redis_restart_mid_match_does_not_end_a_screen_on_b(
    servers: tuple[Server, Server], redis_container: str
) -> None:
    """Redis restarts without its data (#129). B's screen must not be shown full time: it stays open, or closes with
    1012 and reconnects. A crash of A afterwards still ends it with full time and 1000."""
    a, b = servers
    sid = (await create(a))["session_id"]
    ws = await websockets.connect(b.ws(sid))
    await receive(ws, 20)
    subprocess.run(["docker", "restart", "-t", "0", redis_container], capture_output=True, check=True)  # noqa: S603, S607
    events: list[dict[str, Any]] = []
    try:
        async with asyncio.timeout(6):  # several heartbeats of both servers
            async for raw in ws:
                message = json.loads(raw)
                if message["type"] == "event":
                    events.append(message["data"])
    except TimeoutError:
        pass  # still open
    except websockets.ConnectionClosed:
        pass
    assert all(e["type"] != "full_time" for e in events)
    if ws.close_code is not None:
        assert ws.close_code == 1012
        ws = await reconnect(b.ws(sid))
        assert [m["type"] for m in await receive(ws, 2)] == ["meta", "league"]
    a.shared._spawn = lambda work: work.close()  # type: ignore[method-assign, attr-defined]
    for task in list(a.shared.tasks):
        task.cancel()  # A crashes: no goodbye, no more heartbeats
    last: dict[str, Any] = {}
    async with asyncio.timeout(10):
        async for raw in ws:
            last = json.loads(raw)
    assert ws.close_code == 1000
    assert (last["type"], last["data"]["type"]) == ("event", "full_time")


async def reconnect(url: str) -> Any:
    """The screen's client retrying, as frontend/src/lib/socket.ts does after a 1012."""
    for _ in range(20):
        try:
            return await websockets.connect(url)
        except websockets.InvalidStatus:
            await asyncio.sleep(0.25)
    raise AssertionError("never reconnected")


async def test_a_knockout_pressed_on_b_reaches_owner_a(servers: tuple[Server, Server]) -> None:
    """#90's knockout over HTTP on B for a match A owns: armed on A, and A's arm id in B's answer."""
    a, b = servers
    made = await create(a)
    sid, auth = made["session_id"], {"authorization": f"Bearer {made['phone_token']}"}
    async with httpx.AsyncClient(base_url=b.base) as client:
        one = await client.post(f"/api/sessions/{sid}/chaos", json={"manager": "mgr_3"}, headers=auth)
        assert a.session(sid).chaos == "mgr_3"  # armed on A for that manager alone
        every = await client.post(f"/api/sessions/{sid}/chaos", headers=auth)
        bad = await client.post(f"/api/sessions/{sid}/chaos", json={"manager": "mgr_9"}, headers=auth)
    assert (one.status_code, one.json()["arm"]) == (202, 1)
    assert "mgr_3" in one.json()["status"]
    assert (every.status_code, every.json()["arm"]) == (202, 2)
    assert bad.status_code == 422  # refused on B by the request's own schema, as on one server
    session = a.session(sid)
    assert (session.chaos, session.arms) == ("all", 2)


async def test_a_press_on_b_while_owner_as_inbox_reconnects_is_busy(servers: tuple[Server, Server]) -> None:
    """Redis drops the inbox connections; A's heartbeat stays current. In the 1 s before A's inbox is back (_listen's
    retry), B answers a press 503, which says try again, not 404, which says the session does not exist. Once it is
    back, the same press goes through (#134)."""
    a, b = servers
    made = await create(a)
    sid, auth = made["session_id"], {"authorization": f"Bearer {made['phone_token']}"}
    redis = Redis.from_url(URL, decode_responses=True)

    async def heard() -> list[int]:
        """How many listen on A's inbox and on B's, from Redis: the listening flags lag behind a kill."""
        return [int((await redis.pubsub_numsub(inbox(s.shared.id)))[0][1]) for s in (a, b)]

    async with httpx.AsyncClient(base_url=b.base) as client:
        try:
            await redis.client_kill_filter(_type="pubsub")
            async with asyncio.timeout(1):
                while (await heard())[0]:
                    await asyncio.sleep(0.01)
            chaos = await client.post(f"/api/sessions/{sid}/chaos", headers=auth)
            style = await client.post(f"/api/sessions/{sid}/style", json={"style": "analyst"}, headers=auth)
            unheard = (await heard())[0] == 0  # both answers came in the gap (a slow machine fails here, not later)
            assert (chaos.status_code, style.status_code, unheard) == (503, 503, True)
            assert not a.session(sid).chaos
            async with asyncio.timeout(5):
                while (await heard()) != [1, 1]:
                    await asyncio.sleep(0.05)
        finally:
            await redis.aclose()
        again = await client.post(f"/api/sessions/{sid}/chaos", headers=auth)
    assert again.status_code == 202
    assert a.session(sid).chaos == "all"
