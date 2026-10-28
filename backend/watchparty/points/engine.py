"""Points engine (#8): match events in, impact points and Stakes out.

Deterministic, and the only source of numbers the agents may use: every number in a Stake is an
evidence item. Scoring rules are data (rules_v1.json), our own invention, free to play.
"""

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from watchparty.points.standings import Standings, number

HERE = Path(__file__).parent
RULES: dict[str, Any] = json.loads((HERE / "rules_v1.json").read_text())
CONTROL_WINDOW_MS = 5 * 60_000
POSSESSION_EVENTS = {"kickoff", "pass", "carry", "dribble", "shot", "corner", "free_kick"}
BREAKS_IN_PLAY = {"corner", "free_kick", "foul", "kickoff", "half_time", "offside"}
SCORED_BY_PLAYER = {"pass", "pressure", "tackle", "interception", "card", "shot"}


def load_league(name: str = "league_v1.json") -> dict[str, Any]:
    """The single match's league, or league_gw_v1.json for a gameweek (#94)."""
    league: dict[str, Any] = json.loads((HERE / name).read_text())
    return league


def minute(t: int) -> int:
    """Football minutes count from 1: the first 60 seconds are the 1st minute."""
    return t // 60_000 + 1


@dataclass(frozen=True)
class Award:
    player: str
    rule: str
    points: float


class PointsEngine:
    """Scores one fixture. Its awards go into `standings`: its own, or one shared by a gameweek's engines (#150)."""

    def __init__(
        self,
        meta: dict[str, Any],
        league: dict[str, Any],
        rules: dict[str, Any] = RULES,
        standings: Standings | None = None,
        fixture_id: str | None = None,
    ) -> None:
        self.rules, self.r = rules, rules["rules"]
        self.version = rules["version"]
        players = [p | {"side": s} for s in ("home", "away") for p in meta[s]["players"]]
        self.side_of = {p["id"]: p["side"] for p in players}
        self.position = {p["id"]: p["position"] for p in players}
        self.name = {p["id"]: p["name"] for p in players}
        self.managers = league["managers"]
        # Squads are fixed before kickoff, so how many managers own each player is counted once (#87)
        self.owners: Counter[str] = Counter(p for m in self.managers for p in m["squad"])
        if standings is not None and fixture_id is None:  # every engine would file under one key (#150)
            raise ValueError("a shared Standings needs the engine's fixture_id")
        self.shared = standings is not None  # a gameweek: other fixtures add to the same totals
        self.standings = Standings(league) if standings is None else standings
        self.fixture_id = fixture_id
        self.player_points: defaultdict[str, defaultdict[str, float]] = defaultdict(lambda: defaultdict(float))
        self.saves: Counter[str] = Counter()
        self.progressive: Counter[tuple[str, int]] = Counter()
        self.off: set[str] = set()
        self.half = 1
        self.unbeaten_since = {"home": 0, "away": 0}
        self.stretches = {"home": 0, "away": 0}
        self.last_pass: dict[str, Any] | None = None
        self.last_pressure: dict[str, tuple[int, str]] = {}
        # match stats
        self.shots: Counter[str] = Counter()
        self.xg: defaultdict[str, float] = defaultdict(float)
        self.player_xg: defaultdict[str, float] = defaultdict(float)  # each player's own chances, not his team's
        self.player_shots: defaultdict[str, list[int]] = defaultdict(list)
        self.goals: Counter[str] = Counter()
        self.assist: dict[str, Any] | None = None  # the pass that set up the last shot, if one did
        self.passes: Counter[str] = Counter()
        self.completed: Counter[str] = Counter()
        self.possession_ms: Counter[str] = Counter()
        self.ball: list[tuple[int, str, float]] = []  # (t, side in possession, its attacking progress)
        self.t = 0
        # the open key moment
        self.moment: str | None = None
        self.moment_awards: list[Award] = []
        self.moment_events: list[dict[str, Any]] = []

    # --- scoring ------------------------------------------------------------------------------------

    def _defence(self, side: str) -> list[str]:
        return [
            p for p, s in self.side_of.items() if s == side and self.position[p] in ("GK", "DEF") and p not in self.off
        ]

    def _score(self, e: dict[str, Any]) -> list[Award]:
        r, kind, side = self.r, e["type"], e["team"]
        player: str = e["player"] if kind in SCORED_BY_PLAYER else ""  # fail fast on a malformed event
        awards: list[Award] = []
        for s in ("home", "away"):  # 30-minute stretches without conceding, as the clock passes them
            stretches = (e["t"] - self.unbeaten_since[s]) // r["shutout_stretch"]["every_ms"]
            if stretches > self.stretches[s]:
                gained = (stretches - self.stretches[s]) * r["shutout_stretch"]["points"]
                awards += [Award(p, "shutout_stretch", gained) for p in self._defence(s)]
                self.stretches[s] = stretches

        if kind in BREAKS_IN_PLAY:
            self.last_pass = None  # a chance created needs open play from the pass to the shot
        if kind == "half_time":
            self.half = 2
        elif kind == "pass":
            if e.get("outcome") == "success":
                self.last_pass = e
                if (
                    e["pass"]["progressive"]
                    and self.progressive[(player, self.half)] < r["progressive_pass"]["max_per_half"]
                ):
                    self.progressive[(player, self.half)] += 1
                    awards.append(Award(player, "progressive_pass", r["progressive_pass"]["points"]))
            else:
                self.last_pass = None
        elif kind == "pressure":
            self.last_pressure[side] = (e["t"], player)
        elif kind in ("tackle", "interception") and e.get("outcome") == "success":
            self.last_pass = None
            awards.append(Award(player, "ball_win", r["ball_win"]["points"]))
            pressed = self.last_pressure.pop(side, None)
            if pressed and e["t"] - pressed[0] <= r["press_win"]["window_ms"]:
                awards.insert(0, Award(pressed[1], "press_win", r["press_win"]["points"]))
        elif kind == "card":
            rule = "red_card" if e["outcome"] == "red" else "yellow_card"
            awards.append(Award(player, rule, r[rule]["points"]))
            if rule == "red_card":
                self.off.add(player)
        elif kind == "shot":
            awards += self._shot(e)
        return awards

    def _shot(self, e: dict[str, Any]) -> list[Award]:
        r, side, shooter, xg, outcome = self.r, e["team"], e["player"], e["shot"]["xg"], e["outcome"]
        awards = []
        assist = self.last_pass
        led = assist is not None and assist["team"] == side and assist.get("receiver") == shooter
        self.assist = assist if led else None
        if led and assist and xg >= r["chance_created"]["min_xg"]:
            awards.append(Award(assist["player"], "chance_created", round(xg * r["chance_created"]["per_xg"])))
        self.last_pass = None
        if outcome in ("goal", "saved"):
            awards.append(Award(shooter, "shot_on_target", r["shot_on_target"]["points"]))
        if outcome == "goal":
            awards.append(Award(shooter, "goal", r["goal"]["points"]))
            if xg < r["long_odds_goal"]["max_xg"]:
                awards.append(Award(shooter, "long_odds_goal", r["long_odds_goal"]["points"]))
            conceding = "away" if side == "home" else "home"
            awards += [Award(p, "conceded", r["conceded"]["points"]) for p in self._defence(conceding)]
            self.unbeaten_since[conceding], self.stretches[conceding] = e["t"], 0
        elif outcome == "saved":
            keeper = e["opponent"]
            self.saves[keeper] += 1
            awards.append(
                Award(keeper, "save_points", r["save_points"]["points"] + round(xg * r["save_points"]["per_xg"]))
            )
            for at, points in r["save_tier"]["tiers"]:
                if self.saves[keeper] == at:
                    awards.append(Award(keeper, "save_tier", points))
        return awards

    def observe(self, e: dict[str, Any]) -> None:
        """Update the match statistics only: no points, no stakes."""
        if self.ball:
            self.possession_ms[self.ball[-1][1]] += e["t"] - self.t
        self.t = e["t"]
        kind, side = e["type"], e["team"]
        if kind == "shot":
            self.shots[side] += 1
            self.xg[side] += e["shot"]["xg"]
            self.player_xg[e["player"]] += e["shot"]["xg"]
            self.player_shots[e["player"]].append(e["seq"])
            self.goals[e["player"]] += e.get("outcome") == "goal"
        if kind == "pass":
            self.passes[side] += 1
            self.completed[side] += e.get("outcome") == "success"
        if kind in POSSESSION_EVENTS or (kind in ("tackle", "interception") and e.get("outcome") == "success"):
            self.ball.append((e["t"], side, e["x"] if side == "home" else 1 - e["x"]))

    # --- feeding events -------------------------------------------------------------------------------

    def _mult(self, manager: dict[str, Any], player: str) -> float:
        return float(self.rules["captain_multiplier"]) if player == manager["captain"] else 1.0

    def _owners(self, player: str) -> list[tuple[str, float]]:
        return [(m["id"], self._mult(m, player)) for m in self.managers if player in m["squad"]]

    @property
    def manager_points(self) -> dict[str, float]:
        """Every manager's running total, across all fixtures when the Standings is shared."""
        return self.standings.points

    standing = staticmethod(Standings.standing)

    def ranks(self, points: dict[str, float] | None = None) -> dict[str, int]:
        return self.standings.ranks(points)

    def feed(self, e: dict[str, Any]) -> list[dict[str, Any]] | None:
        """Score one event. On a key moment's climax, return one Stake per manager."""
        if e.get("moment_id") and e["moment_id"] != self.moment:
            self.moment, self.moment_awards, self.moment_events = e["moment_id"], [], []
        self.observe(e)
        awards = self._score(e)
        for a in awards:
            self.player_points[a.player][a.rule] += a.points
            for manager, mult in self._owners(a.player):
                self.standings.add(self.fixture_id, manager, a.points * mult)
        if self.moment:  # a stretch the clock happens to complete during a moment is not that moment's doing (#31)
            self.moment_awards += [a for a in awards if a.rule != "shutout_stretch"]
            self.moment_events.append(e)
        if not e.get("climax"):
            return None
        # Ranks before and after this moment's own awards. A stretch the clock completed meanwhile, or an award
        # in another fixture, counts in both, so it never makes a rank change, or a bidder, of a moment it had
        # nothing to do with (#43, #150).
        own: defaultdict[str, float] = defaultdict(float)
        for a in self.moment_awards:
            for manager, mult in self._owners(a.player):
                own[manager] += a.points * mult
        earlier = {m: points - own[m] for m, points in self.manager_points.items()}
        before, after = self.ranks(earlier), self.ranks()
        moved = {m: self.standing(earlier, m) != self.standing(self.manager_points, m) for m in earlier}
        stakes = [self._stake(m, e, before[m["id"]], after[m["id"]], moved[m["id"]]) for m in self.managers]
        self.moment = None
        return stakes

    # --- stakes ---------------------------------------------------------------------------------------

    def _fact(self, eid: str, label: str, value: float, unit: str | None, seq: list[int]) -> dict[str, Any]:
        item = {"id": eid, "label": label, "value": value, "source": {"kind": "event", "event_seq": list(seq)}}
        return item | ({"unit": unit} if unit else {})

    def _computed(
        self, eid: str, label: str, value: float, unit: str, formula: str, seq: list[int] | None = None
    ) -> dict[str, Any]:
        source: dict[str, Any] = {"kind": "computed", "formula": f"{formula}@{self.version}"}
        if seq:
            source["event_seq"] = list(seq)
        return {"id": eid, "label": label, "value": value, "unit": unit, "source": source}

    def _stake(
        self, m: dict[str, Any], climax: dict[str, Any], rank_before: int, rank_after: int, moved: bool = True
    ) -> dict[str, Any]:
        mid, manager = climax["moment_id"], m["id"]
        mult = {p: self._mult(m, p) for p in m["squad"]}
        contributions: dict[tuple[str, str], float] = {}  # (player, rule) -> manager points, in award order
        for a in self.moment_awards:
            if a.player in mult:
                key = (a.player, a.rule)
                contributions[key] = contributions.get(key, 0) + a.points * mult[a.player]
        delta = number(sum(contributions.values()))
        total = number(self.manager_points[manager])
        seqs, at = [e["seq"] for e in self.moment_events], [climax["seq"]]

        evidence = [self._fact(f"ev_{mid}_minute", "Match minute", minute(climax["t"]), "minute", at)]
        if climax["type"] == "shot":
            evidence.append(self._fact(f"ev_{mid}_xg", "xG of the shot", climax["shot"]["xg"], "xg", at))
        for (player, rule), value in contributions.items():
            label = f"{self.name[player]}, {rule.replace('_', ' ')}" + (", captain x2" if mult[player] > 1 else "")
            evidence.append(
                self._computed(f"ev_{mid}_{manager}_{player}_{rule}", label, number(value), "ip", rule, seqs)
            )
        milestone = None
        keeper = next((p for p, rule in contributions if rule == "save_points" and self.position[p] == "GK"), None)
        if keeper:
            saves = self.saves[keeper]
            label = f"Saves by {self.name[keeper]}"
            evidence.append(self._computed(f"ev_{mid}_{keeper}_saves", label, saves, "count", "save_count", seqs))
            target = next((n for n, _ in self.r["save_tier"]["tiers"] if n > saves), None)
            if target:
                milestone = {"name": "save_tier", "progress": saves, "target": target}
        evidence += [
            self._computed(
                f"ev_{mid}_{manager}_delta", "Impact points from this moment", delta, "ip", "moment_delta", seqs
            ),
            self._computed(f"ev_{mid}_{manager}_total", "League total after this moment", total, "ip", "league_total"),
            self._computed(f"ev_{mid}_{manager}_rank_before", "League rank before", rank_before, "count", "rank"),
            self._computed(f"ev_{mid}_{manager}_rank_after", "League rank after", rank_after, "count", "rank"),
        ]
        ranked = sorted(contributions.items(), key=lambda kv: abs(kv[1]))  # stable: ties go to the later award
        one_step = milestone is not None and milestone["target"] - milestone["progress"] == 1
        stake: dict[str, Any] = {
            "moment_id": mid, "manager": manager, "t": climax["t"], "delta_points": delta, "total_points": total,
            "rank_before": rank_before, "rank_after": rank_after,
            "players": list(dict.fromkeys(p for p, _ in contributions)),
            "reason_code": ranked[-1][0][1] if ranked and ranked[-1][1] else "no_change",
            "above_threshold": abs(delta) >= self.rules["threshold_points"] or rank_before != rank_after or one_step,
            "evidence": evidence,
        }  # fmt: skip
        if milestone:
            stake["milestone"] = milestone
        if self.fixture_id:
            stake["fixture_id"] = self.fixture_id
        stake["facts"] = self._facts(m, climax, contributions, evidence, rank_before, rank_after, moved)
        return stake

    # --- facts (#87) ----------------------------------------------------------------------------------
    # Each fact's numbers are evidence items it cites, appended to the stake's evidence, so the number
    # checker accepts any line written from a fact.

    def _facts(
        self,
        m: dict[str, Any],
        climax: dict[str, Any],
        contributions: dict[tuple[str, str], float],
        evidence: list[dict[str, Any]],
        rank_before: int,
        rank_after: int,
        moved: bool,
    ) -> list[dict[str, Any]]:
        mid, manager = climax["moment_id"], m["id"]
        prefix, seqs = f"{mid}_{manager}", [e["seq"] for e in self.moment_events]
        points: dict[str, float] = {}  # this manager's players in this moment, and their points with the multiplier
        for (player, _), value in contributions.items():
            points[player] = points.get(player, 0) + value
        involved = [p for p, value in points.items() if value]
        facts = [self._ownership(prefix, mid, p, contributions, evidence, seqs) for p in involved]
        facts.append(self._captain_swing(m, prefix, mid, points, evidence, seqs))
        if rank_before != rank_after and moved:
            facts.append({"id": f"f_{prefix}_rank_change", "kind": "rank_change", "rank_before": rank_before,
                          "rank_after": rank_after,
                          "evidence_ids": [f"ev_{prefix}_rank_before", f"ev_{prefix}_rank_after"]})  # fmt: skip
        shooters = dict.fromkeys(e["player"] for e in self.moment_events if e["type"] == "shot")
        facts += [self._expected_vs_actual(prefix, mid, p, evidence) for p in shooters if p in m["squad"]]
        if involved:
            facts.append(self._pass_quality(prefix, mid, climax, evidence))
        if self.shared:
            facts.append(self._elsewhere(prefix, manager, evidence))
        return [f for f in facts if f]

    def _ownership(
        self,
        prefix: str,
        mid: str,
        player: str,
        contributions: dict[tuple[str, str], float],
        evidence: list[dict[str, Any]],
        seqs: list[int],
    ) -> dict[str, Any] | None:
        """A differential is owned by this manager alone; a template by at least three of the five."""
        owners = self.owners[player]
        kind = "differential" if owners == 1 else "template" if owners >= 3 else None
        if kind is None:
            return None
        eid = f"ev_{mid}_{player}_owners"
        label = f"Managers in the league who own {self.name[player]}"
        evidence.append(self._computed(eid, label, owners, "count", "ownership", seqs))
        cited = [eid] + [f"ev_{prefix}_{p}_{rule}" for p, rule in contributions if p == player]
        return {"id": f"f_{prefix}_{kind}_{player}", "kind": kind, "player": player, "owners": owners,
                "evidence_ids": cited}  # fmt: skip

    def _captain_swing(
        self,
        m: dict[str, Any],
        prefix: str,
        mid: str,
        points: dict[str, float],
        evidence: list[dict[str, Any]],
        seqs: list[int],
    ) -> dict[str, Any] | None:
        """The captain's extra points over managers who own him without the armband."""
        captain = m["captain"]
        if captain not in self.side_of:  # he plays in another fixture: nothing here is his doing (#150)
            return None
        versus = [o["id"] for o in self.managers if o is not m and captain in o["squad"] and o["captain"] != captain]
        mult = self._mult(m, captain)
        extra = number(points.get(captain, 0) * (mult - 1) / mult)
        if not versus or extra <= 0:
            return None
        eid = f"ev_{prefix}_{captain}_captain_swing"
        label = f"Extra IP from captaining {self.name[captain]}"
        evidence.append(self._computed(eid, label, extra, "ip", "captain_swing", seqs))
        return {"id": f"f_{prefix}_captain_swing", "kind": "captain_swing", "player": captain, "extra_points": extra,
                "versus": versus, "evidence_ids": [eid]}  # fmt: skip

    def _expected_vs_actual(self, prefix: str, mid: str, player: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
        """Goal points expected from his xG so far against goal points scored, both before the captain (#87).

        Shot-on-target, long-odds and other awards are left out of both sides, so the two numbers measure
        finishing alone and compare directly."""
        goal = self.r["goal"]["points"]
        xg = number(self.player_xg[player])
        expected, actual = number(xg * goal), number(self.goals[player] * goal)
        shots, name = list(self.player_shots[player]), self.name[player]  # as of this moment, not later
        ids = [f"ev_{mid}_{player}_{key}" for key in ("xg_so_far", "expected_ip", "actual_ip")]
        evidence += [
            self._computed(ids[0], f"xG of {name}'s shots so far", xg, "xg", "player_xg_sum", shots),
            self._computed(ids[1], f"Goal IP expected from {name}'s xG", expected, "ip", "expected_goal_ip", shots),
            self._computed(ids[2], f"Goal IP scored by {name}", actual, "ip", "goal_ip", shots),
        ]
        return {"id": f"f_{prefix}_expected_vs_actual_{player}", "kind": "expected_vs_actual", "player": player,
                "xg": xg, "expected_points": expected, "actual_points": actual, "evidence_ids": ids}  # fmt: skip

    def _pass_quality(
        self, prefix: str, mid: str, climax: dict[str, Any], evidence: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """The pass that set up the moment's shot, when one did."""
        assist = self.assist if climax["type"] == "shot" else None
        if assist is None:
            return None
        at, distance, difficulty = (
            [assist["seq"]],
            number(assist["pass"]["distance_m"]),
            number(assist["pass"]["difficulty"]),
        )
        ids = [f"ev_{mid}_pass_distance", f"ev_{mid}_pass_difficulty"]
        evidence += [
            self._fact(ids[0], "Distance of the pass", distance, "m", at),
            self._fact(ids[1], "Difficulty of the pass, 0 easy to 1 hardest", difficulty, None, at),
        ]
        return {"id": f"f_{prefix}_pass_quality", "kind": "pass_quality", "player": assist["player"],
                "distance_m": distance, "difficulty": difficulty,
                "evidence_ids": ids}  # fmt: skip

    def _elsewhere(self, prefix: str, manager: str, evidence: list[dict[str, Any]]) -> dict[str, Any] | None:
        """The manager's points from the gameweek's other fixtures so far: the one number about them an agent
        may cite (#150). Nothing when there are none."""
        points = number(self.standings.elsewhere(self.fixture_id, manager))
        if not points:
            return None
        eid = f"ev_{prefix}_elsewhere"
        evidence.append(self._computed(eid, "Impact points from the other matches so far", points, "ip", "elsewhere"))
        return {"id": f"f_{prefix}_elsewhere", "kind": "elsewhere", "points": points, "evidence_ids": [eid]}

    # --- views ----------------------------------------------------------------------------------------

    def table(self) -> list[dict[str, Any]]:
        return self.standings.table()

    def match_stats(self) -> list[dict[str, Any]]:
        """Plain match statistics as evidence, for stat cards and analyst explanations (rule 4.1 iv)."""
        window = [(s, a) for t, s, a in self.ball if t >= self.t - CONTROL_WINDOW_MS]
        total_ms = sum(self.possession_ms.values()) or 1
        stats = []
        for side in ("home", "away"):
            completion = round(self.completed[side] / self.passes[side], 2) if self.passes[side] else 0
            control = round(sum(s == side and a > 2 / 3 for s, a in window) / len(window), 2) if window else 0
            rows = [
                ("shots", "Shots", self.shots[side], "count", "shots"),
                ("xg", "Total xG", number(self.xg[side]), "xg", "xg_sum"),
                ("possession", "Possession", round(self.possession_ms[side] / total_ms, 2), "ratio", "possession_time"),
                ("pass_completion", "Pass completion", completion, "ratio", "pass_completion"),
                ("control", "Control: last 5 minutes in the final third", control, "ratio", "control_index"),
            ]
            stats += [self._computed(f"ev_stats_{side}_{key}", f"{label}, {side}", number(value), unit, formula)
                      for key, label, value, unit, formula in rows]  # fmt: skip
        return stats
