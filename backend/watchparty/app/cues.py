"""Timed overlay cues (rule 4.1 stage d): what each screen shows, when, and for how long."""

import itertools
from typing import Any

from watchparty.agents.base import RULE_NAMES, written

GROUP_DWELL_MS, PHONE_DWELL_MS = 6000, 4000
SHOWN_FOR_MS = 4 * 60_000  # match time; key moments are at least 5 match minutes apart
MAX_T = 7_200_000
HEADLINE_MAX = 80


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def stat_headline(event: dict[str, Any], names: dict[str, str]) -> str:
    """The plain stat card, from code, shown the instant a key moment lands."""
    who = names.get(event.get("player", ""), "")
    if event["type"] == "card":
        return f"{event['outcome'].capitalize()} card: {who}"
    if event["type"] != "shot":
        return str(event["type"]).replace("_", " ").capitalize()
    xg = f"xG {event['shot']['xg']:g}"
    headlines = {
        "goal": f"GOAL! {who}",
        "saved": f"Saved! {names.get(event.get('opponent', ''), 'The keeper')} denies {who}, {xg}",
        "woodwork": f"Off the woodwork: {who}, {xg}",
        "blocked": f"Blocked: {who}, {xg}",
    }
    return headlines.get(event["outcome"], f"Wide: {who}, {xg}")


def points_headline(delta: float, lang: str) -> str:
    """A phone's points card in its own manager's language: +2,5 IP or Sin cambios on a Spanish phone (#108).
    stat_headline stays English: the group screen and the one shared workflow summary read it, never a phone."""
    return f"{written(delta, lang, sign=True)} IP" if delta else RULE_NAMES[lang]["no_change"].capitalize()


RANK_HEADLINE = {"en": ("League position {before} → {after}", "League table"),
                 "es": ("Puesto en la liga {before} → {after}", "Clasificación")}  # fmt: skip


def phone_headline(stake: dict[str, Any], lang: str) -> str:
    """A phone explanation's headline: the swing, or for a moment worth 0 IP the league move it is about. Never
    "No change" over a message about a moment that did change something (#88)."""
    if stake["delta_points"]:
        return points_headline(stake["delta_points"], lang)
    moved, table = RANK_HEADLINE.get(lang, RANK_HEADLINE["en"])
    before, after = stake.get("rank_before"), stake.get("rank_after")
    return moved.format(before=before, after=after) if before != after else table


class Cues:
    """Numbers cues in one sequence across every screen of a session."""

    def __init__(self) -> None:
        self._seq = itertools.count()

    def make(self, screen: str, kind: str, source: str, show_at: int, payload: dict[str, Any],
             moment_id: str | None = None, replaces: str | None = None) -> dict[str, Any]:  # fmt: skip
        seq = next(self._seq)
        cue: dict[str, Any] = {"cue_id": f"c_{seq}", "seq": seq, "screen": screen}
        if moment_id:
            cue["moment_id"] = moment_id
        cue |= {"show_at": show_at, "min_dwell_ms": GROUP_DWELL_MS if screen == "group" else PHONE_DWELL_MS,
                "expires_at": min(MAX_T, show_at + SHOWN_FOR_MS)}  # fmt: skip
        if replaces:
            cue["replaces"] = replaces
        payload = {**payload, "headline": clip(payload["headline"], HEADLINE_MAX)}
        for key in ("body", "body_en"):
            if key in payload:
                payload[key] = clip(payload[key], 280)
        return cue | {"kind": kind, "source": source, "payload": payload}


CLASH_REAL_MS = 1000  # the two pitches stay up about a second of real time before the verdict (#89)


def clash_cues(
    cues: Cues, decision: dict[str, Any], t: int, names: dict[str, str], speed: float
) -> list[dict[str, Any]]:
    """A close call settled by the host (or its fallback) as the group screen and the loser's phone show it (#89):
    a clash with the two pitches, the chosen manager and the second-ranked one, until verdict_at, then the chosen
    card; and a lost-screen note on the loser's phone. Clear winners, and pairs without two pitches, get nothing.

    verdict_at is a real second converted to match time at the session's speed, as the screens' playback clock
    runs, and falls strictly between show_at and expires_at, which match the chosen card's."""
    if not decision["close_call"] or decision["source"] not in ("host", "fallback"):
        return []
    winner = decision["chosen_manager"]
    # A host decision names its contenders (#88); a fallback has none, and the pair is worked out the same way:
    # the winner and the first other manager in the scorer's order
    named = [c["manager"] for c in decision.get("contenders") or []]
    rival = next((m for m in named if m != winner), None) or next(
        (r["manager"] for r in decision["scores"] if r["manager"] != winner), None)  # fmt: skip
    pair = [r for r in decision["scores"] if r["manager"] in (winner, rival) and r.get("pitch")]
    verdict_at, expires_at = t + round(CLASH_REAL_MS * speed), min(MAX_T, t + SHOWN_FOR_MS)
    if len(pair) != 2 or not t < verdict_at < expires_at:
        return []
    mid, source = decision["moment_id"], decision["source"]
    contenders = [
        {"manager": r["manager"], "pitch": clip(r["pitch"], 140)}
        | ({"pitch_source": r["pitch_source"]} if r.get("pitch_source") else {})
        for r in pair
    ]  # #138
    clash = cues.make("group", "clash", source, t, {"headline": "Close call", "contenders": contenders,
                                                    "verdict_at": verdict_at, "winner": winner}, mid)  # fmt: skip
    clash["min_dwell_ms"] = CLASH_REAL_MS
    name = names.get(winner, winner)
    pitch = next(c["pitch"] for c in contenders if c["manager"] == winner)
    body = f"The screen went to {'you' if name == 'You' else name}: {pitch}"  # the judge is "You" (session.py)
    # The loser hears it when the group screen shows the verdict, not a second before
    lost = cues.make(f"phone:{rival}", "lost_screen", source, verdict_at,
                     {"headline": "Shown on your phone", "body": body, "winner": winner}, mid)  # fmt: skip
    return [clash, lost]
