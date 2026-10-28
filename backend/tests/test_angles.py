"""#88: manager agents argue from one of their friend's facts and may decline; the host gives verified reasons.

Rows refer to the test plan on the ticket."""

import json
from collections.abc import Callable
from typing import Any, cast

import pytest

from tests.conftest import drain
from tests.test_host_agent import Scripted as HostScripted
from tests.test_manager_agent import CONTEXT, MANAGER, STAKE, Scripted, answer
from tests.test_session import Host, Manager, cues, no_sleep, run, session, stakes_for
from watchparty.agents.base import says_zero_points
from watchparty.agents.host import HostAgent, HostAnswer, agreed_reason, factor_line, true_factors
from watchparty.agents.manager import ManagerAgent
from watchparty.agents.scorer import Ledger, rank
from watchparty.app.cues import Cues, clash_cues
from watchparty.app.session import Session
from watchparty.app.workflow import decision_problems
from watchparty.points import PointsEngine

pytestmark = pytest.mark.anyio

FIELDS: dict[str, dict[str, Any]] = {
    "differential": {"player": "p_away_1", "owners": 1},
    "captain_swing": {"player": "p_away_1", "extra_points": 3, "versus": ["mgr_3"]},
    "rank_change": {"rank_before": 3, "rank_after": 2},
    "template": {"player": "p_away_1", "owners": 3},
}


def fact(stake: dict[str, Any], kind: str) -> dict[str, Any]:
    """A fact citing the stake's league total: evidence the fake agent's own line does not cite."""
    total = next(e["id"] for e in stake["evidence"] if e["id"].endswith("_total"))
    return {"id": f"f_{stake['moment_id']}_{stake['manager']}_{kind}", "kind": kind, "evidence_ids": [total],
            **FIELDS[kind]}  # fmt: skip


def all_stakes(s: Session) -> dict[str, dict[str, dict[str, Any]]]:
    """Every moment's stakes in this session's match, by moment and manager, as its engine computes them."""
    engine, out = PointsEngine(s.match.meta, s.league), {}
    for e in s.match.events:
        if stakes := engine.feed(e):
            out[stakes[0]["moment_id"]] = {st["manager"]: st for st in stakes}
    return out


def with_facts(stakes: list[dict[str, Any]], kinds: dict[str, str]) -> list[dict[str, Any]]:
    """Each named manager's stake carries one fact of the given kind, and only that one."""
    return [s | {"facts": [fact(s, kinds[s["manager"]])]} if s["manager"] in kinds else s for s in stakes]


class Angled(Manager):
    """The fake manager, arguing from its stake's first fact, or from `angle` when given."""

    def __init__(self, angle: str | None = None) -> None:
        super().__init__()
        self.angle = angle
        self.contexts: list[dict[str, Any]] = []

    async def bid(self, stake: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        self.contexts.append(context)
        bid = await super().bid(stake, context)
        facts = stake.get("facts") or []
        chosen = self.angle or (facts[0]["id"] if facts else None)
        return bid | ({"angle": chosen} if chosen and bid.get("status") != "declined" else {})


# --- the manager agent ----------------------------------------------------------------------------------

DIFF = {"id": "f_m_03_mgr_2_differential", "kind": "differential", "player": "p_away_1", "owners": 1,
        "evidence_ids": ["ev_m_03_mgr_2_delta"]}  # fmt: skip
ANGLED = STAKE | {"facts": [DIFF]}


async def test_row1_a_valid_angle_is_carried_on_the_bid() -> None:
    bid = await ManagerAgent(Scripted(answer(angle=DIFF["id"])), model="m").bid(ANGLED, CONTEXT)
    assert (bid["angle"], bid["status"]) == (DIFF["id"], "ok")
    assert "declined" not in bid


async def test_row1_the_request_offers_the_friends_facts_with_names_not_ids() -> None:
    run_ = Scripted(answer(angle=DIFF["id"]))
    context = CONTEXT | {"players": {"p_away_1": "Rhys Mallory"}}
    await ManagerAgent(run_, model="m").bid(ANGLED, context)
    request = json.loads(run_.sent[0][0].text.split("\n\n", 1)[0])
    assert request["FACTS"] == [{"id": DIFF["id"], "kind": "differential", "player": "Rhys Mallory", "owners": 1,
                                 "evidence_ids": ["ev_m_03_mgr_2_delta"]}]  # fmt: skip


@pytest.mark.parametrize("foreign", ["f_m_03_mgr_4_differential", "f_made_up"])
async def test_row2_an_angle_not_among_the_friends_facts_is_rejected_then_retried(foreign: str) -> None:
    run_ = Scripted(answer(angle=foreign), answer(angle=DIFF["id"]))
    bid = await ManagerAgent(run_, model="m").bid(ANGLED, CONTEXT)
    assert (bid["status"], bid["angle"]) == ("retried", DIFF["id"])
    assert f"angle '{foreign}' is not one of this manager's FACTS" in run_.sent[1][-1].text


async def test_row2_a_foreign_angle_twice_falls_back() -> None:
    with pytest.raises(ValueError, match="checks"):
        await ManagerAgent(Scripted(answer(angle="f_x"), answer(angle="f_y")), model="m").bid(ANGLED, CONTEXT)


async def test_row2_no_facts_means_no_angle() -> None:
    run_ = Scripted(answer(angle="f_x"), answer())
    bid = await ManagerAgent(run_, model="m").bid(STAKE, CONTEXT)
    assert bid["status"] == "retried"
    assert "angle" not in bid


ZERO = STAKE | {"delta_points": 0, "rank_before": 3, "rank_after": 2, "evidence": [
    *[e | {"value": 0} if e["id"].endswith("_delta") else e for e in STAKE["evidence"]],
    {"id": "ev_m_03_mgr_2_rank_before", "label": "League rank before", "value": 3, "unit": "count",
     "source": {"kind": "computed"}},
    {"id": "ev_m_03_mgr_2_rank_after", "label": "League rank after", "value": 2, "unit": "count",
     "source": {"kind": "computed"}}],
    "facts": [{"id": "f_m_03_mgr_2_rank_change", "kind": "rank_change", "rank_before": 3, "rank_after": 2,
               "evidence_ids": ["ev_m_03_mgr_2_rank_before", "ev_m_03_mgr_2_rank_after"]}]}  # fmt: skip
DECLINE = answer(declined=True, angle="", urgency=0, explanation="", pitch_to_host="", evidence_ids=[])


async def test_row3_a_moment_worth_nothing_may_be_declined() -> None:
    bid = await ManagerAgent(Scripted(DECLINE), model="gpt-5.4-mini").bid(ZERO, CONTEXT)
    assert bid == {"moment_id": "m_03", "manager": "mgr_2", "style": "analyst", "lang": "en", "status": "declined",
                   "model": "gpt-5.4-mini"}  # fmt: skip


def test_row3_a_declined_bid_matches_the_contract(contract_errors: Callable[[str, Any], list[str]]) -> None:
    bid = {"moment_id": "m_03", "manager": "mgr_2", "style": "analyst", "lang": "en", "status": "declined",
           "latency_ms": 12, "model": "m"}  # fmt: skip
    assert contract_errors("bid", bid) == []


async def test_row3b_a_decline_on_a_moment_with_points_is_rejected_then_retried() -> None:
    # plan review: a decline with nonzero delta_points must not stand
    run_ = Scripted(DECLINE, answer())
    bid = await ManagerAgent(run_, model="m").bid(STAKE, CONTEXT)
    assert bid["status"] == "retried"
    assert "explanation" in bid
    assert "declined is only for a moment worth 0 IP" in run_.sent[1][-1].text
    assert "+8 IP" in run_.sent[1][-1].text


async def test_row3b_declining_points_twice_falls_back() -> None:
    with pytest.raises(ValueError, match="checks"):
        await ManagerAgent(Scripted(DECLINE, DECLINE), model="m").bid(STAKE, CONTEXT)


@pytest.mark.parametrize("text", ["That is 0 IP for you.", "Nothing changes, +0 IP.", "No points, but you move up.",
                                  "0 impact points this time.", "Zero points for you here.",
                                  "Zero impact points, but you climb."])  # fmt: skip
async def test_row9_an_explanation_saying_zero_points_is_rejected(text: str) -> None:
    good = answer(explanation="You climb from 3 to 2 in the league.", angle="f_m_03_mgr_2_rank_change",
                  pitch_to_host="Priya climbs from 3 to 2 in the league.",
                  evidence_ids=["ev_m_03_mgr_2_rank_before", "ev_m_03_mgr_2_rank_after"])  # fmt: skip
    run_ = Scripted(good.model_copy(update={"explanation": text}), good)
    bid = await ManagerAgent(run_, model="m").bid(ZERO, CONTEXT)
    assert (bid["status"], bid["explanation"]) == ("retried", good.explanation)
    assert "never say the moment gave 0" in run_.sent[1][-1].text


@pytest.mark.parametrize("es", ["Cero puntos para ti esta vez.", "Ni un punto, pero subes en la liga.",
                                "No sumas puntos con esta jugada.", "No ganas puntos, pero subes."])  # fmt: skip
def test_row9_a_spanish_explanation_saying_zero_points_is_a_problem(es: str) -> None:
    # Review of #113: these word forms got past the check
    spanish = CONTEXT | {"manager": MANAGER | {"lang": "es"}}
    line = answer(explanation=es, explanation_en="You climb from 3 to 2 in the league.",
                  angle="f_m_03_mgr_2_rank_change", pitch_to_host="Priya climbs from 3 to 2 in the league.",
                  evidence_ids=["ev_m_03_mgr_2_rank_before", "ev_m_03_mgr_2_rank_after"])  # fmt: skip
    problems = ManagerAgent(Scripted(), model="m").problems(line, ZERO, spanish)
    assert any("never say the moment gave 0" in p for p in problems), problems


@pytest.mark.parametrize("text", ["That is 10 IP for you.", "0.5 IP from the pass.", "Up from 3 to 2.",
                                  "A big swing for you."])  # fmt: skip
def test_row9_the_zero_points_check_leaves_other_numbers_alone(text: str) -> None:
    assert not says_zero_points(text)


@pytest.mark.parametrize("text", [  # seen in the live smoke of 26 Oct, a Spanish analyst's text and its copy
    "Leo gets no swing in points from this one.", "Leo no gana un giro de puntos con esta jugada."])  # fmt: skip
def test_row9_saying_no_swing_in_other_words_is_caught_too(text: str) -> None:
    assert says_zero_points(text)


@pytest.mark.parametrize("style", ["analyst", "casual"])
async def test_row9_a_request_for_a_moment_worth_nothing_carries_no_swing_to_quote(style: str) -> None:
    run_ = Scripted(DECLINE)
    await ManagerAgent(run_, model="m").bid(ZERO, CONTEXT | {"manager": MANAGER | {"style": style}})
    request = json.loads(run_.sent[0][0].text.split("\n\n", 1)[0])
    ids = [e["id"] for e in request["EVIDENCE"]]
    assert not any(i.endswith("_delta") for i in ids)
    assert "ev_m_03_mgr_2_rank_after" in ids
    assert request["POINTS"] == 0


async def test_row8_the_request_carries_the_friends_rank_and_ledger() -> None:
    run_ = Scripted(answer())
    context = CONTEXT | {"ledger": {"airtime_share": 0.25, "close_calls_lost": 2}}
    await ManagerAgent(run_, model="m").bid(STAKE | {"rank_before": 2, "rank_after": 1}, context)
    request = json.loads(run_.sent[0][0].text.split("\n\n", 1)[0])
    assert request["LEAGUE"] == {"rank_before": 2, "rank_now": 1, "share_of_shared_screen": 0.25,
                                 "close_calls_lost_since_last_on_screen": 2}  # fmt: skip


def _numbers(value: Any) -> list[Any]:
    """Every number in a JSON value, booleans aside."""
    if isinstance(value, dict):
        return [n for v in value.values() for n in _numbers(v)]
    if isinstance(value, list):
        return [n for v in value for n in _numbers(v)]
    return [value] if isinstance(value, int | float) and not isinstance(value, bool) else []


@pytest.mark.parametrize("stake", [STAKE | {"rank_before": 2, "rank_after": 1}, ZERO])
async def test_row8_a_casual_request_carries_the_standing_in_words_and_at_most_one_number(
    stake: dict[str, Any],
) -> None:
    # live smoke, 26 Oct: casual managers quoted "from 3 to 4" and failed the one-number rule twice
    big_save = answer(explanation="Big save, 8 IP!", angle="f_m_03_mgr_2_rank_change",
                      evidence_ids=["ev_m_03_mgr_2_delta"])  # fmt: skip
    run_ = Scripted(DECLINE if not stake["delta_points"] else big_save)
    ledger = {"airtime_share": 0.1, "close_calls_lost": 2}
    casual = CONTEXT | {"manager": MANAGER | {"style": "casual"}, "ledger": ledger}
    await ManagerAgent(run_, model="m").bid(stake | {"facts": ZERO["facts"]}, casual)
    request = json.loads(run_.sent[0][0].text.split("\n\n", 1)[0])
    assert request["LEAGUE"] == {"league_move": "up", "rarely_on_shared_screen": True, "lost_recent_close_calls": True}
    assert len(request["EVIDENCE"]) == 1
    assert _numbers(request["FACTS"]) == []
    assert _numbers(request["LEAGUE"]) == []


async def test_a_fact_id_cited_as_evidence_reads_as_that_facts_evidence() -> None:
    # live smoke, 26 Oct: "cites unknown evidence f_m_13_mgr_3_rank_change" failed bids twice
    line = answer(explanation="You climb from 3 to 2 in the league.", angle="f_m_03_mgr_2_rank_change",
                  pitch_to_host="Priya climbs from 3 to 2 in the league.",
                  evidence_ids=["f_m_03_mgr_2_rank_change"])  # fmt: skip
    run_ = Scripted(line)
    bid = await ManagerAgent(run_, model="m").bid(ZERO, CONTEXT)
    assert (bid["status"], bid["evidence_ids"]) == ("ok", ["ev_m_03_mgr_2_rank_before", "ev_m_03_mgr_2_rank_after"])


async def test_another_friends_fact_id_cited_as_evidence_is_still_unknown() -> None:
    foreign = answer(angle="", evidence_ids=["f_m_03_mgr_4_rank_change"])
    run_ = Scripted(foreign, answer())
    bid = await ManagerAgent(run_, model="m").bid(STAKE, CONTEXT)
    assert bid["status"] == "retried"
    assert "cites unknown evidence f_m_03_mgr_4_rank_change" in run_.sent[1][-1].text


# --- the host agent -------------------------------------------------------------------------------------

NAMES = ["Rhys Mallory", "You", "Priya", "Hollinsford United", "Saltmere Rovers"]
MOMENT = {"moment_id": "m_05", "summary": "Saved! Rhys Mallory denies Jonah Pryce", "names": NAMES}
CANDIDATES: list[dict[str, Any]] = [
    {"manager": "mgr_1", "name": "You", "pitch": "Your keeper, 5 IP.", "impact_points": 5, "airtime_share": 0.0,
     "overruled_count": 0, "angle": {"id": "f_1", "kind": "rank_change", "moved": "up"}},
    {"manager": "mgr_2", "name": "Priya", "pitch": "Only Priya owns him, 5 IP.", "impact_points": 5,
     "airtime_share": 0.0, "overruled_count": 0, "angle": {"id": "f_2", "kind": "differential",
                                                           "player": "Rhys Mallory", "owners": 1}},
]  # fmt: skip


def host_answer(chosen: str, because: str) -> HostAnswer:
    reason = "Only Priya owns the keeper who made it."
    return HostAnswer(chosen_manager=chosen, because=cast(Any, because), reason=reason)


async def test_row4_the_host_may_give_a_differential_that_holds() -> None:
    run_ = HostScripted(host_answer("mgr_2", "differential"))
    decision = await HostAgent(run_, model="m").decide(MOMENT, CANDIDATES)
    assert (decision["chosen_manager"], decision["because"], decision["status"]) == ("mgr_2", "differential", "ok")
    request = json.loads(run_.sent[0][0].text)
    assert request["candidates"][1]["angle"]["kind"] == "differential"
    assert "differential" in request["candidates"][1]["true_facts"]


@pytest.mark.parametrize(("chosen", "because"), [("mgr_2", "rank_change"), ("mgr_1", "differential"),
                                                 ("mgr_2", "captain_swing")])  # fmt: skip
async def test_row5_a_fact_reason_the_winners_angle_does_not_hold_is_rejected(chosen: str, because: str) -> None:
    run_ = HostScripted(host_answer(chosen, because), host_answer("mgr_2", "differential"))
    decision = await HostAgent(run_, model="m").decide(MOMENT, CANDIDATES)
    assert decision["status"] == "retried"
    assert f"because '{because}' is not true" in run_.sent[1][-1].text


def test_row5_without_an_angle_no_fact_reason_is_true() -> None:
    bare = [c | {"angle": None} for c in CANDIDATES]
    assert not true_factors("mgr_2", bare) & {"rank_change", "differential", "captain_swing"}


# --- the scorer -----------------------------------------------------------------------------------------


def scored(kinds: tuple[str | None, str | None], points: tuple[float, float]) -> bool:
    stakes = {}
    bids = {}
    for m, kind, delta in zip(("mgr_1", "mgr_2"), kinds, points, strict=True):
        stakes[m] = {"delta_points": delta, "facts": [{"id": f"f_{m}", "kind": kind}] if kind else []}
        bids[m] = {"urgency": 0.5} | ({"angle": f"f_{m}"} if kind else {})
    return rank(bids, stakes, Ledger())[1]


def test_row6_different_angles_within_the_wider_window_are_a_close_call() -> None:
    assert scored(("rank_change", "differential"), (4, 3))
    assert scored(("captain_swing", "differential"), (6, 5))


def test_row6b_the_same_scores_with_the_same_kind_of_angle_stay_with_the_scorer() -> None:
    # plan review: the paired case, so the wider window is not a global threshold increase
    assert not scored(("differential", "differential"), (4, 3))
    assert not scored(("captain_swing", "captain_swing"), (6, 5))
    assert not scored((None, "differential"), (4, 3))  # a bid with no angle offers the host nothing to weigh
    assert not scored((None, None), (4, 3))


def test_row7_a_clear_winner_by_points_stays_with_the_scorer_whatever_the_angles() -> None:
    assert not scored(("rank_change", "differential"), (8, 2))
    assert not scored(("rank_change", "differential"), (3, 2))


def test_a_third_bids_angle_does_not_widen_the_window_between_the_other_two() -> None:
    # A 10 IP rank_change, B 8.5 IP rank_change with all the airtime so far, C 7 IP differential. The top two by
    # score are A and C, whose kinds differ; the 25% window applied to the whole field made B (15% behind A, same
    # kind) a close call. Per pair: A against B is 10% (clear), A against C is 25% (7 < 7.5, clear).
    stakes = {"a": {"delta_points": 10, "facts": [{"id": "f_a", "kind": "rank_change"}]},
              "b": {"delta_points": 8.5, "facts": [{"id": "f_b", "kind": "rank_change"}]},
              "c": {"delta_points": 7, "facts": [{"id": "f_c", "kind": "differential"}]}}  # fmt: skip
    bids = {m: {"urgency": 0.5, "angle": f"f_{m}"} for m in stakes}
    ledger = Ledger()
    ledger.record("b", ["b"], close=False)  # b has had the screen every time: airtime 1.0
    rows, close = rank(bids, stakes, ledger)
    assert [r["manager"] for r in rows] == ["a", "c", "b"]
    assert not close
    # and each pair still counts on its own: C at 7.6 IP is inside 25% of A, so that is the host's
    assert rank(bids, stakes | {"c": stakes["c"] | {"delta_points": 7.6}}, ledger)[1]


def test_scores_carry_each_bids_angle(contract_errors: Callable[[str, Any], list[str]]) -> None:
    stakes: dict[str, dict[str, Any]] = {
        "mgr_1": {"delta_points": 4, "facts": [{"id": "f_m_01_mgr_1_rank_change", "kind": "rank_change"}]},
        "mgr_2": {"delta_points": 2},
    }
    rows, _ = rank({"mgr_1": {"urgency": 0.5, "angle": "f_m_01_mgr_1_rank_change"}, "mgr_2": {"urgency": 0.5}},
                   stakes, Ledger())  # fmt: skip
    assert [r.get("angle") for r in rows] == ["f_m_01_mgr_1_rank_change", None]
    decision = {"moment_id": "m_01", "t": 0, "chosen_manager": "mgr_1", "source": "scorer", "close_call": False,
                "scores": rows}  # fmt: skip
    assert contract_errors("host-decision", decision) == []


# --- the workflow and the session -------------------------------------------------------------------------


async def test_row1_the_phone_shows_the_fact_the_explanation_was_written_around() -> None:
    s = session(manager=Angled())
    queue = s.subscribe("phone:mgr_2")
    result = await s.decide(with_facts(stakes_for(s, {"mgr_2": 6}), {"mgr_2": "rank_change"}))
    assert result is not None
    assert result["scores"][0]["angle"] == "f_m_02_mgr_2_rank_change"
    [phone] = [c for c in cues(drain(queue)) if c["kind"] == "explanation"]
    drawer = [e["id"] for e in phone["payload"]["evidence"]]
    assert drawer == ["ev_m_02_mgr_2_delta", "ev_m_02_mgr_2_total"]  # what the line cites, then the angle's own


async def test_row2_a_foreign_angle_from_any_agent_never_reaches_a_screen() -> None:
    s = session(manager=Angled(angle="f_m_02_mgr_4_differential"))  # another friend's fact
    stakes = with_facts(stakes_for(s, {"mgr_2": 6}), {"mgr_2": "differential", "mgr_4": "differential"})
    result = await s.decide(stakes)
    assert result is not None
    assert result["scores"][0]["status"] == "fallback"
    assert "angle" not in result["scores"][0]


async def test_row3_a_decline_sends_no_phone_message_and_never_reaches_the_scorer_or_host() -> None:
    host = Host()
    s = session(host=host)
    phone = s.subscribe("phone:mgr_1")
    result = await s.decide(stakes_for(s, {"mgr_1": 0, "mgr_2": 6}))  # mgr_1 at stake, but worth 0 IP to them
    assert result is not None
    assert [r["manager"] for r in result["scores"]] == ["mgr_2"]
    assert (result["source"], host.calls) == ("scorer", 0)
    assert cues(drain(phone)) == []
    assert await s.decide(stakes_for(s, {"mgr_1": 0})) is None  # nobody left: the stat card stays up


async def test_row3_a_decline_on_points_from_any_agent_falls_back_to_the_plain_line() -> None:
    class Decliner(Manager):
        async def bid(self, stake: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
            return {"status": "declined"}

    s = session(manager=Decliner())
    result = await s.decide(stakes_for(s, {"mgr_2": 6}))
    assert result is not None
    assert result["scores"][0]["status"] == "fallback"


async def test_row3_and_row9_in_a_whole_match_a_rank_move_worth_nothing_updates_the_table_and_no_phone() -> None:
    # In the demo, m_04 moves mgr_3 from 3rd to 4th for 0 IP: rank_change is its only fact
    code_alone = Session(seed=267, manager="mgr_1", style="casual", lang="en", speed=30, sleep=no_sleep)
    for s in (session(), code_alone):  # the fake agent, which declines such a moment, and the plain line of code
        got = await run(s)
        tables = [c for c in cues(got["group"]) if c["kind"] == "table" and c.get("moment_id") == "m_04"]
        assert tables
        mine = [c for c in cues(got["phone:mgr_3"]) if c.get("moment_id") == "m_04"]
        assert mine == []
        for screen in [f"phone:mgr_{n}" for n in range(1, 6)]:
            for c in cues(got[screen]):
                if c["kind"] != "explanation":
                    continue
                assert c["payload"]["headline"] != "No change"
                assert not says_zero_points(c["payload"]["body"]), c


async def test_row4_a_verified_differential_puts_the_reason_and_the_contenders_on_screen(
    contract_errors: Callable[[str, Any], list[str]],
) -> None:
    keeper = session().names["p_away_1"]
    reason = f"Only Priya owns {keeper}, the keeper who made it."
    s = session(manager=Angled(), host=Host(pick="mgr_2", because="differential", reason=reason))
    group = s.subscribe("group")
    stakes = with_facts(stakes_for(s, {"mgr_1": 6, "mgr_2": 6}), {"mgr_1": "rank_change", "mgr_2": "differential"})
    result = await s.decide(stakes)
    assert result is not None
    assert (result["source"], result["chosen_manager"], result["because"]) == ("host", "mgr_2", "differential")
    assert [c["manager"] for c in result["contenders"]] == ["mgr_1", "mgr_2"]
    assert [c["angle"] for c in result["contenders"]] == ["f_m_02_mgr_1_rank_change", "f_m_02_mgr_2_differential"]
    assert contract_errors("host-decision", result) == []
    assert decision_problems(result, {st["manager"]: st for st in stakes}) == []
    shown = [c for c in cues(drain(group)) if c["kind"] == "host_reason"]
    # a fact reason is always shown in code's words from the verified fact, however true the host's sounded
    assert [c["payload"]["body"] for c in shown] == [f"Priya alone owns {keeper}, so this moment is theirs."]


OWNS = "Priya alone owns {keeper}, so this moment is theirs."
MOVES_UP = "This moves Priya up the table, so it gets the screen."


@pytest.mark.parametrize(
    ("because", "words", "expected"),
    [
        ("differential", "Priya moved up the table.", OWNS),
        ("differential", "Only Priya has {keeper}, and it shows.", OWNS),
        ("captain_swing", "Priya climbs the table tonight.",
         "Priya's armband on {keeper} paid off, so this moment is theirs."),
        ("rank_change", "Priya owns the keeper nobody else has.", MOVES_UP),
        # the final recheck: about the table and still false, the fact says up
        ("rank_change", "Priya drops down the table.", MOVES_UP),
        # "up", but not about the table at all
        ("rank_change", "Priya is up for it tonight.", MOVES_UP),
    ],
)  # fmt: skip
async def test_a_fact_reason_is_always_shown_in_codes_words_from_the_verified_fact(
    because: str, words: str, expected: str
) -> None:
    # Review of #113: code checks which fact the host gave, not whether its prose tells that fact truly
    keeper = session().names["p_away_1"]
    words = words.format(keeper=keeper)
    s = session(manager=Angled(), host=Host(pick="mgr_2", because=because, reason=words))
    group = s.subscribe("group")
    result = await s.decide(with_facts(stakes_for(s, {"mgr_1": 6, "mgr_2": 6}), {"mgr_2": because}))
    assert result is not None
    assert (result["source"], result["because"]) == ("host", because)
    shown = [c["payload"]["body"] for c in cues(drain(group)) if c["kind"] == "host_reason"]
    assert shown == [result["reason"]] == [expected.format(keeper=keeper)]


async def test_a_scorer_reason_keeps_the_hosts_own_words() -> None:
    words = "You have waited longest for a turn, so this one is yours."
    s = session(manager=Angled(), host=Host(pick="mgr_1", overrule=False, reason=words))
    result = await s.decide(stakes_for(s, {"mgr_1": 6, "mgr_2": 6}))
    assert result is not None
    assert result["source"] == "host"
    assert result["because"] not in ("rank_change", "differential", "captain_swing")
    assert result["reason"] == words


@pytest.mark.parametrize("name", ["You", "Priya"])
@pytest.mark.parametrize(("because", "angle"), [
    ("differential", {"kind": "differential", "player": "Rhys Mallory", "owners": 1}),
    ("captain_swing", {"kind": "captain_swing", "player": "Rhys Mallory", "extra_points": 3, "versus": ["Arjun"]}),
    ("rank_change", {"kind": "rank_change", "moved": "up"}),
    ("rank_change", {"kind": "rank_change", "moved": "down"}),
])  # fmt: skip
def test_codes_reason_line_passes_the_hosts_own_checks(name: str, because: str, angle: dict[str, Any]) -> None:
    chosen = CANDIDATES[0] | {"name": name, "angle": angle | {"id": "f_1"}}
    others = [CANDIDATES[1] | {"angle": None}]
    line = factor_line(because, chosen)
    assert len(line) <= 140
    assert angle.get("moved", "") in line
    problems = HostAgent(HostScripted(), model="m").problems(
        HostAnswer(chosen_manager="mgr_1", because=cast(Any, because), reason=line), MOMENT, [chosen, *others]
    )
    assert problems == [], (line, problems)


async def test_review_contenders_when_the_host_picks_the_third_candidate() -> None:
    keeper = session().names["p_away_1"]
    host = Host(pick="mgr_3", because="differential", reason=f"Only Arjun owns {keeper}.")
    s = session(manager=Angled(), host=host)
    stakes = with_facts(stakes_for(s, {"mgr_1": 6, "mgr_2": 6, "mgr_3": 6}), {"mgr_3": "differential"})
    result = await s.decide(stakes)
    assert result is not None
    assert [r["manager"] for r in result["scores"]] == ["mgr_1", "mgr_2", "mgr_3"]  # the pick is third
    assert (result["source"], result["chosen_manager"]) == ("host", "mgr_3")
    assert [c["manager"] for c in result["contenders"]] == ["mgr_1", "mgr_3"]  # the scorer's pick against the host's


def test_review_the_clash_shows_the_contenders_the_decision_names() -> None:
    rows = [{"manager": m, "score": 1.0, "airtime_share": 0.0, "overruled_count": 0, "pitch": f"Show {m}."}
            for m in ("mgr_1", "mgr_2", "mgr_3")]  # fmt: skip
    pair = [{"manager": "mgr_1", "pitch": "Show mgr_1."}, {"manager": "mgr_3", "pitch": "Show mgr_3."}]
    decision = {"moment_id": "m_02", "t": 0, "chosen_manager": "mgr_1", "source": "host", "close_call": True,
                "reason": "Theirs.", "because": "scorer_pick", "scores": rows, "contenders": pair}  # fmt: skip
    clash, lost = clash_cues(Cues(), decision, 0, {"mgr_1": "You", "mgr_3": "Arjun"}, 30)
    assert [c["manager"] for c in clash["payload"]["contenders"]] == ["mgr_1", "mgr_3"]  # not mgr_2, second by score
    assert lost["screen"] == "phone:mgr_3"


def test_the_code_line_addresses_the_viewer_as_you() -> None:
    you = {"manager": "mgr_1", "name": "You", "angle": {"kind": "differential", "player": "Rhys Mallory"}}
    assert (
        agreed_reason("You climb the table.", "differential", you)
        == "You alone own Rhys Mallory, so this moment is yours."
    )
    you["angle"] = {"kind": "captain_swing", "player": "Rhys Mallory"}
    assert (
        agreed_reason("Big night.", "captain_swing", you)
        == "Your armband on Rhys Mallory paid off, so this moment is yours."
    )
    assert agreed_reason("Big night.", "least_airtime", you) == "Big night."  # a ledger reason is not checked here


async def test_row4_the_host_sees_each_candidates_angle_as_a_fact_with_names() -> None:
    host = Host(pick="mgr_2", because="differential")
    s = session(manager=Angled(), host=host)
    await s.decide(with_facts(stakes_for(s, {"mgr_1": 6, "mgr_2": 6}), {"mgr_2": "differential"}))
    [candidates] = host.seen
    angle = next(c["angle"] for c in candidates if c["manager"] == "mgr_2")
    assert (angle["kind"], angle["player"]) == ("differential", s.names["p_away_1"])
    assert next(c["angle"] for c in candidates if c["manager"] == "mgr_1") is None


@pytest.mark.parametrize("because", ["rank_change", "captain_swing"])
async def test_row5_a_false_fact_reason_from_any_host_shows_the_scorers_pick(because: str) -> None:
    s = session(manager=Angled(), host=Host(pick="mgr_2", because=because))
    group = s.subscribe("group")
    stakes = with_facts(stakes_for(s, {"mgr_1": 6, "mgr_2": 6}), {"mgr_1": "rank_change", "mgr_2": "differential"})
    result = await s.decide(stakes)
    assert result is not None
    assert (result["source"], result["chosen_manager"]) == ("fallback", result["scores"][0]["manager"])
    assert "because" not in result
    assert not [c for c in cues(drain(group)) if c["kind"] == "host_reason"]


async def test_row6_bids_with_different_angles_four_against_three_reach_the_host() -> None:
    host = Host(overrule=False)
    s = session(manager=Angled(), host=host)
    result = await s.decide(with_facts(stakes_for(s, {"mgr_1": 4, "mgr_2": 3}),
                                       {"mgr_1": "rank_change", "mgr_2": "differential"}))  # fmt: skip
    assert result is not None
    assert (result["close_call"], host.calls) == (True, 1)


async def test_row6b_the_same_moment_with_the_same_kind_of_angle_does_not() -> None:
    host = Host(overrule=False)
    s = session(manager=Angled(), host=host)
    result = await s.decide(with_facts(stakes_for(s, {"mgr_1": 4, "mgr_2": 3}),
                                       {"mgr_1": "differential", "mgr_2": "differential"}))  # fmt: skip
    assert result is not None
    assert (result["close_call"], result["source"], host.calls) == (False, "scorer", 0)


async def test_row7_a_clear_winner_by_points_is_not_sent_to_the_host() -> None:
    host = Host()
    s = session(manager=Angled(), host=host)
    result = await s.decide(with_facts(stakes_for(s, {"mgr_1": 8, "mgr_2": 2}),
                                       {"mgr_1": "rank_change", "mgr_2": "differential"}))  # fmt: skip
    assert result is not None
    assert (result["source"], result["chosen_manager"], host.calls) == ("scorer", "mgr_1", 0)


async def test_row8_the_agent_context_carries_its_friends_ledger() -> None:
    manager = Angled()
    s = session(manager=manager, host=Host(overrule=False))
    await s.decide(stakes_for(s, {"mgr_1": 6, "mgr_2": 6}))  # a close call mgr_2 loses
    await s.decide(stakes_for(s, {"mgr_2": 6}))
    last = manager.contexts[-1]
    assert last["ledger"] == {"airtime_share": 0.0, "close_calls_lost": 1}
    assert last["managers"]["mgr_2"] == "Priya"
    assert last["players"] == s.names


async def test_every_decision_in_a_whole_match_keeps_the_rules_the_schema_cannot() -> None:
    s = session(manager=Angled(), host=Host())
    got = await run(s)
    decisions = [m["data"] for m in got["group"] if m["type"] == "decision"]
    assert any(d["source"] == "host" for d in decisions)
    assert any(r.get("angle") for d in decisions for r in d["scores"])  # the real facts were argued from
    stakes = all_stakes(s)
    for d in decisions:
        assert decision_problems(d, stakes[d["moment_id"]]) == [], d
        assert (d["source"] == "host") is all(k in d for k in ("because", "contenders"))


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"source": "scorer"}, "source scorer with close_call True"),
        ({"close_call": False}, "source host with close_call False"),
        ({"chosen_manager": "mgr_5"}, "chosen_manager was not scored"),
        ({"contenders": [{"manager": "mgr_2", "pitch": "x"}, {"manager": "mgr_2", "pitch": "y"}]}, "contenders"),
        ({"contenders": [{"manager": "mgr_2", "pitch": "x"}, {"manager": "mgr_3", "pitch": "y"}]}, "contenders"),
        ({"because": "rank_change"}, "because rank_change but the winner's angle is differential"),
        ({"because": None}, "needs reason, because and contenders"),
    ],
)
def test_decision_problems_catch_what_the_schema_cannot(change: dict[str, Any], problem: str) -> None:
    good = {"moment_id": "m_02", "t": 0, "chosen_manager": "mgr_1", "source": "host", "close_call": True,
            "reason": "Theirs.", "because": "differential",
            "contenders": [{"manager": "mgr_2", "pitch": "x"}, {"manager": "mgr_1", "pitch": "y"}],
            "scores": [{"manager": "mgr_2", "score": 1, "airtime_share": 0, "overruled_count": 0},
                       {"manager": "mgr_1", "score": 0.9, "airtime_share": 0, "overruled_count": 0,
                        "angle": "f_1"}]}  # fmt: skip
    stakes = {"mgr_1": {"facts": [{"id": "f_1", "kind": "differential"}]}}
    assert decision_problems(good, stakes) == []
    bad = good | change
    assert any(problem in p for p in decision_problems(bad, stakes)), decision_problems(bad, stakes)
