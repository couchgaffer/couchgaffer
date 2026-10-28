"""The per-moment pipeline as a Microsoft Agent Framework workflow.

    start ─fan-out─▶ manager_mgr_1 … manager_mgr_5 ─fan-in─▶ scorer ─close call─▶ host
                                                                   └─clear winner─▶ decided

Every agent call has a deadline; a miss, an error, an invalid answer or a simulated failure falls back
to code (a plain-stat bid, or the scorer's pick), so the match never waits on a model. A manager may decline
a moment worth 0 IP to it (#88): a declined bid goes to no phone, is never scored and never reaches the host.

Each key moment runs its own workflow from its arrival, so its bids get their full time even while an earlier
moment is still being decided (#149). The scorer, the host and the ledger are the arbiter's turn, taken one moment
at a time in arrival order: the scorer waits for the earlier moment's turn to end (`turn`, given by the session),
and the ledger is recorded in the last step, so every moment is ranked on every earlier decision. A manager agent
reads the ledger (its airtime share and close calls lost) when its bid starts, so a moment that overlaps an earlier
one may be argued on a ledger without that decision; the scorer still ranks it on the full ledger.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from agent_framework import Executor, Workflow, WorkflowBuilder, WorkflowContext, handler
from opentelemetry import trace

from watchparty.agents.base import (
    AGENT_FIELDS,
    ANGLE_KINDS,
    HostAgent,
    ManagerAgent,
    fact_for_agent,
    pitch_source,
    plain_bid,
)
from watchparty.agents.foundry import one_line
from watchparty.agents.host import agreed_reason, true_factors
from watchparty.agents.scorer import Ledger, angle_kind, rank
from watchparty.telemetry import mark_failed
from watchparty.telemetry import span as traced

tracer = trace.get_tracer("watchparty.workflow")
log = logging.getLogger(__name__)


def fell_back(what: str, exc: Exception, span: trace.Span, reason: str | None = None) -> None:
    """An agent failure the match recovered from, still visible: a warning in the logs and an error on its
    trace span. A Foundry 401 or 429 must not look like the fallback working as designed (#24). `reason`, when
    given, replaces the exception in the log line with a fuller account of it (#84). The span gets the
    exception's type only (telemetry.mark_failed)."""
    log.warning("%s fell back to code: %s", what, reason or f"{type(exc).__name__} {one_line(str(exc))}")
    mark_failed(span, exc)


class InvalidAnswer(ValueError):
    """An agent answered, but not with something the match can use."""


def why(exc: Exception, elapsed_s: float) -> str:
    """A bid's or the host's fallback, for the log line: what failed and after how long (#111, as #84 did for
    summaries)."""
    if isinstance(exc, TimeoutError):
        return f"timeout after {elapsed_s:.1f} s"
    return f"{type(exc).__name__} after {elapsed_s:.1f} s: {one_line(str(exc))}"


def failed(what: str, exc: Exception, span: trace.Span, start: float) -> None:
    """An agent call that failed: its kind on the span, then the log line and the error status."""
    kind = "timeout" if isinstance(exc, TimeoutError) else "error"
    span.set_attribute("fallback", "invalid answer" if isinstance(exc, InvalidAnswer) else kind)
    fell_back(what, exc, span, why(exc, time.perf_counter() - start))


def skip_reason(no_agent: bool, chaos: bool, no_time: bool) -> str:
    """Why an agent is not asked at all, or "" when it is. Not an error: the match is working as designed."""
    return "no agent" if no_agent else "chaos" if chaos else "no time left" if no_time else ""


@dataclass(frozen=True)
class MomentInput:
    moment_id: str
    t: int
    summary: str
    stakes: dict[str, dict[str, Any]]  # manager -> stake
    deadline: float  # time.monotonic() by which the group decision must be made: before screens show it (#26)
    chaos: bool
    names: list[str]  # every club, player and manager in this match: the only names an agent may use
    players: dict[str, str]  # player id -> name
    session_id: str = ""  # for the trace spans (#111)
    knocked_out: str = ""  # one manager whose agent is skipped this moment; the others and the host run (#90)
    # A gameweek's fixtures as the moment is decided (#94): clubs, goals, player ids, and whether it is this
    # moment's. Empty for a single match.
    gameweek: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ManagerResult:
    moment: MomentInput
    manager: str
    bid: dict[str, Any] | None


@dataclass(frozen=True)
class Scored:
    moment: MomentInput
    bids: dict[str, dict[str, Any]]
    ranked: list[dict[str, Any]]
    close: bool


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def answer_problem(answer: Any, stake: dict[str, Any]) -> str | None:
    """Why the workflow will not use an agent's answer, or None. The manager agent checks the angle and the
    decline before it answers; this holds for any agent, so a foreign angle or a decline on points never reaches a
    screen (#88). The text checks, the room's line among them (#138), run only inside the manager agent."""
    if not isinstance(answer, dict):
        return "not an answer"
    if answer.get("status") == "declined":
        return f"declined a moment worth {stake['delta_points']:+g} IP" if stake["delta_points"] else None
    if not all(k in answer for k in AGENT_FIELDS):
        return "missing fields"
    angle = answer.get("angle")
    if angle and angle not in {f["id"] for f in stake.get("facts") or []}:
        return f"angle {angle} is not one of this manager's facts"
    return None


class Start(Executor):
    @handler
    async def fan_out(self, moment: MomentInput, ctx: WorkflowContext[MomentInput]) -> None:
        await ctx.send_message(moment)


class ManagerStep(Executor):
    def __init__(self, manager: dict[str, Any], agent: ManagerAgent | None, deadline_s: float,
                 publish: Callable[[dict[str, Any]], None], ledger: Ledger | None = None,
                 display_names: dict[str, str] | None = None) -> None:  # fmt: skip
        super().__init__(id=f"manager_{manager['id']}")
        self.manager, self.agent, self.deadline_s, self.publish = manager, agent, deadline_s, publish
        self.ledger, self.display_names = ledger or Ledger(), display_names or {}

    def context(self, moment: MomentInput) -> dict[str, Any]:
        """What the agent knows besides the stake: the moment, its manager, the names, and the ledger (#88)."""
        me = self.manager["id"]
        ledger = {"airtime_share": self.ledger.share(me), "close_calls_lost": self.ledger.overruled[me]}
        context: dict[str, Any] = {"moment": moment.summary, "manager": self.manager, "names": moment.names,
                                   "squad": [moment.players[p] for p in self.manager["squad"]],
                                   "captain": moment.players[self.manager["captain"]], "players": moment.players,
                                   "managers": self.display_names, "ledger": ledger}  # fmt: skip
        if moment.gameweek:  # every score, and where this manager's players are playing (#94)
            context["gameweek"] = [
                {"match": f"{f['home']} {f['home_goals']}, {f['away']} {f['away_goals']}",
                 "this_moment": f["this_moment"],
                 "your_players": [moment.players[p] for p in self.manager["squad"] if p in f["players"]]}
                for f in moment.gameweek
            ]  # fmt: skip
        return context

    async def _ask(
        self, stake: dict[str, Any], moment: MomentInput, span: trace.Span, start: float
    ) -> dict[str, Any] | None:
        left = min(self.deadline_s, moment.deadline - time.monotonic())
        chaos = moment.chaos or moment.knocked_out == self.manager["id"]
        skipped = skip_reason(self.agent is None, chaos, left <= 0)
        if skipped or self.agent is None:
            span.set_attribute("fallback", skipped)
            return None
        what = f"manager {self.manager['id']}"
        try:
            answer = await asyncio.wait_for(self.agent.bid(stake, self.context(moment)), left)
        except Exception as exc:  # timeout, model or network error: fall back, never stall the match
            failed(what, exc, span, start)
            return None
        if problem := answer_problem(answer, stake):
            failed(what, InvalidAnswer(problem), span, start)
            return None
        return answer

    def _bid(self, stake: dict[str, Any], answer: dict[str, Any] | None) -> dict[str, Any]:
        if answer is None:
            return plain_bid(stake, self.manager)
        bid = {"moment_id": stake["moment_id"], "manager": self.manager["id"], "style": self.manager["style"],
               "lang": self.manager["lang"]}  # fmt: skip
        if answer.get("status") != "declined":
            bid |= {k: answer[k] for k in AGENT_FIELDS} | {"status": answer.get("status", "ok")}
            bid |= {"angle": answer["angle"]} if answer.get("angle") else {}
            # code's line may stand in for a pitch that failed its checks; the cards say who wrote it (#138)
            bid |= {"pitch_source": answer["pitch_source"]} if answer.get("pitch_source") in ("manager", "code") else {}
        else:
            bid["status"] = "declined"
        return bid | ({"model": answer["model"]} if answer.get("model") else {})

    @handler
    async def bid(self, moment: MomentInput, ctx: WorkflowContext[ManagerResult]) -> None:
        stake = moment.stakes.get(self.manager["id"])
        if not stake or not stake["above_threshold"]:
            await ctx.send_message(ManagerResult(moment, self.manager["id"], None))
            return
        start = time.perf_counter()
        ids = {"manager": self.manager["id"], "session_id": moment.session_id, "moment_id": moment.moment_id}
        with traced(tracer, "manager_bid", ids) as span:
            bid = self._bid(stake, await self._ask(stake, moment, span, start))
            bid["latency_ms"] = _elapsed_ms(start)
            if stake.get("fixture_id"):  # a gameweek's (#94)
                bid["fixture_id"] = stake["fixture_id"]
            span.set_attributes({"status": bid["status"], "latency_ms": bid["latency_ms"]})
        self.publish(bid)  # the phone gets its explanation now, not after the host decides; a decline, nothing
        if bid["status"] == "declined":  # and nothing for the scorer or the host
            log.info("manager %s declined %s: worth 0 IP to it", self.manager["id"], stake["moment_id"])
            await ctx.send_message(ManagerResult(moment, self.manager["id"], None))
            return
        await ctx.send_message(ManagerResult(moment, self.manager["id"], bid))


async def no_wait(moment_id: str) -> None:
    """A workflow that decides one moment at a time has no turn to wait for."""


class ScoreStep(Executor):
    def __init__(self, ledger: Ledger, turn: Callable[[str], Awaitable[None]] = no_wait) -> None:
        super().__init__(id="scorer")
        self.ledger, self.turn = ledger, turn

    @handler
    async def score(self, results: list[ManagerResult], ctx: WorkflowContext[Scored, dict[str, Any] | None]) -> None:
        bids = {r.manager: r.bid for r in results if r.bid}  # a declined bid arrives as None (ManagerStep.bid)
        moment = results[0].moment
        # The arbiter's turn (#149). The earlier moment arrived first and has the earlier deadline, so this wait
        # ends by then. A callable, not a future on the message: the workflow copies every message it passes on.
        await self.turn(moment.moment_id)
        if not bids:
            await ctx.yield_output(None)  # nobody at stake: the plain stat card stays up
            return
        ranked, close = rank(bids, moment.stakes, self.ledger)
        await ctx.send_message(Scored(moment, bids, ranked, close))


def contenders(scored: Scored, chosen: str) -> list[dict[str, Any]]:
    """The two pitches a close call puts side by side (#89): the scorer's pick and the host's, or the scorer's
    first two when the host agreed with it. In the scorer's order."""
    order = [r["manager"] for r in scored.ranked]
    rival = order[1] if chosen == order[0] else chosen
    pair = [m for m in order if m in (order[0], rival)]
    return [{"manager": m, "pitch": scored.bids[m]["pitch_to_host"]}
            | ({"angle": scored.bids[m]["angle"]} if scored.bids[m].get("angle") else {}) for m in pair]  # fmt: skip


def decision(scored: Scored, chosen: str, source: str, latency_ms: int, reason: str | None = None,
             because: str | None = None) -> dict[str, Any]:  # fmt: skip
    rows = [r | {"pitch": bid["pitch_to_host"], "status": bid["status"], "latency_ms": bid["latency_ms"],
                 "pitch_source": pitch_source(bid)}
            for r in scored.ranked if (bid := scored.bids[r["manager"]])]  # fmt: skip
    out: dict[str, Any] = {"moment_id": scored.moment.moment_id, "t": scored.moment.t, "chosen_manager": chosen,
                           "source": source, "close_call": scored.close, "scores": rows,
                           "latency_ms": latency_ms}  # fmt: skip
    if reason:
        out["reason"] = reason
    if because:
        out |= {"because": because, "contenders": contenders(scored, chosen)}
    return out


def decision_problems(d: dict[str, Any], stakes: dict[str, dict[str, Any]]) -> list[str]:
    """What the host-decision schema cannot say (#86, #88): source and close_call agree, the winner was scored
    and, on a host call, is a contender, and a fact-kind reason is the kind of the winner's angle."""
    problems = []
    if (d["source"] == "scorer") == d["close_call"]:
        problems.append(f"source {d['source']} with close_call {d['close_call']}")
    rows = {r["manager"]: r for r in d["scores"]}
    if d["chosen_manager"] not in rows:
        problems.append("chosen_manager was not scored")
    if d["source"] == "host" and not all(d.get(k) for k in ("reason", "because", "contenders")):
        problems.append("a host decision needs reason, because and contenders")
    pair = [c["manager"] for c in d.get("contenders") or []]
    if pair and (len(set(pair)) != 2 or d["chosen_manager"] not in pair):
        problems.append(f"contenders {pair} must be two managers, one of them {d['chosen_manager']}")
    winner = rows.get(d["chosen_manager"], {})
    kind = angle_kind(winner, stakes.get(d["chosen_manager"], {}))
    if d.get("because") in ANGLE_KINDS and kind != d["because"]:
        problems.append(f"because {d['because']} but the winner's angle is {kind}")
    return problems


def recorded(ledger: Ledger, scored: Scored, result: dict[str, Any]) -> dict[str, Any]:
    """The decision, in the ledger before it leaves the workflow: the arbiter's turn ends in one place (#149)."""
    ledger.record(result["chosen_manager"], list(scored.bids), close=result["close_call"])
    return result


class HostStep(Executor):
    def __init__(self, agent: HostAgent | None, deadline_s: float, display_names: dict[str, str],
                 ledger: Ledger | None = None) -> None:  # fmt: skip
        super().__init__(id="host")
        self.agent, self.deadline_s, self.display_names = agent, deadline_s, display_names
        self.ledger = ledger or Ledger()

    def _candidates(self, scored: Scored) -> list[dict[str, Any]]:
        out = []
        for r in scored.ranked:
            stake, bid = scored.moment.stakes[r["manager"]], scored.bids[r["manager"]]
            fact = next((f for f in stake.get("facts") or [] if f["id"] == bid.get("angle")), None)
            angle = fact_for_agent(fact, scored.moment.players, self.display_names) if fact else None
            out.append({"manager": r["manager"], "name": self.display_names[r["manager"]],
                        "pitch": bid["pitch_to_host"], "impact_points": stake["delta_points"],
                        "airtime_share": r["airtime_share"], "overruled_count": r["overruled_count"],
                        "angle": angle})  # fmt: skip
        return out

    def _from_host(self, scored: Scored, answer: Any, candidates: list[dict[str, Any]], latency_ms: int
                   ) -> dict[str, Any] | None:  # fmt: skip
        """The host's decision, if every check holds: its pick was scored, its reason fits the screen, and its
        `because` is true of that pick (#88). Otherwise None, and the scorer's pick goes out."""
        if not isinstance(answer, dict) or answer.get("chosen_manager") not in scored.bids:
            return None
        if not 0 < len((answer.get("reason") or "").strip()) <= 140:
            return None
        if answer.get("because") not in true_factors(answer["chosen_manager"], candidates):
            return None
        chosen = next(c for c in candidates if c["manager"] == answer["chosen_manager"])
        reason = agreed_reason(answer["reason"], answer["because"], chosen)  # the words match the verified fact
        result = decision(scored, answer["chosen_manager"], "host", latency_ms, reason, answer["because"])
        return None if decision_problems(result, scored.moment.stakes) else result

    @handler
    async def decide(self, scored: Scored, ctx: WorkflowContext[None, dict[str, Any]]) -> None:
        start = time.perf_counter()
        pick = scored.ranked[0]["manager"]
        candidates = self._candidates(scored)
        what = f"host (the scorer picked {pick})"
        ids = {"session_id": scored.moment.session_id, "moment_id": scored.moment.moment_id}
        with traced(tracer, "host_decide", ids) as span:
            answer = None
            # The host gets what the bids and the wait for its turn left of the budget (#26, #149). With under a
            # quarter of its usual time left, a call would almost surely be cut off: the scorer's pick goes out
            # without spending one.
            left = min(self.deadline_s, scored.moment.deadline - time.monotonic())
            skipped = skip_reason(self.agent is None, scored.moment.chaos, left < self.deadline_s / 4)
            if skipped:
                span.set_attribute("fallback", skipped)
            if self.agent is not None and not skipped:
                try:
                    moment = {"moment_id": scored.moment.moment_id, "summary": scored.moment.summary,
                              "names": scored.moment.names}  # fmt: skip
                    answer = await asyncio.wait_for(self.agent.decide(moment, candidates), left)
                except Exception as exc:  # timeout or error: the scorer's pick goes out
                    failed(what, exc, span, start)
                    answer = None
            result = self._from_host(scored, answer, candidates, _elapsed_ms(start))
            if answer is not None and result is None:
                why = "no candidate chosen, no reason of 1 to 140 characters, or a reason that is not true of the pick"
                failed(what, InvalidAnswer(why), span, start)
            if result is None:
                result = decision(scored, pick, "fallback", _elapsed_ms(start))
            span.set_attributes({"source": result["source"], "manager": result["chosen_manager"],
                                 "latency_ms": result["latency_ms"]})  # fmt: skip
        await ctx.yield_output(recorded(self.ledger, scored, result))


class Decided(Executor):
    def __init__(self, ledger: Ledger) -> None:
        super().__init__(id="decided")
        self.ledger = ledger

    @handler
    async def clear_winner(self, scored: Scored, ctx: WorkflowContext[None, dict[str, Any]]) -> None:
        # Always the scorer, even under a simulated failure: no model decides a clear call, and "fallback" means
        # the host's close call fell back (#86). The plain card's own source says the bid fell back.
        result = decision(scored, scored.ranked[0]["manager"], "scorer", 0)
        await ctx.yield_output(recorded(self.ledger, scored, result))


def build(
    managers: list[dict[str, Any]],
    manager_agent: ManagerAgent | None,
    host_agent: HostAgent | None,
    ledger: Ledger,
    publish: Callable[[dict[str, Any]], None],
    manager_deadline_s: float,
    host_deadline_s: float,
    turn: Callable[[str], Awaitable[None]] = no_wait,
) -> Workflow:
    """turn: waits until the moment with this id may take the arbiter's turn (#149)."""
    start = Start(id="start")
    display_names = {m["id"]: m["display_name"] for m in managers}
    steps = [ManagerStep(m, manager_agent, manager_deadline_s, publish, ledger, display_names) for m in managers]
    scorer, decided = ScoreStep(ledger, turn), Decided(ledger)
    host = HostStep(host_agent, host_deadline_s, display_names, ledger)
    workflow = (
        WorkflowBuilder(name="moment", description="One key moment: manager bids, scorer, host", start_executor=start)
        .add_fan_out_edges(start, steps)
        .add_fan_in_edges(steps, scorer)
        .add_edge(scorer, host, condition=lambda s: isinstance(s, Scored) and s.close)
        .add_edge(scorer, decided, condition=lambda s: isinstance(s, Scored) and not s.close)
        .build()
    )
    return workflow
