"""A league store that cannot start (sign-in refused, no permission to migrate, no network) leaves the server up,
ready, and without the league routes, as with no DATABASE_URL (#180). Before, it ended the process, so a wrong
DATABASE_URL restarted every replica in a loop and took the matches down with the leagues."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

import watchparty.app.main as main


class Refusing:
    """A league store whose start fails the way an Entra role without CREATE on the schema would."""

    def __init__(self) -> None:
        self.closed = False

    async def start(self) -> None:
        raise PermissionError("permission denied for schema public")

    async def close(self) -> None:
        self.closed = True


class Starting(Refusing):
    started = False

    async def start(self) -> None:
        self.started = True

    async def is_demo(self, league_id: str) -> bool:
        return league_id == "lg_demo"

    async def league(self, league_id: str, viewer: str | None) -> dict[str, str]:
        return {"league_id": league_id}


def app_with(monkeypatch: pytest.MonkeyPatch, store: Any) -> Any:
    monkeypatch.setattr(main, "leagues_from_env", lambda _url: store)
    return main.create_app(database_url="postgresql://id-watchparty@db.example:5432/watchparty")


def test_a_league_store_that_cannot_start_leaves_the_server_up_without_leagues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Refusing()
    with TestClient(app_with(monkeypatch, store)) as client:
        assert client.get("/health").status_code == 200
        ready = client.get("/ready")
        assert (ready.status_code, ready.json()) == (200, {"status": "ok", "leagues": "unavailable: PermissionError"})
        assert client.get("/api/leagues/lg_demo").status_code == 404  # as with no DATABASE_URL
        assert client.post("/api/leagues", json={"name": "x"}).status_code in (404, 405)
        made = client.post("/api/sessions", json={"seed": "demo", "manager": "mgr_1"})
        assert made.status_code == 201  # matches still run
    assert store.closed
    assert not hasattr(client.app.state, "leagues")  # type: ignore[attr-defined]


def test_a_league_store_that_starts_keeps_its_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    store = Starting()
    app = app_with(monkeypatch, store)
    with TestClient(app) as client:
        assert store.started
        assert client.get("/ready").json() == {"status": "ok"}
        assert client.get("/api/leagues/lg_demo").status_code == 200  # the routes are still there
    assert store.closed  # closed at shutdown
