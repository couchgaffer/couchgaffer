"""Per-friend facts on each stake (#87): ownership, captain swing, rank change, expected against actual, pass."""

import json
from collections.abc import Callable
from typing import Any

from tests.test_points import Events, league, moment, play, run, shot
from watchparty.agents.checks import number_problems
from watchparty.points import RULES, PointsEngine, load_league
from watchparty.sim import DEMO_SEED

GOAL = RULES["rules"]["goal"]["points"]
NUMBERS = ("owners", "extra_points", "rank_before", "rank_after", "xg", "expected_points", "actual_points",
           "distance_m", "difficulty")  # fmt: skip


def facts(stakes: list[dict[str, Any]], manager: str, kind: str | None = None) -> list[dict[str, Any]]:
    stake = next(s for s in stakes if s["manager"] == manager)
    return [f for f in stake["facts"] if kind is None or f["kind"] == kind]


def kinds(stakes: list[dict[str, Any]], manager: str) -> set[str]:
    return {f["kind"] for f in facts(stakes, manager)}


def owned_also_by(lg: dict[str, Any], player: str, *managers: int) -> dict[str, Any]:
    for n in managers:
        lg["managers"][n - 1]["squad"].append(player)
    return lg


def assist(ev: Events, player: str, receiver: str, distance: float, difficulty: float) -> dict[str, Any]:
    event = ev("pass", "home", player, outcome="success", end_x=0.8, end_y=0.5, receiver=receiver)
    return event | {"pass": {"distance_m": distance, "difficulty": difficulty, "progressive": False}}


def test_1_a_save_by_a_keeper_only_one_manager_owns_is_his_differential() -> None:
    ev = Events()  # p_away_5 is in nobody's squad, so only the keeper's owner has anyone in this moment
    _, stakes = play(moment(ev, "m_01", shot(ev, "away", "p_away_5", 0.31, "saved", "p_home_1")))
    [fact] = facts(stakes[0], "mgr_1", "differential")
    assert (fact["player"], fact["owners"]) == ("p_home_1", 1)
    for other in ("mgr_2", "mgr_3", "mgr_4", "mgr_5"):
        assert {"differential", "template"}.isdisjoint(kinds(stakes[0], other)), other


def test_2_a_goal_by_a_player_three_managers_own_is_a_template_on_each_owners_stake() -> None:
    ev = Events()
    lg = owned_also_by(league(), "p_home_9", 3, 4)
    _, stakes = play(moment(ev, "m_01", shot(ev, "home", "p_home_9", 0.4, "goal", "p_away_1")), lg)
    for owner in ("mgr_1", "mgr_3", "mgr_4"):
        [fact] = facts(stakes[0], owner, "template")
        assert (fact["player"], fact["owners"]) == ("p_home_9", 3), owner
        assert not [f for f in facts(stakes[0], owner, "differential") if f["player"] == "p_home_9"]
    assert "template" not in kinds(stakes[0], "mgr_2")  # the conceding keeper's owner


def test_a_player_two_managers_own_is_neither_differential_nor_template() -> None:
    ev = Events()
    lg = owned_also_by(league(), "p_home_9", 3)
    _, stakes = play(moment(ev, "m_01", shot(ev, "home", "p_home_9", 0.4, "off_target")), lg)
    for owner in ("mgr_1", "mgr_3"):
        assert {"differential", "template"}.isdisjoint(kinds(stakes[0], owner))


def test_3_a_captains_goal_swings_the_extra_points_against_an_owner_without_the_armband() -> None:
    ev = Events()
    lg = owned_also_by(league(captain="p_home_9"), "p_home_9", 3)
    _, stakes = play(moment(ev, "m_01", shot(ev, "home", "p_home_9", 0.4, "goal", "p_away_1")), lg)
    single = GOAL + RULES["rules"]["shot_on_target"]["points"]  # no long-odds goal at xG 0.4
    [fact] = facts(stakes[0], "mgr_1", "captain_swing")
    assert (fact["player"], fact["extra_points"], fact["versus"]) == ("p_home_9", single, ["mgr_3"])
    assert facts(stakes[0], "mgr_3", "captain_swing") == []


def test_a_captain_nobody_else_owns_has_no_swing() -> None:
    ev = Events()
    _, stakes = play(moment(ev, "m_01", shot(ev, "home", "p_home_9", 0.4, "goal", "p_away_1")),
                     league(captain="p_home_9"))  # fmt: skip
    assert facts(stakes[0], "mgr_1", "captain_swing") == []


def test_4_a_moment_that_lifts_a_manager_from_third_to_first_is_a_rank_change() -> None:
    ev = Events()  # p_home_6 is mgr_3's captain: (5 + 1) x 2 takes 97 past 99
    _, stakes = play(moment(ev, "m_01", shot(ev, "home", "p_home_6", 0.4, "goal", "p_away_1")))
    [fact] = facts(stakes[0], "mgr_3", "rank_change")
    assert (fact["rank_before"], fact["rank_after"]) == (3, 1)
    unmoved = [s for s in stakes[0] if s["rank_before"] == s["rank_after"]]
    assert unmoved  # someone kept his place, and has no rank_change
    for stake in stakes[0]:
        moved = stake["rank_before"] != stake["rank_after"]
        assert ("rank_change" in kinds(stakes[0], stake["manager"])) == moved, stake["manager"]


def test_5_expected_against_actual_sums_each_players_own_xg_before_the_captain() -> None:
    ev = Events()
    events = [
        shot(ev, "home", "p_home_9", 0.4, "saved", "p_away_1"),
        shot(ev, "home", "p_home_8", 0.3, "off_target"),  # a teammate's chances, in between
        *moment(ev, "m_01", shot(ev, "home", "p_home_8", 0.25, "off_target"),
                shot(ev, "home", "p_home_9", 0.2, "goal", "p_away_1")),
    ]  # fmt: skip
    _, stakes = play(events, league(captain="p_home_9"))
    by_player = {f["player"]: f for f in facts(stakes[0], "mgr_1", "expected_vs_actual")}
    striker, teammate = by_player["p_home_9"], by_player["p_home_8"]
    assert (striker["xg"], striker["expected_points"], striker["actual_points"]) == (0.6, 0.6 * GOAL, 1 * GOAL)
    assert (teammate["xg"], teammate["expected_points"], teammate["actual_points"]) == (0.55, 0.55 * GOAL, 0)
    for other in ("mgr_2", "mgr_3", "mgr_4", "mgr_5"):  # only managers who own a shooter
        assert "expected_vs_actual" not in kinds(stakes[0], other), other


def test_expected_against_actual_only_comes_with_a_moment_the_player_shot_in() -> None:
    ev = Events()
    events = [shot(ev, "home", "p_home_9", 0.4, "saved", "p_away_1"),
              *moment(ev, "m_01", shot(ev, "away", "p_away_9", 0.3, "saved", "p_home_1"))]  # fmt: skip
    _, stakes = play(events)
    assert "expected_vs_actual" not in kinds(stakes[0], "mgr_1")


def test_6_a_shot_set_up_by_a_long_hard_pass_carries_its_quality() -> None:
    ev = Events()
    events = moment(ev, "m_01", assist(ev, "p_home_8", "p_home_9", 34, 0.72),
                    shot(ev, "home", "p_home_9", 0.3, "saved", "p_away_1"))  # fmt: skip
    _, stakes = play(events)
    for manager in ("mgr_1", "mgr_2"):  # the shooter's owner and the keeper's
        [fact] = facts(stakes[0], manager, "pass_quality")
        assert (fact["player"], fact["distance_m"], fact["difficulty"]) == ("p_home_8", 34, 0.72)
    assert "pass_quality" not in kinds(stakes[0], "mgr_3")  # nobody of his in this moment


def test_7_a_moment_with_no_pass_before_it_has_no_pass_quality() -> None:
    ev = Events()
    unassisted = moment(ev, "m_01", shot(ev, "home", "p_home_9", 0.3, "saved", "p_away_1"))
    to_someone_else = moment(ev, "m_02", assist(ev, "p_home_8", "p_home_10", 34, 0.72),
                             shot(ev, "home", "p_home_9", 0.3, "saved", "p_away_1"))  # fmt: skip
    broken = moment(ev, "m_03", assist(ev, "p_home_8", "p_home_9", 34, 0.72), ev("corner", "home", "p_home_9"),
                    shot(ev, "home", "p_home_9", 0.3, "saved", "p_away_1"))  # fmt: skip
    _, stakes = play(unassisted + to_someone_else + broken)
    assert len(stakes) == 3
    for moment_stakes in stakes:
        assert all(f["kind"] != "pass_quality" for s in moment_stakes for f in s["facts"])


def check_facts(stake: dict[str, Any]) -> None:
    evidence_ids = [e["id"] for e in stake["evidence"]]
    assert len(evidence_ids) == len(set(evidence_ids)), stake["moment_id"]
    ids = [f["id"] for f in stake["facts"]]
    assert len(ids) == len(set(ids)), stake["moment_id"]
    for fact in stake["facts"]:
        assert set(fact["evidence_ids"]) <= set(evidence_ids), fact["id"]
        values = [str(fact[k]) for k in NUMBERS if k in fact]
        english, spanish = " and ".join(values), " y ".join(v.replace(".", ",") for v in values)  # 0,72 in Spanish
        assert number_problems(english, stake["evidence"], fact["evidence_ids"]) == [], fact["id"]
        assert number_problems(spanish, stake["evidence"], fact["evidence_ids"], "es") == [], fact["id"]


def test_8_every_fact_cites_evidence_the_number_checker_accepts() -> None:
    ev = Events()
    lg = owned_also_by(league(captain="p_home_9"), "p_home_9", 3, 4)
    events = [shot(ev, "home", "p_home_9", 0.4, "saved", "p_away_1"),
              *moment(ev, "m_01", assist(ev, "p_home_8", "p_home_9", 34, 0.72),
                      shot(ev, "home", "p_home_9", 0.2, "goal", "p_away_1"))]  # fmt: skip
    _, stakes = play(events, lg)
    seen = {f["kind"] for s in stakes[0] for f in s["facts"]}
    assert seen == {"differential", "template", "captain_swing", "rank_change", "expected_vs_actual", "pass_quality"}
    for stake in stakes[0]:
        check_facts(stake)


def test_9_the_demo_match_gives_valid_facts_on_every_stake(contract_errors: Callable[[str, Any], list[str]]) -> None:
    _, demo = run(DEMO_SEED)
    _, stakes = run(1)  # a second match, for kinds the demo may not reach
    for stake in (s for match in (stakes, demo) for moment_stakes in match for s in moment_stakes):
        assert contract_errors("stake", stake) == []
        check_facts(stake)
    seen = {f["kind"] for moment_stakes in demo for s in moment_stakes for f in s["facts"]}
    assert {"differential", "captain_swing", "rank_change", "expected_vs_actual", "pass_quality"} <= seen, seen


def test_a_stake_does_not_change_after_it_is_emitted() -> None:
    from watchparty.sim import simulate

    match = simulate(DEMO_SEED)
    engine = PointsEngine(match.meta, load_league())
    emitted = [(stakes, json.dumps(stakes)) for e in match.events if (stakes := engine.feed(e))]
    changed = [stakes[0]["moment_id"] for stakes, at_emission in emitted if json.dumps(stakes) != at_emission]
    assert changed == []


def test_a_card_climax_after_an_assisted_shot_has_no_pass_quality() -> None:
    ev = Events()
    events = moment(ev, "m_01", assist(ev, "p_home_8", "p_home_9", 34, 0.72),
                    shot(ev, "home", "p_home_9", 0.3, "saved", "p_away_1"),
                    ev("card", "home", "p_home_9", outcome="yellow"))  # fmt: skip
    _, stakes = play(events)
    assert all(f["kind"] != "pass_quality" for s in stakes[0] for f in s["facts"])


def test_a_place_that_only_the_id_tie_break_changes_is_no_rank_change() -> None:
    lg = league()
    lg["managers"][0]["points"] = 110  # mgr_1 out of reach
    lg["managers"][2]["points"] = lg["managers"][1]["points"]  # mgr_3 level with mgr_2, behind on the tie-break
    ev = Events()  # p_home_6 is mgr_3's captain: a progressive pass, 0.5 doubled, takes him one clear of mgr_2
    _, stakes = play(
        moment(
            ev,
            "m_01",
            ev(
                "pass",
                "home",
                "p_home_6",
                outcome="success",
                receiver="p_home_7",
                **{"pass": {"distance_m": 20, "difficulty": 0.3, "progressive": True}},
            ),
        ),
        lg,
    )
    mgr_3 = next(s for s in stakes[0] if s["manager"] == "mgr_3")
    assert (mgr_3["rank_before"], mgr_3["rank_after"]) == (3, 2)  # joint second before, second after
    assert "rank_change" not in kinds(stakes[0], "mgr_3")
    mgr_2 = next(s for s in stakes[0] if s["manager"] == "mgr_2")
    assert (mgr_2["rank_before"], mgr_2["rank_after"]) == (2, 3)  # joint second before, third after: a real drop
    assert "rank_change" in kinds(stakes[0], "mgr_2")


def test_a_booked_captain_is_no_captain_swing() -> None:
    ev = Events()
    lg = owned_also_by(league(captain="p_home_9"), "p_home_9", 3)
    _, stakes = play(moment(ev, "m_01", ev("card", "home", "p_home_9", outcome="yellow")), lg)
    assert next(s for s in stakes[0] if s["manager"] == "mgr_1")["delta_points"] == -2
    assert facts(stakes[0], "mgr_1", "captain_swing") == []
