from collections.abc import Callable
from typing import Any

import pytest

from watchparty.agents.base import fallback_bid
from watchparty.agents.scorer import Ledger, rank


def bids(**urgency: float) -> dict[str, dict[str, Any]]:
    return {m: {"urgency": u} for m, u in urgency.items()}


def stakes(**delta: float) -> dict[str, dict[str, Any]]:
    return {m: {"delta_points": d} for m, d in delta.items()}


def ledger(*shown: str, overruled: tuple[str, ...] = ()) -> Ledger:
    """A ledger where `shown` had the screen in turn, and `overruled` lost a close call to someone else."""
    lg = Ledger()
    for m in shown:
        lg.record(m, [m], close=False)
    for m in overruled:
        lg.record("mgr_9", ["mgr_9", m], close=True)
    return lg


def test_the_bigger_swing_wins_and_urgency_only_nudges() -> None:
    rows, close = rank(bids(mgr_1=0.0, mgr_2=1.0), stakes(mgr_1=8, mgr_2=4), Ledger())
    assert [r["manager"] for r in rows] == ["mgr_1", "mgr_2"]
    assert not close


def test_a_loss_matters_as_much_as_a_gain() -> None:
    rows, _ = rank(bids(mgr_1=0.5, mgr_2=0.5), stakes(mgr_1=-6, mgr_2=2), Ledger())
    assert rows[0]["manager"] == "mgr_1"


def test_scoring_is_deterministic_whatever_order_the_bids_arrive_in() -> None:
    a = rank(bids(mgr_1=0.5, mgr_2=0.5, mgr_3=0.5), stakes(mgr_1=4, mgr_2=4, mgr_3=4), ledger("mgr_3"))
    b = rank(bids(mgr_3=0.5, mgr_2=0.5, mgr_1=0.5), stakes(mgr_3=4, mgr_1=4, mgr_2=4), ledger("mgr_3"))
    assert a == b
    assert [r["manager"] for r in a[0]] == ["mgr_1", "mgr_2", "mgr_3"]  # ties go to the lower id


def test_equal_swings_are_a_close_call() -> None:
    _, close = rank(bids(mgr_1=0.7, mgr_2=0.7), stakes(mgr_1=3, mgr_2=3), Ledger())
    assert close


def test_one_bidder_is_never_a_close_call() -> None:
    rows, close = rank(bids(mgr_1=0.7), stakes(mgr_1=3), ledger("mgr_1", "mgr_1"))
    assert [r["manager"] for r in rows] == ["mgr_1"]
    assert not close


def test_screen_hogs_lose_ground_and_the_overruled_gain_it() -> None:
    fair = rank(bids(mgr_1=0.7, mgr_2=0.7), stakes(mgr_1=4, mgr_2=3), Ledger())[0]
    hog = rank(bids(mgr_1=0.7, mgr_2=0.7), stakes(mgr_1=4, mgr_2=3), ledger("mgr_1", "mgr_1", "mgr_1"))[0]
    owed = rank(bids(mgr_1=0.7, mgr_2=0.7), stakes(mgr_1=4, mgr_2=3), ledger(overruled=("mgr_2",)))[0]
    score = {r["manager"]: r["score"] for r in fair}
    assert {r["manager"]: r["score"] for r in hog}["mgr_1"] < score["mgr_1"]
    assert {r["manager"]: r["score"] for r in owed}["mgr_2"] > score["mgr_2"]


def test_fairness_reversing_the_stake_order_is_a_close_call_for_the_host() -> None:
    rows, close = rank(bids(mgr_1=0.7, mgr_2=0.7), stakes(mgr_1=4, mgr_2=3),
                       ledger("mgr_1", "mgr_1", "mgr_1", overruled=("mgr_2",)))  # fmt: skip
    assert rows[0]["manager"] == "mgr_2"
    assert close


def test_fairness_does_not_overturn_a_much_bigger_moment() -> None:
    rows, close = rank(bids(mgr_1=0.7, mgr_2=0.7), stakes(mgr_1=16, mgr_2=6),
                       ledger("mgr_1", "mgr_1", "mgr_1", overruled=("mgr_2", "mgr_2")))  # fmt: skip
    assert rows[0]["manager"] == "mgr_1"
    assert not close


def test_the_ledger_tracks_airtime_and_close_calls_lost_since_last_on_screen() -> None:
    lg = Ledger()
    lg.record("mgr_1", ["mgr_1", "mgr_2", "mgr_3"], close=True)
    lg.record("mgr_1", ["mgr_1", "mgr_2"], close=True)
    assert (lg.share("mgr_1"), lg.share("mgr_2")) == (1.0, 0.0)
    assert (lg.overruled["mgr_2"], lg.overruled["mgr_3"]) == (2, 1)
    lg.record("mgr_2", ["mgr_1", "mgr_2"], close=True)
    assert lg.overruled["mgr_2"] == 0  # their turn came
    assert lg.overruled["mgr_1"] == 1
    assert lg.share("mgr_1") == pytest.approx(0.667)


def test_losing_a_clear_call_is_not_being_overruled() -> None:
    lg = Ledger()
    lg.record("mgr_1", ["mgr_1", "mgr_2"], close=False)  # #27: code decided, nothing was close
    assert lg.overruled["mgr_2"] == 0
    assert lg.share("mgr_1") == 1.0  # airtime counts either way
    lg.record("mgr_1", ["mgr_1", "mgr_2"], close=True)
    assert lg.overruled["mgr_2"] == 1
    lg.record("mgr_2", ["mgr_1", "mgr_2"], close=False)
    assert lg.overruled["mgr_2"] == 0


def test_rows_validate_against_the_host_decision_contract(contract_errors: Callable[[str, Any], list[str]]) -> None:
    rows, _ = rank(bids(mgr_1=2.0, mgr_2=-1.0), stakes(mgr_1=-3, mgr_2=0), ledger("mgr_1", overruled=("mgr_2",)))
    decision = {
        "moment_id": "m_01",
        "t": 0,
        "chosen_manager": "mgr_1",
        "source": "scorer",
        "close_call": False,
        "scores": rows,
    }
    assert contract_errors("host-decision", decision) == []
    assert all(r["score"] >= 0 for r in rows)


def test_a_manager_whose_agent_failed_is_not_pushed_off_the_screen() -> None:
    stake = {"moment_id": "m_03", "reason_code": "save_points", "evidence": [
        {"id": "ev_m_03_mgr_1_delta", "value": 3}, {"id": "ev_m_03_mgr_1_total", "value": 215}]}  # fmt: skip
    plain = fallback_bid(stake, {"id": "mgr_1", "display_name": "You", "style": "casual", "lang": "en"})
    _, close = rank({"mgr_1": plain, "mgr_3": {"urgency": 0.9}}, stakes(mgr_1=3, mgr_3=3), Ledger())
    assert close  # the same swing stays a close call, not a clear win for the agent that answered
