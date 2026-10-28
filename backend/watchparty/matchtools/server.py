"""The match tools over MCP (#12): streamable HTTP, mounted in the app at /mcp/{session_id}/.

Stateless, with JSON responses: each request stands alone, so the server keeps no MCP sessions of its own.
A request names a watch-party session in its path and carries that session's phone token as a bearer
token; `?at=half_time` or `?at=full_time` reads the view frozen at that whistle instead of the live match.
Anything else gets a bare 404. Every tool is read-only and reads only that session's synthetic match.
`caller=mgr_n` names whose agent is asking, so rulebook entries are recorded per manager (#37).

The summaries call it in-process (`connect`); the app mounts it publicly only for debugging (#34).
"""

import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from watchparty.matchtools import tools
from watchparty.matchtools.rulebook import Rulebook
from watchparty.matchtools.view import MatchView

# session_id, bearer token (or None), `at` (or None) -> the view to serve, or None to refuse
Resolve = Callable[[str, str | None, str | None], MatchView | None]

Manager = Annotated[str, Field(pattern=r"^mgr_[1-5]$", description="A manager id, mgr_1 to mgr_5")]
# A single match's ids name the side (p_home_9), a gameweek's the club (p_hol_9, #152)
Player = Annotated[str, Field(pattern=r"^p_[a-z]{3,4}_[0-9]{1,2}$", description="A player id from get_squad_points,"
                              " such as p_home_9, or in a gameweek p_hol_9")]  # fmt: skip
Fixture = Annotated[str, Field(pattern=r"^fx_[1-9]$", description="In a gameweek, one fixture: fx_1 to fx_3. Leave it"
                               " out for every fixture, and always on a single match")]  # fmt: skip
Minute = Annotated[int, Field(ge=0, le=130, description="Minutes elapsed; 0 to 45 is the first half")]
Question = Annotated[str, Field(min_length=3, max_length=200, description="A question about the league's rules")]

CALLER = re.compile(r"mgr_[1-5]")
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
INSTRUCTIONS = """Read-only tools over one invented match and its fantasy-style mini-league. Every number is an
evidence item with an id; cite the ids of the numbers you use."""


def _state(ctx: Context[Any, Any, Request]) -> dict[str, Any]:
    request = ctx.request_context.request
    if request is None:  # only reachable over a transport other than HTTP, which is never mounted
        raise RuntimeError("the match tools are served over HTTP only")
    state: dict[str, Any] = request.scope["state"]
    return state


def _view(ctx: Context[Any, Any, Request]) -> MatchView:
    view: MatchView = _state(ctx)["view"]
    return view


def build(resolve: Resolve, rulebook: Rulebook | None = None) -> tuple[FastMCP[Any], ASGIApp]:
    """The MCP server (its session manager must run in the app's lifespan) and the ASGI app to mount.

    With a rulebook (Foundry IQ, #18), it also explains the league's rules from the knowledge base."""
    # DNS-rebinding protection guards servers that trust any local caller; this one needs a bearer token,
    # and the app sends no CORS headers, so a browser page cannot attach one to a cross-site request.
    server = FastMCP("watchparty-match", instructions=INSTRUCTIONS, stateless_http=True, json_response=True,
                     transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))  # fmt: skip

    @server.tool(annotations=READ_ONLY)
    def get_standings(ctx: Context[Any, Any, Request]) -> tools.Standings:
        """The mini-league table: each manager's league points and rank."""
        return tools.standings(_view(ctx))

    @server.tool(annotations=READ_ONLY)
    def get_squad_points(manager: Manager, ctx: Context[Any, Any, Request]) -> tools.SquadPoints:
        """Impact points (IP) per player and per rule this match for one manager's squad; the captain counts double."""
        return tools.squad_points(_view(ctx), manager)

    @server.tool(annotations=READ_ONLY)
    def get_match_stats(
        ctx: Context[Any, Any, Request], from_minute: Minute = 0, to_minute: Minute | None = None,
        fixture_id: Fixture | None = None,
    ) -> tools.MatchStats:  # fmt: skip
        """Shots, xG, possession, pass completion and control for both clubs, from from_minute to to_minute
        (default: the match so far). In a gameweek, for one fixture or every fixture."""
        return tools.match_stats(_view(ctx), from_minute, to_minute, fixture_id)

    @server.tool(annotations=READ_ONLY)
    def get_key_moments(
        manager: Manager, ctx: Context[Any, Any, Request], fixture_id: Fixture | None = None
    ) -> tools.KeyMoments:
        """The key moments that mattered to one manager so far, with their IP swing and rank in each. In a gameweek,
        from one fixture or every fixture."""
        return tools.key_moments(_view(ctx), manager, fixture_id)

    @server.tool(annotations=READ_ONLY)
    def get_player_summary(player: Player, ctx: Context[Any, Any, Request]) -> tools.PlayerSummary:
        """One player's events, metrics and IP so far."""
        return tools.player_summary(_view(ctx), player)

    @server.tool(annotations=READ_ONLY)
    def get_scoring_rules(ctx: Context[Any, Any, Request]) -> tools.ScoringRules:
        """The impact points rules, v1."""
        return tools.scoring_rules(_view(ctx))

    if rulebook is not None:

        @server.tool(annotations=READ_ONLY)
        async def get_rule_explanation(question: Question, ctx: Context[Any, Any, Request]) -> tools.RuleExplanation:
            """Why a rule exists and how it works, from the league rulebook (a Foundry IQ knowledge base). Use it to
            explain in words why a moment was worth its points; take numbers from the other tools' evidence."""
            return await tools.rule_explanation(_view(ctx), rulebook, question, _state(ctx)["caller"])

    server.streamable_http_app()  # creates the session manager

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return  # no websockets here, and the session manager's lifespan is the app's
        request = Request(scope)
        session_id = scope["path"].removeprefix(scope.get("root_path", "")).strip("/")
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        view = resolve(session_id, token if scheme.lower() == "bearer" else None, request.query_params.get("at"))
        if view is None:
            await Response(status_code=404)(scope, receive, send)
            return
        caller = request.query_params.get("caller", "")
        caller = caller if CALLER.fullmatch(caller) else ""
        scope.setdefault("state", {}).update(view=view.for_caller(caller), caller=caller)
        await server.session_manager.handle_request(scope, receive, send)

    return server, app


@asynccontextmanager
async def connect(app: ASGIApp, session_id: str, token: str, at: str, caller: str) -> AsyncIterator[Any]:
    """An Agent Framework MCP tool on one session's frozen view, for one manager's agent, called in-process
    with no network hop. `app` is the MCP app itself, so it works whether or not the app mounts it publicly."""
    from agent_framework import MCPStreamableHTTPTool

    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, headers={"Authorization": f"Bearer {token}"}) as http,
        MCPStreamableHTTPTool(
            name="match",
            url=f"http://watchparty/{session_id}/?at={at}&caller={caller}",
            http_client=http,
            load_prompts=False,
            approval_mode="never_require",
        ) as tool,  # fmt: skip
    ):
        yield tool
