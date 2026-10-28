"""A session plays a stored league's gameweek and records its results (#178). The Postgres cases need
TEST_DATABASE_URL, a database these tests may wipe, as tests/test_store_db.py does; the rest run without one."""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import OperationalError

from tests.conftest import ClientFactory
from tests.leagues import six_clubs
from watchparty.agents.checks import names_problems
from watchparty.app.main import create_app
from watchparty.app.session import NOT_PLAYED, Session
from watchparty.store import one_fixture
from watchparty.store.rules import Roster, default_squad, score, shown_names
from watchparty.store.service import LeagueStore

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.anyio
needs_db = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is not set (a Postgres these tests may wipe)")
ANA = {"players": ["p_home_9", "p_home_8", "p_away_1", "p_away_4", "p_away_10"], "captain": "p_away_10"}
BEN = {"players": ["p_home_1", "p_home_2", "p_home_3", "p_away_9", "p_away_8"], "captain": "p_home_1"}


async def no_sleep(_: float) -> None:
    await asyncio.sleep(0)


async def sql(query: str, *args: Any) -> list[Any]:
    connection = await asyncpg.connect(URL)
    try:
        return list(await connection.fetch(query, *args))
    finally:
        await connection.close()


@pytest.fixture
async def app() -> AsyncIterator[FastAPI]:
    connection = await asyncpg.connect(URL)
    try:
        await connection.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await connection.close()
    app = create_app(database_url=URL, session_kwargs={"sleep": no_sleep})
    store: LeagueStore = app.state.leagues
    await store.start()
    yield app
    await store.close()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def pick(client: httpx.AsyncClient, lid: str, token: str, squad: dict[str, Any], number: int = 1
               ) -> httpx.Response:  # fmt: skip
    return await client.put(f"/api/leagues/{lid}/gameweeks/{number}/squad", json=squad, headers=bearer(token))


async def league_of_three(client: httpx.AsyncClient) -> dict[str, Any]:
    """Ana creates (seat 1), Ben (analyst, Spanish) and Cy join; Ana and Ben pick, Cy does not."""
    ana = (await client.post("/api/leagues", json={"name": "Sofa", "display_name": "Ana"})).json()
    code = ana["join_code"]
    ben = (await client.post("/api/leagues/join", json={"code": code, "display_name": "Ben", "lang": "es",
                                                         "style": "analyst"})).json()  # fmt: skip
    cy = (await client.post("/api/leagues/join", json={"code": code, "display_name": "Cy"})).json()
    lid = ana["league_id"]
    for who, squad in ((ana, ANA), (ben, BEN)):
        picked = await pick(client, lid, who["member_token"], squad)
        assert picked.status_code == 200, picked.text
    return {"id": lid, "ana": ana["member_token"], "ben": ben["member_token"], "cy": cy["member_token"]}


async def start(client: httpx.AsyncClient, lid: str, token: str | None, manager: str, **body: Any) -> httpx.Response:
    headers = bearer(token) if token is not None else {}
    return await client.post("/api/sessions", json={"manager": manager, "league_id": lid, **body}, headers=headers)


async def play(app: FastAPI, session_id: str) -> Session:
    """The session to full time, with a group screen watching so it is not abandoned."""
    session: Session = app.state.store.get(session_id)
    session.subscribe("group")
    await session.run()
    return session


async def gameweek_seed(lid: str, number: int = 1) -> int:
    rows = await sql("SELECT seed FROM gameweeks WHERE league_id = $1 AND number = $2", lid, number)
    return int(rows[0]["seed"])


# --- the session's league (Postgres) -----------------------------------------------------------------------


@needs_db
@pytest.mark.postgres
async def test_a_session_plays_the_league_gameweek_with_every_members_squad(
    app: FastAPI, client: httpx.AsyncClient, contract_errors: Callable[[str, Any], list[str]]
) -> None:
    """Cases 1, 13 and 16: picked squads, template squads for Cy and the bots, names, styles and languages."""
    lg = await league_of_three(client)
    made = await start(client, lg["id"], lg["ben"], "mgr_2")  # style and lang left at the body's defaults
    assert made.status_code == 201, made.text
    session: Session = app.state.store.get(made.json()["session_id"])
    seed = await gameweek_seed(lg["id"])
    roster = Roster.of(one_fixture(seed))
    managers = session.league["managers"]
    assert [m["id"] for m in managers] == ["mgr_1", "mgr_2", "mgr_3", "mgr_4", "mgr_5"]
    assert [m["display_name"] for m in managers] == ["Ana", "You", "Cy", "Leo", "Sam"]
    assert (managers[0]["squad"], managers[0]["captain"]) == (ANA["players"], ANA["captain"])
    assert (managers[1]["squad"], managers[1]["captain"]) == (BEN["players"], BEN["captain"])
    for seat in (3, 4, 5):  # Cy did not pick; seats 4 and 5 are bots: each gets its seat's template squad
        expected = default_squad(roster, app.state.leagues.templates[seat - 1]["squad"])
        assert (managers[seat - 1]["squad"], managers[seat - 1]["captain"]) == expected
    assert (managers[1]["style"], managers[1]["lang"]) == ("analyst", "es")  # Ben's own, not the body's defaults
    assert session.match.events == one_fixture(seed)[0].events  # the gameweek's seed
    assert session.league["league_id"] == lg["id"]
    assert session.league["name"] == "Sofa"
    assert {"Ana", "Cy", "You"} <= set(session.allowed_names)
    assert contract_errors("league", session.league) == []


@needs_db
@pytest.mark.postgres
async def test_full_time_records_the_gameweek_in_the_season(app: FastAPI, client: httpx.AsyncClient) -> None:
    """Case 2: the points the store scores for the five squads as played, ranks among the members."""
    lg = await league_of_three(client)
    made = (await start(client, lg["id"], lg["ben"], "mgr_2")).json()
    session = await play(app, made["session_id"])
    played = {m["id"]: (m["squad"], m["captain"]) for m in session.league["managers"]}
    expected = {s.member: s.points for s in score(one_fixture(await gameweek_seed(lg["id"])), played, list(played))}
    season = (await client.get(f"/api/leagues/{lg['id']}/season", headers=bearer(lg["ana"]))).json()
    rows = {r["seat"]: r for r in season["table"]}
    assert [season["played"][0]["gameweek"]] == [1]
    for seat in (1, 2, 3):
        assert [g["gameweek"] for g in rows[seat]["gameweeks"]] == [1]
        assert rows[seat]["points"] == pytest.approx(expected[f"mgr_{seat}"])
        # what the room watched: the session's own engine over the same five squads
        assert rows[seat]["points"] == pytest.approx(session.engine.manager_points[f"mgr_{seat}"], abs=0.01)
    assert sorted(g["rank"] for r in rows.values() for g in r["gameweeks"]) == [1, 2, 3]
    league = (await client.get(f"/api/leagues/{lg['id']}", headers=bearer(lg["ana"]))).json()
    assert league["gameweek"] == {"number": 2, "status": "open"}
    # Gameweek 2 starts from the season so far
    nxt: Session = app.state.store.get((await start(client, lg["id"], lg["ben"], "mgr_2")).json()["session_id"])
    starting = [m["points"] for m in nxt.league["managers"]]
    assert starting == [pytest.approx(rows[s]["points"]) for s in (1, 2, 3)] + [0.0, 0.0]


@needs_db
@pytest.mark.postgres
@pytest.mark.parametrize("token", [None, "not-a-token", "other"], ids=["missing", "wrong", "another-league"])
async def test_a_missing_or_wrong_token_is_404(client: httpx.AsyncClient, token: str | None) -> None:
    """Cases 3 and 4."""
    lg = await league_of_three(client)
    if token == "other":  # noqa: S105  a marker, not a secret
        token = (await client.post("/api/leagues", json={"name": "Else", "display_name": "Zed"})).json()["member_token"]
    assert (await start(client, lg["id"], token, "mgr_2")).status_code == 404


@needs_db
@pytest.mark.postgres
async def test_refusals_for_a_league_session(client: httpx.AsyncClient) -> None:
    """Cases 5 to 8: an unknown league, a seat not the caller's, a gameweek or seed with league_id, no gameweek."""
    lg = await league_of_three(client)
    assert (await start(client, "lg_nope", lg["ben"], "mgr_2")).status_code == 404
    wrong = await start(client, lg["id"], lg["ben"], "mgr_1")
    assert wrong.status_code == 422
    assert "mgr_2" in wrong.json()["detail"]
    assert (await start(client, lg["id"], lg["ben"], "mgr_2", gameweek=True)).status_code == 422
    assert (await start(client, lg["id"], lg["ben"], "mgr_2", seed=5)).status_code == 422
    # none of the refusals kicked the gameweek off
    assert (await pick(client, lg["id"], lg["ana"], ANA)).status_code == 200
    assert (await sql("SELECT playing_by FROM gameweeks WHERE league_id = $1", lg["id"]))[0]["playing_by"] is None
    await sql("DELETE FROM gameweeks WHERE league_id = $1", lg["id"])
    gone = await start(client, lg["id"], lg["ben"], "mgr_2")
    assert gone.status_code == 409
    assert "no gameweek" in gone.json()["detail"]


@needs_db
@pytest.mark.postgres
async def test_a_started_match_fixes_squads_and_keeps_other_sessions_out(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """Case 18 and the claim: once Ben's match starts, no pick and no other member's session until it ends; Ben may
    start again (a refreshed lobby). After full time the next gameweek takes picks again."""
    lg = await league_of_three(client)
    first: Session = app.state.store.get((await start(client, lg["id"], lg["ben"], "mgr_2")).json()["session_id"])
    assert first.on_kickoff is not None
    soon = "SELECT playing_until < now() + interval '60 seconds' AS soon FROM gameweeks WHERE league_id = $1"
    assert (await sql(soon, lg["id"]))[0]["soon"]  # an unopened claim lasts under a minute
    await first.on_kickoff(first)  # its first screen opened: the match starts
    assert not (await sql(soon, lg["id"]))[0]["soon"]  # a started match holds it for the match
    fixed = await pick(client, lg["id"], lg["ana"], BEN)
    assert fixed.status_code == 409
    assert "squads are fixed" in fixed.json()["detail"]
    other = await start(client, lg["id"], lg["ana"], "mgr_1")
    assert other.status_code == 409
    held = await claim_of(lg["id"])
    again = await start(client, lg["id"], lg["ben"], "mgr_2")  # Ben's own match is on: no second one
    assert again.status_code == 409
    assert await claim_of(lg["id"]) == held  # the refusal does not extend it
    assert await first.on_kickoff(first)  # nor does the match's kickoff when it comes again
    assert await claim_of(lg["id"]) == held
    first.subscribe("group")
    await first.run()
    assert await claim_of(lg["id"]) == (None, None)  # recorded
    session = first
    assert session.league["managers"][0]["squad"] == ANA["players"]  # Ana's accepted pick, not the refused one
    stored = await sql("SELECT players FROM squads WHERE league_id = $1 AND gameweek = 1 ORDER BY member_id", lg["id"])
    assert sorted(tuple(r["players"]) for r in stored) == sorted(
        tuple(m["squad"]) for m in session.league["managers"][:3]
    )
    assert (await pick(client, lg["id"], lg["ana"], ANA, number=2)).status_code == 200


@needs_db
@pytest.mark.postgres
async def test_an_unopened_session_blocks_nobody(app: FastAPI, client: httpx.AsyncClient) -> None:
    """Item 1: before its match starts, a claim takes no picks away and another member's session takes it over; the
    first session then neither fixes the squads nor records."""
    lg = await league_of_three(client)
    first: Session = app.state.store.get((await start(client, lg["id"], lg["ben"], "mgr_2")).json()["session_id"])
    assert (await pick(client, lg["id"], lg["cy"], BEN)).status_code == 200
    second = await start(client, lg["id"], lg["ana"], "mgr_1")
    assert second.status_code == 201
    first.subscribe("group")
    await first.run()
    assert await sql("SELECT 1 FROM results WHERE league_id = $1", lg["id"]) == []
    session = await play(app, second.json()["session_id"])
    assert session.league["managers"][2]["squad"] == BEN["players"]  # Cy's pick made while Ben's session waited
    assert len(await sql("SELECT 1 FROM results WHERE league_id = $1 AND gameweek = 1", lg["id"])) == 3


@needs_db
@pytest.mark.postgres
async def test_a_pick_after_the_session_read_the_squads_stops_its_kickoff(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """A session would play and record a squad that is no longer the pick: it starts without fixing the squads,
    records nothing, and lets go of the gameweek."""
    lg = await league_of_three(client)
    stale: Session = app.state.store.get((await start(client, lg["id"], lg["ben"], "mgr_2")).json()["session_id"])
    assert (await pick(client, lg["id"], lg["cy"], BEN)).status_code == 200
    screen = stale.subscribe("group")
    await stale.run()
    got = drain_all(screen)
    assert not [m for m in got if m and m["type"] == "frame"]  # the match never played
    assert any(m and m["type"] == "cue" and m["data"]["payload"]["headline"] == NOT_PLAYED for m in got)
    assert got[-1] is None  # and the screen was closed
    assert not await kicked_off(lg["id"])
    assert await claim_of(lg["id"]) == (None, None)
    assert await sql("SELECT 1 FROM results WHERE league_id = $1", lg["id"]) == []


@needs_db
@pytest.mark.postgres
async def test_a_claim_that_ran_out_lets_another_member_play(app: FastAPI, client: httpx.AsyncClient) -> None:
    """A claim whose session never started runs out (the backstop when its server is gone): picks open again,
    another member may play it, and the first session then records nothing."""
    lg = await league_of_three(client)
    first: Session = app.state.store.get((await start(client, lg["id"], lg["ben"], "mgr_2")).json()["session_id"])
    await sql("UPDATE gameweeks SET playing_until = now() - interval '1 second' WHERE league_id = $1", lg["id"])
    assert (await pick(client, lg["id"], lg["ana"], ANA)).status_code == 200  # no match ever started
    second = await start(client, lg["id"], lg["ana"], "mgr_1")
    assert second.status_code == 201
    first.subscribe("group")  # dropped from the store when this client started Ana's; it still plays to the end
    await first.run()  # Ben's session finishes, but the gameweek is Ana's now
    assert await sql("SELECT 1 FROM results WHERE league_id = $1", lg["id"]) == []
    await play(app, second.json()["session_id"])
    assert len(await sql("SELECT 1 FROM results WHERE league_id = $1 AND gameweek = 1", lg["id"])) == 3


@needs_db
@pytest.mark.postgres
async def test_the_creator_recording_by_hand_leaves_the_session_nothing_to_record(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """Case 12: the creator records while Ben's session plays; at full time the session records nothing more."""
    lg = await league_of_three(client)
    made = (await start(client, lg["id"], lg["ben"], "mgr_2")).json()
    recorded = await client.post(f"/api/leagues/{lg['id']}/gameweeks/1/results", headers=bearer(lg["ana"]))
    assert recorded.status_code == 201
    before = await sql("SELECT member_id, points FROM results WHERE league_id = $1 ORDER BY member_id", lg["id"])
    await play(app, made["session_id"])
    after = await sql("SELECT member_id, points FROM results WHERE league_id = $1 ORDER BY member_id", lg["id"])
    assert [tuple(r) for r in after] == [tuple(r) for r in before]
    assert len(after) == 3
    assert len(await sql("SELECT 1 FROM gameweeks WHERE league_id = $1", lg["id"])) == 2  # no third gameweek


@needs_db
@pytest.mark.postgres
async def test_a_typed_name_the_agents_read_as_the_viewer_is_shown_by_seat(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """Case 14, through the store: Cy typed "you", and a bot seat shares Ana's name."""
    ana = (await client.post("/api/leagues", json={"name": "Sofa", "display_name": "Leo"})).json()
    you = (await client.post("/api/leagues/join", json={"code": ana["join_code"], "display_name": "you"})).json()
    made = (await start(client, ana["league_id"], you["member_token"], "mgr_2")).json()
    session: Session = app.state.store.get(made["session_id"])
    names = [m["display_name"] for m in session.league["managers"]]
    assert names == ["Leo", "You", "Arjun", "Friend A", "Sam"]  # the viewer is "You"; the bot named Leo is seat 4


@needs_db
@pytest.mark.postgres
async def test_a_busy_server_does_not_kick_the_gameweek_off(app: FastAPI) -> None:
    """No seat for the match (503): the claim is undone, so squads stay open and nobody is locked out."""
    busy = create_app(database_url=URL, max_sessions=0)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=busy), base_url="http://test") as client:
        lg = await league_of_three(client)
        assert (await start(client, lg["id"], lg["ben"], "mgr_2")).status_code == 503
        assert (await pick(client, lg["id"], lg["ana"], ANA)).status_code == 200
    await busy.state.leagues.close()


@needs_db
@pytest.mark.postgres
async def test_a_friend_who_joins_during_the_match_gets_no_result_for_it(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """Seat 4 played as a bot, so Dee, who took it mid-match, is not scored for this gameweek."""
    lg = await league_of_three(client)
    made = (await start(client, lg["id"], lg["ben"], "mgr_2")).json()
    code = (await client.get(f"/api/leagues/{lg['id']}", headers=bearer(lg["ana"]))).json()["join_code"]
    dee = await client.post("/api/leagues/join", json={"code": code, "display_name": "Dee"})
    assert dee.status_code == 201
    await play(app, made["session_id"])
    scored = await sql("SELECT m.seat FROM results r JOIN members m ON m.id = r.member_id WHERE r.league_id = $1",
                       lg["id"])  # fmt: skip
    assert sorted(r["seat"] for r in scored) == [1, 2, 3]


@needs_db
@pytest.mark.postgres
async def test_an_older_session_of_the_same_member_records_nothing(app: FastAPI, client: httpx.AsyncClient) -> None:
    """Ben starts twice (two devices, or a refreshed lobby): only the newest session's claim records."""
    lg = await league_of_three(client)
    older: Session = app.state.store.get((await start(client, lg["id"], lg["ben"], "mgr_2")).json()["session_id"])
    newer = await start(client, lg["id"], lg["ben"], "mgr_2")
    assert newer.status_code == 201
    older.subscribe("group")
    await older.run()
    assert await sql("SELECT 1 FROM results WHERE league_id = $1", lg["id"]) == []
    await play(app, newer.json()["session_id"])
    assert len(await sql("SELECT 1 FROM results WHERE league_id = $1 AND gameweek = 1", lg["id"])) == 3


@needs_db
@pytest.mark.postgres
async def test_a_busy_server_leaves_no_claim(app: FastAPI, client: httpx.AsyncClient) -> None:
    """After an earlier claim ran out, a 503 leaves no live claim: nobody is locked out."""
    lg = await league_of_three(client)
    assert (await start(client, lg["id"], lg["ben"], "mgr_2")).status_code == 201
    await sql("UPDATE gameweeks SET playing_until = now() - interval '1 second' WHERE league_id = $1", lg["id"])
    busy = create_app(database_url=URL, max_sessions=0)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=busy), base_url="http://test") as other:
        assert (await start(other, lg["id"], lg["ana"], "mgr_1")).status_code == 503
    await busy.state.leagues.close()
    assert await claim_of(lg["id"]) == (None, None)


async def claim_of(lid: str) -> tuple[Any, Any]:
    row = (await sql("SELECT playing_by, playing_until FROM gameweeks WHERE league_id = $1 AND number = 1", lid))[0]
    return row["playing_by"], row["playing_until"]


async def kicked_off(lid: str) -> bool:
    rows = await sql("SELECT kicked_off_at FROM gameweeks WHERE league_id = $1 AND number = 1", lid)
    return rows[0]["kicked_off_at"] is not None


@needs_db
@pytest.mark.postgres
async def test_a_session_that_never_starts_lets_go_of_the_gameweek(app: FastAPI, client: httpx.AsyncClient) -> None:
    """Item 1, the never-opened path: the store drops it (as _reap does after 30 s) and the claim is released, so
    picks and other members' sessions go through again, and nothing was kicked off."""
    lg = await league_of_three(client)
    sid = (await start(client, lg["id"], lg["ben"], "mgr_2")).json()["session_id"]
    app.state.store._drop(sid)
    await asyncio.gather(*app.state.store.background)
    assert await claim_of(lg["id"]) == (None, None)
    assert not await kicked_off(lg["id"])
    assert (await pick(client, lg["id"], lg["ana"], ANA)).status_code == 200
    assert (await start(client, lg["id"], lg["ben"], "mgr_2")).status_code == 429
    assert (await start(client, lg["id"], lg["ana"], "mgr_1")).status_code == 201
    await sql("UPDATE gameweeks SET cooldown_until = now() - interval '1 second' WHERE league_id = $1", lg["id"])
    assert (await start(client, lg["id"], lg["ben"], "mgr_2")).status_code == 201  # after the minute


@needs_db
@pytest.mark.postgres
async def test_an_abandoned_match_lets_go_but_its_squads_stay_fixed(app: FastAPI, client: httpx.AsyncClient) -> None:
    """Item 1, the abandoned path: the match started, so squads are fixed for good, but the claim is released and
    another member may play the gameweek at once."""
    lg = await league_of_three(client)
    session: Session = app.state.store.get((await start(client, lg["id"], lg["ben"], "mgr_2")).json()["session_id"])
    session.subscribe("group")
    session.abandoned = True
    await session.run()
    assert await kicked_off(lg["id"])
    assert await claim_of(lg["id"]) == (None, None)
    fixed = await pick(client, lg["id"], lg["ana"], BEN)
    assert fixed.status_code == 409
    assert "even if that match was left early" in fixed.json()["detail"]
    assert (await start(client, lg["id"], lg["ben"], "mgr_2")).status_code == 429  # Ben left: he waits a minute
    assert (await start(client, lg["id"], lg["ana"], "mgr_1")).status_code == 201  # nobody else does


@needs_db
@pytest.mark.postgres
async def test_a_member_cannot_extend_their_own_claim(app: FastAPI, client: httpx.AsyncClient) -> None:
    """Item 1: starting again takes a new claim that ends when the first one would."""
    lg = await league_of_three(client)
    assert (await start(client, lg["id"], lg["ben"], "mgr_2")).status_code == 201
    first_by, first_until = await claim_of(lg["id"])
    assert (await start(client, lg["id"], lg["ben"], "mgr_2")).status_code == 201
    second_by, second_until = await claim_of(lg["id"])
    assert second_by != first_by
    assert second_until == first_until


@needs_db
@pytest.mark.postgres
async def test_league_starts_are_limited_per_client_before_postgres(app: FastAPI) -> None:
    """Item 2: past the per-client limit the answer is 429, and the store is not asked."""
    limited = create_app(database_url=URL, league_per_minute=2)
    asked: list[str] = []
    real = limited.state.leagues.authenticate

    async def counting(league_id: str, token: str) -> str | None:
        asked.append(league_id)
        found: str | None = await real(league_id, token)
        return found

    limited.state.leagues.authenticate = counting
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=limited), base_url="http://test") as client:
        codes = [(await start(client, "lg_abc", "nope", "mgr_1")).status_code for _ in range(3)]
    await limited.state.leagues.close()
    assert codes == [404, 404, 429]
    assert len(asked) == 2


@needs_db
@pytest.mark.postgres
@pytest.mark.parametrize("error", [OSError("refused"), OperationalError("SELECT", None, Exception("gone"))],
                         ids=["os", "sqlalchemy"])  # fmt: skip
async def test_a_database_error_on_the_league_path_is_503(
    app: FastAPI, client: httpx.AsyncClient, error: Exception
) -> None:
    """Item 2: Postgres out of reach answers busy, not 500."""
    lg = await league_of_three(client)

    async def broken(*_: Any) -> None:
        raise error

    app.state.leagues.matchday = broken
    made = await start(client, lg["id"], lg["ben"], "mgr_2")
    assert made.status_code == 503


@needs_db
@pytest.mark.postgres
async def test_a_failure_after_the_claim_lets_go_of_it(app: FastAPI, client: httpx.AsyncClient) -> None:
    """Item 2: the claim is released when creating the session raises, not only when it answers busy."""
    lg = await league_of_three(client)

    def broken(*_: Any, **__: Any) -> None:
        raise RuntimeError("the session could not be built")

    app.state.store.create = broken
    with pytest.raises(RuntimeError):
        await start(client, lg["id"], lg["ben"], "mgr_2")
    assert await claim_of(lg["id"]) == (None, None)


@needs_db
@pytest.mark.postgres
async def test_the_league_says_who_has_picked_but_not_what(client: httpx.AsyncClient) -> None:
    """For #179: Cy sees that Ana and Ben picked for the current gameweek and that he did not, but not their squads;
    bots have not picked."""
    lg = await league_of_three(client)
    seats = (await client.get(f"/api/leagues/{lg['id']}", headers=bearer(lg["cy"]))).json()["seats"]
    assert [s["picked"] for s in seats] == [True, True, False, False, False]
    assert [s["squad"] for s in seats] == [None] * 5


@needs_db
@pytest.mark.postgres
@pytest.mark.parametrize("six", [False, True], ids=["one-fixture", "six-clubs"])
async def test_the_league_lists_the_roster_and_rules_pick_squad_enforces(
    app: FastAPI, client: httpx.AsyncClient, six: bool
) -> None:
    """For #179: every listed player is accepted in a squad, one outside the list is refused, and the per-club cap is
    the one the server applies."""
    if six:
        app.state.leagues.fixtures = six_clubs
    ana = (await client.post("/api/leagues", json={"name": "Sofa", "display_name": "Ana"})).json()
    lid, token = ana["league_id"], ana["member_token"]
    league = (await client.get(f"/api/leagues/{lid}", headers=bearer(token))).json()
    roster, rules = league["roster"], league["squad_rules"]
    assert rules == {"squad_size": 5, "max_per_club": 2 if six else 3}
    assert len(roster) == (66 if six else 22)
    assert set(roster[0]) == {"id", "name", "position", "club_id", "club_name"}
    club_of = {p["id"]: p["club_id"] for p in roster}
    for player in roster:  # a squad led by each listed player, filled from the list within the cap
        squad: list[str] = []
        for q in [player["id"], *club_of]:
            if q not in squad and sum(club_of[x] == club_of[q] for x in squad) < rules["max_per_club"]:
                squad.append(q)
        squad = squad[: rules["squad_size"]]
        picked = await pick(client, lid, token, {"players": squad, "captain": player["id"]})
        assert picked.status_code == 200, (player["id"], picked.text)
    outside = await pick(client, lid, token, {"players": [*squad[:4], "p_nobody_99"], "captain": squad[0]})
    assert outside.status_code == 422
    club = roster[0]["club_id"]
    crowded = [p["id"] for p in roster if p["club_id"] == club][: rules["max_per_club"] + 1]
    others = [p["id"] for p in roster if p["club_id"] != club][: rules["squad_size"] - len(crowded)]
    over = await pick(client, lid, token, {"players": crowded + others, "captain": crowded[0]})
    assert over.status_code == 422


# --- without a database --------------------------------------------------------------------------------------


async def test_league_id_without_a_database_is_a_clear_400(client_for: ClientFactory) -> None:
    """Case 9."""
    async with client_for(create_app()) as client:
        made = await client.post("/api/sessions", json={"manager": "mgr_1", "league_id": "lg_abc"},
                                 headers=bearer("x"))  # fmt: skip
    assert made.status_code == 400
    assert made.json()["detail"] == "Leagues are not available on this server."


def test_shown_names_keeps_the_viewer_and_gives_a_clashing_seat_a_label_nobody_typed() -> None:
    """Case 14, and item 3: a typed "Friend A" never meets a label of the same name."""
    typed = {1: "Sam", 2: "Ana", 3: "YOU", 4: "sam", 5: "I"}
    assert shown_names(typed, 2) == {1: "Sam", 2: "Ana", 3: "Friend A", 4: "Friend B", 5: "Friend C"}
    clash = shown_names({1: "Ana", 2: "you", 3: "friend a", 4: "Bo", 5: "Friend C", 6: "bo"}, 1)
    assert clash == {1: "Ana", 2: "Friend B", 3: "friend a", 4: "Bo", 5: "Friend C", 6: "Friend D"}
    assert len({n.casefold() for n in clash.values()}) == 6
    assert shown_names({1: "Ana", 2: "Ana"}, 1) == {1: "Ana", 2: "Ana"}  # the viewer plays as "You" anyway


def test_a_lower_case_league_name_passes_the_names_check_when_capitalised() -> None:
    """Case 15, and the check still refuses a name from outside the match."""
    assert names_problems("Great save for Jo.", ["jo", "Vasquith"]) == []
    assert names_problems("Great save for Jo.", ["Vasquith"]) != []


def test_a_stored_league_plays_a_single_match_only() -> None:
    league = {"league_id": "lg_x", "name": "x", "scoring_rules_version": "v1", "managers": []}
    with pytest.raises(ValueError, match="single match"):
        Session(1, "mgr_1", "casual", "en", gameweek=True, league=league)


def calls(log: list[str], name: str, fail: bool = False) -> Callable[[Session], Awaitable[None]]:
    async def hook(_: Session) -> None:
        log.append(name)
        if fail:
            raise RuntimeError("the database is gone")

    return hook


async def test_full_time_records_before_the_recaps_even_when_recording_fails() -> None:
    log: list[str] = []
    s = Session(267, "mgr_1", "casual", "en", sleep=no_sleep, on_played=calls(log, "record", fail=True),
                on_full_time=calls(log, "recaps"))  # fmt: skip
    s.subscribe("group")
    await s.run()
    assert log == ["record", "recaps"]


async def test_a_kickoff_that_fails_ends_the_match_on_every_screen() -> None:
    """Round 3, item 4: the store out of reach at kickoff: no match, a clear message, nothing recorded."""
    log: list[str] = []

    async def broken(_: Session) -> bool:
        raise OSError("the database is gone")

    s = Session(267, "mgr_1", "casual", "en", sleep=no_sleep, on_kickoff=broken, on_played=calls(log, "record"))
    screen = s.subscribe("group")
    await s.run()
    got = drain_all(screen)
    assert any(m and m["type"] == "cue" and m["data"]["payload"]["headline"] == NOT_PLAYED for m in got)
    assert not [m for m in got if m and m["type"] == "frame"]
    assert log == []


def drain_all(queue: "asyncio.Queue[dict[str, Any] | None]") -> list[dict[str, Any] | None]:
    return [queue.get_nowait() for _ in range(queue.qsize())]


async def test_an_abandoned_match_records_nothing() -> None:
    """Case 11."""
    log: list[str] = []
    s = Session(267, "mgr_1", "casual", "en", sleep=no_sleep, on_played=calls(log, "record"))
    s.subscribe("group")
    s.abandoned = True
    await s.run()
    assert log == []
