"""The read-only match tools (#12). Every number they return is an evidence item, as the contracts define.

Plain functions of a MatchView, so the MCP server, the code fallbacks and the tests share one source.
"""

import asyncio
from collections import Counter
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer

from watchparty.matchtools.rulebook import Rulebook
from watchparty.matchtools.view import LOOKUPS_PER_SUMMARY, RULEBOOK_LOOKUPS, MatchView
from watchparty.points import PointsEngine
from watchparty.points.engine import RULES, minute
from watchparty.points.standings import number

VERSION = RULES["version"]


class Compact(BaseModel):
    """Leaves out empty fields, as the contracts expect, instead of sending nulls."""

    model_config = ConfigDict(extra="forbid")

    @model_serializer(mode="wrap")
    def _compact(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return {k: v for k, v in handler(self).items() if v is not None}


class Source(Compact):
    kind: Literal["event", "computed"]
    event_seq: list[int] | None = None
    formula: str | None = None


class Evidence(Compact):
    """One number an agent may use (contracts: common.schema.json#/$defs/evidence). Whole numbers stay whole."""

    id: Annotated[str, Field(pattern=r"^ev_[a-z0-9_]+$")]
    label: Annotated[str, Field(max_length=80)]
    value: int | float
    unit: Literal["ip", "xg", "km_h", "m", "count", "ratio", "minute"] | None = None
    source: Source


class Ref(BaseModel):
    id: str
    name: str


class Result(BaseModel):
    as_of_minute: int
    evidence: list[Evidence]


class Standings(Result):
    """The mini-league table: each manager's league points and rank."""

    managers: list[Ref]


class SquadPoints(Result):
    """Impact points (IP) per player and per rule this match, with the captain doubled."""

    manager: Ref
    captain: Ref
    players: list[Ref]


class MatchStats(Result):
    """Shots, xG, possession, pass completion and control for both clubs over a window of the match."""

    from_minute: int
    to_minute: int
    clubs: list[Ref]


class KeyMoment(Compact):
    moment_id: str
    rule: str  # the rule that earned or cost the most IP in it
    players: list[Ref]
    fixture_id: str | None = None  # in a gameweek, the fixture it happened in (#152)


class KeyMoments(Result):
    """The key moments that mattered to one manager so far, with that manager's stake in each."""

    manager: Ref
    moments: list[KeyMoment]


class PlayerSummary(Result):
    """One player's events, metrics and IP so far."""

    player: Ref
    club: Ref
    position: str


class ScoringRules(Result):
    """Impact points rules v1."""

    version: str
    name: str
    rules: list[Ref]


def _item(eid: str, label: str, value: float, unit: str | None, formula: str) -> dict[str, Any]:
    item = {"id": eid, "label": label, "value": number(value), "source": {"kind": "computed",
            "formula": f"{formula}@{VERSION}"}}  # fmt: skip
    return item | ({"unit": unit} if unit else {})


def _served(view: MatchView, evidence: list[dict[str, Any]], manager: str | None = None) -> list[Evidence]:
    view.serve(evidence, manager)
    return [Evidence.model_validate(e) for e in evidence]


def _manager(view: MatchView, manager: str) -> dict[str, Any]:
    if manager not in view.managers:
        raise ValueError(f"unknown manager {manager!r}: use mgr_1 to mgr_5")
    return view.managers[manager]


def _player(view: MatchView, player: str) -> dict[str, Any]:
    if player not in view.players:
        example = next(iter(view.players))
        raise ValueError(f"unknown player {player!r}: use an id such as {example} from get_squad_points")
    return view.players[player]


def _ref(view: MatchView, player: str) -> Ref:
    return Ref(id=player, name=view.players[player]["name"])


def standings(view: MatchView) -> Standings:
    evidence = []
    for row in view.engine.table():
        name = view.managers[row["manager"]]["display_name"]
        evidence += [
            _item(f"ev_table_{row['manager']}_points", f"{name}: league points", row["points"], "ip", "league_total"),
            _item(f"ev_table_{row['manager']}_rank", f"{name}: league rank", row["rank"], "count", "rank"),
        ]
    managers = [Ref(id=m["id"], name=m["display_name"]) for m in view.league["managers"]]
    return Standings(as_of_minute=minute(view.t), managers=managers, evidence=_served(view, evidence))


def squad_points(view: MatchView, manager: str) -> SquadPoints:
    m = _manager(view, manager)
    evidence, gained = [], 0.0
    for p in m["squad"]:
        mult = RULES["captain_multiplier"] if p == m["captain"] else 1
        name, captain = view.players[p]["name"], " (captain x2)" if mult > 1 else ""
        points = view.points_of(p)
        for rule, value in points.items():
            label = f"{name}, {rule.replace('_', ' ')}{captain}"
            evidence.append(_item(f"ev_squad_{manager}_{p}_{rule}", label, value * mult, "ip", rule))
        total = sum(points.values()) * mult
        evidence.append(_item(f"ev_squad_{manager}_{p}_total", f"{name}: IP for {m['display_name']}{captain}", total,
                              "ip", "squad_points"))  # fmt: skip
        gained += total
    span = "this gameweek" if view.gameweek else "this match"
    evidence.append(_item(f"ev_squad_{manager}_total", f"{m['display_name']}: IP gained {span}", gained, "ip",
                          "squad_points"))  # fmt: skip
    return SquadPoints(as_of_minute=minute(view.t), manager=Ref(id=manager, name=m["display_name"]),
                       captain=_ref(view, m["captain"]), players=[_ref(view, p) for p in m["squad"]],
                       evidence=_served(view, evidence, manager))  # fmt: skip


def _fixture_stats(view: MatchView, meta: dict[str, Any], from_minute: int, to: int) -> list[dict[str, Any]]:
    """One fixture's stats over the window. In a gameweek the ids carry the fixture, so three never collide."""
    fixture = meta.get("fixture_id")
    window = [e for e in view.past() if from_minute * 60_000 <= e["t"] < to * 60_000 and e.get("fixture_id") == fixture]
    replay = PointsEngine(meta, view.league)
    replay.t = window[0]["t"] if window else 0
    for e in window:
        replay.observe(e)
    prefix = f"ev_stats_{fixture}_{from_minute}_{to}_" if fixture else f"ev_stats_{from_minute}_{to}_"
    evidence = []
    for e in replay.match_stats():
        side = "home" if e["id"].startswith("ev_stats_home_") else "away"  # the engine's own stat ids name the side
        evidence.append(e | {"id": e["id"].replace("ev_stats_", prefix, 1),
                             "label": e["label"].removesuffix(f", {side}") + f", {meta[side]['name']}"})  # fmt: skip
    return evidence


def match_stats(
    view: MatchView, from_minute: int = 0, to_minute: int | None = None, fixture_id: str | None = None
) -> MatchStats:
    """Minutes elapsed: 0 to 45 is the first half. The window never reaches past the current minute. In a
    gameweek, one fixture's stats, or every fixture's when fixture_id is left out."""
    now = minute(view.t)
    to = now if to_minute is None else min(to_minute, now)
    metas = view.fixture_metas(fixture_id)
    if not 0 <= from_minute < to:
        raise ValueError(f"from_minute must be at least 0 and before to_minute (now minute {now})")
    evidence = [e for meta in metas for e in _fixture_stats(view, meta, from_minute, to)]
    return MatchStats(as_of_minute=now, from_minute=from_minute, to_minute=to,
                      clubs=[Ref(id=meta[s]["id"], name=meta[s]["name"]) for meta in metas for s in ("home", "away")],
                      evidence=_served(view, evidence))  # fmt: skip


def key_moments(view: MatchView, manager: str, fixture_id: str | None = None) -> KeyMoments:
    """In a gameweek, one fixture's moments, or every fixture's when fixture_id is left out."""
    m = _manager(view, manager)
    view.fixture_metas(fixture_id)  # an unknown fixture is an error, not an empty list
    mattered = [s for moment in view.stakes for s in moment if s["manager"] == manager and s["above_threshold"]
                and fixture_id in (None, s.get("fixture_id"))]  # fmt: skip
    moments = [KeyMoment(moment_id=s["moment_id"], rule=s["reason_code"], players=[_ref(view, p) for p in s["players"]],
                         fixture_id=s.get("fixture_id")) for s in mattered]  # fmt: skip
    evidence = [e for s in mattered for e in s["evidence"]]
    return KeyMoments(as_of_minute=minute(view.t), manager=Ref(id=manager, name=m["display_name"]), moments=moments,
                      evidence=_served(view, evidence, manager))  # fmt: skip


def player_summary(view: MatchView, player: str) -> PlayerSummary:
    p = _player(view, player)
    counts: Counter[str] = Counter()
    xg = 0.0
    for e in view.past():
        if e.get("player") == player:
            kind, outcome = e["type"], e.get("outcome")
            counts["passes"] += kind == "pass"
            counts["passes_completed"] += kind == "pass" and outcome == "success"
            counts["shots"] += kind == "shot"
            counts["shots_on_target"] += kind == "shot" and outcome in ("saved", "goal")
            counts["goals"] += kind == "shot" and outcome == "goal"
            counts["ball_wins"] += kind in ("tackle", "interception") and outcome == "success"
            counts["yellow_cards"] += kind == "card" and outcome == "yellow"
            counts["red_cards"] += kind == "card" and outcome == "red"
            xg += e["shot"]["xg"] if kind == "shot" else 0
        if p["position"] == "GK" and e["type"] == "shot" and e.get("opponent") == player:
            counts["saves"] += e.get("outcome") == "saved"
    keys = ["passes", "passes_completed", "shots", "shots_on_target", "goals", "ball_wins", "yellow_cards",
            "red_cards"] + (["saves"] if p["position"] == "GK" else [])  # fmt: skip
    evidence = [_item(f"ev_player_{player}_{k}", f"{p['name']}: {k.replace('_', ' ')}", counts[k], "count",
                      "player_events") for k in keys]  # fmt: skip
    evidence.append(_item(f"ev_player_{player}_xg", f"{p['name']}: total xG", round(xg, 2), "xg", "xg_sum"))
    points = view.points_of(player)
    span = "gameweek" if view.gameweek else "match"
    evidence += [_item(f"ev_player_{player}_{rule}", f"{p['name']}: IP from {rule.replace('_', ' ')}", value, "ip",
                       rule) for rule, value in points.items()]  # fmt: skip
    evidence.append(_item(f"ev_player_{player}_total", f"{p['name']}: IP this {span}", sum(points.values()), "ip",
                          "player_points"))  # fmt: skip
    return PlayerSummary(as_of_minute=minute(view.t), player=_ref(view, player),
                         club=Ref(id=p["club"]["id"], name=p["club"]["name"]), position=p["position"],
                         evidence=_served(view, evidence))  # fmt: skip


_RULE_NUMBERS = {"points": "ip", "per_xg": "ip", "min_xg": "xg", "max_xg": "xg", "max_per_half": "count"}


def scoring_rules(view: MatchView) -> ScoringRules:
    evidence = [_item("ev_rule_captain_multiplier", "Captain multiplier", RULES["captain_multiplier"], None, "rules"),
                _item("ev_rule_threshold_points", "IP swing that makes a moment matter", RULES["threshold_points"],
                      "ip", "rules")]  # fmt: skip
    for rule, spec in RULES["rules"].items():
        name = rule.replace("_", " ")
        for key, unit in _RULE_NUMBERS.items():
            if key in spec:
                evidence.append(_item(f"ev_rule_{rule}_{key}", f"{name}: {key.replace('_', ' ')}", spec[key], unit,
                                      "rules"))  # fmt: skip
        if "every_ms" in spec:
            evidence.append(_item(f"ev_rule_{rule}_minutes", f"{name}: minutes", spec["every_ms"] / 60_000, "minute",
                                  "rules"))  # fmt: skip
        if "window_ms" in spec:
            evidence.append(_item(f"ev_rule_{rule}_seconds", f"{name}: seconds", spec["window_ms"] / 1000, None,
                                  "rules"))  # fmt: skip
        for n, (saves, points) in enumerate(spec.get("tiers", []), start=1):
            evidence += [_item(f"ev_rule_{rule}_{n}_saves", f"{name} {n}: saves", saves, "count", "rules"),
                         _item(f"ev_rule_{rule}_{n}_points", f"{name} {n}: IP", points, "ip", "rules")]  # fmt: skip
    return ScoringRules(as_of_minute=minute(view.t), version=VERSION, name=RULES["name"],
                        rules=[Ref(id=r, name=spec["label"]) for r, spec in RULES["rules"].items()],
                        evidence=_served(view, evidence))  # fmt: skip


class RulebookEntry(BaseModel):
    id: str
    title: str
    reason: str  # why the rule exists, in words: the numbers come from get_scoring_rules, as evidence


class RuleExplanation(BaseModel):
    """Entries of the league rulebook that answer a question, retrieved from Foundry IQ."""

    question: str
    entries: list[RulebookEntry]


def _reason(content: str) -> str:
    """The rule's "Why:" sentence; a glossary entry, which has none, as it is."""
    why = content.partition("Why: ")[2]
    return why[:1].upper() + why[1:] if why else content


async def rule_explanation(view: MatchView, rulebook: Rulebook, question: str, caller: str = "") -> RuleExplanation:
    """Asks Foundry IQ once per question per match, within the match's budget, and records what each
    manager's agent was given, so its summary can cite only those entries (#34, #37)."""
    key = " ".join(question.lower().split())
    if key not in view.lookups.answers:
        if view.asked[caller] >= LOOKUPS_PER_SUMMARY or view.lookups.used >= RULEBOOK_LOOKUPS:
            raise ValueError("your rulebook lookups are used up: explain from the other tools, and leave"
                             " rulebook_ids to entries get_rule_explanation already gave you, or empty")  # fmt: skip
        view.asked[caller] += 1
        view.lookups.used += 1
        view.lookups.answers[key] = asyncio.ensure_future(rulebook.explain(question))  # before any await
    # Shielded: a summary that hits its deadline does not cancel the lookup another one is waiting for.
    answer = await asyncio.shield(view.lookups.answers[key])
    entries = [RulebookEntry(id=e["id"], title=e["title"], reason=_reason(e["content"])) for e in answer]
    view.serve_rulebook(caller, [e.model_dump() for e in entries])
    return RuleExplanation(question=question, entries=entries)
