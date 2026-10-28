"""Leagues in a real Postgres (#96). Needs TEST_DATABASE_URL, a database these tests may wipe; skipped without it:

docker run -d --rm -p 15432:5432 -e POSTGRES_PASSWORD=test postgres:16
TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:15432/postgres uv run pytest -m postgres
"""

import asyncio
import os
import time
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import httpx
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi import FastAPI
from sqlalchemy import Column, Connection, Integer, MetaData
from sqlalchemy.exc import DBAPIError

from tests.leagues import six_clubs
from watchparty.app.main import create_app
from watchparty.sim import DEMO_SEED
from watchparty.store.db import make_engine, migrate
from watchparty.store.rules import Roster, squad_problems
from watchparty.store.schema import Base
from watchparty.store.service import CODE_ALPHABET, DEMO_LEAGUE, LeagueStore

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = [
    pytest.mark.anyio,
    pytest.mark.postgres,
    pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is not set (a Postgres these tests may wipe)"),
]
SIX_CLUB_SQUAD = {"players": ["p_ashby_1", "p_ashby_9", "p_brook_5", "p_corran_9", "p_farley_10"],
                  "captain": "p_corran_9"}  # fmt: skip


async def wipe() -> None:
    connection = await asyncpg.connect(URL)
    try:
        await connection.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await connection.close()


@pytest.fixture
async def empty_db() -> str:
    await wipe()
    return URL


@pytest.fixture
async def app(empty_db: str) -> AsyncIterator[FastAPI]:
    """The whole app with DATABASE_URL set, started as uvicorn would (migrations, then the demo league)."""
    app = create_app(database_url=empty_db)
    store: LeagueStore = app.state.leagues
    await store.start()
    yield app
    await store.close()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.fixture
def six(app: FastAPI) -> None:
    """Gameweeks of three fixtures among six clubs (#94) instead of today's one."""
    app.state.leagues.fixtures = six_clubs


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def create(client: httpx.AsyncClient, name: str = "Sofa", who: str = "Ana") -> dict[str, Any]:
    response = await client.post("/api/leagues", json={"name": name, "display_name": who, "lang": "es"})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def join(client: httpx.AsyncClient, code: str, who: str) -> httpx.Response:
    return await client.post("/api/leagues/join", json={"code": code, "display_name": who})


# --- 1: migrations ---------------------------------------------------------------------------------------


def drift(connection: Connection, metadata: MetaData = Base.metadata) -> list[Any]:
    return list(compare_metadata(MigrationContext.configure(connection, opts={"compare_type": True}), metadata))


async def test_migrations_apply_from_scratch_and_match_the_models(empty_db: str) -> None:
    engine = make_engine(empty_db)
    try:
        await migrate(engine)
        async with engine.connect() as connection:
            assert await connection.run_sync(drift) == []
        await migrate(engine)  # a second start applies nothing and does not fail
        async with engine.connect() as connection:
            version = (await connection.exec_driver_sql("SELECT version_num FROM alembic_version")).scalar()
        assert version == "0002_gameweek_playing"
    finally:
        await engine.dispose()


async def test_the_drift_check_sees_a_column_no_migration_adds(empty_db: str) -> None:
    """The instrument for case 1: a model change without a migration is reported."""
    changed = MetaData(naming_convention=Base.metadata.naming_convention)
    for table in Base.metadata.tables.values():
        table.to_metadata(changed)
    changed.tables["members"].append_column(Column("nickname", Integer))
    engine = make_engine(empty_db)
    try:
        await migrate(engine)
        async with engine.connect() as connection:
            found = await connection.run_sync(drift, changed)
    finally:
        await engine.dispose()
    assert [d[0] for d in found] == ["add_column"]


async def test_a_token_provider_supplies_the_password_for_each_new_connection(empty_db: str) -> None:
    """The Entra path (DATABASE_AUTH=entra): the URL has no password; every new connection asks the provider."""
    asked: list[str] = []

    async def provider() -> str:
        asked.append("token")
        return URL.split("//", 1)[1].split("@", 1)[0].split(":", 1)[1]  # the test database's password

    without_password = "postgresql://postgres@" + URL.split("@", 1)[1]
    engine = make_engine(without_password, provider)
    try:
        async with engine.connect() as a, engine.connect() as b:
            assert (await a.exec_driver_sql("SELECT 1")).scalar() == 1
            assert (await b.exec_driver_sql("SELECT 1")).scalar() == 1
    finally:
        await engine.dispose()
    assert len(asked) == 2


async def test_a_wrong_token_is_refused_by_the_database(empty_db: str) -> None:
    async def provider() -> str:
        return "not-the-password"

    engine = make_engine("postgresql://postgres@" + URL.split("@", 1)[1], provider)
    try:
        with pytest.raises(DBAPIError, match="password authentication failed"):
            async with engine.connect():
                pass
    finally:
        await engine.dispose()


async def test_concurrent_starts_migrate_once(empty_db: str) -> None:
    """Replicas starting together: the advisory lock lets one migrate and the rest find nothing to do."""
    engines = [make_engine(empty_db) for _ in range(4)]
    try:
        await asyncio.wait_for(asyncio.gather(*(migrate(e) for e in engines)), timeout=30)
    finally:
        for e in engines:
            await e.dispose()
    connection = await asyncpg.connect(URL)
    try:
        rows = await connection.fetch("SELECT version_num FROM alembic_version")
    finally:
        await connection.close()
    assert [r["version_num"] for r in rows] == ["0002_gameweek_playing"]


# --- 2, 3, 4: create and join ----------------------------------------------------------------------------


async def test_create_returns_a_join_code_and_the_creators_token(client: httpx.AsyncClient) -> None:
    made = await create(client)
    assert len(made["join_code"]) == 6
    assert set(made["join_code"]) <= set(CODE_ALPHABET)
    assert len(made["member_token"]) >= 40
    assert made["seat"] == 1
    league = (await client.get(f"/api/leagues/{made['league_id']}", headers=bearer(made["member_token"]))).json()
    assert league["you"] == made["member_id"]
    assert [s["is_bot"] for s in league["seats"]] == [False, True, True, True, True]  # bots in the empty seats
    assert league["seats"][0]["display_name"] == "Ana"
    assert league["seats"][0]["lang"] == "es"
    assert league["gameweek"]["number"] == 1
    assert league["gameweek"]["status"] == "open"


async def test_the_token_is_stored_only_as_a_hash(app: FastAPI, client: httpx.AsyncClient) -> None:
    made = await create(client)
    connection = await asyncpg.connect(URL)
    try:
        stored = await connection.fetchval("SELECT token_hash FROM members WHERE id = $1", made["member_id"])
    finally:
        await connection.close()
    assert len(stored) == 32
    assert made["member_token"].encode() not in stored


async def test_join_with_the_code_adds_a_member(client: httpx.AsyncClient) -> None:
    made = await create(client)
    joined = await join(client, made["join_code"].lower(), "Ben")
    assert joined.status_code == 201
    body = joined.json()
    assert body["league_id"] == made["league_id"]
    assert body["seat"] == 2
    league = (await client.get(f"/api/leagues/{made['league_id']}", headers=bearer(body["member_token"]))).json()
    assert [s["display_name"] for s in league["seats"]][:2] == ["Ana", "Ben"]


async def test_join_with_a_wrong_code_is_404(client: httpx.AsyncClient) -> None:
    made = await create(client)
    wrong = "".join(c for c in CODE_ALPHABET if c not in made["join_code"])[:6]
    assert (await join(client, wrong, "Ben")).status_code == 404


async def test_a_sixth_member_is_refused(client: httpx.AsyncClient) -> None:
    made = await create(client)
    for n in range(4):
        assert (await join(client, made["join_code"], f"Friend {n}")).status_code == 201
    sixth = await join(client, made["join_code"], "Sixth")
    assert sixth.status_code == 409
    assert "taken" in sixth.json()["detail"]


async def test_concurrent_joins_never_overfill(client: httpx.AsyncClient) -> None:
    made = await create(client)
    responses = await asyncio.gather(*(join(client, made["join_code"], f"Rush {n}") for n in range(8)))
    assert sorted(r.status_code for r in responses) == [201] * 4 + [409] * 4
    assert sorted(r.json()["seat"] for r in responses if r.status_code == 201) == [2, 3, 4, 5]


async def test_a_favourite_club_must_play_in_the_league(client: httpx.AsyncClient) -> None:
    body = {"name": "Sofa", "display_name": "Ana", "favourite_club": "t_nowhere"}
    assert (await client.post("/api/leagues", json=body)).status_code == 422
    body["favourite_club"] = "t_saltmere"
    assert (await client.post("/api/leagues", json=body)).status_code == 201


# --- 5: squads -------------------------------------------------------------------------------------------


@pytest.mark.usefixtures("six")
@pytest.mark.parametrize(
    "squad",
    [
        {"players": ["p_ashby_1", "p_ashby_9", "p_ashby_5", "p_corran_9", "p_farley_10"], "captain": "p_corran_9"},
        {"players": SIX_CLUB_SQUAD["players"], "captain": "p_dunmere_9"},
        {"players": SIX_CLUB_SQUAD["players"][:4], "captain": "p_corran_9"},
    ],
    ids=["three-from-one-club", "captain-not-in-squad", "four-players"],
)
async def test_a_bad_squad_is_rejected_and_not_saved(client: httpx.AsyncClient, squad: dict[str, Any]) -> None:
    made = await create(client)
    path = f"/api/leagues/{made['league_id']}/gameweeks/1/squad"
    response = await client.put(path, json=squad, headers=bearer(made["member_token"]))
    assert response.status_code == 422, response.text
    league = (await client.get(f"/api/leagues/{made['league_id']}", headers=bearer(made["member_token"]))).json()
    assert league["seats"][0]["squad"] is None


async def test_a_squad_with_no_captain_field_is_rejected(client: httpx.AsyncClient) -> None:
    made = await create(client)
    path = f"/api/leagues/{made['league_id']}/gameweeks/1/squad"
    response = await client.put(path, json={"players": SIX_CLUB_SQUAD["players"]}, headers=bearer(made["member_token"]))
    assert response.status_code == 422


@pytest.mark.usefixtures("six")
async def test_a_valid_squad_is_saved_and_can_be_changed_before_kickoff(client: httpx.AsyncClient) -> None:
    made = await create(client)
    path, auth = f"/api/leagues/{made['league_id']}/gameweeks/1/squad", bearer(made["member_token"])
    assert (await client.put(path, json=SIX_CLUB_SQUAD, headers=auth)).status_code == 200
    changed = {**SIX_CLUB_SQUAD, "captain": "p_ashby_9"}
    assert (await client.put(path, json=changed, headers=auth)).status_code == 200
    league = (await client.get(f"/api/leagues/{made['league_id']}", headers=auth)).json()
    assert league["seats"][0]["squad"] == changed


async def test_another_members_squad_is_hidden(client: httpx.AsyncClient) -> None:
    made = await create(client)
    ben = (await join(client, made["join_code"], "Ben")).json()
    squad = {"players": ["p_home_9", "p_home_8", "p_away_1", "p_away_4", "p_away_10"], "captain": "p_home_9"}
    path = f"/api/leagues/{made['league_id']}/gameweeks/1/squad"
    assert (await client.put(path, json=squad, headers=bearer(made["member_token"]))).status_code == 200
    seen_by_ben = (await client.get(f"/api/leagues/{made['league_id']}", headers=bearer(ben["member_token"]))).json()
    assert seen_by_ben["seats"][0]["squad"] is None


# --- 6: results and the season ---------------------------------------------------------------------------


@pytest.mark.usefixtures("six")
async def test_a_finished_gameweek_is_saved_and_the_season_sums_it(client: httpx.AsyncClient) -> None:
    made = await create(client)
    ben = (await join(client, made["join_code"], "Ben")).json()
    league, ana_auth = made["league_id"], bearer(made["member_token"])
    put = await client.put(f"/api/leagues/{league}/gameweeks/1/squad", json=SIX_CLUB_SQUAD, headers=ana_auth)
    assert put.status_code == 200  # Ben picks nothing: he gets a squad picked for him

    first = await client.post(f"/api/leagues/{league}/gameweeks/1/results", headers=ana_auth)
    assert first.status_code == 201, first.text
    by = {r["member"]: r for r in first.json()["results"]}
    assert by[made["member_id"]]["points"] == 12
    assert by[made["member_id"]]["rank"] == 1  # the captain scored
    assert (by[made["member_id"]]["goals"], by[made["member_id"]]["expected_goals"]) == (1, 0.4)
    assert by[ben["member_id"]]["rank"] == 2

    assert (await client.post(f"/api/leagues/{league}/gameweeks/1/results", headers=ana_auth)).status_code == 409
    late = await client.put(f"/api/leagues/{league}/gameweeks/1/squad", json=SIX_CLUB_SQUAD, headers=ana_auth)
    assert late.status_code == 409  # picked before kickoff, never after

    view = (await client.get(f"/api/leagues/{league}", headers=ana_auth)).json()
    assert view["gameweek"] == {"number": 2, "status": "open"}
    put = await client.put(f"/api/leagues/{league}/gameweeks/2/squad", json=SIX_CLUB_SQUAD, headers=ana_auth)
    assert put.status_code == 200
    assert (await client.post(f"/api/leagues/{league}/gameweeks/2/results", headers=ana_auth)).status_code == 201

    table = (await client.get(f"/api/leagues/{league}/season", headers=bearer(ben["member_token"]))).json()["table"]
    ana = next(r for r in table if r["member_id"] == made["member_id"])
    assert ana["played"] == 2
    assert ana["points"] == sum(g["points"] for g in ana["gameweeks"]) == 24
    assert [r["rank"] for r in table] == [1, 2]
    assert table[0]["member_id"] == made["member_id"]


async def test_a_join_waits_while_the_gameweek_is_scored(client: httpx.AsyncClient,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    """A friend joining mid-scoring joins after the gameweek closes, never into it with no result."""
    made = await create(client)
    finished: list[str] = []
    slowed_scoring(monkeypatch)

    async def record() -> None:
        path = f"/api/leagues/{made['league_id']}/gameweeks/1/results"
        assert (await client.post(path, headers=bearer(made["member_token"]))).status_code == 201
        finished.append("results")

    async def late_join() -> None:
        await asyncio.sleep(0.3)
        assert (await join(client, made["join_code"], "Late")).status_code == 201
        finished.append("join")

    await asyncio.gather(record(), late_join())
    assert finished == ["results", "join"]


def slowed_scoring(monkeypatch: pytest.MonkeyPatch, seconds: float = 1.0) -> None:
    """Scoring takes `seconds` in its thread while the results transaction holds its locks."""
    from watchparty.store import rules, service

    real = rules.score

    def scoring(*args: Any) -> Any:
        time.sleep(seconds)
        return real(*args)

    monkeypatch.setattr(service, "score", scoring)


async def test_the_seed_is_told_only_once_the_gameweek_is_played(client: httpx.AsyncClient) -> None:
    """The simulator is deterministic: an open gameweek's seed would name its scorers."""
    made = await create(client)
    league, auth = made["league_id"], bearer(made["member_token"])
    assert "seed" not in (await client.get(f"/api/leagues/{league}", headers=auth)).json()["gameweek"]
    assert (await client.get(f"/api/leagues/{league}/season", headers=auth)).json()["played"] == []
    recorded = (await client.post(f"/api/leagues/{league}/gameweeks/1/results", headers=auth)).json()
    assert isinstance(recorded["seed"], int)
    played = (await client.get(f"/api/leagues/{league}/season", headers=auth)).json()["played"]
    assert played == [{"gameweek": 1, "seed": recorded["seed"]}]
    assert "seed" not in (await client.get(f"/api/leagues/{league}", headers=auth)).json()["gameweek"]


async def test_only_the_creator_records_a_gameweek(client: httpx.AsyncClient) -> None:
    made = await create(client)
    ben = (await join(client, made["join_code"], "Ben")).json()
    path = f"/api/leagues/{made['league_id']}/gameweeks/1/results"
    refused = await client.post(path, headers=bearer(ben["member_token"]))
    assert refused.status_code == 403
    view = (await client.get(f"/api/leagues/{made['league_id']}", headers=bearer(ben["member_token"]))).json()
    assert view["gameweek"] == {"number": 1, "status": "open"}  # still open for everyone's picks
    assert (await client.post(path, headers=bearer(made["member_token"]))).status_code == 201


async def test_a_squad_edit_during_scoring_is_refused(client: httpx.AsyncClient,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    """An edit that arrives while the gameweek is being scored waits for it and then finds it played."""
    made = await create(client)
    league, auth = made["league_id"], bearer(made["member_token"])
    squad = {"players": ["p_home_9", "p_home_8", "p_away_1", "p_away_4", "p_away_10"], "captain": "p_home_9"}
    slowed_scoring(monkeypatch)

    async def late_edit() -> int:
        await asyncio.sleep(0.3)
        return (await client.put(f"/api/leagues/{league}/gameweeks/1/squad", json=squad, headers=auth)).status_code

    recorded, edited = await asyncio.gather(client.post(f"/api/leagues/{league}/gameweeks/1/results", headers=auth),
                                            late_edit())  # fmt: skip
    assert (recorded.status_code, edited) == (201, 409)


async def test_two_results_calls_at_once_record_the_gameweek_once(client: httpx.AsyncClient,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    made = await create(client)
    await join(client, made["join_code"], "Ben")
    path, auth = f"/api/leagues/{made['league_id']}/gameweeks/1/results", bearer(made["member_token"])
    slowed_scoring(monkeypatch, 0.5)
    first, second = await asyncio.gather(client.post(path, headers=auth), client.post(path, headers=auth))
    assert sorted([first.status_code, second.status_code]) == [201, 409]
    connection = await asyncpg.connect(URL)
    try:
        assert await connection.fetchval("SELECT count(*) FROM results WHERE league_id = $1", made["league_id"]) == 2
        assert await connection.fetchval("SELECT count(*) FROM gameweeks WHERE league_id = $1", made["league_id"]) == 2
    finally:
        await connection.close()


async def test_results_for_a_gameweek_that_does_not_exist_are_404(client: httpx.AsyncClient) -> None:
    made = await create(client)
    path = f"/api/leagues/{made['league_id']}/gameweeks/7/results"
    assert (await client.post(path, headers=bearer(made["member_token"]))).status_code == 404


# --- 7: a token is good for its own league only ----------------------------------------------------------


@pytest.mark.usefixtures("six")
async def test_a_token_from_league_a_is_refused_on_league_b(client: httpx.AsyncClient) -> None:
    a, b = await create(client, "A"), await create(client, "B")
    a_auth, b_league = bearer(a["member_token"]), b["league_id"]
    calls = [
        client.get(f"/api/leagues/{b_league}", headers=a_auth),
        client.put(f"/api/leagues/{b_league}/gameweeks/1/squad", json=SIX_CLUB_SQUAD, headers=a_auth),
        client.post(f"/api/leagues/{b_league}/gameweeks/1/results", headers=a_auth),
        client.get(f"/api/leagues/{b_league}/season", headers=a_auth),
    ]
    assert [(await c).status_code for c in calls] == [404, 404, 404, 404]
    own = await client.get(f"/api/leagues/{b_league}", headers=bearer(b["member_token"]))
    assert own.status_code == 200  # the same route answers its own member


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer "}, {"Authorization": "Bearer not-a-token"},
                                     {"Authorization": "Bearer ünïcode".encode("latin-1")}])  # fmt: skip
async def test_no_token_or_a_wrong_one_is_refused(client: httpx.AsyncClient, headers: dict[str, Any]) -> None:
    made = await create(client)
    response = await client.get(f"/api/leagues/{made['league_id']}", headers=headers)
    assert response.status_code == 404


async def test_creates_and_joins_are_limited_per_client(client: httpx.AsyncClient,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    from watchparty.store.api import PER_MINUTE

    made = await create(client)
    for _ in range(PER_MINUTE - 1):
        assert (await client.post("/api/leagues", json={"name": "x", "display_name": "y"})).status_code == 201
    assert (await client.post("/api/leagues", json={"name": "x", "display_name": "y"})).status_code == 429
    for _ in range(PER_MINUTE):
        assert (await join(client, "ZZZZZZ", "Guess")).status_code == 404  # guessing codes
    assert (await join(client, made["join_code"], "Real")).status_code == 429  # even the right code, for a minute
    monkeypatch.setenv("TRUST_PROXY", "1")
    other = {"X-Forwarded-For": "203.0.113.9"}
    response = await client.post("/api/leagues/join", json={"code": made["join_code"], "display_name": "Ben"},
                                 headers=other)  # fmt: skip
    assert response.status_code == 201  # another client is not held up


@pytest.mark.parametrize("field", ["name", "display_name"])
async def test_a_control_character_in_a_name_is_422(client: httpx.AsyncClient, field: str) -> None:
    body = {"name": "Sofa", "display_name": "Ana"} | {field: "Ana\x00"}
    assert (await client.post("/api/leagues", json=body)).status_code == 422


@pytest.mark.parametrize(("header", "status"), [("bearer {}", 200), ("BEARER {}", 200), ("{}", 404),
                                                 ("Basic {}", 404), ("Bearer", 404)])  # fmt: skip
async def test_the_bearer_scheme_is_required_in_any_case(client: httpx.AsyncClient, header: str, status: int) -> None:
    made = await create(client)
    response = await client.get(f"/api/leagues/{made['league_id']}",
                                headers={"Authorization": header.format(made["member_token"])})  # fmt: skip
    assert response.status_code == status


# --- 9: the demo league ----------------------------------------------------------------------------------


async def test_the_demo_league_is_there_after_start_and_read_without_joining(client: httpx.AsyncClient) -> None:
    league = (await client.get(f"/api/leagues/{DEMO_LEAGUE}")).json()
    assert league["is_demo"]
    assert league["join_code"] is None
    assert league["gameweek"] == {"number": 1, "status": "open"}  # no seed while open
    assert [s["display_name"] for s in league["seats"]] == ["Jo", "Priya", "Arjun", "Leo", "Sam"]
    assert [s["is_bot"] for s in league["seats"]] == [False, True, True, True, True]
    from watchparty.app.session import simulated

    roster = Roster.of([simulated(DEMO_SEED, 30)])
    for seat in league["seats"]:
        assert squad_problems(roster, seat["squad"]["players"], seat["squad"]["captain"]) == []
    assert (await client.get(f"/api/leagues/{DEMO_LEAGUE}/season")).status_code == 200
    # ...and a judge still goes straight into a match
    session = await client.post("/api/sessions", json={"manager": "mgr_1"})
    assert session.status_code == 201


async def test_the_demo_league_shows_whatever_token_is_sent(client: httpx.AsyncClient) -> None:
    other = await create(client)
    for token in (other["member_token"], "nonsense"):
        response = await client.get(f"/api/leagues/{DEMO_LEAGUE}", headers=bearer(token))
        assert response.status_code == 200
        assert response.json()["you"] is None
        assert (await client.get(f"/api/leagues/{DEMO_LEAGUE}/season", headers=bearer(token))).status_code == 200


async def test_the_demo_league_cannot_be_changed_without_a_token(client: httpx.AsyncClient) -> None:
    squad = {"players": ["p_home_9", "p_home_8", "p_away_1", "p_away_4", "p_away_10"], "captain": "p_home_9"}
    assert (await client.put(f"/api/leagues/{DEMO_LEAGUE}/gameweeks/1/squad", json=squad)).status_code == 404
    assert (await client.post(f"/api/leagues/{DEMO_LEAGUE}/gameweeks/1/results")).status_code == 404


async def test_starting_twice_seeds_the_demo_league_once(app: FastAPI) -> None:
    await app.state.leagues.start()
    connection = await asyncpg.connect(URL)
    try:
        assert await connection.fetchval("SELECT count(*) FROM members WHERE league_id = $1", DEMO_LEAGUE) == 5
        assert await connection.fetchval("SELECT count(*) FROM leagues") == 1
    finally:
        await connection.close()
