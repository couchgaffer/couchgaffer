"""The demo gameweek (#155, slice 9 of #94): the seed the lobby's default starts, and what it guarantees."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.test_session import no_sleep
from tools.demo_gameweek_scan import scan
from watchparty.app.main import create_app
from watchparty.points import load_league
from watchparty.sim import DEMO_GAMEWEEK_SEED, DEMO_SEED
from watchparty.sim.gameweek import build_gameweek


@pytest.mark.anyio
async def test_demo_gameweek_has_the_story() -> None:
    # Fake agents: the stakes are code, so every friend's moments are fixed; the close calls depend on the bids
    got = await scan(DEMO_GAMEWEEK_SEED)
    assert all(n >= 2 and fixtures >= 2 for n, fixtures in got["per_friend"].values()), got
    assert len(got["per_friend"]) == 5
    assert got["shared"], got  # one moment on Priya's analyst phone and a casual friend's
    assert got["host_close_calls"] >= 1, got
    assert got["both_halves"], got
    assert got["ok"]


@pytest.mark.parametrize(
    ("body", "seed"),
    [
        ({"seed": "demo", "gameweek": True}, DEMO_GAMEWEEK_SEED),
        ({"seed": "demo"}, DEMO_SEED),
        ({"seed": "demo", "gameweek": False}, DEMO_SEED),
        ({"seed": 5, "gameweek": True}, 5),
        ({"seed": 5}, 5),
    ],
)
def test_the_demo_seed_follows_the_format(body: dict[str, Any], seed: int) -> None:
    app = create_app(session_kwargs={"sleep": no_sleep})
    with TestClient(app) as client:
        made = client.post("/api/sessions", json=body | {"manager": "mgr_1"})
        assert made.status_code == 201
        session = app.state.store.get(made.json()["session_id"])
        assert session.match.meta["seed"] == seed
        assert (session.gameweek is not None) == bool(body.get("gameweek"))


def test_the_lobby_has_each_formats_squads_and_the_single_match_as_before() -> None:
    with TestClient(create_app(session_kwargs={"sleep": no_sleep})) as client:
        got = client.get("/api/lobby").json()
    # The single match's roster stays at the top level, unchanged
    assert got["league"] == load_league()
    assert sorted(got["players"]) == sorted(f"p_{s}_{n}" for s in ("home", "away") for n in range(1, 12))
    assert len(got["clubs"]) == 2
    # The gameweek's: its own league, every squad player known, the ids and names a built gameweek uses
    gw = got["gameweek"]
    assert gw["league"] == load_league("league_gw_v1.json")
    assert all(p in gw["players"] for m in gw["league"]["managers"] for p in m["squad"])
    fixtures = build_gameweek(DEMO_GAMEWEEK_SEED).fixtures
    names = {p["id"]: p["name"] for f in fixtures for side in ("home", "away") for p in f.meta[side]["players"]}
    assert {pid: p["name"] for pid, p in gw["players"].items()} == names
    assert len(gw["clubs"]) == 6
