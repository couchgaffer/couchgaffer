"""The host agent (#11): settles the close calls for the one group screen, and says why on it.

Called only when the scorer has no clear winner. It sees each candidate's pitch, impact points and
airtime ledger and the facts code has verified about them, in the scorer's order. Its judgement is which
fact matters most (a bigger swing against fairness), and it says so on screen. Code checks the fact is
true, the reason quotes only the chosen manager's own numbers, and names and tone; one retry, then the
workflow shows the scorer's pick. Model: docs/decisions/0001-models.md.
"""

import json
import re
from typing import Any, Literal

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict

from watchparty.agents.base import ANGLE_KINDS
from watchparty.agents.checks import language_problems, names_problems, number_problems, tone_problems
from watchparty.agents.foundry import Runner, agent_runner, checked

REASON_MAX = 140
_PRONOUNS = re.compile(r"\b(?:he|she|him|her|his|hers|himself|herself)\b", re.IGNORECASE)


def shown(reason: str) -> str:
    """The reason as the group screen shows it: trimmed, and starting with a capital. Checks read this form."""
    reason = reason.strip()
    return reason[:1].upper() + reason[1:]


def factor_line(because: str, candidate: dict[str, Any]) -> str:
    """The reason code writes from the verified fact: every word of it comes from the stake."""
    you = candidate["name"] == "You"
    name, their = ("you", "yours") if you else (candidate["name"], "theirs")
    angle = candidate.get("angle") or {}
    player, moved = angle.get("player", ""), angle.get("moved", "up")
    lines = {
        "differential": f"{name} alone {'own' if you else 'owns'} {player}, so this moment is {their}.",
        "captain_swing": f"{'Your' if you else name + chr(39) + 's'} armband on {player} paid off, so this moment"
                         f" is {their}.",
        "rank_change": f"This moves {name} {moved} the table, so it gets the screen.",
    }  # fmt: skip
    return shown(lines[because])


def agreed_reason(reason: str, because: str, candidate: dict[str, Any]) -> str:
    """The reason the group screen shows. For a fact-kind `because` (rank_change, differential, captain_swing) it
    is always code's line from the verified fact: code can check which fact the host gave, not whether its prose
    tells it truly, and "Priya drops down the table" read as about the table while the fact said up (review of
    #113). For the scorer's reasons (swing, airtime, close calls) the host's own words stand; their numbers are
    checked against the chosen manager's own."""
    return factor_line(because, candidate) if because in ANGLE_KINDS else reason


class HostAnswer(BaseModel):
    """The model's structured output (strict JSON schema)."""

    model_config = ConfigDict(extra="forbid")
    chosen_manager: str
    because: Literal["bigger_swing", "least_airtime", "most_close_calls_lost", "dead_heat", "scorer_pick",
                     "rank_change", "differential", "captain_swing"]  # fmt: skip
    reason: str


def true_factors(chosen: str, candidates: list[dict[str, Any]]) -> set[str]:
    """The facts that hold for `chosen`: the scorer's reasons against every other candidate, and the kind of
    the fact its manager agent argued from when that kind is one the host may give (#88).

    dead_heat: level with the best on all three of swing, airtime and close calls lost, and ahead on none. Early
    in a match everyone is at zero, and without it only the scorer's first pick would have a true fact (#27).
    rank_change, differential and captain_swing: the chosen bid's angle is a fact of that kind, which the points
    engine computed for that manager (#87), so the reason is checked against the stake, not the pitch.
    """
    me = next(c for c in candidates if c["manager"] == chosen)
    others = [c for c in candidates if c is not me]
    facts = {
        "bigger_swing": all(abs(me["impact_points"]) > abs(o["impact_points"]) for o in others),
        "least_airtime": all(me["airtime_share"] < o["airtime_share"] for o in others),
        "most_close_calls_lost": all(me["overruled_count"] > o["overruled_count"] for o in others),
    }
    level = all(abs(me["impact_points"]) >= abs(o["impact_points"]) and me["airtime_share"] <= o["airtime_share"]
                and me["overruled_count"] >= o["overruled_count"] for o in others)  # fmt: skip
    facts |= {"dead_heat": level and not any(facts.values()), "scorer_pick": candidates[0] is me}
    angle = (me.get("angle") or {}).get("kind")
    facts |= {kind: angle == kind for kind in ANGLE_KINDS}
    return {name for name, holds in facts.items() if holds}


INSTRUCTIONS = """You host the one shared screen of a friends' free-to-play, fantasy-style football mini-league
watch party. Several managers want this moment on the shared screen and it is too close for the scorer to call.
Choose one.

Rules:
- Each candidate's true_facts are checked by code: bigger_swing (the largest impact_points of this moment),
  least_airtime (the least of the shared screen so far), most_close_calls_lost (the most close calls lost
  since last on screen), dead_heat (level with the best on all three, ahead on none: too close to split, so it
  is your call), scorer_pick (first in the scorer's order). Candidates are in the scorer's order.
- Each candidate's angle is the fact its own agent argued from. Its kind is also a true fact when it is
  rank_change (the moment moved them in the league table), differential (only they own the player) or
  captain_swing (their captain's armband paid off against friends who own him without it).
- Weigh how much the moment matters against fairness, then choose. Do not favour the viewer.
- chosen_manager: the id of one candidate. because: one of that candidate's true_facts, the one that decided it.
- reason: one lively sentence of at most 140 characters, in English, shown to everyone, that says why in
  words. The only numbers allowed are the chosen manager's own: impact_points, overruled_count, or
  airtime_share as a percentage. Viewers do not know the scorer: never mention it or its order; for
  scorer_pick, say what makes this moment matter.
- Name only people and clubs in NAMES. The manager named "You" is the viewer: address them as "you".
  Refer to managers by name or "they", never "he", "she", "his" or "her".
- Friendly banter only, never mean. Never mention betting, money or prizes. The points are impact points (IP);
  never say "bonus" or "clean sheet".
- Never write an em dash or an en dash: use a comma instead."""


def foundry_runner(client: AsyncOpenAI, model: str) -> Runner[HostAnswer]:
    return agent_runner(client, model, "host", INSTRUCTIONS, HostAnswer, max_tokens=200)


class HostAgent:
    def __init__(self, run: Runner[HostAnswer], model: str) -> None:
        self.run, self.model = run, model

    def problems(self, answer: HostAnswer, moment: dict[str, Any], candidates: list[dict[str, Any]]) -> list[str]:
        problems = []
        chosen = next((c for c in candidates if c["manager"] == answer.chosen_manager), None)
        if chosen is None:
            return ["chosen_manager must be the id of one candidate"]
        if answer.because not in true_factors(answer.chosen_manager, candidates):
            problems.append(f"because '{answer.because}' is not true for {chosen['name']} against every other"
                            " candidate")  # fmt: skip
        reason = shown(answer.reason)  # "vasquith ..." passes the name check only until it is capitalised (#53)
        if not 0 < len(reason) <= REASON_MAX:
            problems.append(f"reason must be 1 to {REASON_MAX} characters")
        units = {"impact_points": "ip", "overruled_count": "count", "airtime_share": "ratio"}
        own = [{"id": k, "value": chosen[k], "unit": unit} for k, unit in units.items()]
        problems += [p.replace("a value of any evidence you cited", "one of the chosen manager's own numbers")
                     for p in number_problems(reason, own, [f["id"] for f in own])]  # fmt: skip
        problems += names_problems(reason, moment["names"])
        problems += language_problems(reason, "en", moment["names"])
        problems += tone_problems(reason)
        problems += [f"'{m}' guesses a gender: use the name or 'they'" for m in _PRONOUNS.findall(reason)]
        return list(dict.fromkeys(problems))

    async def decide(self, moment: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
        request = json.dumps({
            "moment": moment["summary"],
            "candidates": [{"id": c["manager"], "name": c["name"], "pitch": c["pitch"], "impact_points":
                            c["impact_points"], "airtime_share": c["airtime_share"],
                            "overruled_count": c["overruled_count"], "angle": c.get("angle"),
                            "true_facts": sorted(true_factors(c["manager"], candidates))} for c in candidates],
            "NAMES": moment["names"],
        }, ensure_ascii=False)  # fmt: skip
        answer, status = await checked(self.run, request, lambda a: self.problems(a, moment, candidates))
        return {"chosen_manager": answer.chosen_manager, "because": answer.because, "reason": shown(answer.reason),
                "status": status, "model": self.model}  # fmt: skip
