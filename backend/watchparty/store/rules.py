"""Squad rules and gameweek scoring, with no database: the clubs and players come from the gameweek's matches.

A gameweek is a list of fixtures (today one, #94 makes it three). Every rule here reads the club set from those
fixtures' metadata, so six clubs across three fixtures need no change in this file.
"""

import math
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from watchparty.points import PointsEngine
from watchparty.sim import Match

SQUAD_SIZE = 5
SEATS = 5
MAX_PER_CLUB = 2

Fixtures = Callable[[int], Sequence[Match]]  # a gameweek's seed -> its fixtures


@dataclass(frozen=True)
class Roster:
    """Every player in a gameweek, and the club each plays for."""

    club_of: dict[str, str]  # player id -> club id

    @classmethod
    def of(cls, fixtures: Sequence[Match]) -> "Roster":
        return cls({p["id"]: m.meta[side]["id"] for m in fixtures for side in ("home", "away")
                    for p in m.meta[side]["players"]})  # fmt: skip

    @property
    def clubs(self) -> set[str]:
        return set(self.club_of.values())

    @property
    def max_per_club(self) -> int:
        """Two from one club, as the league plays with six clubs (#94). With fewer clubs two is not enough to
        fill five places (one fixture has two clubs, so at most four), so the cap rises just enough to fill
        a squad: three for today's single fixture."""
        return max(MAX_PER_CLUB, math.ceil(SQUAD_SIZE / max(len(self.clubs), 1)))


def squad_problems(roster: Roster, players: Sequence[str], captain: str) -> list[str]:
    """Why a squad cannot be picked; empty when it can."""
    problems = []
    if len(players) != SQUAD_SIZE:
        problems.append(f"a squad has exactly {SQUAD_SIZE} players")
    if len(set(players)) != len(players):
        problems.append("a player can be picked once")
    unknown = [p for p in players if p not in roster.club_of]
    if unknown:
        problems.append(f"not playing this gameweek: {', '.join(sorted(set(unknown)))}")
    per_club: defaultdict[str, int] = defaultdict(int)
    for p in set(players) - set(unknown):
        per_club[roster.club_of[p]] += 1
    crowded = sorted(club for club, n in per_club.items() if n > roster.max_per_club)
    if crowded:
        problems.append(f"at most {roster.max_per_club} players from one club: {', '.join(crowded)}")
    if captain not in players:
        problems.append("the captain must be one of the squad")
    return problems


def default_squad(roster: Roster, preferred: Sequence[str] = ()) -> tuple[list[str], str]:
    """A valid squad for a bot or a member who did not pick: the preferred players that fit, then the rest of the
    roster in order. The first player is captain."""
    squad: list[str] = []
    per_club: defaultdict[str, int] = defaultdict(int)
    for p in [*preferred, *roster.club_of]:
        club = roster.club_of.get(p)
        if club is None or p in squad or per_club[club] >= roster.max_per_club:
            continue
        squad.append(p)
        per_club[club] += 1
        if len(squad) == SQUAD_SIZE:
            break
    if len(squad) != SQUAD_SIZE:
        raise ValueError(f"the roster cannot fill a squad of {SQUAD_SIZE}")
    return squad, squad[0]


# Names the agents read as the viewer: on the shared screen and in a recap they would mean the person watching.
VIEWER_WORDS = {"you", "your", "i"}


def shown_names(names: dict[int, str], viewer: int) -> dict[int, str]:
    """Each seat's name in a session, from the names friends typed (#178). The viewer's seat plays as "You" (the
    session sets it), so another seat called "you", or one whose name an earlier seat already has, is shown as
    "Friend A", "Friend B" and on, a label no seat typed: the host, the room lines and the recaps must never take one
    manager for another. Letters, not seat numbers: a number in a name fails the agents' number check."""
    typed = {name.casefold() for name in names.values()}
    labels = (f"Friend {letter}" for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if f"friend {letter.lower()}" not in typed)
    shown: dict[int, str] = {}
    taken = set(VIEWER_WORDS)
    for seat in sorted(names):
        name = names[seat]
        if seat != viewer and name.casefold() in taken:
            name = next(labels)
        shown[seat] = name
        if seat != viewer:
            taken.add(name.casefold())
    return shown


@dataclass(frozen=True)
class Score:
    member: str
    points: float
    rank: int
    expected_goals: float  # xG of the squad's shots
    goals: int  # goals the squad scored


def score(fixtures: Sequence[Match], squads: dict[str, tuple[list[str], str]], order: Sequence[str]) -> list[Score]:
    """Every member's points over the gameweek's fixtures, by the same engine the live match uses. `order`
    breaks ties (seat order)."""
    managers = [{"id": m, "squad": s, "captain": c, "points": 0} for m, (s, c) in squads.items()]
    owners: defaultdict[str, list[str]] = defaultdict(list)
    for m, (players, _) in squads.items():
        for p in players:
            owners[p].append(m)
    points: defaultdict[str, float] = defaultdict(float)
    xg: defaultdict[str, float] = defaultdict(float)
    goals: defaultdict[str, int] = defaultdict(int)
    for match in fixtures:
        engine = PointsEngine(match.meta, {"managers": managers})
        for event in match.events:
            engine.feed(event)
            if event["type"] != "shot":
                continue
            for m in owners.get(event["player"], []):
                xg[m] += float(event["shot"]["xg"])
                goals[m] += event.get("outcome") == "goal"
        for m, earned in engine.manager_points.items():
            points[m] += earned
    ranked = sorted(squads, key=lambda m: (-round(points[m], 6), order.index(m)))
    return [Score(m, round(points[m], 2), n, round(xg[m], 2), goals[m]) for n, m in enumerate(ranked, start=1)]
