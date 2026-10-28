"""What the recap job needs from a session, written once at full time: plain JSON, so the job needs no app.

Version 1 is a single match: one meta and its whole frame stream. Version 2 is a gameweek (#94): one entry per
fixture with its meta, its goals and only the frames around its climaxes, which is all any clip can draw, so
three fixtures make a smaller bundle than one match.
"""

import bisect
import itertools
from typing import TYPE_CHECKING, Any

from watchparty.recap.plan import AFTER_MS, BEFORE_MS
from watchparty.sim.engine import FRAME_MS

if TYPE_CHECKING:
    from watchparty.app.session import Session
    from watchparty.sim.gameweek import Gameweek

VERSION = 1
GAMEWEEK_VERSION = 2
VERSIONS = (VERSION, GAMEWEEK_VERSION)


def make_bundle(session: "Session") -> dict[str, Any]:
    return {
        "v": VERSION,
        "session_id": session.id,
        "meta": session.match.meta,
        "league": session.league,  # as this session set it up: the judge's manager is "You"
        "frames": session.match.frames,
        "climaxes": list(session.climaxes.values()),
        "stakes": session.stakes_log,
        "log": session.log,  # every cue and decision: the edit list
        "table": session.engine.table(),
    }


def gameweek_bundle(
    *,
    session_id: str,
    gameweek: "Gameweek",
    league: dict[str, Any],
    climaxes: list[dict[str, Any]],
    stakes: list[list[dict[str, Any]]],
    log: list[dict[str, Any]],
    table: list[dict[str, Any]],
) -> dict[str, Any]:
    """A gameweek session's bundle. Call it where make_bundle is called, before the frames are dropped.

    `climaxes` are the climax events the session saw, each with its fixture_id; `stakes` one list per key moment,
    `log` every cue and decision and `table` the gameweek table, as make_bundle takes them from a single match.
    A fixture that stopped early has only the climaxes it reached, so it keeps only their frames. A cue or
    decision about a moment that came without a fixture_id gets its climax's, in a copy: the session's log is
    not changed.
    """
    unfiled = [e["moment_id"] for e in climaxes if "fixture_id" not in e]
    if unfiled:  # its clip could not say which match it is from
        raise ValueError(f"session {session_id}: climaxes without a fixture_id: {', '.join(unfiled)}")
    fixture_of = {e["moment_id"]: e["fixture_id"] for e in climaxes}
    log = [_with_fixture(entry, fixture_of) for entry in log]
    fixtures = []
    for match in gameweek.fixtures:
        fixture_id = match.meta["fixture_id"]
        own = [e["t"] for e in climaxes if e["fixture_id"] == fixture_id]
        goals = [{"t": e["t"], "team": e["team"]} for e in match.events
                 if e["type"] == "shot" and e["outcome"] == "goal"]  # fmt: skip
        fixtures.append({"fixture_id": fixture_id, "meta": match.meta, "goals": goals,
                         "frames": trimmed(match.frames, own)})  # fmt: skip
    meta = {k: v for k, v in gameweek.meta.items() if k != "fixtures"}  # each fixture's meta is in its own entry
    return {
        "v": GAMEWEEK_VERSION,
        "session_id": session_id,
        "gameweek": meta,
        "league": league,
        "fixtures": fixtures,
        "climaxes": climaxes,
        "stakes": stakes,
        "log": log,
        "table": table,
    }


def _with_fixture(entry: dict[str, Any], fixture_of: dict[str, str]) -> dict[str, Any]:
    if "fixture_id" in entry or entry.get("moment_id") not in fixture_of:
        return entry
    return {**entry, "fixture_id": fixture_of[entry["moment_id"]]}


def trimmed(frames: list[dict[str, Any]], climax_times: list[int]) -> list[dict[str, Any]]:
    """The frames a clip around any of these climaxes reads: for each window, the last frame at or before its
    start through the first frame after its end. Positions.at interpolates between those two neighbours, so a
    clip draws the same from these frames as from the whole stream."""
    times = [f["t"] for f in frames]
    keep: set[int] = set()
    for t in climax_times:
        first = max(bisect.bisect_right(times, max(0, t - BEFORE_MS)) - 1, 0)
        last = min(bisect.bisect_right(times, t + AFTER_MS), len(frames) - 1)
        keep.update(range(first, last + 1))
    return [frames[i] for i in sorted(keep)]


def covers(frames: list[dict[str, Any]], start: float, end: float) -> bool:
    """Whether a clip from start to end has a frame at or before its start, one at or after its end, and none
    missing between: frames that skip the window would pass a check of the first and last frame only, and the
    dots would slide across the gap (or stand still past the ends) for part of the clip."""
    times = [f["t"] for f in frames]
    first, last = bisect.bisect_right(times, start) - 1, bisect.bisect_left(times, end)
    if first < 0 or last >= len(times):
        return False
    return all(b - a <= FRAME_MS for a, b in itertools.pairwise(times[first : last + 1]))


V1_KEYS = {"v", "session_id", "meta", "frames", "league", "climaxes", "stakes", "log", "table"}
V2_KEYS = {"v", "session_id", "gameweek", "fixtures", "league", "climaxes", "stakes", "log", "table"}
FIXTURE_KEYS = {"fixture_id", "meta", "goals", "frames"}


def check(bundle: dict[str, Any]) -> None:
    """Refuses a bundle the job could only half render, with what is wrong, before any video is started."""
    v, sid = bundle.get("v"), bundle.get("session_id", "?")
    if v not in VERSIONS:
        raise ValueError(f"unknown bundle version {v!r}")
    keys = V1_KEYS if v == VERSION else V2_KEYS
    missing, unexpected = sorted(keys - set(bundle)), sorted(set(bundle) - keys)
    if missing or unexpected:  # a layout the job would read as the other version, or not at all
        raise ValueError(f"bundle {sid} (v{v}): missing {missing}, unexpected {unexpected}")
    if v == VERSION:
        return
    entries = bundle["fixtures"]
    bad = [f.get("fixture_id", i) for i, f in enumerate(entries) if set(f) != FIXTURE_KEYS]
    if bad:
        raise ValueError(f"bundle {sid}: fixture entries {bad} need exactly {sorted(FIXTURE_KEYS)}")
    by_id = {f["fixture_id"]: f for f in entries}
    if len(by_id) != len(entries):
        raise ValueError(f"bundle {sid}: a fixture_id appears twice")
    for e in bundle["climaxes"]:
        fixture = by_id.get(e.get("fixture_id"))
        if fixture is None:
            raise ValueError(f"bundle {sid}: moment {e['moment_id']} names fixture {e.get('fixture_id')!r}, "
                             f"which the bundle does not hold")  # fmt: skip
        if not covers(fixture["frames"], max(0, e["t"] - BEFORE_MS), e["t"] + AFTER_MS):
            raise ValueError(f"bundle {sid}: fixture {e['fixture_id']} has no frames around moment {e['moment_id']}")
