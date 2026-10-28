"""Leagues in Postgres: create and join with a code, pick squads, record a gameweek, read the season."""

import asyncio
import functools
import hashlib
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from watchparty.points import load_league
from watchparty.sim import DEMO_SEED
from watchparty.store.db import migrate
from watchparty.store.rules import (
    SEATS,
    SQUAD_SIZE,
    Fixtures,
    Roster,
    default_squad,
    score,
    shown_names,
    squad_problems,
)
from watchparty.store.schema import Gameweek, League, Member, Result, Squad

DEMO_LEAGUE = "lg_demo"
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I to misread off a screen
CODE_LENGTH = 6
MAX_SEED = 1_000_000  # the range POST /api/sessions accepts
# How long a session's claim on a gameweek lasts (#178): a match takes about four minutes with its summaries. A
# session that is never opened, is abandoned or fails releases its claim; this is the backstop if its server dies.
PLAYING_S = 600
# How long a claim lasts before its match starts: just past the 30 s after which the store drops a session nobody
# opened. A member cannot keep a gameweek by starting sessions and never opening them.
UNOPENED_S = 45
# How long a member waits to start the gameweek again after their session was abandoned or never opened, so a loop
# of opening and leaving cannot keep the others out
COOLDOWN_S = 60


class NotFound(Exception):
    """No such league, gameweek or code, or a token that is not this league's: the caller sees 404 either way."""


class Full(Exception):
    """Every seat is taken."""


class Closed(Exception):
    """The gameweek has been played: no squad changes, no second result."""


class NotCreator(Exception):
    """Only the league's creator (seat 1) records a gameweek."""


class NoGameweek(Exception):
    """The league has no open gameweek to play."""


class Playing(Exception):
    """Another member's match of the gameweek is being played: nobody else starts it until it ends."""


class Cooldown(Exception):
    """This member's last session of the gameweek was abandoned or never opened a moment ago."""


class KickedOff(Exception):
    """A session has started the gameweek: its squads are fixed, so a session never shows a squad that changes."""


class NotYourSeat(Exception):
    def __init__(self, seat: int) -> None:
        super().__init__(f"mgr_{seat}")
        self.seat = seat


class Invalid(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Matchday:
    """A league's open gameweek as one member's session plays it: the seed, and the five seats as a session's
    league (league.schema.json), seat N as manager mgr_N."""

    league_id: str
    number: int
    seed: int
    seat: int  # the caller's
    league: dict[str, Any]
    members: dict[int, str]  # seat -> member id, as the session plays them; the other seats are bots
    claim: str  # "<member id>/<nonce>": this session's own claim, so an older or failed one never acts for it


@dataclass(frozen=True)
class Profile:
    display_name: str
    lang: str = "en"
    style: str = "casual"
    favourite_club: str | None = None
    favourite_player: str | None = None


def playing(gameweek: Gameweek, now: datetime) -> bool:
    return gameweek.playing_until is not None and gameweek.playing_until > now


def claimant(gameweek: Gameweek) -> str | None:
    """The member holding the gameweek's claim (playing_by is "<member id>/<nonce>")."""
    return None if gameweek.playing_by is None else gameweek.playing_by.split("/", 1)[0]


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def join_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def new_seed() -> int:
    return secrets.randbelow(MAX_SEED + 1)


@functools.lru_cache(maxsize=64)
def cached_roster(fixtures: Fixtures, seed: int) -> Roster:
    """A seed's roster, simulated once (about 44 ms) and kept: a gameweek is read on every pick."""
    return Roster.of(fixtures(seed))


@functools.lru_cache(maxsize=64)
def cached_players(fixtures: Fixtures, seed: int) -> tuple[dict[str, Any], ...]:
    """Every player a squad may hold this gameweek, from the fixtures pick_squad checks against (#179)."""
    return tuple({"id": p["id"], "name": p["name"], "position": p["position"], "club_id": m.meta[side]["id"],
                  "club_name": m.meta[side]["name"]}
                 for m in fixtures(seed) for side in ("home", "away") for p in m.meta[side]["players"])  # fmt: skip


class LeagueStore:
    def __init__(self, engine: AsyncEngine, fixtures: Fixtures) -> None:
        self.engine, self.fixtures = engine, fixtures
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        # Bots fill empty seats with the demo league's managers, seat by seat.
        self.templates = load_league()["managers"]

    async def roster(self, seed: int) -> Roster:
        """Off the event loop: a seed not seen before runs the simulator."""
        return await asyncio.to_thread(cached_roster, self.fixtures, seed)

    # --- members ------------------------------------------------------------------------------------------

    def _profile_problems(self, profile: Profile, roster: Roster) -> list[str]:
        problems = []
        if profile.favourite_club is not None and profile.favourite_club not in roster.clubs:
            problems.append("favourite_club is not a club in this league")
        if profile.favourite_player is not None and profile.favourite_player not in roster.club_of:
            problems.append("favourite_player is not a player in this league")
        return problems

    def _member(self, league_id: str, seat: int, profile: Profile) -> tuple[Member, str]:
        token = secrets.token_urlsafe(32)
        member = Member(id=f"mb_{secrets.token_hex(8)}", league_id=league_id, seat=seat, is_bot=False,
                        token_hash=token_hash(token), **profile.__dict__)  # fmt: skip
        return member, token

    async def create_league(self, name: str, profile: Profile) -> dict[str, Any]:
        seed = new_seed()
        problems = self._profile_problems(profile, await self.roster(seed))
        if problems:
            raise Invalid(problems)
        for _ in range(5):  # a clash on a 32^6 code space is rare; try again rather than fail
            league = League(id=f"lg_{secrets.token_hex(6)}", name=name, join_code=join_code())
            member, token = self._member(league.id, 1, profile)
            try:
                async with self.sessions.begin() as db:
                    db.add(league)
                    await db.flush()
                    db.add_all([Gameweek(league_id=league.id, number=1, seed=seed), member])
            except IntegrityError:
                continue
            return {"league_id": league.id, "join_code": league.join_code, "member_id": member.id,
                    "member_token": token, "seat": 1}  # fmt: skip
        raise RuntimeError("could not find a free join code")

    async def join(self, code: str, profile: Profile) -> dict[str, Any]:
        async with self.sessions.begin() as db:
            # The lock serialises joins to one league, so two friends never take the same last seat.
            league = await db.scalar(select(League).where(League.join_code == code.strip().upper()).with_for_update())
            if league is None:
                raise NotFound
            taken = set((await db.scalars(select(Member.seat).where(Member.league_id == league.id))).all())
            free = [s for s in range(1, SEATS + 1) if s not in taken]
            if not free:
                raise Full
            gameweek = await self._current(db, league.id)
            problems = self._profile_problems(profile, await self.roster(gameweek.seed))
            if problems:
                raise Invalid(problems)
            member, token = self._member(league.id, free[0], profile)
            db.add(member)
        return {"league_id": league.id, "member_id": member.id, "member_token": token, "seat": member.seat}

    async def authenticate(self, league_id: str, token: str) -> str | None:
        """The member of this league holding the token, or None. Every member's hash is compared, in constant
        time, and only this league's members are candidates: another league's token never matches."""
        given = token_hash(token)
        async with self.sessions() as db:
            rows = (await db.execute(
                select(Member.id, Member.token_hash).where(Member.league_id == league_id)
            )).all()  # fmt: skip
        found = None
        for member_id, stored in rows:
            if stored is not None and secrets.compare_digest(given, stored):
                found = member_id
        return found

    async def is_demo(self, league_id: str) -> bool:
        async with self.sessions() as db:
            return bool(await db.scalar(select(League.is_demo).where(League.id == league_id)))

    # --- gameweeks ----------------------------------------------------------------------------------------

    @staticmethod
    async def _current(db: AsyncSession, league_id: str) -> Gameweek:
        gameweek = await db.scalar(
            select(Gameweek).where(Gameweek.league_id == league_id).order_by(Gameweek.number.desc()).limit(1)
        )
        if gameweek is None:
            raise NotFound
        return gameweek

    async def pick_squad(self, league_id: str, member_id: str, number: int, players: Sequence[str],
                         captain: str) -> dict[str, Any]:  # fmt: skip
        async with self.sessions.begin() as db:
            # A shared lock: picks go on side by side, but not while the gameweek's result is being written.
            gameweek = await db.scalar(
                select(Gameweek).where(Gameweek.league_id == league_id, Gameweek.number == number)
                .with_for_update(read=True)
            )  # fmt: skip
            if gameweek is None:
                raise NotFound
            if gameweek.status != "open":
                raise Closed
            if gameweek.kicked_off_at is not None:
                raise KickedOff
            problems = squad_problems(await self.roster(gameweek.seed), players, captain)
            if problems:
                raise Invalid(problems)
            row = {"member_id": member_id, "gameweek": number, "league_id": league_id, "players": list(players),
                   "captain": captain}  # fmt: skip
            await db.execute(
                insert(Squad).values(row).on_conflict_do_update(
                    index_elements=[Squad.member_id, Squad.gameweek],
                    set_={"players": row["players"], "captain": captain, "picked_at": func.now()},
                )
            )  # fmt: skip
        return {"gameweek": number, "players": list(players), "captain": captain}

    def _template(self, seat: int) -> dict[str, Any]:
        return dict(self.templates[seat - 1])

    async def record_results(self, league_id: str, number: int, member_id: str) -> dict[str, Any]:
        """Plays the gameweek's seed through the points engine with every member's squad and saves the points.
        The server scores it, so a member cannot send a result; a member with no squad gets one picked. Only
        the creator may close a gameweek, so nobody else can end it before the others have picked."""
        async with self.sessions.begin() as db:
            if await db.scalar(select(Member.seat).where(Member.id == member_id, Member.league_id == league_id)) != 1:
                raise NotCreator
            # The league row first, as join takes it: a friend joining while this gameweek is scored waits for
            # it to close, rather than joining an open gameweek and getting no result for it.
            await db.execute(select(League.id).where(League.id == league_id).with_for_update())
            gameweek = await db.scalar(
                select(Gameweek).where(Gameweek.league_id == league_id, Gameweek.number == number).with_for_update()
            )
            if gameweek is None:
                raise NotFound
            if gameweek.status != "open":
                raise Closed
            members = (await db.scalars(select(Member).where(Member.league_id == league_id)
                                        .order_by(Member.seat))).all()  # fmt: skip
            picked = {s.member_id: (list(s.players), s.captain)
                      for s in await db.scalars(select(Squad).where(Squad.league_id == league_id,
                                                                    Squad.gameweek == number))}  # fmt: skip
            fixtures = await asyncio.to_thread(self.fixtures, gameweek.seed)
            roster = Roster.of(fixtures)
            for m in members:
                if m.id in picked:
                    continue
                players, captain = default_squad(roster, self._template(m.seat)["squad"])
                picked[m.id] = (players, captain)
                db.add(Squad(member_id=m.id, gameweek=number, league_id=league_id, players=players, captain=captain))
            scores = await asyncio.to_thread(score, fixtures, picked, [m.id for m in members])
            db.add_all(Result(member_id=s.member, gameweek=number, league_id=league_id, points=s.points, rank=s.rank,
                              expected_goals=s.expected_goals, goals=s.goals) for s in scores)  # fmt: skip
            gameweek.status, gameweek.finished_at = "finished", datetime.now(UTC)
            db.add(Gameweek(league_id=league_id, number=number + 1, seed=new_seed()))
        # The seed is told only now: while the gameweek is open it would give away every scorer.
        return {"gameweek": number, "seed": gameweek.seed, "results": [s.__dict__ for s in scores]}

    # --- a session plays the gameweek (#178) ----------------------------------------------------------------

    async def matchday(self, league_id: str, member_id: str, manager: str) -> Matchday:
        """The open gameweek for a member's session, which it claims: every member's picked squad and captain, the
        seat's template squad for a member who has not picked and for a bot in an empty seat, and each seat's season
        total so far as its starting points. The claim lasts UNOPENED_S until kickoff() starts its match, and
        another member's session takes over a claim whose match has not started, so an unopened session blocks
        nobody; a started match keeps everyone else out until it ends. The same member may claim it again (a lobby
        refreshed), with a new claim that ends when the first one would: a member never extends their own claim.
        `manager` must be the member's seat, checked before anything is claimed. release() lets go of a claim whose
        session never starts, is abandoned or fails."""
        async with self.sessions.begin() as db:
            league = await db.get(League, league_id)
            if league is None:
                raise NotFound
            # The row lock waits for a pick in flight (it holds a shared lock), and the pick after it sees the claim
            gameweek = await db.scalar(
                select(Gameweek).where(Gameweek.league_id == league_id, Gameweek.status == "open")
                .order_by(Gameweek.number.desc()).limit(1).with_for_update()
            )  # fmt: skip
            if gameweek is None:
                raise NoGameweek
            members = {m.seat: m for m in await db.scalars(select(Member).where(Member.league_id == league_id))}
            seat = next((n for n, m in members.items() if m.id == member_id), None)
            if seat is None:
                raise NotFound
            if manager != f"mgr_{seat}":
                raise NotYourSeat(seat)
            now = datetime.now(UTC)
            live, mine = playing(gameweek, now), claimant(gameweek) == member_id
            if live and gameweek.playing_started:  # a match is on, the member's own too: nobody starts another
                raise Playing
            if gameweek.cooldown_by == member_id and gameweek.cooldown_until and gameweek.cooldown_until > now:
                raise Cooldown
            claim = f"{member_id}/{secrets.token_hex(6)}"
            until = gameweek.playing_until if live and mine else now + timedelta(seconds=UNOPENED_S)
            gameweek.playing_by, gameweek.playing_until, gameweek.playing_started = claim, until, False
            squads = {s.member_id: (list(s.players), s.captain)
                      for s in await db.scalars(select(Squad).where(Squad.league_id == league_id,
                                                                    Squad.gameweek == gameweek.number))}  # fmt: skip
            totals = dict((await db.execute(
                select(Result.member_id, func.sum(Result.points)).where(Result.league_id == league_id)
                .group_by(Result.member_id)
            )).all())  # fmt: skip
        roster = await self.roster(gameweek.seed)
        names = shown_names({s: m.display_name for s, m in members.items()} |
                            {s: self._template(s)["display_name"] for s in range(1, SEATS + 1) if s not in members},
                            seat)  # fmt: skip
        managers = []
        for n in range(1, SEATS + 1):
            m, template = members.get(n), self._template(n)
            picked = squads.get(m.id) if m is not None else None
            players, captain = picked or default_squad(roster, template["squad"])
            if m is None:  # a bot in an empty seat
                style, lang, club, points = template["style"], template["lang"], template.get("favourite_club"), 0.0
            else:
                style, lang, club, points = m.style, m.lang, m.favourite_club, float(totals.get(m.id) or 0.0)
            seat_row = {"id": f"mgr_{n}", "display_name": names[n], "is_bot": m is None, "style": style, "lang": lang,
                       "squad": players, "captain": captain, "points": round(points, 2)}  # fmt: skip
            if club in roster.clubs:
                seat_row["favourite_club"] = club
            managers.append(seat_row)
        played = {"league_id": league.id, "name": league.name, "scoring_rules_version": "v1", "managers": managers}
        playing_members = {n: m.id for n, m in members.items()}
        return Matchday(league.id, gameweek.number, gameweek.seed, seat, played, playing_members, claim)

    async def _held(self, db: AsyncSession, day: Matchday) -> Gameweek | None:
        """The gameweek, locked, while this matchday's claim still holds it; None once anyone else's does."""
        gameweek = await db.scalar(
            select(Gameweek).where(Gameweek.league_id == day.league_id, Gameweek.number == day.number)
            .with_for_update()
        )  # fmt: skip
        if gameweek is None or gameweek.status != "open" or gameweek.playing_by != day.claim:
            return None
        return gameweek

    async def kickoff(self, day: Matchday) -> bool:
        """The session's match has started: its squads are fixed from now until the gameweek is recorded, so every
        later session of it shows the same squads, and the claim holds for PLAYING_S. Refused (False) when the claim
        is no longer this session's, or when a member picked after the session read the squads: it would show and
        record squads that are not the picks. That session then plays without recording, and lets go."""
        squads = {
            day.members[n]: (list(r["squad"]), r["captain"])
            for n, r in enumerate(day.league["managers"], start=1)
            if n in day.members
        }  # seat n is mgr_n
        async with self.sessions.begin() as db:
            gameweek = await self._held(db, day)
            if gameweek is None:
                return False
            if gameweek.playing_started:  # this session's match already started: nothing to fix, nothing extended
                return True
            stored = {s.member_id: (list(s.players), s.captain)
                      for s in await db.scalars(select(Squad).where(Squad.league_id == day.league_id,
                                                                    Squad.gameweek == day.number))}  # fmt: skip
            if any(squads.get(member) != squad for member, squad in stored.items() if member in squads):
                gameweek.playing_by, gameweek.playing_until, gameweek.playing_started = None, None, False
                return False
            now = datetime.now(UTC)
            gameweek.kicked_off_at = gameweek.kicked_off_at or now
            gameweek.playing_until, gameweek.playing_started = now + timedelta(seconds=PLAYING_S), True
        return True

    async def release(self, day: Matchday, cooldown: bool = False) -> None:
        """Lets go of a claim whose session never started, was abandoned, failed or could not be created: another
        member may play the gameweek, and picks open again unless a match already kicked it off. A newer claim, by
        anyone, is left alone. With `cooldown` (the member's session was abandoned or never opened), that member
        cannot start the gameweek again for COOLDOWN_S."""
        async with self.sessions.begin() as db:
            gameweek = await self._held(db, day)
            if gameweek is None:
                return
            gameweek.playing_by, gameweek.playing_until, gameweek.playing_started = None, None, False
            if cooldown:
                member = day.members[day.seat]
                gameweek.cooldown_by, gameweek.cooldown_until = (
                    member,
                    datetime.now(UTC) + timedelta(seconds=COOLDOWN_S),
                )

    async def record_played(self, day: Matchday, played: dict[int, tuple[list[str], str]]) -> dict[str, Any]:
        """Records a gameweek the member's session played to full time, with the five squads by seat as it played
        them. Scored over all five, bots included, so each member's points are the ones the room watched; ranks are
        among the members the session played, as record_results gives them: a friend who joined during the match
        played as a bot and gets no result for it. The played squads become those members' stored squads (fixed
        since kickoff, so they are the picks). Closed when the gameweek was already recorded (its creator did it by
        hand), or another session claimed it since: another member's once this claim ran out, or a newer session of
        the same member."""
        league_id, number = day.league_id, day.number
        async with self.sessions.begin() as db:
            await db.execute(select(League.id).where(League.id == league_id).with_for_update())  # as join takes it
            gameweek = await db.scalar(
                select(Gameweek).where(Gameweek.league_id == league_id, Gameweek.number == number).with_for_update()
            )
            if gameweek is None:
                raise NotFound
            if gameweek.status != "open" or gameweek.playing_by != day.claim:
                raise Closed
            members = {m.seat: m for m in await db.scalars(select(Member).where(Member.league_id == league_id))
                       if day.members.get(m.seat) == m.id}  # fmt: skip
            seats = {n: members[n].id if n in members else f"seat_{n}" for n in range(1, SEATS + 1)}
            fixtures = await asyncio.to_thread(self.fixtures, gameweek.seed)
            squads = {seats[n]: played[n] for n in seats}
            scores = await asyncio.to_thread(score, fixtures, squads, list(seats.values()))
            ours = [s for s in scores if s.member in {m.id for m in members.values()}]  # best first, ties by seat
            for m in members.values():
                players, captain = played[m.seat]
                row = {"member_id": m.id, "gameweek": number, "league_id": league_id, "players": list(players),
                       "captain": captain}  # fmt: skip
                await db.execute(
                    insert(Squad).values(row).on_conflict_do_update(
                        index_elements=[Squad.member_id, Squad.gameweek],
                        set_={"players": row["players"], "captain": captain, "picked_at": func.now()},
                    )
                )  # fmt: skip
            db.add_all(Result(member_id=s.member, gameweek=number, league_id=league_id, points=s.points, rank=rank,
                              expected_goals=s.expected_goals, goals=s.goals)
                       for rank, s in enumerate(ours, start=1))  # fmt: skip
            gameweek.status, gameweek.finished_at = "finished", datetime.now(UTC)
            gameweek.playing_by, gameweek.playing_until = None, None
            db.add(Gameweek(league_id=league_id, number=number + 1, seed=new_seed()))
        return {"gameweek": number, "seed": gameweek.seed,
                "results": [s.__dict__ | {"rank": rank} for rank, s in enumerate(ours, start=1)]}  # fmt: skip

    # --- reading ------------------------------------------------------------------------------------------

    async def league(self, league_id: str, viewer: str | None) -> dict[str, Any]:
        """The league as a member sees it: every seat (a bot in each empty one), the open gameweek, and the
        viewer's own squad. The demo league shows every squad: they are all bots but one."""
        async with self.sessions() as db:
            league = await db.get(League, league_id)
            if league is None:
                raise NotFound
            members = {m.seat: m for m in await db.scalars(select(Member).where(Member.league_id == league_id))}
            gameweek = await self._current(db, league_id)
            squads = {s.member_id: {"players": list(s.players), "captain": s.captain}
                      for s in await db.scalars(select(Squad).where(Squad.league_id == league_id,
                                                                    Squad.gameweek == gameweek.number))}  # fmt: skip
        seats = []
        for seat in range(1, SEATS + 1):
            m = members.get(seat)
            if m is None:
                t = self._template(seat)
                seats.append({"seat": seat, "member_id": None, "display_name": t["display_name"], "is_bot": True,
                              "style": t["style"], "lang": t["lang"], "squad": None, "picked": False})  # fmt: skip
                continue
            visible = league.is_demo or m.id == viewer
            seats.append({"seat": seat, "member_id": m.id, "display_name": m.display_name, "is_bot": m.is_bot,
                          "style": m.style, "lang": m.lang, "favourite_club": m.favourite_club,
                          "favourite_player": m.favourite_player,
                          "squad": squads.get(m.id) if visible else None,
                          "picked": m.id in squads})  # fmt: skip  # whether, never what: #179 shows who has picked
        current = {"number": gameweek.number, "status": gameweek.status}  # no seed while open: it names the scorers
        # What a pick may hold, so the picker never guesses (#179): the players and the rules pick_squad enforces
        roster = await self.roster(gameweek.seed)
        players = await asyncio.to_thread(cached_players, self.fixtures, gameweek.seed)
        rules = {"squad_size": SQUAD_SIZE, "max_per_club": roster.max_per_club}
        return {"league_id": league.id, "name": league.name, "join_code": league.join_code, "is_demo": league.is_demo,
                "you": viewer, "gameweek": current, "seats": seats, "roster": list(players),
                "squad_rules": rules}  # fmt: skip

    async def season(self, league_id: str) -> dict[str, Any]:
        """Totals across every played gameweek, best first; ties by seat."""
        async with self.sessions() as db:
            if await db.get(League, league_id) is None:
                raise NotFound
            totals = (await db.execute(
                select(Member.id, Member.seat, Member.display_name, Member.is_bot,
                       func.coalesce(func.sum(Result.points), 0.0), func.count(Result.gameweek))
                .outerjoin(Result, Result.member_id == Member.id)
                .where(Member.league_id == league_id)
                .group_by(Member.id)
            )).all()  # fmt: skip
            rows = (await db.scalars(select(Result).where(Result.league_id == league_id)
                                     .order_by(Result.gameweek))).all()  # fmt: skip
            played = (await db.execute(
                select(Gameweek.number, Gameweek.seed)
                .where(Gameweek.league_id == league_id, Gameweek.status == "finished").order_by(Gameweek.number)
            )).all()  # fmt: skip
        by_member: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            by_member.setdefault(r.member_id, []).append(
                {"gameweek": r.gameweek, "points": r.points, "rank": r.rank, "expected_goals": r.expected_goals,
                 "goals": r.goals, "recap_url": r.recap_url}
            )  # fmt: skip
        ordered = sorted(totals, key=lambda t: (-round(t[4], 6), t[1]))
        table = [{"rank": n, "member_id": mid, "seat": seat, "display_name": name, "is_bot": bot,
                  "points": round(total, 2), "played": played, "gameweeks": by_member.get(mid, [])}
                 for n, (mid, seat, name, bot, total, played) in enumerate(ordered, start=1)]  # fmt: skip
        seeds = [{"gameweek": n, "seed": seed} for n, seed in played]  # played gameweeks only
        return {"league_id": league_id, "table": table, "played": seeds}

    async def start(self) -> None:
        """Migrations to head, then the demo league: on every start, before the first request."""
        await migrate(self.engine)
        await self.seed_demo()

    async def close(self) -> None:
        await self.engine.dispose()

    # --- the demo league ----------------------------------------------------------------------------------

    async def seed_demo(self) -> None:
        """The demo league, once: the five managers the lobby shows, gameweek 1 on the demo match, each with a
        squad that keeps the rules (their lobby squad where it fits). Safe to run on every start."""
        roster = await self.roster(DEMO_SEED)
        demo = load_league()
        async with self.sessions.begin() as db:
            added = await db.execute(
                insert(League).values(id=DEMO_LEAGUE, name=demo["name"], is_demo=True)
                .on_conflict_do_nothing(index_elements=[League.id]).returning(League.id)
            )  # fmt: skip
            if added.scalar() is None:
                return
            db.add(Gameweek(league_id=DEMO_LEAGUE, number=1, seed=DEMO_SEED))
            await db.flush()
            for seat, m in enumerate(demo["managers"], start=1):
                member_id = f"mb_demo_{seat}"
                db.add(Member(id=member_id, league_id=DEMO_LEAGUE, seat=seat, display_name=m["display_name"],
                              lang=m["lang"], style=m["style"], favourite_club=m.get("favourite_club"),
                              is_bot=m["is_bot"]))  # fmt: skip
                await db.flush()
                players, captain = default_squad(roster, m["squad"])
                captain = m["captain"] if m["captain"] in players else captain
                db.add(Squad(member_id=member_id, gameweek=1, league_id=DEMO_LEAGUE, players=players, captain=captain))
