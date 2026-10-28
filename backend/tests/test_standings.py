"""One Standings shared by a gameweek's points engines (#150): #94's cases 7 to 11 and 22, on hand-built fixtures."""

from typing import Any

import pytest

from tests.leagues import club
from tests.test_points import Events, moment, pass_, shot
from watchparty.matchtools.view import freeze
from watchparty.points import PointsEngine, Standings
from watchparty.points.standings import number

MINUTE = 60_000
FIXTURES = {"fx_1": ("t_ashby", "t_brook"), "fx_2": ("t_corran", "t_dunmere"), "fx_3": ("t_elstow", "t_farley")}
SQUADS = {  # five managers, each squad spread across fixtures; nobody owns a keeper or a defender
    "mgr_1": (["p_ashby_9", "p_ashby_6", "p_elstow_9"], "p_elstow_9"),
    "mgr_2": (["p_corran_9", "p_elstow_9", "p_brook_9"], "p_corran_9"),
    "mgr_3": (["p_dunmere_9", "p_farley_9", "p_ashby_8"], "p_dunmere_9"),
    "mgr_4": (["p_brook_10", "p_corran_10", "p_farley_10"], "p_brook_10"),
    "mgr_5": (["p_ashby_11", "p_dunmere_11", "p_elstow_11"], "p_ashby_11"),
}
START = {"mgr_1": 100, "mgr_2": 99, "mgr_3": 98, "mgr_4": 97, "mgr_5": 96}
GOAL = 5 + 1  # a goal and its shot on target, from a chance too good for the long-odds award


def league() -> dict[str, Any]:
    return {"league_id": "lg_gw", "name": "Gameweek League", "scoring_rules_version": "v1",
            "managers": [{"id": m, "display_name": m, "is_bot": True, "style": "casual", "lang": "en",
                          "squad": squad, "captain": captain, "points": START[m]}
                         for m, (squad, captain) in SQUADS.items()]}  # fmt: skip


def engines(standings: Standings) -> dict[str, PointsEngine]:
    lg = league()
    return {fx: PointsEngine({"home": club(home), "away": club(away)}, lg, standings=standings, fixture_id=fx)
            for fx, (home, away) in FIXTURES.items()}  # fmt: skip


def merged(fixtures: dict[str, list[dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
    """The gameweek's feed order: (t, fixture index, seq)."""
    order = list(FIXTURES)
    stream = [(fx, e) for fx, events in fixtures.items() for e in events]
    return sorted(stream, key=lambda item: (item[1]["t"], order.index(item[0]), item[1]["seq"]))


def play(
    fixtures: dict[str, list[dict[str, Any]]], engs: dict[str, PointsEngine] | None = None
) -> tuple[dict[str, PointsEngine], dict[str, dict[str, dict[str, Any]]]]:
    """Feed every fixture into its own engine over one Standings. Stakes come back by moment id, then manager."""
    engs = engs or engines(Standings(league()))
    stakes = {}
    for fx, e in merged(fixtures):
        if out := engs[fx].feed(e):
            stakes[e["moment_id"]] = {s["manager"]: s for s in out}
    return engs, stakes


def goal(ev: Events, side: str, scorer: str, keeper: str, at: int) -> dict[str, Any]:
    return shot(ev, side, scorer, 0.4, "goal", keeper, at=at)


def facts(stake: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [f for f in stake["facts"] if f["kind"] == kind]


def test_1_three_engines_on_one_standings_count_the_starting_points_once() -> None:
    standings = Standings(league())
    engs = engines(standings)
    assert {e.standings for e in engs.values()} == {standings}
    assert {row["manager"]: row["points"] for row in engs["fx_2"].table()} == START


def test_2_a_goal_in_fx_2_lands_on_its_owners_stake_and_the_gameweek_table() -> None:
    ev = Events()
    engs, stakes = play({"fx_2": moment(ev, "m_01", goal(ev, "home", "p_corran_9", "p_dunmere_1", MINUTE))})
    mine = stakes["m_01"]["mgr_2"]
    assert mine["fixture_id"] == "fx_2"
    assert (mine["delta_points"], mine["total_points"]) == (2 * GOAL, 99 + 2 * GOAL)  # his captain
    assert engs["fx_1"].table()[0] == {"manager": "mgr_2", "points": 99 + 2 * GOAL, "rank": 1}
    assert dict(engs["fx_1"].standings.by_fixture["fx_2"]) == {"mgr_2": 2 * GOAL}


def gameweek() -> dict[str, list[dict[str, Any]]]:
    """Awards in all three fixtures, captains among them, with one moment in each."""
    fx1, fx2, fx3 = Events(), Events(), Events()
    return {
        "fx_1": [pass_(fx1, "home", "p_ashby_6", "p_ashby_10", progressive=True),
                 *moment(fx1, "m_01", goal(fx1, "home", "p_ashby_9", "p_brook_1", 5 * MINUTE)),
                 shot(fx1, "away", "p_brook_10", 0.3, "saved", "p_ashby_1", at=8 * MINUTE)],
        "fx_2": [*moment(fx2, "m_02", goal(fx2, "home", "p_corran_9", "p_dunmere_1", 6 * MINUTE)),
                 shot(fx2, "away", "p_dunmere_11", 0.1, "goal", "p_corran_1", at=9 * MINUTE)],
        "fx_3": [pass_(fx3, "home", "p_elstow_11", "p_elstow_10", progressive=True),
                 *moment(fx3, "m_03", goal(fx3, "home", "p_elstow_9", "p_farley_1", 7 * MINUTE)),
                 goal(fx3, "away", "p_farley_9", "p_elstow_1", 10 * MINUTE)],
    }  # fmt: skip


def test_3_each_total_is_its_starting_points_plus_every_fixtures_awards_times_the_captain() -> None:
    engs, _ = play(gameweek())
    standings = engs["fx_1"].standings
    for m, (squad, captain) in SQUADS.items():
        mult = {p: 2 if p == captain else 1 for p in squad}
        earned = sum(sum(e.player_points[p].values()) * mult[p] for e in engs.values() for p in squad)
        assert standings.points[m] == pytest.approx(START[m] + earned)
        assert standings.points[m] == pytest.approx(START[m] + sum(by[m] for by in standings.by_fixture.values()))
    assert standings.points["mgr_1"] == pytest.approx(100 + 0.5 + GOAL + 2 * GOAL)  # pass, fx_1 goal, captain's
    assert set(standings.by_fixture) == set(FIXTURES)


def test_4_an_award_in_fx_3_during_a_moment_in_fx_1_is_not_that_moments_doing() -> None:
    fx1, fx3 = Events(), Events()
    at = 10 * MINUTE
    _, stakes = play({
        # mgr_3's midfielder in fx_1 makes a progressive pass, and at the same t his forward scores in fx_3
        "fx_1": moment(fx1, "m_01", pass_(fx1, "home", "p_ashby_8", "p_ashby_9", progressive=True) | {"t": at},
                       fx1("carry", "home", "p_ashby_9", at=at + 4_000)),
        "fx_3": [goal(fx3, "away", "p_farley_9", "p_elstow_1", at)],
    })  # fmt: skip
    mgr_3 = stakes["m_01"]["mgr_3"]
    assert (mgr_3["delta_points"], mgr_3["total_points"]) == (0.5, 98 + 0.5 + GOAL)  # each award counted once
    assert mgr_3["rank_before"] == mgr_3["rank_after"] == 1  # the fx_3 goal counts before and after
    assert facts(mgr_3, "rank_change") == []
    assert mgr_3["above_threshold"] is False


def test_5_a_captain_in_fx_3_doubles_only_fx_3_awards_and_swings_only_there() -> None:
    _, stakes = play(gameweek())
    fx_1, fx_3 = stakes["m_01"]["mgr_1"], stakes["m_03"]["mgr_1"]
    assert fx_1["delta_points"] == GOAL  # p_ashby_9 is not his captain
    assert facts(fx_1, "captain_swing") == []
    assert fx_3["delta_points"] == 2 * GOAL
    swing = facts(fx_3, "captain_swing")
    assert [(f["player"], f["extra_points"], f["versus"]) for f in swing] == [("p_elstow_9", GOAL, ["mgr_2"])]
    assert facts(stakes["m_03"]["mgr_2"], "captain_swing") == []  # owns him without the armband


def test_6_level_totals_across_fixtures_rank_by_manager_id_on_every_replay() -> None:
    def run() -> list[dict[str, Any]]:
        fx2, fx3 = Events(), Events()
        engs, _ = play({  # mgr_5 catches mgr_4 on 97 with half a point in each of two fixtures
            "fx_2": [pass_(fx2, "away", "p_dunmere_11", "p_dunmere_9", progressive=True)],
            "fx_3": [pass_(fx3, "home", "p_elstow_11", "p_elstow_9", progressive=True)],
        })  # fmt: skip
        return engs["fx_1"].table()

    table = run()
    assert [(r["manager"], r["points"], r["rank"]) for r in table[3:]] == [("mgr_4", 97, 4), ("mgr_5", 97, 5)]
    assert run() == table


def test_7_elsewhere_is_a_managers_points_from_the_other_fixtures_so_far() -> None:
    _, stakes = play(gameweek())
    mine = stakes["m_03"]["mgr_1"]  # fx_3 at 7:00; fx_1 gave him a pass and a goal by then
    [fact] = facts(mine, "elsewhere")
    assert fact["points"] == number(0.5 + GOAL)
    [item] = [e for e in mine["evidence"] if e["id"] in fact["evidence_ids"]]
    assert item["value"] == fact["points"]
    assert item["source"] == {"kind": "computed", "formula": "elsewhere@v1"}
    mgr_4 = stakes["m_03"]["mgr_4"]  # his captain's shot on target in fx_1 comes at 8:00, after this moment
    assert facts(mgr_4, "elsewhere") == []


def test_8_no_elsewhere_for_a_manager_with_nothing_in_the_other_fixtures() -> None:
    _, stakes = play(gameweek())
    assert facts(stakes["m_02"]["mgr_4"], "elsewhere") == []
    assert facts(stakes["m_01"]["mgr_3"], "elsewhere") == []


def test_14_points_lost_in_another_fixture_are_elsewhere_too() -> None:
    fx1, fx2 = Events(), Events()
    _, stakes = play({
        "fx_1": [fx1("card", "away", "p_brook_9", outcome="yellow", at=MINUTE)],  # mgr_2's, no armband: -1
        "fx_2": moment(fx2, "m_01", goal(fx2, "home", "p_corran_9", "p_dunmere_1", 2 * MINUTE)),
    })  # fmt: skip
    [fact] = facts(stakes["m_01"]["mgr_2"], "elsewhere")
    assert fact["points"] == -1


def test_22_a_frozen_gameweek_keeps_one_standings_and_its_totals_while_play_goes_on() -> None:
    fixtures = gameweek()
    live = engines(Standings(league()))
    first = {fx: [e for e in events if e["t"] < 7 * MINUTE] for fx, events in fixtures.items()}
    play(first, live)
    frozen = freeze(live)
    before = dict(frozen["fx_1"].standings.points)
    play({fx: [e for e in events if e["t"] >= 7 * MINUTE] for fx, events in fixtures.items()}, live)

    copies = {id(e.standings) for e in frozen.values()}
    assert len(copies) == 1
    assert id(live["fx_1"].standings) not in copies
    standings = frozen["fx_2"].standings
    assert standings.points == before != live["fx_1"].standings.points
    for m in START:
        assert standings.points[m] == pytest.approx(START[m] + sum(by[m] for by in standings.by_fixture.values()))


def test_engines_without_a_shared_standings_keep_their_own_tables() -> None:
    ev = Events()
    lg = league()
    meta = {"home": club("t_corran"), "away": club("t_dunmere")}
    scoring, idle = PointsEngine(meta, lg), PointsEngine(meta, lg)
    scoring.feed(goal(ev, "home", "p_corran_9", "p_dunmere_1", MINUTE))
    assert scoring.manager_points["mgr_2"] == 99 + 2 * GOAL
    assert idle.manager_points == START
    assert scoring.standings is not idle.standings


def test_a_shared_standings_needs_the_engines_fixture_id() -> None:
    # Without one, every engine files under one key and `elsewhere` is always 0 (#150 review, #151)
    lg = league()
    with pytest.raises(ValueError, match="fixture_id"):
        PointsEngine({"home": club("t_ashby"), "away": club("t_brook")}, lg, standings=Standings(lg))
