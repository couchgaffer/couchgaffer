import json
import re
from collections.abc import Callable
from typing import Any

import pytest

from watchparty.points import RULES, PointsEngine, load_league
from watchparty.sim import DEMO_SEED, simulate
from watchparty.sim.clubs import team

MINUTE = 60_000
META = {"home": team("home"), "away": team("away")}


def league(captain: str = "p_home_1") -> dict[str, Any]:
    """Five managers; mgr_1 owns the home keeper, a defender and the striker, and captains `captain`."""
    squads = [
        ["p_home_1", "p_home_2", "p_home_9", "p_home_8"],
        ["p_away_1", "p_away_2", "p_away_9", "p_away_8"],
        ["p_home_6", "p_home_7", "p_away_6", "p_away_7"],
        ["p_home_10", "p_home_11", "p_away_10", "p_away_11"],
        ["p_home_3", "p_home_4", "p_away_3", "p_away_4"],
    ]
    return {
        "league_id": "lg_test",
        "name": "Test League",
        "scoring_rules_version": "v1",
        "managers": [
            {"id": f"mgr_{n}", "display_name": f"M{n}", "is_bot": n > 1, "style": "casual", "lang": "en",
             "squad": squad, "captain": captain if n == 1 else squad[0], "points": 100 - n}
            for n, squad in enumerate(squads, start=1)
        ],
    }  # fmt: skip


class Events:
    """Builds schema-shaped events with increasing seq and time."""

    def __init__(self) -> None:
        self.seq, self.t = 0, 0

    def __call__(self, kind: str, team: str, player: str | None = None, **extra: Any) -> dict[str, Any]:
        at = extra.pop("at", None)
        self.t = at if at is not None else self.t + 2000
        event = {"seq": self.seq, "t": self.t, "type": kind, "team": team, "x": 0.5, "y": 0.5, **extra}
        if player:
            event["player"] = player
        self.seq += 1
        return event


def shot(
    ev: Events, team: str, player: str, xg: float, outcome: str, keeper: str | None = None, **extra: Any
) -> dict[str, Any]:
    fields = {"opponent": keeper} if keeper else {}
    return ev("shot", team, player, outcome=outcome, shot={"xg": xg, "speed_km_h": 90, "body_part": "right_foot"},
              **fields, **extra)  # fmt: skip


def pass_(
    ev: Events, team: str, player: str, receiver: str | None = None, outcome: str = "success", progressive: bool = False
) -> dict[str, Any]:
    event = ev("pass", team, player, outcome=outcome, end_x=0.8, end_y=0.5)
    if receiver:
        event["receiver"] = receiver
    event["pass"] = {"distance_m": 20, "difficulty": 0.3, "progressive": progressive}
    return event


def play(
    events: list[dict[str, Any]], lg: dict[str, Any] | None = None
) -> tuple[PointsEngine, list[list[dict[str, Any]]]]:
    engine = PointsEngine(META, lg or league())
    stakes = [s for e in events if (s := engine.feed(e))]
    return engine, stakes


def pts(engine: PointsEngine, player: str) -> dict[str, float]:
    return {rule: v for rule, v in engine.player_points[player].items() if v}


def test_rules_are_versioned_labelled_and_our_own() -> None:
    assert RULES["version"] == "v1"
    assert all(rule["label"] for rule in RULES["rules"].values())
    text = json.dumps(RULES).lower()
    for borrowed in ("bonus", "clean sheet", "bps", "bet", "betting", "wager", "prize", "money"):
        assert not re.search(rf"\b{borrowed}\b", text), borrowed


def test_goal_scores_five_plus_two_from_long_odds_plus_one_on_target() -> None:
    ev = Events()
    engine, _ = play([shot(ev, "home", "p_home_9", 0.1, "goal", "p_away_1")])
    assert pts(engine, "p_home_9") == {"goal": 5, "long_odds_goal": 2, "shot_on_target": 1}
    engine, _ = play([shot(Events(), "home", "p_home_9", 0.4, "goal", "p_away_1")])
    assert pts(engine, "p_home_9") == {"goal": 5, "shot_on_target": 1}


def test_conceding_costs_keeper_and_defenders_on_the_pitch_but_not_the_sent_off() -> None:
    ev = Events()
    engine, _ = play([
        ev("card", "home", "p_home_2", outcome="red"),
        shot(ev, "away", "p_away_9", 0.3, "goal", "p_home_1"),
    ])  # fmt: skip
    assert pts(engine, "p_home_1") == {"conceded": -1}
    assert pts(engine, "p_home_2") == {"red_card": -3}
    assert pts(engine, "p_home_3")["conceded"] == -1
    assert "conceded" not in pts(engine, "p_home_6")  # midfielders are not charged


def test_saves_score_by_chance_quality_and_unlock_the_wall_tiers() -> None:
    ev = Events()
    events = [shot(ev, "away", "p_away_9", 0.31, "saved", "p_home_1") for _ in range(5)]
    engine, _ = play(events)
    assert engine.saves["p_home_1"] == 5
    assert pts(engine, "p_home_1") == {"save_points": 5 * (1 + round(0.31 * 4)), "save_tier": 2 + 3}


def test_chance_created_goes_to_the_passer_of_a_good_chance_only() -> None:
    ev = Events()
    good = [pass_(ev, "home", "p_home_8", "p_home_9"), shot(ev, "home", "p_home_9", 0.31, "off_target")]
    engine, _ = play(good)
    assert pts(engine, "p_home_8") == {"chance_created": round(0.31 * 5)}
    ev = Events()
    poor = [good[0] | {"seq": 0}, shot(ev, "home", "p_home_9", 0.1, "off_target") | {"seq": 1, "t": 4000}]
    engine, _ = play(poor)
    assert pts(engine, "p_home_8") == {}


def test_progressive_passes_are_capped_per_half() -> None:
    ev = Events()

    def prog() -> dict[str, Any]:
        return pass_(ev, "home", "p_home_8", "p_home_9", progressive=True)

    first = [prog() for _ in range(5)]
    second = [ev("half_time", "home", at=45 * MINUTE), *(prog() for _ in range(2))]
    engine, _ = play(first + second)
    assert pts(engine, "p_home_8") == {"progressive_pass": 0.5 * 3 + 0.5 * 2}


def test_winning_the_ball_and_winning_it_under_pressure() -> None:
    ev = Events()
    engine, _ = play([
        ev("pressure", "away", "p_away_8"),
        ev("tackle", "away", "p_away_8", outcome="success"),
        ev("interception", "home", "p_home_6", outcome="success", at=ev.t + 10_000),
    ])  # fmt: skip
    assert pts(engine, "p_away_8") == {"press_win": 1, "ball_win": 0.5}
    assert pts(engine, "p_home_6") == {"ball_win": 0.5}


def test_cards() -> None:
    ev = Events()
    engine, _ = play([ev("card", "home", "p_home_6", outcome="yellow"), ev("card", "away", "p_away_6", outcome="red")])
    assert pts(engine, "p_home_6") == {"yellow_card": -1}
    assert pts(engine, "p_away_6") == {"red_card": -3}


def test_each_thirty_minutes_without_conceding_rewards_keeper_and_defenders() -> None:
    ev = Events()
    engine, _ = play([ev("carry", "home", "p_home_8", at=29 * MINUTE), ev("carry", "home", "p_home_8", at=61 * MINUTE)])
    assert pts(engine, "p_home_1") == {"shutout_stretch": 2}
    assert pts(engine, "p_away_2") == {"shutout_stretch": 2}
    ev = Events()
    engine, _ = play([
        shot(ev, "away", "p_away_9", 0.3, "goal", "p_home_1", at=20 * MINUTE),
        ev("carry", "home", "p_home_8", at=45 * MINUTE),  # only 25 minutes since conceding
    ])  # fmt: skip
    assert "shutout_stretch" not in pts(engine, "p_home_1")


def test_captain_doubles_the_managers_points_not_the_players() -> None:
    ev = Events()
    events = [shot(ev, "home", "p_home_9", 0.4, "goal", "p_away_1")]
    plain, _ = play(events, league(captain="p_home_1"))
    doubled, _ = play(events, league(captain="p_home_9"))
    assert doubled.manager_points["mgr_1"] - 99 == 2 * (plain.manager_points["mgr_1"] - 99)
    assert pts(doubled, "p_home_9") == pts(plain, "p_home_9")


def moment(ev: Events, mid: str, *events: dict[str, Any]) -> list[dict[str, Any]]:
    tagged = [e | {"moment_id": mid, "scenario": "keeper_save_run"} for e in events]
    tagged[-1]["climax"] = True
    return tagged


def test_stakes_come_once_per_moment_for_every_manager(contract_errors: Callable[[str, Any], list[str]]) -> None:
    ev = Events()
    events = [
        ev("carry", "home", "p_home_8"),
        *moment(ev, "m_01", shot(ev, "away", "p_away_9", 0.31, "saved", "p_home_1"),
                shot(ev, "away", "p_away_9", 0.22, "saved", "p_home_1")),
        ev("carry", "home", "p_home_8"),
    ]  # fmt: skip
    _, stakes = play(events)
    assert len(stakes) == 1
    assert [s["manager"] for s in stakes[0]] == [f"mgr_{n}" for n in range(1, 6)]
    for stake in stakes[0]:
        assert contract_errors("stake", stake) == []
        assert stake["moment_id"] == "m_01"


def test_a_stake_holds_only_this_moments_points_and_cites_every_number() -> None:
    ev = Events()
    events = [
        *(shot(ev, "away", "p_away_9", 0.3, "saved", "p_home_1") for _ in range(2)),  # before the moment
        *moment(ev, "m_01", shot(ev, "away", "p_away_9", 0.31, "saved", "p_home_1")),
    ]
    _, stakes = play(events, league(captain="p_home_1"))
    stake = next(s for s in stakes[0] if s["manager"] == "mgr_1")
    # This save is 1 + round(0.31 * 4) = 2 and reaches the first Wall tier (+2); the captain doubles it.
    assert stake["delta_points"] == 8
    assert stake["reason_code"] == "save_tier"
    assert stake["milestone"] == {"name": "save_tier", "progress": 3, "target": 5}
    assert stake["above_threshold"] is True
    values = {e["value"] for e in stake["evidence"]}
    assert {8, 0.31, 3, stake["total_points"], stake["rank_before"], stake["rank_after"]} <= values
    ids = [e["id"] for e in stake["evidence"]]
    assert len(ids) == len(set(ids))
    assert all(
        e["source"].get("formula", "").endswith("@v1") for e in stake["evidence"] if e["source"]["kind"] == "computed"
    )


def test_the_clock_passing_30_minutes_during_a_moment_is_not_that_moments_doing() -> None:
    ev = Events()
    events = [
        ev("carry", "home", "p_home_8", at=29 * MINUTE),
        *moment(ev, "m_01", ev("carry", "home", "p_home_8", at=30 * MINUTE + 5_000),  # the clock passes 30:00
                shot(ev, "away", "p_away_9", 0.31, "saved", "p_home_1", at=30 * MINUTE + 9_000)),
    ]  # fmt: skip
    engine, stakes = play(events, league(captain="p_home_1"))
    assert pts(engine, "p_home_1")["shutout_stretch"] == 1  # still scored (#31)
    for stake in stakes[0]:
        assert stake["reason_code"] != "shutout_stretch"
        assert not any("shutout_stretch" in e["id"] for e in stake["evidence"])
    mine = next(s for s in stakes[0] if s["manager"] == "mgr_1")
    assert mine["delta_points"] == 2 * 2  # the save alone (1 + round(0.31 * 4)), doubled for the captain


def test_a_rank_the_clock_changes_during_a_moment_does_not_make_it_that_moments() -> None:
    ev = Events()
    events = [
        ev("carry", "home", "p_home_8", at=29 * MINUTE),
        *moment(ev, "m_01", ev("carry", "home", "p_home_8", at=30 * MINUTE + 5_000),  # the clock passes 30:00
                ev("carry", "home", "p_home_8", at=30 * MINUTE + 9_000)),  # nothing in the moment scores
    ]  # fmt: skip
    engine, stakes = play(events)
    assert engine.ranks()["mgr_5"] < 5  # mgr_5's defenders, on both sides, earned the stretch (#43)
    mgr_5 = next(s for s in stakes[0] if s["manager"] == "mgr_5")
    assert mgr_5["rank_before"] == mgr_5["rank_after"] == engine.ranks()["mgr_5"]
    assert (mgr_5["delta_points"], mgr_5["above_threshold"]) == (0, False)  # not a bidder for this moment


def test_threshold_is_two_points_or_a_rank_change_or_one_step_from_a_tier() -> None:
    ev = Events()
    events = moment(ev, "m_01", pass_(ev, "home", "p_home_6", "p_home_7", progressive=True))
    _, stakes = play(events)
    mgr_3 = next(s for s in stakes[0] if s["manager"] == "mgr_3")
    assert mgr_3["delta_points"] == 1  # 0.5, doubled: the passer is mgr_3's captain
    assert mgr_3["above_threshold"] is False
    nobody = next(s for s in stakes[0] if s["manager"] == "mgr_5")
    assert nobody["delta_points"] == 0
    assert nobody["reason_code"] == "no_change"
    assert nobody["above_threshold"] is False


def test_a_one_point_moment_that_changes_a_rank_bids() -> None:
    lg = league()
    lg["managers"][2]["points"] = lg["managers"][1]["points"]  # mgr_3 level with mgr_2, behind on the tie-break
    ev = Events()
    _, stakes = play(moment(ev, "m_01", pass_(ev, "home", "p_home_6", "p_home_7", progressive=True)), lg)
    mgr_3 = next(s for s in stakes[0] if s["manager"] == "mgr_3")
    assert (mgr_3["delta_points"], mgr_3["rank_before"], mgr_3["rank_after"]) == (1, 3, 2)
    assert mgr_3["above_threshold"] is True


def test_a_one_point_save_one_short_of_a_tier_bids() -> None:
    ev = Events()
    events = [
        shot(ev, "away", "p_away_9", 0.1, "saved", "p_home_1"),  # before the moment
        *moment(ev, "m_01", shot(ev, "away", "p_away_9", 0.1, "saved", "p_home_1")),
    ]
    _, stakes = play(events, league(captain="p_home_9"))
    mgr_1 = next(s for s in stakes[0] if s["manager"] == "mgr_1")
    assert mgr_1["delta_points"] == 1  # 1 + round(0.1 * 4), and the keeper is not the captain
    assert mgr_1["rank_before"] == mgr_1["rank_after"]
    assert mgr_1["milestone"] == {"name": "save_tier", "progress": 2, "target": 3}
    assert mgr_1["above_threshold"] is True


def test_table_ranks_every_manager() -> None:
    engine, _ = play([shot(Events(), "home", "p_home_9", 0.4, "goal", "p_away_1")])
    table = engine.table()
    assert [row["rank"] for row in table] == [1, 2, 3, 4, 5]
    assert table == sorted(table, key=lambda r: -r["points"])
    assert table[0]["manager"] == "mgr_1"


def test_match_stats_are_evidence() -> None:
    ev = Events()
    engine, _ = play([
        pass_(ev, "home", "p_home_8", "p_home_9"),
        pass_(ev, "home", "p_home_9", outcome="fail"),
        shot(ev, "home", "p_home_9", 0.25, "saved", "p_away_1"),
    ])  # fmt: skip
    stats = {e["id"]: e["value"] for e in engine.match_stats()}
    assert stats["ev_stats_home_shots"] == 1
    assert stats["ev_stats_home_xg"] == 0.25
    assert stats["ev_stats_home_pass_completion"] == 0.5
    assert 0 <= stats["ev_stats_home_control"] <= 1


def run(seed: int) -> tuple[PointsEngine, list[list[dict[str, Any]]]]:
    m = simulate(seed)
    engine = PointsEngine(m.meta, load_league())
    return engine, [s for e in m.events if (s := engine.feed(e))]


def test_the_league_fixture_is_valid(contract_errors: Callable[[str, Any], list[str]]) -> None:
    lg = load_league()
    assert contract_errors("league", lg) == []
    for m in lg["managers"]:
        assert m["captain"] in m["squad"]
    assert sum(not m["is_bot"] for m in lg["managers"]) == 1
    assert {m["style"] for m in lg["managers"]} == {"analyst", "casual"}
    assert [m["display_name"] for m in lg["managers"] if m["lang"] == "es"] == ["Leo"]  # the second language (#16)


def test_replaying_the_demo_match_gives_the_same_totals(contract_errors: Callable[[str, Any], list[str]]) -> None:
    first, stakes = run(DEMO_SEED)
    second, _ = run(DEMO_SEED)
    assert first.manager_points == second.manager_points
    for moment_stakes in stakes:
        for stake in moment_stakes:
            assert contract_errors("stake", stake) == []


def test_every_manager_has_at_least_two_moments_that_matter_in_the_demo() -> None:
    _, stakes = run(DEMO_SEED)
    for manager in (m["id"] for m in load_league()["managers"]):
        mattering = [s for moment in stakes for s in moment if s["manager"] == manager and s["above_threshold"]]
        assert len(mattering) >= 2, manager


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_manager_totals_equal_their_players_points(seed: int) -> None:
    engine, _ = run(seed)
    for m in load_league()["managers"]:
        expected = m["points"] + sum(
            sum(engine.player_points[p].values()) * (2 if p == m["captain"] else 1) for p in m["squad"]
        )
        assert engine.manager_points[m["id"]] == pytest.approx(expected)


@pytest.mark.parametrize("break_in_play", ["corner", "free_kick", "foul", "kickoff"])
def test_a_break_in_play_cancels_the_chance_created(break_in_play: str) -> None:
    ev = Events()
    engine, _ = play([
        pass_(ev, "home", "p_home_8", "p_home_9"),
        ev(break_in_play, "home", "p_home_9"),
        shot(ev, "home", "p_home_9", 0.4, "off_target"),
    ])  # fmt: skip
    assert "chance_created" not in pts(engine, "p_home_8")


def test_whole_points_are_whole_numbers_so_nobody_reads_226_point_0() -> None:
    _, stakes = run(DEMO_SEED)
    for stake in (s for moment in stakes for s in moment):
        for value in [stake["delta_points"], stake["total_points"], *(e["value"] for e in stake["evidence"])]:
            assert not (isinstance(value, float) and value.is_integer()), stake["moment_id"]
