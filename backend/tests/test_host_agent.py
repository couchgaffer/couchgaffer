import json
from typing import Any

import pytest
from agent_framework import Message

from watchparty.agents.host import HostAgent, HostAnswer, true_factors

pytestmark = pytest.mark.anyio

NAMES = ["Tom Ashcombe", "Marcus Eyre", "Hollinsford United", "Saltmere Rovers", "You", "Arjun"]
MOMENT = {"moment_id": "m_05", "summary": "Saved! Tom Ashcombe denies Marcus Eyre, xG 0.42", "names": NAMES}
CANDIDATES: list[dict[str, Any]] = [
    {"manager": "mgr_1", "name": "You", "pitch": "Your keeper again, 5 IP.", "impact_points": 5,
     "airtime_share": 0.5, "overruled_count": 0},
    {"manager": "mgr_3", "name": "Arjun", "pitch": "Arjun's keeper too, 5 IP.", "impact_points": 5,
     "airtime_share": 0.0, "overruled_count": 1},
]  # fmt: skip


class Scripted:
    def __init__(self, *answers: HostAnswer | None) -> None:
        self.answers = list(answers)
        self.sent: list[list[Message]] = []

    async def __call__(self, messages: list[Message]) -> tuple[HostAnswer | None, int, int]:
        self.sent.append(messages)
        return self.answers.pop(0), 500, 40


def answer(chosen: str = "mgr_3", reason: str = "Same 5 IP each; Arjun lost the last close call.",
           because: str = "most_close_calls_lost") -> HostAnswer:  # fmt: skip
    return HostAnswer(chosen_manager=chosen, because=because, reason=reason)


async def test_a_valid_pick_is_returned_with_its_reason_and_model() -> None:
    decision = await HostAgent(Scripted(answer()), model="gpt-5.4-mini").decide(MOMENT, CANDIDATES)
    assert decision == {"chosen_manager": "mgr_3", "because": "most_close_calls_lost", "reason": answer().reason,
                        "status": "ok", "model": "gpt-5.4-mini"}  # fmt: skip


async def test_the_request_carries_the_candidates_in_scorer_order_with_their_true_facts() -> None:
    run = Scripted(answer())
    await HostAgent(run, model="m").decide(MOMENT, CANDIDATES)
    request = json.loads(run.sent[0][0].text)
    assert [c["id"] for c in request["candidates"]] == ["mgr_1", "mgr_3"]
    assert request["candidates"][1] == {"id": "mgr_3", "name": "Arjun", "pitch": "Arjun's keeper too, 5 IP.",
                                        "impact_points": 5, "airtime_share": 0.0, "overruled_count": 1, "angle": None,
                                        "true_facts": ["least_airtime", "most_close_calls_lost"]}  # fmt: skip
    assert request["candidates"][0]["true_facts"] == ["scorer_pick"]
    assert request["moment"] == MOMENT["summary"]
    assert "Tom Ashcombe" in request["NAMES"]


@pytest.mark.parametrize(
    ("bad", "problem"),
    [
        (answer(chosen="mgr_2"), "candidate"),
        (answer(reason="x" * 141), "140"),
        (answer(reason="Arjun gets it: 7 IP."), "'7 IP'"),
        (answer(reason="Arjun gets it: 5 xG."), "'5 xG'"),  # Arjun's 5 is IP (#23)
        (answer(reason="Arjun, our Henrik Vandrup tonight."), "'Henrik'"),
        (answer(reason="Arjun's turn, no bet needed."), "betting"),
        (answer(because="bigger_swing"), "not true"),  # 5 IP each
        (answer(because="scorer_pick"), "not true"),  # the scorer had You first
        (answer(reason="You have had 50% of the screen, so Arjun gets it."), "'50%'"),  # not the chosen's number
        (answer(reason=""), "140"),
        (answer(reason="   "), "140"),  # blank once trimmed (#53)
        (answer(reason="vasquith gets this moment."), "'Vasquith'"),  # checked as displayed, capitalised (#53)
        (answer(reason="Vale, este momento merece pantalla."), "Spanish"),
        (answer(reason="Que parada, el portero sigue en racha."), "Spanish"),
        (None, "valid"),
    ],
)
async def test_a_bad_pick_gets_one_retry_with_the_problem_fed_back(bad: HostAnswer | None, problem: str) -> None:
    run = Scripted(bad, answer())
    decision = await HostAgent(run, model="m").decide(MOMENT, CANDIDATES)
    assert decision["chosen_manager"] == "mgr_3"
    assert problem in run.sent[1][-1].text


async def test_the_chosen_managers_own_ledger_numbers_may_be_quoted() -> None:
    reason = "Arjun has had 0% of the screen and lost 1 close call, so this 5 IP save is theirs."
    decision = await HostAgent(Scripted(answer(reason=reason)), model="m").decide(MOMENT, CANDIDATES)
    assert decision["reason"] == reason


@pytest.mark.parametrize(
    ("chosen", "true"),
    [("mgr_1", {"scorer_pick"}), ("mgr_3", {"least_airtime", "most_close_calls_lost"})],
)
def test_only_facts_that_hold_against_every_other_candidate_are_true(chosen: str, true: set[str]) -> None:
    assert true_factors(chosen, CANDIDATES) == true


TIED = [c | {"airtime_share": 0.0, "overruled_count": 0} for c in CANDIDATES]  # early in a match (#27)


def test_candidates_level_on_everything_are_a_dead_heat() -> None:
    assert true_factors("mgr_1", TIED) == {"scorer_pick", "dead_heat"}
    assert true_factors("mgr_3", TIED) == {"dead_heat"}
    behind = [*TIED, TIED[0] | {"manager": "mgr_5", "impact_points": 3}]
    assert "dead_heat" in true_factors("mgr_3", behind)  # level with the best; the one behind does not matter
    assert "dead_heat" not in true_factors("mgr_5", behind)
    assert "dead_heat" not in true_factors("mgr_3", CANDIDATES)  # ahead on airtime: not level


async def test_in_a_dead_heat_the_host_may_pick_either_first_time() -> None:
    run = Scripted(answer(because="dead_heat", reason="Too close to split: 5 IP each, so Arjun takes it."))
    decision = await HostAgent(run, model="m").decide(MOMENT, TIED)
    assert (decision["chosen_manager"], decision["status"], len(run.sent)) == ("mgr_3", "ok", 1)


async def test_failing_twice_raises_so_the_workflow_falls_back() -> None:
    with pytest.raises(ValueError, match="checks"):
        await HostAgent(Scripted(answer(chosen="mgr_9"), answer(chosen="mgr_9")), model="m").decide(MOMENT, CANDIDATES)


async def test_the_reason_reads_as_a_sentence_and_never_guesses_a_managers_gender() -> None:
    run = Scripted(answer(reason="you get it: same 5 IP, and Arjun has had his turn."), answer())
    await HostAgent(run, model="m").decide(MOMENT, CANDIDATES)
    assert "'his'" in run.sent[1][-1].text
    lower = await HostAgent(Scripted(answer(reason="you get it: same 5 IP.")), model="m").decide(MOMENT, CANDIDATES)
    assert lower["reason"] == "You get it: same 5 IP."
    padded = await HostAgent(Scripted(answer(reason="  you get it.  ")), model="m").decide(MOMENT, CANDIDATES)
    assert padded["reason"] == "You get it."  # the text checked is the text shown


async def test_dashes_in_a_reason_are_rewritten_in_code_without_a_retry() -> None:
    run = Scripted(answer(reason="Same 5 IP each \u2013 Arjun lost the last close call\u2014so Arjun."))
    decision = await HostAgent(run, model="m").decide(MOMENT, CANDIDATES)
    assert decision["reason"] == "Same 5 IP each, Arjun lost the last close call, so Arjun."
    assert (decision["status"], len(run.sent)) == ("ok", 1)
