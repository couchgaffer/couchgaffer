"""A gameweek (#94): three fixtures among the six clubs, built up front from one seed, on one match clock.

build_gameweek(seed) draws one full time, a pairing of the six clubs, three fixture seeds and the moment
slots for the whole gameweek, simulates each fixture with them, and returns the fixtures and one merged
stream. Every fixture kicks off at t = 0 and is given the same full time, so its whistles land within about
half a match minute of the others'; the session (slice 5) blows the gameweek's own whistles.

The simulator keeps p_home_n and p_away_n; rekey() turns them into p_<club short name>_n afterwards, so a
player id is unique across the gameweek. Moment ids and event seq are renumbered over the merged stream, so
neither repeats across fixtures and every key that uses them keeps working with no fixture in it.
"""

import functools
import itertools
import random
from dataclasses import dataclass
from typing import Any

from watchparty.sim.clubs import CLUBS
from watchparty.sim.engine import FULL, MINUTE, Match, simulate

FIXTURES = 3
MOMENTS = (15, 18)  # five or six per fixture, decided by the owner on #94
SLOT_SPACING = 4 * MINUTE  # a scenario lasts under a minute, so climaxes across fixtures stay 3+ minutes apart
FIRST_START = 3 * MINUTE
# No start here, so no climax lands while the fixtures are blowing for half time; 46:30 is _schedule's own restart
# (engine.HALF_TIME_GAP). A moment that starts by 42:00 can still run past 43:00 (filler play runs to a break
# first): it climaxes before 43:00 only because fits() checks it, and a plan that fails is drawn again.
SLOT_GAP = (42 * MINUTE, 46 * MINUTE + 30_000)
LAST_START_BEFORE_WHISTLE = 3 * MINUTE  # the last climax ends at least 2 minutes before full time
CLIMAX_SPACING = 3 * MINUTE  # 6 real seconds at 30x: the group screen holds a card that long
CLIMAX_GAP = (43 * MINUTE, 46 * MINUTE + 30_000)
WHISTLE_SPREAD = 30_000
ATTEMPTS = 10


@dataclass(frozen=True)
class Plan:
    """What a gameweek seed decides before anything is simulated."""

    full_time: int
    pairs: tuple[tuple[dict[str, Any], dict[str, Any]], ...]  # (home, away) per fixture
    seeds: tuple[int, ...]
    starts: tuple[tuple[int, ...], ...]  # each fixture's moment start times, rising


@dataclass
class Gameweek:
    meta: dict[str, Any]  # the `gameweek` message's data
    fixtures: list[Match]  # fx_1, fx_2, fx_3, rekeyed and renumbered
    events: list[dict[str, Any]]  # every fixture's events, merged by (t, fixture index, seq); the same dicts
    frames: list[dict[str, Any]]  # every fixture's frames, merged by (t, fixture index)
    plan: Plan  # the plan these fixtures were built from


def _slots(rng: random.Random, full_time: int) -> list[int]:
    """15 to 18 start times, SLOT_SPACING apart, from 3:00 to 3 minutes before full time, none in SLOT_GAP.

    Drawn on a timeline with the gap cut out, as _schedule does: spacing there is at least the spacing on the
    clock. The worst case, 18 moments in a 92-minute match, needs 68 of the 81.5 minutes available (92 less
    3 at each end and the 4.5-minute gap).
    """
    width = SLOT_GAP[1] - SLOT_GAP[0]
    span = full_time - LAST_START_BEFORE_WHISTLE - FIRST_START - width
    n = rng.randint(*MOMENTS)
    slack = span - (n - 1) * SLOT_SPACING
    starts = []
    for i, offset in enumerate(sorted(rng.uniform(0, slack) for _ in range(n))):
        v = int(FIRST_START + offset + i * SLOT_SPACING)
        starts.append(v if v < SLOT_GAP[0] else v + width)
    return starts


def _deal(rng: random.Random, starts: list[int]) -> tuple[tuple[int, ...], ...]:
    """Starts to fixtures in rounds of three, each round in a random fixture order, so every fixture gets five
    or six moments spread over both halves (a fixture with none after half time would never blow for it)."""
    dealt: list[list[int]] = [[] for _ in range(FIXTURES)]
    for r in range(0, len(starts), FIXTURES):
        order = rng.sample(range(FIXTURES), FIXTURES)
        for fixture, start in zip(order, starts[r : r + FIXTURES], strict=False):
            dealt[fixture].append(start)
    return tuple(tuple(d) for d in dealt)


def plan_gameweek(seed: int, attempt: int = 0) -> Plan:
    """The seed's plan; a later attempt is another plan, drawn the same way from the seed and the attempt."""
    rng = random.Random(seed if attempt == 0 else f"{seed}/{attempt}")  # noqa: S311  reproducible, not security
    full_time = FULL + rng.randint(2, 5) * MINUTE  # as simulate draws a single match's stoppage
    clubs = rng.sample(CLUBS, len(CLUBS))
    pairs = tuple((clubs[2 * i], clubs[2 * i + 1]) for i in range(FIXTURES))
    seeds = tuple(rng.randrange(2**31) for _ in range(FIXTURES))
    return Plan(full_time, pairs, seeds, _deal(rng, _slots(rng, full_time)))


def rekey(match: Match) -> Match:
    """The same match with p_home_n and p_away_n renamed p_<short>_n from its meta's short names, lower case.

    A new Match: the input, which may sit in a cache, is not changed. An id outside the two rosters raises.
    """
    ids = {
        p["id"]: f"p_{match.meta[side]['short_name'].lower()}_{p['id'].rsplit('_', 1)[1]}"
        for side in ("home", "away")
        for p in match.meta[side]["players"]
    }
    meta = {**match.meta}
    for side in ("home", "away"):
        meta[side] = {**match.meta[side], "players": [{**p, "id": ids[p["id"]]} for p in match.meta[side]["players"]]}
    events = [{**e, **{k: ids[e[k]] for k in ("player", "receiver", "opponent") if k in e}} for e in match.events]
    frames = [{**f, "players": [{**p, "id": ids[p["id"]]} for p in f["players"]]} for f in match.frames]
    return Match(meta, events, frames)


def fits(plan: Plan, fixtures: list[Match]) -> bool:
    """What the slots aim for, checked on the simulated fixtures: a moment can start a little after its slot
    (filler play runs to a break first) and lasts up to a minute, so 4 minutes between slots does not prove
    3 between climaxes. Climaxes at least 3 match minutes apart, none from 43:00 to 46:30 or in the last 2
    minutes before full time, and each whistle within half a match minute across the fixtures."""
    climaxes = sorted(e["t"] for m in fixtures for e in m.events if e.get("climax"))
    whistles = [
        [e["t"] for m in fixtures for e in m.events if e["type"] == kind] for kind in ("half_time", "full_time")
    ]
    return (
        all(b - a >= CLIMAX_SPACING for a, b in itertools.pairwise(climaxes))
        and not any(CLIMAX_GAP[0] <= t < CLIMAX_GAP[1] for t in climaxes)
        and climaxes[-1] <= plan.full_time - 2 * MINUTE
        and all(len(w) == FIXTURES and max(w) - min(w) <= WHISTLE_SPREAD for w in whistles)
    )


def build_gameweek(seed: int, speed: float = 30) -> Gameweek:
    for attempt in range(ATTEMPTS):  # the fallback, not the guarantee: it raises after ATTEMPTS plans, and no seed
        # from 0 to 2999 needs a second one
        plan = plan_gameweek(seed, attempt)
        fixtures = [
            rekey(simulate(fseed, speed, clubs=pair, starts=list(starts), full_time=plan.full_time))
            for fseed, pair, starts in zip(plan.seeds, plan.pairs, plan.starts, strict=True)
        ]
        if fits(plan, fixtures):
            return _merge(seed, speed, plan, fixtures)
    raise RuntimeError(f"no plan for gameweek {seed} fits in {ATTEMPTS} attempts")


def _merge(seed: int, speed: float, plan: Plan, fixtures: list[Match]) -> Gameweek:
    """Set fixture ids, renumber moment ids and seq over the gameweek, and merge the fixtures' streams."""
    # Moment ids m_01 upwards in start order across the gameweek; each fixture numbered its own from m_01.
    order = sorted((start, i, n) for i, starts in enumerate(plan.starts) for n, start in enumerate(starts, start=1))
    moment_ids = {(i, f"m_{n:02d}"): f"m_{k:02d}" for k, (_, i, n) in enumerate(order, start=1)}
    for i, match in enumerate(fixtures):
        fixture_id = f"fx_{i + 1}"
        match.meta |= {"fixture_id": fixture_id, "match_id": f"match_gw_{seed}_{i + 1}"}
        for e in match.events:
            e["fixture_id"] = fixture_id
            if "moment_id" in e:
                e["moment_id"] = moment_ids[(i, e["moment_id"])]
        for f in match.frames:
            f["fixture_id"] = fixture_id
    events = sorted(
        (e for match in fixtures for e in match.events), key=lambda e: (e["t"], int(e["fixture_id"][3:]), e["seq"])
    )
    for seq, e in enumerate(events):
        e["seq"] = seq  # gap-free and strictly increasing over the gameweek (match-event.schema.json)
    frames = sorted((f for match in fixtures for f in match.frames), key=lambda f: (f["t"], int(f["fixture_id"][3:])))
    meta = {"gameweek_id": f"gw_{seed}", "seed": seed, "speed": speed, "playback_delay_ms": 5000,
            "fixtures": [m.meta for m in fixtures]}  # fmt: skip
    return Gameweek(meta, fixtures, events, frames, plan)


# A gameweek is three simulated matches, about 42 MB, and only ever read once built, like `simulated`
# (app/session.py); two fit beside that cache inside one replica's memory (#94).
built = functools.lru_cache(maxsize=2)(build_gameweek)
