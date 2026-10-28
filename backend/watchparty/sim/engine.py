"""Seeded synthetic match: filler play between scripted key moments (#7), 12 to 15 in a single match, or
at the start times a caller injects (a gameweek gives each fixture five or six, sim/gameweek.py, #94).

simulate(seed) builds the whole match up front, so the same seed always gives the same match;
play() then paces it out in match time. Coordinates follow contracts/: x from the home goal line (0)
to the away goal line (1). Internally, play is described by `a`, the attacking side's progress
towards the goal it attacks.
"""

import asyncio
import bisect
import heapq
import itertools
import math
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from watchparty.sim.clubs import Slot, slots, team

MINUTE = 60_000
HALF = 45 * MINUTE
FULL = 90 * MINUTE
FRAME_MS = 3_000  # about 10 frames per real second at 30x
MOMENT_SPACING = 6 * MINUTE  # scenarios last under a minute, so climaxes stay 5+ minutes apart
LENGTH_M, WIDTH_M = 105.0, 68.0
SPRINT_KM_H = 30.0

HALF_TIME_GAP = (43 * MINUTE, HALF + 90_000)  # a moment (under a minute, plus a goal restart) ends before half time
MIN_START_GAP = 2 * MINUTE  # an injected moment ends, goal restart included, before the next one starts
Clubs = tuple[dict[str, Any], dict[str, Any]]

SCENARIOS = {"build_up": 3, "counterattack": 3, "high_press": 2, "set_piece": 3, "low_block": 1.5,
             "keeper_save_run": 1.5}  # fmt: skip


@dataclass
class Match:
    meta: dict[str, Any]
    events: list[dict[str, Any]]
    frames: list[dict[str, Any]]


def other(side: str) -> str:
    return "away" if side == "home" else "home"


def to_x(side: str, a: float) -> float:
    return a if side == "home" else 1 - a


def xg(a: float, y: float, head: bool = False) -> float:
    """Chance quality from distance and the angle the goal mouth presents (a simple logistic model)."""
    dx, dy = (1 - a) * LENGTH_M, (y - 0.5) * WIDTH_M
    dist = math.hypot(dx, dy)
    angle = math.atan2(7.32 * dx, dx * dx + dy * dy - 3.66**2) % math.pi
    z = -1.1 - 0.11 * dist + 1.5 * angle - (0.6 if head else 0)
    return round(max(0.01, min(0.95, 1 / (1 + math.exp(-z)))), 2)


def _clip(v: float, lo: float = 0.01, hi: float = 0.99) -> float:
    return max(lo, min(hi, v))


@dataclass
class _Sim:
    rng: random.Random
    t: int = 0
    side: str = "home"  # in possession
    a: float = 0.5
    y: float = 0.5
    holder: str = "p_home_9"
    score: dict[str, int] = field(default_factory=lambda: {"home": 0, "away": 0})
    events: list[dict[str, Any]] = field(default_factory=list)
    ball: list[tuple[int, float, float]] = field(default_factory=list)  # (t, x, y) keyframes
    possession: list[tuple[int, str]] = field(default_factory=list)
    holders: list[tuple[int, str]] = field(default_factory=list)
    yellow: set[str] = field(default_factory=set)
    off: dict[str, int] = field(default_factory=dict)  # sent-off player -> time
    moment: dict[str, str] | None = None
    after_moment: list[Callable[[], None]] = field(default_factory=list)
    lineups: dict[str, dict[str, Slot]] = field(default_factory=lambda: {s: slots(s) for s in ("home", "away")})

    # --- state -------------------------------------------------------------------------------------

    def on_pitch(self, side: str) -> dict[str, tuple[float, float]]:
        return {p: s for p, s in self.lineups[side].items() if p not in self.off}

    def pick(self, side: str, a: float, y: float, keeper: bool = False) -> str:
        """A player of `side` whose slot is near (a, y) in that side's attacking frame."""
        players = {p: s for p, s in self.on_pitch(side).items() if keeper or not p.endswith("_1")}
        weights = [1 / (0.01 + (sa - a) ** 2 + (sy - y) ** 2) for sa, sy in players.values()]
        return self.rng.choices(list(players), weights)[0]

    def keeper(self, side: str) -> str:
        return f"p_{side}_1"

    def move(self, a: float, y: float, holder: str, travel_ms: int = 0) -> None:
        self.a, self.y, self.holder = _clip(a), _clip(y), holder
        self.t += travel_ms
        self.ball.append((self.t, to_x(self.side, self.a), self.y))
        self.holders.append((self.t, holder))

    def turn_over(self, holder: str) -> None:
        self.side, self.a = other(self.side), 1 - self.a
        self.possession.append((self.t, self.side))
        self.move(self.a, self.y, holder)

    def tick(self, lo: float = 1.5, hi: float = 4.5) -> None:
        self.t += int(self.rng.uniform(lo, hi) * 1000)

    def emit(self, kind: str, side: str, player: str | None, **extra: Any) -> dict[str, Any]:
        a = self.a if side == self.side else 1 - self.a
        event: dict[str, Any] = {"seq": len(self.events), "t": self.t, "type": kind, "team": side}
        if player:
            event["player"] = player
        event |= {"x": round(to_x(side, a), 4), "y": round(self.y, 4)} | extra
        if self.moment:
            event |= self.moment
        self.events.append(event)
        return event

    def then(self, follow_up: Callable[[], None]) -> None:
        """Restarts and follow-ups happen after a key moment's climax, never inside it."""
        if self.moment:
            self.after_moment.append(follow_up)
        else:
            follow_up()

    # --- actions -----------------------------------------------------------------------------------

    def pass_(self, a: float, y: float, success: bool | None = None, pressure: float = 0.0) -> bool:
        a, y = _clip(a), _clip(y)
        dist = math.hypot((a - self.a) * LENGTH_M, (y - self.y) * WIDTH_M)
        difficulty = round(min(1.0, 0.12 + dist / 70 + 0.3 * pressure + 0.25 * max(0.0, a - 0.7)), 2)
        if success is None:
            success = self.rng.random() < 1.0 - 0.45 * difficulty
        speed = round(self.rng.uniform(35, 55) + dist * 0.8, 1)
        receiver = self.pick(self.side, a, y)
        end_x = round(to_x(self.side, a), 4)
        self.emit("pass", self.side, self.holder, end_x=end_x, end_y=round(y, 4),
                  **({"receiver": receiver} if success else {}), outcome="success" if success else "fail",
                  **{"pass": {"distance_m": round(dist, 1), "difficulty": difficulty,
                              "progressive": a - self.a >= 0.1, "ball_speed_km_h": speed}})  # fmt: skip
        travel = int(dist / (speed / 3.6) * 1000) + 200
        if success:
            self.move(a, y, receiver, travel)
        else:
            self.move(a, y, self.holder, travel)
            interceptor = self.pick(other(self.side), 1 - a, y)
            self.turn_over(interceptor)
            self.emit("interception", self.side, interceptor, outcome="success")
        return success

    def carry(self, da: float, sprint: bool = False) -> None:
        start = (self.a, self.y)
        extra = {"player_speed_km_h": round(self.rng.uniform(SPRINT_KM_H, 35.0), 1)} if sprint else {}
        self.emit("carry", self.side, self.holder, **extra)
        self.move(self.a + da, self.y + self.rng.gauss(0, 0.05), self.holder)
        metres = math.hypot((self.a - start[0]) * LENGTH_M, (self.y - start[1]) * WIDTH_M)
        self.t += int(metres / ((extra.get("player_speed_km_h") or 18) / 3.6) * 1000) + 300

    def win_ball(self, kind: str, pressure: bool = False) -> None:
        """The defending side wins the ball where it is."""
        defender = self.pick(other(self.side), 1 - self.a, self.y)
        if pressure:
            self.emit("pressure", other(self.side), defender)
            self.t += 800
        self.turn_over(defender)
        self.emit(kind, self.side, defender, outcome="success")

    def shot(self, head: bool = False, outcome: str | None = None, boost: float = 1.0) -> str:
        chance = xg(self.a, self.y, head)
        if outcome is None:
            r = self.rng.random()
            p_goal = min(0.9, chance * boost)
            outcome = ("goal" if r < p_goal else "saved" if r < p_goal + 0.33 else "blocked" if r < p_goal + 0.55
                       else "woodwork" if r < p_goal + 0.58 else "off_target")  # fmt: skip
        keeper = self.keeper(other(self.side))
        speed = round(self.rng.uniform(55, 75) if head else self.rng.uniform(80, 120), 1)
        extra = {"opponent": keeper} if outcome in ("goal", "saved") else {}
        self.emit("shot", self.side, self.holder, outcome=outcome, **extra,
                  shot={"xg": chance, "speed_km_h": speed, "body_part": "head" if head else "right_foot"})  # fmt: skip
        self.t += 600
        self.then(lambda: self.restart(outcome))
        return outcome

    def restart(self, outcome: str) -> None:
        attacker = self.side
        if outcome == "goal":
            self.score[attacker] += 1
            self.side = other(attacker)
            self.possession.append((self.t, self.side))
            self.t += 25_000
            self.move(0.5, 0.5, self.pick(self.side, 0.66, 0.5))
            self.emit("kickoff", self.side, self.holder)
        elif outcome == "blocked" and self.rng.random() < 0.5:
            self.t += 8_000
            self.move(0.99, self.rng.choice([0.01, 0.99]), self.pick(attacker, 0.62, 0.5))
            self.emit("corner", attacker, self.holder)
        else:  # saved, off target, woodwork, or a blocked ball cleared: the defending keeper restarts
            self.turn_over(self.keeper(other(attacker)))
            self.move(0.06, 0.5, self.holder)
            self.t += 6_000

    def foul(self, card: str | None = None) -> None:
        victim, offender = self.holder, self.pick(other(self.side), 1 - self.a, self.y)
        self.emit("foul", other(self.side), offender, opponent=victim)
        self.t += 1_500
        if card:
            if card == "yellow" and offender in self.yellow:
                card = "red"
            self.yellow.add(offender)
            self.emit("card", other(self.side), offender, outcome=card)
            if card == "red":
                self.off[offender] = self.t

        def free_kick() -> None:
            self.t += 10_000
            self.emit("free_kick", self.side, self.holder)

        self.then(free_kick)

    # --- play --------------------------------------------------------------------------------------

    def filler(self, until: int) -> None:
        while self.t < until:
            r = self.rng.random()
            if self.a > 0.72 and r < 0.005 + 0.06 * (self.a - 0.72) / 0.28:
                self.shot()
            elif r < 0.02:
                self.foul(card="yellow" if self.rng.random() < 0.1 else None)
            elif r < 0.07:
                self.win_ball(self.rng.choice(["tackle", "interception"]), pressure=self.rng.random() < 0.5)
            elif r < 0.17:
                self.carry(self.rng.uniform(0.02, 0.08), sprint=self.rng.random() < 0.15)
            else:
                ahead = self.rng.gauss(0.05 if self.a < 0.7 else -0.02, 0.12)
                self.pass_(self.a + ahead, self.y + self.rng.gauss(0, 0.22), pressure=self.rng.random() * 0.5)
            self.tick()

    def key_moment(self, moment_id: str, scenario: str, side: str) -> None:
        if side != self.side:
            self.turn_over(self.pick(side, 0.4, 0.5))
            self.tick(1, 2)
        self.moment = {"moment_id": moment_id, "scenario": scenario}
        getattr(self, f"_{scenario}")()
        self.events[-1]["climax"] = True
        self.moment = None
        for follow_up in self.after_moment:
            follow_up()
        self.after_moment.clear()

    # Each scenario ends with its climax event and lasts well under a minute.

    def _build_up(self) -> None:
        self.move(0.3, 0.5, self.holder)
        while self.a < 0.78:
            self.pass_(self.a + self.rng.uniform(0.06, 0.14), self.rng.uniform(0.15, 0.85), success=True)
            self.tick(1, 2.5)
        self.pass_(0.93, self.rng.choice([0.12, 0.88]), success=True)
        self.tick(1, 2)
        self.pass_(0.86, self.rng.uniform(0.4, 0.6), success=True)
        self.shot()

    def _counterattack(self) -> None:
        self.move(0.35, self.rng.uniform(0.3, 0.7), self.holder)
        self.side, self.a = other(self.side), 1 - self.a  # the opponent has the ball deep in our half
        self.win_ball("interception")
        self.pass_(self.a + 0.25, self.rng.uniform(0.2, 0.8), success=True)
        self.carry(0.12, sprint=True)
        self.pass_(0.88, self.rng.uniform(0.35, 0.65), success=True)
        self.shot(boost=1.2)

    def _high_press(self) -> None:
        self.side = other(self.side)
        self.move(0.2, self.rng.uniform(0.2, 0.8), self.pick(self.side, 0.2, 0.5))
        self.win_ball("tackle", pressure=True)
        self.tick(0.5, 1.5)
        self.pass_(0.85, self.rng.uniform(0.35, 0.65), success=True)
        self.shot()

    def _set_piece(self) -> None:
        self.move(0.74, self.rng.uniform(0.2, 0.8), self.holder)
        if self.rng.random() < 0.4:
            self.foul(card="red" if self.rng.random() < 0.15 else "yellow")
            return
        self.t += 8_000
        self.move(0.99, self.rng.choice([0.01, 0.99]), self.holder)
        self.emit("corner", self.side, self.holder)
        self.pass_(0.93, self.rng.uniform(0.38, 0.62), success=True)
        self.shot(head=True)

    def _low_block(self) -> None:
        self.move(0.62, 0.5, self.holder)
        for _ in range(self.rng.randint(5, 7)):
            self.pass_(self.rng.uniform(0.6, 0.7), self.rng.uniform(0.1, 0.9), success=True)
            self.tick(1, 2)
        self.carry(0.04)
        self.shot()

    def _keeper_save_run(self) -> None:
        self.move(0.84, self.rng.uniform(0.3, 0.7), self.holder)
        self.shot(outcome="saved")
        self.after_moment.clear()  # the rebound is part of the moment
        self.tick(1, 2)
        self.move(0.9, self.rng.uniform(0.35, 0.65), self.pick(self.side, 0.85, 0.5))
        self.shot(outcome="saved")

    def _late_equaliser(self) -> None:
        self.move(0.7, self.rng.uniform(0.3, 0.7), self.holder)
        self.pass_(0.89, self.rng.uniform(0.4, 0.6), success=True)
        self.shot(outcome="goal")


def _schedule(rng: random.Random, full_time: int) -> list[int]:
    """12 to 15 moment start times, MOMENT_SPACING apart, kept clear of half time and full time.

    Never more moments than fit: with 2 minutes of stoppage, 15 do not, and a negative slack started the
    first one before 3:00 and ran the last into the final whistle (#31). Seeds where 15 fit are unchanged.
    """
    gap = HALF_TIME_GAP
    span = full_time - 2 * MINUTE - 3 * MINUTE - (gap[1] - gap[0])
    n = rng.randint(12, min(15, 1 + span // MOMENT_SPACING))
    slack = span - (n - 1) * MOMENT_SPACING  # never negative: n fits by construction (test_sim)
    starts = []
    for i, offset in enumerate(sorted(rng.uniform(0, slack) for _ in range(n))):
        v = int(3 * MINUTE + offset + i * MOMENT_SPACING)
        starts.append(v if v < gap[0] else v + gap[1] - gap[0])
    return starts


def _choose(sim: _Sim, rng: random.Random, start: int, hot_keeper: str) -> tuple[str, str]:
    diff = sim.score["home"] - sim.score["away"]
    if start >= 80 * MINUTE and abs(diff) == 1 and rng.random() < 0.6:
        return "late_equaliser", "home" if diff < 0 else "away"
    scenario = rng.choices(list(SCENARIOS), list(SCENARIOS.values()))[0]
    if scenario == "keeper_save_run":
        return scenario, other(hot_keeper)
    return scenario, "home" if rng.random() < 0.55 else "away"


def _interpolate(points: list[tuple[int, float, float]], times: list[int], t: int) -> tuple[float, float]:
    i = bisect.bisect_right(times, t)
    if i == 0:
        return points[0][1], points[0][2]
    if i == len(points):
        return points[-1][1], points[-1][2]
    (t0, x0, y0), (t1, x1, y1) = points[i - 1], points[i]
    f = (t - t0) / (t1 - t0) if t1 > t0 else 1.0
    return x0 + (x1 - x0) * f, y0 + (y1 - y0) * f


def _latest(timeline: list[tuple[int, str]], times: list[int], t: int) -> str:
    return timeline[max(0, bisect.bisect_right(times, t) - 1)][1]


def _frames(sim: _Sim, end: int) -> list[dict[str, Any]]:
    points = sorted(sim.ball)
    times = [p[0] for p in points]
    possession_times, holder_times = [p[0] for p in sim.possession], [h[0] for h in sim.holders]
    frames = []
    for t in range(0, end + 1, FRAME_MS):
        bx, by = _interpolate(points, times, t)
        side, holder = _latest(sim.possession, possession_times, t), _latest(sim.holders, holder_times, t)
        players = []
        for s in ("home", "away"):
            ball_a = to_x(s, bx)  # the ball's progress in this side's attacking frame
            for i, (p, (sa, sy)) in enumerate(sim.lineups[s].items()):
                if sim.off.get(p, end + 1) <= t:
                    continue
                if p == holder:
                    x, y = bx, by
                else:
                    shift = (ball_a - 0.45) * (0.1 if p.endswith("_1") else 0.45) - (0.06 if s != side else 0)
                    a = _clip(sa + shift + 0.012 * math.sin(t / 4000 + i))
                    x, y = to_x(s, a), _clip(sy + (by - 0.5) * 0.25 + 0.01 * math.cos(t / 5000 + i))
                players.append({"id": p, "x": round(x, 4), "y": round(y, 4)})
        frames.append({"t": t, "possession": side, "ball": {"x": round(bx, 4), "y": round(by, 4)}, "players": players})
    return frames


def _check_clubs(clubs: Clubs) -> None:
    """The simulator takes the first player as the keeper and the ninth as the kickoff taker (sim/clubs.py), and
    frames and ids assume eleven a side."""
    if len(clubs) != 2:
        raise ValueError("clubs must be two clubs, home and away")
    if clubs[0]["id"] == clubs[1]["id"]:
        raise ValueError("a club cannot play itself")
    for club in clubs:
        players = club["players"]
        if len(players) != 11:
            raise ValueError(f"{club['name']} must have eleven players")
        if players[0][2] != "GK" or [p[2] for p in players].count("GK") != 1:
            raise ValueError(f"{club['name']} must have one keeper, listed first")


def _check_injection(clubs: Clubs | None, starts: list[int] | None, full_time: int | None) -> None:
    """Injected starts and full time must leave a match the simulator can play: each moment ends before the next
    starts, none runs into half time or full time, and half time is blown. How many moments, and how far apart
    beyond that, is the caller's choice; _schedule's 12 to 15, six minutes apart, bind only the single match."""
    if clubs is not None:
        _check_clubs(clubs)
    if full_time is not None and type(full_time) is not int:
        raise ValueError("full_time must be an integer number of milliseconds")
    if full_time is not None and not FULL + 2 * MINUTE <= full_time <= FULL + 5 * MINUTE:
        raise ValueError("full_time must be between 92:00 and 95:00, as the drawn stoppage is")
    if starts is None:
        return
    if any(type(t) is not int for t in starts):
        raise ValueError("starts must be integer numbers of milliseconds")
    whistle = full_time if full_time is not None else FULL + 2 * MINUTE  # the earliest a drawn whistle can be
    if any(b - a < MIN_START_GAP for a, b in itertools.pairwise(starts)):
        raise ValueError(f"starts must rise by at least {MIN_START_GAP // 1000} s, so each moment ends first")
    if not starts or starts[0] < 3 * MINUTE or starts[-1] > whistle - 2 * MINUTE:
        raise ValueError("starts must run from 3:00 to 2 minutes before full time")
    if any(HALF_TIME_GAP[0] <= t < HALF_TIME_GAP[1] for t in starts):
        raise ValueError("no moment may start between 43:00 and 46:30, around half time")
    if starts[-1] < HALF:
        raise ValueError("at least one moment must start after half time: half time is blown on the way to it")


def simulate(
    seed: int,
    speed: float = 30,
    *,
    clubs: Clubs | None = None,
    starts: list[int] | None = None,
    full_time: int | None = None,
) -> Match:
    """The whole match for `seed`. A gameweek (#94) injects the two clubs (home, away), the moment start times
    and one full time for every fixture; left out, each is today's, and the random draws are the same draws in
    the same order, so every seed's single match is unchanged."""
    _check_injection(clubs, starts, full_time)
    rng = random.Random(seed)  # noqa: S311  reproducible simulation, not security; tokens use `secrets`
    sim = _Sim(rng)
    if clubs is not None:
        sim.lineups = {side: slots(side, club) for side, club in zip(("home", "away"), clubs, strict=True)}
    if full_time is None:
        full_time = FULL + rng.randint(2, 5) * MINUTE
    hot_keeper = rng.choice(["home", "away"])
    sim.possession.append((0, "home"))
    sim.move(0.5, 0.5, "p_home_9")
    sim.emit("kickoff", "home", "p_home_9")
    sim.tick()
    for n, start in enumerate(_schedule(rng, full_time) if starts is None else starts, start=1):
        if sim.t < HALF <= start:
            sim.filler(HALF)  # the referee blows at the first break in play after 45:00
            sim.emit("half_time", sim.side, None)
            sim.side = "away"
            sim.possession.append((sim.t, "away"))
            # The usual taker, unless sent off: drawing only then keeps every other match's output the same.
            taker = "p_away_9" if "p_away_9" not in sim.off else sim.pick("away", 0.66, 0.5)
            sim.move(0.5, 0.5, taker)
            sim.emit("kickoff", "away", taker)
            sim.tick()
        sim.filler(start)
        scenario, side = _choose(sim, rng, start, hot_keeper)
        sim.key_moment(f"m_{n:02d}", scenario, side)
    sim.filler(full_time)
    sim.t = max(sim.t, full_time)
    sim.emit("full_time", sim.side, None)
    home, away = clubs if clubs is not None else (None, None)
    meta = {"match_id": f"match_{seed}", "seed": seed, "speed": speed, "playback_delay_ms": 5000,
            "home": team("home", home), "away": team("away", away)}  # fmt: skip
    return Match(meta, sim.events, _frames(sim, sim.t))


def messages(match: Match) -> Iterator[dict[str, Any]]:
    """Meta, then events and frames in match-time order, as WsMessage envelopes."""
    yield {"v": 1, "type": "meta", "data": match.meta}
    events: Iterator[dict[str, Any]] = ({"v": 1, "type": "event", "data": e} for e in match.events)
    frames: Iterator[dict[str, Any]] = ({"v": 1, "type": "frame", "data": f} for f in match.frames)
    yield from heapq.merge(events, frames, key=lambda m: m["data"]["t"])


async def play(
    match: Match, speed: float, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
) -> AsyncIterator[dict[str, Any]]:
    """The messages, paced: one match second takes 1/speed real seconds."""
    now = 0
    for message in messages(match):
        t = message["data"].get("t", now)
        if t > now:
            await sleep((t - now) / 1000 / speed)
            now = t
        yield message
