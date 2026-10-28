"""HTTP entry point: sessions, the per-screen WebSocket stream, health, security headers, the front end."""

import asyncio
import logging
import os
import re
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from functools import cache, partial
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Header, HTTPException, Request, Response, WebSocket
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError
from starlette.routing import Route

from watchparty import telemetry
from watchparty.app.session import AGENTS, Session, SessionStore, simulated
from watchparty.app.shared import Shared, shared_from_env
from watchparty.matchtools import server as match_tools
from watchparty.matchtools.view import MatchView
from watchparty.points import load_league
from watchparty.sim import DEMO_GAMEWEEK_SEED, DEMO_SEED
from watchparty.sim.clubs import CLUBS, team

CSP = ("default-src 'self'; connect-src 'self' {ws}; img-src 'self' data:; object-src 'none'; base-uri 'none'; "
       "form-action 'self'; frame-ancestors 'none'")  # fmt: skip
# Older Safari does not count ws: and wss: as 'self', so the page's own host is named; only a plain host
# name and port are ever written into the header.
HOST = re.compile(r"[A-Za-z0-9.-]+(:[0-9]{1,5})?")
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    # The phone token travels in URL fragments and the WebSocket query; never send a URL on in a Referer.
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
SCREEN = re.compile(r"group|phone:mgr_[1-5]")
LEAGUE_ID = re.compile(r"lg_(demo|[0-9a-f]{12})")  # as the store makes them, and the app's router reads them (#179)
MAX_CONNECTIONS_PER_SESSION = 18  # two judge tabs (a group screen and five cues-only phones each) and five phones
BUSY = "Every seat is taken right now. Please try again in a minute."
NO_LEAGUES = "Leagues are not available on this server."
LEAGUE_MATCH = "A league plays its own gameweek: send no seed and no gameweek with league_id."
NO_GAMEWEEK = "This league has no gameweek to play."
NOT_YOUR_SEAT = "manager must be your own seat in this league: {manager}"
COOLDOWN = "You left a match of this gameweek a moment ago. Try again in a minute."
log = logging.getLogger(__name__)
# A send or close waits only when the socket's write buffer is past uvicorn's 64 KiB high-water mark, so the
# deadline measures how fast a screen drains, not how big a message is (the largest is about 2 KB, a late
# joiner's replay about 46 KB). A screen that cannot drain about 48 KB in 10 s cannot keep up with the match (#69).
SEND_DEADLINE_S = 10.0


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seed: Literal["demo"] | Annotated[int, Field(ge=0, le=1_000_000)] = "demo"
    manager: Annotated[str, Field(pattern=r"^mgr_[1-5]$")]
    style: Literal["analyst", "casual"] = "casual"
    lang: Literal["en", "es"] = "en"  # the languages the checks know (checks.LANGUAGES, #16)
    gameweek: bool = False  # three fixtures at once (#94); one match against every limit either way
    # A stored league's open gameweek (#178), with the member's token in Authorization: Bearer
    league_id: Annotated[str, Field(pattern=r"^lg_[a-z0-9_]{1,37}$")] | None = None


class StyleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    style: Literal["analyst", "casual"]


class ChaosRequest(BaseModel):
    """Whose agent to knock out (#90); without one, every agent on the next moment."""

    model_config = ConfigDict(extra="forbid")
    manager: Annotated[str, Field(pattern=r"^mgr_[1-5]$")] | None = None


def client_address(request: Request) -> str:
    """Who a request is from, for the per-client seat limit.

    Behind the Container Apps ingress (TRUST_PROXY=1) the TCP peer is the proxy; it appends the real address
    as the last X-Forwarded-For entry. Earlier entries come from the caller and are never trusted.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if os.environ.get("TRUST_PROXY") == "1" and forwarded:
        return forwarded.rsplit(",", 1)[-1].strip()
    return request.client.host if request.client else "unknown"


def content_security_policy(host: str) -> str:
    return CSP.format(ws=f"ws://{host} wss://{host}" if HOST.fullmatch(host) else "").replace(" ;", ";")


def secured(response: Response, request: Request) -> Response:
    response.headers.update(SECURITY_HEADERS)
    response.headers["Content-Security-Policy"] = content_security_policy(request.headers.get("host", ""))
    return response


def _roster(teams: list[dict[str, Any]], league: dict[str, Any]) -> dict[str, Any]:
    clubs = {t["id"]: {k: t[k] for k in ("name", "short_name", "colour")} for t in teams}
    players = {p["id"]: {"name": p["name"], "club": t["name"], "colour": t["colour"], "position": p["position"],
                         "shirt": p["shirt"]} for t in teams for p in t["players"]}  # fmt: skip
    return {"league": league, "players": players, "clubs": clubs}


@cache
def lobby() -> dict[str, Any]:
    """What the lobby shows before any session exists: the managers, their squads, the players and the clubs. The
    top level is the single match's, as before; `gameweek` holds the gameweek's league and all six clubs (#155)."""
    meta = simulated(DEMO_SEED, 30).meta  # every single match has these clubs
    single = _roster([meta["home"], meta["away"]], load_league())
    # A gameweek's ids are the club's short name and the squad number, as rekey() makes them (sim/gameweek.py)
    teams = [team("home", club) for club in CLUBS]
    for t in teams:
        t["players"] = [{**p, "id": f"p_{t['short_name'].lower()}_{p['id'].rsplit('_', 1)[1]}"} for p in t["players"]]
    return single | {"gameweek": _roster(teams, load_league("league_gw_v1.json"))}


def same_token(given: str, token: str) -> bool:
    """Timing-safe, and a plain no for any text: compare_digest raises on non-ASCII str (#50)."""
    return secrets.compare_digest(given.encode(), token.encode())


MAX_BODY = 4096  # every real request body is under 100 bytes


class BodyLimit:
    """Refuses a request body over MAX_BODY with 413, whether declared or streamed, before it is all read: a few
    100 MB bodies took the one 1 GiB replica, and every live match, down (#50)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length", b"0")
        if not declared.isdigit() or int(declared) > MAX_BODY:
            await self.refuse(send)
            return
        # Read it here, at most MAX_BODY bytes, and hand the app the whole of it: a limit raised while the app
        # reads would come back as FastAPI's 400 for a body it could not parse.
        body, more = b"", True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                return  # the client went away
            body += message.get("body", b"")
            more = message.get("more_body", False)
            if len(body) > MAX_BODY:
                await self.refuse(send)
                return
        whole = [{"type": "http.request", "body": body, "more_body": False}]

        async def replay() -> Any:
            return whole.pop() if whole else await receive()

        await self.app(scope, replay, send)

    @staticmethod
    async def refuse(send: Any) -> None:
        await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"Request body too large"})


class _TokenMask(logging.Filter):
    """Masks token=... in log lines: uvicorn logs each WebSocket's query string, phone token included (#50)."""

    TOKEN = re.compile(r"token=[^&\s\"']+")

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.TOKEN.sub("token=***", str(record.msg))
        if isinstance(record.args, tuple):
            record.args = tuple(self.TOKEN.sub("token=***", a) if isinstance(a, str) else a for a in record.args)
        return True


def mask_tokens() -> logging.Filter:
    return _TokenMask()


TOKEN_MASK = mask_tokens()


def create_app(
    static_dir: Path | None = None,
    session_kwargs: dict[str, Any] | None = None,
    max_sessions: int = 20,
    per_client: int = 4,
    mcp_public: bool | None = None,
    send_deadline_s: float = SEND_DEADLINE_S,
    database_url: str | None = None,
    shared: Shared | None = None,
    league_per_minute: int = 10,
) -> FastAPI:
    """mcp_public mounts the match tools at /mcp for debugging with an MCP client (docs/mcp.md); off unless
    MCP_PUBLIC=1, so visitors cannot call paid rulebook lookups (#34).

    database_url (DATABASE_URL) turns on leagues in Postgres under /api/leagues (#96). Without it the store
    package is never imported and the app is exactly the fixed-league product.

    shared: live state in Redis, so several servers share the limits and any of them serves any screen (#95).
    None keeps everything on this server."""
    store = SessionStore(max_sessions=max_sessions, per_client=per_client, session_kwargs=dict(session_kwargs or {}))
    if shared is not None:
        shared.attach(store, MAX_CONNECTIONS_PER_SESSION)

    def resolve(session_id: str, token: str | None, at: str | None) -> MatchView | None:
        """The match tools may read a session only with its phone token."""
        session = store.get(session_id)
        if session is None or token is None or not same_token(token, session.phone_token):
            return None
        return session.view(at)

    mcp, mcp_app = match_tools.build(resolve, rulebook_from_env())
    league_store = leagues_from_env(database_url) if database_url else None
    league_starts = league_limit(league_per_minute) if league_store is not None else None
    leagues_down: list[str] = []  # why the league store did not start, for /ready (#180)
    league_routes: list[Any] = []  # what including the league router added, to take out again

    async def start_leagues() -> bool:
        """Migrations, then the demo league. A database that refuses (sign-in, permissions, network) must not take
        the matches down with it: the server starts without the league routes, as with no DATABASE_URL."""
        nonlocal league_store
        try:
            await league_store.start()  # type: ignore[union-attr]
            return True
        except Exception as exc:
            logging.getLogger(__name__).exception("league store did not start; serving without leagues")
            leagues_down.append(type(exc).__name__)
        with suppress(Exception):
            await league_store.close()  # type: ignore[union-attr]
        league_store = None
        del app.state.leagues
        app.router.routes[:] = [r for r in app.router.routes if r not in league_routes]
        return False

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:  # started in this order, closed in reverse
            if league_store is not None and await start_leagues():
                stack.push_async_callback(league_store.close)
            await stack.enter_async_context(mcp.session_manager.run())
            if shared is not None:
                await shared.start()
                stack.push_async_callback(shared.close)
            yield

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(BodyLimit)  # first, so the security headers below wrap its 413 too
    for name in ("uvicorn.error", "uvicorn.access"):  # the WebSocket line carries ?token= (#50)
        logging.getLogger(name).addFilter(TOKEN_MASK)
    app.state.store = store
    store.session_kwargs["mcp_connect"] = partial(match_tools.connect, mcp_app)  # summaries, in-process

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        return secured(await call_next(request), request)

    @app.exception_handler(Exception)
    async def server_error(request: Request, _: Exception) -> Response:
        """The 500 for an unhandled exception. Starlette sends it from outside every middleware, so the headers
        above would miss it; it then re-raises, so the traceback is still logged and the trace still records it."""
        return secured(PlainTextResponse("Internal Server Error", status_code=500), request)

    @app.get("/api/lobby")
    async def get_lobby() -> dict[str, Any]:
        return lobby()

    @app.get("/health")
    async def health() -> dict[str, Any]:
        """Up, whether the agents are configured (without them every match runs on code fallbacks, #24), and whether
        a finished match queues recap videos, so a live check knows whether to wait for one (#204)."""
        agents = any(store.session_kwargs.get(k) for k in AGENTS)
        return {"status": "ok", "agents": agents, "recaps": store.session_kwargs.get("on_full_time") is not None}

    @app.get("/ready", response_model=None)
    async def ready() -> dict[str, str] | JSONResponse:
        """Whether to send this server new traffic: the readiness probe's path (#95). With shared state, not while
        Redis is out of reach, or this server would run apart from the rest. Liveness stays on /health, which never
        asks Redis: an outage must not restart every replica and end every match with it."""
        if shared is not None and not await shared.ready():
            return JSONResponse({"status": "unavailable", "redis": "unreachable"}, status_code=503)
        if leagues_down:  # still ready: matches run without leagues (#180)
            return {"status": "ok", "leagues": f"unavailable: {leagues_down[0]}"}
        return {"status": "ok"}

    async def league_match(body: SessionRequest, request: Request, authorization: str
                           ) -> tuple[Any, str, str, dict[str, Any]]:  # fmt: skip
        """The matchday, the viewer's style and language, and the league for a session a member of a stored league
        starts (#178): its open gameweek, claimed for this member, with the results recorded at full time."""
        if league_store is None or league_starts is None:
            raise HTTPException(status_code=400, detail=NO_LEAGUES)
        from sqlalchemy.exc import SQLAlchemyError

        from watchparty.store.api import PLAYING, SLOW_DOWN, bearer_token
        from watchparty.store.service import Closed, Cooldown, NoGameweek, NotFound, NotYourSeat, Playing

        if not league_starts.allow(client_address(request)):  # before Postgres is touched
            raise HTTPException(status_code=429, detail=SLOW_DOWN)
        league_id, token = str(body.league_id), bearer_token(authorization)
        try:
            member = await league_store.authenticate(league_id, token) if token else None
            if member is None:
                raise HTTPException(status_code=404)  # as the league routes: no token, a wrong one, no such league
            if body.gameweek or body.seed != "demo":
                raise HTTPException(status_code=422, detail=LEAGUE_MATCH)
            day = await league_store.matchday(league_id, member, body.manager)
        except (SQLAlchemyError, OSError) as e:  # the database is out of reach: busy, as a full server is
            log.warning("league session refused, database unavailable: %s", type(e).__name__)
            raise HTTPException(status_code=503, detail=BUSY) from e
        except NotYourSeat as e:
            raise HTTPException(status_code=422, detail=NOT_YOUR_SEAT.format(manager=f"mgr_{e.seat}")) from e
        except NotFound as e:
            raise HTTPException(status_code=404) from e
        except NoGameweek as e:
            raise HTTPException(status_code=409, detail=NO_GAMEWEEK) from e
        except Playing as e:
            raise HTTPException(status_code=409, detail=PLAYING) from e
        except Cooldown as e:
            raise HTTPException(status_code=429, detail=COOLDOWN) from e
        viewer = day.league["managers"][day.seat - 1]

        async def record(session: Session) -> None:
            played = {int(m["id"].removeprefix("mgr_")): (list(m["squad"]), m["captain"])
                      for m in session.league["managers"]}  # fmt: skip
            try:
                await league_store.record_played(day, played)
            except (NotFound, Closed):
                log.info("league %s gameweek %s was recorded without match %s", day.league_id, day.number,
                         session.id)  # fmt: skip

        async def kickoff(_: Session) -> bool:
            return bool(await league_store.kickoff(day))

        async def release(_: Session) -> None:  # abandoned, never opened or failed: the member waits a minute
            await league_store.release(day, cooldown=True)

        hooks = {"on_played": record, "on_kickoff": kickoff, "on_released": release}
        return day, viewer["style"], viewer["lang"], {"league": day.league, **hooks}

    async def let_go(day: Any) -> None:
        """Releases a claim no session came of; a failure here is logged, and the claim runs out on its own."""
        if league_store is None:
            return
        try:
            await league_store.release(day)
        except Exception:
            log.exception("could not release league %s gameweek %s", day.league_id, day.number)

    @app.post("/api/sessions", status_code=201)
    async def create_session(body: SessionRequest, request: Request, authorization: str = Header("")
                             ) -> dict[str, str]:  # fmt: skip
        demo = DEMO_GAMEWEEK_SEED if body.gameweek else DEMO_SEED
        seed = demo if body.seed == "demo" else body.seed
        style: str = body.style
        lang: str = body.lang
        league: dict[str, Any] = {}
        day = None
        if body.league_id is not None:
            day, style, lang, league = await league_match(body, request, authorization)
            seed = day.seed
        client = client_address(request)
        session = None
        try:
            if shared is None:
                session = store.create(seed, body.manager, style, lang, client, body.gameweek, **league)
            else:
                try:
                    session = await shared.create(seed, body.manager, style, lang, client, body.gameweek, **league)
                except (RedisError, OSError):
                    session = None  # the limits cannot be counted across servers: busy, never this server's alone
        finally:
            if session is None and day is not None:  # refused, failed or cancelled: no match came of the claim
                await asyncio.shield(let_go(day))
        if session is None:
            raise HTTPException(status_code=503, detail=BUSY)  # never a login or paywall (rule 4.8)
        return {"session_id": session.id, "phone_token": session.phone_token, "manager": body.manager}

    def judges_session(session_id: str, authorization: str) -> Session:
        """The session, for a holder of its phone token only; anyone else sees 404."""
        session = store.get(session_id)
        token = authorization.removeprefix("Bearer ")
        if session is None or not same_token(token, session.phone_token):
            raise HTTPException(status_code=404)
        return session

    async def forwarded(session_id: str, authorization: str, op: dict[str, Any]) -> dict[str, Any] | None:
        """A session another server owns: the request goes to its owner, and this is the owner's answer. None for
        a session here."""
        if shared is None or store.get(session_id) is not None:
            return None
        try:
            answer = await shared.forward(session_id, authorization.removeprefix("Bearer "), op, same_token)
        except (RedisError, OSError):
            raise HTTPException(status_code=503, detail=BUSY) from None
        if answer is None:
            raise HTTPException(status_code=404)
        return answer

    @app.post("/api/sessions/{session_id}/chaos", status_code=202)
    async def simulate_failure(
        session_id: str, body: ChaosRequest | None = None, authorization: str = Header("")
    ) -> dict[str, str | int]:
        """The judge's "simulate failure": every agent on the next moment, or one manager's on its next bid (#90).
        The arm id comes back on the decision of the moment that spends this press. A session another server owns
        is armed there, and its arm id comes back from it (#95)."""
        manager = body.manager if body else None
        answer = await forwarded(session_id, authorization, {"op": "chaos", "manager": manager})
        if answer is not None and "error" in answer:
            raise HTTPException(status_code=422, detail=answer["error"])
        if answer is not None:
            arm = int(answer["arm"])
        else:
            session = judges_session(session_id, authorization)
            try:
                arm = session.simulate_failure(manager)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        if manager:
            return {"status": f"{manager}'s agent will fall back the next time it bids", "arm": arm}
        return {"status": "the next moment with agents in it will fall back", "arm": arm}

    @app.post("/api/sessions/{session_id}/style", status_code=204)
    async def set_style(session_id: str, body: StyleRequest, authorization: str = Header("")) -> None:
        """The judge's style toggle."""
        if await forwarded(session_id, authorization, {"op": "style", "style": body.style}) is None:
            judges_session(session_id, authorization).set_style(body.style)

    @app.websocket("/ws/{session_id}/{screen}")
    async def stream(websocket: WebSocket, session_id: str, screen: str, token: str | None = None,
                     cues: str | None = None) -> None:  # fmt: skip
        session = store.get(session_id)
        if session is None and shared is not None and SCREEN.fullmatch(screen):  # owned by another server (#95)
            serve = partial(relay, websocket, send_deadline_s=send_deadline_s)
            await shared.serve(websocket, session_id, screen, token, cues == "only", serve, same_token)
            return
        allowed = (
            session is not None
            and SCREEN.fullmatch(screen) is not None
            and session.connections() < MAX_CONNECTIONS_PER_SESSION
            and (screen == "group" or (token is not None and same_token(token, session.phone_token)))
        )
        if not allowed or session is None:
            await websocket.close(code=1008)  # policy violation: unknown session, screen or token
            return
        # takes the seat before the first await, so handshakes cannot race
        queue = session.subscribe(screen, cues_only=cues == "only")
        try:
            await websocket.accept()
            if shared is None:
                store.start(session)  # the hourly limits count it from here (#39)
            else:
                await shared.start_match(session)
            await relay(websocket, queue, send_deadline_s)
        finally:
            session.unsubscribe(screen, queue)

    if league_store is not None:
        from watchparty.store.api import build_router

        app.state.leagues = league_store
        before = len(app.router.routes)
        app.include_router(build_router(league_store, client_address))
        league_routes.extend(app.router.routes[before:])

    if mcp_public if mcp_public is not None else os.environ.get("MCP_PUBLIC") == "1":
        app.mount("/mcp", mcp_app)
    else:  # a bare 404 for every method, not the static mount's 405, so a live check can prove #34 (#55)
        missing = Response(status_code=404)
        app.router.routes.append(Route("/mcp", missing))  # the mount alone misses the bare path
        app.mount("/mcp", missing)

    if static_dir is not None:
        if not static_dir.is_dir():
            raise RuntimeError(f"static_dir does not exist: {static_dir}")
        index = static_dir / "index.html"

        @app.get("/watch/{_:path}", include_in_schema=False)
        @app.get("/phone/{_:path}", include_in_schema=False)
        async def screen(_: str) -> FileResponse:
            return FileResponse(index)  # the app routes these itself

        @app.get("/judges", include_in_schema=False)  # this exact path only; /judges/x stays a 404 (#92)
        async def judges() -> FileResponse:
            return FileResponse(index)

        # The league screens (#179): start or join, the share link, and one league's page; nothing else under it
        @app.get("/league", include_in_schema=False)
        @app.get("/league/join", include_in_schema=False)
        async def league_home() -> FileResponse:
            return FileResponse(index)

        @app.get("/league/{league_id}", include_in_schema=False)
        async def league_page(league_id: str) -> FileResponse:
            if not LEAGUE_ID.fullmatch(league_id):
                raise HTTPException(status_code=404)
            return FileResponse(index)

        # Mounted last so API routes take precedence.
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")

    return app


def agents_from_env() -> dict[str, Any]:
    """The Foundry-backed agents when FOUNDRY_ENDPOINT is set; otherwise sessions run on code fallbacks."""
    endpoint = os.environ.get("FOUNDRY_ENDPOINT")
    if not endpoint:
        return {}
    from watchparty.agents import host, manager, summary
    from watchparty.agents.foundry import foundry_client

    client = foundry_client(endpoint)
    manager_model = os.environ.get("MANAGER_MODEL", "gpt-5.4-mini")
    host_model = os.environ.get("HOST_MODEL", "gpt-5.4-mini")
    return {
        "manager_agent": manager.ManagerAgent(manager.foundry_runner(client, manager_model), manager_model),
        "host_agent": host.HostAgent(host.foundry_runner(client, host_model), host_model),
        # the manager agent's model writes the summaries too, calling the match tools itself
        "summary_agent": summary.SummaryAgent(summary.foundry_runner(client, manager_model), manager_model),
    }


async def relay(
    websocket: WebSocket,
    queue: asyncio.Queue[dict[str, Any] | None],
    send_deadline_s: float = SEND_DEADLINE_S,
    close_code: Callable[[], int] | None = None,
) -> None:
    """Sends the screen its messages until either side ends it, and returns as soon as one does:
    - the screen disconnects (a send loop alone would wait for ever once the match goes quiet, #33)
    - the session is dropped (None in the queue): the socket is closed with 1000, and a half-open client that
      never answers the close does not hold the session (#44), nor one that stopped reading, whose close waits
      on the same full buffer as a send (#69)
    - a send fails, or takes longer than send_deadline_s: the screen is gone or has stopped reading, and its
      queue would otherwise grow and hold the session for as long as the client keeps the socket open (#69)
    - the screen sends anything: screens only listen, so it is closed with 1008 rather than read; reading and
      discarding a flood took the one replica to full CPU (#69)

    close_code: the code for that last close, when it is not always 1000 (a screen on another server, #95).
    """

    async def send() -> None:
        while (message := await queue.get()) is not None:
            await asyncio.wait_for(websocket.send_json(message), send_deadline_s)
        code = 1000 if close_code is None else close_code()
        await asyncio.wait_for(websocket.close(code=code), send_deadline_s)  # the session is over and gone

    async def receive() -> None:
        if (await websocket.receive())["type"] != "websocket.disconnect":
            await asyncio.wait_for(websocket.close(code=1008), send_deadline_s)  # policy violation

    tasks = [asyncio.create_task(send()), asyncio.create_task(receive())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
            task.add_done_callback(lambda t: t.cancelled() or t.exception())  # a send to a gone screen


def league_limit(per_minute: int) -> Any:
    """League session starts per client a minute (#178): each one reads and writes Postgres."""
    from watchparty.store.api import Limit

    return Limit(per_minute)


def leagues_from_env(database_url: str) -> Any:
    """The league store (#96), authenticating as DATABASE_AUTH says: the URL's password, or a Microsoft Entra
    token from the app's managed identity (entra)."""
    from watchparty.store import open_store, token_provider_from_env

    return open_store(database_url, token_provider_from_env())


def rulebook_from_env() -> Any:
    """The league rulebook in Foundry IQ (#18) when FOUNDRY_IQ_ENDPOINT is set; otherwise no rule tool."""
    endpoint = os.environ.get("FOUNDRY_IQ_ENDPOINT")
    if not endpoint:
        return None
    from watchparty.matchtools.rulebook import FoundryIQ, search_token

    return FoundryIQ(endpoint, search_token())


def recaps_from_env() -> dict[str, Any]:
    """Recap videos at full time (#15) when RECAP_STORAGE_ACCOUNT is set; otherwise sessions end without one."""
    account = os.environ.get("RECAP_STORAGE_ACCOUNT")
    if not account:
        return {}
    from watchparty.recap.dispatch import publish_recaps
    from watchparty.recap.storage import AzureRecaps

    return {"on_full_time": partial(publish_recaps, AzureRecaps.for_account(account))}


# Our own lines (for example how long recaps took) reach the container log; library chatter stays out.
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("watchparty").setLevel(logging.INFO)
# Before the app and the agents exist, so their spans go to the provider configured here (#111).
telemetry.configure("watchparty", agents=True)
app = create_app(
    Path(os.environ["STATIC_DIR"]) if os.environ.get("STATIC_DIR") else None,
    agents_from_env() | recaps_from_env(),
    database_url=os.environ.get("DATABASE_URL") or None,
    shared=shared_from_env(),
)
telemetry.instrument(app)
