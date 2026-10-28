"""Squad rules and gameweek scoring (#96), without a database."""

import asyncio
import time
from collections.abc import Sequence

import pytest

from tests.leagues import six_clubs
from watchparty.app.session import simulated
from watchparty.points import RULES, load_league
from watchparty.sim import DEMO_SEED, Match
from watchparty.store.api import Limit
from watchparty.store.db import make_engine
from watchparty.store.rules import Roster, default_squad, score, squad_problems
from watchparty.store.service import LeagueStore

SIX = Roster.of(six_clubs(1))
ONE_FIXTURE = Roster.of([simulated(DEMO_SEED, 30)])
VALID = ["p_ashby_1", "p_ashby_9", "p_brook_5", "p_corran_9", "p_farley_10"]


def test_clubs_come_from_the_fixtures() -> None:
    assert SIX.clubs == {"t_ashby", "t_brook", "t_corran", "t_dunmere", "t_elstow", "t_farley"}
    assert SIX.max_per_club == 2
    assert ONE_FIXTURE.clubs == {"t_hollinsford", "t_saltmere"}
    assert ONE_FIXTURE.max_per_club == 3  # two clubs cannot fill five places at two each


def test_a_valid_squad_passes() -> None:
    assert squad_problems(SIX, VALID, "p_corran_9") == []


@pytest.mark.parametrize(
    ("players", "captain", "problem"),
    [
        (["p_ashby_1", "p_ashby_9", "p_ashby_5", "p_corran_9", "p_farley_10"], "p_corran_9", "at most 2"),
        (VALID, "p_dunmere_9", "captain"),
        (VALID, "", "captain"),
        (VALID[:4], "p_corran_9", "exactly 5"),
        ([*VALID, "p_elstow_2"], "p_corran_9", "exactly 5"),
        (["p_ashby_1", "p_ashby_1", "p_brook_5", "p_corran_9", "p_farley_10"], "p_corran_9", "once"),
        (["p_ashby_1", "p_home_9", "p_brook_5", "p_corran_9", "p_farley_10"], "p_corran_9", "not playing"),
    ],
    ids=["three-from-one-club", "captain-not-in-squad", "no-captain", "four-players", "six-players", "duplicate",
         "unknown-player"],
)  # fmt: skip
def test_a_bad_squad_is_rejected(players: list[str], captain: str, problem: str) -> None:
    problems = squad_problems(SIX, players, captain)
    assert any(problem in p for p in problems), problems


def test_one_fixture_allows_three_but_not_four_from_a_club() -> None:
    three = ["p_home_9", "p_home_8", "p_home_1", "p_away_4", "p_away_10"]
    four = ["p_home_9", "p_home_8", "p_home_1", "p_home_2", "p_away_10"]
    assert squad_problems(ONE_FIXTURE, three, "p_home_9") == []
    assert any("at most 3" in p for p in squad_problems(ONE_FIXTURE, four, "p_home_9"))


@pytest.mark.parametrize("roster", [SIX, ONE_FIXTURE], ids=["six-clubs", "one-fixture"])
def test_default_squads_keep_the_rules(roster: Roster) -> None:
    for manager in load_league()["managers"]:
        players, captain = default_squad(roster, manager["squad"])
        assert squad_problems(roster, players, captain) == []


def test_scoring_counts_the_captain_twice_and_xg_against_goals() -> None:
    fixtures = six_clubs(1)
    squads = {
        "mb_a": (VALID, "p_corran_9"),  # owns the scorer, as captain
        "mb_b": (VALID, "p_ashby_1"),  # owns the scorer
        "mb_c": (["p_ashby_2", "p_brook_2", "p_dunmere_2", "p_elstow_2", "p_farley_2"], "p_ashby_2"),
    }
    by = {s.member: s for s in score(fixtures, squads, ["mb_a", "mb_b", "mb_c"])}
    goal_points = RULES["rules"]["goal"]["points"] + RULES["rules"]["shot_on_target"]["points"]
    assert by["mb_b"].points == goal_points
    assert by["mb_a"].points == goal_points * RULES["captain_multiplier"]
    assert (by["mb_a"].rank, by["mb_b"].rank, by["mb_c"].rank) == (1, 2, 3)
    assert (by["mb_a"].goals, by["mb_a"].expected_goals) == (1, 0.4)
    assert (by["mb_c"].goals, by["mb_c"].expected_goals) == (0, 0)


def test_ties_go_to_the_earlier_seat() -> None:
    nobody = (["p_ashby_2", "p_brook_2", "p_dunmere_2", "p_elstow_2", "p_farley_2"], "p_ashby_2")
    ranked = score(six_clubs(1), {"mb_x": nobody, "mb_y": nobody}, ["mb_y", "mb_x"])
    assert [(s.member, s.rank) for s in ranked] == [("mb_y", 1), ("mb_x", 2)]


def test_default_squad_refuses_a_roster_too_small_for_five() -> None:
    tiny = Roster({"p_a_1": "t_a", "p_a_2": "t_a", "p_b_1": "t_b", "p_b_2": "t_b"})
    with pytest.raises(ValueError, match="cannot fill"):
        default_squad(tiny)


@pytest.mark.anyio
async def test_a_seed_is_simulated_once_and_off_the_event_loop() -> None:
    """Create, join and pick each need the roster; the simulator must not block other requests, nor rerun."""
    calls: list[int] = []

    def slow(seed: int) -> Sequence[Match]:
        calls.append(seed)
        time.sleep(0.3)  # a simulation, standing still
        return six_clubs(seed)

    store = LeagueStore(make_engine("postgresql://nobody@127.0.0.1:1/none"), slow)  # never connects
    done: list[str] = []

    async def ticker() -> None:
        for _ in range(5):
            await asyncio.sleep(0.02)
        done.append("ticker")

    async def build() -> Roster:
        roster = await store.roster(4242)
        done.append("roster")
        return roster

    try:
        first, _ = await asyncio.gather(build(), ticker())
        assert done == ["ticker", "roster"]  # the loop kept running while the roster was built
        assert await store.roster(4242) == first
        assert calls == [4242]
    finally:
        await store.close()


def test_the_limit_counts_each_client_over_a_sliding_minute() -> None:
    now = [0.0]
    limit = Limit(2, clock=lambda: now[0])
    assert [limit.allow("a"), limit.allow("a"), limit.allow("a"), limit.allow("b")] == [True, True, False, True]
    now[0] = 59.0
    assert not limit.allow("a")
    now[0] = 60.5
    assert limit.allow("a")
    now[0] = 200.0
    limit.allow("c")
    assert set(limit.calls) == {"c"}  # quiet clients are forgotten
