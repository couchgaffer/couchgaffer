"""A gameweek built from one seed: three fixtures, six clubs, one clock, one merged stream (#148, #94)."""

import copy
import itertools
import json
import re
from collections import Counter
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from watchparty.agents.checks import names_problems
from watchparty.sim import Match, engine, simulate
from watchparty.sim import gameweek as gameweek_module
from watchparty.sim.clubs import CLUBS, GRINDLESTONE, PELLBROOK, team
from watchparty.sim.gameweek import (
    Gameweek,
    Plan,
    build_gameweek,
    built,
    plan_gameweek,
    rekey,
)

MINUTE = 60_000
ID_KEYS = ("player", "receiver", "opponent")
SEEDS = range(400)
LOOP = (0, 7, 211, 368, 399)  # whole gameweeks, frames and all; 368 is the demo gameweek (#155)
LEAGUE = json.loads((Path(engine.__file__).parents[1] / "points" / "league_gw_v1.json").read_text())


@cache
def gameweek(seed: int = 7) -> Gameweek:
    return build_gameweek(seed)


def dump(g: Gameweek) -> str:
    return json.dumps([g.meta, g.events, g.frames, [[m.meta, m.events, m.frames] for m in g.fixtures]])


@pytest.fixture(scope="module")
def many() -> dict[int, Gameweek]:
    """400 gameweeks with no frames: climaxes and whistles do not depend on frames, which are 80% of the cost.
    The patch ends before any test runs, so nothing else is built without frames."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(engine, "_frames", lambda sim, end: [])
        return {seed: build_gameweek(seed) for seed in SEEDS}


def test_three_fixtures_of_six_clubs_and_66_players_none_shared() -> None:
    g = gameweek()
    assert [m.meta["fixture_id"] for m in g.fixtures] == ["fx_1", "fx_2", "fx_3"]
    assert g.meta["fixtures"] == [m.meta for m in g.fixtures]
    clubs = [m.meta[side]["id"] for m in g.fixtures for side in ("home", "away")]
    assert sorted(clubs) == sorted(c["id"] for c in CLUBS)
    rosters = [{p["id"] for side in ("home", "away") for p in m.meta[side]["players"]} for m in g.fixtures]
    assert sum(len(r) for r in rosters) == len(set().union(*rosters)) == 66
    for roster, m in zip(rosters, g.fixtures, strict=True):  # a fixture's events and frames name its own players
        used = {e[k] for e in m.events for k in ("player", "receiver", "opponent") if k in e}
        used |= {p["id"] for f in m.frames for p in f["players"]}
        assert used <= roster


def test_every_club_and_player_name_passes_the_names_checker() -> None:
    teams = [m.meta[side] for m in gameweek().fixtures for side in ("home", "away")]
    known = [t["name"] for t in teams] + [p["name"] for t in teams for p in t["players"]]
    for t in teams:
        for p in t["players"]:
            assert names_problems(f"Great run by {p['name']} for {t['name']}.", known) == [], p["name"]
    assert names_problems("Great run by Bartholomew Quaxley.", known) != []


def test_same_seed_gives_the_same_gameweek_and_seeds_differ() -> None:
    assert dump(build_gameweek(7)) == dump(gameweek(7))
    assert dump(gameweek(8)) != dump(gameweek(7))


def test_every_fixture_kicks_off_at_zero(many: dict[int, Gameweek]) -> None:
    for g in [gameweek(), *many.values()]:
        for m in g.fixtures:
            assert (m.events[0]["t"], m.events[0]["type"]) == (0, "kickoff")
    assert all(m.frames[0]["t"] == 0 for m in gameweek().fixtures)


def test_whistles_land_within_half_a_minute_of_each_other(many: dict[int, Gameweek]) -> None:
    for seed, g in many.items():
        full_time = g.plan.full_time
        for kind in ("half_time", "full_time"):
            whistles = [[e["t"] for e in m.events if e["type"] == kind] for m in g.fixtures]
            assert all(len(w) == 1 for w in whistles), (seed, kind)
            ts = [w[0] for w in whistles]
            assert max(ts) - min(ts) <= 30_000, (seed, kind, ts)
        assert all(full_time <= m.events[-1]["t"] for m in g.fixtures)
        assert [m.events[-1]["type"] for m in g.fixtures] == ["full_time"] * 3


def test_moment_slots_across_400_gameweeks(many: dict[int, Gameweek]) -> None:
    for seed, g in many.items():
        plan = g.plan
        assert plan == plan_gameweek(seed), seed  # none of these seeds needs a second plan
        assert [len(s) for s in plan.starts].count(5) + [len(s) for s in plan.starts].count(6) == 3, seed
        starts = sorted(itertools.chain(*plan.starts))
        assert 15 <= len(starts) <= 18, seed
        assert all(b - a >= 4 * MINUTE for a, b in itertools.pairwise(starts)), seed
        assert all(any(t > 45 * MINUTE for t in s) for s in plan.starts), seed  # or that fixture never blows
        climaxes = sorted(e["t"] for e in g.events if e.get("climax"))
        assert len(climaxes) == len(starts), seed
        assert all(b - a >= 3 * MINUTE for a, b in itertools.pairwise(climaxes)), seed
        assert not any(43 * MINUTE <= t < 46 * MINUTE + 30_000 for t in climaxes), seed
        assert climaxes[-1] <= plan.full_time - 2 * MINUTE, seed
        assert starts[0] >= 3 * MINUTE, seed


@pytest.mark.parametrize("seed", LOOP)
def test_every_moment_is_one_fixtures_and_numbered_in_start_order(seed: int) -> None:
    g = gameweek(seed)
    plan = g.plan
    by_moment: dict[str, list[dict[str, Any]]] = {}
    for e in g.events:
        if "moment_id" in e:
            by_moment.setdefault(e["moment_id"], []).append(e)
    n = sum(len(s) for s in plan.starts)
    assert sorted(by_moment) == [f"m_{k:02d}" for k in range(1, n + 1)]
    assert [ms[0]["t"] for _, ms in sorted(by_moment.items())] == sorted(ms[0]["t"] for ms in by_moment.values())
    for m in g.fixtures:
        ids = [e["moment_id"] for e in m.events if "moment_id" in e]
        assert all(len({e["fixture_id"] for e in by_moment[i]}) == 1 for i in ids)
        runs = [k for k, _ in itertools.groupby(ids)]
        assert len(runs) == len(set(runs)), "a moment's events are contiguous in its fixture"
        assert sorted(runs) == runs


@pytest.mark.parametrize("seed", LOOP)
def test_fixture_id_on_everything_and_seq_runs_over_the_merged_stream(seed: int) -> None:
    g = gameweek(seed)
    for m in g.fixtures:
        assert {e["fixture_id"] for e in m.events} == {f["fixture_id"] for f in m.frames} == {m.meta["fixture_id"]}
    assert [e["seq"] for e in g.events] == list(range(len(g.events)))
    assert len(g.events) == sum(len(m.events) for m in g.fixtures)
    assert len(g.frames) == sum(len(m.frames) for m in g.fixtures)
    for m in g.fixtures:  # the merge keeps each fixture's own order
        assert [e["seq"] for e in m.events] == sorted(e["seq"] for e in m.events)


@pytest.mark.parametrize("seed", LOOP)
def test_merge_is_by_time_then_fixture(seed: int) -> None:
    g = gameweek(seed)
    keys = [(e["t"], e["fixture_id"]) for e in g.events]
    assert keys == sorted(keys)
    frame_keys = [(f["t"], f["fixture_id"]) for f in g.frames]
    assert frame_keys == sorted(frame_keys)
    for m in g.fixtures:  # one frame per fixture per tick, every tick from kickoff to its last event
        ticks = [f["t"] for f in g.frames if f["fixture_id"] == m.meta["fixture_id"]]
        assert ticks == list(range(0, m.events[-1]["t"] + 1, engine.FRAME_MS))


@pytest.mark.parametrize("seed", LOOP)
def test_a_built_gameweek_matches_the_contracts(contract_errors: Callable[[str, Any], list[str]], seed: int) -> None:
    g = gameweek(seed)
    assert contract_errors("gameweek", g.meta) == []
    for meta in g.meta["fixtures"]:  # what the schema cannot say (gameweek.schema.json)
        assert (meta["speed"], meta["playback_delay_ms"]) == (g.meta["speed"], g.meta["playback_delay_ms"])
    assert contract_errors("ws-message", {"v": 1, "type": "gameweek", "data": g.meta}) == []
    for e in g.events:
        assert contract_errors("match-event", e) == [], e
    for f in g.frames:
        assert contract_errors("match-frame", f) == [], f["t"]


@pytest.mark.parametrize("seed", LOOP)
def test_no_simulator_player_id_is_left_anywhere_in_a_gameweek(seed: int) -> None:
    text = dump(gameweek(seed))
    assert "p_home_" not in text
    assert "p_away_" not in text


def test_rekey_names_players_by_club_and_leaves_the_input_alone() -> None:
    starts = [m * MINUTE for m in (5, 20, 50, 70)]
    m = simulate(5, clubs=(PELLBROOK, GRINDLESTONE), starts=starts, full_time=93 * MINUTE)
    before = copy.deepcopy(m)
    r = rekey(m)
    assert (m.meta, m.events, m.frames) == (before.meta, before.events, before.frames)
    assert json.dumps(r.meta).count("p_home_") == json.dumps(r.meta).count("p_away_") == 0
    assert [p["id"] for p in r.meta["home"]["players"]] == [f"p_pel_{n}" for n in range(1, 12)]
    assert [p["id"] for p in r.meta["away"]["players"]] == [f"p_gri_{n}" for n in range(1, 12)]
    swap = {f"p_home_{n}": f"p_pel_{n}" for n in range(1, 12)} | {f"p_away_{n}": f"p_gri_{n}" for n in range(1, 12)}
    for old, new in zip(m.events, r.events, strict=True):
        for key in old.keys() - {"player", "receiver", "opponent"}:
            assert new[key] == old[key]
        for key in ("player", "receiver", "opponent"):
            assert new.get(key) == (swap[old[key]] if key in old else None)
    assert {k for e in m.events for k in ("player", "receiver", "opponent") if k in e} == set(ID_KEYS)  # all three seen
    for old_f, new_f in zip(m.frames, r.frames, strict=True):
        assert [swap[p["id"]] for p in old_f["players"]] == [p["id"] for p in new_f["players"]]
        assert [(p["x"], p["y"]) for p in old_f["players"]] == [(p["x"], p["y"]) for p in new_f["players"]]


def test_rekey_reads_the_short_names_from_the_meta() -> None:
    home, away = team("home"), team("away")
    meta = {"match_id": "match_1", "home": {**home, "short_name": "ABC"}, "away": {**away, "short_name": "XYZ"}}
    event = {"seq": 0, "t": 0, "type": "foul", "team": "away", "player": "p_away_4", "opponent": "p_home_10"}
    r = rekey(Match(meta, [event], [{"t": 0, "players": [{"id": "p_home_1", "x": 0.1, "y": 0.5}]}]))
    assert (r.events[0]["player"], r.events[0]["opponent"]) == ("p_xyz_4", "p_abc_10")
    assert r.frames[0]["players"][0]["id"] == "p_abc_1"
    with pytest.raises(KeyError):
        rekey(Match(meta, [{**event, "player": "p_away_12"}], []))


def test_a_plan_that_does_not_fit_is_replanned(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    real = gameweek_module.fits

    def first_never_fits(plan: Plan, fixtures: list[Match]) -> bool:
        calls.append(1)
        return len(calls) > 1 and real(plan, fixtures)

    monkeypatch.setattr(gameweek_module, "fits", first_never_fits)
    g = build_gameweek(7)
    assert g.plan == plan_gameweek(7, 1) != plan_gameweek(7)
    assert g.meta["fixtures"][0]["seed"] == plan_gameweek(7, 1).seeds[0]
    assert len(calls) == 2


def test_a_seed_with_no_plan_that_fits_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gameweek_module, "fits", lambda plan, fixtures: False)
    with pytest.raises(RuntimeError, match="no plan"):
        build_gameweek(7)


def test_fits_refuses_climaxes_too_close() -> None:
    g = gameweek()
    assert gameweek_module.fits(g.plan, g.fixtures)
    moved = copy.deepcopy(g.fixtures)
    climaxes = sorted((e for m in moved for e in m.events if e.get("climax")), key=lambda e: e["t"])
    climaxes[1]["t"] = climaxes[0]["t"] + 3 * MINUTE - 1
    assert not gameweek_module.fits(g.plan, moved)


def test_the_cache_keeps_a_built_gameweek() -> None:
    built.cache_clear()
    assert built(7) is built(7)
    assert built.cache_info().maxsize == 2


def test_the_gameweek_league_draws_from_the_six_clubs(contract_errors: Callable[[str, Any], list[str]]) -> None:
    assert contract_errors("league", LEAGUE) == []
    players = {f"p_{c['short_name'].lower()}_{n}" for c in CLUBS for n in range(1, len(c["players"]) + 1)}
    club_ids = {c["id"] for c in CLUBS}
    for m in LEAGUE["managers"]:
        assert len(m["squad"]) == 5
        assert set(m["squad"]) <= players
        assert m["captain"] in m["squad"]
        assert m["favourite_club"] in club_ids
        clubs = Counter(re.fullmatch(r"p_([a-z]{3})_\d+", p)[1] for p in m["squad"])  # type: ignore[index]
        assert len(clubs) >= 3, m["id"]  # three clubs cannot all be in one fixture, whatever the pairing
        assert max(clubs.values()) <= 2, m["id"]  # the store's limit once there are six clubs (#96)
    for seed in range(50):
        fixture_of = {p: i for i, (h, a) in enumerate(plan_gameweek(seed).pairs) for c in (h, a)
                      for p in (f"p_{c['short_name'].lower()}_{n}" for n in range(1, 12))}  # fmt: skip
        for m in LEAGUE["managers"]:
            assert len({fixture_of[p] for p in m["squad"]}) >= 2, (seed, m["id"])
