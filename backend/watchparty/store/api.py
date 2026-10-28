"""HTTP routes for leagues, mounted only when DATABASE_URL is set (#96).

A member token goes in `Authorization: Bearer <token>` and is good for its own league only; anyone else sees 404,
as the session routes do. The demo league is read without one.
"""

import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from watchparty.store.service import (
    Closed,
    Full,
    Invalid,
    KickedOff,
    LeagueStore,
    NotCreator,
    NotFound,
    Profile,
)

FULL = "Every seat in this league is taken."
CLOSED = "This gameweek has been played."
PLAYING = "A friend is playing this gameweek right now. Try again once it is over."
KICKED_OFF = (
    "A match of this gameweek has started, so squads are fixed until the gameweek is recorded, even if "
    "that match was left early."
)
NOT_CREATOR = "Only the friend who created the league records a gameweek."
SLOW_DOWN = "Too many tries from here. Please wait a minute."
GAMEWEEK = Annotated[int, Field(ge=1, le=1000)]
# Printable text only: a control character (a NUL in particular) is refused here, not by Postgres as a 500.
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, pattern=r"^[^\x00-\x1f\x7f]+$")]
PER_MINUTE = 10  # creates, and joins, per client: a group of five needs one create and four joins


class MemberIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: Annotated[Name, StringConstraints(max_length=20)]
    lang: Literal["en", "es"] = "en"
    style: Literal["analyst", "casual"] = "casual"
    favourite_club: Annotated[str, Field(pattern=r"^t_[a-z0-9_]{1,36}$")] | None = None
    favourite_player: Annotated[str, Field(pattern=r"^p_[a-z0-9_]{1,36}$")] | None = None

    def profile(self) -> Profile:
        return Profile(self.display_name, self.lang, self.style, self.favourite_club, self.favourite_player)


class CreateLeague(MemberIn):
    name: Annotated[Name, StringConstraints(max_length=40)]


class JoinLeague(MemberIn):
    code: Annotated[str, Field(pattern=r"^[A-Za-z0-9]{6}$")]


class SquadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    players: Annotated[list[Annotated[str, Field(max_length=40)]], Field(max_length=11)]
    captain: Annotated[str, Field(max_length=40)]


class Limit:
    """At most `per_minute` calls per client in any 60 s. Join codes cannot be guessed at speed, and leagues
    (each one simulates a match) cannot be created in bulk."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute, self.clock = per_minute, clock
        self.calls: defaultdict[str, deque[float]] = defaultdict(deque)

    def allow(self, client: str) -> bool:
        now = self.clock()
        for key in [k for k, q in self.calls.items() if q and q[-1] <= now - 60]:
            del self.calls[key]  # nobody keeps an entry for a client that went quiet
        calls = self.calls[client]
        while calls and calls[0] <= now - 60:
            calls.popleft()
        if len(calls) >= self.per_minute:
            return False
        calls.append(now)
        return True


def bearer_token(authorization: str) -> str | None:
    """The token from `Bearer <token>`, the scheme in any case; None for any other header."""
    scheme, _, token = authorization.partition(" ")
    token = token.strip()
    return token if scheme.lower() == "bearer" and token else None


def build_router(store: LeagueStore, client_of: Callable[[Request], str], per_minute: int = PER_MINUTE) -> APIRouter:
    router = APIRouter(prefix="/api/leagues")
    creates, joins = Limit(per_minute), Limit(per_minute)

    async def member_of(league_id: str, authorization: str) -> str:
        token = bearer_token(authorization)
        member = await store.authenticate(league_id, token) if token else None
        if member is None:
            raise HTTPException(status_code=404)
        return member

    async def reader_of(league_id: str, authorization: str) -> str | None:
        """A member, or nobody on the demo league, whatever token was sent."""
        token = bearer_token(authorization)
        member = await store.authenticate(league_id, token) if token else None
        if member is None and not await store.is_demo(league_id):
            raise HTTPException(status_code=404)
        return member

    def limited(limit: Limit, request: Request) -> None:
        if not limit.allow(client_of(request)):
            raise HTTPException(status_code=429, detail=SLOW_DOWN)

    @router.post("", status_code=201)
    async def create_league(body: CreateLeague, request: Request) -> dict[str, Any]:
        limited(creates, request)
        try:
            return await store.create_league(body.name, body.profile())
        except Invalid as e:
            raise HTTPException(status_code=422, detail=e.problems) from e

    @router.post("/join", status_code=201)
    async def join_league(body: JoinLeague, request: Request) -> dict[str, Any]:
        limited(joins, request)
        try:
            return await store.join(body.code, body.profile())
        except NotFound as e:
            raise HTTPException(status_code=404, detail="No league has that code.") from e
        except Full as e:
            raise HTTPException(status_code=409, detail=FULL) from e
        except Invalid as e:
            raise HTTPException(status_code=422, detail=e.problems) from e

    @router.get("/{league_id}")
    async def get_league(league_id: str, authorization: str = Header("")) -> dict[str, Any]:
        viewer = await reader_of(league_id, authorization)
        try:
            return await store.league(league_id, viewer)
        except NotFound as e:
            raise HTTPException(status_code=404) from e

    @router.put("/{league_id}/gameweeks/{number}/squad")
    async def pick_squad(league_id: str, number: GAMEWEEK, body: SquadIn,
                         authorization: str = Header("")) -> dict[str, Any]:  # fmt: skip
        member = await member_of(league_id, authorization)
        try:
            return await store.pick_squad(league_id, member, number, body.players, body.captain)
        except NotFound as e:
            raise HTTPException(status_code=404) from e
        except Closed as e:
            raise HTTPException(status_code=409, detail=CLOSED) from e
        except KickedOff as e:
            raise HTTPException(status_code=409, detail=KICKED_OFF) from e
        except Invalid as e:
            raise HTTPException(status_code=422, detail=e.problems) from e

    @router.post("/{league_id}/gameweeks/{number}/results", status_code=201)
    async def record_results(league_id: str, number: GAMEWEEK, authorization: str = Header("")) -> dict[str, Any]:
        member = await member_of(league_id, authorization)
        try:
            return await store.record_results(league_id, number, member)
        except NotFound as e:
            raise HTTPException(status_code=404) from e
        except NotCreator as e:
            raise HTTPException(status_code=403, detail=NOT_CREATOR) from e
        except Closed as e:
            raise HTTPException(status_code=409, detail=CLOSED) from e

    @router.get("/{league_id}/season")
    async def season(league_id: str, authorization: str = Header("")) -> dict[str, Any]:
        await reader_of(league_id, authorization)
        try:
            return await store.season(league_id)
        except NotFound as e:
            raise HTTPException(status_code=404) from e

    return router
