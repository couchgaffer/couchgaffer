"""Half-time and full-time summaries (#12): the manager agent looks the match up through the MCP tools.

There is no latency limit here, so the agent calls the match tools itself, on the view frozen at the
whistle, then writes a short summary in the manager's style and language. A number may only be one the
tools served for that view. One retry with the problems fed back, then a plain summary from code.
"""

import json
import re
from collections.abc import Callable
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict

from watchparty.agents.base import written
from watchparty.agents.checks import (
    DECIMAL_STYLE,
    GENDER_NEUTRAL,
    LANGUAGES,
    SUMMARY,
    language_problems,
    names_problems,
    number_problems,
    style_problems,
    tone_problems,
)
from watchparty.agents.foundry import Runner, agent_runner, checked, english_first
from watchparty.matchtools import tools as match_tools
from watchparty.matchtools.view import Given, MatchView

PHASES = {"half_time": "Half time", "full_time": "Full time"}
MAX_CHARS = 280


class SummaryAnswer(BaseModel):
    """The model's structured output (strict JSON schema)."""

    model_config = ConfigDict(extra="forbid")
    text_en: str  # first: for another language the model writes English, then translates it, as explanations do
    text: str
    evidence_ids: list[str]
    rulebook_ids: list[str]  # rulebook entries from get_rule_explanation (Foundry IQ, #18) that the text relies on


INSTRUCTIONS = """You write one manager's half-time or full-time summary for their phone, in a friends'
free-to-play, fantasy-style football mini-league watching one invented match together.

Rules:
- Look the match up with the tools first. Every number in your summary must be the value of an evidence item
  the tools returned, written as given; put the id of every evidence item you use in evidence_ids. Cite
  squad points and key moments only for your own manager; the standings cover everyone.
- Name only the players, clubs and managers in NAMES. Never mention or compare with any real player, club,
  manager, competition or broadcaster.
- Speak to the manager as "you": their league position, what their squad earned, and the moment that
  mattered most to them.
- analyst style: at most three sentences, with the key numbers. casual style: two short, warm sentences
  with only two numbers, the manager's rank and IP total; the moment that mattered goes in words.
- text is in the manager's language. When that is not en, write text_en first, in English, and then text as
  its translation; when it is en, text_en is "".
- Friendly banter only, never mean. This league is free to play: never mention betting, money or prizes.
- The points are impact points (IP). Never say "bonus" or "clean sheet": those are another game's words.
- If get_rule_explanation is available, use it to add one short clause on why the moment that mattered most
  counted (for example "since a save against a big chance counts for more"). Explain why in words; any number
  must come from the match tools' evidence. Put the ids of the rulebook entries you relied on in
  rulebook_ids, never in evidence_ids.
- Never write an em dash or an en dash: use a comma instead."""

STYLE_DIRECTIVES = {
    "analyst": (f"ANALYST style: at most three sentences, at most {SUMMARY['analyst'][2]} characters, with the key"
                " numbers."),
    "casual": (f"CASUAL style: two short, warm sentences, at most {SUMMARY['casual'][2]} characters in all. First,"
               " where you stand, with exactly two numbers: your rank and your IP total. Second, the moment that"
               " mattered most to you, in words only, no numbers."),
}  # fmt: skip


def foundry_runner(client: AsyncOpenAI, model: str) -> Callable[[list[Any]], Runner[SummaryAnswer]]:
    """A runner per summary, so each one gets the MCP tool for its own session and view."""
    return lambda tools: agent_runner(client, model, "summary", INSTRUCTIONS, SummaryAnswer, 500, tools)


def summary_problems(
    answer: dict[str, Any], manager: dict[str, Any], served: dict[str, dict[str, Any]], names: list[str],
    rulebook: Given | None = None, gameweek: bool = False,
) -> list[str]:  # fmt: skip
    """The checks every summary passes before a phone shows it, whoever wrote it.

    rulebook: the entries get_rule_explanation gave this manager's agent (#37). It may cite only those, and
    if it was given any it must cite the ones its reason relies on.
    """
    cited, english = answer.get("evidence_ids") or [], manager["lang"] == "en"
    given, cited_rules = rulebook or {}, answer.get("rulebook_ids") or []
    problems = [] if cited else ["cite at least one evidence id"]
    # Say what would pass: told only "not X", the model swaps in another scoring rule name or evidence id (#84)
    allowed = f"one of {', '.join(given)}" if given else "empty, since it gave you no entries"
    problems += [f"rulebook_ids must be entries get_rule_explanation gave you ({allowed}), not {i}"
                 for i in cited_rules if i not in given]  # fmt: skip
    if given and not cited_rules:
        problems.append("you looked up the league rulebook: put the ids of the entries your reason relies on in"
                        " rulebook_ids")  # fmt: skip
    if not 0 < len(answer.get("text") or "") <= MAX_CHARS:
        problems.append(f"text must be 1 to {MAX_CHARS} characters")
    if not english and not 0 < len(answer.get("text_en") or "") <= MAX_CHARS:
        problems.append(f"text_en must be the English version, 1 to {MAX_CHARS} characters")
    problems += style_problems(answer.get("text") or "", manager["style"], SUMMARY, manager["lang"])
    if not english:  # the copy reads in the same style (#36)
        problems += [f"text_en: {p}" for p in style_problems(answer.get("text_en") or "", manager["style"], SUMMARY)]
    texts = [(answer.get("text") or "", manager["lang"])] + ([] if english else [(answer.get("text_en") or "", "en")])
    for text, lang in texts:
        problems += number_problems(text, list(served.values()), cited, lang)
        problems += names_problems(text, names)
        problems += tone_problems(text)
        problems += language_problems(text, lang, names)
        problems += gameweek_problems(text) if gameweek else []
    return list(dict.fromkeys(problems))


# A gameweek's summary covers three matches: "this match" would mean one of them, and a fixture id is not a word
# for a phone (#152)
# Only words that mean one match and name no clubs: "the match between ..." and "el partido del ..." are fine
ONE_MATCH = re.compile(
    r"\bthis (match|game)\b|\beste partido\b|\ben el partido\b(?!\s+(entre|del?)\b)|\bfx_\d\b", re.IGNORECASE
)


def gameweek_problems(text: str) -> list[str]:
    return [f"this covers the gameweek's three matches: say \"this gameweek\" (\"esta jornada\") or name the clubs,"
            f" not {found.group(0)!r}" for found in ONE_MATCH.finditer(text)]  # fmt: skip


class SummaryAgent:
    def __init__(self, runner_for: Callable[[list[Any]], Runner[SummaryAnswer]], model: str) -> None:
        self.runner_for, self.model = runner_for, model

    def request(self, manager: dict[str, Any], phase: str, names: list[str], gameweek: bool = False) -> str:
        """The facts as JSON, then the directive last, where models weigh it most."""
        facts = json.dumps({
            "manager": manager["display_name"], "manager_id": manager["id"], "phase": PHASES[phase].lower(),
            "style": manager["style"], "language": manager["lang"], "NAMES": names,
        }, ensure_ascii=False)  # fmt: skip
        directive = (f"Call get_standings, then get_squad_points and get_key_moments for {manager['id']}, before"
                     " writing. If get_rule_explanation is available, add one short clause on why your biggest moment"
                     " counted, explained in words (any number must come from the match tools' evidence), and put"
                     " the entry ids in rulebook_ids. rulebook_ids holds only ids get_rule_explanation returned,"
                     " never evidence ids or scoring rule names; it is [] if you did not call it."
                     f" {STYLE_DIRECTIVES[manager['style']]}")  # fmt: skip
        if gameweek:  # one summary for three fixtures at once (#152)
            directive += (" This is a gameweek: three fixtures, fx_1 to fx_3, played at once, and your squad's players"
                          " are spread across them. Sum up the whole gameweek for you, not one fixture; a moment may"
                          " come from any fixture. get_key_moments and get_match_stats take an optional"
                          " fixture_id. Never write a fixture id such as fx_3 in text: name the clubs.")  # fmt: skip
        if manager["lang"] != "en":
            language = LANGUAGES.get(manager["lang"], manager["lang"])
            directive += (f" First write text_en in English; it is never empty. Then write text as its natural"
                          f" {language} ('{manager['lang']}') translation, as a {language}-speaking fan would say it,"
                          " with the same facts and numbers; keep every name as in NAMES.")  # fmt: skip
            directive += GENDER_NEUTRAL.get(manager["lang"], "") + DECIMAL_STYLE.get(manager["lang"], "")
        return f"{facts}\n\n{directive}"

    async def summarise(self, manager: dict[str, Any], phase: str, tools: Any, served: dict[str, dict[str, Any]],
                        names: list[str], rulebook: Given | None = None,
                        gameweek: bool = False) -> dict[str, Any]:  # fmt: skip
        run = self.runner_for([tools])
        run = english_first(run, "text", "text_en") if manager["lang"] == "en" else run
        answer, status = await checked(run, self.request(manager, phase, names, gameweek),
                                       lambda a: summary_problems(a.model_dump(), manager, served, names,
                                                                  rulebook, gameweek))  # fmt: skip
        return answer.model_dump() | {"status": status, "model": self.model}


def _ordinal(n: int) -> str:
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n, 'th') }"


# The plain summary, per language: the Spanish and its English copy come from the same facts (#36).
FALLBACK_SUMMARY = {
    "en": {"standing": "{phase}: you're {rank} on {points} IP", "analyst": "{standing}, {gained} {span}."
           " Best for you: {best}, {best_points} IP.", "half_time": "Half time", "full_time": "Full time",
           "match": "this match", "gameweek": "this gameweek"},
    "es": {"standing": "{phase}: vas en el {rank} puesto con {points} IP", "analyst": "{standing}, {gained} {span}."
           " Lo mejor para ti: {best}, {best_points} IP.", "half_time": "Al descanso",
           "full_time": "Final del partido", "match": "en este partido", "gameweek": "en esta jornada",
           "gameweek_full_time": "Final de la jornada"},
}  # fmt: skip


def fallback_summary(view: MatchView, manager: dict[str, Any], phase: str) -> dict[str, Any]:
    """A plain summary built by code from what the tools serve, so it always passes the checks."""
    mid = manager["id"]
    view = view.for_caller(mid)  # served to this manager, so it can cite them
    table = {e.id: e for e in match_tools.standings(view).evidence}
    squad = match_tools.squad_points(view, mid)
    points, rank = table[f"ev_table_{mid}_points"], table[f"ev_table_{mid}_rank"]
    cited = [points.id, rank.id]
    items = {e.id: e for e in squad.evidence}
    gained = items[f"ev_squad_{mid}_total"]
    best = max(squad.players, key=lambda p: items[f"ev_squad_{mid}_{p.id}_total"].value)
    best_points = items[f"ev_squad_{mid}_{best.id}_total"]
    if manager["style"] == "analyst":
        cited += [gained.id, best_points.id]

    def say(lang: str) -> str:
        words = FALLBACK_SUMMARY[lang]
        place = _ordinal(int(rank.value)) if lang == "en" else f"{int(rank.value)}.º"  # never a gender (#16)
        whistle = words.get(f"gameweek_{phase}", words[phase]) if view.gameweek else words[phase]
        standing = words["standing"].format(phase=whistle, rank=place, points=written(points.value, lang))
        if manager["style"] == "casual":
            return f"{standing}."
        return words["analyst"].format(standing=standing, gained=written(gained.value, lang, sign=True),
                                       span=words["gameweek" if view.gameweek else "match"], best=best.name,
                                       best_points=written(best_points.value, lang))  # fmt: skip

    lang = manager["lang"]
    return {"text": say(lang), "text_en": "" if lang == "en" else say("en"), "evidence_ids": cited,
            "rulebook_ids": [], "status": "fallback"}  # fmt: skip
