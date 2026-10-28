"""Thirty scripted key moments for the latency test (#6), built on the contract examples.

Hand-written so the test does not wait for the simulator (#7) or the points engine (#8).
Every stake validates against contracts/schemas/stake.schema.json.
"""

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from watchparty.points.standings import number

EXAMPLES = Path(__file__).parents[2] / "contracts" / "examples"

# (key, label, value, unit) facts about the event, shared by every stake in the moment.
Fact = tuple[str, str, float, str]
# (manager, delta_points, reason_code)
Hit = tuple[str, float, str]


@cache
def meta() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((EXAMPLES / "match-meta.json").read_text())
    return data


@cache
def league() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((EXAMPLES / "league.json").read_text())
    return data  # Leo writes in Spanish (#16): the heavier, conservative case for latency


@dataclass(frozen=True)
class Moment:
    moment_id: str
    minute: int
    summary: str
    stakes: list[dict[str, Any]]


XG = "xG of the shot"
SCRIPT: list[tuple[int, str, list[Fact], list[Hit]]] = [
    (4, "Jonah Pryce (Hollinsford United) forces a save from Rhys Mallory (Saltmere Rovers).",
     [("xg", XG, 0.12, "xg")], [("mgr_2", 2, "save_points"), ("mgr_1", 2, "shot_on_target")]),
    (9, "Ollie Fenwick wins the ball back high up the pitch for Hollinsford United.",
     [], [("mgr_4", 1, "press_win")]),
    (12, "Dylan Crossfield scores for Saltmere Rovers from a cutback.",
     [("xg", XG, 0.38, "xg")], [("mgr_5", 10, "goal"), ("mgr_2", 5, "goal"), ("mgr_1", -1, "conceded")]),
    (15, "Tayo Brennan hits the post for Hollinsford United.",
     [("xg", XG, 0.21, "xg")], [("mgr_4", 0, "shot_woodwork")]),
    (18, "Elliot Marsden picks out Jules Ravier with a 31 m through ball.",
     [("pass_m", "Pass distance", 31, "m"), ("difficulty", "Pass difficulty", 0.8, "ratio")],
     [("mgr_3", 4, "chance_created")]),
    (21, "Yellow card for Marcus Eyre (Saltmere Rovers) after a late tackle.",
     [], [("mgr_1", -1, "card")]),
    (24, "Rhys Mallory saves from Tayo Brennan, his second save of the match.",
     [("xg", XG, 0.27, "xg"), ("saves", "Saves by Rhys Mallory", 2, "count")],
     [("mgr_2", 4, "save_points"), ("mgr_4", 2, "shot_on_target")]),
    (27, "Rafael Dunmore completes his third progressive pass of the half.",
     [("passes", "Progressive passes this half", 3, "count")], [("mgr_1", 1.5, "progressive_pass")]),
    (30, "Saltmere Rovers reach 30 minutes without conceding again.",
     [("stretch", "Minutes without conceding", 30, "minute")],
     [("mgr_2", 3, "shutout_stretch"), ("mgr_4", 1, "shutout_stretch")]),
    (33, "Jonah Pryce heads in the equaliser for Hollinsford United.",
     [("xg", XG, 0.17, "xg")], [("mgr_1", 10, "goal"), ("mgr_3", 5, "goal"), ("mgr_2", -2, "conceded")]),
    (36, "Ade Fashola intercepts in midfield.",
     [], [("mgr_4", 0.5, "ball_win")]),
    (39, "Rhys Mallory makes his third save, from Jonah Pryce.",
     [("xg", XG, 0.31, "xg"), ("saves", "Saves by Rhys Mallory", 3, "count")],
     [("mgr_2", 8, "save_tier_reached"), ("mgr_1", 2, "shot_on_target"), ("mgr_3", 1, "shot_on_target")]),
    (42, "Malik Tennant sprints at 34 km/h to break the press.",
     [("speed", "Top speed", 34, "km_h")], [("mgr_1", 1, "press_beaten")]),
    (45, "Half time: Hollinsford United 1, Saltmere Rovers 1.",
     [("home", "Hollinsford United goals", 1, "count"), ("away", "Saltmere Rovers goals", 1, "count")],
     [("mgr_2", 0, "half_time"), ("mgr_1", 0, "half_time")]),
    (48, "Conor Blythe's shot is blocked.",
     [("xg", XG, 0.09, "xg")], [("mgr_5", 0, "shot_blocked")]),
    (51, "Kwame Adomako wins a tackle on the edge of his own box.",
     [], [("mgr_2", 0.5, "ball_win")]),
    (54, "Jules Ravier scores for Saltmere Rovers from 27 m.",
     [("xg", XG, 0.06, "xg"), ("distance", "Shot distance", 27, "m")],
     [("mgr_3", 7, "long_odds_goal"), ("mgr_1", -1, "conceded"), ("mgr_5", -1, "conceded")]),
    (57, "Luca Ferrante is booked for dissent.",
     [], [("mgr_5", -1, "card")]),
    (60, "Elliot Marsden's cross creates a big chance.",
     [("xg", "xG of the chance created", 0.24, "xg")], [("mgr_3", 2, "chance_created")]),
    (63, "Tayo Brennan scores; Hollinsford United are level again.",
     [("xg", XG, 0.44, "xg")], [("mgr_4", 10, "goal"), ("mgr_2", 3, "goal_and_conceded")]),
    (66, "Lasse Vikholm clears off the line.",
     [], [("mgr_2", 1, "ball_win")]),
    (69, "Rhys Mallory's fourth save, from Ollie Fenwick.",
     [("xg", XG, 0.19, "xg"), ("saves", "Saves by Rhys Mallory", 4, "count")],
     [("mgr_2", 4, "save_points"), ("mgr_4", 1, "shot_on_target")]),
    (72, "Marcus Eyre is sent off for a second yellow card.",
     [], [("mgr_1", -3, "card")]),
    (75, "Ollie Fenwick completes a 38 m switch of play under pressure.",
     [("pass_m", "Pass distance", 38, "m"), ("difficulty", "Pass difficulty", 0.75, "ratio")],
     [("mgr_4", 0.5, "progressive_pass")]),
    (78, "Dylan Crossfield misses an open goal.",
     [("xg", XG, 0.62, "xg")], [("mgr_5", 0, "big_miss"), ("mgr_2", 0, "big_miss")]),
    (81, "Rhys Mallory's fifth save unlocks the second Wall tier.",
     [("xg", XG, 0.35, "xg"), ("saves", "Saves by Rhys Mallory", 5, "count")],
     [("mgr_2", 12, "save_tier_reached"), ("mgr_4", 1, "shot_on_target")]),
    (84, "Tom Ashcombe saves a penalty from Dylan Crossfield.",
     [("xg", XG, 0.76, "xg")],
     [("mgr_1", 5, "save_points"), ("mgr_5", -2, "penalty_missed"), ("mgr_2", -1, "penalty_missed")]),
    (87, "Jonah Pryce scores the winner for Hollinsford United.",
     [("xg", XG, 0.29, "xg")], [("mgr_1", 10, "goal"), ("mgr_3", 5, "goal"), ("mgr_2", -2, "conceded")]),
    (90, "Rafael Dunmore sprints at 33 km/h to win the ball back.",
     [("speed", "Top speed", 33, "km_h")], [("mgr_1", 1, "press_win")]),
    (93, "Full time: Hollinsford United 3, Saltmere Rovers 2.",
     [("home", "Hollinsford United goals", 3, "count"), ("away", "Saltmere Rovers goals", 2, "count")],
     [("mgr_1", 0, "full_time"), ("mgr_2", 0, "full_time"), ("mgr_5", 0, "full_time")]),
]  # fmt: skip


def _ranks(totals: dict[str, float]) -> dict[str, int]:
    order = sorted(totals, key=lambda m: -totals[m])
    return {m: order.index(m) + 1 for m in totals}


def _build() -> list[Moment]:
    totals = {m["id"]: float(m["points"]) for m in league()["managers"]}
    moments = []
    for n, (minute, summary, facts, hits) in enumerate(SCRIPT, start=1):
        moment_id = f"m_{n:02d}"
        before = _ranks(totals)
        for manager, delta, _ in hits:
            totals[manager] += delta
        after = _ranks(totals)
        shared = [
            {"id": f"ev_{moment_id}_minute", "label": "Match minute", "value": minute, "unit": "minute",
             "source": {"kind": "event"}},
            *({"id": f"ev_{moment_id}_{key}", "label": label, "value": value, "unit": unit,
               "source": {"kind": "event"}} for key, label, value, unit in facts),
        ]  # fmt: skip
        stakes = []
        for manager, delta, reason in hits:
            own = [
                ("delta", "Impact points from this moment", delta, "ip"),
                ("total", "League total after this moment", number(totals[manager]), "ip"),
                ("rank_before", "League rank before", before[manager], "count"),
                ("rank_after", "League rank after", after[manager], "count"),
            ]
            evidence = shared + [
                {"id": f"ev_{moment_id}_{manager}_{key}", "label": label, "value": value, "unit": unit,
                 "source": {"kind": "computed", "formula": f"{reason}@v1"}}
                for key, label, value, unit in own
            ]  # fmt: skip
            stakes.append({
                "moment_id": moment_id, "manager": manager, "t": minute * 60_000,
                "delta_points": delta, "total_points": number(totals[manager]),
                "rank_before": before[manager], "rank_after": after[manager],
                "reason_code": reason, "above_threshold": True, "evidence": evidence,
            })  # fmt: skip
        moments.append(Moment(moment_id, minute, summary, stakes))
    return moments


MOMENTS = _build()
