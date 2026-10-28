import json
from typing import Any

import pytest
from agent_framework import Message

from watchparty.agents.base import AGENT_FIELDS, RULE_NAMES, fallback_bid, plain_bid
from watchparty.agents.foundry import FailedChecks
from watchparty.agents.manager import Answer, ManagerAgent
from watchparty.points import RULES

pytestmark = pytest.mark.anyio

STAKE: dict[str, Any] = {
    "moment_id": "m_03", "manager": "mgr_2", "t": 900_000, "delta_points": 8, "total_points": 226,
    "rank_before": 1, "rank_after": 1, "reason_code": "save_tier", "above_threshold": True,
    "evidence": [
        {"id": "ev_m_03_minute", "label": "Match minute", "value": 16, "unit": "minute", "source": {"kind": "event"}},
        {"id": "ev_m_03_xg", "label": "xG of the shot", "value": 0.31, "unit": "xg", "source": {"kind": "event"}},
        {"id": "ev_m_03_mgr_2_delta", "label": "Impact points from this moment", "value": 8, "unit": "ip",
         "source": {"kind": "computed", "formula": "moment_delta@v1"}},
        {"id": "ev_m_03_mgr_2_total", "label": "League total after this moment", "value": 226, "unit": "ip",
         "source": {"kind": "computed", "formula": "league_total@v1"}},
    ],
}  # fmt: skip
MANAGER = {"id": "mgr_2", "display_name": "Priya", "style": "analyst", "lang": "en", "squad": ["p_away_1"],
           "captain": "p_away_1"}  # fmt: skip
CONTEXT = {"moment": "Saved! Rhys Mallory denies Jonah Pryce, xG 0.31", "manager": MANAGER,
           "names": ["Rhys Mallory", "Jonah Pryce", "Hollinsford United", "Saltmere Rovers", "Priya"],
           "squad": ["Rhys Mallory"], "captain": "Rhys Mallory"}  # fmt: skip


def answer(**changes: Any) -> Answer:
    base = {"declined": False, "angle": "", "urgency": 0.8,
            "explanation": "Rhys Mallory stops a 0.31 xG chance: 8 IP for Priya.",
            "explanation_en": "", "pitch_to_host": "Priya's captain keeper, 8 IP.",
            "evidence_ids": ["ev_m_03_xg", "ev_m_03_mgr_2_delta"]}  # fmt: skip
    return Answer(**(base | changes))


class Scripted:
    """Stands in for the model: returns the scripted answers in order and records what it was sent."""

    def __init__(self, *answers: Answer | None) -> None:
        self.answers = list(answers)
        self.sent: list[list[Message]] = []

    async def __call__(self, messages: list[Message]) -> tuple[Answer | None, int, int]:
        self.sent.append(messages)
        return self.answers.pop(0), 700, 150


async def test_a_grounded_answer_is_a_bid_first_time() -> None:
    run = Scripted(answer())
    bid = await ManagerAgent(run, model="gpt-5.4-mini").bid(STAKE, CONTEXT)
    assert bid["status"] == "ok"
    assert bid["model"] == "gpt-5.4-mini"
    assert bid["explanation"].startswith("Rhys Mallory")
    assert len(run.sent) == 1


async def test_the_request_carries_style_language_names_and_every_evidence_id() -> None:
    run = Scripted(answer())
    await ManagerAgent(run, model="m").bid(STAKE, CONTEXT)
    data, directive = run.sent[0][0].text.split("\n\n", 1)
    request = json.loads(data)
    assert "ANALYST" in directive
    assert (request["style"], request["language"], request["manager"]) == ("analyst", "en", "Priya")
    assert {e["id"] for e in request["EVIDENCE"]} == {e["id"] for e in STAKE["evidence"]}
    assert "Rhys Mallory" in request["NAMES"]
    assert request["captain"] == "Rhys Mallory"


@pytest.mark.parametrize(
    ("bad", "problem"),
    [
        (answer(explanation="9 IP for Priya."), "'9 IP'"),
        (answer(explanation="Mallory is the new Henrik Vandrup, 8 IP."), "'Henrik'"),
        (answer(pitch_to_host="Bet on Priya!"), "betting"),
        (answer(evidence_ids=["ev_nope"]), "unknown evidence"),
        (answer(explanation="", evidence_ids=["ev_m_03_mgr_2_delta"]), "explanation"),
        (answer(evidence_ids=[]), "cite"),
        (None, "valid"),
    ],
)
async def test_a_failed_check_gets_one_retry_with_the_problem_fed_back(bad: Answer | None, problem: str) -> None:
    run = Scripted(bad, answer())
    bid = await ManagerAgent(run, model="m").bid(STAKE, CONTEXT)
    assert bid["status"] == "retried"
    feedback = run.sent[1][-1].text
    assert problem in feedback


async def test_failing_twice_raises_so_the_workflow_falls_back() -> None:
    run = Scripted(answer(explanation="9 IP."), answer(explanation="10 IP."))
    with pytest.raises(ValueError, match="checks"):
        await ManagerAgent(run, model="m").bid(STAKE, CONTEXT)


async def test_other_languages_need_an_english_copy_that_is_also_checked() -> None:
    spanish = CONTEXT | {"manager": MANAGER | {"lang": "es"}}
    es = "Otra parada de Rhys Mallory, 8 IP."
    run = Scripted(answer(explanation=es), answer(explanation=es, explanation_en="Rhys Mallory, 7 IP."),
                   answer(explanation=es, explanation_en="Rhys Mallory, 8 IP."))  # fmt: skip
    with pytest.raises(ValueError, match="checks"):  # missing English, then a wrong number in the English
        await ManagerAgent(run, model="m").bid(STAKE, spanish)
    bid = await ManagerAgent(run, model="m").bid(STAKE, spanish)
    assert bid["explanation_en"] == "Rhys Mallory, 8 IP."


async def test_urgency_is_kept_between_zero_and_one() -> None:
    bid = await ManagerAgent(Scripted(answer(urgency=3.0)), model="m").bid(STAKE, CONTEXT)
    assert bid["urgency"] == 1.0


async def test_the_request_ends_with_the_style_and_language_directive() -> None:
    run = Scripted(answer(explanation="¡Gran parada, 8 IP!", explanation_en="Big save, 8 IP!"))
    casual_spanish = CONTEXT | {"manager": MANAGER | {"style": "casual", "lang": "es"}}
    await ManagerAgent(run, model="m").bid(STAKE, casual_spanish)
    directive = run.sent[0][0].text.split("\n\n", 1)[1]
    assert "CASUAL" in directive
    assert "15 words" in directive
    assert "Spanish ('es')" in directive
    assert "explanation_en" in directive
    assert "as 0,31" in directive


async def test_casual_requests_carry_only_the_points_swing_and_a_wordless_milestone() -> None:
    run = Scripted(answer(explanation="Big save, 8 IP!", evidence_ids=["ev_m_03_mgr_2_delta"]))
    casual = CONTEXT | {"manager": MANAGER | {"style": "casual"}}
    stake = STAKE | {"milestone": {"name": "save_tier", "progress": 2, "target": 3}}
    await ManagerAgent(run, model="m").bid(stake, casual)
    request = json.loads(run.sent[0][0].text.split("\n\n", 1)[0])
    assert [e["id"] for e in request["EVIDENCE"]] == ["ev_m_03_mgr_2_delta"]
    assert request["milestone"] == "one more save reaches the Wall tier"


@pytest.mark.parametrize(
    ("summary", "plain"),
    [
        ("Saved! Rhys Mallory denies Jonah Pryce, xG 0.31", "Saved! Rhys Mallory denies Jonah Pryce"),
        (
            "Elliot Marsden picks out Jules Ravier with a 31 m through ball.",
            "Elliot Marsden picks out Jules Ravier with a through ball.",
        ),
        ("Leon sprints at 34 km/h to break the press.", "Leon sprints to break the press."),
    ],
)
async def test_casual_requests_get_the_moment_without_its_numbers(summary: str, plain: str) -> None:
    run = Scripted(answer(explanation="Big save, 8 IP!", evidence_ids=["ev_m_03_mgr_2_delta"]))
    casual = CONTEXT | {"manager": MANAGER | {"style": "casual"}, "moment": summary}
    await ManagerAgent(run, model="m").bid(STAKE, casual)
    assert json.loads(run.sent[0][0].text.split("\n\n", 1)[0])["moment"] == plain


async def test_spanish_explanations_are_checked_in_spanish_and_their_english_copy_in_english() -> None:
    spanish = CONTEXT | {"manager": MANAGER | {"lang": "es"}}
    es, en = "Rhys Mallory para una ocasión de 0,31 xG: 8 IP para Priya.", "Rhys Mallory stops a 0.31 xG chance: 8 IP."
    agent = ManagerAgent(Scripted(), model="m")
    assert agent.problems(answer(explanation=es, explanation_en=en), STAKE, spanish) == []
    # 0,31 is a Spanish decimal: read as English, as the copy is, it is a 0 nobody cited (#32)
    swapped = agent.problems(answer(explanation=es, explanation_en=en.replace("0.31", "0,31")), STAKE, spanish)
    assert any("'0' is not a value" in p for p in swapped)
    mixed = agent.problems(answer(explanation=es.replace("ocasión", "chance"), explanation_en=en), STAKE, spanish)
    assert any("'chance' is English" in p for p in mixed)
    bid = await ManagerAgent(Scripted(answer(explanation=es, explanation_en=en)), model="m").bid(STAKE, spanish)
    assert bid["status"] == "ok"
    directive = await _directive(spanish)
    assert "Spanish" in directive
    assert "puesto" in directive  # places agree with the place, not a guessed gender


def test_the_decimal_separator_follows_each_fields_language() -> None:
    # A Spanish manager's explanation writes 0,31; its English copy and the host pitch write 0.31 (#85)
    spanish = CONTEXT | {"manager": MANAGER | {"lang": "es"}}
    es, en = "Rhys Mallory para una ocasión de 0,31 xG: 8 IP para Priya.", "Rhys Mallory stops a 0.31 xG chance: 8 IP."
    pitch = "Priya's captain keeper stops a 0.31 xG chance."
    agent = ManagerAgent(Scripted(), model="m")
    assert agent.problems(answer(explanation=es, explanation_en=en, pitch_to_host=pitch), STAKE, spanish) == []
    point = agent.problems(answer(explanation=es.replace("0,31", "0.31"), explanation_en=en, pitch_to_host=pitch),
                           STAKE, spanish)  # fmt: skip
    assert point == ["'0.31' uses a decimal point: in Spanish write '0,31'"]


async def _directive(context: dict[str, Any]) -> str:
    run = Scripted(answer(explanation="Gran parada de Rhys Mallory: 8 IP.", explanation_en="Big save: 8 IP."))
    await ManagerAgent(run, model="m").bid(STAKE, context)
    return str(run.sent[0][0].text.split("\n\n", 1)[1])


async def test_the_english_copy_keeps_the_style_and_one_language() -> None:
    casual = CONTEXT | {"manager": MANAGER | {"style": "casual", "lang": "es"}}
    es = "¡Gran parada de Rhys Mallory, 8 IP!"
    agent = ManagerAgent(Scripted(), model="m")
    long_en = "Rhys Mallory saves. That is 8 IP for you."  # two sentences: not casual (#36)
    assert any("explanation_en" in p for p in agent.problems(answer(explanation=es, explanation_en=long_en),
                                                               STAKE, casual))  # fmt: skip
    mixed = "Big parada from Rhys Mallory, 8 IP!"
    assert agent.problems(answer(explanation=es, explanation_en=mixed), STAKE, casual)
    assert (
        agent.problems(answer(explanation=es, explanation_en="Big save from Rhys Mallory, 8 IP!"), STAKE, casual) == []
    )


@pytest.mark.parametrize("lang", ["en", "es"])
@pytest.mark.parametrize("style", ["casual", "analyst"])
@pytest.mark.parametrize("delta", [8, -1.5])
def test_the_plain_bid_passes_the_agents_own_checks_in_every_language(lang: str, style: str, delta: float) -> None:
    evidence = [e | {"value": delta} if e["id"].endswith("_delta") else e for e in STAKE["evidence"]]
    stake = STAKE | {"delta_points": delta, "evidence": evidence}
    manager = MANAGER | {"style": style, "lang": lang}
    bid = plain_bid(stake, manager)
    assert bid["lang"] == lang
    assert bool(bid["explanation_en"]) is (lang != "en")  # Spanish and its English copy, from one template (#36)
    context = CONTEXT | {"manager": manager}
    fields = {k: bid[k] for k in AGENT_FIELDS} | {"declined": False, "angle": ""}
    assert ManagerAgent(Scripted(), model="m").problems(Answer(**fields), stake, context) == []


@pytest.mark.parametrize("lang", ["en", "es"])
def test_the_plain_bid_for_a_moment_worth_nothing_is_a_decline_not_plus_zero(lang: str) -> None:
    # #88 row 9: the plain stat would read "+0 IP for you from that moment"
    evidence = [e | {"value": 0} if e["id"].endswith("_delta") else e for e in STAKE["evidence"]]
    stake = STAKE | {"delta_points": 0, "rank_before": 3, "rank_after": 2, "evidence": evidence}
    bid = plain_bid(stake, MANAGER | {"lang": lang})
    assert bid == {"moment_id": "m_03", "manager": "mgr_2", "style": "analyst", "lang": lang, "status": "declined"}
    assert "+0 IP" in fallback_bid(stake, MANAGER)["explanation"]  # what it replaces


def test_every_reason_has_a_name_in_every_language() -> None:
    for names in RULE_NAMES.values():
        assert set(names) == {*RULES["rules"], "no_change"}


async def test_an_english_answer_written_in_the_copy_field_is_taken_as_the_explanation() -> None:
    text = "Rhys Mallory stops a 0.31 xG chance: 8 IP for Priya."
    run = Scripted(answer(explanation="", explanation_en=text))  # the English-first schema invites this slip
    bid = await ManagerAgent(run, model="m").bid(STAKE, CONTEXT)
    assert (bid["explanation"], bid["explanation_en"], bid["status"]) == (text, "", "ok")


async def test_an_english_answer_with_a_copy_too_never_shows_the_unchecked_copy() -> None:
    sneaky = answer(explanation_en="Vasquith would have scored 99, place your bets!")  # #49: an unchecked field
    bid = await ManagerAgent(Scripted(sneaky), model="m").bid(STAKE, CONTEXT)
    assert (bid["explanation"], bid["explanation_en"]) == (answer().explanation, "")


async def test_dashes_in_an_english_bid_are_rewritten_in_code_without_a_retry() -> None:
    dashed = answer(explanation="Rhys Mallory stops a 0.31 xG chance \u2014 8 IP for Priya.",
                    pitch_to_host="Priya's captain keeper \u2013 8 IP.")  # fmt: skip
    run = Scripted(dashed)
    bid = await ManagerAgent(run, model="m").bid(STAKE, CONTEXT)
    assert (bid["explanation"], bid["pitch_to_host"], bid["status"]) == (
        "Rhys Mallory stops a 0.31 xG chance, 8 IP for Priya.",
        "Priya's captain keeper, 8 IP.",
        "ok",
    )
    assert len(run.sent) == 1  # no model call spent on a dash (#82)


async def test_dashes_in_a_spanish_bid_and_its_english_copy_are_rewritten() -> None:
    spanish = CONTEXT | {"manager": MANAGER | {"lang": "es"}}
    es = "Rhys Mallory para una ocasión de 0,31 xG \u2014 8 IP para Priya."
    en = "Rhys Mallory stops a 0.31 xG chance \u2014 8 IP."
    run = Scripted(answer(explanation=es, explanation_en=en))
    bid = await ManagerAgent(run, model="m").bid(STAKE, spanish)
    assert bid["explanation"] == "Rhys Mallory para una ocasión de 0,31 xG, 8 IP para Priya."
    assert bid["explanation_en"] == "Rhys Mallory stops a 0.31 xG chance, 8 IP."
    assert (bid["status"], len(run.sent)) == ("ok", 1)


async def test_a_loss_written_across_a_dash_keeps_its_sign() -> None:
    lost = STAKE | {"evidence": [e | {"value": -8} if e["id"].endswith("_delta") else e for e in STAKE["evidence"]]}
    down = answer(explanation="Down \u2014 8 IP for Priya.", pitch_to_host="Priya, down \u2014 8 IP.",
                  evidence_ids=["ev_m_03_mgr_2_delta"])  # fmt: skip
    bid = await ManagerAgent(Scripted(down), model="m").bid(lost, CONTEXT)
    assert (bid["explanation"], bid["status"]) == ("Down, 8 IP for Priya.", "ok")
    with pytest.raises(FailedChecks, match="'8 IP' is not a value"):  # for a gain of 8 it still reads as a loss
        await ManagerAgent(Scripted(down, down), model="m").bid(STAKE, CONTEXT)


def test_the_prompt_asks_for_numbers_as_given_not_rounded() -> None:
    from watchparty.agents.manager import INSTRUCTIONS

    assert "Write whole numbers without decimals" not in INSTRUCTIONS  # it invited rounding 3.5 (#49)
    assert "as given" in INSTRUCTIONS


# #138: the pitch is the group screen's line, so it is written for the room


@pytest.mark.parametrize(
    ("pitch", "problem"),
    [("Show this, and you're up in the league.", "'you'"), ("Show my moment.", "'my'"),
     ("Priya's captain returned 8 actual IP against 226 expected.", "not both")],
)  # fmt: skip
async def test_a_pitch_that_addresses_someone_or_stacks_facts_is_retried(pitch: str, problem: str) -> None:
    run = Scripted(answer(pitch_to_host=pitch), answer())
    bid = await ManagerAgent(run, model="m").bid(STAKE, CONTEXT)
    assert bid["status"] == "retried"
    assert problem in run.sent[1][-1].text
    assert bid["pitch_to_host"] == "Priya's captain keeper, 8 IP."


async def test_the_phone_explanation_still_speaks_to_its_manager() -> None:
    run = Scripted(answer(explanation="Rhys Mallory saves, 8 IP for you."))
    bid = await ManagerAgent(run, model="m").bid(STAKE, CONTEXT)
    assert (bid["explanation"], bid["status"]) == ("Rhys Mallory saves, 8 IP for you.", "ok")


async def test_the_judges_own_pitch_may_say_you() -> None:
    judge = CONTEXT | {"manager": MANAGER | {"display_name": "You"}, "names": ["Rhys Mallory", "Jonah Pryce", "You"]}
    mine = answer(explanation="Rhys Mallory saves, 8 IP for you.", pitch_to_host="Your captain keeper, 8 IP.")
    bid = await ManagerAgent(Scripted(mine), model="m").bid(STAKE, judge)
    assert bid["status"] == "ok"


@pytest.mark.parametrize(("name", "shown"), [("Priya", "Priya"), ("You", "you")])
@pytest.mark.parametrize("lang", ["en", "es"])
def test_the_plain_pitch_is_one_english_fact_about_the_manager(name: str, shown: str, lang: str) -> None:
    bid = fallback_bid(STAKE, MANAGER | {"display_name": name, "lang": lang})
    assert bid["pitch_to_host"] == f"+8 IP for {shown} from this moment (save tier)."


# #141: a pitch that fails twice no longer costs the phone its explanation


async def test_a_pitch_that_fails_twice_keeps_the_phone_text_and_takes_codes_line() -> None:
    phone = "Rhys Mallory stops a 0.31 xG chance."  # cites the xG only; code's line adds the swing it quotes
    bad = answer(explanation=phone, pitch_to_host="Show my moment.", evidence_ids=["ev_m_03_xg"])
    bid = await ManagerAgent(Scripted(bad, bad), model="m").bid(STAKE, CONTEXT)
    assert (bid["explanation"], bid["status"], bid["pitch_source"]) == (phone, "retried", "code")
    assert bid["pitch_to_host"] == "+8 IP for Priya from this moment (save tier)."
    assert bid["evidence_ids"] == ["ev_m_03_xg", "ev_m_03_mgr_2_delta"]


async def test_a_good_pitch_is_the_agents_own() -> None:
    bid = await ManagerAgent(Scripted(answer()), model="m").bid(STAKE, CONTEXT)
    assert (bid["pitch_source"], bid["pitch_to_host"]) == ("manager", "Priya's captain keeper, 8 IP.")


async def test_a_bad_explanation_still_falls_back_whole_even_with_a_bad_pitch() -> None:
    bad = answer(explanation="9 IP for Priya.", pitch_to_host="Show my moment.")
    with pytest.raises(FailedChecks):
        await ManagerAgent(Scripted(bad, bad), model="m").bid(STAKE, CONTEXT)


@pytest.mark.parametrize(("delta", "says"), [(-3, "COST"), (8, "GAINED")])
async def test_the_request_tells_the_agent_which_way_the_swing_went(delta: float, says: str) -> None:
    evidence = [e | {"value": delta} if e["id"].endswith("_delta") else e for e in STAKE["evidence"]]
    pitch = "Rhys Mallory's save costs Priya 3 IP." if delta < 0 else "Priya's captain keeper, 8 IP."
    run = Scripted(answer(explanation=f"Rhys Mallory, {delta:g} IP.", pitch_to_host=pitch))
    await ManagerAgent(run, model="m").bid(STAKE | {"delta_points": delta, "evidence": evidence}, CONTEXT)
    assert says in run.sent[0][0].text.split("\n\n", 1)[1]


async def test_a_pitch_the_wrong_way_round_is_retried() -> None:
    lost = STAKE | {
        "delta_points": -3,
        "evidence": [e | {"value": -3} if e["id"].endswith("_delta") else e for e in STAKE["evidence"]],
    }
    upbeat = answer(explanation="Rhys Mallory, -3 IP.", pitch_to_host="A swing that can protect Priya's lead.")
    run = Scripted(upbeat, answer(explanation="Rhys Mallory, -3 IP.", pitch_to_host="A blow for Priya."))
    bid = await ManagerAgent(run, model="m").bid(lost, CONTEXT)
    assert (bid["status"], bid["pitch_to_host"]) == ("retried", "A blow for Priya.")
    assert "loss" in run.sent[1][-1].text
