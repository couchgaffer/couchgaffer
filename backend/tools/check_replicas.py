"""Is a match created on one app server watched, and pressed, from another, through the shared Redis (#180)?

Behind Container Apps ingress nobody chooses the replica a request lands on, so this does not try to. It reads
Redis instead, as the servers do (shared.py): the live servers in {wp}:alive, the session's owner in its registry
entry, and {wp}:subs:<owner>, the screens the owner is serving for another server. It:

1. needs at least two live servers
2. creates a demo session on the first URL and reads its owner
3. opens SCREENS group screens at once (round robin over the URLs given), each on its own connection, and needs
   live match frames, in order, on every one
4. while they are open, needs the owner to be serving more screens for another server than just before they
   opened: proof that one of them landed on a server that is not the owner and watched through it (a viewer
   joining elsewhere in that window could also raise it; on a demo with no other traffic nobody does)
5. presses the judge's failure button PRESSES times (round robin) and needs 202 every time, with arm ids that
   count up one by one: every press reached the one session on its owner. With one URL it cannot tell which
   presses the ingress sent to the other replica, so it proves no 503 and no 404 over several tries, not that
   a given press was forwarded; with two URLs (local servers) every second press is

It fails closed: anything short, an error or a timeout exits 1, and it prints PASS only when all five held. With
one URL (the live app), step 4 can miss by chance when every screen lands on the owner; the run then fails and
says so, and running it again is the answer. The match it starts calls the agents, as the smoke test does, and
holds a seat against the limits until its screens close and the match stops unwatched (abandon_after_s, 30 s).
From backend/:

  uv run python -m tools.check_replicas <redis url> <app url> [<second app url>]
"""

import asyncio
import json
import sys
from collections.abc import Callable
from typing import Any

import httpx
import websockets
from redis.asyncio import Redis

from tools.smoke import tls_for, ws_url
from watchparty.app.shared import ALIVE, partial_client, registry, serving

__all__ = ["ALIVE", "check", "main"]

SCREENS = 8
FRAMES = 5  # live frames each screen must see
PRESSES = 6
WITHIN_S = 60.0


async def live_servers(redis: Redis) -> list[str]:
    """Server ids whose heartbeat has not expired, by the Redis server's clock as shared.py counts them."""
    seconds, micros = await redis.time()
    now_ms = seconds * 1000 + micros // 1000
    alive = await redis.hgetall(ALIVE)
    return sorted(str(server) for server, until in alive.items() if int(until) > now_ms)  # decoded: partial_client


async def frames_on(url: str, opened: asyncio.Event, release: asyncio.Event) -> list[float]:
    """One screen: the times of its first FRAMES live frames, then held open until release."""
    times: list[float] = []
    async with websockets.connect(url, open_timeout=20, ssl=tls_for(url)) as ws:
        async with asyncio.timeout(WITHIN_S):
            while len(times) < FRAMES:
                message = json.loads(await ws.recv())
                if message["type"] == "frame":
                    times.append(float(message["data"]["t"]))
        opened.set()
        await release.wait()
    return times


async def check(bases: list[str], connect: Callable[[float | None], Redis]) -> list[str]:
    """Every problem found; empty only when all five steps held."""
    redis = connect(10.0)
    try:
        servers = await live_servers(redis)
        print(f"live servers: {len(servers)}")
        if len(servers) < 2:
            return [f"{len(servers)} live server(s) in {ALIVE}; two are needed"]
        async with httpx.AsyncClient(timeout=20) as http:
            made = await http.post(f"{bases[0]}/api/sessions", json={"seed": "demo", "manager": "mgr_1"})
            if made.status_code != 201:
                return [f"POST /api/sessions answered {made.status_code}: {made.text[:200]}"]
            sid, token = made.json()["session_id"], made.json()["phone_token"]
            owner = str(await redis.hget(registry(sid), "owner"))  # "None" when missing: never a live server
            print(f"session {sid[:6]}...: owner {owner}")
            if owner not in servers:
                return [f"the session's owner {owner} is not a live server"]
            problems = await watch_and_press(http, bases, sid, token, owner, redis)
        return problems
    finally:
        await redis.aclose()


async def watch_and_press(http: httpx.AsyncClient, bases: list[str], sid: str, token: str, owner: str,
                          redis: Redis) -> list[str]:  # fmt: skip
    """Steps 3 to 5: the screens, read while they are all open, then the presses."""
    before = int(await redis.scard(serving(owner)))  # remote screens the owner had already
    release = asyncio.Event()
    opened = [asyncio.Event() for _ in range(SCREENS)]
    screens = [asyncio.create_task(frames_on(ws_url(bases[n % len(bases)], f"/ws/{sid}/group"), opened[n], release))
               for n in range(SCREENS)]  # fmt: skip
    problems: list[str] = []
    try:
        async with asyncio.timeout(WITHIN_S + 10):  # every screen has its frames, or one has stopped
            while not all(e.is_set() for e in opened) and not any(t.done() for t in screens):
                await asyncio.sleep(0.1)
        if not any(t.done() for t in screens):  # a stopped screen is reported below, from its own result
            problems += await remote_and_presses(http, bases, sid, token, owner, redis, before)
    finally:
        release.set()
        results = await asyncio.gather(*screens, return_exceptions=True)
    for n, times in enumerate(results):
        if isinstance(times, BaseException):
            problems.append(f"screen {n + 1}: {type(times).__name__}: {times}")
        elif len(times) < FRAMES or times != sorted(times) or times[-1] <= times[0]:
            problems.append(f"screen {n + 1}: frames not live and in order: {times}")
    return problems


async def remote_and_presses(http: httpx.AsyncClient, bases: list[str], sid: str, token: str, owner: str,
                             redis: Redis, before: int) -> list[str]:  # fmt: skip
    problems = []
    remote = await redis.scard(serving(owner)) - before  # read while every screen is still open
    print(f"screens: {SCREENS} with {FRAMES} live frames each; served by the owner for another server: {remote}")
    if remote < 1:
        problems.append("every screen landed on the owner, so nothing was watched from another server; run again")
    arms: list[Any] = []
    for n in range(PRESSES):
        press = await http.post(f"{bases[n % len(bases)]}/api/sessions/{sid}/chaos",
                                headers={"authorization": f"Bearer {token}"})  # fmt: skip
        arms.append(press.json().get("arm") if press.status_code == 202 else press.status_code)
        if press.status_code != 202:
            problems.append(f"press {n + 1}: {press.status_code} {press.text[:200]}")
    print(f"presses: {PRESSES}, arm ids {arms}")
    if not problems and arms != list(range(arms[0], arms[0] + PRESSES)):
        problems.append(f"the arm ids {arms} do not count up one by one: the presses did not reach one session")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print("usage: python -m tools.check_replicas <redis url> <app url> [<second app url>]", file=sys.stderr)
        return 2
    bases = [b.rstrip("/") for b in argv[2:]]
    try:
        problems = asyncio.run(check(bases, partial_client(argv[1])))
    except Exception as exc:  # a failure to reach the app or Redis is a failed check, never a pass
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    for problem in problems:
        print(f"FAIL: {problem}", file=sys.stderr)
    if problems:
        return 1
    print("PASS: a match created on one server was watched through another, and every press reached it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
