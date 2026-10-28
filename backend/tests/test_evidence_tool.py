"""#91: the evidence harness. Stub models only; no network."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from agent_framework import Message

from tools import evidence as ev
from watchparty.agents.base import written
from watchparty.agents.host import HostAnswer
from watchparty.agents.manager import Answer, ManagerAgent
from watchparty.agents.scorer import Ledger
from watchparty.app.workflow import ManagerStep, MomentInput

SEED = 5  # a match where Leo (mgr_4) is above threshold but never has the biggest swing
LEO = "mgr_4"

pytestmark = pytest.mark.anyio


def _line(lang: str, value: float, wrong: bool = False) -> str:
    """A one-number line that passes every check, or states a number no evidence holds."""
    shown = value + 77 if wrong else value
    return f"{written(shown, lang, sign=True)} IP {'por esta jugada' if lang == 'es' else 'from this moment'}."


def _delta(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return next(e for e in evidence if e["id"].endswith("_delta"))


def _text(messages: list[Message]) -> str:
    return str(messages[0].text)


class Script:
    """A stub runner: answers built from the request's own evidence; `attempts` says which attempts lie, for whom
    (`liar`, one id or several). The one agent leaves out `omit` and picks `pick` on its first true fact."""

    def __init__(self, liar: str | tuple[str, ...] = "", attempts: tuple[int, ...] = (), delay: float = 0.0,
                 invalid: tuple[int, ...] = (), omit: str = "", pick: str = "") -> None:  # fmt: skip
        self.liars = (liar,) if isinstance(liar, str) else liar
        self.attempts, self.delay, self.invalid, self.omit, self.pick = attempts, delay, invalid, omit, pick

    @staticmethod
    def attempt(messages: list[Message]) -> int:
        return sum(1 for m in messages if m.role == "user")  # checked() adds one user message per retry

    def lies(self, manager_id: str, messages: list[Message]) -> bool:
        return manager_id in self.liars and self.attempt(messages) in self.attempts

    async def manager(self, messages: list[Message]) -> tuple[Answer | None, int, int]:
        await asyncio.sleep(self.delay)
        facts = json.loads(_text(messages).partition("\n\n")[0])
        if not facts["POINTS"]:  # worth 0 IP: the request carries no swing to quote, and the agent passes (#88)
            return Answer(declined=True, angle="", urgency=0, explanation_en="", explanation="", pitch_to_host="",
                          evidence_ids=[]), 100, 5  # fmt: skip
        delta = _delta(facts["EVIDENCE"])
        who = next(m["id"] for m in ev.MANAGERS if m["display_name"] == facts["manager"])
        lang, lie = facts["language"], self.lies(who, messages)
        angle = facts["FACTS"][0]["id"] if facts["FACTS"] else ""
        answer = Answer(declined=False, angle=angle, urgency=0.5, explanation=_line(lang, delta["value"], lie),
                        explanation_en="" if lang == "en" else _line("en", delta["value"]),
                        pitch_to_host=f"A big one for {facts['manager']}.", evidence_ids=[delta["id"]])  # fmt: skip
        return answer, 100, 20

    async def host(self, messages: list[Message]) -> tuple[HostAnswer | None, int, int]:
        request = json.loads(_text(messages))
        pick = next(c["id"] for c in request["candidates"] if "scorer_pick" in c["true_facts"])
        return HostAnswer(chosen_manager=pick, because="scorer_pick", reason="Too close to call."), 80, 10

    async def one(self, messages: list[Message]) -> tuple[ev.OneAnswer | None, int, int]:
        await asyncio.sleep(self.delay)
        if self.attempt(messages) in self.invalid:
            return None, 300, 60  # not valid JSON for the schema
        request = json.loads(_text(messages))
        items = []
        for block in request["MANAGERS"]:
            lang, who = block["facts"]["language"], block["manager_id"]
            if who == self.omit:
                continue
            if not block["facts"]["POINTS"]:  # no swing to quote (#88): the league position instead
                rank = next(e for e in block["facts"]["EVIDENCE"] if e["id"].endswith("_rank_after"))
                shown = rank["value"] + 77 if self.lies(who, messages) else rank["value"]
                line = {"en": "Now rank {} in the league.", "es": "Ahora puesto {} en la liga."}
                items.append(ev.Explanation(manager_id=who, evidence_ids=[rank["id"]],
                                            explanation_en="" if lang == "en" else line["en"].format(shown),
                                            explanation=line[lang].format(shown)))  # fmt: skip
                continue
            delta = _delta(block["facts"]["EVIDENCE"])
            english = "" if lang == "en" else _line("en", delta["value"])
            items.append(ev.Explanation(manager_id=who, evidence_ids=[delta["id"]], explanation_en=english,
                                        explanation=_line(lang, delta["value"], self.lies(who, messages))))  # fmt: skip
        chosen, because = next((b["manager_id"], "scorer_pick") for b in request["MANAGERS"]
                               if "scorer_pick" in b["true_facts"])  # fmt: skip
        if self.pick:
            chosen, because = (
                self.pick,
                next(b for b in request["MANAGERS"] if b["manager_id"] == self.pick)["true_facts"][0],
            )
        reason = "Too close to call." if because == "scorer_pick" else "The least of the screen so far."
        answer = ev.OneAnswer(explanations=items, chosen_manager=chosen, because=because, reason=reason)
        return answer, 300, 60


def _runners(script: Script) -> tuple[Any, Any, Any]:
    from watchparty.agents.host import HostAgent
    from watchparty.agents.manager import ManagerAgent

    return (lambda m: ManagerAgent(m.wrap(script.manager), "stub"), lambda m: HostAgent(m.wrap(script.host), "stub"),
            script.one)  # fmt: skip


def _two_eligible() -> ev.Moment:
    return next(m for s in range(5) for m in ev.moments(s) if len(m.eligible) >= 2)


def _arb(strategy: ev.Strategy, seeds: list[int]) -> dict[str, float]:
    by_seed = {s: ev.moments(s) for s in seeds}
    return ev.arbitration(by_seed, asyncio.run(ev.run_strategy(strategy, by_seed)))


def _shares(strategy: ev.Strategy) -> dict[str, float]:
    by_seed = {SEED: ev.moments(SEED)}
    return ev.shares(by_seed, asyncio.run(ev.run_strategy(strategy, by_seed)), SEED)


# 1
def test_greedy_never_shows_a_friend_who_never_has_the_biggest_swing() -> None:
    ms = ev.moments(SEED)
    assert any(LEO in m.eligible for m in ms)  # Leo is owed the screen at least once
    assert all(ev.biggest_swing(m) != LEO for m in ms if m.eligible)
    assert _shares(ev.Greedy())[LEO] == 0
    for m in ms:  # and greedy is the biggest swing, every time
        chosen = asyncio.run(ev.Greedy().decide(m)).chosen
        top = max((abs(m.stakes[x]["delta_points"]) for x in m.eligible), default=None)
        assert (abs(m.stakes[chosen]["delta_points"]) if chosen else None) == top


# 2
def test_our_arbitration_gives_that_friend_airtime() -> None:
    assert _shares(ev.Ours(ev.StubManager(), ev.StubHost()))[LEO] > 0


# 3
def test_the_checker_catches_every_injected_false_number() -> None:
    by_seed = {s: ev.moments(s) for s in range(10)}
    caught, n, misses = ev.checker_catches(by_seed, 100)
    assert (caught, n, misses) == (100, 100, [])


def test_every_injection_changes_the_line() -> None:
    """The instrument: an injection that writes nothing false would be 'caught' by nothing. Casual lines too."""
    stakes = [m.stakes[x] for m in ev.moments(0) for x in m.eligible]
    assert {ev.BY_ID[s["manager"]]["style"] for s in stakes} == {"casual", "analyst"}
    for stake in stakes:
        lang = ev.BY_ID[stake["manager"]]["lang"]
        for kind in ev.INJECTIONS:
            text, what = ev.injected(stake, kind)
            if kind == "unit":
                assert " xG" in text, text
                continue
            false = float(what.split(": ")[1])
            assert written(false, lang, sign=kind != "total") in text, (kind, text)


# 4
async def test_one_agent_runs_on_a_stub_model_and_counts_latency_and_completion() -> None:
    moment = _two_eligible()
    script = Script(delay=0.01)
    one = ev.OneAgent(script.one)
    outcome = await one.decide(moment)
    assert outcome.statuses == dict.fromkeys(moment.eligible, "ok")
    assert outcome.pick == "model"
    assert outcome.latency_ms >= 10
    assert (one.meter.calls, one.meter.tokens_in, one.meter.tokens_out) == (1, 300, 60)


async def test_one_agent_past_the_budget_falls_back_everywhere() -> None:
    moment = _two_eligible()
    one = ev.OneAgent(Script(delay=0.5).one, budget_s=0.05)
    outcome = await one.decide(moment)
    assert outcome.statuses == dict.fromkeys(moment.eligible, "fallback")
    assert (outcome.pick, outcome.chosen) == ("fallback", ev.biggest_swing(moment))
    assert 50 <= outcome.latency_ms < 500
    assert outcome.errors == ["TimeoutError"]
    assert (one.meter.calls, one.meter.cut_off, one.meter.tokens_in) == (1, 1, 0)  # counted, with no tokens


# 4b: the one agent gets the manager agent's controls
@pytest.mark.parametrize(("attempts", "status"), [((1,), "retried"), ((1, 2), "fallback")])
async def test_a_false_number_is_rejected_retried_and_counted_alike(attempts: tuple[int, ...], status: str) -> None:
    moment = _two_eligible()
    liar = moment.eligible[0]

    one = ev.OneAgent(Script(liar, attempts).one)
    one_out = await one.decide(moment)

    ours_script, meter = Script(liar, attempts), ev.Meter()
    manager, host, _ = _runners(ours_script)
    ours = ev.Ours(manager(meter), host(meter), meter)
    ours_out = await ours.decide(moment)

    assert one_out.statuses[liar] == ours_out.statuses[liar] == status
    assert one.meter.calls == 2  # the one retry, then no more
    assert sum(1 for s in ours_out.statuses.values() if s != "ok") == 1  # only the liar was retried
    honest = [m for m in moment.eligible if m != liar]
    assert all(one_out.statuses[m] == "retried" for m in honest)  # the one agent re-wrote them all


# 5
def test_the_report_is_a_markdown_table_with_method_seeds_denominators_and_limitations(tmp_path: Path) -> None:
    text = asyncio.run(ev.evaluate([0, 1], models=3, runners=_runners(Script()), model_name="stub"))
    for needed in ("## Method", "seeds 0 to 1 (2 matches)", "| Method | Controls |", "| ours |", "| one_agent |",
                   "| greedy |", "Denominators:", "decided moments", "| Measure | ours | greedy |",
                   "| Measure | ours | one agent |", "explanations owed to each", "## Where ours loses",
                   "## Limitations", "no model-written text is part of it", "over the friends eligible"):  # fmt: skip
        assert needed in text, needed
    assert "Explanations delivered in 4.5 s | 100%" in text


def test_main_writes_the_report_and_prints_its_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "evidence.md"
    assert ev.main(["--seeds", "2", "--out", str(out)]) == out
    assert capsys.readouterr().out.strip() == str(out)
    assert out.read_text().startswith("# Evidence")


# 6
def test_the_same_seeds_give_the_same_arbitration_numbers() -> None:
    first = _arb(ev.Ours(ev.StubManager(), ev.StubHost()), [3, 4])
    again = _arb(ev.Ours(ev.StubManager(), ev.StubHost()), [3, 4])
    assert first == again
    assert _arb(ev.Greedy(), [3, 4]) == _arb(ev.Greedy(), [3, 4])


def test_where_ours_loses_lists_every_measure_a_baseline_wins() -> None:
    arb = {"ours": _arb(ev.Ours(ev.StubManager(), ev.StubHost()), [0]), "greedy": _arb(ev.Greedy(), [0])}
    arb["ours"]["reach_mgr_1"], arb["greedy"]["reach_mgr_1"] = 0.3, 0.5
    base = {"delivered": 0.9, "p50_ms": 2000.0, "p95_ms": 4000.0, "fallback": 2, "fallback_picks": 0,
            "over_budget": 0, "tokens_in": 100, "tokens_out": 10, "cost_usd": 0.01, "calls": 5,
            "cut_off": 0}  # fmt: skip
    ours = base | {"p50_ms": 3600.0, "cut_off": 1}
    lost = ev.losses(arb, {"ours": ours, "one_agent": base | {"calls": 2, "delivered": 0.8}})
    assert "Jo's reach: ours 0.300, greedy 0.500" in lost
    assert "p50 decision latency: ours 3600, one agent 2000" in lost
    assert "model calls: ours 5, one agent 2" in lost
    assert "model calls cut off by the budget: ours 1, one agent 0" in lost
    assert not any(line.startswith("explanations delivered") for line in lost)  # ours wins that one


# review fixes on #103


def test_the_one_agent_keeps_every_manager_rule_but_the_bid_fields() -> None:
    from watchparty.agents.manager import INSTRUCTIONS

    rules = ev.rule_lines(INSTRUCTIONS)
    kept = [r for r in rules if not r.startswith(ev.DROPPED_RULES)]
    assert len(kept) == len(rules) - 4  # pitch_to_host, urgency, the angle and the decline (#88), nothing else
    for rule in kept:
        assert rule in ev.ONE_AGENT_INSTRUCTIONS, rule
    assert not any(r.startswith(ev.DROPPED_RULES) for r in ev.rule_lines(ev.ONE_AGENT_INSTRUCTIONS))


def _moment(seed: int, mid: str, eligible: dict[str, float]) -> ev.Moment:
    stakes = {m["id"]: {"delta_points": eligible.get(m["id"], 0), "above_threshold": m["id"] in eligible}
              for m in ev.MANAGERS}  # fmt: skip
    return ev.Moment(seed, mid, 0, "", stakes, [], {})


def _chose(manager: str | None) -> ev.Outcome:
    return ev.Outcome(manager, {}, "code" if manager else "none")


def test_arbitration_numbers_on_hand_built_moments() -> None:
    by_seed = {
        1: [_moment(1, "a", {"mgr_1": 5, "mgr_2": 3}), _moment(1, "b", {"mgr_1": 2, "mgr_2": 4}),
            _moment(1, "c", {}), _moment(1, "d", {"mgr_3": -6})],
        2: [_moment(2, "a", {"mgr_4": 3, "mgr_5": 3}), _moment(2, "b", {"mgr_5": 1})],
    }  # fmt: skip
    outcomes = {1: [_chose("mgr_1"), _chose("mgr_1"), _chose(None), _chose("mgr_3")],
                2: [_chose("mgr_4"), _chose("mgr_5")]}  # fmt: skip
    got = ev.arbitration(by_seed, outcomes)
    # Seed 1: eligible mgr_1, mgr_2, mgr_3; shares 2/3, 0, 1/3. Seed 2: eligible mgr_4, mgr_5; shares 1/2, 1/2.
    assert got["decided"] == 5
    assert got["spread_mean"] == pytest.approx((2 / 3 + 0) / 2)
    assert got["min_share_mean"] == pytest.approx((0 + 0.5) / 2)  # never-eligible friends are left out
    assert (got["shut_out"], got["friend_seeds"]) == (1, 5)  # mgr_2
    assert got["reach_min"] == 0  # mgr_2: 0 of 2
    assert got["reach_mgr_5"] == pytest.approx(0.5)
    assert got["reach_all"] == pytest.approx(5 / 8)
    assert got["biggest_swing"] == pytest.approx(4 / 5)  # all but seed 1, moment b
    assert got["swing_ratio"] == pytest.approx((1 + 0.5 + 1 + 1 + 1) / 5)


def test_the_one_agent_pick_gets_the_host_checks() -> None:
    moment = _two_eligible()
    facts_of = ev.candidates(moment, Ledger())
    first = facts_of[0]["manager"]
    good = ev.OneAnswer(explanations=[], chosen_manager=first, because="scorer_pick", reason="Too close to call.")
    assert ev.pick_problems(good, moment, facts_of) == []
    untrue = good.model_copy(update={"because": "least_airtime"})  # everyone is at 0 airtime
    assert any("because 'least_airtime'" in p for p in ev.pick_problems(untrue, moment, facts_of))
    gendered = good.model_copy(update={"reason": "Too close to call, but his swing wins."})
    assert any("guesses a gender" in p for p in ev.pick_problems(gendered, moment, facts_of))


def test_the_first_strategy_alternates_moment_by_moment() -> None:
    log: list[str] = []

    class Recorder:
        controls = ""

        def __init__(self, name: str) -> None:
            self.name = name

        def new_match(self) -> None:
            pass

        async def decide(self, moment: ev.Moment) -> ev.Outcome:
            log.append(self.name)
            return ev.Outcome(None, {}, "none")

    by_seed = {0: ev.moments(0)[:2], 1: ev.moments(1)[:1]}
    asyncio.run(ev.interleaved([Recorder("a"), Recorder("b")], by_seed))
    assert log == ["a", "b", "b", "a", "a", "b"]


async def test_nothing_is_salvaged_from_a_first_attempt_when_the_second_is_not_json() -> None:
    moment = _two_eligible()
    one = ev.OneAgent(Script(moment.eligible[0], (1,), invalid=(2,)).one)
    outcome = await one.decide(moment)
    assert outcome.statuses == dict.fromkeys(moment.eligible, "fallback")
    assert (outcome.pick, outcome.chosen) == ("fallback", ev.biggest_swing(moment))


async def test_the_second_attempt_is_salvaged_when_the_first_was_not_json() -> None:
    moment = _two_eligible()
    liar = moment.eligible[0]
    one = ev.OneAgent(Script(liar, (2,), invalid=(1,)).one)
    outcome = await one.decide(moment)
    assert outcome.statuses == {m: "fallback" if m == liar else "retried" for m in moment.eligible}
    assert outcome.pick == "model"
    assert one.meter.calls == 2


def test_a_cost_against_a_lower_bound_is_not_a_loss() -> None:
    arb = {"ours": _arb(ev.Greedy(), [0]), "greedy": _arb(ev.Greedy(), [0])}
    base = {"delivered": 0.9, "p50_ms": 2000.0, "p95_ms": 4000.0, "fallback": 2, "fallback_picks": 0,
            "over_budget": 0, "tokens_in": 100, "tokens_out": 10, "cost_usd": 0.01, "calls": 5, "cut_off": 0,
            "ok": 9, "retried": 0, "declined": 0, "owed": 10, "model_picks": 1, "scorer_picks": 0,
            "moments": 1}  # fmt: skip
    ours = base | {"tokens_in": 200, "cost_usd": 0.02}
    exact = ev.losses(arb, {"ours": ours, "one_agent": base})
    assert "input tokens: ours 200, one agent 100" in exact
    assert not any(line.startswith("cost") for line in exact)  # money stays in the table, never in the losses
    bounded = ev.losses(arb, {"ours": ours, "one_agent": base | {"cut_off": 3}})
    assert "input tokens: ours 200, one agent at least 100; not comparable, 3 of its calls were cut off" in bounded
    slower = ev.losses(arb, {"ours": ours | {"cut_off": 2, "p50_ms": 3000.0}, "one_agent": base})
    assert "p50 decision latency: ours 3000, one agent 2000" in slower  # a measured time is not a lower bound
    assert "input tokens: ours at least 200, one agent 100" in slower
    rows = {r[0]: r[1:] for r in ev.model_rows({"ours": ours, "one_agent": base | {"cut_off": 3}})}
    assert rows["Cost, USD"] == ("0.0200", "at least 0.0100")
    assert rows["of which cut off by the budget (no tokens reported)"] == ("0", "3")


def test_methods_that_decided_different_moments_get_no_report() -> None:
    ev.same_denominators({"ours": {"decided": 4}, "greedy": {"decided": 4}}, ("decided",))
    with pytest.raises(ValueError, match="methods disagree on decided"):
        ev.same_denominators({"ours": {"decided": 4}, "greedy": {"decided": 5}}, ("decided",))


@pytest.mark.parametrize("args", [["--seeds", "0"], ["--seeds", "-1"], ["--seeds", str(ev.HELD_OUT_START)],
                                  ["--models", "-1"]])  # fmt: skip
def test_bad_sizes_are_refused_at_parsing(args: list[str], tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as stopped:
        ev.main([*args, "--out", str(tmp_path / "x.md")])
    assert stopped.value.code == 2
    assert not (tmp_path / "x.md").exists()


# --- review of #113: the comparison stays fair ------------------------------------------------------------


def _priced(moment: ev.Moment, deltas: dict[str, float]) -> ev.Moment:
    """A real moment with chosen swings: the named managers eligible, the rest below the threshold."""
    stakes = {}
    for m, stake in moment.stakes.items():
        delta = deltas.get(m, 0)
        evidence = [e | {"value": delta} if e["id"].endswith("_delta") else e for e in stake["evidence"]]
        stakes[m] = stake | {"delta_points": delta, "above_threshold": m in deltas, "evidence": evidence}
    seed, mid = moment.seed, moment.moment_id
    return ev.Moment(seed, mid, moment.t, moment.summary, stakes, moment.names, moment.players)


# A close call, and a friend at stake for 0 IP
TIED_WITH_A_ZERO: dict[str, float] = {"mgr_1": 6, "mgr_2": 6, "mgr_3": 0}


async def test_the_stub_manager_declines_a_moment_worth_nothing_as_production_does() -> None:
    moment = _priced(ev.moments(0)[0], {"mgr_3": 0.0})
    bid = await ev.StubManager().bid(moment.stakes["mgr_3"], ev.context_for(moment, "mgr_3"))
    assert bid["status"] == "declined"
    outcome = await ev.Ours(ev.StubManager(), ev.StubHost()).decide(moment)
    assert (outcome.chosen, outcome.statuses, outcome.pick) == (None, {}, "none")  # not a "+0 IP" ok bid


async def test_ours_owes_and_overrules_only_the_friends_whose_moment_is_worth_points() -> None:
    moment = _priced(ev.moments(0)[0], TIED_WITH_A_ZERO)
    ours = ev.Ours(ev.StubManager(), ev.StubHost())
    outcome = await ours.decide(moment)
    assert moment.owed == ["mgr_1", "mgr_2"]
    assert set(outcome.statuses) == {"mgr_1", "mgr_2"}  # the decline is not owed, so not counted
    assert (ours.ledger.overruled["mgr_2"], ours.ledger.overruled["mgr_3"]) == (1, 0)  # a decliner is not overruled
    assert ours.ledger.decisions == 1  # recorded once, by the workflow (#149)


async def test_the_baselines_owe_the_same_friends() -> None:
    moment = _priced(ev.moments(0)[0], TIED_WITH_A_ZERO)
    assert set((await ev.Greedy().decide(moment)).statuses) == {"mgr_1", "mgr_2"}
    one = await ev.OneAgent(Script().one).decide(moment)
    assert set(one.statuses) == {"mgr_1", "mgr_2"}


def test_the_one_agent_reads_each_friend_exactly_as_production_builds_the_request() -> None:
    moment = _priced(ev.moments(0)[0], TIED_WITH_A_ZERO)
    ledger = Ledger()
    ledger.record("mgr_1", ["mgr_1", "mgr_2"], close=True)  # a ledger that is not all zeros
    request = json.loads(ev.one_agent_request(moment, ev.candidates(moment, ledger), ledger))
    given = MomentInput(moment.moment_id, moment.t, moment.summary, moment.stakes, 0.0, False, moment.names,
                        moment.players)  # fmt: skip
    names = {m["id"]: m["display_name"] for m in ev.MANAGERS}
    writer = ManagerAgent(ev._never, "none")
    for block in request["MANAGERS"]:
        m = block["manager_id"]
        step = ManagerStep(ev.BY_ID[m], None, 4.0, lambda _: None, ledger, names)
        facts, _, directive = writer.request(moment.stakes[m], step.context(given)).partition("\n\n")
        assert (block["facts"], block["directive"]) == (json.loads(facts), directive), m
    by_id = {b["manager_id"]: b["facts"] for b in request["MANAGERS"]}
    assert by_id["mgr_2"]["LEAGUE"]["close_calls_lost_since_last_on_screen"] == 1  # the real ledger, not zeros


# --- #135: the one agent owes explanations only to the friends whose moment is worth points ----------------


def _zero_ip() -> ev.Moment:
    moment = _priced(ev.moments(0)[0], TIED_WITH_A_ZERO)
    assert (moment.eligible, moment.owed) == (["mgr_1", "mgr_2", "mgr_3"], ["mgr_1", "mgr_2"])
    return moment


def _screened(one: ev.OneAgent) -> ev.OneAgent:
    """mgr_1 and mgr_2 have had the screen, so mgr_3 has had the least of it: a true fact to pick them on."""
    for winner in ("mgr_1", "mgr_2"):
        one.ledger.record(winner, ["mgr_1", "mgr_2"], close=False)
    return one


# 1
async def test_a_bad_line_for_a_friend_worth_0_ip_does_not_fail_the_answer() -> None:
    moment = _zero_ip()
    one = ev.OneAgent(Script("mgr_3", (1, 2)).one)
    outcome = await one.decide(moment)
    assert outcome.statuses == {"mgr_1": "ok", "mgr_2": "ok"}
    assert (outcome.pick, outcome.errors, one.meter.calls) == ("model", [], 1)


# 2
@pytest.mark.parametrize(("attempts", "status"), [((1,), "retried"), ((1, 2), "fallback")])
async def test_a_bad_line_for_an_owed_friend_is_retried_then_falls_back(attempts: tuple[int, ...], status: str) -> None:
    moment = _zero_ip()
    one = ev.OneAgent(Script("mgr_1", attempts).one)
    outcome = await one.decide(moment)
    assert outcome.statuses == {"mgr_1": status, "mgr_2": "retried"}
    assert one.meter.calls == 2


# 3
async def test_an_answer_may_leave_out_a_friend_worth_0_ip() -> None:
    moment = _zero_ip()
    one = ev.OneAgent(Script(omit="mgr_3").one)
    outcome = await one.decide(moment)
    assert (outcome.statuses, outcome.pick, one.meter.calls) == ({"mgr_1": "ok", "mgr_2": "ok"}, "model", 1)


# 4, 5
def test_an_owed_friend_left_out_or_twice_and_a_stranger_are_still_refused() -> None:
    moment = _zero_ip()
    facts_of = ev.candidates(moment, Ledger())
    good = asyncio.run(Script().one([Message("user", [ev.one_agent_request(moment, facts_of)])]))[0]
    assert good is not None
    assert ev.one_agent_problems(good, moment, facts_of) == []
    by_id = {e.manager_id: e for e in good.explanations}
    left_out = good.model_copy(update={"explanations": [by_id["mgr_2"], by_id["mgr_3"]]})
    assert ev.one_agent_problems(left_out, moment, facts_of) == ["write exactly one explanation for mgr_1"]
    twice = good.model_copy(update={"explanations": [*good.explanations, by_id["mgr_2"]]})
    assert ev.one_agent_problems(twice, moment, facts_of) == ["write exactly one explanation for mgr_2"]
    stranger = by_id["mgr_3"].model_copy(update={"manager_id": "mgr_4"})  # below the threshold here
    extra = good.model_copy(update={"explanations": [*good.explanations, stranger]})
    assert ev.one_agent_problems(extra, moment, facts_of) == ["mgr_4 is not in MANAGERS"]


# 6
async def test_a_friend_worth_0_ip_can_still_be_picked_on_a_line_that_passes() -> None:
    moment = _zero_ip()
    one = _screened(ev.OneAgent(Script(pick="mgr_3").one))
    request = json.loads(ev.one_agent_request(moment, ev.candidates(moment, one.ledger), one.ledger))
    assert [b["manager_id"] for b in request["MANAGERS"]] == ["mgr_1", "mgr_2", "mgr_3"]
    outcome = await one.decide(moment)
    assert (outcome.chosen, outcome.pick, one.meter.calls) == ("mgr_3", "model", 1)
    assert outcome.statuses == {"mgr_1": "ok", "mgr_2": "ok"}  # still owed only to the friends worth points


# 6b: ours picks a 0 IP friend only on a bid that passed its checks, so the one agent must have a line too
@pytest.mark.parametrize("script", [Script("mgr_3", (1, 2), pick="mgr_3"), Script(omit="mgr_3", pick="mgr_3")],
                         ids=["bad line", "no line"])  # fmt: skip
async def test_a_friend_worth_0_ip_is_not_picked_without_a_line_that_passes(script: Script) -> None:
    moment = _zero_ip()
    one = _screened(ev.OneAgent(script.one))
    outcome = await one.decide(moment)
    assert (outcome.chosen, outcome.pick) == (ev.biggest_swing(moment), "fallback")
    assert outcome.statuses == {"mgr_1": "retried", "mgr_2": "retried"}  # the owed lines of the second attempt
    assert one.meter.calls == 2


# 7
async def test_salvage_keeps_the_owed_lines_that_pass_and_ignores_a_bad_0_ip_line() -> None:
    moment = _zero_ip()
    one = ev.OneAgent(Script(("mgr_1", "mgr_3"), (1, 2)).one)
    outcome = await one.decide(moment)
    assert outcome.statuses == {"mgr_1": "fallback", "mgr_2": "retried"}
    assert (outcome.pick, one.meter.calls) == ("model", 2)


def test_the_one_agent_is_told_a_line_for_a_friend_worth_0_ip_is_optional() -> None:
    assert "for each manager in MANAGERS whose POINTS is not 0" in ev.ONE_AGENT_INSTRUCTIONS
    assert "only if you wrote their item" in ev.ONE_AGENT_INSTRUCTIONS
