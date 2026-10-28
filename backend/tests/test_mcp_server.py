import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from watchparty.app.main import create_app
from watchparty.app.session import Session

pytestmark = pytest.mark.anyio

MANAGERS = [f"mgr_{n}" for n in range(1, 6)]
WINDOWS: list[dict[str, int]] = [{}, {"from_minute": 0, "to_minute": 45}, {"from_minute": 45}, {"from_minute": 30,
                                 "to_minute": 60}, {"from_minute": 0, "to_minute": 130}]  # fmt: skip
INITIALIZE = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18",
              "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}  # fmt: skip
ACCEPT = {"Accept": "application/json, text/event-stream"}


async def no_sleep(_: float) -> None:
    await asyncio.sleep(0)


@asynccontextmanager
async def demo(played: bool = True) -> AsyncIterator[tuple[Any, Session]]:
    """The app with its lifespan running, and one demo session, played to full time with code fallbacks."""
    app = create_app(session_kwargs={"sleep": no_sleep}, mcp_public=True)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            made = (await http.post("/api/sessions", json={"seed": "demo", "manager": "mgr_1"})).json()
        session = app.state.store.get(made["session_id"])
        if played:
            await session.run()
        yield app, session


@asynccontextmanager
async def mcp_client(app: Any, session: Session, at: str | None = None) -> AsyncIterator[ClientSession]:
    query = f"?at={at}" if at else ""
    headers = {"Authorization": f"Bearer {session.phone_token}"}
    async with (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), headers=headers) as http,
        streamable_http_client(f"http://test/mcp/{session.id}/{query}", http_client=http) as (read, write, _),
        ClientSession(read, write) as client,
    ):
        await client.initialize()
        yield client


async def test_every_tool_and_parameter_on_every_view_returns_valid_output(
    contract_errors: Callable[[str, Any], list[str]],
) -> None:
    players = [f"p_{side}_{n}" for side in ("home", "away") for n in range(1, 12)]
    calls: list[tuple[str, dict[str, Any]]] = [
             ("get_standings", {}), ("get_scoring_rules", {}), *(("get_match_stats", w) for w in WINDOWS),
             *(("get_squad_points", {"manager": m}) for m in MANAGERS),
             *(("get_key_moments", {"manager": m}) for m in MANAGERS),
             *(("get_player_summary", {"player": p}) for p in players)]  # fmt: skip
    async with demo() as (app, session):
        for at in (None, "half_time", "full_time"):
            async with mcp_client(app, session, at) as client:
                listed = {t.name: t for t in (await client.list_tools()).tools}
                assert set(listed) == {name for name, _ in calls}
                for name, args in calls:
                    result = await client.call_tool(name, args)
                    assert not result.isError, (at, name, args, result.content)
                    tool = listed[name]
                    assert tool.annotations is not None
                    assert tool.annotations.readOnlyHint
                    assert tool.outputSchema is not None
                    output = result.structuredContent
                    assert not list(Draft202012Validator(tool.outputSchema).iter_errors(output)), (name, args)
                    assert output is not None
                    for item in output["evidence"]:
                        assert contract_errors("common.schema.json#/$defs/evidence", item) == [], (name, item)
                        assert not (isinstance(item["value"], float) and item["value"].is_integer()), item


async def test_the_half_time_view_is_frozen_at_the_whistle() -> None:
    async with demo() as (app, session):
        async with mcp_client(app, session, "half_time") as client:
            half = (await client.call_tool("get_standings", {})).structuredContent
        async with mcp_client(app, session, "full_time") as client:
            full = (await client.call_tool("get_standings", {})).structuredContent
    assert half is not None
    assert full is not None
    assert half["as_of_minute"] == 46
    assert full["as_of_minute"] > 90
    assert half["evidence"] != full["evidence"]


@pytest.mark.parametrize("args", [{"manager": "mgr_9"}, {"manager": "Priya"}])
async def test_bad_arguments_are_refused_by_the_input_schema(args: dict[str, Any]) -> None:
    async with demo() as (app, session), mcp_client(app, session) as client:
        result = await client.call_tool("get_squad_points", args)
    assert result.isError
    assert "mgr_" in str(result.content)


async def test_an_unknown_extra_argument_is_ignored() -> None:
    # The MCP SDK's argument model ignores fields it does not know, as JSON Schema does by default (#32).
    async with demo() as (app, session), mcp_client(app, session) as client:
        result = await client.call_tool("get_squad_points", {"manager": "mgr_1", "extra": 1})
    assert not result.isError
    assert result.structuredContent is not None
    assert result.structuredContent["evidence"]


async def test_an_impossible_window_is_an_error_not_a_crash() -> None:
    async with demo() as (app, session), mcp_client(app, session) as client:
        result = await client.call_tool("get_match_stats", {"from_minute": 60, "to_minute": 30})
    assert result.isError
    assert "from_minute" in str(result.content)


async def test_only_a_session_with_its_phone_token_and_a_view_that_exists_is_served() -> None:
    async with demo(played=False) as (app, session):
        good = {"Authorization": f"Bearer {session.phone_token}"}
        cases = [
            (f"/mcp/{session.id}/", good, 200),
            (f"/mcp/{session.id}/", {}, 404),
            (f"/mcp/{session.id}/", {"Authorization": "Bearer wrong"}, 404),
            (f"/mcp/{session.id}/", {"Authorization": f"Basic {session.phone_token}"}, 404),
            ("/mcp/nope/", good, 404),
            (f"/mcp/{session.id}/extra/", good, 404),
            (f"/mcp/{session.id}/?at=half_time", good, 404),  # not yet half time
            (f"/mcp/{session.id}/?at=tomorrow", good, 404),
        ]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            for path, headers, status in cases:
                response = await http.post(path, json=INITIALIZE, headers=headers | ACCEPT)
                assert response.status_code == status, (path, headers)
                assert response.headers["X-Content-Type-Options"] == "nosniff"


async def test_the_rulebook_tool_exists_only_when_foundry_iq_is_configured() -> None:
    from watchparty.matchtools import server as match_tools

    class Rulebook:
        async def explain(self, question: str) -> list[dict[str, str]]:
            return [{"id": "rule-goal", "title": "Goal scored", "content": "A goal is worth 5 IP."}]

    async with demo() as (app, session), mcp_client(app, session, "half_time") as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert "get_rule_explanation" not in names
    server, _ = match_tools.build(lambda *_: None, Rulebook())
    assert "get_rule_explanation" in {t.name for t in await server.list_tools()}


async def test_the_mcp_endpoint_is_public_only_when_asked_for() -> None:
    app = create_app(session_kwargs={"sleep": no_sleep})
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        made = (await http.post("/api/sessions", json={"seed": "demo", "manager": "mgr_1"})).json()
        headers = {"Authorization": f"Bearer {made['phone_token']}"} | ACCEPT
        response = await http.post(f"/mcp/{made['session_id']}/", json=INITIALIZE, headers=headers)
    assert response.status_code == 404  # the summaries call the tools in-process; visitors cannot


@pytest.mark.parametrize("public", [False, True])
async def test_the_packaged_app_answers_a_disabled_mcp_with_a_bare_404(tmp_path: Path, public: bool) -> None:
    (tmp_path / "index.html").write_text("app")  # the frontend is mounted at / in the image (#55)
    app = create_app(static_dir=tmp_path, session_kwargs={"sleep": no_sleep}, mcp_public=public)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        made = (await http.post("/api/sessions", json={"seed": "demo", "manager": "mgr_1"})).json()
        headers = {"Authorization": f"Bearer {made['phone_token']}"} | ACCEPT
        response = await http.post(f"/mcp/{made['session_id']}/", json=INITIALIZE, headers=headers)
        if public:
            assert response.status_code == 200
            return
        assert response.status_code == 404
        for method in ("GET", "POST", "DELETE"):
            for path in ("/mcp", "/mcp/", "/mcp/x/"):
                got = await http.request(method, path)
                assert (got.status_code, got.text) == (404, ""), (method, path)
        assert (await http.get("/health")).status_code == 200
