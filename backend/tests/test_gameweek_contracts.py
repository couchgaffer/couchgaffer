"""What three points engines on one shared Standings emit keeps to the gameweek contracts (#146)."""

from collections.abc import Callable
from typing import Any

from tests.leagues import club
from tests.test_points import Events, moment, shot
from watchparty.points import PointsEngine, Standings

MINUTE = 60_000
FIXTURES = {"fx_1": ("t_hol", "t_sal"), "fx_2": ("t_cor", "t_dru"), "fx_3": ("t_mar", "t_pel")}
SQUADS = {  # club short names in the ids, as a gameweek sends them; every squad spans two or three fixtures
    "mgr_1": (["p_hol_9", "p_cor_9", "p_mar_9"], "p_cor_9"),
    "mgr_2": (["p_sal_9", "p_dru_9", "p_pel_9"], "p_sal_9"),
    "mgr_3": (["p_hol_10", "p_dru_10", "p_mar_10"], "p_hol_10"),
    "mgr_4": (["p_sal_10", "p_cor_10", "p_pel_10"], "p_pel_10"),
    "mgr_5": (["p_hol_11", "p_cor_11", "p_pel_11"], "p_hol_11"),
}


def league() -> dict[str, Any]:
    return {"league_id": "lg_gw", "name": "Gameweek League", "scoring_rules_version": "v1",
            "managers": [{"id": m, "display_name": m, "is_bot": True, "style": "casual", "lang": "en",
                          "squad": squad, "captain": captain, "points": 100 - n}
                         for n, (m, (squad, captain)) in enumerate(SQUADS.items())]}  # fmt: skip


def gameweek() -> list[tuple[str, dict[str, Any]]]:
    """A goal in each fixture, two minutes apart, in one merged stream with seq increasing across fixtures."""
    ev = Events()
    stream = []
    for n, (fx, (home, away)) in enumerate(FIXTURES.items(), start=1):
        scorer, keeper = f"p_{home[2:]}_9", f"p_{away[2:]}_1"
        goal = shot(ev, "home", scorer, 0.4, "goal", keeper, at=(2 * n - 1) * MINUTE)
        stream += [(fx, e | {"fixture_id": fx}) for e in moment(ev, f"m_0{n}", goal)]
    return stream


def test_every_stake_from_engines_on_one_standings_keeps_to_the_contracts(
    contract_errors: Callable[[str, Any], list[str]],
) -> None:
    lg = league()
    standings = Standings(lg)
    engines = {fx: PointsEngine({"home": club(home), "away": club(away)}, lg, standings=standings, fixture_id=fx)
               for fx, (home, away) in FIXTURES.items()}  # fmt: skip
    stakes = []
    for fx, event in gameweek():
        assert contract_errors("match-event", event) == []
        stakes += [(fx, s) for s in engines[fx].feed(event) or []]

    assert len(stakes) == 3 * 5
    for fx, stake in stakes:
        assert contract_errors("stake", stake) == []
        assert stake["fixture_id"] == fx
    elsewhere = [(fx, s) for fx, s in stakes if any(f["kind"] == "elsewhere" for f in s["facts"])]
    # mgr_1 owns fx_1's scorer, and fx_2's scorer is his captain: by m_02 and m_03 he has points elsewhere
    assert {(fx, s["manager"]) for fx, s in elsewhere} >= {("fx_2", "mgr_1"), ("fx_3", "mgr_1")}
