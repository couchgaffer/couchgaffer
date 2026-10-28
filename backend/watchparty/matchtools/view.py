"""What the match tools read: one session's match as it stands now, or frozen at half time or full time.

A summary is written from a frozen view, so its numbers cannot drift while the second half plays on, and
the view records every evidence item it served to each manager's agent: the checker accepts a number only if
the tools gave it to that agent (#54).
"""

import asyncio
import copy
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any

from watchparty.points import PointsEngine

MAX_SERVED = 2000  # per agent: every tool, every manager and every player is about 600 items; the rest is abuse
# Foundry IQ lookups: each summary's agent may make two of its own, so the early summaries cannot use up the
# last ones' (25 Oct: models ask about their own manager, so few questions repeat). Ten summaries a match.
LOOKUPS_PER_SUMMARY = 2
RULEBOOK_LOOKUPS = 10 * LOOKUPS_PER_SUMMARY  # the match's ceiling, live view included (#34)
Given = dict[str, dict[str, str]]  # the rulebook entries one manager's agent was given: id -> title and reason
Served = dict[str, dict[str, Any]]  # the evidence one manager's agent was given: id -> item


def freeze[T](engines: T) -> T:
    """A copy of one engine, or of a gameweek's engines, that play does not change. One deepcopy for all of
    them, so engines that share a Standings still share one copy: three calls would split it in three (#150)."""
    return copy.deepcopy(engines)


@dataclass
class Lookups:
    """One match's rulebook lookups, shared by all its views: a budget, and answers by question. An answer is
    stored as its lookup starts, so a second summary asking the same question meanwhile waits for it (#42)."""

    used: int = 0
    answers: dict[str, asyncio.Future[list[dict[str, str]]]] = field(default_factory=dict)


@dataclass
class MatchView:
    meta: dict[str, Any]
    league: dict[str, Any]  # as the session set it up; `points` are the totals before kickoff
    engine: PointsEngine
    events: list[dict[str, Any]]  # the whole match: tools read only events up to t
    stakes: list[list[dict[str, Any]]]  # every key moment's stakes so far
    t: int
    recording: bool = False  # frozen views record what they serve; the live view does not
    caller: str = ""  # the manager whose agent is asking: see for_caller
    served: dict[str, Served] = field(default_factory=dict)  # caller -> what it was given
    lookups: Lookups = field(default_factory=Lookups)
    # rulebook entries given to each manager's agent (#18, #37): caller -> id -> title and reason
    rulebook: dict[str, Given] = field(default_factory=dict)
    asked: Counter[str] = field(default_factory=Counter)  # lookups each caller made from this view
    # A gameweek's fixtures' metas (#94): the players are all of theirs. Empty for a single match: the meta's.
    fixtures: list[dict[str, Any]] = field(default_factory=list)
    # A gameweek's engines by fixture id, sharing one Standings, which `engine` (one of them) reads (#152)
    engines: dict[str, PointsEngine] = field(default_factory=dict)
    players: dict[str, dict[str, Any]] = field(init=False)
    managers: dict[str, dict[str, Any]] = field(init=False)

    def __post_init__(self) -> None:
        # A player's club and side come from the meta: in a gameweek the id names the club (p_hol_9), not the side
        self.players = {p["id"]: p | {"side": side, "club": meta[side], "fixture_id": meta.get("fixture_id")}
                        for meta in self.fixtures or [self.meta] for side in ("home", "away")
                        for p in meta[side]["players"]}  # fmt: skip
        self.managers = {m["id"]: m for m in self.league["managers"]}

    def frozen(self, t: int) -> "MatchView":
        """A copy that stops at t and records what it serves."""
        engine, engines = freeze((self.engine, self.engines))  # one copy, so the engines keep one Standings
        view = MatchView(self.meta, self.league, engine, self.events, list(self.stakes), t, True,
                         fixtures=self.fixtures, engines=engines)  # fmt: skip
        view.lookups = self.lookups  # one budget per match
        return view

    @property
    def gameweek(self) -> bool:
        return bool(self.fixtures)

    def points_of(self, player: str) -> dict[str, float]:
        """One player's IP by rule, from their own fixture's engine."""
        fixture = self.players[player]["fixture_id"]
        return (self.engines[fixture] if self.engines else self.engine).player_points[player]

    def fixture_metas(self, fixture_id: str | None) -> list[dict[str, Any]]:
        """The fixtures a tool reads: the one named, or every one. A single match is its own, and names none."""
        metas = self.fixtures or [self.meta]
        if fixture_id is None:
            return metas
        named = [m for m in metas if m.get("fixture_id") == fixture_id]
        if not named:
            there = ", ".join(m["fixture_id"] for m in self.fixtures) or "none here: leave fixture_id out"
            raise ValueError(f"unknown fixture {fixture_id!r}: the fixtures are {there}")
        return named

    def for_caller(self, caller: str) -> "MatchView":
        """The same view, with what it serves recorded for `caller`, one manager's agent."""
        return replace(self, caller=caller)

    def served_to(self, caller: str) -> Served:
        return self.served.setdefault(caller, {})

    def past(self) -> list[dict[str, Any]]:
        """Events up to t. In a gameweek, each fixture's only as far as its engine got: a fixture that stopped
        early has later events in the stream that were never played (#152)."""
        if not self.engines:
            return [e for e in self.events if e["t"] <= self.t]
        return [e for e in self.events if e["t"] <= min(self.t, self.engines[e["fixture_id"]].t)]

    def serve_rulebook(self, caller: str, entries: list[dict[str, str]]) -> None:
        if self.recording:
            mine = self.rulebook.setdefault(caller, {})
            mine.update({e["id"]: {"title": e["title"], "reason": e["reason"]} for e in entries})

    def serve(self, evidence: list[dict[str, Any]], manager: str | None = None) -> None:
        """Record what the caller's agent may cite. Evidence about one manager (`manager`: their squad and key
        moments) is citable only by that manager's agent; any agent may look, and the standings stay open."""
        if not self.recording or manager not in (None, self.caller):
            return
        mine = self.served_to(self.caller)
        for e in evidence:
            if e["id"] in mine or len(mine) < MAX_SERVED:
                mine[e["id"]] = e
