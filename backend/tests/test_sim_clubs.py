"""The gameweek's clubs, and a simulator that takes injected clubs, starts and full time (#147)."""

import hashlib
import json
import math
import re
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from watchparty.agents.checks import names_problems
from watchparty.sim import Match, simulate
from watchparty.sim.clubs import AWAY, CLUBS, GRINDLESTONE, HOME, KESTLEBURY, PELLBROOK, RAVENSHOLME, team

MINUTE = 60_000
# sha256 of the JSON of meta, events and frames for seeds 1 to 50, recorded from origin/develop at ff6858a
# before simulate took clubs, starts or full_time.
DEVELOP_HASHES = json.loads((Path(__file__).parent / "fixtures" / "sim_develop_hashes.json").read_text())
STARTS = [m * MINUTE for m in (4, 10, 16, 22, 28, 34, 40, 48, 54, 60, 66, 72, 78)]
FULL_TIME = 93 * MINUTE
_GRI = GRINDLESTONE["players"]
TEN_MEN = {**GRINDLESTONE, "players": _GRI[:10]}
KEEPER_LAST = {**GRINDLESTONE, "players": [*_GRI[1:], _GRI[0]]}
TWO_KEEPERS = {**GRINDLESTONE, "players": [*_GRI[:5], ("Second Keeper", 99, "GK", (0.3, 0.5)), *_GRI[6:]]}


def dump(m: Match) -> str:
    return json.dumps([m.meta, m.events, m.frames])


@cache
def injected(seed: int = 5) -> Match:
    return simulate(seed, clubs=(KESTLEBURY, RAVENSHOLME), starts=STARTS, full_time=FULL_TIME)


@pytest.mark.parametrize("seed", range(1, 51))
def test_defaults_give_the_same_match_as_develop(seed: int) -> None:
    assert hashlib.sha256(dump(simulate(seed)).encode()).hexdigest() == DEVELOP_HASHES[str(seed)]


def test_injected_clubs_name_the_teams_and_keep_side_ids() -> None:
    meta = injected().meta
    for side, club in (("home", KESTLEBURY), ("away", RAVENSHOLME)):
        for key in ("id", "name", "short_name", "colour", "formation"):
            assert meta[side][key] == club[key]
        assert [p["name"] for p in meta[side]["players"]] == [p[0] for p in club["players"]]
        assert [p["id"] for p in meta[side]["players"]] == [f"p_{side}_{n}" for n in range(1, 12)]


def test_injected_clubs_line_up_in_their_own_formation() -> None:
    """At kickoff the ball is on the spot and home has it, so every other player stands at their slot plus the
    frame's fixed shift and sway; that pins each side's positions to the injected club's slots."""
    first = injected().frames[0]
    assert first["t"] == 0
    at = {p["id"]: (p["x"], p["y"]) for p in first["players"]}
    for side, club in (("home", KESTLEBURY), ("away", RAVENSHOLME)):
        for i, (_, _, pos, (sa, sy)) in enumerate(club["players"]):
            pid = f"p_{side}_{i + 1}"
            if pid == "p_home_9":  # the kickoff taker is on the ball
                continue
            shift = 0.05 * (0.1 if pos == "GK" else 0.45) - (0.06 if side == "away" else 0)
            a = min(0.99, max(0.01, sa + shift + 0.012 * math.sin(i)))
            y = min(0.99, max(0.01, sy + 0.01 * math.cos(i)))
            assert at[pid] == (round(a if side == "home" else 1 - a, 4), round(y, 4)), pid


def test_injected_match_matches_the_contracts(contract_errors: Callable[[str, Any], list[str]]) -> None:
    m = injected()
    assert contract_errors("match-meta", m.meta) == []
    for event in m.events:
        assert contract_errors("match-event", event) == [], event
    for frame in m.frames[::20]:
        assert contract_errors("match-frame", frame) == [], frame


def test_injected_starts_are_the_moment_starts() -> None:
    by_moment: dict[str, list[dict[str, Any]]] = {}
    for e in injected().events:
        if "moment_id" in e:
            by_moment.setdefault(e["moment_id"], []).append(e)
    assert list(by_moment) == [f"m_{n:02d}" for n in range(1, len(STARTS) + 1)]
    ends = [*STARTS[1:], FULL_TIME]
    for (start, end), events in zip(zip(STARTS, ends, strict=True), by_moment.values(), strict=True):
        assert start <= events[0]["t"]
        assert events[-1]["t"] < end


@pytest.mark.parametrize("seed", range(1, 21))
def test_injected_full_time_is_the_whistle(seed: int) -> None:
    events = simulate(seed, full_time=FULL_TIME).events
    assert events[-1]["type"] == "full_time"
    assert FULL_TIME <= events[-1]["t"] < FULL_TIME + MINUTE  # the first break in play after full time
    assert [e["type"] for e in events].count("half_time") == 1


def test_injection_is_deterministic() -> None:
    again = simulate(5, clubs=(KESTLEBURY, RAVENSHOLME), starts=STARTS, full_time=FULL_TIME)
    assert dump(again) == dump(injected())
    assert dump(again) != dump(simulate(5))


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"starts": [STARTS[1], STARTS[0], *STARTS[2:]]}, "rise"),
        ({"starts": [*STARTS[:3], STARTS[2] + MINUTE, *STARTS[3:]]}, "rise"),
        ({"starts": [2 * MINUTE, *STARTS]}, "3:00"),
        ({"starts": [*STARTS, 91 * MINUTE]}, "full time"),
        ({"starts": [*STARTS, 94 * MINUTE], "full_time": 95 * MINUTE}, "full time"),
        ({"starts": []}, "3:00"),
        ({"starts": [*STARTS[:6], 44 * MINUTE, *STARTS[7:]]}, "half time"),
        ({"starts": [*STARTS[:7], 46 * MINUTE, *STARTS[8:]]}, "half time"),
        ({"starts": STARTS[:7]}, "after half time"),
        ({"full_time": 91 * MINUTE}, "full_time"),
        ({"full_time": 96 * MINUTE}, "full_time"),
        ({"clubs": (PELLBROOK, PELLBROOK)}, "itself"),
        ({"clubs": (PELLBROOK,)}, "two clubs"),
        ({"clubs": (PELLBROOK, GRINDLESTONE, HOME)}, "two clubs"),
        ({"clubs": (PELLBROOK, TEN_MEN)}, "eleven"),
        ({"clubs": (PELLBROOK, KEEPER_LAST)}, "keeper"),
        ({"clubs": (PELLBROOK, TWO_KEEPERS)}, "one keeper"),
        ({"starts": [*STARTS[:-1], STARTS[-1] + 0.5]}, "integer"),
        ({"full_time": float(FULL_TIME)}, "integer"),
    ],
)
def test_bad_injections_are_refused(kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        simulate(1, **kwargs)


def test_latest_allowed_start_plays() -> None:
    m = simulate(3, starts=[*STARTS, 91 * MINUTE], full_time=FULL_TIME)
    assert m.events[-1]["type"] == "full_time"
    assert any(e.get("moment_id") == f"m_{len(STARTS) + 1}" for e in m.events)


def test_the_six_clubs_are_well_formed() -> None:
    assert CLUBS[:2] == (HOME, AWAY)
    assert [c["id"] for c in CLUBS[2:]] == [c["id"] for c in (KESTLEBURY, RAVENSHOLME, PELLBROOK, GRINDLESTONE)]
    for key in ("id", "name", "colour"):
        assert len({c[key] for c in CLUBS}) == 6, key
    assert len({c["short_name"].lower() for c in CLUBS}) == 6
    names = [p[0] for c in CLUBS for p in c["players"]]
    assert len(names) == len(set(names)) == 66
    for club in CLUBS:
        assert re.fullmatch(r"[A-Z]{3}", club["short_name"]), club["name"]
        assert re.fullmatch(r"t_[a-z0-9]+", club["id"]), club["name"]
        players = club["players"]
        assert len(players) == 11
        assert players[0][1:3] == (1, "GK"), club["name"]
        assert [p[2] for p in players].count("GK") == 1
        assert players[8][2] == "FWD", club["name"]  # the ninth, p_<side>_9, takes the kickoff
        assert len({p[1] for p in players}) == 11
        assert all(p[2] in {"GK", "DEF", "MID", "FWD"} for p in players)
        digits = [int(d) for d in club["formation"].split("-")]
        assert sum(digits) == 10, club["name"]
        assert digits[0] == [p[2] for p in players].count("DEF"), club["name"]
        assert all(0 < a < 1 and 0 < y < 1 for *_, (a, y) in players)


@pytest.mark.parametrize("home", range(6))
def test_every_name_passes_the_names_checker_in_its_own_match(home: int) -> None:
    """The session allows the names in its match's meta (app/session.py, allowed_names): a fixture's own clubs
    and players pass, and a player from another club is refused."""
    a, b = CLUBS[home], CLUBS[(home + 1) % 6]
    meta = {"home": team("home", a), "away": team("away", b)}
    known = [p["name"] for s in ("home", "away") for p in meta[s]["players"]] + [meta[s]["name"] for s in meta]
    for side in ("home", "away"):
        for p in meta[side]["players"]:
            assert names_problems(f"Great run by {p['name']} for {meta[side]['name']}.", known) == [], p["name"]
    stranger = CLUBS[(home + 2) % 6]["players"][8][0]
    assert names_problems(f"Great run by {stranger}.", known) != []
