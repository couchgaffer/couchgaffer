"""Smoke test of a deployed watch party: health, the lobby, a demo session, and an agent at work.

The short smoke passes only on an explanation a manager agent wrote: the plain-stat fallback that every
broken dependency falls back to (Foundry sign-in, the model, the network) is not enough. The full smoke
watches a whole match and fails unless every summary, recap, rulebook citation and Spanish text arrives.

Run daily (.github/workflows/smoke.yml), and after each deploy. From backend/:

  uv run python -m tools.smoke https://<app>.azurecontainerapps.io
  uv run python -m tools.smoke https://<app>.azurecontainerapps.io --full   # a whole match, with agent stats
"""

import asyncio
import json
import ssl
import statistics
import sys
import time
from collections import Counter
from typing import Any

import certifi
import httpx
import websockets

from watchparty.agents.base import says_zero_points

AGENT_CUE_WITHIN_S = 90  # the first key moment lands within about 10 s of kick-off at 30x
TITLE = "<title>Couch Gaffer</title>"  # this exact tag, a substring check: the old name no longer passes (#120)
MATCH_WITHIN_S = 420  # three minutes of match, with room
RECAPS_WITHIN_S = 600  # after full time: the recap job renders five videos (#15)
PHONES = [f"phone:mgr_{n}" for n in range(1, 6)]


def expect(ok: bool, what: str) -> None:
    if not ok:
        raise SystemExit(f"smoke test failed: {what}")


def ws_url(base: str, path: str) -> str:
    return base.replace("https://", "wss://").replace("http://", "ws://") + path


def tls_for(url: str) -> ssl.SSLContext | None:
    return ssl.create_default_context(cafile=certifi.where()) if url.startswith("wss://") else None  # as httpx


async def collect(url: str, out: list[dict[str, Any]], done: asyncio.Event) -> None:
    """One screen's messages until told to stop, or until the server closes the socket: then it is done (#47)."""
    async with websockets.connect(url, open_timeout=20, ssl=tls_for(url)) as ws:
        while not done.is_set():
            try:
                out.append(json.loads(await asyncio.wait_for(ws.recv(), 1)))
            except TimeoutError:
                continue
            except websockets.ConnectionClosed:
                return


def watch(base: str, session: str, token: str, screens: dict[str, list[dict[str, Any]]],
          done: asyncio.Event) -> list[asyncio.Task[None]]:  # fmt: skip
    return [asyncio.create_task(collect(ws_url(base, f"/ws/{session}/{s}" + ("" if s == "group" else
            f"?token={token}")), out, done)) for s, out in screens.items()]  # fmt: skip


def cues_in(screens: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [m["data"] for out in screens.values() for m in out if m["type"] == "cue"]


async def agent_cue(base: str, session: str, token: str) -> dict[str, Any]:
    """The first explanation a manager agent wrote, on any phone."""
    screens: dict[str, list[dict[str, Any]]] = {s: [] for s in PHONES}
    done = asyncio.Event()
    tasks = watch(base, session, token, screens, done)
    deadline, found = time.monotonic() + AGENT_CUE_WITHIN_S, None
    while found is None and time.monotonic() < deadline and not all(t.done() for t in tasks):
        await asyncio.sleep(0.5)
        found = next((c for c in cues_in(screens) if c["kind"] == "explanation" and c["source"] == "manager"), None)
    done.set()
    await asyncio.gather(*tasks)
    if found is None:
        sources = Counter(c["source"] for c in cues_in(screens) if c["kind"] == "explanation")
        raise SystemExit(f"smoke test failed: no explanation from a manager agent within {AGENT_CUE_WITHIN_S} s;"
                         f" explanations by source: {dict(sources)}")  # fmt: skip
    return found


async def full_match(base: str, session: str, token: str) -> None:
    """Watch every screen to full time, report what the agents did as measured in-region, and fail on gaps."""
    screens: dict[str, list[dict[str, Any]]] = {"group": [], **{s: [] for s in PHONES}}
    done = asyncio.Event()
    tasks = watch(base, session, token, screens, done)
    deadline = time.monotonic() + MATCH_WITHIN_S
    full_time: float | None = None
    while time.monotonic() < deadline and not all(t.done() for t in tasks):  # every screen closed: nothing more
        await asyncio.sleep(1)
        cues = cues_in(screens)
        if full_time is None and any(
            m["type"] == "event" and m["data"]["type"] == "full_time" for m in screens["group"]
        ):
            full_time, deadline = time.monotonic(), time.monotonic() + RECAPS_WITHIN_S  # then the recaps (#15)
        summaries = sum(c["kind"] == "summary" for c in cues)
        recaps = [c for c in cues if c["kind"] == "recap_ready"]
        # The group socket is read on its own, so it can still be behind the phones on the whistle (#59)
        if full_time is not None and summaries == 10 and (len(recaps) == 5 or "--no-recaps" in sys.argv):
            break
    done.set()
    await asyncio.gather(*tasks)
    decisions = [m["data"] for m in screens["group"] if m["type"] == "decision"]
    bids = [r for d in decisions for r in d["scores"]]
    cues = cues_in(screens)
    latency = sorted(r["latency_ms"] for r in bids if "latency_ms" in r)
    host = sorted(d["latency_ms"] for d in decisions if d["source"] == "host")
    print(f"decisions: {dict(Counter(d['source'] for d in decisions))}")
    timing = f"latency p50 {statistics.median(latency):.0f} ms, max {latency[-1]} ms" if latency else "latency n/a"
    print(f"bids: {dict(Counter(r.get('status') for r in bids))}; {timing}")  # none on code alone: still report
    if host:
        print(f"host: p50 {statistics.median(host):.0f} ms, max {host[-1]} ms")
    print(f"summaries: {dict(Counter(c['source'] for c in cues if c['kind'] == 'summary'))} of 10")
    grounded = [c for c in cues if c["kind"] == "summary" and c["payload"].get("rulebook")]
    print(f"summaries citing the rulebook (Foundry IQ): {len(grounded)} of 10")
    for c in grounded[:2]:
        titles = " · ".join(r["title"] for r in c["payload"]["rulebook"])
        print(f"  {c['screen']} {c['payload']['headline']}: {c['payload']['body']}\n    rulebook: {titles}")
    written = [c for c in cues if c["kind"] in ("explanation", "summary") and c["screen"] != "group"]
    print(f"phone text languages: {dict(Counter(c['payload'].get('lang') for c in written))}")
    for c in [c for c in written if c["payload"].get("lang") not in (None, "en")][:2]:
        print(
            f"  {c['screen']} ({c['payload']['lang']}): {c['payload']['body']}\n    en: {c['payload'].get('body_en')}"
        )
    for c in cues:
        if c["kind"] == "host_reason":
            print(f"  host reason: {c['payload']['body']}")
    # #88: a moment worth 0 IP is declined or written about the table, never "+0 IP for you"
    zero = [c for c in written if c["kind"] == "explanation" and (c["payload"]["headline"] == "No change" or any(
        says_zero_points(c["payload"].get(k) or "") for k in ("body", "body_en")))]  # fmt: skip
    print(f"phone explanations saying a moment gave 0 points: {len(zero)}")
    for c in zero[:2]:
        print(f"  {c['screen']} {c['payload']['headline']}: {c['payload']['body']}")
    recaps = [c for c in cues if c["kind"] == "recap_ready"]
    if full_time is not None:
        print(f"recaps: {len(recaps)} of 5, the last {time.monotonic() - full_time:.0f} s after full time")
    playable = 0
    async with httpx.AsyncClient(timeout=30) as http:
        for c in recaps:
            head = await http.get(c["payload"]["recap_url"], headers={"Range": "bytes=0-1023"})
            playable += head.status_code == 206
            print(f"  {c['screen']}: {head.status_code} {head.headers.get('content-type')} "
                  f"{head.headers.get('content-range')}")  # fmt: skip
    summaries = [c for c in cues if c["kind"] == "summary"]
    # Written by an agent: a code fallback carries Spanish and its copy too, so it proves nothing about Spanish (#47)
    spanish = [c for c in written if c["payload"].get("lang") == "es" and c["payload"].get("body_en")
               and c["source"] == "manager"]  # fmt: skip
    gaps = [
        *([] if full_time is not None else ["no full-time whistle"]),
        *([] if decisions else ["no group-screen decisions"]),
        *([] if any(c["kind"] == "explanation" and c["source"] == "manager" for c in cues)
          else ["no explanation from a manager agent"]),
        *([] if len(summaries) == 10 else [f"{len(summaries)} of 10 summaries"]),
        *([] if any(c["source"] == "manager" for c in summaries) else ["no summary from the summary agent"]),
        *([] if grounded else ["no summary cites the rulebook (Foundry IQ)"]),
        *([] if spanish else ["no Spanish text with its English copy"]),
        *([] if not zero else [f"{len(zero)} phone explanations say a moment gave 0 points"]),
        *([] if "--no-recaps" in sys.argv or len(recaps) == 5 else [f"{len(recaps)} of 5 recaps"]),
        *([] if playable == len(recaps) else [f"{len(recaps) - playable} recap links do not answer a range request"]),
    ]  # fmt: skip
    expect(not gaps, "; ".join(gaps))


async def smoke(base: str, full: bool = False) -> None:
    async with httpx.AsyncClient(base_url=base, timeout=20) as http:
        health = (await http.get("/health")).json()
        # agents off is a failure too; other keys (recaps, #204) do not decide the smoke
        expect(health.get("status") == "ok" and health.get("agents") is True, f"health: {health}")
        page = (await http.get("/")).text
        expect(TITLE in page, "lobby page")
        expect(len((await http.get("/api/lobby")).json()["league"]["managers"]) == 5, "lobby data")
        made = await http.post("/api/sessions", json={"seed": "demo", "manager": "mgr_1"})
        expect(made.status_code == 201, f"session: {made.status_code} {made.text}")
        session, token = made.json()["session_id"], made.json()["phone_token"]
    if full:
        await full_match(base, session, token)
        print(f"ok: {base} played a whole match with every agent")
        return
    start = time.monotonic()
    cue = await agent_cue(base, session, token)
    print(f"ok: {base}: {cue['screen']} got a manager agent's explanation {time.monotonic() - start:.1f} s in")


if __name__ == "__main__":
    asyncio.run(smoke(sys.argv[1].rstrip("/"), "--full" in sys.argv[2:]))
