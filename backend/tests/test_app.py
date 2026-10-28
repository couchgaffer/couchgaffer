import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.conftest import ClientFactory
from watchparty.agents.host import HostAgent
from watchparty.agents.manager import ManagerAgent
from watchparty.agents.summary import SummaryAgent
from watchparty.app.main import agents_from_env, client_address, create_app, recaps_from_env

pytestmark = pytest.mark.anyio


async def test_health_says_whether_the_agents_are_configured(client_for: ClientFactory) -> None:
    async with client_for(create_app()) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "agents": False, "recaps": False}  # code alone (#24), no recaps
    async with client_for(create_app(session_kwargs={"manager_agent": object()})) as client:
        assert (await client.get("/health")).json() == {"status": "ok", "agents": True, "recaps": False}
    async with client_for(create_app(session_kwargs={"on_full_time": object()})) as client:
        assert (await client.get("/health")).json()["recaps"] is True  # the recap job is wired (#204)


@pytest.mark.parametrize("path", ["/health", "/no-such-page"])
async def test_security_headers_on_every_response(client_for: ClientFactory, path: str) -> None:
    async with client_for(create_app()) as client:
        headers = (await client.get(path)).headers
    csp = headers["content-security-policy"]
    for directive in ("default-src 'self'", "frame-ancestors 'none'", "object-src 'none'", "base-uri 'none'"):
        assert directive in csp
    assert headers["x-content-type-options"] == "nosniff"
    # Phone links carry a session token in the query string; never leak it in Referer.
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["strict-transport-security"].startswith("max-age=")


async def test_a_500_from_an_unhandled_exception_carries_the_security_headers_and_no_text() -> None:
    app = create_app()

    @app.get("/api/broken")
    async def broken() -> None:
        raise RuntimeError("a detail only the log should see")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)  # as uvicorn: the 500 is sent, then raised
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/broken")
    assert (response.status_code, response.text) == (500, "Internal Server Error")
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["strict-transport-security"].startswith("max-age=")
    assert response.headers["permissions-policy"].startswith("camera=()")


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_api_docs_are_not_exposed(client_for: ClientFactory, path: str) -> None:
    async with client_for(create_app()) as client:
        assert (await client.get(path)).status_code == 404


async def test_serves_built_frontend(client_for: ClientFactory, tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>watch party</title>")
    async with client_for(create_app(static_dir=tmp_path)) as client:
        assert "watch party" in (await client.get("/")).text
        assert (await client.get("/health")).json()["status"] == "ok"


async def test_static_files_cannot_escape_their_directory(client_for: ClientFactory, tmp_path: Path) -> None:
    (tmp_path / "secret.txt").write_text("do not serve")
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("ok")
    async with client_for(create_app(static_dir=static)) as client:
        for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt", "/%2e%2e%2fsecret.txt"):
            assert "do not serve" not in (await client.get(path)).text


def test_missing_static_dir_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        create_app(static_dir=tmp_path / "missing")


def test_foundry_turns_on_both_agents_and_no_endpoint_means_code_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FOUNDRY_ENDPOINT", raising=False)
    assert agents_from_env() == {}
    monkeypatch.setenv("FOUNDRY_ENDPOINT", "https://example.cognitiveservices.azure.com/")
    monkeypatch.setenv("HOST_MODEL", "host-model")
    agents = agents_from_env()
    assert isinstance(agents["manager_agent"], ManagerAgent)
    assert isinstance(agents["host_agent"], HostAgent)
    assert isinstance(agents["summary_agent"], SummaryAgent)
    assert (agents["manager_agent"].model, agents["host_agent"].model) == ("gpt-5.4-mini", "host-model")


async def test_the_lobby_carries_each_club_and_its_colour(client_for: ClientFactory) -> None:
    async with client_for(create_app()) as client:
        lobby = (await client.get("/api/lobby")).json()
    clubs = lobby["clubs"]
    assert {c["short_name"] for c in clubs.values()} == {"HOL", "SAL"}
    assert all(p["colour"] == next(c["colour"] for c in clubs.values() if c["name"] == p["club"])
               for p in lobby["players"].values())  # fmt: skip
    assert {m["favourite_club"] for m in lobby["league"]["managers"]} <= set(clubs)


async def test_the_lobby_lists_every_manager_with_squad_and_captain(client_for: ClientFactory) -> None:
    async with client_for(create_app()) as client:
        lobby = (await client.get("/api/lobby")).json()
    assert [m["id"] for m in lobby["league"]["managers"]] == [f"mgr_{n}" for n in range(1, 6)]
    for m in lobby["league"]["managers"]:
        assert m["captain"] in m["squad"]
        assert all(p in lobby["players"] for p in m["squad"])
    assert lobby["players"]["p_home_1"] == {"name": "Tom Ashcombe", "club": "Hollinsford United", "colour": "#1f5fbf",
                                            "position": "GK", "shirt": 1}  # fmt: skip


async def test_the_judge_can_switch_style_with_the_session_token(client_for: ClientFactory) -> None:
    app = create_app()
    async with client_for(app) as client:
        made = (await client.post("/api/sessions", json={"seed": "demo", "manager": "mgr_2"})).json()
        sid, token = made["session_id"], made["phone_token"]
        url = f"/api/sessions/{sid}/style"
        assert (await client.post(url, json={"style": "casual"})).status_code == 404
        bad_token = {"Authorization": "Bearer nope"}
        assert (await client.post(url, json={"style": "casual"}, headers=bad_token)).status_code == 404
        auth = {"Authorization": f"Bearer {token}"}
        assert (await client.post(url, json={"style": "loud"}, headers=auth)).status_code == 422
        assert (await client.post(url, json={"style": "casual"}, headers=auth)).status_code == 204
    assert app.state.store.get(sid).managers["mgr_2"]["style"] == "casual"


@pytest.mark.parametrize(("lang", "status"), [("en", 201), ("es", 201), ("hi", 422), ("en-GB", 422)])
async def test_a_match_runs_only_in_a_language_the_checks_know(
    client_for: ClientFactory, lang: str, status: int
) -> None:
    async with client_for(create_app()) as client:
        made = await client.post("/api/sessions", json={"seed": "demo", "manager": "mgr_1", "lang": lang})
    assert made.status_code == status


async def test_screen_routes_serve_the_app_and_other_paths_do_not(client_for: ClientFactory, tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>watch party</title>")
    async with client_for(create_app(static_dir=tmp_path)) as client:
        for path in ("/watch/abc", "/phone/abc/mgr_1"):
            response = await client.get(path)
            assert response.status_code == 200
            assert "watch party" in response.text
        assert (await client.get("/nope")).status_code == 404


async def test_the_judges_page_loads_directly_with_the_headers_and_starts_nothing(
    client_for: ClientFactory, tmp_path: Path
) -> None:
    """#92: a judge can open or refresh /judges itself, and reading it costs no seat and no model call."""
    used: list[str] = []

    class Agent:  # any use of an agent, a model call included, is recorded
        def __getattr__(self, name: str) -> Any:
            used.append(name)
            raise AttributeError(name)

    (tmp_path / "index.html").write_text("<!doctype html><title>watch party</title>")
    agents = {"manager_agent": Agent(), "host_agent": Agent(), "summary_agent": Agent()}
    app = create_app(static_dir=tmp_path, session_kwargs=agents)
    async with client_for(app) as client:
        for _ in range(2):  # a load, then a refresh
            response = await client.get("/judges")
            assert response.status_code == 200
            assert "watch party" in response.text
            assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["referrer-policy"] == "no-referrer"
            assert response.headers["strict-transport-security"].startswith("max-age=")
            assert response.headers["permissions-policy"].startswith("camera=()")
        for path in ("/judges/", "/judges/x", "/judgesx", "/judge"):
            assert (await client.get(path)).status_code == 404
    assert app.state.store.sessions == {}
    assert used == []


async def test_the_league_screens_load_directly_and_nothing_else_under_league(
    client_for: ClientFactory, tmp_path: Path
) -> None:
    """#179: a friend opens the share link or a league's page directly, or refreshes it; the server sends the app
    with the security headers. Other paths under /league stay 404, and the league API is not shadowed."""
    (tmp_path / "index.html").write_text("<!doctype html><title>watch party</title>")
    async with client_for(create_app(static_dir=tmp_path)) as client:
        for path in ("/league", "/league/join", "/league/join?code=ABC234", "/league/lg_0123456789ab",
                     "/league/lg_demo"):  # fmt: skip
            response = await client.get(path)
            assert response.status_code == 200, path
            assert "watch party" in response.text
            assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
            assert response.headers["referrer-policy"] == "no-referrer"
        for path in ("/league/", "/league/x", "/league/lg_", "/league/lg_x", "/league/lg_0123456789abc",
                     "/league/lg_1/x", "/league/join/x", "/leagues", "/leaguex"):  # fmt: skip
            assert (await client.get(path)).status_code == 404, path
        # no DATABASE_URL: the league API is not mounted, and the fallback does not answer for it either
        assert (await client.get("/api/leagues/lg_0123456789ab")).status_code == 404


@pytest.mark.parametrize(("host", "allowed"), [("test", "ws://test wss://test"), ("evil;script-src *", "")])
async def test_the_csp_lets_screens_open_websockets_to_this_host_only(
    client_for: ClientFactory, host: str, allowed: str
) -> None:
    async with client_for(create_app()) as client:
        csp = (await client.get("/health", headers={"Host": host})).headers["Content-Security-Policy"]
    assert f"connect-src 'self' {allowed}".strip() + ";" in csp
    assert "script-src *" not in csp


@pytest.mark.parametrize(
    ("trusted", "forwarded", "expected"),
    [
        (False, "9.9.9.9", "direct"),  # no proxy configured: the header is ignored
        (True, "6.6.6.6, 9.9.9.9", "9.9.9.9"),  # the proxy appends the real address; the rest is the caller's
        (True, None, "direct"),
    ],
)
def test_the_client_address_trusts_only_the_proxys_own_hop(
    monkeypatch: pytest.MonkeyPatch, trusted: bool, forwarded: str | None, expected: str
) -> None:
    from starlette.requests import Request

    if trusted:
        monkeypatch.setenv("TRUST_PROXY", "1")
    else:
        monkeypatch.delenv("TRUST_PROXY", raising=False)
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    request = Request({"type": "http", "headers": headers, "client": ("direct", 1234)})
    assert client_address(request) == expected


def test_recaps_are_published_only_where_storage_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RECAP_STORAGE_ACCOUNT", raising=False)
    assert recaps_from_env() == {}
    monkeypatch.setenv("RECAP_STORAGE_ACCOUNT", "stwpexample")
    assert callable(recaps_from_env()["on_full_time"])


# --- security review (#50) ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/sessions", "/api/sessions/x/style"])
async def test_an_oversized_request_body_is_refused_before_it_is_read(client_for: ClientFactory, path: str) -> None:
    async with client_for(create_app()) as client:
        big = await client.post(path, content=b'{"manager": "' + b"x" * 1_000_000 + b'"}',
                                headers={"content-type": "application/json"})  # fmt: skip
    assert big.status_code == 413  # 100 MB bodies took the 1 GiB replica past its memory


async def test_a_streamed_body_without_a_length_is_capped_too(client_for: ClientFactory) -> None:
    async def chunks() -> Any:
        for _ in range(100):
            yield b"x" * 10_000

    async with client_for(create_app()) as client:
        r = await client.post("/api/sessions", content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413


async def test_the_server_refuses_large_websocket_frames() -> None:
    dockerfile = (Path(__file__).parents[2] / "Dockerfile").read_text()
    assert "--ws-max-size" in dockerfile  # screens never send: 16 MiB frames were accepted (#50)


async def test_a_token_with_any_characters_is_a_plain_no(client_for: ClientFactory) -> None:
    async with client_for(create_app()) as client:
        made = (await client.post("/api/sessions", json={"seed": "demo", "manager": "mgr_1"})).json()
        r = await client.post(
            f"/api/sessions/{made['session_id']}/chaos", headers={b"Authorization": "Bearer é".encode()}
        )  # raw bytes, as an attacker sends
    assert r.status_code == 404  # not a 500 from compare_digest on non-ASCII text


def test_tokens_never_reach_the_logs() -> None:
    import logging

    from watchparty.app.main import mask_tokens

    record = logging.LogRecord("uvicorn.error", logging.INFO, "", 0, '%s - "WebSocket %s" [accepted]',
                               ("127.0.0.1:1", "/ws/abc/phone%3Amgr_2?token=SECRET123&cues=only"), None)  # fmt: skip
    assert mask_tokens().filter(record)
    assert "SECRET123" not in record.getMessage()
    assert "token=***" in record.getMessage()


def test_without_database_url_the_league_store_is_never_imported() -> None:
    """#96: with DATABASE_URL unset the app is the fixed-league product; it does not even load the store."""
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "STATIC_DIR")}
    code = "import sys, watchparty.app.main; print('watchparty.store' in sys.modules, 'sqlalchemy' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)  # noqa: S603
    assert out.stdout.split() == ["False", "False"]


async def test_without_a_database_there_are_no_league_routes(client_for: ClientFactory) -> None:
    async with client_for(create_app()) as client:
        assert (await client.post("/api/leagues", json={"name": "x", "display_name": "y"})).status_code == 404
        assert (await client.get("/api/leagues/lg_demo")).status_code == 404
