"""What the workflow needs from the agents (#10, #11), and the plain-stat fallback that always works."""

import re
from typing import Any, Protocol

from watchparty.agents.checks import DECIMAL_COMMA
from watchparty.matchtools.view import Given
from watchparty.points import RULES


class ManagerAgent(Protocol):
    async def bid(self, stake: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """Return urgency, explanation, explanation_en, pitch_to_host, evidence_ids and an optional angle (one of
        the stake's fact ids) for one manager's stake; or status "declined" when the stake is worth 0 IP (#88).

        context: moment, manager, names, squad, captain, players (id to name), managers (id to display name) and
        ledger (airtime_share, close_calls_lost).
        """
        ...


class HostAgent(Protocol):
    async def decide(self, moment: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
        """Return chosen_manager (one of the candidates), because (a fact true of it) and a reason of at most 140
        characters.

        moment: moment_id, summary and the names an agent may use. candidates, in the scorer's order: manager,
        name, pitch, impact_points, airtime_share, overruled_count and angle (the bid's fact, or None).
        """
        ...


class SummaryAgent(Protocol):
    async def summarise(self, manager: dict[str, Any], phase: str, tools: Any, served: dict[str, dict[str, Any]],
                        names: list[str], rulebook: Given | None = None,
                        gameweek: bool = False) -> dict[str, Any]:  # fmt: skip
        """Return text, text_en, evidence_ids and rulebook_ids for one manager's half-time or full-time summary.
        gameweek: the summary covers a gameweek's three fixtures (#152), and is checked for it.

        tools: the match tools over MCP, on the view frozen at that whistle. served: every evidence item those
        tools have returned so far, which is all a number may come from. names: the only names allowed.
        rulebook: the rulebook entries (Foundry IQ, #18) the tools have returned, which is all it may cite.
        """
        ...


AGENT_FIELDS = ("urgency", "explanation", "explanation_en", "pitch_to_host", "evidence_ids")


def evidence(stake: dict[str, Any], suffix: str) -> dict[str, Any]:
    return next(e for e in stake["evidence"] if e["id"].endswith(suffix))


# Which rule fired, by reason code, for the plain bid. Not the rule labels: those carry numbers.
RULE_NAMES = {
    "en": {code: code.replace("_", " ") for code in (*RULES["rules"], "no_change")},
    "es": {
        "goal": "gol", "long_odds_goal": "gol improbable", "shot_on_target": "tiro a puerta",
        "chance_created": "ocasión creada", "progressive_pass": "pase progresivo", "ball_win": "balón recuperado",
        "press_win": "presión ganada", "save_points": "parada", "save_tier": "nivel Wall",
        "shutout_stretch": "racha sin encajar", "conceded": "gol encajado", "yellow_card": "tarjeta amarilla",
        "red_card": "tarjeta roja", "no_change": "sin cambios",
    },
}  # fmt: skip
# The plain bid, per language and style: the Spanish and its English copy come from the same facts (#36).
FALLBACK_BID = {
    "en": {"analyst": "{gained} from this moment ({rule}). Your league total is {total}.",
           "casual": "{gained} for you from that moment."},
    "es": {"analyst": "{gained} por esta jugada ({rule}). Tu total en la liga es {total}.",
           "casual": "{gained} para ti por esa jugada."},
}  # fmt: skip


def written(value: float, lang: str, sign: bool = False) -> str:
    """A number as the language writes it: 213,5 in Spanish."""
    text = f"{value:+g}" if sign else f"{value:g}"
    return text.replace(".", ",") if lang in DECIMAL_COMMA else text


def room_line(stake: dict[str, Any], manager: dict[str, Any]) -> str:
    """The plain pitch, which the group screen shows if it wins (#138): English, about the manager in the third
    person, one fact. The judge's own manager is "You", the viewer at the screen."""
    name = "you" if manager["display_name"] == "You" else manager["display_name"]
    gained = f"{evidence(stake, '_delta')['value']:+g} IP"
    return f"{gained} for {name} from this moment ({RULE_NAMES['en'][stake['reason_code']]})."


def pitch_source(bid: dict[str, Any]) -> str:
    """Who wrote a bid's pitch, the group screen's line (#138): code on a plain bid, or when code's line replaced
    a pitch that failed its checks; otherwise the agent."""
    return str(bid.get("pitch_source") or ("code" if bid.get("status") == "fallback" else "manager"))


def fallback_bid(stake: dict[str, Any], manager: dict[str, Any]) -> dict[str, Any]:
    """A plain statement built by code from the stake's own evidence, so it always passes the checks."""
    delta, total = evidence(stake, "_delta"), evidence(stake, "_total")

    def say(lang: str) -> str:
        return FALLBACK_BID[lang][manager["style"]].format(
            gained=f"{written(delta['value'], lang, sign=True)} IP", rule=RULE_NAMES[lang][stake["reason_code"]],
            total=written(total["value"], lang))  # fmt: skip

    lang = manager["lang"]
    return {
        "moment_id": stake["moment_id"], "manager": manager["id"], "style": manager["style"], "lang": lang,
        "explanation_en": "" if lang == "en" else say("en"),
        "urgency": 0.5, "explanation": say(lang),  # neutral: a failed model call neither wins nor loses the screen
        "pitch_to_host": room_line(stake, manager),
        "evidence_ids": [delta["id"], total["id"]],
        "status": "fallback",
    }  # fmt: skip


def declined_bid(stake: dict[str, Any], manager: dict[str, Any]) -> dict[str, Any]:
    """A manager passing on a moment worth 0 IP to them (#88): no phone message, never scored, never shown."""
    return {"moment_id": stake["moment_id"], "manager": manager["id"], "style": manager["style"],
            "lang": manager["lang"], "status": "declined"}  # fmt: skip


def plain_bid(stake: dict[str, Any], manager: dict[str, Any]) -> dict[str, Any]:
    """What code says when the agent cannot: the plain stat, or nothing at all for a moment worth 0 IP, where the
    plain stat would be "+0 IP for you" (#88). The league table on the group screen still shows the move."""
    return fallback_bid(stake, manager) if stake["delta_points"] else declined_bid(stake, manager)


# "0 IP", "+0 points", "no points", "0 puntos": a moment worth nothing, said on a phone (#88 row 9).
_ZERO_POINTS = re.compile(
    r"(?<![\d.,])[+-]?0(?:[.,]0+)?\s*(?:IP|impact points?|points?|puntos?)\b|\bno (?:impact )?points\b"
    r"|\bno (?:points? )?swing\b|\bsin puntos\b|\bningún punto\b|\bni un punto\b|\bno (?:gana|suma)s?\b[^.]*\bpuntos\b"
    r"|\b(?:zero|cero) (?:impact )?(?:points?|puntos?|IP)\b",
    re.IGNORECASE,
)


def says_zero_points(text: str) -> bool:
    return bool(_ZERO_POINTS.search(text))


ANGLE_KINDS = ("rank_change", "differential", "captain_swing")  # facts the host may give as its reason (#88)


def fact_for_agent(
    fact: dict[str, Any], players: dict[str, str], managers: dict[str, str], *, casual: bool = False
) -> dict[str, Any]:
    """A fact as an agent reads it: names for ids. A casual request gets no numbers (see the manager's request);
    an analyst's keeps them with the evidence ids that carry them, so a line written from it passes the checker."""
    out: dict[str, Any] = {"id": fact["id"], "kind": fact["kind"]}
    if "player" in fact:
        out["player"] = players.get(fact["player"], fact["player"])
    if "versus" in fact:
        out["versus"] = [managers.get(m, m) for m in fact["versus"]]
    if fact["kind"] == "rank_change":
        out["moved"] = "up" if fact["rank_after"] < fact["rank_before"] else "down"
    if casual:
        return out
    numbers = {k: v for k, v in fact.items() if k not in ("id", "kind", "player", "versus", "evidence_ids")}
    return out | numbers | {"evidence_ids": fact["evidence_ids"]}
