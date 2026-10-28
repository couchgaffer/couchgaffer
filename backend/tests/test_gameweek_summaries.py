"""A gameweek's match tools and summaries (#152, slice 6 of #94): the tools read every fixture, and each
manager's half-time and full-time summary covers the gameweek.

Numbered cases are #94's revised test plan (21, 22); the rest are this ticket's plan.
"""

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator

from tests.test_gameweek_session import failing, gameweek, run
from tests.test_mcp_server import mcp_client, no_sleep
from tests.test_summary import fallback_lines, summaries
from watchparty.agents.base import written
from watchparty.agents.summary import SummaryAgent, fallback_summary, summary_problems
from watchparty.app.main import create_app
from watchparty.app.session import Session
from watchparty.matchtools import tools
from watchparty.matchtools.view import Given, MatchView
from watchparty.points.engine import RULES

pytestmark = pytest.mark.anyio
MANAGERS = [f"mgr_{n}" for n in range(1, 6)]


@pytest.fixture(scope="module")
def played() -> Session:
    """One gameweek played to full time, summaries by code: once for the module, on a loop of its own."""
    s = gameweek()
    asyncio.run(run(s))
    return s


def fixture_of(view: MatchView, player: str) -> str:
    return str(view.players[player]["fixture_id"])


# --- the tools across fixtures --------------------------------------------------------------------------


async def test_squad_points_read_each_players_own_fixture(played: Session) -> None:
    view = played.views["full_time"]
    spread = set()
    for m in played.league["managers"]:
        out = {e.id: e.value for e in tools.squad_points(view, m["id"]).evidence}
        for p in m["squad"]:
            mult = RULES["captain_multiplier"] if p == m["captain"] else 1
            own = played.engines[fixture_of(view, p)].player_points[p]
            assert out[f"ev_squad_{m['id']}_{p}_total"] == pytest.approx(sum(own.values()) * mult)
        spread |= {(m["id"], fixture_of(view, p)) for p in m["squad"]}
    assert len({f for _, f in spread}) == 3  # squads reach all three fixtures
    total = next(e for e in tools.squad_points(view, "mgr_1").evidence if e.id == "ev_squad_mgr_1_total")
    assert total.label.endswith("IP gained this gameweek")


async def test_standings_are_the_one_gameweek_table(played: Session) -> None:
    view = played.views["full_time"]
    out = {e.id: e.value for e in tools.standings(view).evidence}
    for row in played.engine.table():
        assert out[f"ev_table_{row['manager']}_points"] == row["points"]
        assert out[f"ev_table_{row['manager']}_rank"] == row["rank"]


async def test_match_stats_for_one_fixture_count_only_its_events(played: Session) -> None:
    view = played.views["full_time"]
    meta = played.fixtures["fx_2"]
    out = tools.match_stats(view, fixture_id="fx_2")
    assert [c.id for c in out.clubs] == [meta["home"]["id"], meta["away"]["id"]]
    assert all(e.id.startswith(f"ev_stats_fx_2_0_{out.to_minute}_") for e in out.evidence)
    for side in ("home", "away"):
        shots = sum(e["type"] == "shot" and e["team"] == side for e in played.match.events
                    if e["fixture_id"] == "fx_2" and e["t"] < out.to_minute * 60_000)  # fmt: skip
        item = next(e for e in out.evidence if e.id == f"ev_stats_fx_2_0_{out.to_minute}_{side}_shots")
        assert item.value == shots
        assert item.label == f"Shots, {meta[side]['name']}"


async def test_match_stats_without_a_fixture_cover_every_fixture(played: Session) -> None:
    out = tools.match_stats(played.views["full_time"])
    ids = [e.id for e in out.evidence]
    assert len(ids) == len(set(ids)) == 30
    assert {i.split("_")[2] + "_" + i.split("_")[3] for i in ids} == {"fx_1", "fx_2", "fx_3"}
    assert len({c.id for c in out.clubs}) == 6


async def test_an_unknown_fixture_or_a_fixture_on_a_single_match_is_an_error(played: Session) -> None:
    view = played.views["full_time"]
    with pytest.raises(ValueError, match="fx_1, fx_2, fx_3"):
        tools.match_stats(view, fixture_id="fx_9")
    with pytest.raises(ValueError, match="fx_1, fx_2, fx_3"):
        tools.key_moments(view, "mgr_1", "fx_9")
    single = Session(seed=267, manager="mgr_1", style="casual", lang="en", sleep=no_sleep).live_view()
    with pytest.raises(ValueError, match="leave fixture_id out"):
        tools.match_stats(single, fixture_id="fx_1")


async def test_key_moments_by_fixture_each_name_their_fixture(played: Session) -> None:
    view = played.views["full_time"]
    seen = set()
    for m in MANAGERS:
        every = tools.key_moments(view, m).moments
        by_fixture = [k for f in ("fx_1", "fx_2", "fx_3") for k in tools.key_moments(view, m, f).moments]
        assert sorted(k.moment_id for k in by_fixture) == sorted(k.moment_id for k in every)
        for f in ("fx_1", "fx_2", "fx_3"):
            assert {k.fixture_id for k in tools.key_moments(view, m, f).moments} <= {f}
        seen |= {k.fixture_id for k in every}
    assert seen == {"fx_1", "fx_2", "fx_3"}
    single = Session(seed=267, manager="mgr_1", style="casual", lang="en", sleep=no_sleep)
    await single.run()
    moments = tools.key_moments(single.views["full_time"], "mgr_2").model_dump()["moments"]
    assert moments
    assert all("fixture_id" not in k for k in moments)  # a single match's output as before


async def test_a_player_of_an_away_club_is_read_from_the_meta(played: Session) -> None:
    view = played.views["full_time"]
    meta = played.fixtures["fx_3"]
    player = meta["away"]["players"][-1]["id"]
    assert "_home_" not in player
    assert "_away_" not in player
    out = tools.player_summary(view, player)
    assert out.club.id == meta["away"]["id"]
    goals = sum(e["type"] == "shot" and e.get("outcome") == "goal" and e.get("player") == player
                for e in played.match.events)  # fmt: skip
    assert {e.id: e.value for e in out.evidence}[f"ev_player_{player}_goals"] == goals


async def test_frozen_gameweek_view_keeps_its_totals_and_one_standings(played: Session) -> None:
    # #94 case 22
    half = played.views["half_time"]
    [cue] = [c for c in played.log if c.get("kind") == "table" and c["payload"]["headline"] == "Half time"]
    assert half.engine.table() == cue["payload"]["table"]  # unchanged by the second half
    assert half.engine.table() != played.engine.table()
    standings = {id(e.standings) for e in half.engines.values()}
    assert standings == {id(half.engine.standings)}
    assert id(played.engine.standings) not in standings
    s = half.engine.standings
    for m in MANAGERS:
        assert s.points[m] == pytest.approx(s.start[m] + sum(s.by_fixture[f][m] for f in ("fx_1", "fx_2", "fx_3")))


async def test_a_fixture_that_stopped_early_counts_only_what_it_played() -> None:
    s = gameweek()
    failing(s, "fx_2", 30 * 60_000)
    await run(s)
    view = s.views["full_time"]
    played = [e for e in s.match.events if e["fixture_id"] == "fx_2" and e["t"] <= s.engines["fx_2"].t]
    assert len(played) < sum(e["fixture_id"] == "fx_2" for e in s.match.events)
    out = {e.id: e.value for e in tools.match_stats(view, fixture_id="fx_2", to_minute=130).evidence}
    to = tools.match_stats(view, fixture_id="fx_2").to_minute
    for side in ("home", "away"):
        shots = sum(e["type"] == "shot" and e["team"] == side for e in played)
        assert out[f"ev_stats_fx_2_0_{to}_{side}_shots"] == shots
    player = s.fixtures["fx_2"]["home"]["players"][9]["id"]
    passes = sum(e["type"] == "pass" and e.get("player") == player for e in played)
    assert {e.id: e.value for e in tools.player_summary(view, player).evidence}[f"ev_player_{player}_passes"] == passes


async def test_the_live_view_runs_on_the_gameweek_clock_not_one_fixtures() -> None:
    s = gameweek()
    shots = [e for e in s.match.events if e["fixture_id"] == "fx_2" and e["type"] == "shot"][:2]
    for e in s.match.events:  # only fx_2 plays, up to its second shot
        if e["fixture_id"] == "fx_2" and e["t"] <= shots[1]["t"]:
            s.engines["fx_2"].observe(e)
    view = s.view()
    assert view is not None
    assert view.t == shots[1]["t"] > s.engines["fx_1"].t
    out = tools.match_stats(view, fixture_id="fx_2", to_minute=130)
    counted = sum(e.value for e in out.evidence if e.id.endswith("_shots"))
    assert counted == sum(e["t"] < out.to_minute * 60_000 for e in shots)


@pytest.mark.parametrize("style", ["analyst", "casual"])
@pytest.mark.parametrize("lang", ["en", "es"])
@pytest.mark.parametrize("phase", ["half_time", "full_time"])
async def test_every_plain_gameweek_summary_speaks_of_the_gameweek(
    played: Session, style: str, lang: str, phase: str
) -> None:
    manager = played.managers["mgr_2"] | {"style": style, "lang": lang}
    out = fallback_summary(played.views[phase], manager, phase)
    for text in filter(None, (out["text"], out["text_en"])):
        assert not re.search(r"match|partido", text), text
    if style == "analyst":
        assert ("this gameweek" if lang == "en" else "en esta jornada") in out["text"]
    if (lang, phase) == ("es", "full_time"):
        assert out["text"].startswith("Final de la jornada")
    served = played.views[phase].served_to("mgr_2")
    assert summary_problems(out, manager, served, played.allowed_names, gameweek=True) == []


@pytest.mark.parametrize(
    "text", ["You're 2nd on 241 IP after this match.", "Big moment in fx_3 for you.", "Vas 2.º con 241 IP en este"
             " partido.", "You're 2nd on 241 IP, this game was kind.", "Vas 2.º con 241 IP, fuerte en el partido."]
)  # fmt: skip
def test_a_gameweek_summary_that_speaks_of_one_match_or_a_fixture_id_fails(text: str) -> None:
    manager = {"id": "mgr_2", "display_name": "Priya", "style": "casual", "lang": "en"}
    served = {
        "ev_table_mgr_2_points": {"id": "ev_table_mgr_2_points", "label": "Priya: league points", "value": 241},
        "ev_table_mgr_2_rank": {"id": "ev_table_mgr_2_rank", "label": "Priya: league rank", "value": 2},
    }
    answer = {"text": text, "text_en": "", "evidence_ids": list(served)}
    assert any("gameweek" in p for p in summary_problems(answer, manager, served, ["Priya"], gameweek=True))
    assert not any("gameweek" in p for p in summary_problems(answer, manager, served, ["Priya"]))  # single match


@pytest.mark.parametrize(
    "text", ["The match between Hollinsford United and Saltmere Rovers is level for you on 241 IP.",
             "El partido entre Hollinsford United y Saltmere Rovers sigue igual, vas con 241 IP.",
             "Ganaste en el partido del Hollinsford United, vas con 241 IP.", "You're ahead in the game on 241 IP.",
             "The game plan worked, 241 IP."]
)  # fmt: skip
def test_a_gameweek_summary_that_names_the_clubs_or_says_the_game_passes(text: str) -> None:
    manager = {"id": "mgr_2", "display_name": "Priya", "style": "casual", "lang": "en"}
    served = {"ev_table_mgr_2_points": {"id": "ev_table_mgr_2_points", "label": "Priya: league points", "value": 241}}
    answer = {"text": text, "text_en": "", "evidence_ids": list(served)}
    names = ["Priya", "Hollinsford United", "Saltmere Rovers"]
    assert not [p for p in summary_problems(answer, manager, served, names, gameweek=True) if "gameweek" in p]


async def test_a_player_summary_says_gameweek_in_a_gameweek_only(played: Session) -> None:
    player = played.fixtures["fx_1"]["home"]["players"][8]["id"]
    out = {e.id: e for e in tools.player_summary(played.views["full_time"], player).evidence}
    assert out[f"ev_player_{player}_total"].label.endswith("IP this gameweek")
    single = Session(seed=267, manager="mgr_1", style="casual", lang="en", sleep=no_sleep).live_view()
    mine = {e.id: e for e in tools.player_summary(single, "p_home_9").evidence}
    assert mine["ev_player_p_home_9_total"].label.endswith("IP this match")


def test_only_a_gameweek_request_mentions_fixtures() -> None:
    agent = SummaryAgent(lambda _: None, "m")  # type: ignore[arg-type, return-value]
    manager = {"id": "mgr_1", "display_name": "You", "style": "casual", "lang": "en"}
    single = agent.request(manager, "half_time", ["You"])
    assert single == agent.request(manager, "half_time", ["You"], gameweek=False)
    assert "fixture" not in single
    assert "This is a gameweek" in agent.request(manager, "half_time", ["You"], gameweek=True)


# --- over MCP ---------------------------------------------------------------------------------------------


@asynccontextmanager
async def gameweek_app(**kwargs: Any) -> AsyncIterator[tuple[Any, Session]]:
    app = create_app(session_kwargs={"sleep": no_sleep} | kwargs, mcp_public=True)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            made = await http.post("/api/sessions", json={"seed": "demo", "manager": "mgr_1", "gameweek": True})
        s = app.state.store.get(made.json()["session_id"])
        assert s.gameweek is not None
        yield app, s


async def test_the_tools_take_club_player_ids_and_fixture_ids_over_mcp() -> None:
    async with gameweek_app() as (app, s):
        await s.run()
        player = s.fixtures["fx_1"]["away"]["players"][8]["id"]
        async with mcp_client(app, s, "full_time") as client:
            listed = {t.name: t for t in (await client.list_tools()).tools}
            for name, args in (("get_player_summary", {"player": player}),
                               ("get_match_stats", {"fixture_id": "fx_2"}),
                               ("get_key_moments", {"manager": "mgr_2", "fixture_id": "fx_3"})):  # fmt: skip
                result = await client.call_tool(name, args)
                assert not result.isError, (name, result.content)
                schema = listed[name].outputSchema
                assert schema is not None
                assert not list(Draft202012Validator(schema).iter_errors(result.structuredContent))
            summary = (await client.call_tool("get_player_summary", {"player": player})).structuredContent
            assert summary is not None
            assert summary["club"]["id"] == s.fixtures["fx_1"]["away"]["id"]
            for name, args in (("get_player_summary", {"player": "P_HOL_9"}),
                               ("get_player_summary", {"player": "p_fx1_home_9"}),
                               ("get_match_stats", {"fixture_id": "fx_x"}),
                               ("get_match_stats", {"fixture_id": "fx_9"})):  # fmt: skip
                assert (await client.call_tool(name, args)).isError, (name, args)


class GameweekToolUser:
    """A fake summary agent that calls the real match tools over MCP: the table, its own squad and its key
    moments from every fixture, then quotes its points. `lie` changes the number; `borrow` cites mgr_2's squad."""

    def __init__(self, lie: bool = False, borrow: bool = False) -> None:
        self.lie, self.borrow = lie, borrow
        self.calls: list[bool] = []
        self.fixtures: list[set[str]] = []  # per call

    async def summarise(self, manager: dict[str, Any], phase: str, tools: Any, served: dict[str, dict[str, Any]],
                        names: list[str], rulebook: Given | None = None,
                        gameweek: bool = False) -> dict[str, Any]:  # fmt: skip
        self.calls.append(gameweek)
        mid = manager["id"]
        [table] = await tools.call_tool("get_standings")
        points = next(e for e in json.loads(table.text)["evidence"] if e["id"] == f"ev_table_{mid}_points")
        owner = "mgr_2" if self.borrow else mid
        [squad] = await tools.call_tool("get_squad_points", manager=owner)
        total = next(e for e in json.loads(squad.text)["evidence"] if e["id"] == f"ev_squad_{owner}_total")
        [moments] = await tools.call_tool("get_key_moments", manager=mid)
        self.fixtures.append({k["fixture_id"] for k in json.loads(moments.text)["moments"]})
        cited = [total] if self.borrow else [points, total]
        value = cited[0]["value"] + (1 if self.lie else 0)
        english = f"You are on {value:g} IP."
        ids = [e["id"] for e in cited]
        if manager["lang"] == "en":
            return {"text": english, "text_en": "", "evidence_ids": ids, "status": "ok"}
        return {"text": f"Estás en {written(value, 'es')} IP.", "text_en": english, "evidence_ids": ids, "status": "ok"}


async def test_each_manager_gets_one_gameweek_summary_a_whistle_from_the_tools() -> None:
    # #94 case 21
    agent = GameweekToolUser()
    async with gameweek_app(summary_agent=agent) as (_, s):
        await s.run()
    assert agent.calls == [True] * 10
    assert len(set().union(*agent.fixtures)) >= 2  # the agents were given moments from more than one fixture
    got = summaries(s)
    assert sorted((c["screen"], c["payload"]["headline"]) for c in got) == sorted(
        (f"phone:{m}", h) for m in MANAGERS for h in ("Half time", "Full time")
    )
    assert {c["source"] for c in got} == {"manager"}
    for c in got:
        mid = c["payload"]["manager"]
        phase = "half_time" if c["payload"]["headline"] == "Half time" else "full_time"
        served = s.views[phase].served_to(mid)
        assert [e["id"] for e in c["payload"]["evidence"]] == [f"ev_table_{mid}_points", f"ev_squad_{mid}_total"]
        assert all(served[e["id"]] == e for e in c["payload"]["evidence"])


async def test_a_gameweek_summary_may_cite_only_its_own_squad() -> None:
    async with gameweek_app(summary_agent=GameweekToolUser(borrow=True)) as (_, s):
        await s.run()
    sources = {(c["screen"], c["source"]) for c in summaries(s)}
    assert sources == {("phone:mgr_2", "manager")} | {(f"phone:mgr_{n}", "fallback") for n in (1, 3, 4, 5)}


async def test_a_gameweek_summary_with_a_changed_number_falls_back(caplog: pytest.LogCaptureFixture) -> None:
    async with gameweek_app(summary_agent=GameweekToolUser(lie=True)) as (_, s):
        with caplog.at_level(logging.WARNING, logger="watchparty"):
            await s.run()
    assert {c["source"] for c in summaries(s)} == {"fallback"}
    lines = fallback_lines(caplog)
    assert len(lines) == 10
    assert all("is not a value" in line for line in lines), lines
