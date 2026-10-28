"""Which moments go in each manager's recap, and what each one says. Pure, so it is tested without video.

Per manager: their biggest swings first, then moments where they won the group screen; at most five,
shown in match order; in a gameweek they can come from any of the three fixtures (#94). Captions are that
manager's own phone text, in their style and language; the end card is their full-time summary (#12).
"""

from dataclasses import dataclass
from typing import Any

from watchparty.app.cues import phone_headline

MAX_CLIPS = 5
BEFORE_MS, AFTER_MS = 10_500, 3_000  # match time around the climax: the build-up and the outcome


@dataclass(frozen=True)
class Clip:
    moment_id: str
    climax_t: int
    start_t: int
    end_t: int
    minute: int
    headline: str  # what happened, from the group screen's stat card
    points: str  # what it meant for this manager, for example "+5 IP"
    caption: str  # this manager's own explanation
    by_ai: bool
    players: list[str]  # this manager's players in the moment, tagged on the pitch
    fixture_id: str | None = None  # the fixture it is drawn from in a gameweek bundle (#94); None in one match


@dataclass(frozen=True)
class Recap:
    manager: str
    name: str
    lang: str
    league_name: str
    rank: int
    points: float
    clips: list[Clip]
    end_card: str
    end_by_ai: bool


def _latest(cues: list[dict[str, Any]]) -> dict[str, Any] | None:
    return max(cues, key=lambda c: c["seq"]) if cues else None


def plan(bundle: dict[str, Any], manager: str) -> Recap:
    m = next(x for x in bundle["league"]["managers"] if x["id"] == manager)
    stakes = [s for moment in bundle["stakes"] for s in moment if s["manager"] == manager and s["above_threshold"]]
    won = [d["moment_id"] for d in bundle["log"] if d.get("chosen_manager") == manager]
    ranked = [s["moment_id"] for s in sorted(stakes, key=lambda s: -abs(s["delta_points"]))]
    chosen = list(dict.fromkeys(ranked + won))[:MAX_CLIPS]
    climaxes = {e["moment_id"]: e for e in bundle["climaxes"]}
    stake_of = {s["moment_id"]: s for s in stakes}
    cues = [c for c in bundle["log"] if "cue_id" in c]
    phone = [c for c in cues if c["screen"] == f"phone:{manager}"]

    clips = []
    for mid in sorted(chosen, key=lambda x: climaxes[x]["t"]):
        t = climaxes[mid]["t"]
        mine = _latest([c for c in phone if c.get("moment_id") == mid and c["kind"] in ("stat", "explanation")])
        group = _latest([c for c in cues if c["screen"] == "group" and c.get("moment_id") == mid
                         and c["kind"] == "stat"])  # fmt: skip
        stake = stake_of.get(mid)
        # A moment worth 0 IP that moved the manager in the table is about the move, as on their phone (#88):
        # "League position 3 → 4", never "+0 IP".
        points = phone_headline(stake, m["lang"]) if stake else (mine["payload"]["headline"] if mine else "")
        clips.append(Clip(
            moment_id=mid, climax_t=t, start_t=max(0, t - BEFORE_MS), end_t=t + AFTER_MS, minute=t // 60_000 + 1,
            headline=group["payload"]["headline"] if group else "", points=points,
            caption=(mine["payload"].get("body") or mine["payload"]["headline"]) if mine else "",  # never a repeat
            by_ai=bool(mine and mine["source"] == "manager"), players=stake["players"] if stake else [],
            fixture_id=climaxes[mid].get("fixture_id"),
        ))  # fmt: skip
    summary = _latest([c for c in phone if c["kind"] == "summary" and c["payload"]["headline"] == "Full time"])
    final = next(r for r in bundle["table"] if r["manager"] == manager)
    return Recap(manager=manager, name=m["display_name"], lang=m["lang"], league_name=bundle["league"]["name"],
                 rank=final["rank"], points=final["points"], clips=clips,
                 end_card=summary["payload"]["body"] if summary else "Full time.",
                 end_by_ai=bool(summary and summary["source"] == "manager"))  # fmt: skip
