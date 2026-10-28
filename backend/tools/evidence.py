"""Evidence: our arbitration against one agent and against greedy code, on the same moments.

Three strategies decide the same key moments, behind one small interface (Strategy):
  ours:       the production moment workflow: manager agents in parallel, scorer, fairness ledger, host.
  one_agent:  a single model call per moment that sees every eligible friend's stake, writes all their
              explanations and picks the group screen.
  greedy:     code only: the biggest points swing always wins the screen.

Every strategy gets identical moments and the identical eligible set per moment (the managers whose stake is
above threshold, which is who our workflow asks). Both model strategies get the same text checks on each
explanation (numbers, names, tone, language, style) with one retry through the same `checked`, the session's
decision budget (4.5 s), and per-explanation fallback accounting (an explanation that is not delivered in time
or fails the checks twice is the code's plain-stat line, counted as a fallback). Ours also runs the angle check
and the room check on each bid's pitch (#138); the one agent writes no pitch, so it skips both.

Two runs:
  arbitration: 50 seeds, no model calls. Ours runs with stub agents that bid deterministically (urgency 0.5)
               and a stub host that takes the scorer's order, so it measures the code half of our arbitration.
  models:      a held-out sample (seeds from 1000, at most --models moments), ours with the real agents
               against the one agent with the same model. Laptop latency, not in-region.

From backend/:
  uv run python -m tools.evidence                                   # arbitration only, no model calls
  FOUNDRY_ENDPOINT=https://<name>.cognitiveservices.azure.com/ uv run python -m tools.evidence --models 20

The markdown result is written to --out (default: a dated file in the system temp folder) and its path printed.
Cost is the tokens used times --price-in and --price-out, each per 1M tokens; the defaults are the PRICE_IN and
PRICE_OUT constants in this file. Pass both for another model or rate.
"""

import argparse
import asyncio
import inspect
import json
import os
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from agent_framework import Message
from pydantic import BaseModel, ConfigDict

from tools.latency_test import percentile
from watchparty.agents import host as host_agent
from watchparty.agents import manager as manager_agent
from watchparty.agents.base import fallback_bid, plain_bid
from watchparty.agents.checks import number_problems
from watchparty.agents.foundry import Runner, checked
from watchparty.agents.scorer import Ledger
from watchparty.app.cues import stat_headline
from watchparty.app.session import Session
from watchparty.app.workflow import MomentInput, build
from watchparty.points import PointsEngine, load_league
from watchparty.sim import simulate

_DEFAULTS = inspect.signature(Session.__init__).parameters
BUDGET_S: float = _DEFAULTS["decision_budget_s"].default  # the session's own budget, so the two never drift
MANAGER_DEADLINE_S: float = _DEFAULTS["manager_deadline_s"].default
HOST_DEADLINE_S: float = _DEFAULTS["host_deadline_s"].default
HELD_OUT_START = 1000  # model seeds never overlap the 50 arbitration seeds
PRICE_IN, PRICE_OUT = 0.75, 4.50  # per 1M tokens, the defaults for --price-in and --price-out


# --- moments -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Moment:
    seed: int
    moment_id: str
    t: int
    summary: str
    stakes: dict[str, dict[str, Any]]  # every manager's stake
    names: list[str]  # the only names any agent may use
    players: dict[str, str]

    @property
    def eligible(self) -> list[str]:
        """Who is owed an explanation and can win the screen: above threshold, as the workflow decides."""
        return sorted(m for m, s in self.stakes.items() if s["above_threshold"])

    @property
    def owed(self) -> list[str]:
        """The eligible friends whose moment is worth points: the explanations counted for every strategy. A moment
        worth 0 IP is left out for all of them (#88): ours may decline it and the one agent cannot, so counting it
        would score a decline against a line nobody needed."""
        return [m for m in self.eligible if self.stakes[m]["delta_points"]]


def moments(seed: int) -> list[Moment]:
    """Every key moment of one simulated match, in order, with every manager's stake."""
    match, league = simulate(seed), load_league()
    engine = PointsEngine(match.meta, league)
    players = {p["id"]: p["name"] for s in ("home", "away") for p in match.meta[s]["players"]}
    names = [*players.values(), *(match.meta[s]["name"] for s in ("home", "away"))]
    names += [m["display_name"] for m in league["managers"]]
    out = []
    for event in match.events:
        if stakes := engine.feed(event):
            out.append(Moment(seed, event["moment_id"], event["t"], stat_headline(event, players),
                              {s["manager"]: s for s in stakes}, names, players))  # fmt: skip
    return out


MANAGERS: list[dict[str, Any]] = load_league()["managers"]
BY_ID = {m["id"]: m for m in MANAGERS}


def biggest_swing(moment: Moment) -> str:
    """The eligible manager with the largest |delta|; ties to the lower id."""
    return min(moment.eligible, key=lambda m: (-abs(moment.stakes[m]["delta_points"]), m))


# --- the interface -------------------------------------------------------------------------------------


@dataclass
class Outcome:
    """One strategy's decision on one moment."""

    chosen: str | None  # None only when nobody is eligible
    statuses: dict[str, str]  # eligible manager -> ok | retried | fallback | code
    pick: str  # model | scorer | fallback | code | none
    latency_ms: float = 0.0
    errors: list[str] = field(default_factory=list)


class Strategy(Protocol):
    name: str
    controls: str  # what checks, budget and fallback this strategy ran under, for the report

    def new_match(self) -> None: ...

    async def decide(self, moment: Moment) -> Outcome: ...


@dataclass
class Meter:
    """Counts model calls and tokens through a Runner."""

    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cut_off: int = 0  # calls cancelled by a deadline: the service reports no tokens for them

    def wrap[T: BaseModel](self, run: Runner[T]) -> Runner[T]:
        async def metered(messages: list[Message]) -> tuple[T | None, int, int]:
            self.calls += 1
            try:
                answer, tokens_in, tokens_out = await run(messages)
            except asyncio.CancelledError:
                self.cut_off += 1
                raise
            self.tokens_in, self.tokens_out = self.tokens_in + tokens_in, self.tokens_out + tokens_out
            return answer, tokens_in, tokens_out

        return metered


class Greedy:
    name = "greedy"
    controls = "none needed: code writes the plain-stat line for every eligible manager; no model, no budget"

    def new_match(self) -> None:
        pass

    async def decide(self, moment: Moment) -> Outcome:
        if not moment.eligible:
            return Outcome(None, {}, "none")
        return Outcome(biggest_swing(moment), dict.fromkeys(moment.owed, "code"), "code")


class Ours:
    """The production workflow, on its own ledger, which records each decision in that ledger, as in a session."""

    name = "ours"
    controls = (f"production checks: text, angle and the room check on the pitch (#138), one retry (`checked`),"
                f" {MANAGER_DEADLINE_S:g} s per bid and"
                f" {HOST_DEADLINE_S:g} s host inside the {BUDGET_S:g} s budget, plain-stat fallback per manager,"
                " scorer's pick if the host fails")  # fmt: skip

    def __init__(self, manager: Any, host: Any, meter: Meter | None = None) -> None:
        self.manager, self.host, self.meter = manager, host, meter or Meter()
        self.new_match()

    def new_match(self) -> None:
        self.ledger, self.bids = Ledger(), dict[str, dict[str, Any]]()
        self.workflow = build(MANAGERS, self.manager, self.host, self.ledger, self._publish,
                              MANAGER_DEADLINE_S, HOST_DEADLINE_S)  # fmt: skip

    def _publish(self, bid: dict[str, Any]) -> None:
        self.bids[bid["manager"]] = bid

    async def decide(self, moment: Moment) -> Outcome:
        self.bids = {}
        start = time.perf_counter()
        given = MomentInput(moment.moment_id, moment.t, moment.summary, moment.stakes,
                            time.monotonic() + BUDGET_S, False, moment.names, moment.players)  # fmt: skip
        outputs = (await self.workflow.run(given)).get_outputs()
        latency = (time.perf_counter() - start) * 1000
        result = outputs[0] if outputs else None
        if result is None:
            return Outcome(None, {}, "none", latency)
        # the workflow has recorded it in the ledger (#149)
        pick = {"host": "model", "scorer": "scorer", "fallback": "fallback"}[result["source"]]
        statuses = {m: self.bids[m]["status"] for m in moment.owed if m in self.bids}
        return Outcome(result["chosen_manager"], statuses, pick, latency)


# --- the one-agent baseline ----------------------------------------------------------------------------


class Explanation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manager_id: str
    explanation_en: str
    explanation: str
    evidence_ids: list[str]


class OneAnswer(BaseModel):
    """The one agent's structured output: every eligible manager's explanation, and the screen."""

    model_config = ConfigDict(extra="forbid")
    explanations: list[Explanation]
    chosen_manager: str
    because: Literal["bigger_swing", "least_airtime", "most_close_calls_lost", "dead_heat", "scorer_pick"]
    reason: str


# The bid's fields: the one agent writes no bid, so it has no pitch, urgency, angle or decline (#88)
DROPPED_RULES = ("- pitch_to_host", "- urgency", "- FACTS say", "- declined")


def rule_lines(instructions: str) -> list[str]:
    """Each "- " rule of a prompt with its continuation lines, as one string."""
    rules: list[str] = []
    for line in instructions.splitlines():
        if line.startswith("- "):
            rules.append(line)
        elif rules and line.startswith("  "):
            rules[-1] += "\n" + line
    return rules


def _without_bid_rules(instructions: str) -> str:
    dropped = {r for r in rule_lines(instructions) if r.startswith(DROPPED_RULES)}
    for rule in dropped:
        instructions = instructions.replace(rule + "\n", "").replace("\n" + rule, "")
    return instructions


ONE_AGENT_INSTRUCTIONS = (
    _without_bid_rules(manager_agent.INSTRUCTIONS).replace(
        "You are a manager's personal companion", "You are every manager's personal companion"
    )
    + """

You also host the one shared screen. Choose which manager's moment everyone sees: weigh how much the moment
matters (impact_points) against fairness (airtime_share, the share of the screen each has had so far). Do not
favour the viewer.

Write one item in explanations for each manager in MANAGERS whose POINTS is not 0, with that manager's
manager_id, following that manager's own style and language directive. For a manager whose POINTS is 0 an item is
optional: write one only when something in their FACTS is worth their attention. You may choose a manager whose
POINTS is 0 only if you wrote their item.
- chosen_manager: the id of one manager in MANAGERS.
- because: one of that manager's true_facts, the one that decided it. They are checked by code: bigger_swing
  (the largest impact_points), least_airtime (the least of the shared screen so far), most_close_calls_lost
  (the most close calls lost since last on screen), dead_heat (level with the best on all three, ahead on none),
  scorer_pick (first in MANAGERS, which lists the biggest swing first).
- reason: one lively sentence of at most 140 characters, in English, shown to everyone, that says why in words.
  The only numbers allowed are the chosen manager's own: impact_points, overruled_count, or airtime_share as a
  percentage. Viewers do not know the order of MANAGERS: never mention it; for scorer_pick, say what makes this
  moment matter.
- Name only people and clubs in NAMES. Refer to managers by name or "they", never "he", "she", "his" or "her"."""
)


def candidates(moment: Moment, ledger: Ledger) -> list[dict[str, Any]]:
    """The host's candidate facts for the one agent, biggest swing first (its scorer_pick): the eligible managers
    with their impact points and this strategy's own airtime. It has no close calls, so overruled_count is 0."""
    order = sorted(moment.eligible, key=lambda m: (-abs(moment.stakes[m]["delta_points"]), m))
    return [{"manager": m, "name": BY_ID[m]["display_name"], "pitch": "", "impact_points":
             moment.stakes[m]["delta_points"], "airtime_share": ledger.share(m),
             "overruled_count": ledger.overruled[m]} for m in order]  # fmt: skip


def one_agent_request(moment: Moment, facts_of: list[dict[str, Any]], ledger: Ledger | None = None) -> str:
    """Each eligible manager's facts and directive, exactly as the manager agent builds them, and the host's
    candidate facts, in one request."""
    writer = manager_agent.ManagerAgent(_never, "none")
    blocks = []
    for c in facts_of:
        m = c["manager"]
        facts, _, directive = writer.request(moment.stakes[m], context_for(moment, m, ledger)).partition("\n\n")
        blocks.append({"manager_id": m, "facts": json.loads(facts), "directive": directive,
                       "impact_points": c["impact_points"], "airtime_share": c["airtime_share"],
                       "overruled_count": c["overruled_count"],
                       "true_facts": sorted(host_agent.true_factors(m, facts_of))})  # fmt: skip
    return json.dumps({"moment": moment.summary, "MANAGERS": blocks, "NAMES": moment.names}, ensure_ascii=False)


async def _never(messages: list[Message]) -> tuple[Any, int, int]:  # pragma: no cover - only builds requests
    raise RuntimeError("request builder only")


def context_for(moment: Moment, manager_id: str, ledger: Ledger | None = None) -> dict[str, Any]:
    """The manager agent's context as production's ManagerStep.context builds it, on this strategy's ledger."""
    manager, ledger = BY_ID[manager_id], ledger or Ledger()
    return {"moment": moment.summary, "manager": manager, "names": moment.names,
            "squad": [moment.players[p] for p in manager["squad"]],
            "captain": moment.players[manager["captain"]], "players": moment.players,
            "managers": {m["id"]: m["display_name"] for m in MANAGERS},
            "ledger": {"airtime_share": ledger.share(manager_id),
                       "close_calls_lost": ledger.overruled[manager_id]}}  # fmt: skip


def explanation_problems(item: Explanation, moment: Moment) -> list[str]:
    """The manager agent's own checks on the text, on one of the one agent's explanations. The one agent is not
    asked for an angle or a decline (#88), so only the text checks apply; it writes no pitch, so the stand-in is
    one that passes them (#138)."""
    context = context_for(moment, item.manager_id)
    pitch = f"A big moment for {context['manager']['display_name']}."
    as_bid = manager_agent.Answer(declined=False, angle="", urgency=0.5, explanation_en=item.explanation_en,
                                  explanation=item.explanation, pitch_to_host=pitch,
                                  evidence_ids=item.evidence_ids)  # fmt: skip
    stake = moment.stakes[item.manager_id]
    as_bid.evidence_ids = manager_agent.with_fact_evidence(as_bid.evidence_ids, stake)  # as ours reads them
    checker = manager_agent.ManagerAgent(_never, "none")
    return checker.text_problems(as_bid, stake, context)


def pick_problems(answer: OneAnswer, moment: Moment, facts_of: list[dict[str, Any]]) -> list[str]:
    """The host agent's own checks on the pick: a true fact, the chosen manager's own numbers, names, language,
    tone and pronouns, over the same candidate facts."""
    host = host_agent.HostAgent(_never, "none")
    as_host = host_agent.HostAnswer(chosen_manager=answer.chosen_manager, because=answer.because, reason=answer.reason)
    return host.problems(as_host, {"names": moment.names}, facts_of)


def needed(answer: OneAnswer, moment: Moment) -> list[str]:
    """Whose explanation the answer must hold and pass (#135): every owed friend, and the chosen friend. A friend
    whose moment is worth 0 IP is otherwise left out, as ours may decline them, so a line for them is neither
    required nor checked; but ours can pick them only on a bid that passed its checks, so the one agent can too."""
    chosen = [answer.chosen_manager] if answer.chosen_manager in moment.eligible else []
    return [*moment.owed, *(m for m in chosen if m not in moment.owed)]


def _single(item: Explanation, ids: list[str], who: list[str]) -> bool:
    """The only explanation written for one of `who`."""
    return item.manager_id in who and ids.count(item.manager_id) == 1


def one_agent_problems(answer: OneAnswer, moment: Moment, facts_of: list[dict[str, Any]]) -> list[str]:
    ids, who = [e.manager_id for e in answer.explanations], needed(answer, moment)
    problems = [f"write exactly one explanation for {m}" for m in who if ids.count(m) != 1]
    problems += [f"{m} is not in MANAGERS" for m in ids if m not in moment.eligible]
    for item in answer.explanations:
        if _single(item, ids, who):
            problems += [f"{item.manager_id}: {p}" for p in explanation_problems(item, moment)]
    return problems + pick_problems(answer, moment, facts_of)


def english_placed(run: Runner[OneAnswer]) -> Runner[OneAnswer]:
    """english_first, per explanation: an English manager's text written into the copy field is moved."""

    async def placed(messages: list[Message]) -> tuple[OneAnswer | None, int, int]:
        answer, tokens_in, tokens_out = await run(messages)
        if answer is None:
            return answer, tokens_in, tokens_out
        fixed = []
        for e in answer.explanations:
            if BY_ID.get(e.manager_id, {}).get("lang") == "en" and e.explanation_en:
                e = e.model_copy(update={"explanation": e.explanation or e.explanation_en, "explanation_en": ""})
            fixed.append(e)
        return answer.model_copy(update={"explanations": fixed}), tokens_in, tokens_out

    return placed


class OneAgent:
    name = "one_agent"
    controls = (f"the manager agent's text checks on every explanation owed and on the chosen friend's (no pitch,"
                f" so no angle or room check), and the host"
                f" agent's checks on the pick and"
                f" reason, over the same candidate facts (overruled_count is 0: it has no close calls); one retry"
                f" (`checked`); the {BUDGET_S:g} s budget on the whole call; plain-stat fallback per explanation"
                " (explanations that pass on the second attempt are kept); biggest swing if the pick fails"
                )  # fmt: skip

    def __init__(self, run: Runner[OneAnswer], meter: Meter | None = None, budget_s: float = BUDGET_S) -> None:
        self.meter = meter or Meter()
        self.run, self.budget_s = english_placed(self.meter.wrap(run)), budget_s
        self.new_match()

    def new_match(self) -> None:
        self.ledger = Ledger()  # its own airtime, shown to it as the host sees ours

    async def decide(self, moment: Moment) -> Outcome:
        if not moment.eligible:
            return Outcome(None, {}, "none")
        start = time.perf_counter()
        facts_of = candidates(moment, self.ledger)
        answers: list[OneAnswer | None] = []  # each attempt's answer, None when it was not valid JSON

        async def attempt(messages: list[Message]) -> tuple[OneAnswer | None, int, int]:
            answer, tokens_in, tokens_out = await self.run(messages)
            answers.append(answer)
            return answer, tokens_in, tokens_out

        request = one_agent_request(moment, facts_of, self.ledger)
        outcome = Outcome(biggest_swing(moment), dict.fromkeys(moment.owed, "fallback"), "fallback")
        try:
            answer, status = await asyncio.wait_for(
                checked(attempt, request, lambda a: one_agent_problems(a, moment, facts_of)), self.budget_s
            )
            outcome = Outcome(answer.chosen_manager, dict.fromkeys(moment.owed, status), "model")
        except ValueError as exc:  # failed twice: keep what passed on the second attempt, never on the first
            outcome.errors.append(str(exc))
            second = answers[1] if len(answers) == 2 else None
            if second is not None:
                outcome = self._salvage(second, moment, facts_of, outcome)
        except Exception as exc:  # timeout, model or network error: everything falls back to code
            outcome.errors.append(type(exc).__name__)
        outcome.latency_ms = (time.perf_counter() - start) * 1000
        self.ledger.record(outcome.chosen or "", moment.eligible, close=False)
        return outcome

    @staticmethod
    def _salvage(answer: OneAnswer, moment: Moment, facts_of: list[dict[str, Any]], outcome: Outcome) -> Outcome:
        ids = [e.manager_id for e in answer.explanations]
        passed = [e.manager_id for e in answer.explanations if _single(e, ids, moment.eligible)
                  and not explanation_problems(e, moment)]  # fmt: skip
        outcome.statuses |= dict.fromkeys((m for m in passed if m in moment.owed), "retried")
        chosen_ok = answer.chosen_manager in moment.owed or answer.chosen_manager in passed  # 0 IP: needs its line
        if chosen_ok and not pick_problems(answer, moment, facts_of):
            outcome.chosen, outcome.pick = answer.chosen_manager, "model"
        return outcome


# --- stub agents for the model-free run ----------------------------------------------------------------


class StubManager:
    """Bids deterministically: the code's plain line with neutral urgency, as if the model answered first time,
    and a decline on a moment worth 0 IP, as production's code does (#88)."""

    async def bid(self, stake: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        bid = plain_bid(stake, context["manager"])
        return bid if bid["status"] == "declined" else bid | {"status": "ok", "urgency": 0.5}


class StubHost:
    """Takes the scorer's order: the model-free run measures the scorer and the fairness ledger."""

    async def decide(self, moment: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
        return {"chosen_manager": candidates[0]["manager"], "because": "scorer_pick", "reason": "Too close to call.",
                "status": "ok"}  # fmt: skip


# --- running -------------------------------------------------------------------------------------------


async def run_strategy(strategy: Strategy, by_seed: dict[int, list[Moment]]) -> dict[int, list[Outcome]]:
    out: dict[int, list[Outcome]] = {}
    for seed, ms in by_seed.items():
        strategy.new_match()
        out[seed] = [await strategy.decide(m) for m in ms]
    return out


def arbitration(by_seed: dict[int, list[Moment]], outcomes: dict[int, list[Outcome]]) -> dict[str, float]:
    """Airtime spread and minimum, reach per friend, and how often the biggest swing is on screen."""
    spreads, minimums = [], []
    eligible, shown = Counter[str](), Counter[str]()
    shut_out = friend_seeds = biggest = decided = 0
    ratio_sum = 0.0
    for seed, ms in by_seed.items():
        wins: Counter[str] = Counter()
        seed_eligible: Counter[str] = Counter()
        for moment, outcome in zip(ms, outcomes[seed], strict=True):
            if outcome.chosen is None:
                continue
            decided += 1
            wins[outcome.chosen] += 1
            seed_eligible.update(moment.eligible)
            top = max(abs(moment.stakes[m]["delta_points"]) for m in moment.eligible)
            mine = abs(moment.stakes[outcome.chosen]["delta_points"])
            biggest += mine == top
            ratio_sum += mine / top if top else 1.0
        n = sum(wins.values())
        if not n:
            continue
        shares = [wins[m] / n for m in seed_eligible]  # friends never eligible here had nothing to win
        spreads.append(max(shares) - min(shares))
        minimums.append(min(shares))
        eligible.update(seed_eligible)
        shown.update(wins)
        friend_seeds += len(seed_eligible)
        shut_out += sum(1 for m in seed_eligible if wins[m] == 0)
    reach = {m["id"]: shown[m["id"]] / eligible[m["id"]] if eligible[m["id"]] else 0.0 for m in MANAGERS}
    return {
        "decided": decided,
        "spread_mean": sum(spreads) / len(spreads) if spreads else 0.0,
        "min_share_mean": sum(minimums) / len(minimums) if minimums else 0.0,
        "shut_out": shut_out,
        "friend_seeds": friend_seeds,
        "reach_min": min(reach.values()),
        "reach_all": sum(shown.values()) / sum(eligible.values()) if eligible else 0.0,
        "biggest_swing": biggest / decided if decided else 0.0,
        "swing_ratio": ratio_sum / decided if decided else 0.0,
        **{f"reach_{m}": r for m, r in reach.items()},
    }


def shares(by_seed: dict[int, list[Moment]], outcomes: dict[int, list[Outcome]], seed: int) -> dict[str, float]:
    """Each friend's airtime share on one seed."""
    wins = Counter(o.chosen for o in outcomes[seed] if o.chosen)
    n = sum(wins.values())
    return {m["id"]: (wins[m["id"]] / n if n else 0.0) for m in MANAGERS}


# --- the checker on injected false numbers ------------------------------------------------------------

INJECTIONS = ("value", "sign", "total", "unit")


def _clear_of(value: float, stake: dict[str, Any]) -> bool:
    """Not within the checker's rounding of any evidence value in the stake: a number with no source."""
    return all(abs(abs(value) - abs(e["value"])) > 0.05 * abs(e["value"]) + 0.5 for e in stake["evidence"])


def injected(stake: dict[str, Any], kind: str) -> tuple[str, str]:
    """The plain-stat line with one false number written into it, and what was injected."""
    manager = BY_ID[stake["manager"]]
    delta = next(e for e in stake["evidence"] if e["id"].endswith("_delta"))
    total = next(e for e in stake["evidence"] if e["id"].endswith("_total"))
    if kind == "sign" and not delta["value"]:  # zero has no sign to flip
        kind = "value"
    false = json.loads(json.dumps(stake))
    target = next(e for e in false["evidence"] if e["id"] == (total if kind == "total" else delta)["id"])
    if kind == "sign":
        target["value"] = -delta["value"]
    elif kind in ("value", "total"):
        step = 3
        while not _clear_of(target["value"] + step, stake):
            step += 3
        target["value"] = round(target["value"] + step, 2)
    if kind == "total":  # only the analyst line states the total
        manager = manager | {"style": "analyst"}
    text = fallback_bid(false, manager)["explanation"]
    if kind == "unit":  # the true value, in the wrong unit: "5 xG" is never 5 IP
        text = text.replace(" IP", " xG", 1)
    return text, f"{kind}: {target['value']:+g}" if kind != "unit" else "unit: IP written as xG"


def checker_catches(by_seed: dict[int, list[Moment]], n: int = 100) -> tuple[int, int, list[str]]:
    """n false numbers, one per eligible stake in seed order, each injection kind in turn. Returns caught, n,
    and every miss. The check is the production one, against the evidence the line cites."""
    caught, misses, made = 0, [], 0
    kinds = iter(INJECTIONS * n)
    for ms in by_seed.values():
        for moment in ms:
            for m in moment.eligible:
                stake = moment.stakes[m]
                kind = next(kinds)
                text, what = injected(stake, kind)
                cited = [e["id"] for e in stake["evidence"] if e["id"].endswith(("_delta", "_total"))]
                if number_problems(text, stake["evidence"], cited, BY_ID[m]["lang"]):
                    caught += 1
                else:
                    misses.append(f"{moment.seed}/{moment.moment_id}/{m} {what}: {text}")
                made += 1
                if made == n:
                    return caught, n, misses
    return caught, made, misses


# --- model metrics -------------------------------------------------------------------------------------


def delivery(outcomes: Sequence[Outcome], meter: Meter, price: tuple[float, float]) -> dict[str, float]:
    """Delivered: an owed explanation that passed the checks in time, first time or after the retry. Only friends
    whose moment is worth points are owed one, and code refuses a decline from them (#88), so a decline is never
    counted here. A plain-line fallback is not delivered."""
    statuses = Counter(s for o in outcomes for s in o.statuses.values())
    owed = sum(statuses.values())
    answered = statuses["ok"] + statuses["retried"]
    decided = [o for o in outcomes if o.chosen is not None]
    latencies = [o.latency_ms for o in decided] or [0.0]
    cost = (meter.tokens_in * price[0] + meter.tokens_out * price[1]) / 1e6
    picks = Counter(o.pick for o in decided)
    return {
        "moments": len(decided), "owed": owed, "ok": statuses["ok"], "retried": statuses["retried"],
        "fallback": statuses["fallback"],
        "delivered": answered / owed if owed else 0.0,
        "model_picks": picks["model"], "scorer_picks": picks["scorer"], "fallback_picks": picks["fallback"],
        "p50_ms": percentile(latencies, 50), "p95_ms": percentile(latencies, 95),
        "over_budget": sum(o.latency_ms > BUDGET_S * 1000 for o in decided),
        "calls": meter.calls, "cut_off": meter.cut_off, "tokens_in": meter.tokens_in, "tokens_out": meter.tokens_out,
        "cost_usd": cost,
    }  # fmt: skip


def sample(start: int, limit: int) -> dict[int, list[Moment]]:
    """Held-out moments with at least one eligible manager, in match order, up to `limit`."""
    out: dict[int, list[Moment]] = {}
    seed, taken = start, 0
    while taken < limit:
        ms = [m for m in moments(seed) if m.eligible][: limit - taken]
        out[seed], taken, seed = ms, taken + len(ms), seed + 1
    return out


def order(n: int, turn: int) -> list[int]:
    """Strategy indexes for one moment, rotated by turn."""
    return [(turn + i) % n for i in range(n)]


async def interleaved(strategies: Sequence[Strategy], by_seed: dict[int, list[Moment]]) -> list[list[Outcome]]:
    """Each moment through every strategy before the next moment, so load on the model is shared alike, with the
    first strategy rotating moment by moment."""
    out: list[list[Outcome]] = [[] for _ in strategies]
    turn = 0
    for ms in by_seed.values():
        for s in strategies:
            s.new_match()
        for moment in ms:
            for i in order(len(strategies), turn):  # who goes first alternates: a warm connection favours no one
                out[i].append(await strategies[i].decide(moment))
            turn += 1
            print(
                f"{moment.seed}/{moment.moment_id}: "
                + ", ".join(  # progress, on stderr
                    f"{s.name} {o[-1].latency_ms:.0f} ms {dict(Counter(o[-1].statuses.values()))}"
                    for s, o in zip(strategies, out, strict=True)
                ),
                file=sys.stderr,
            )
    return out


# --- report --------------------------------------------------------------------------------------------


def _pct(x: float) -> str:
    return f"{x:.0%}"


LOWER_BOUNDS = ("tokens_in", "tokens_out", "cost_usd")


def losses(arb: dict[str, dict[str, float]], models: dict[str, dict[str, float]] | None) -> list[str]:
    """Every measure where ours is worse than a baseline, worded as a loss."""
    out = []
    higher_better = {"min_share_mean": "mean minimum airtime share", "reach_min": "worst friend's reach",
                     "reach_all": "reach over all friends", "biggest_swing": "moments showing the biggest swing",
                     "swing_ratio": "shown swing as a share of the biggest",
                     **{f"reach_{m['id']}": f"{m['display_name']}'s reach" for m in MANAGERS}}  # fmt: skip
    lower_better = {"spread_mean": "mean airtime spread", "shut_out": "friends shut out of the screen"}
    ours, greedy = arb["ours"], arb["greedy"]
    for key, label in higher_better.items():
        if ours[key] < greedy[key] - 1e-9:
            out.append(f"{label}: ours {ours[key]:.3f}, greedy {greedy[key]:.3f}")
    for key, label in lower_better.items():
        if ours[key] > greedy[key] + 1e-9:
            out.append(f"{label}: ours {ours[key]:.3f}, greedy {greedy[key]:.3f}")
    if not models:
        return out or ["none on these measures"]
    real, one = models["ours"], models["one_agent"]
    measures = (("delivered", "explanations delivered in the budget", max), ("p50_ms", "p50 decision latency", min),
                ("p95_ms", "p95 decision latency", min), ("fallback", "explanations that fell back", min),
                ("fallback_picks", "screen picks that fell back", min), ("over_budget", "decisions over budget", min),
                ("tokens_in", "input tokens", min), ("tokens_out", "output tokens", min),
                ("calls", "model calls", min), ("cut_off", "model calls cut off by the budget", min))  # fmt: skip
    for key, label, better in measures:
        if real[key] == one[key] or better(real[key], one[key]) != one[key]:
            continue
        bound = key in LOWER_BOUNDS  # only tokens miss what a cut-off call used; cost stays in the table only
        ours_text = f"ours {_at_least(real) if bound else ''}{real[key]:.4g}"
        if bound and one["cut_off"]:  # its true figure may be higher: not a proven loss
            out.append(f"{label}: {ours_text}, one agent at least {one[key]:.4g};"
                       f" not comparable, {int(one['cut_off'])} of its calls were cut off")  # fmt: skip
            continue
        out.append(f"{label}: {ours_text}, one agent {one[key]:.4g}")
    return out or ["none on these measures"]


def _at_least(d: dict[str, float]) -> str:
    """Tokens and cost are a lower bound once a call was cut off: its tokens were never reported."""
    return "at least " if d["cut_off"] else ""


def _table(columns: Sequence[str], rows: list[tuple[str, ...]]) -> list[str]:
    head = ["Measure", *columns]
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head), *("| " + " | ".join(r) + " |" for r in rows)]


def arbitration_rows(arb: dict[str, dict[str, float]]) -> list[tuple[str, ...]]:
    ours, greedy = arb["ours"], arb["greedy"]

    def row(label: str, key: str, fmt: Callable[[float], str] = _pct) -> tuple[str, ...]:
        return label, fmt(ours[key]), fmt(greedy[key])

    def two(x: float) -> str:
        return f"{x:.2f}"

    return [
        row("Mean airtime spread (lower is fairer)", "spread_mean", two),
        row("Mean minimum airtime share", "min_share_mean", lambda x: f"{x:.1%}"),
        row(f"Friends shut out (of {int(ours['friend_seeds'])})", "shut_out", lambda x: str(int(x))),
        row("Reach, all friends", "reach_all"),
        row("Reach, worst friend", "reach_min"),
        *(row(f"Reach, {m['display_name']} ({m['id']})", f"reach_{m['id']}") for m in MANAGERS),
        row("Screen shows the biggest swing", "biggest_swing"),
        row("Shown swing / biggest swing", "swing_ratio", lambda x: f"{x:.1%}"),
    ]


def model_rows(models: dict[str, dict[str, float]]) -> list[tuple[str, ...]]:
    ours, one = models["ours"], models["one_agent"]

    def each(fmt: Callable[[dict[str, float]], str]) -> tuple[str, str]:
        return fmt(ours), fmt(one)

    def count(key: str) -> Callable[[dict[str, float]], str]:
        return lambda d: str(int(d[key]))

    return [
        (f"Explanations delivered in {BUDGET_S:g} s",
         *each(lambda d: f"{d['delivered']:.0%} ({int(d['ok'] + d['retried'])}/{int(d['owed'])})")),
        ("of which after the one retry", *each(count("retried"))),
        ("Fell back to the plain line", *each(count("fallback"))),
        ("Screen picked by a model", *each(count("model_picks"))),
        ("Screen picked by our scorer (clear winner, no host call)", str(int(ours["scorer_picks"])), "n/a"),
        ("Screen pick fell back to code", *each(count("fallback_picks"))),
        ("Decision latency p50 / p95 ms", *each(lambda d: f"{d['p50_ms']:.0f} / {d['p95_ms']:.0f}")),
        (f"Decisions over {BUDGET_S:g} s", *each(count("over_budget"))),
        ("Model calls", *each(count("calls"))),
        ("of which cut off by the budget (no tokens reported)", *each(count("cut_off"))),
        ("Tokens in / out", *each(lambda d: f"{_at_least(d)}{int(d['tokens_in'])} / {int(d['tokens_out'])}")),
        ("Cost, USD", *each(lambda d: f"{_at_least(d)}{d['cost_usd']:.4f}")),
    ]  # fmt: skip


def report(
    seeds: list[int], arb: dict[str, dict[str, float]], catches: tuple[int, int, list[str]],
    strategies: dict[str, str], models: dict[str, dict[str, float]] | None, sample_seeds: list[int],
    model_name: str, price: tuple[float, float],
) -> str:  # fmt: skip
    caught, n, misses = catches
    lines = [
        f"# Evidence: ours vs one agent vs greedy code (#91), {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        "",
        "## Method",
        "",
        f"- Arbitration: seeds {seeds[0]} to {seeds[-1]} ({len(seeds)} matches), no model calls. Every method gets"
        " the same moments and, per moment, the same eligible managers (stake above threshold). A moment with"
        " nobody eligible is not decided by any method.",
        "- Ours here runs the production workflow with stub agents: every manager bids the plain-stat line at"
        " urgency 0.5 and the host takes the scorer's order. This measures the scorer and fairness ledger only.",
        "- Airtime share: a friend's screens over decided moments, per match, over the friends eligible at least"
        " once in that match (a friend never eligible had nothing to win). Spread: largest share minus smallest."
        " Reach: a friend's screens over their eligible moments. Shut out: a friend eligible at least once in a"
        " match who never got the screen.",
        "",
        "Controls each method had:",
        "",
        "| Method | Controls |",
        "|---|---|",
        *(f"| {k} | {v} |" for k, v in strategies.items()),
        "",
        "## Arbitration",
        "",
        f"Denominators: {int(arb['ours']['decided'])} decided moments; {int(arb['ours']['friend_seeds'])}"
        " friend-matches with at least one eligible moment.",
        "",
        *_table(("ours", "greedy"), arbitration_rows(arb)),
        "",
        "## Number checker on injected false numbers",
        "",
        f"{caught} of {n} caught. Code, not a model, wrote each false number into a manager's plain-stat line in"
        " their own language: in turn a wrong value, a flipped sign, a wrong league total, and the true value in"
        " the wrong unit. Each line was checked against the evidence it cites. This measures the number check"
        " both model methods run; no model-written text is part of it.",
        *(["", "Misses:", "", *(f"- {m}" for m in misses)] if misses else []),
        "",
    ]
    if models:
        ours = models["ours"]
        lines += [
            "## Models",
            "",
            f"Model {model_name} for every agent call. Held-out seeds {', '.join(map(str, sample_seeds))}: the same"
            f" {int(ours['moments'])} moments for both, {int(ours['owed'])} explanations owed to each, one per"
            " friend whose moment is worth points (a moment worth 0 IP is left out for both: ours may decline it,"
            " #88)."
            f" Cost at ${price[0]:g} in and ${price[1]:g} out per 1M tokens. Laptop to Azure, not in-region.",
            "",
            *_table(("ours", "one agent"), model_rows(models)),
            "",
            "The retry row does not compare like with like: our agents retry one friend's bid at a time, while the one"
            " agent retries its whole answer, so every owed friend whose line passes on that second attempt counts as"
            " retried.",
            "",
        ]
    lines += [
        "## Where ours loses",
        "",
        *(f"- {line}" for line in losses(arb, models)),
        "",
        "## Limitations",
        "",
        "- The arbitration run does not include the host model's judgement; the model run does, on few moments.",
        "- The model sample is small and one run: latency and delivery move with the service's load that hour.",
        "- Latency is from a laptop; production calls run in-region.",
        "- Tokens of a call cut off by the budget are not reported by the service: tokens and cost are then a"
        " lower bound, marked 'at least', and a cost difference against a lower bound is not listed as a loss.",
        "- The one agent keeps any explanation that passes on its second attempt, which is generous to it: its"
        " single answer is otherwise all or nothing.",
        "- The one agent writes no pitch, so its answer skips two checks our agents run on every bid: the angle check"
        " and the room check on the pitch (#138). Its explanations get the same text checks as ours. This exemption"
        " favours the baseline.",
        "- Both baselines are ours to build; a stronger one-agent prompt might do better.",
        "- Airtime is measured from five managers with fixed squads (league_v1); another league may differ.",
    ]  # fmt: skip
    if not models:
        lines.append("- No model run in this report: pass --models N with FOUNDRY_ENDPOINT set.")
    return "\n".join(lines) + "\n"


def same_denominators(results: dict[str, dict[str, float]], keys: Sequence[str]) -> None:
    """Every method decided the same moments and owed the same explanations, or the table compares nothing."""
    for key in keys:
        values = {name: r[key] for name, r in results.items()}
        if len(set(values.values())) > 1:
            raise ValueError(f"methods disagree on {key}: {values}")


async def evaluate(
    seeds: list[int], *, models: int = 0, runners: tuple[Any, Any, Runner[OneAnswer]] | None = None,
    model_name: str = "stub", price: tuple[float, float] = (PRICE_IN, PRICE_OUT), held_out: int = HELD_OUT_START,
) -> str:  # fmt: skip
    """The whole report. `runners`: manager agent, host agent and one-agent runner for the model run."""
    by_seed = {s: moments(s) for s in seeds}
    ours, greedy = Ours(StubManager(), StubHost()), Greedy()
    arb = {s.name: arbitration(by_seed, await run_strategy(s, by_seed)) for s in (ours, greedy)}
    same_denominators(arb, ("decided", "friend_seeds"))
    strategies = {"ours": Ours.controls, "one_agent": OneAgent.controls, "greedy": Greedy.controls}
    model_metrics, sample_seeds = None, []
    if models and runners:
        manager, host, one_run = runners
        ours_meter, one_meter = Meter(), Meter()
        real = Ours(manager(ours_meter), host(ours_meter), ours_meter)
        one = OneAgent(one_run, one_meter)
        warm = next(m for m in moments(held_out - 1) if m.eligible)  # unmetered: sign-in and connection (#6)
        await Ours(manager(Meter()), host(Meter())).decide(warm)
        await OneAgent(one_run).decide(warm)
        held = sample(held_out, models)
        sample_seeds = list(held)
        got = await interleaved([real, one], held)
        model_metrics = {"ours": delivery(got[0], ours_meter, price), "one_agent": delivery(got[1], one_meter, price)}
        same_denominators(model_metrics, ("moments", "owed"))
    return report(seeds, arb, checker_catches(by_seed), strategies, model_metrics, sample_seeds, model_name, price)


def foundry_runners(model: str) -> tuple[Callable[[Meter], Any], Callable[[Meter], Any], Runner[OneAnswer]]:
    from watchparty.agents.foundry import agent_runner, foundry_client

    client = foundry_client(os.environ["FOUNDRY_ENDPOINT"])
    manager_run = manager_agent.foundry_runner(client, model)
    host_run = host_agent.foundry_runner(client, model)
    one_run = agent_runner(client, model, "one_agent", ONE_AGENT_INSTRUCTIONS, OneAnswer, max_tokens=2000)

    def manager(meter: Meter) -> manager_agent.ManagerAgent:
        return manager_agent.ManagerAgent(meter.wrap(manager_run), model)

    def host(meter: Meter) -> host_agent.HostAgent:
        return host_agent.HostAgent(meter.wrap(host_run), model)

    return manager, host, one_run


def _seeds(text: str) -> int:
    """Arbitration seeds start at 0 and must stay below the held-out model seeds."""
    n = int(text)
    if not 0 < n < HELD_OUT_START:
        raise argparse.ArgumentTypeError(f"--seeds must be 1 to {HELD_OUT_START - 1}, so no seed is also held out")
    return n


def _models(text: str) -> int:
    n = int(text)
    if n < 0:
        raise argparse.ArgumentTypeError("--models must be 0 or more")
    return n


def main(argv: Sequence[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seeds", type=_seeds, default=50, help=f"matches for the arbitration run, 1 to"
                        f" {HELD_OUT_START - 1}")  # fmt: skip
    parser.add_argument("--models", type=_models, default=0, help="held-out moments for the model run (0: none)")
    parser.add_argument("--model", default=os.environ.get("MANAGER_MODEL", "gpt-5.4-mini"))
    parser.add_argument("--price-in", type=float, default=PRICE_IN, help="price per 1M input tokens")
    parser.add_argument("--price-out", type=float, default=PRICE_OUT, help="price per 1M output tokens")
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    parser.add_argument("--out", type=Path, default=Path(tempfile.gettempdir()) / f"evidence-{stamp}.md")
    args = parser.parse_args(argv)
    runners = foundry_runners(args.model) if args.models else None
    text = asyncio.run(evaluate(list(range(args.seeds)), models=args.models, runners=runners,
                                model_name=args.model, price=(args.price_in, args.price_out)))  # fmt: skip
    args.out.write_text(text)
    print(args.out)
    return Path(args.out)


if __name__ == "__main__":
    main()
