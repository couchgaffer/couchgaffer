"""The league table (#150): starting totals counted once, and every fixture's awards added into one total.

A single match has one points engine and its own Standings. In a watch party's gameweek (#94) the three fixtures'
engines share one, so the starting points are not counted three times and the ranks are the gameweek's. The
season scorer in store/rules.py is separate: it sums per-fixture engines of its own.
"""

from collections import defaultdict
from typing import Any


def number(value: float) -> float | int:
    """Two decimal places at most, and whole numbers as integers: nobody should read "226.0 IP"."""
    rounded = round(value, 2)
    return int(rounded) if float(rounded).is_integer() else rounded


class Standings:
    def __init__(self, league: dict[str, Any]) -> None:
        self.start = {m["id"]: float(m["points"]) for m in league["managers"]}  # before kickoff
        self.points = dict(self.start)  # running totals
        # each manager's points from each fixture so far; a single match files under None
        self.by_fixture: defaultdict[str | None, defaultdict[str, float]] = defaultdict(lambda: defaultdict(float))

    def add(self, fixture_id: str | None, manager: str, points: float) -> None:
        self.points[manager] += points
        self.by_fixture[fixture_id][manager] += points

    def elsewhere(self, fixture_id: str | None, manager: str) -> float:
        """The manager's points so far from every fixture but this one."""
        return sum(by.get(manager, 0.0) for fixture, by in self.by_fixture.items() if fixture != fixture_id)

    @staticmethod
    def standing(points: dict[str, float], manager: str) -> int:
        """Place with ties shared: 1 + managers strictly ahead. ranks() breaks ties by id, which is not a move."""
        mine = round(points[manager], 6)
        return 1 + sum(round(p, 6) > mine for p in points.values())

    def ranks(self, points: dict[str, float] | None = None) -> dict[str, int]:
        points = self.points if points is None else points
        order = sorted(points, key=lambda m: (-round(points[m], 6), m))
        return {m: n for n, m in enumerate(order, start=1)}

    def table(self) -> list[dict[str, Any]]:
        ranks = self.ranks()
        rows = [{"manager": m, "points": number(p), "rank": ranks[m]} for m, p in self.points.items()]
        return sorted(rows, key=lambda r: r["rank"])
