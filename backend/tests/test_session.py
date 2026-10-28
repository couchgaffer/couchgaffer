import asyncio
import logging
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from typing import Any

import pytest
from agent_framework import Workflow
from fastapi.testclient import TestClient
from opentelemetry.trace import StatusCode
from starlette.websockets import WebSocketDisconnect

from tests.conftest import ClientFactory, drain
from watchparty.agents.host import true_factors
from watchparty.app import session as session_module
from watchparty.app import workflow
from watchparty.app.cues import phone_headline, points_headline, stat_headline
from watchparty.app.main import create_app
from watchparty.app.session import MomentState, Session, SessionStore
from watchparty.points import PointsEngine

pytestmark = pytest.mark.anyio


async def no_sleep(_: float) -> None:
    await asyncio.sleep(0)


class Manager:
    """A fake manager agent: answers from the stake's evidence, optionally slowly."""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay, self.calls = delay, 0

    async def bid(self, stake: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        await asyncio.sleep(self.delay)
        if not stake["delta_points"]:  # as the real agent may, and must rather than say "0 IP" (#88)
            return {"status": "declined"}
        delta = next(e for e in stake["evidence"] if e["id"].endswith("_delta"))
        return {"urgency": 0.7, "explanation": f"That is {delta['value']:g} IP for you.", "explanation_en": "",
                "pitch_to_host": "Show my moment.", "evidence_ids": [delta["id"]]}  # fmt: skip


class Host:
    """A fake host agent: overrules the scorer by picking its last candidate (or follows it), or answers `pick`."""

    def __init__(self, delay: float = 0.0, pick: str | None = None, overrule: bool = True,
                 reason: str = "Close call; fair turn.", because: str | None = None) -> None:  # fmt: skip
        self.delay, self.pick, self.overrule, self.reason, self.calls = delay, pick, overrule, reason, 0
        self.because = because  # None: a fact that is true of the pick, as the real host is made to give
        self.seen: list[list[dict[str, Any]]] = []

    async def decide(self, moment: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls += 1
        self.seen.append(candidates)
        await asyncio.sleep(self.delay)
        # Overruling, the last candidate with a fact true of it: the first always has one (scorer_pick)
        backed = [c["manager"] for c in candidates if true_factors(c["manager"], candidates)]
        chosen = self.pick or (backed[-1] if self.overrule else candidates[0]["manager"])
        true = sorted(true_factors(chosen, candidates)) if chosen in {c["manager"] for c in candidates} else []
        because = self.because or (true[0] if true else "dead_heat")
        return {"chosen_manager": chosen, "because": because, "reason": self.reason}


def session(manager: Manager | None = None, host: Host | None = None, seed: int = 267, **kw: Any) -> Session:
    return Session(seed=seed, manager="mgr_1", style="casual", lang="en", speed=30, sleep=no_sleep,
                   manager_agent=manager or Manager(), host_agent=host or Host(), **kw)  # fmt: skip


async def run(s: Session) -> dict[str, list[dict[str, Any]]]:
    """Run a whole session, recording what every screen receives."""
    screens = ["group"] + [f"phone:mgr_{n}" for n in range(1, 6)]
    queues = {screen: s.subscribe(screen) for screen in screens}
    await s.run()
    got: dict[str, list[dict[str, Any]]] = {}
    for screen, q in queues.items():
        got[screen] = drain(q)
    return got


def cues(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [m["data"] for m in messages if m["type"] == "cue"]


async def test_the_moment_pipeline_is_an_agent_framework_workflow() -> None:
    s = session()
    assert isinstance(s.workflow, Workflow)
    ids = {e.id for e in s.workflow.executors.values()}
    assert {"start", "scorer", "host", "decided"} | {f"manager_mgr_{n}" for n in range(1, 6)} <= ids


async def test_a_whole_demo_session_streams_contract_valid_messages(
    contract_errors: Callable[[str, Any], list[str]],
) -> None:
    got = await run(session())
    for screen, messages in got.items():
        assert messages[0]["type"] == "meta"
        assert messages[1]["type"] == "league"
        for m in messages:
            assert contract_errors("ws-message", m) == [], (screen, m)
        for cue in cues(messages):
            assert cue["screen"] == screen
    assert any(m["type"] == "decision" for m in got["group"])
    assert sum(m["type"] == "event" and m["data"]["type"] == "full_time" for m in got["group"]) == 1


async def test_cues_are_sequenced_timed_and_upgrade_the_stat_card() -> None:
    got = await run(session())
    every = sorted((c for msgs in got.values() for c in cues(msgs)), key=lambda c: c["seq"])
    assert [c["seq"] for c in every] == list(range(len(every)))
    by_id = {c["cue_id"]: c for c in every}
    upgrades = [c for c in every if "replaces" in c]
    assert upgrades
    for c in upgrades:
        stat = by_id[c["replaces"]]
        assert stat["kind"] == "stat"
        assert stat["seq"] < c["seq"]
        assert stat["show_at"] == c["show_at"]
        assert stat["screen"] == c["screen"]
    # a clash holds its pitches about a real second before the verdict (#89); every other group cue holds 6 s
    assert all(c["min_dwell_ms"] == (1000 if c["kind"] == "clash" else 6000) for c in every if c["screen"] == "group")
    assert any(c["kind"] == "clash" for c in every)  # the demo has close calls


async def test_the_judges_style_and_language_override_the_fixture() -> None:
    s = Session(seed=267, manager="mgr_3", style="analyst", lang="es", speed=30, sleep=no_sleep,
                manager_agent=Manager(), host_agent=Host())  # fmt: skip
    mgr_3 = next(m for m in s.league["managers"] if m["id"] == "mgr_3")
    assert (mgr_3["style"], mgr_3["lang"], mgr_3["is_bot"]) == ("analyst", "es", False)
    assert sum(not m["is_bot"] for m in s.league["managers"]) == 1


async def test_a_slow_manager_falls_back_on_time_and_the_match_goes_on() -> None:
    slow = Manager(delay=5)
    s = session(manager=slow, manager_deadline_s=0.05)
    start = time.perf_counter()
    got = await run(s)
    assert time.perf_counter() - start < 3  # 14 moments, each bounded by the deadline, not by the agent
    phone = [c for msgs in got.values() for c in cues(msgs) if c["kind"] == "explanation" and c["screen"] != "group"]
    assert phone
    assert all(c["source"] == "fallback" for c in phone)


async def test_simulated_failure_falls_back_once_then_recovers() -> None:
    manager = Manager()
    s = session(manager=manager)
    s.simulate_failure()
    got = await run(s)
    shown = [c for c in cues(got["group"]) if c["kind"] == "explanation"]
    first, rest = shown[0], shown[1:]
    assert first["source"] == "fallback"  # the group card says so, whoever decided (#86: a clear call is "scorer")
    assert any(c["source"] != "fallback" for c in rest)
    assert manager.calls > 0
    decisions = [m["data"] for m in got["group"] if m["type"] == "decision"]
    assert decisions[0]["source"] == ("fallback" if decisions[0]["close_call"] else "scorer")


def stakes_for(s: Session, deltas: dict[str, float], moment_id: str = "m_02") -> list[dict[str, Any]]:
    """Real stakes for one moment, with chosen swings; the rest below the threshold."""
    engine = PointsEngine(s.match.meta, s.league)
    real = next(st for e in s.match.events if (st := engine.feed(e)) and st[0]["moment_id"] == moment_id)
    out = []
    for stake in real:
        delta = deltas.get(stake["manager"], 0)
        evidence = [e | {"value": delta} if e["id"].endswith("_delta") else e for e in stake["evidence"]]
        out.append(stake | {"delta_points": delta, "above_threshold": stake["manager"] in deltas, "evidence": evidence})
    return out


async def test_close_calls_go_to_the_host_and_clear_winners_do_not() -> None:
    host = Host()
    s = session(host=host)
    close = await s.decide(stakes_for(s, {"mgr_1": 6, "mgr_2": 6}))
    clear = await s.decide(stakes_for(s, {"mgr_1": 8, "mgr_2": 2}))
    alone = await s.decide(stakes_for(s, {"mgr_3": 4}))
    assert close is not None
    assert clear is not None
    assert alone is not None
    assert (close["source"], clear["source"], alone["source"]) == ("host", "scorer", "scorer")
    assert host.calls == 1
    assert close["reason"]
    assert clear["chosen_manager"] == "mgr_1"


async def test_nobody_at_stake_leaves_the_stat_card_up() -> None:
    s = session()
    assert await s.decide(stakes_for(s, {})) is None


async def test_a_slow_host_falls_back_to_the_scorers_pick_on_time() -> None:
    s = session(host=Host(delay=5), host_deadline_s=0.05)
    start = time.perf_counter()
    result = await s.decide(stakes_for(s, {"mgr_1": 6, "mgr_2": 6}))
    assert time.perf_counter() - start < 1
    assert result is not None
    assert result["source"] == "fallback"
    assert result["chosen_manager"] == result["scores"][0]["manager"]


async def test_a_host_with_too_little_time_left_is_not_called() -> None:
    # #26, scaled down tenfold: bids at 3.9 s of 4.0 leave under a quarter of the host's 2.5 s in a 4.5 s budget
    host = Host(delay=0.24)
    s = session(manager=Manager(delay=0.39), host=host, manager_deadline_s=0.4, host_deadline_s=0.25,
                decision_budget_s=0.45)  # fmt: skip
    start = time.perf_counter()
    result = await s.decide(stakes_for(s, {"mgr_1": 6, "mgr_2": 6}))
    assert time.perf_counter() - start < 0.45 + 0.03  # the screens show the moment 5 s after it happened
    assert result is not None
    assert (result["source"], result["chosen_manager"]) == ("fallback", result["scores"][0]["manager"])
    assert host.calls == 0  # a call that would almost surely be cut off is not made


async def test_a_host_already_thinking_is_cut_off_at_the_shared_deadline() -> None:
    # #48: bids take 0.30 of a 0.45 budget, so the host starts with 0.15 left, but needs 0.24
    host = Host(delay=0.24)
    s = session(manager=Manager(delay=0.30), host=host, manager_deadline_s=0.4, host_deadline_s=0.25,
                decision_budget_s=0.45)  # fmt: skip
    start = time.perf_counter()
    result = await s.decide(stakes_for(s, {"mgr_1": 6, "mgr_2": 6}))
    assert time.perf_counter() - start < 0.45 + 0.03  # not 0.30 + 0.24: the host did not get its own 0.25
    assert host.calls == 1  # it started
    assert result is not None
    assert result["source"] == "fallback"


async def test_the_host_gets_the_time_the_bids_left() -> None:
    s = session(manager=Manager(delay=0.1), host=Host(delay=0.1), manager_deadline_s=0.4, host_deadline_s=0.25,
                decision_budget_s=0.45)  # fmt: skip
    result = await s.decide(stakes_for(s, {"mgr_1": 6, "mgr_2": 6}))
    assert result is not None
    assert result["source"] == "host"


# --- moments in flight together (#149) -------------------------------------------------------------------
# The simulator keeps real moments 10 s apart (test_sim MIN_GAP), so these are the worst case, not the usual. Times
# are scaled down tenfold: bids 4.0 s, host 2.5 s, budget 4.5 s.


class SlowOn(Manager):
    """A fake manager agent that takes `delays[moment_id]` seconds on that moment, and no time on any other."""

    def __init__(self, delays: dict[str, float]) -> None:
        super().__init__()
        self.delays = delays

    async def bid(self, stake: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(self.delays.get(stake["moment_id"], 0))
        return await super().bid(stake, context)


class Arrivals:
    """Key moments reaching the server as the match feeds them: each takes its turn and starts deciding at once."""

    def __init__(self, s: Session) -> None:
        self.s = s
        self.deadlines: dict[str, float] = {}
        self.late: dict[str, float] = {}  # moment -> seconds past its own deadline when it was decided
        self.tasks: dict[str, asyncio.Task[dict[str, Any] | None]] = {}

    def arrive(self, mid: str, deltas: dict[str, float]) -> None:
        self.deadlines[mid] = time.monotonic() + self.s.decision_budget_s
        moment = self.s._arrive(stakes_for(self.s, deltas, mid), self.deadlines[mid])
        self.tasks[mid] = asyncio.create_task(self._timed(moment))

    async def _timed(self, moment: MomentState) -> dict[str, Any] | None:
        result = await self.s._decide(moment)
        self.late[moment.moment_id] = time.monotonic() - self.deadlines[moment.moment_id]
        return result

    async def results(self) -> dict[str, dict[str, Any] | None]:
        done = await asyncio.wait_for(asyncio.gather(*self.tasks.values(), return_exceptions=True), 3)
        return dict(zip(self.tasks, done, strict=True))  # type: ignore[arg-type]


async def test_a_second_moment_bids_on_arrival_and_both_are_decided_before_they_are_shown() -> None:
    # #94 case 12: 1 s apart, scaled. Decided one after the other, the second's 3.8 s bids would start 3.8 s late
    # and be cut off at its deadline; started on arrival, they get their full time.
    s = session(manager=Manager(delay=0.38), manager_deadline_s=0.4, decision_budget_s=0.45)
    moments = Arrivals(s)
    moments.arrive("m_02", {"mgr_1": 6})
    await asyncio.sleep(0.1)
    moments.arrive("m_03", {"mgr_2": 6})
    got = await moments.results()
    assert {mid: statuses(result) for mid, result in got.items()} == {"m_02": {"mgr_1": "ok"},
                                                                       "m_03": {"mgr_2": "ok"}}  # fmt: skip
    assert all(late < 0.03 for late in moments.late.values()), moments.late  # each before its own deadline
    assert len(s.idle) == 2  # one workflow cannot run twice at once: the second moment had one of its own
    assert s.moments == {}  # dropped once decided


async def test_moments_overlapping_in_a_whole_match_each_get_their_bid_time() -> None:
    # Played with no pause, every moment of the demo arrives at once. Each bid takes 0.3 s of a 1 s budget: one
    # after the other, the fourth moment's bids would start past 0.9 s and fall back.
    s = session(manager=Manager(delay=0.3), manager_deadline_s=0.4, decision_budget_s=1.0)
    start = time.perf_counter()
    got = await run(s)
    assert time.perf_counter() - start < 2
    phone = [c for msgs in got.values() for c in cues(msgs) if c["kind"] == "explanation" and c["screen"] != "group"]
    assert len({c["moment_id"] for c in phone}) >= 10
    assert {c["source"] for c in phone} == {"manager"}


async def test_the_scorer_host_and_ledger_take_their_turn_in_arrival_order() -> None:
    # m_02 arrives first but bids slowly; m_03's bids are in at once. m_03 is ranked only once m_02 is in the
    # ledger, so mgr_1's airtime from m_02 counts against it on m_03.
    s = session(manager=SlowOn({"m_02": 0.2}))
    recorded: list[str] = []
    real = s.ledger.record

    def record(chosen: str, candidates: list[str], *, close: bool) -> None:
        recorded.append(chosen)
        real(chosen, candidates, close=close)

    s.ledger.record = record  # type: ignore[method-assign]
    moments = Arrivals(s)
    moments.arrive("m_02", {"mgr_1": 8, "mgr_2": 2})
    await asyncio.sleep(0.05)
    moments.arrive("m_03", {"mgr_1": 6, "mgr_3": 6})
    got = await moments.results()
    assert recorded == ["mgr_1", got["m_03"]["chosen_manager"]]  # type: ignore[index]
    assert [d["moment_id"] for d in s.log if "chosen_manager" in d] == ["m_02", "m_03"]
    row = {r["manager"]: r for r in got["m_03"]["scores"]}  # type: ignore[index]
    assert row["mgr_1"]["airtime_share"] == 1.0


async def test_a_turn_that_comes_with_under_a_quarter_of_the_hosts_time_left_goes_to_the_scorer() -> None:
    # #94 case 14. m_02's bids take 0.2 s and its host is cut off at its 0.45 s deadline; m_03 arrives 0.01 s
    # later, so its turn comes with about 0.01 s left of its own 0.45, under a quarter of the host's 0.25.
    host = Host(delay=0.24)
    s = session(manager=SlowOn({"m_02": 0.2}), host=host, manager_deadline_s=0.4, host_deadline_s=0.25,
                decision_budget_s=0.45)  # fmt: skip
    moments = Arrivals(s)
    moments.arrive("m_02", {"mgr_1": 6, "mgr_2": 6})
    await asyncio.sleep(0.01)
    moments.arrive("m_03", {"mgr_3": 6, "mgr_4": 6})
    got = await moments.results()
    assert host.calls == 1  # m_02's: a call for m_03 would almost surely be cut off, so none is made
    m_03 = got["m_03"]
    assert m_03 is not None
    assert (m_03["source"], m_03["chosen_manager"]) == ("fallback", m_03["scores"][0]["manager"])
    assert all(late < 0.03 for late in moments.late.values()), moments.late  # before the screens show it


async def test_two_moments_in_flight_never_see_each_others_bids_stakes_or_evidence() -> None:
    # #94 case 16
    host = Host()
    s = session(manager=Manager(delay=0.05), host=host)
    queues = close_call_screens(s)
    moments = Arrivals(s)
    moments.arrive("m_02", {"mgr_1": 6, "mgr_2": 6})
    moments.arrive("m_03", {"mgr_3": 4, "mgr_4": 4})
    assert set(s.moments) == {"m_02", "m_03"}
    got = await moments.results()
    assert {mid: {r["manager"] for r in d["scores"]} for mid, d in got.items() if d} == {
        "m_02": {"mgr_1", "mgr_2"}, "m_03": {"mgr_3", "mgr_4"}}  # fmt: skip
    assert [{c["manager"] for c in seen} for seen in host.seen] == [{"mgr_1", "mgr_2"}, {"mgr_3", "mgr_4"}]
    phones = drained_cues(queues)
    for screen, mid, headline in (("phone:mgr_1", "m_02", "+6 IP"), ("phone:mgr_3", "m_03", "+4 IP")):
        [explanation] = [c for c in phones[screen] if c["kind"] == "explanation"]
        assert (explanation["moment_id"], explanation["payload"]["headline"]) == (mid, headline)
        assert explanation["payload"]["evidence"]
        assert all(e["id"].startswith(f"ev_{mid}_") for e in explanation["payload"]["evidence"])
    assert s.moments == {}


async def test_a_moment_that_fails_ends_its_turn_and_the_next_is_still_decided(monkeypatch: pytest.MonkeyPatch) -> None:
    s = session()
    arm = s.simulate_failure()  # m_02 runs under it, so the press must outlive m_02's failure
    real = s._run

    async def m_02_fails(moment: workflow.MomentInput) -> list[Any]:
        if moment.moment_id == "m_02":
            await asyncio.sleep(0.05)  # m_03 is waiting for its turn by now
            raise RuntimeError("a transient fault")
        return await real(moment)

    monkeypatch.setattr(s, "_run", m_02_fails)
    moments = Arrivals(s)
    moments.arrive("m_02", {"mgr_1": 6})
    moments.arrive("m_03", {"mgr_2": 6})
    got = await moments.results()
    assert isinstance(got["m_02"], RuntimeError)
    assert got["m_03"] is not None
    assert "knocked_out" not in got["m_03"]  # it arrived while m_02 held the press
    assert s.moments == {}
    assert s.chaos == "all"  # m_02 never spent it: the next moment that arrives does
    moments.arrive("m_04", {"mgr_3": 6})
    later = (await moments.results())["m_04"]
    assert later is not None
    assert later["knocked_out"] == {"arm": arm}
    assert s.chaos is None


async def test_nobody_at_stake_in_the_first_moment_does_not_hold_up_the_second() -> None:
    s = session(manager=SlowOn({"m_03": 0.05}))
    moments = Arrivals(s)
    moments.arrive("m_02", {})
    moments.arrive("m_03", {"mgr_1": 6})
    got = await moments.results()
    assert got["m_02"] is None
    assert got["m_03"] is not None


async def test_chaos_hits_only_the_first_of_two_moments_in_flight() -> None:
    manager = Recording()
    s = session(manager=manager)
    arm = s.simulate_failure()
    moments = Arrivals(s)
    moments.arrive("m_02", {"mgr_1": 8, "mgr_2": 2})
    moments.arrive("m_03", {"mgr_3": 8})
    got = await moments.results()
    assert statuses(got["m_02"]) == {"mgr_1": "fallback", "mgr_2": "fallback"}
    assert got["m_02"]["knocked_out"] == {"arm": arm}  # type: ignore[index]
    assert statuses(got["m_03"]) == {"mgr_3": "ok"}
    assert "knocked_out" not in got["m_03"]  # type: ignore[operator]
    assert manager.asked == ["mgr_3"]
    assert s.chaos is None


async def test_a_knockout_passes_over_a_moment_worth_nothing_to_that_friend_while_it_is_in_flight() -> None:
    # mgr_4 is above the threshold on m_02 but for 0 IP: its plain stat would be a decline, so m_02 does not spend
    # the press, and m_03, arriving while m_02 is in flight, does.
    s = session(manager=Manager(delay=0.05))
    arm = s.simulate_failure("mgr_4")
    moments = Arrivals(s)
    moments.arrive("m_02", {"mgr_4": 0, "mgr_1": 6})
    moments.arrive("m_03", {"mgr_4": 8, "mgr_2": 2})
    got = await moments.results()
    assert statuses(got["m_02"]) == {"mgr_1": "ok"}
    assert "knocked_out" not in got["m_02"]  # type: ignore[operator]
    assert statuses(got["m_03"]) == {"mgr_4": "fallback", "mgr_2": "ok"}
    assert got["m_03"]["knocked_out"] == {"arm": arm, "manager": "mgr_4"}  # type: ignore[index]
    assert s.chaos is None


def publish_fails_on(monkeypatch: pytest.MonkeyPatch, moment_id: str) -> None:
    """A real exception inside that moment's workflow run: its first bid's phone card cannot be made."""
    real = phone_headline

    def headline(stake: dict[str, Any], lang: str) -> str:
        if stake["moment_id"] == moment_id:
            raise RuntimeError("a transient fault")
        return real(stake, lang)

    monkeypatch.setattr(session_module, "phone_headline", headline)


async def test_a_run_that_raised_does_not_stop_later_moments_being_decided(monkeypatch: pytest.MonkeyPatch) -> None:
    # A workflow whose run raised keeps messages in flight and refuses every later run: it must not be reused.
    # One moment at a time, as real moments 10 s apart are, so a workflow returned to the pool would be the next one
    # used.
    s = session()
    publish_fails_on(monkeypatch, "m_02")
    with pytest.raises(RuntimeError, match="a transient fault"):
        await s.decide(stakes_for(s, {"mgr_1": 6}, "m_02"))
    later = [await s.decide(stakes_for(s, {"mgr_1": 6}, mid)) for mid in ("m_03", "m_04")]
    assert [(d["moment_id"], d["source"]) for d in later if d] == [("m_03", "scorer"), ("m_04", "scorer")]
    assert statuses(later[1]) == {"mgr_1": "ok"}  # the agent's bid, through the workflow


async def test_a_turn_waits_for_every_earlier_moment_even_past_one_that_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A bids slowly, B fails at once, C's bids are in at once. C waits on B's turn, and B's turn ends only once
    # A's has: C is ranked on a ledger that has A in it.
    s = session(manager=SlowOn({"m_02": 0.2}))
    publish_fails_on(monkeypatch, "m_03")
    moments = Arrivals(s)
    moments.arrive("m_02", {"mgr_1": 8, "mgr_2": 2})
    moments.arrive("m_03", {"mgr_3": 6})
    moments.arrive("m_04", {"mgr_1": 6, "mgr_4": 6})
    got = await moments.results()
    assert isinstance(got["m_03"], RuntimeError)
    assert [d["moment_id"] for d in s.log if "chosen_manager" in d] == ["m_02", "m_04"]
    row = {r["manager"]: r for r in got["m_04"]["scores"]}
    assert row["mgr_1"]["airtime_share"] == 1.0


async def test_two_moments_in_flight_with_one_id_are_refused() -> None:
    s = session(manager=Manager(delay=0.05))
    moments = Arrivals(s)
    moments.arrive("m_02", {"mgr_1": 6})
    with pytest.raises(ValueError, match="already in flight"):
        moments.arrive("m_02", {"mgr_2": 6})
    got = await moments.results()
    assert statuses(got["m_02"]) == {"mgr_1": "ok"}
    assert s.moments == {}


async def test_a_bid_for_a_moment_no_longer_in_flight_goes_nowhere() -> None:
    s = session()
    s._on_bid({"moment_id": "m_02", "manager": "mgr_1", "status": "ok"})
    assert s.log == []


async def test_a_full_store_says_busy_and_ids_are_unguessable() -> None:
    store = SessionStore(max_sessions=1, session_kwargs={"sleep": no_sleep, "manager_agent": Manager(),
                                                          "host_agent": Host()})  # fmt: skip
    first = store.create(seed=267, manager="mgr_1", style="casual", lang="en", client="1.2.3.4")
    assert first is not None
    assert len(first.id) >= 16
    assert len(first.phone_token) >= 16
    assert first.id != first.phone_token
    assert store.create(seed=1, manager="mgr_1", style="casual", lang="en", client="5.6.7.8") is None


async def test_one_client_cannot_take_every_seat() -> None:
    store = SessionStore(max_sessions=10, per_client=2, session_kwargs={"sleep": no_sleep})
    made = []
    for _ in range(3):
        session = store.create(seed=1, manager="mgr_1", style="casual", lang="en", client="1.2.3.4")
        if session is not None:
            session.subscribe("group")  # watched, so it holds its seat (an unopened one is replaced, #50)
        made.append(session)
    assert [m is not None for m in made] == [True, True, False]


async def test_session_api_validates_input(client_for: ClientFactory) -> None:
    async with client_for(create_app(session_kwargs={"sleep": no_sleep})) as client:
        ok = await client.post(
            "/api/sessions", json={"seed": "demo", "manager": "mgr_2", "style": "analyst", "lang": "en"}
        )
        assert ok.status_code == 201
        assert set(ok.json()) == {"session_id", "phone_token", "manager"}
        for bad in (
            {"seed": "demo", "manager": "mgr_9"},
            {"seed": -1, "manager": "mgr_1"},
            {"seed": "demo", "manager": "mgr_1", "lang": "<script>"},
            {"seed": "demo", "manager": "mgr_1", "x": 1},
        ):
            assert (await client.post("/api/sessions", json=bad)).status_code == 422, bad  # fmt: skip
        assert (await client.post("/api/sessions/nope/chaos")).status_code == 404


def test_websockets_need_the_session_and_phones_need_the_token() -> None:
    app = create_app(session_kwargs={"sleep": no_sleep, "manager_agent": Manager(), "host_agent": Host()})
    with TestClient(app) as client:
        made = client.post("/api/sessions", json={"seed": "demo", "manager": "mgr_1"}).json()
        sid, token = made["session_id"], made["phone_token"]
        for path in ("/ws/nope/group", f"/ws/{sid}/phone:mgr_1?token=wrong", f"/ws/{sid}/phone:mgr_1",
                     f"/ws/{sid}/nonsense"):  # fmt: skip
            with pytest.raises(WebSocketDisconnect), client.websocket_connect(path) as ws:
                ws.receive_json()
        with client.websocket_connect(f"/ws/{sid}/phone:mgr_1?token={token}") as ws:
            assert ws.receive_json()["type"] == "meta"
        with client.websocket_connect(f"/ws/{sid}/group") as ws:
            assert ws.receive_json()["type"] == "meta"


async def test_the_demo_sends_the_host_a_minority_of_moments_and_only_contested_ones() -> None:
    host = Host()
    got = await run(session(host=host))
    decisions = [m["data"] for m in got["group"] if m["type"] == "decision"]
    by_host = [d for d in decisions if d["source"] == "host"]
    assert 3 <= len(by_host) <= 6 < len(decisions)
    assert host.calls == len(by_host)
    assert all(len(d["scores"]) >= 2 for d in by_host)
    reasons = [c for c in cues(got["group"]) if c["kind"] == "host_reason"]
    assert [c["moment_id"] for c in reasons] == [d["moment_id"] for d in by_host]


async def test_every_manager_gets_the_group_screen_in_the_demo() -> None:
    got = await run(session(host=Host(overrule=False)))
    shown = {m["data"]["chosen_manager"] for m in got["group"] if m["type"] == "decision"}
    assert shown == {f"mgr_{n}" for n in range(1, 6)}


async def test_the_host_sees_each_candidates_pitch_points_and_ledger_in_scorer_order() -> None:
    host = Host()
    s = session(host=host)
    await s.decide(stakes_for(s, {"mgr_1": 6, "mgr_2": 6}))
    [candidates] = host.seen
    assert [c["manager"] for c in candidates] == ["mgr_1", "mgr_2"]
    assert candidates[1] == {"manager": "mgr_2", "name": "Priya", "pitch": "Show my moment.", "impact_points": 6,
                             "airtime_share": 0.0, "overruled_count": 0, "angle": None}  # fmt: skip


async def test_the_ledger_remembers_who_was_overruled() -> None:
    s = session()
    first = await s.decide(stakes_for(s, {"mgr_1": 6, "mgr_2": 6}))
    assert first is not None
    assert first["chosen_manager"] == "mgr_2"  # the fake host overrules the scorer
    second = await s.decide(stakes_for(s, {"mgr_1": 6, "mgr_2": 6}))
    assert second is not None
    row = {r["manager"]: r for r in second["scores"]}
    assert (row["mgr_1"]["overruled_count"], row["mgr_2"]["airtime_share"]) == (1, 1.0)
    assert second["scores"][0]["manager"] == "mgr_1"


async def test_only_close_calls_overrule_anyone() -> None:
    s = session()
    clear = await s.decide(stakes_for(s, {"mgr_1": 9, "mgr_2": 2}))
    assert clear is not None
    assert (clear["chosen_manager"], clear["source"], clear["close_call"]) == ("mgr_1", "scorer", False)
    after = await s.decide(stakes_for(s, {"mgr_1": 6, "mgr_2": 6}))
    assert after is not None
    assert after["close_call"]
    row = {r["manager"]: r for r in after["scores"]}
    assert row["mgr_2"]["overruled_count"] == 0  # losing a clear call is not being overruled (#27)


async def test_a_host_pick_outside_the_bidders_falls_back_to_the_scorer() -> None:
    s = session(host=Host(pick="mgr_5"))
    result = await s.decide(stakes_for(s, {"mgr_1": 6, "mgr_2": 6}))
    assert result is not None
    assert (result["source"], result["chosen_manager"]) == ("fallback", "mgr_1")


async def test_a_blank_host_reason_falls_back_to_the_scorer() -> None:
    s = session(host=Host(reason="   "))  # #53
    result = await s.decide(stakes_for(s, {"mgr_1": 6, "mgr_2": 6}))
    assert result is not None
    assert (result["source"], result["chosen_manager"]) == ("fallback", "mgr_1")


async def test_a_simulated_failure_on_a_close_call_skips_the_host_and_shows_the_fallback() -> None:
    host = Host()
    s = session(host=host)
    s.simulate_failure()
    queue = s.subscribe("group")
    result = await s.decide(stakes_for(s, {"mgr_1": 6, "mgr_2": 6}))
    assert result is not None
    assert result["source"] == "fallback"
    assert host.calls == 0
    shown = drain(queue)
    group = [m["data"] for m in shown if m["type"] == "cue"]
    assert [(c["kind"], c["source"]) for c in group] == [("explanation", "fallback"), ("clash", "fallback")]


# --- one friend's agent knocked out (#90) --------------------------------------------------------------


class Recording(Manager):
    """A fake manager agent that remembers whose bids it was asked for."""

    def __init__(self) -> None:
        super().__init__()
        self.asked: list[str] = []

    async def bid(self, stake: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        self.asked.append(context["manager"]["id"])
        return await super().bid(stake, context)


def statuses(decision: dict[str, Any] | None) -> dict[str, str]:
    """A decision's bids: whose came from an agent ("ok") and whose from code ("fallback")."""
    assert decision is not None
    return {row["manager"]: row["status"] for row in decision["scores"]}


async def test_a_knockout_skips_only_that_friends_agent_and_the_others_carry_on(
    contract_errors: Callable[[str, Any], list[str]],
) -> None:
    manager = Recording()
    s = session(manager=manager)
    arm = s.simulate_failure("mgr_2")
    queues = close_call_screens(s)
    result = await s.decide(stakes_for(s, {"mgr_2": 2, "mgr_3": 8}))
    assert result is not None
    assert result["knocked_out"] == {"arm": arm, "manager": "mgr_2"}  # the page clears its label on this
    assert contract_errors("host-decision", result) == []
    assert statuses(result) == {"mgr_2": "fallback", "mgr_3": "ok"}
    assert manager.asked == ["mgr_3"]
    assert (result["source"], result["chosen_manager"]) == ("scorer", "mgr_3")  # a clear winner, decided as usual
    got = drained_cues(queues)
    phones = {m: [c["source"] for c in got[f"phone:{m}"] if c["kind"] == "explanation"] for m in ("mgr_2", "mgr_3")}
    assert phones == {"mgr_2": ["fallback"], "mgr_3": ["manager"]}
    group = [c["source"] for c in got["group"] if c["kind"] == "explanation"]
    assert group == ["scorer"]  # mgr_3's agent card is up: nothing on the group screen fell back
    assert s.chaos is None  # spent: mgr_2 bid in this moment


async def test_a_knocked_out_friend_who_wins_shows_as_a_fallback_on_the_group_screen() -> None:
    s = session()
    s.simulate_failure("mgr_2")
    queues = close_call_screens(s)
    result = await s.decide(stakes_for(s, {"mgr_2": 8, "mgr_3": 2}))
    assert result is not None
    assert (result["chosen_manager"], result["source"]) == ("mgr_2", "scorer")
    group = [c["source"] for c in drained_cues(queues)["group"] if c["kind"] == "explanation"]
    assert group == ["fallback"]  # the card is mgr_2's plain stat


async def test_a_knockout_on_a_close_call_still_asks_the_host() -> None:
    host = Host()
    s = session(host=host)
    s.simulate_failure("mgr_2")
    result = await s.decide(stakes_for(s, {"mgr_2": 6, "mgr_3": 6}))
    assert result is not None
    assert statuses(result) == {"mgr_2": "fallback", "mgr_3": "ok"}
    assert host.calls == 1
    assert (result["source"], result["close_call"]) == ("host", True)


async def test_a_knockout_waits_for_a_moment_that_friend_bids_in() -> None:
    manager = Recording()
    s = session(manager=manager)
    arm = s.simulate_failure("mgr_4")
    first = await s.decide(stakes_for(s, {"mgr_1": 8, "mgr_2": 2}))
    assert first is not None
    assert "knocked_out" not in first
    assert statuses(first) == {"mgr_1": "ok", "mgr_2": "ok"}
    assert s.chaos == "mgr_4"  # still armed: mgr_4 had nothing at stake
    second = await s.decide(stakes_for(s, {"mgr_4": 8, "mgr_1": 2}))
    assert second is not None
    assert second["knocked_out"] == {"arm": arm, "manager": "mgr_4"}
    assert statuses(second) == {"mgr_4": "fallback", "mgr_1": "ok"}
    assert sorted(manager.asked) == ["mgr_1", "mgr_1", "mgr_2"]
    assert s.chaos is None


def press_during_the_run(s: Session, monkeypatch: pytest.MonkeyPatch, manager: str) -> list[int]:
    """Press "knock out" for `manager` while the next moment is being decided; the arm id lands in the list."""
    real, arms = s.workflow.run, list[int]()

    async def press_during(moment: Any) -> Any:
        arms.append(s.simulate_failure(manager))
        return await real(moment)

    monkeypatch.setattr(s.workflow, "run", press_during)
    return arms


async def test_a_knockout_pressed_while_another_is_spent_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    s = session()
    first = s.simulate_failure("mgr_2")
    later = press_during_the_run(s, monkeypatch, "mgr_3")
    result = await s.decide(stakes_for(s, {"mgr_2": 8, "mgr_3": 2}))
    assert result is not None
    assert result["knocked_out"] == {"arm": first, "manager": "mgr_2"}
    assert statuses(result) == {"mgr_2": "fallback", "mgr_3": "ok"}  # this moment started with mgr_2 knocked out
    assert s.armed is not None
    assert (s.armed.arm, s.armed.manager) == (later[0], "mgr_3")


async def test_a_second_press_for_the_same_friend_during_their_moment_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    s = session()
    first = s.simulate_failure("mgr_2")
    later = press_during_the_run(s, monkeypatch, "mgr_2")
    result = await s.decide(stakes_for(s, {"mgr_2": 8, "mgr_3": 2}))
    assert result is not None
    assert result["knocked_out"] == {"arm": first, "manager": "mgr_2"}
    assert later[0] != first
    assert s.armed is not None
    assert (s.armed.arm, s.armed.manager) == (later[0], "mgr_2")


async def test_a_knockout_of_everyone_names_its_arm_on_the_decision() -> None:
    s = session()
    arm = s.simulate_failure()
    result = await s.decide(stakes_for(s, {"mgr_1": 8, "mgr_2": 2}))
    assert result is not None
    assert result["knocked_out"] == {"arm": arm}
    assert s.armed is None


def test_a_knockout_names_a_manager_in_the_league() -> None:
    s = session()
    with pytest.raises(ValueError, match="no such manager"):
        s.simulate_failure("mgr_9")
    assert s.chaos is None


def close_call_screens(s: Session) -> dict[str, Any]:
    """The real subscription queues of the group screen and every phone, cues only."""
    return {screen: s.subscribe(screen) for screen in ["group"] + [f"phone:mgr_{n}" for n in range(1, 6)]}


def drained_cues(queues: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {screen: [m["data"] for m in drain(q) if m["type"] == "cue"] for screen, q in queues.items()}


async def test_a_close_call_shows_a_clash_then_the_verdict_and_tells_the_loser(
    contract_errors: Callable[[str, Any], list[str]],
) -> None:
    s = session(host=Host())  # overrules the scorer: picks the second-ranked bid
    queues = close_call_screens(s)
    result = await s.decide(stakes_for(s, {"mgr_2": 6, "mgr_3": 6}))
    assert result is not None
    assert (result["source"], result["close_call"]) == ("host", True)
    winner = result["chosen_manager"]
    loser = next(m for m in ("mgr_2", "mgr_3") if m != winner)
    got = drained_cues(queues)
    for screen, cs in got.items():
        for c in cs:
            assert contract_errors("cue", c) == [], (screen, c)
    clash = [c for c in got["group"] if c["kind"] == "clash"]
    card = next(c for c in got["group"] if c["kind"] == "explanation")
    assert len(clash) == 1
    p = clash[0]["payload"]
    assert {c["manager"] for c in p["contenders"]} == {"mgr_2", "mgr_3"}
    assert all(c["pitch"] == "Show my moment." for c in p["contenders"])
    assert p["winner"] == winner == card["payload"]["manager"]
    assert clash[0]["show_at"] == card["show_at"] == result["t"]
    assert clash[0]["show_at"] < p["verdict_at"] < clash[0]["expires_at"] == card["expires_at"]
    assert p["verdict_at"] - clash[0]["show_at"] == 1000 * s.speed  # one real second on the playback clock
    assert clash[0]["moment_id"] == card["moment_id"] == result["moment_id"]
    notes = {screen: [c for c in cs if c["kind"] == "lost_screen"] for screen, cs in got.items()}
    assert [c["payload"]["winner"] for c in notes[f"phone:{loser}"]] == [winner]
    assert notes[f"phone:{loser}"][0]["show_at"] == p["verdict_at"]  # with the verdict, not before it
    name = s.managers[winner]["display_name"]
    assert notes[f"phone:{loser}"][0]["payload"]["body"] == f"The screen went to {name}: Show my moment."
    assert all(not ns for screen, ns in notes.items() if screen != f"phone:{loser}")  # never the winner's phone


async def test_a_clear_winner_shows_no_clash_and_tells_nobody() -> None:
    s = session()
    queues = close_call_screens(s)
    result = await s.decide(stakes_for(s, {"mgr_1": 8, "mgr_2": 2}))
    assert result is not None
    assert not result["close_call"]
    got = drained_cues(queues)
    assert not [c for cs in got.values() for c in cs if c["kind"] in ("clash", "lost_screen")]


async def test_a_clear_winner_during_a_simulated_failure_shows_no_clash_either() -> None:
    s = session()
    s.simulate_failure()
    queues = close_call_screens(s)
    result = await s.decide(stakes_for(s, {"mgr_1": 8, "mgr_2": 2}))
    assert result is not None
    assert (result["source"], result["close_call"]) == ("scorer", False)  # no model decided it (#86, #88)
    got = drained_cues(queues)
    assert not [c for cs in got.values() for c in cs if c["kind"] in ("clash", "lost_screen")]
    assert [c["source"] for c in got["group"] if c["kind"] == "explanation"] == ["fallback"]  # the card says so


async def test_a_close_call_the_host_missed_still_shows_the_clash_as_a_fallback() -> None:
    s = session(host=Host(delay=5), host_deadline_s=0.05)
    queues = close_call_screens(s)
    result = await s.decide(stakes_for(s, {"mgr_2": 6, "mgr_3": 6}))
    assert result is not None
    got = drained_cues(queues)
    clash = [c for c in got["group"] if c["kind"] == "clash"]
    assert [(c["source"], c["payload"]["winner"]) for c in clash] == [("fallback", result["chosen_manager"])]
    loser = next(m for m in ("mgr_2", "mgr_3") if m != result["chosen_manager"])
    assert [c["kind"] for c in got[f"phone:{loser}"] if c["kind"] == "lost_screen"] == ["lost_screen"]


async def test_the_judge_who_wins_a_close_call_is_you_on_the_losers_phone() -> None:
    s = session(host=Host(pick="mgr_1"))
    queues = close_call_screens(s)
    await s.decide(stakes_for(s, {"mgr_1": 6, "mgr_4": 6}))
    note = next(c for c in drained_cues(queues)["phone:mgr_4"] if c["kind"] == "lost_screen")
    assert note["payload"]["body"] == "The screen went to you: Show my moment."


async def test_a_goal_brings_up_the_table_even_without_a_rank_change() -> None:
    got = await run(session())
    goals = {m["data"]["moment_id"] for m in got["group"]
             if m["type"] == "event" and m["data"].get("climax") and m["data"].get("outcome") == "goal"}  # fmt: skip
    tables = {c.get("moment_id") for c in cues(got["group"]) if c["kind"] == "table"}
    assert goals
    assert goals <= tables


async def test_the_judge_is_you_whichever_manager_they_play() -> None:
    s = Session(seed=267, manager="mgr_3", style="casual", lang="en", speed=30, sleep=no_sleep)
    names = {m["id"]: m["display_name"] for m in s.league["managers"]}
    assert names == {"mgr_1": "Jo", "mgr_2": "Priya", "mgr_3": "You", "mgr_4": "Leo", "mgr_5": "Sam"}
    assert "Arjun" not in s.allowed_names


async def test_decisions_show_each_bidders_pitch_status_and_latency_for_the_trace() -> None:
    s = session()
    result = await s.decide(stakes_for(s, {"mgr_1": 6, "mgr_2": 2}))
    assert result is not None
    for row in result["scores"]:
        assert row["pitch"] == "Show my moment."
        assert row["status"] == "ok"
        assert row["latency_ms"] >= 0


async def test_the_judges_style_can_change_mid_match_and_only_theirs() -> None:
    s = session()
    before = {m["id"]: m["style"] for m in s.league["managers"]}
    s.set_style("analyst")
    after = {m["id"]: m["style"] for m in s.league["managers"]}
    assert after == before | {"mgr_1": "analyst"}
    with pytest.raises(ValueError, match="style"):
        s.set_style("loud")


async def test_the_table_is_refreshed_at_each_whistle_so_it_matches_the_summaries() -> None:
    s = session()
    await s.run()
    tables = [c for c in s.log if c.get("kind") == "table"]
    for phase in ("half_time", "full_time"):
        at = next(c for c in tables if c["show_at"] == s.views[phase].t)
        served = {i: e for given in s.views[phase].served.values() for i, e in given.items()}
        for row in at["payload"]["table"]:
            if f"ev_table_{row['manager']}_points" in served:
                assert row["points"] == served[f"ev_table_{row['manager']}_points"]["value"]
        assert at["payload"]["headline"] == ("Half time" if phase == "half_time" else "Full time")


async def test_finished_sessions_linger_for_their_recap_without_taking_a_seat() -> None:
    store = SessionStore(max_sessions=1, per_client=1, session_kwargs={"sleep": no_sleep})
    first = store.create(seed=1, manager="mgr_1", style="casual", lang="en", client="1.2.3.4")
    assert first is not None
    assert store.create(seed=1, manager="mgr_1", style="casual", lang="en", client="5.6.7.8") is None
    first.finished_at = time.monotonic()  # full time
    second = store.create(seed=1, manager="mgr_1", style="casual", lang="en", client="1.2.3.4")
    assert second is not None
    assert store.get(first.id) is first  # still there for its recap link


async def test_lingering_sessions_are_bounded() -> None:
    store = SessionStore(max_sessions=2, per_client=2, per_client_hour=100, session_kwargs={"sleep": no_sleep})
    made = []
    for _ in range(10):
        s = store.create(seed=1, manager="mgr_1", style="casual", lang="en", client="1.2.3.4")
        assert s is not None
        s.finished_at = time.monotonic()
        made.append(s)
    assert len(store.sessions) <= 3 * store.max_sessions
    assert store.get(made[-1].id) is made[-1]  # the oldest finished ones went first


async def test_a_screen_that_joins_late_gets_its_own_history_first() -> None:
    s = session()
    await s.run()
    late = s.subscribe("phone:mgr_2")
    got = drain(late)
    assert [m["type"] for m in got[:2]] == ["meta", "league"]
    replayed = [m["data"] for m in got[2:] if m["type"] == "cue"]
    expected = [c for c in s.log if c.get("screen") == "phone:mgr_2"]
    assert replayed == expected  # its own cues, in order, and nobody else's
    decisions = [m["data"] for m in got[2:] if m["type"] == "decision"]
    assert decisions == [d for d in s.log if "chosen_manager" in d]  # the trace, for a rejoining watch page


class Span:
    """A trace span that records what the workflow sets on it."""

    def __init__(self, name: str) -> None:
        self.name, self.status = name, None
        self.events: list[tuple[str, dict[str, Any]]] = []

    def set_attribute(self, key: str, value: Any) -> None: ...

    def set_attributes(self, attributes: dict[str, Any]) -> None: ...

    def add_event(self, name: str, attributes: dict[str, Any]) -> None:
        self.events.append((name, attributes))

    def set_status(self, status: Any) -> None:
        self.status = status


async def test_an_agent_failure_is_logged_and_marked_on_its_span(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Broken(Manager):
        async def bid(self, stake: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("401 Unauthorized")

    spans: list[Span] = []

    class Tracer:
        @contextmanager
        def start_as_current_span(self, name: str, **_: Any) -> Iterator[Span]:
            spans.append(Span(name))
            yield spans[-1]

    monkeypatch.setattr(workflow, "tracer", Tracer())
    s = session(manager=Broken())
    with caplog.at_level(logging.WARNING, logger="watchparty.app.workflow"):
        result = await s.decide(stakes_for(s, {"mgr_1": 6}))
    assert result is not None  # the match went on, on the plain bid
    assert any("manager mgr_1" in r.getMessage() and "RuntimeError" in r.getMessage() for r in caplog.records)
    [bid] = [span for span in spans if span.name == "manager_bid"]
    assert bid.status is not None
    assert bid.status.status_code == StatusCode.ERROR  # a 401 for days must not look like the fallback (#24)


async def test_a_match_on_code_alone_calls_no_agent() -> None:
    class Summary:
        calls = 0

        async def summarise(self, *_: Any) -> dict[str, Any]:
            Summary.calls += 1
            return {}

    manager, host = Manager(), Host()
    s = session(manager=manager, host=host, summary_agent=Summary(), mcp_connect=lambda *_: nullcontext())
    s.run_on_code()  # past the hourly quota (#39)
    result = await s.decide(stakes_for(s, {"mgr_1": 6, "mgr_2": 6}))
    assert result is not None
    assert {r["status"] for r in result["scores"]} == {"fallback"}
    got = await run(s)  # through half time and full time (#56)
    assert (manager.calls, host.calls, Summary.calls, s.agents_on) == (0, 0, 0, False)
    summaries = [c for screen, messages in got.items() if screen != "group" for c in cues(messages)
                 if c["kind"] == "summary"]  # fmt: skip
    assert len(summaries) == 10
    assert {c["source"] for c in summaries} == {"fallback"}


async def test_the_group_screen_shows_an_english_bid_itself_never_a_copy() -> None:
    class Sneaky(Manager):
        async def bid(self, stake: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
            return await super().bid(stake, context) | {"explanation_en": "Unchecked text."}  # #49

    s = session(manager=Sneaky())
    q = s.subscribe("group")
    await s.decide(stakes_for(s, {"mgr_1": 6}))
    shown = [
        m["data"]["payload"]["body"] for m in drain(q) if m["type"] == "cue" and m["data"]["kind"] == "explanation"
    ]
    assert shown
    assert "Unchecked text." not in shown


@pytest.mark.parametrize(
    ("delta", "lang", "expected"),
    [(2.5, "es", "+2,5 IP"), (2.5, "en", "+2.5 IP"), (-1.5, "es", "-1,5 IP"), (-1.5, "en", "-1.5 IP"),
     (0, "es", "Sin cambios"), (0, "en", "No change"), (3, "es", "+3 IP")],
)  # fmt: skip
async def test_a_points_card_is_written_in_its_managers_language(delta: float, lang: str, expected: str) -> None:
    assert points_headline(delta, lang) == expected  # #108


async def test_one_moment_writes_each_phone_in_its_own_managers_language() -> None:
    """mgr_1 is English and mgr_4 (Leo) Spanish in the fixture league; both have a stake in the same save (#108)."""
    s = session()
    queues = close_call_screens(s)
    stakes = stakes_for(s, {"mgr_1": 2.5, "mgr_4": -1.5})
    s._stat_cues(s.climaxes["m_02"], stakes)
    assert await s.decide(stakes) is not None
    got = {
        screen: [c for c in cs if c["kind"] in ("stat", "explanation")] for screen, cs in drained_cues(queues).items()
    }
    headlines = {screen: [(c["kind"], c["payload"]["headline"]) for c in got[screen]] for screen in got}
    assert headlines["phone:mgr_1"] == [("stat", "+2.5 IP"), ("explanation", "+2.5 IP")]
    assert headlines["phone:mgr_4"] == [("stat", "-1,5 IP"), ("explanation", "-1,5 IP")]
    for screen in ("phone:mgr_1", "phone:mgr_4"):
        stat, explanation = got[screen]
        assert explanation["replaces"] == stat["cue_id"]
    # The group screen stays English, with a decimal point, in the stat card and the decided explanation
    group = {c["kind"]: c["payload"]["headline"] for c in got["group"]}
    english = stat_headline(s.climaxes["m_02"], s.names)
    assert "xG 0.15" in english
    assert group["stat"] == english
    assert group["explanation"].endswith(f": {english}")


class TableWriter(Manager):
    """A fake agent that writes about the table on a moment worth 0 IP instead of declining it."""

    async def bid(self, stake: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        after = next(e for e in stake["evidence"] if e["id"].endswith("_rank_after"))
        return {"urgency": 0.5, "explanation": f"You sit at {after['value']:g} in the league.", "explanation_en": "",
                "pitch_to_host": "Show the table.", "evidence_ids": [after["id"]]}  # fmt: skip


async def test_a_moment_worth_nothing_is_headed_by_the_table_in_each_managers_language() -> None:
    # #108 wrote "No change" here in each language; #88 heads it with the league instead, never "No change"
    s = session(manager=TableWriter())
    queues = close_call_screens(s)
    stakes = stakes_for(s, {"mgr_1": 0, "mgr_4": 0})  # at stake with no points: a rank move or a milestone
    stakes = [st | {"rank_before": 3, "rank_after": 2} if st["manager"] == "mgr_1" else st for st in stakes]
    stakes = [st | {"rank_before": 4, "rank_after": 4} if st["manager"] == "mgr_4" else st for st in stakes]
    s._stat_cues(s.climaxes["m_02"], stakes)
    await s.decide(stakes)
    got = {
        screen: [(c["kind"], c["payload"]["headline"]) for c in cs if c["kind"] in ("stat", "explanation")]
        for screen, cs in drained_cues(queues).items()
    }  # a close call also sends the loser a note (#89)
    assert got["phone:mgr_1"] == [("explanation", "League position 3 → 2")]  # no points, so no stat card first
    assert got["phone:mgr_4"] == [("explanation", "Clasificación")]


async def test_every_phone_points_card_in_a_whole_match_is_in_its_managers_language() -> None:
    s = session()
    got = await run(s)
    deltas = {(st["moment_id"], st["manager"]): st["delta_points"] for stakes in s.stakes_log for st in stakes}
    langs = {m["id"]: m["lang"] for m in s.league["managers"]}
    checked: set[str] = set()
    for n in range(1, 6):
        manager = f"mgr_{n}"
        for c in cues(got[f"phone:{manager}"]):
            if c["kind"] not in ("stat", "explanation"):
                continue
            assert c["payload"]["headline"] == points_headline(deltas[(c["moment_id"], manager)], langs[manager])
            checked.add(manager)
    assert checked == set(langs)  # every phone had cards to check, not only some
    spanish = [c["payload"]["headline"] for c in cues(got["phone:mgr_4"]) if c["kind"] in ("stat", "explanation")]
    assert "+0,5 IP" in spanish  # Leo's half point in the demo, with a decimal comma


class Writer(Manager):
    """A fake agent with a phone text for its manager and a different line for the room (#138)."""

    async def bid(self, stake: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        bid = await super().bid(stake, context)
        name, spanish = context["manager"]["display_name"], context["manager"]["lang"] == "es"
        phone = ({"explanation": "Es tu momento.", "explanation_en": "It is your moment."} if spanish
                 else {"explanation": "It is your moment.", "explanation_en": ""})  # fmt: skip
        return bid | phone | {"pitch_to_host": f"A big moment for {name}."}


@pytest.mark.parametrize(("winner", "name"), [("mgr_2", "Priya"), ("mgr_4", "Leo")])  # English, Spanish
async def test_the_group_card_is_the_winners_line_for_the_room_never_their_phone_text(winner: str, name: str) -> None:
    s = session(manager=Writer())
    queues = close_call_screens(s)
    result = await s.decide(stakes_for(s, {winner: 9, "mgr_3": 1}))
    assert result is not None
    assert result["chosen_manager"] == winner
    got = drained_cues(queues)
    shown = [c["payload"]["body"] for c in got["group"] if c["kind"] == "explanation"]
    assert shown == [f"A big moment for {name}."]  # not "It is your moment." (#138)
    phone = next(c["payload"] for c in got[f"phone:{winner}"] if c["kind"] == "explanation")
    expected = ("It is your moment.", None) if winner == "mgr_2" else ("Es tu momento.", "It is your moment.")
    assert (phone["body"], phone.get("body_en")) == expected  # the phone text is unchanged


async def test_a_close_call_shows_both_lines_for_the_room_and_the_winners_again() -> None:
    s = session(manager=Writer(), host=Host())
    queues = close_call_screens(s)
    result = await s.decide(stakes_for(s, {"mgr_2": 6, "mgr_4": 6}))
    assert result is not None
    assert result["close_call"]
    got = drained_cues(queues)
    clash = next(c["payload"] for c in got["group"] if c["kind"] == "clash")
    assert sorted(c["pitch"] for c in clash["contenders"]) == ["A big moment for Leo.", "A big moment for Priya."]
    card = next(c["payload"] for c in got["group"] if c["kind"] == "explanation")
    name = s.managers[result["chosen_manager"]]["display_name"]
    assert card["body"] == f"A big moment for {name}."


async def test_a_match_on_code_alone_puts_a_third_person_line_on_the_group_screen() -> None:
    s = session()
    s.run_on_code()
    got = await run(s)
    shown = [c["payload"]["body"] for c in cues(got["group"]) if c["kind"] == "explanation"]
    assert len(shown) > 3
    line = re.compile(r"[+-][\d.]+ IP for (?:Priya|Arjun|Leo|Sam|you) from this moment \([a-z ]+\)\.")
    assert all(line.fullmatch(body) for body in shown), shown
    sources = {c["payload"]["body_source"] for c in cues(got["group"]) if c["kind"] == "explanation"}
    assert sources == {"code"}  # marked as code's line, whoever decided the screen


class PlainPitch(Writer):
    """An agent whose pitch failed twice: code's line stood in for it, its phone text kept (#138)."""

    async def bid(self, stake: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        return await super().bid(stake, context) | {"pitch_source": "code"}


@pytest.mark.parametrize(("agent", "by"), [(Writer, "manager"), (PlainPitch, "code")])
async def test_the_group_card_clash_and_trace_say_who_wrote_the_line(
    agent: type[Writer], by: str, contract_errors: Callable[[str, Any], list[str]]
) -> None:
    s = session(manager=agent(), host=Host())
    queues = close_call_screens(s)
    result = await s.decide(stakes_for(s, {"mgr_2": 6, "mgr_4": 6}))
    assert result is not None
    assert result["close_call"]
    got = drained_cues(queues)
    card = next(c for c in got["group"] if c["kind"] == "explanation")
    clash = next(c for c in got["group"] if c["kind"] == "clash")
    assert card["payload"]["body_source"] == by  # not the decision's source (smoke-en3 card-05)
    assert {c["pitch_source"] for c in clash["payload"]["contenders"]} == {by}
    assert {r["pitch_source"] for r in result["scores"]} == {by}
    assert contract_errors("cue", card) == contract_errors("cue", clash) == []
    assert contract_errors("host-decision", result) == []
