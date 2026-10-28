"""The manager agent (#10): one parameterised Agent Framework agent, run for each manager with a stake.

It writes the manager's explanation in their style and language, argues for the group screen, and cites
evidence. It argues from one angle, a fact the points engine computed for its own manager (#88), and may
decline a moment that does not change its manager's points. Code checks every answer (angle, numbers, names,
tone); one retry with the problems fed back, then the workflow's plain-stat fallback. Model choice and
settings: docs/decisions/0001-models.md.
"""

import json
import re
from typing import Any

from agent_framework import Message
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict

from watchparty.agents.base import declined_bid, evidence, fact_for_agent, room_line, says_zero_points
from watchparty.agents.checks import (
    DECIMAL_STYLE,
    GENDER_NEUTRAL,
    LANGUAGES,
    language_problems,
    names_problems,
    number_problems,
    room_problems,
    style_problems,
    tone_problems,
)
from watchparty.agents.foundry import FailedChecks, Runner, agent_runner, checked, english_first
from watchparty.agents.scorer import FAIR_SHARE


class Answer(BaseModel):
    """The model's structured output (strict JSON schema)."""

    model_config = ConfigDict(extra="forbid")
    declined: bool  # first: a moment worth nothing to the manager needs no text at all (#88)
    angle: str  # then the fact to argue from, before the text written around it
    urgency: float
    explanation_en: str  # first: for another language the model writes English, then translates it
    explanation: str
    pitch_to_host: str
    evidence_ids: list[str]


INSTRUCTIONS = """You are a manager's personal companion in a free-to-play, fantasy-style football mini-league.
Friends watch one invented match together. Explain what this moment means for this manager's own squad.

Rules:
- FACTS say what this moment means for this manager beyond the points: who else owns the player, the captain's
  armband, a change of league position. Choose the one that matters most to this manager as angle (its id), and
  write the explanation and pitch_to_host around it. When FACTS is empty, angle is "".
- declined: true only when POINTS is 0, the moment does not change this manager's points, and nothing in FACTS
  is worth their attention. Then the phone stays quiet, and every other field is "", 0 or []. Otherwise false.
- Never tell a manager that a moment gave them 0 or no points. When POINTS is 0, do not mention points or a
  swing at all: say what the moment does mean, from FACTS.
- Write a league position as a plain number, as EVIDENCE gives it: "up to 2 in the league", never "2nd".
- LEAGUE is this manager's league position and how much of the shared screen they have had. Use it to judge
  urgency (a manager rarely on the screen argues harder). Never quote any of its numbers; a league position may
  be quoted only from EVIDENCE.
- Use only numbers from EVIDENCE, written as given. Put the id of every evidence item you use in evidence_ids,
  and always cite at least one item. evidence_ids holds EVIDENCE ids (ev_...), never a FACTS id (f_...).
- Name only the players, clubs and managers in NAMES. Never mention or compare with any real player, club,
  manager, competition or broadcaster. The manager named "You" is the viewer: write "you", not "You",
  in the middle of a sentence.
- analyst style: at most two sentences, with the key numbers.
- casual style: exactly one short, warm sentence of at most 15 words, with at most one number.
- explanation is in the manager's language. When that is not en, write explanation_en first, in English, and
  then explanation as its translation; when it is en, explanation_en is "".
- pitch_to_host: one line of at most 140 characters, in English, arguing why the shared screen should show this
  moment for this manager. If it wins, the whole room reads it on the shared screen: write it about the manager
  by name, in the third person, as a statement, not an order such as "Show this". Never "you" or "your" (only
  the manager named You, the viewer, is "you": "you climb", "your captain", never "You has" or "You's"), never
  "I", "my" or "we". One fact, the angle, with at most two numbers: never put a captain's extra points and
  expected against actual in one line.
- urgency: from 0 to 1, how much this moment matters to this manager.
- Friendly banter only, never mean. This league is free to play: never mention betting, money or prizes.
- The points are impact points (IP). Never say "bonus" or "clean sheet": those are another game's words.
- Write each number as EVIDENCE gives it: 226, not 226.0, and 3.5 stays 3.5.
- Never write an em dash or an en dash: use a comma instead."""

# "xG 0.31", "31 m", "34 km/h": the analysis a casual request leaves out.
_NUMBERS_IN_SUMMARY = re.compile(r",?(?:\s+(?:at|of))?\s*(?:xG\s*)?\b\d+(?:\.\d+)?\s*(?:km/h|m|%)?(?=[\s.,!?]|$)")

STYLE_DIRECTIVES = {
    "analyst": "ANALYST style: at most two sentences, with the key numbers from EVIDENCE.",
    "casual": ("CASUAL style: exactly one sentence of at most 15 words, with at most one number."
               " For example: 'Big save from your captain, +2 IP!'"),
}  # fmt: skip
# By the sign of POINTS: a red card that cost a manager 3 IP once went on screen as "a swing that can protect the
# lead" (#138). The room's line is checked for it.
PITCH_DIRECTION = {
    -1: " This moment COST the manager points: pitch_to_host says it is a loss for them (it costs, a blow, drops).",
    1: " This moment GAINED the manager points: pitch_to_host never calls it a loss, a blow or a setback.",
}


def foundry_runner(client: AsyncOpenAI, model: str) -> Runner[Answer]:
    return agent_runner(client, model, "manager", INSTRUCTIONS, Answer, max_tokens=600)


def angle_problems(angle: str, stake: dict[str, Any]) -> list[str]:
    """The angle is one of this manager's own facts, or empty when the stake has none (#88)."""
    ids = [f["id"] for f in stake.get("facts") or []]
    if not ids and angle:
        return ['angle must be "": there are no FACTS for this moment']
    if ids and angle not in ids:
        return [f"angle '{angle}' is not one of this manager's FACTS: choose one of {', '.join(ids)}"]
    return []


def shown_evidence(stake: dict[str, Any], style: str) -> list[dict[str, Any]]:
    """The evidence the request carries. A casual fan gets the swing, not the analysis: a model cannot overuse
    numbers it never sees. A moment worth 0 IP has no swing to quote, and "0 IP" would be the line (#88), so the
    request leaves the delta out and a casual fan gets the league position instead."""
    evidence: list[dict[str, Any]] = stake["evidence"]
    zero = not stake["delta_points"]
    if zero:
        evidence = [e for e in evidence if not e["id"].endswith("_delta")]
    if style != "casual":
        return evidence
    # One number at most for a casual fan: the position now, or the swing (the run of 26 Oct quoted "3 to 4")
    keep = "_rank_after" if zero else "_delta"
    return [e for e in evidence if e["id"].endswith(keep)]


def league_for(stake: dict[str, Any], ledger: dict[str, Any], casual: bool) -> dict[str, Any]:
    """The manager's standing for the request (#88). An analyst gets the numbers; a casual request gets the same
    in words, since every number it sees is one it may quote, and a casual line allows only one."""
    before, after = stake.get("rank_before"), stake.get("rank_after")
    share, lost = ledger.get("airtime_share", 0.0), ledger.get("close_calls_lost", 0)
    if not casual:
        return {"rank_before": before, "rank_now": after, "share_of_shared_screen": share,
                "close_calls_lost_since_last_on_screen": lost}  # fmt: skip
    moved = "none" if before == after or before is None or after is None else "up" if after < before else "down"
    return {"league_move": moved, "rarely_on_shared_screen": share < FAIR_SHARE, "lost_recent_close_calls": lost > 0}


def with_fact_evidence(ids: list[str], stake: dict[str, Any]) -> list[str]:
    """evidence_ids with any of this stake's own fact ids replaced by the evidence that fact cites (#88). Models
    cite the fact they argue from as if it were evidence; the fact's evidence is what they mean, and it is in the
    stake. Another manager's fact id, or a made-up one, is left as is and fails as unknown evidence."""
    facts = {f["id"]: f["evidence_ids"] for f in stake.get("facts") or []}
    return list(dict.fromkeys(e for i in ids for e in facts.get(i, [i])))


def fact_ids_mapped(run: Runner[Answer], stake: dict[str, Any]) -> Runner[Answer]:
    async def mapped(messages: list[Message]) -> tuple[Answer | None, int, int]:
        answer, tokens_in, tokens_out = await run(messages)
        if answer is not None:
            answer = answer.model_copy(update={"evidence_ids": with_fact_evidence(answer.evidence_ids, stake)})
        return answer, tokens_in, tokens_out

    return mapped


class ManagerAgent:
    def __init__(self, run: Runner[Answer], model: str) -> None:
        self.run, self.model = run, model

    def request(self, stake: dict[str, Any], context: dict[str, Any]) -> str:
        """The facts as JSON, then the style and language directive last, where models weigh it most."""
        manager = context["manager"]
        casual = manager["style"] == "casual"
        milestone, moment = stake.get("milestone"), context["moment"]
        if casual:
            one_step = milestone and milestone["target"] - milestone["progress"] == 1
            milestone = "one more save reaches the Wall tier" if one_step else None
            moment = _NUMBERS_IN_SUMMARY.sub("", context["moment"]).strip(" ,")
        players, managers = context.get("players", {}), context.get("managers", {})
        facts = [fact_for_agent(f, players, managers, casual=casual) for f in stake.get("facts") or []]
        league = league_for(stake, context.get("ledger", {}), casual)
        fields = {
            "manager": manager["display_name"], "style": manager["style"], "language": manager["lang"],
            "squad": context["squad"], "captain": context["captain"], "moment": moment,
            "rule_fired": stake["reason_code"], "milestone": milestone, "POINTS": stake["delta_points"],
            "FACTS": facts, "LEAGUE": league,
            "EVIDENCE": [{k: e[k] for k in ("id", "label", "value")} for e in shown_evidence(stake, manager["style"])],
            "NAMES": context["names"],
        }  # fmt: skip
        if context.get("gameweek"):  # to know where the moment is; its numbers are not evidence, so never cited (#94)
            fields["GAMEWEEK"] = context["gameweek"]
        request = json.dumps(fields, ensure_ascii=False)
        directive = STYLE_DIRECTIVES[manager["style"]] + PITCH_DIRECTION.get(
            (stake["delta_points"] > 0) - (stake["delta_points"] < 0), ""
        )  # the room's line says which way it went (#138)
        if manager["lang"] != "en":
            language = LANGUAGES.get(manager["lang"], manager["lang"])
            directive += (f" First write explanation_en in English; it is never empty. Then write explanation as"
                          f" its natural {language} ('{manager['lang']}') translation, as a {language}-speaking fan"
                          " would say it, with the same facts and numbers; keep every name as in NAMES.")  # fmt: skip
            directive += GENDER_NEUTRAL.get(manager["lang"], "") + DECIMAL_STYLE.get(manager["lang"], "")
        return f"{request}\n\n{directive}"

    def problems(self, answer: Answer, stake: dict[str, Any], context: dict[str, Any]) -> list[str]:
        delta = stake["delta_points"]
        if answer.declined and delta:
            return [f"declined is only for a moment worth 0 IP to this manager, and this one is worth {delta:+g} IP:"
                    " set declined to false and write the explanation"]  # fmt: skip
        if answer.declined:
            return []
        return list(dict.fromkeys(angle_problems(answer.angle, stake) + self.text_problems(answer, stake, context)))

    def text_problems(self, answer: Answer, stake: dict[str, Any], context: dict[str, Any]) -> list[str]:
        """The checks on what a phone, the host and the room read: evidence, numbers, names, tone, language, style."""
        delta = stake["delta_points"]
        problems = []
        if not answer.evidence_ids:
            problems.append("cite at least one evidence id")
        if not 0 < len(answer.explanation) <= 280:
            problems.append("explanation must be 1 to 280 characters")
        if not 0 < len(answer.pitch_to_host) <= 140:
            problems.append("pitch_to_host must be 1 to 140 characters")
        lang, style = context["manager"]["lang"], context["manager"]["style"]
        problems += style_problems(answer.explanation, style, lang=lang)
        problems += room_problems(answer.pitch_to_host, context["manager"]["display_name"], delta)  # the room's line
        english = lang == "en"
        if not english and not 0 < len(answer.explanation_en) <= 280:
            problems.append("explanation_en must be the English version, 1 to 280 characters")
        if not english:  # the copy reads in the same style (#36)
            problems += [f"explanation_en: {p}" for p in style_problems(answer.explanation_en, style)]
        texts = [(answer.explanation, lang), (answer.pitch_to_host, "en")]
        texts += [] if english else [(answer.explanation_en, "en")]
        for text, text_lang in texts:
            problems += number_problems(text, stake["evidence"], answer.evidence_ids, text_lang)
            problems += names_problems(text, context["names"])
            problems += tone_problems(text)
            problems += language_problems(text, text_lang, context["names"])
        if not delta and any(says_zero_points(text) for text, _ in texts):
            problems.append("never say the moment gave 0 or no points: say what it does mean, or decline")
        return list(dict.fromkeys(problems))

    async def bid(self, stake: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        request = self.request(stake, context)
        english = context["manager"]["lang"] == "en"
        run = fact_ids_mapped(self.run, stake)
        run = english_first(run, "explanation", "explanation_en") if english else run
        last: list[Answer] = []

        def problems(answer: Answer) -> list[str]:
            last[:] = [answer]
            return self.problems(answer, stake, context)

        pitch_source = "manager"
        try:
            answer, status = await checked(run, request, problems)
        except FailedChecks:
            plain = self.with_plain_pitch(last[0], stake, context) if last else None
            if plain is None:
                raise
            answer, status, pitch_source = plain, "retried", "code"
        if answer.declined:
            return declined_bid(stake, context["manager"]) | {"model": self.model}
        bid = answer.model_dump(exclude={"declined", "angle"})
        bid |= {"urgency": min(1.0, max(0.0, answer.urgency)), "status": status, "model": self.model,
                "pitch_source": pitch_source}  # fmt: skip
        return bid | ({"angle": answer.angle} if answer.angle else {})

    def with_plain_pitch(self, answer: Answer, stake: dict[str, Any], context: dict[str, Any]) -> Answer | None:
        """The second answer with code's line for the room in place of its pitch, when the pitch was all that failed
        (#138): a good phone explanation is not thrown away over the group screen's line. None when anything else
        still fails, so the whole plain bid goes out as before."""
        if answer.declined:
            return None
        delta = evidence(stake, "_delta")["id"]  # the plain line quotes the swing
        plain = answer.model_copy(
            update={
                "pitch_to_host": room_line(stake, context["manager"]),
                "evidence_ids": list(dict.fromkeys([*answer.evidence_ids, delta])),
            }
        )
        return None if self.problems(plain, stake, context) else plain
