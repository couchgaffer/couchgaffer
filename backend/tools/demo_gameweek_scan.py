"""Scan gameweek seeds for the demo gameweek (#155): uv run python -m tools.demo_gameweek_scan [first] [last].

Each seed is scored with the real points engines on one shared Standings, as the gameweek session scores it. A
moment is worth points to a friend when its stake is above threshold with a points swing: that friend's agent
bids and their phone gets an explanation. A seed qualifies when every friend has at least two such moments, in
at least two fixtures; one moment reaches Priya's analyst phone and a casual friend (Jo, Arjun or Sam) together;
each of the three fixtures has a key moment in both halves; and, played as a whole session with fake agents (no
model calls), at least one close call goes to the host.
"""

import asyncio
import sys
from collections import defaultdict
from typing import Any

from tests.test_session import Host, Manager
from watchparty.app.session import Session
from watchparty.points import PointsEngine, Standings

HALF = 45 * 60_000
CASUAL, ANALYST = {"mgr_1", "mgr_3", "mgr_5"}, "mgr_2"  # Jo, Arjun and Sam; Priya


async def settle(_: float) -> None:
    for _ in range(50):
        await asyncio.sleep(0)


def worth(s: Session) -> dict[str, dict[str, str]]:
    """Per manager, every moment worth points to them, with its fixture."""
    standings = Standings(s.league)
    engines = {f: PointsEngine(meta, s.league, standings=standings, fixture_id=f) for f, meta in s.fixtures.items()}
    out: dict[str, dict[str, str]] = defaultdict(dict)
    for e in s.match.events:
        for st in engines[e["fixture_id"]].feed(e) or []:
            if st["above_threshold"] and st["delta_points"]:
                out[st["manager"]][st["moment_id"]] = st["fixture_id"]
    return out


async def scan(seed: int) -> dict[str, Any]:
    s = Session(seed=seed, manager="mgr_1", style="casual", lang="en", speed=30, sleep=settle,
                manager_agent=Manager(), host_agent=Host(), gameweek=True)  # fmt: skip
    moments = worth(s)
    managers = [m["id"] for m in s.league["managers"]]
    per_friend = {m: (len(moments.get(m, {})), len(set(moments.get(m, {}).values()))) for m in managers}
    reached: dict[str, set[str]] = defaultdict(set)
    for m, mine in moments.items():
        for mid in mine:
            reached[mid].add(m)
    shared = sorted(mid for mid, ms in reached.items() if ANALYST in ms and ms & CASUAL)
    climaxes = [e for e in s.match.events if e.get("climax")]
    both_halves = all({e["t"] >= HALF for e in climaxes if e["fixture_id"] == f} == {False, True}
                      for f in ("fx_1", "fx_2", "fx_3"))  # fmt: skip
    can_pass = all(n >= 2 and fx >= 2 for n, fx in per_friend.values()) and bool(shared) and both_halves
    decisions: list[dict[str, Any]] = []
    if can_pass:  # the whole session runs only for a seed that can pass
        group = s.subscribe("group")
        await s.run()
        got = [group.get_nowait() for _ in range(group.qsize())]
        decisions = [m["data"] for m in got if m and m["type"] == "decision"]
    host_calls = sum(1 for d in decisions if d["close_call"] and d["source"] == "host")
    return {"seed": seed, "ok": can_pass and host_calls >= 1, "per_friend": per_friend, "worth": len(reached),
            "moments": len(climaxes), "shared": shared, "host_close_calls": host_calls,
            "decisions": len(decisions), "both_halves": both_halves}  # fmt: skip


async def main(first: int, last: int) -> None:
    for seed in range(first, last + 1):
        print(await scan(seed), flush=True)


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 0, int(sys.argv[2]) if len(sys.argv) > 2 else 49))
