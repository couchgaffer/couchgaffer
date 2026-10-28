import asyncio
import copy
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from functools import partial
from typing import Any

import httpx
import httpx2
import openai
import pytest
from agent_framework import BaseChatClient, ChatResponse, Content, FunctionInvocationLayer, Message
from agent_framework.exceptions import ChatClientException
from opentelemetry import trace

from watchparty.agents.base import written
from watchparty.agents.checks import SUMMARY
from watchparty.agents.foundry import FailedChecks, chat_runner
from watchparty.agents.summary import INSTRUCTIONS as SUMMARY_INSTRUCTIONS
from watchparty.agents.summary import SummaryAgent, SummaryAnswer, fallback_summary, summary_problems
from watchparty.app.main import create_app
from watchparty.app.session import Session, why_summary_fell_back
from watchparty.app.workflow import fell_back
from watchparty.matchtools import tools as match_tools
from watchparty.matchtools.view import Given

pytestmark = pytest.mark.anyio

SERVED = {
    "ev_table_mgr_2_points": {
        "id": "ev_table_mgr_2_points",
        "label": "Priya: league points",
        "value": 231,
        "unit": "ip",
    },
    "ev_table_mgr_2_rank": {"id": "ev_table_mgr_2_rank", "label": "Priya: league rank", "value": 1, "unit": "count"},
    "ev_squad_mgr_2_total": {
        "id": "ev_squad_mgr_2_total",
        "label": "Priya: IP gained this match",
        "value": 13,
        "unit": "ip",
    },
}
PRIYA = {"id": "mgr_2", "display_name": "Priya", "style": "analyst", "lang": "en", "squad": ["p_away_1"],
         "captain": "p_away_1"}  # fmt: skip
NAMES = ["Priya", "Rhys Mallory", "Saltmere Rovers", "Hollinsford United"]
CITED = ["ev_table_mgr_2_points", "ev_table_mgr_2_rank"]


def answer(text: str = "Top of the league on 231 IP at the break.", text_en: str = "",
           cited: list[str] | None = None) -> SummaryAnswer:  # fmt: skip
    return SummaryAnswer(text=text, text_en=text_en, evidence_ids=CITED if cited is None else cited, rulebook_ids=[])


class Scripted:
    """Stands in for the model; records the tools it was given and what it was sent."""

    def __init__(self, *answers: SummaryAnswer | None) -> None:
        self.answers = list(answers)
        self.tools: list[list[Any]] = []
        self.sent: list[list[Message]] = []

    def __call__(self, tools: list[Any]) -> Callable[[list[Message]], Any]:
        self.tools.append(tools)

        async def run(messages: list[Message]) -> tuple[SummaryAnswer | None, int, int]:
            self.sent.append(messages)
            return self.answers.pop(0), 900, 80

        return run


async def test_a_grounded_summary_passes_first_time_and_the_agent_gets_the_tools() -> None:
    model = Scripted(answer())
    out = await SummaryAgent(model, model="gpt-5.4-mini").summarise(PRIYA, "half_time", "TOOLS", SERVED, NAMES)
    assert (out["text"], out["status"], out["model"]) == (answer().text, "ok", "gpt-5.4-mini")
    assert model.tools == [["TOOLS"]]


async def test_the_request_names_the_manager_the_whistle_and_the_tools_to_use() -> None:
    model = Scripted(answer(text="Líder de la liga con 231 IP al descanso.", text_en="Top of the league on 231 IP."))
    await SummaryAgent(model, model="m").summarise(PRIYA | {"lang": "es"}, "full_time", None, SERVED, NAMES)
    data, directive = model.sent[0][0].text.split("\n\n", 1)
    request = json.loads(data)
    assert (request["manager_id"], request["manager"], request["phase"]) == ("mgr_2", "Priya", "full time")
    assert (request["style"], request["language"], request["NAMES"]) == ("analyst", "es", NAMES)
    assert "Spanish" in directive
    assert "get_squad_points" in directive
    assert "explanation_en" not in directive
    assert "text_en" in directive
    assert "as 0,31" in directive


@pytest.mark.parametrize(
    ("bad", "problem"),
    [
        (answer(text="Top of the league on 240 IP."), "'240 IP'"),  # not a number the tools served
        (answer(cited=["ev_made_up"]), "unknown evidence"),
        (answer(cited=[]), "cite"),
        (answer(text="Top on 231 IP, like Castlebridge."), "'Castlebridge'"),
        (answer(text="Top on 231 IP: the jackpot!"), "betting"),
        (answer(text="One. Two. Three. Four sentences is too many."), "sentences"),
    ],
)
async def test_a_bad_summary_gets_one_retry_with_the_problem_fed_back(bad: SummaryAnswer, problem: str) -> None:
    model = Scripted(bad, answer())
    out = await SummaryAgent(model, model="m").summarise(PRIYA, "half_time", None, SERVED, NAMES)
    assert out["status"] == "retried"
    assert problem in model.sent[1][-1].text


async def test_casual_summaries_are_short_with_at_most_two_numbers() -> None:
    casual = PRIYA | {"style": "casual"}
    three = {"text": "Top on 231 IP, rank 1, and 13 IP gained.", "text_en": "", "evidence_ids": [*SERVED]}
    assert summary_problems(three, casual, SERVED, NAMES)
    two = three | {"text": "You're top on 231 IP at the break, 13 IP up!"}
    assert summary_problems(two, casual, SERVED, NAMES) == []


async def test_other_languages_need_a_checked_english_copy() -> None:
    spanish = PRIYA | {"lang": "es"}
    text = {"text": "Priya sigue arriba con 231 IP.", "evidence_ids": CITED}
    assert summary_problems(text | {"text_en": ""}, spanish, SERVED, NAMES)
    assert summary_problems(text | {"text_en": "Priya tops on 232 IP."}, spanish, SERVED, NAMES)
    assert summary_problems(text | {"text_en": "Priya sigue top on 231 IP."}, spanish, SERVED, NAMES)  # #36
    assert summary_problems(text | {"text_en": "Priya tops the league on 231 IP."}, spanish, SERVED, NAMES) == []


async def no_sleep(_: float) -> None:
    await asyncio.sleep(0)


async def played(**kwargs: Any) -> Session:
    s = Session(seed=267, manager="mgr_1", style="casual", lang="en", speed=30, sleep=no_sleep, **kwargs)
    await s.run()
    return s


def summaries(s: Session) -> list[dict[str, Any]]:
    return [c for c in s.log if c.get("kind") == "summary"]


async def test_every_phone_gets_a_half_time_and_a_full_time_summary_even_with_no_agent(
    contract_errors: Callable[[str, Any], list[str]],
) -> None:
    s = await played()
    got = summaries(s)
    assert sorted((c["screen"], c["payload"]["headline"]) for c in got) == sorted(
        (f"phone:mgr_{n}", h) for n in range(1, 6) for h in ("Half time", "Full time")
    )
    for cue in got:
        assert cue["source"] == "fallback"
        assert contract_errors("cue", cue) == []
        phase = "half_time" if cue["payload"]["headline"] == "Half time" else "full_time"
        manager = s.managers[cue["payload"]["manager"]]
        assert cue["payload"]["lang"] == manager["lang"]  # in the manager's own language (#36)
        cited = {"text": cue["payload"]["body"], "text_en": cue["payload"].get("body_en", ""),
                 "evidence_ids": [e["id"] for e in cue["payload"]["evidence"]]}  # fmt: skip
        assert summary_problems(cited, manager, s.views[phase].served_to(manager["id"]), s.allowed_names) == []


@pytest.mark.parametrize("lang", ["en", "es"])
@pytest.mark.parametrize("style", ["casual", "analyst"])
async def test_the_code_summary_passes_its_own_checks_in_every_language(style: str, lang: str) -> None:
    s = await played()
    for manager in s.league["managers"]:
        for phase in ("half_time", "full_time"):
            mine = manager | {"style": style, "lang": lang}
            out = fallback_summary(s.views[phase], mine, phase)
            english = out["text_en"] or out["text"]  # Spanish and its English copy, from one template (#36)
            assert english.startswith("Half time" if phase == "half_time" else "Full time")
            assert bool(out["text_en"]) is (lang != "en")
            assert summary_problems(out, mine, s.views[phase].served_to(mine["id"]), s.allowed_names) == [], out


async def test_a_spanish_manager_whose_summary_times_out_gets_spanish_with_an_english_copy() -> None:
    class Slow(ToolUser):
        async def summarise(self, *args: Any) -> dict[str, Any]:
            await asyncio.sleep(5)
            return {}

    s = Session(seed=267, manager="mgr_1", style="analyst", lang="es", speed=30, sleep=no_sleep,
                summary_agent=Slow(), mcp_connect=fake_connect, summary_deadline_s=0.05)  # fmt: skip
    await s.run()
    mine = [c["payload"] for c in summaries(s) if c["screen"] == "phone:mgr_1"]
    assert [(p["lang"], p["body"][:3], p["body_en"][:4]) for p in mine] == [
        ("es", "Al ", "Half"),
        ("es", "Fin", "Full"),
    ]


def test_the_request_states_the_length_the_checks_allow() -> None:
    for style in ("casual", "analyst"):
        model = Scripted(answer())
        asyncio.run(
            SummaryAgent(model, model="m").summarise(PRIYA | {"style": style}, "half_time", None, SERVED, NAMES)
        )
        assert f"at most {SUMMARY[style][2]} characters" in model.sent[0][0].text


def test_a_summary_is_drafted_in_english_first() -> None:
    assert list(SummaryAnswer.model_json_schema()["properties"])[:2] == ["text_en", "text"]  # as explanations (#36)
    model = Scripted(answer(text="Priya sigue arriba con 231 IP.", text_en="Priya tops the league on 231 IP."))
    asyncio.run(SummaryAgent(model, model="m").summarise(PRIYA | {"lang": "es"}, "full_time", None, SERVED, NAMES))
    directive = model.sent[0][0].text.split("\n\n", 1)[1]
    assert directive.index("text_en in English") < directive.index("translation")


class ToolUser:
    """A fake summary agent that really calls the match tools over MCP, then quotes one number."""

    def __init__(self, lie: bool = False) -> None:
        self.lie, self.called = lie, 0

    async def summarise(self, manager: dict[str, Any], phase: str, tools: Any, served: dict[str, dict[str, Any]],
                        names: list[str], rulebook: Given | None = None) -> dict[str, Any]:  # fmt: skip
        self.called += 1
        [content] = await tools.call_tool("get_standings")
        points = next(e for e in json.loads(content.text)["evidence"] if e["id"] == f"ev_table_{manager['id']}_points")
        assert served[points["id"]] == points  # what the tools served is what the agent's own checks see
        value = points["value"] + (1 if self.lie else 0)
        english = f"You are on {value:g} IP."
        if manager["lang"] == "en":
            return {"text": english, "text_en": "", "evidence_ids": [points["id"]], "status": "ok"}
        spanish = f"Estás en {written(value, 'es')} IP."  # other languages carry an English copy (#16)
        return {"text": spanish, "text_en": english, "evidence_ids": [points["id"]], "status": "ok"}


class Span:
    def __init__(self) -> None:
        self.exceptions: list[dict[str, Any]] = []
        self.status: Any = None

    def add_event(self, name: str, attributes: dict[str, Any]) -> None:
        if name == "exception":
            self.exceptions.append(attributes)

    def set_status(self, status: Any) -> None:
        self.status = status

    def set_attribute(self, key: str, value: Any) -> None: ...

    def set_attributes(self, attributes: dict[str, Any]) -> None: ...


class SummaryTracer:
    """Hands every summary the same recording span, and every other span a throwaway one."""

    def __init__(self, span: Span) -> None:
        self.span = span

    @contextmanager
    def start_as_current_span(self, name: str, **_: Any) -> Iterator[Span]:
        yield self.span if name == "summary" else Span()


@pytest.mark.parametrize(("lie", "source"), [(False, "manager"), (True, "fallback")])
async def test_summaries_come_from_the_mcp_tools_and_only_served_numbers_reach_a_phone(
    lie: bool, source: str, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, span = ToolUser(lie), Span()
    monkeypatch.setattr("watchparty.app.session.tracer", SummaryTracer(span))
    app = create_app(session_kwargs={"sleep": no_sleep, "summary_agent": agent})
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            made = (await http.post("/api/sessions", json={"seed": "demo", "manager": "mgr_1"})).json()
        s = app.state.store.get(made["session_id"])
        with caplog.at_level(logging.WARNING, logger="watchparty"):
            await s.run()
    assert agent.called == 10
    assert {c["source"] for c in summaries(s)} == {source}
    # The session's own re-check catches the lie: logged with its problem, and an error on the trace (#84)
    lines = fallback_lines(caplog)
    assert len(lines) == (10 if lie else 0)
    assert all("fell back to code: failed the session's checks: " in line and "is not a value" in line
               for line in lines), lines  # fmt: skip
    assert (len(span.exceptions), span.status is not None) == ((10, True) if lie else (0, False))
    half = next(c for c in summaries(s) if c["screen"] == "phone:mgr_2" and c["payload"]["headline"] == "Half time")
    if not lie:
        assert half["payload"]["evidence"] == [s.views["half_time"].served_to("mgr_2")["ev_table_mgr_2_points"]]


class Borrower(ToolUser):
    """A fake summary agent that writes every manager's summary about mgr_2's squad."""

    async def summarise(self, manager: dict[str, Any], phase: str, tools: Any, served: dict[str, dict[str, Any]],
                        names: list[str], rulebook: Given | None = None) -> dict[str, Any]:  # fmt: skip
        [content] = await tools.call_tool("get_squad_points", manager="mgr_2")
        total = next(e for e in json.loads(content.text)["evidence"] if e["id"] == "ev_squad_mgr_2_total")
        english = f"Your squad has {total['value']:g} IP."
        spanish = f"Tu equipo suma {written(total['value'], 'es')} IP."
        text, text_en = (english, "") if manager["lang"] == "en" else (spanish, english)
        return {"text": text, "text_en": text_en, "evidence_ids": [total["id"]], "status": "ok"}


async def test_a_summary_may_cite_only_its_own_managers_squad() -> None:
    app = create_app(session_kwargs={"sleep": no_sleep, "summary_agent": Borrower()})
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            made = (await http.post("/api/sessions", json={"seed": "demo", "manager": "mgr_1"})).json()
        s = app.state.store.get(made["session_id"])
        await s.run()
    sources = {(c["screen"], c["source"]) for c in summaries(s)}  # #54: fetched by mgr_2's agent too, at once
    assert sources == {("phone:mgr_2", "manager")} | {(f"phone:mgr_{n}", "fallback") for n in (1, 3, 4, 5)}


async def test_a_slow_summary_falls_back_on_time() -> None:
    class Slow(ToolUser):
        async def summarise(self, *args: Any) -> dict[str, Any]:
            await asyncio.sleep(5)
            return await super().summarise(*args)  # a valid summary: only the deadline can stop it (#32)

    s = Session(seed=267, manager="mgr_1", style="casual", lang="en", speed=30, sleep=no_sleep,
                summary_agent=Slow(), mcp_connect=fake_connect, summary_deadline_s=0.05)  # fmt: skip
    start = time.perf_counter()
    await s.run()
    assert time.perf_counter() - start < 2  # ten summaries, three at a time, each cut off at 0.05 s
    assert {c["source"] for c in summaries(s)} == {"fallback"}
    assert len(summaries(s)) == 10


class _NoTools:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: Any) -> None:
        return None


def fake_connect(session_id: str, token: str, at: str, caller: str) -> _NoTools:
    return _NoTools()


def test_a_summary_may_cite_only_rulebook_entries_its_own_lookup_returned() -> None:
    cited = {"text": "Top of the league on 231 IP.", "text_en": "", "evidence_ids": CITED}
    mine = {"rule-save-points": {"title": "Save", "reason": "Stopping a big chance matters more."}}
    assert summary_problems(cited | {"rulebook_ids": ["rule-save-points"]}, PRIYA, SERVED, NAMES, mine) == []
    # an entry another manager's agent retrieved, which this one never read
    problems = summary_problems(cited | {"rulebook_ids": ["rule-goal"]}, PRIYA, SERVED, NAMES, mine)
    assert any("rulebook" in p for p in problems)


def test_a_summary_that_looked_up_the_rulebook_must_cite_it() -> None:
    cited = {"text": "Top of the league on 231 IP.", "text_en": "", "evidence_ids": CITED, "rulebook_ids": []}
    mine = {"rule-save-points": {"title": "Save", "reason": "Stopping a big chance matters more."}}
    assert any("rulebook_ids" in p for p in summary_problems(cited, PRIYA, SERVED, NAMES, mine))
    assert summary_problems(cited, PRIYA, SERVED, NAMES, {}) == []  # no lookup, nothing to cite


def test_number_words_in_a_reason_pass_when_the_number_is_evidence() -> None:
    served = SERVED | {"ev_saves": {"id": "ev_saves", "label": "Saves by Rhys Mallory", "value": 3}}
    text = {"text": "Top on 231 IP: three saves reached the Wall tier.", "text_en": "", "rulebook_ids": [],
            "evidence_ids": [*CITED, "ev_saves"]}  # fmt: skip
    assert summary_problems(text, PRIYA, served, NAMES, {}) == []


async def test_the_request_points_the_agent_to_the_rulebook() -> None:
    model = Scripted(answer())
    await SummaryAgent(model, model="m").summarise(PRIYA, "full_time", None, SERVED, NAMES)
    request = model.sent[0][0].text
    assert "get_rule_explanation" in request
    assert "rulebook_ids" in request  # ids go there, never among the evidence
    assert "any number must come from the match tools" in request


class RuleCiter(ToolUser):
    """A fake agent whose rulebook lookup served one entry, which it then cites."""

    async def summarise(self, manager: dict[str, Any], phase: str, tools: Any, served: dict[str, dict[str, Any]],
                        names: list[str], rulebook: Given | None = None) -> dict[str, Any]:  # fmt: skip
        assert rulebook is not None
        rulebook["rule-save-points"] = {
            "title": "Save, worth more against a better chance",
            "reason": "Stopping a big chance matters more.",
        }  # as the tool records it
        out = await super().summarise(manager, phase, tools, served, names)
        return out | {"rulebook_ids": ["rule-save-points"]}


async def test_rulebook_citations_reach_the_phone_with_their_titles() -> None:
    app = create_app(session_kwargs={"sleep": no_sleep, "summary_agent": RuleCiter()})
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            made = (await http.post("/api/sessions", json={"seed": "demo", "manager": "mgr_1"})).json()
        s = app.state.store.get(made["session_id"])
        await s.run()
    for cue in summaries(s):
        assert cue["source"] == "manager"
        assert cue["payload"]["rulebook"] == [{"id": "rule-save-points",
                                               "title": "Save, worth more against a better chance"}]  # fmt: skip


async def test_at_most_three_summaries_run_at_once() -> None:
    # Why: summaries share the model with the second half's live bids. This checks the cap, not bid latency.
    class Counting(ToolUser):
        def __init__(self) -> None:
            super().__init__()
            self.now = self.most = 0

        async def summarise(self, *args: Any) -> dict[str, Any]:
            self.now += 1
            self.most = max(self.most, self.now)
            await asyncio.sleep(0.01)
            self.now -= 1
            return {}  # falls back; only the overlap matters here

    agent = Counting()
    s = Session(seed=267, manager="mgr_1", style="casual", lang="en", speed=30, sleep=no_sleep,
                summary_agent=agent, mcp_connect=fake_connect)  # fmt: skip
    await s.run()
    assert agent.most == 3
    assert s.summary_deadline_s == 40


async def test_every_view_of_a_match_spends_one_rulebook_budget() -> None:
    s = Session(seed=267, manager="mgr_1", style="casual", lang="en", sleep=no_sleep)
    await s.run()
    assert s.views["half_time"].lookups is s.lookups
    assert s.views["full_time"].lookups is s.lookups
    assert s.live_view().lookups is s.lookups  # a question asked at half time is not paid for again


def test_the_english_copy_of_a_summary_keeps_the_style() -> None:
    casual = PRIYA | {"lang": "es", "style": "casual"}
    es = {"text": "Priya sigue arriba con 231 IP.", "evidence_ids": CITED}
    three = "Priya tops the league. Still 231 IP. Lovely."
    assert any("text_en" in p for p in summary_problems(es | {"text_en": three}, casual, SERVED, NAMES))
    assert summary_problems(es | {"text_en": "Priya tops the league on 231 IP."}, casual, SERVED, NAMES) == []


async def test_an_english_summary_written_in_the_copy_field_is_taken_as_the_text() -> None:
    model = Scripted(answer(text="", text_en="Top of the league on 231 IP at the break."))
    out = await SummaryAgent(model, model="m").summarise(PRIYA, "half_time", None, SERVED, NAMES)
    assert (out["text"], out["text_en"], out["status"]) == ("Top of the league on 231 IP at the break.", "", "ok")


async def test_an_english_summary_with_a_copy_too_drops_the_unchecked_copy() -> None:
    model = Scripted(answer(text_en="Vasquith would have scored 99, place your bets!"))  # #49
    out = await SummaryAgent(model, model="m").summarise(PRIYA, "half_time", None, SERVED, NAMES)
    assert (out["text"], out["text_en"]) == ("Top of the league on 231 IP at the break.", "")


def test_a_spanish_summary_writes_decimals_with_a_comma_and_its_english_copy_with_a_point() -> None:
    served = SERVED | {"ev_xg": {"id": "ev_xg", "label": "xG of the best chance", "value": 0.13, "unit": "xg"}}
    spanish, cited = PRIYA | {"lang": "es"}, [*CITED, "ev_xg"]
    es = "Priya sigue arriba con 231 IP tras una ocasión de 0.13 xG."
    en = "Priya tops the league on 231 IP after a 0.13 xG chance."
    point = {"text": es, "text_en": en, "evidence_ids": cited}
    assert summary_problems(point, spanish, served, NAMES) == ["'0.13' uses a decimal point: in Spanish write '0,13'"]
    comma = point | {"text": es.replace("0.13", "0,13")}
    assert summary_problems(comma, spanish, served, NAMES) == []  # #85


# Why a summary fell back, in the logs (#84): the smoke counts fallbacks but cannot tell their causes apart.


def fallback_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if "summary for" in r.getMessage()]


async def test_a_summary_that_times_out_is_logged_as_a_timeout_with_its_elapsed_time(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Slow(ToolUser):
        async def summarise(self, *args: Any) -> dict[str, Any]:
            await asyncio.sleep(5)
            return {}

    with caplog.at_level(logging.WARNING, logger="watchparty"):
        await played(summary_agent=Slow(), mcp_connect=fake_connect, summary_deadline_s=0.05)
    lines = fallback_lines(caplog)
    assert len(lines) == 10
    assert all(re.search(r"fell back to code: timeout after 0\.\d s$", line) for line in lines), lines


class Always:
    """Stands in for the model, giving the same answer on every attempt."""

    def __init__(self, given: SummaryAnswer) -> None:
        self.given = given

    def __call__(self, tools: list[Any]) -> Callable[[list[Message]], Any]:
        async def run(messages: list[Message]) -> tuple[SummaryAnswer, int, int]:
            return self.given, 900, 80

        return run


async def test_a_summary_rejected_twice_is_logged_with_the_checkers_problems(caplog: pytest.LogCaptureFixture) -> None:
    uncited = SummaryAnswer(text="You lead the league.", text_en="", evidence_ids=[], rulebook_ids=[])
    with caplog.at_level(logging.WARNING, logger="watchparty"):
        s = await played(summary_agent=SummaryAgent(Always(uncited), model="m"), mcp_connect=fake_connect)
    assert {c["source"] for c in summaries(s)} == {"fallback"}
    lines = fallback_lines(caplog)
    assert len(lines) == 10
    assert all("fell back to code: failed the checks twice: cite at least one evidence id" in line for line in lines)


async def test_the_checker_error_carries_the_second_attempts_problems() -> None:
    model = Scripted(answer(cited=[]), answer(text="Top of the league.", cited=["ev_made_up"]))
    with pytest.raises(FailedChecks) as failed:
        await SummaryAgent(model, model="m").summarise(PRIYA, "half_time", None, SERVED, NAMES)
    assert failed.value.problems  # the second attempt's problems, not the first's
    assert "cite at least one evidence id" not in failed.value.problems
    assert isinstance(failed.value, ValueError)  # which every agent's caller already catches


class Unreachable(ToolUser):
    async def summarise(self, *args: Any) -> dict[str, Any]:
        raise openai.APIConnectionError(request=httpx2.Request("POST", "https://foundry.test/openai/v1/responses"))


class Wrapped(ToolUser):
    async def summarise(self, *args: Any) -> dict[str, Any]:  # how the chat client passes an SDK error on
        raise ChatClientException("service failed to complete the prompt: 429")


class OurBug(ToolUser):
    async def summarise(self, *args: Any) -> dict[str, Any]:
        raise RuntimeError("a bug in our own code")


@asynccontextmanager
async def no_mcp(session_id: str, token: str, at: str, caller: str) -> AsyncIterator[None]:
    raise httpx.ConnectError("the MCP app did not answer")
    yield  # an async generator, as the real connect is


@pytest.mark.parametrize(
    ("agent", "connect", "logged"),
    [
        (Unreachable, fake_connect, r"fell back to code: model or API error after \d+\.\d s: APIConnectionError"),
        (Wrapped, fake_connect, r"fell back to code: model or API error after \d+\.\d s: ChatClientException"),
        (ToolUser, no_mcp, r"fell back to code: match tools error after \d+\.\d s: ToolsUnavailable ConnectError"),
        (OurBug, fake_connect, r"fell back to code: unexpected summary error after \d+\.\d s: RuntimeError a bug"),
    ],
)
async def test_a_model_failure_a_tool_failure_and_our_own_bug_are_logged_apart(
    agent: type[ToolUser], connect: Any, logged: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="watchparty"):
        s = await played(summary_agent=agent(), mcp_connect=connect)
    assert {c["source"] for c in summaries(s)} == {"fallback"}
    lines = fallback_lines(caplog)
    assert len(lines) == 10
    assert all(re.search(logged, line) for line in lines), lines


class ToolCaller(FunctionInvocationLayer[Any], BaseChatClient[Any]):
    """Stands in for the model inside the real Agent Framework function loop: it calls get_key_moments over MCP,
    then answers without citing evidence, so every summary falls back."""

    async def _inner_get_response(self, *, messages: Any, stream: bool, options: Any, **kwargs: Any) -> Any:
        if not any(c.type == "function_result" for m in messages for c in m.contents):
            call = Content.from_function_call(call_id="c1", name="get_key_moments", arguments='{"manager": "mgr_1"}')
            return ChatResponse(messages=[Message(role="assistant", contents=[call])])
        uncited = SummaryAnswer(text="You lead the league.", text_en="", evidence_ids=[], rulebook_ids=[])
        return ChatResponse(messages=[Message(role="assistant", contents=[uncited.model_dump_json()])])


async def test_a_failing_match_tool_is_logged_though_the_model_saw_only_an_error_string(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(*_: Any) -> Any:
        raise RuntimeError("key moments broke")

    monkeypatch.setattr(match_tools, "key_moments", broken)  # served only over MCP, so the match itself still plays
    runner = partial(chat_runner, ToolCaller(), "summary", SUMMARY_INSTRUCTIONS, SummaryAnswer, 500)
    app = create_app(session_kwargs={"sleep": no_sleep, "summary_agent": SummaryAgent(runner, model="m")})
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            made = (await http.post("/api/sessions", json={"seed": "demo", "manager": "mgr_1"})).json()
        with caplog.at_level(logging.WARNING, logger="watchparty"):
            await app.state.store.get(made["session_id"]).run()
    lines = fallback_lines(caplog)
    assert len(lines) == 10
    assert all("fell back to code: match tools error (get_key_moments: ToolExecutionException" in line
               and "), then failed the checks twice: cite at least one evidence id" in line
               for line in lines), lines  # fmt: skip


def test_model_text_in_a_reason_stays_on_one_capped_line(caplog: pytest.LogCaptureFixture) -> None:
    forged = "ok\nINFO watchparty: all good " + "x" * 300
    failed = FailedChecks([forged])
    for line in (str(failed), why_summary_fell_back(failed, 1.0), why_summary_fell_back(RuntimeError(forged), 1.0)):
        assert "\n" not in line
        assert len(line) < 300
    with caplog.at_level(logging.WARNING, logger="watchparty.app.workflow"):
        fell_back("manager mgr_1", ValueError(forged), trace.get_current_span())
    assert [r.getMessage().count("\n") for r in caplog.records] == [0]  # a bid's error is one line too
    assert failed.args == ([forged],)  # the problems are the args, so rebuilding from them is the same error
    assert copy.copy(failed).problems == [forged]


def test_a_made_up_rulebook_id_is_told_what_would_pass() -> None:
    # The cause measured locally (#84): with no entries given, the model cited scoring rule names and evidence
    # ids, and the retry, told only "not goal", swapped in another one.
    cited = {"text": "Top of the league on 231 IP.", "text_en": "", "evidence_ids": CITED, "rulebook_ids": ["goal"]}
    assert ("rulebook_ids must be entries get_rule_explanation gave you (empty, since it gave you no entries), not"
            " goal") in summary_problems(cited, PRIYA, SERVED, NAMES, {})  # fmt: skip
    mine = {"rule-save-points": {"title": "Save", "reason": "Stopping a big chance matters more."}}
    assert ("rulebook_ids must be entries get_rule_explanation gave you (one of rule-save-points), not goal"
            in summary_problems(cited, PRIYA, SERVED, NAMES, mine))  # fmt: skip


@pytest.mark.parametrize(
    ("lang", "text", "text_en", "shown", "shown_en"),
    [
        ("en", "Top of the league \u2014 231 IP at the break.", "", "Top of the league, 231 IP at the break.", ""),
        ("es", "Priya sigue arriba \u2014 231 IP al descanso.", "Priya tops the league \u2013 231 IP.",
         "Priya sigue arriba, 231 IP al descanso.", "Priya tops the league, 231 IP."),
    ],
)  # fmt: skip
async def test_dashes_in_a_summary_are_rewritten_in_code_without_a_retry(
    lang: str, text: str, text_en: str, shown: str, shown_en: str
) -> None:
    model = Scripted(answer(text=text, text_en=text_en))
    out = await SummaryAgent(model, model="m").summarise(PRIYA | {"lang": lang}, "half_time", None, SERVED, NAMES)
    assert (out["text"], out["text_en"], out["status"]) == (shown, shown_en, "ok")
    assert len(model.sent) == 1


def test_every_prompt_asks_for_commas_instead_of_dashes() -> None:
    from watchparty.agents.host import INSTRUCTIONS as HOST
    from watchparty.agents.manager import INSTRUCTIONS as MANAGER

    for prompt in (HOST, MANAGER, SUMMARY_INSTRUCTIONS):
        assert "Never write an em dash or an en dash: use a comma instead." in prompt


def test_the_request_says_what_rulebook_ids_may_hold() -> None:
    model = Scripted(answer())
    asyncio.run(SummaryAgent(model, model="m").summarise(PRIYA, "full_time", None, SERVED, NAMES))
    assert "never evidence ids or scoring rule names; it is [] if you did not call it" in model.sent[0][0].text
