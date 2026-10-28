"""Gameweeks for the league tests: today's single fixture, and a six-club gameweek of three fixtures as #94 plans."""

from collections.abc import Sequence
from typing import Any

from watchparty.sim import Match

POSITIONS = ["GK", "DEF", "DEF", "DEF", "DEF", "MID", "MID", "MID", "FWD", "FWD", "FWD"]


def club(club_id: str) -> dict[str, Any]:
    tag = club_id.removeprefix("t_")
    players = [{"id": f"p_{tag}_{n}", "name": f"{tag} {n}", "shirt": n, "position": pos}
               for n, pos in enumerate(POSITIONS, start=1)]  # fmt: skip
    return {"id": club_id, "name": tag.title(), "short_name": tag[:3].upper(), "colour": "#000000",
            "formation": "4-3-3", "players": players}  # fmt: skip


def goal(t: int, side: str, shooter: str, xg: float) -> dict[str, Any]:
    return {"seq": t, "t": t, "type": "shot", "team": side, "player": shooter, "x": 0.9, "y": 0.5,
            "outcome": "goal", "shot": {"xg": xg}}  # fmt: skip


def six_clubs(seed: int) -> Sequence[Match]:
    """Three fixtures among six invented clubs. In the second fixture the home side's no. 9 scores once."""
    pairs = [("t_ashby", "t_brook"), ("t_corran", "t_dunmere"), ("t_elstow", "t_farley")]
    fixtures = []
    for n, (home, away) in enumerate(pairs, start=1):
        events = [goal(60_000, "home", "p_corran_9", 0.4)] if n == 2 else []
        meta = {"match_id": f"match_{seed}_{n}", "seed": seed, "home": club(home), "away": club(away)}
        fixtures.append(Match(meta, events, []))
    return fixtures
