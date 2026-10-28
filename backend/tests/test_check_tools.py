"""tools.check_redis_publish (#144) and tools.check_replicas (#180) against a real Redis and two app servers, and
against a Redis that miscounts. Needs Docker, as test_shared_redis does."""

import socket
from collections.abc import Callable
from typing import Any

import pytest
from redis.asyncio import Redis

import tests.test_shared_redis as two
from tests.test_shared_redis import Server, redis_container, servers  # noqa: F401  (fixtures)
from tools import check_redis_publish, check_replicas
from watchparty.app.shared import partial_client, serving

pytestmark = pytest.mark.redis


class Miscounting:
    """A publisher that lies the way a proxy could: the named fault on channels whose name contains `on`."""

    def __init__(self, real: Redis, fault: str, on: str) -> None:
        self.real, self.fault, self.on = real, fault, on

    def __getattr__(self, name: str) -> Any:
        return getattr(self.real, name)

    async def publish(self, channel: str, payload: str) -> int:
        if self.on not in channel or self.fault == "numsub":
            return int(await self.real.publish(channel, payload))
        if self.fault == "short":
            return int(await self.real.publish(channel, payload)) - 1
        if self.fault == "lost":  # the count is right but the message goes nowhere
            subscribers = dict(await self.real.pubsub_numsub(channel))[channel]
            await self.real.publish(channel + ":elsewhere", payload)
            return int(subscribers)
        return int(await self.real.publish(channel, payload)) + 1  # "extra"

    async def pubsub_numsub(self, *channels: str) -> list[tuple[str, int]]:
        got = await self.real.pubsub_numsub(*channels)
        return [(str(c), n - 1 if self.fault == "numsub" and self.on in str(c) else n) for c, n in got]


def lying(fault: str, on: str) -> Callable[[float | None], Redis]:
    real = partial_client(two.URL)

    def connect(timeout: float | None) -> Any:
        client = real(timeout)
        return Miscounting(client, fault, on) if timeout is not None else client  # the publisher has a timeout

    return connect


def test_publish_check_passes_on_a_real_redis(redis_container: str, capsys: Any) -> None:  # noqa: F811  cases 3-5
    assert check_redis_publish.main(["x", two.URL]) == 0
    out = capsys.readouterr().out
    assert out.count("subscribers 1, NUMSUB 1, PUBLISH 1, received 1/1") == check_redis_publish.BUCKETS + 1
    assert "{wp}:inbox:check-" in out
    assert "subscribers 2, NUMSUB 2, PUBLISH 2, received 2/2" in out
    assert "subscribers 0, NUMSUB 0, PUBLISH 0, received 0/0" in out
    assert "PASS" in out
    slots = [int(line.split("(slot ")[1].split(")")[0]) for line in out.splitlines() if "-two}" not in line and
             "-none}" not in line and "{wpcheck-" in line]  # fmt: skip
    assert sorted(s * check_redis_publish.BUCKETS // check_redis_publish.SLOTS for s in slots) == list(range(8))


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("fault", "on", "says"),
    [
        ("short", ":inbox:", "PUBLISH returned 0, 1 subscribed"),  # case 6
        ("numsub", "-two}", "PUBSUB NUMSUB said 1, 2 subscribed"),  # case 7
        ("lost", ":inbox:", "1 of 1 subscribers did not receive the message"),  # case 8
        ("extra", "-none}", "PUBLISH returned 1, 0 subscribed"),  # case 5: the control must read 0
    ],
)
async def test_publish_check_fails_on_a_miscount(redis_container: str, fault: str, on: str, says: str) -> None:  # noqa: F811
    problems = await check_redis_publish.check(lying(fault, on))
    assert len(problems) == 1
    assert says in problems[0]


def test_publish_check_fails_closed_when_redis_is_unreachable(capsys: Any) -> None:  # case 9
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # closed again before the check connects
    assert check_redis_publish.main(["x", f"redis://127.0.0.1:{port}/0"]) == 1
    captured = capsys.readouterr()
    assert "FAIL: ConnectionError" in captured.err
    assert "PASS" not in captured.out
    assert "PASS" not in captured.err


@pytest.mark.anyio
async def test_replicas_check_passes_when_b_watches_and_presses_a_match_made_on_a(
    servers: tuple[Server, Server],  # noqa: F811
    capsys: Any,
) -> None:  # case 19
    a, b = servers
    assert await check_replicas.check([a.base, b.base], partial_client(two.URL)) == []
    out = capsys.readouterr().out  # what it saw, so a check that skipped its steps cannot pass
    assert "live servers: 2" in out
    assert f"owner {a.shared.id}" in out
    assert "screens: 8 with 5 live frames each; served by the owner for another server: 4" in out  # B's half
    assert "presses: 6, arm ids [1, 2, 3, 4, 5, 6]" in out
    session = a.session(next(iter(a.app.state.store.sessions)))
    assert session.arms == 6  # every press reached the owner's session, B's three through A's inbox


@pytest.mark.anyio
async def test_replicas_check_fails_when_every_screen_is_on_the_owner(servers: tuple[Server, Server]) -> None:  # noqa: F811  case 21
    a, _ = servers
    problems = await check_replicas.check([a.base], partial_client(two.URL))
    assert problems == ["every screen landed on the owner, so nothing was watched from another server; run again"]


@pytest.mark.anyio
async def test_replicas_check_fails_with_one_live_server(servers: tuple[Server, Server]) -> None:  # noqa: F811  case 20
    a, b = servers
    await b.stop()
    redis = Redis.from_url(two.URL, decode_responses=True)
    await redis.hdel(check_replicas.ALIVE, b.shared.id)  # as if its heartbeat had expired
    await redis.aclose()
    problems = await check_replicas.check([a.base], partial_client(two.URL))
    assert problems == ["1 live server(s) in {wp}:alive; two are needed"]


@pytest.mark.anyio
async def test_replicas_check_ignores_remote_screens_the_owner_already_had(servers: tuple[Server, Server]) -> None:  # noqa: F811  case 21
    a, b = servers
    other = (await two.create(a))["session_id"]
    async with two.screen(b.ws(other)) as viewer:  # someone else's match on A, watched through B all along
        await two.receive(viewer, 3)
        redis = Redis.from_url(two.URL, decode_responses=True)
        assert await redis.scard(serving(a.shared.id)) == 1
        await redis.aclose()
        problems = await check_replicas.check([a.base], partial_client(two.URL))
    assert problems == ["every screen landed on the owner, so nothing was watched from another server; run again"]
