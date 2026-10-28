"""The smoke test itself (#24), against a real local server: it must fail when the agents fail."""

import asyncio
import socket
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from tests.test_session import Host, Manager
from tools import smoke
from watchparty.app.main import create_app

pytestmark = pytest.mark.anyio


async def no_sleep(_: float) -> None:
    await asyncio.sleep(0)


class Broken:
    """A manager agent whose every call fails, as with a Foundry 401 or 429."""

    async def bid(self, stake: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("401 Unauthorized")


smoke_store: dict[str, Any] = {}  # each test server's session store, by its base URL


@asynccontextmanager
async def serving(static: Path, title: str = "Couch Gaffer", **session_kwargs: Any) -> AsyncIterator[str]:
    """The app on a free local port, with a stand-in front end and an instant match clock."""
    (static / "index.html").write_text(f"<title>{title}</title>")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = create_app(static_dir=static, session_kwargs={"sleep": no_sleep, **session_kwargs})
    server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="warning", lifespan="on"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    base = f"http://127.0.0.1:{port}"
    smoke_store[base] = app.state.store
    try:
        yield base
    finally:
        server.should_exit = True
        await task


async def test_the_smoke_passes_on_a_manager_agents_explanation(tmp_path: Path) -> None:
    async with serving(tmp_path, manager_agent=Manager(), host_agent=Host()) as base:
        await smoke.smoke(base)


async def test_the_smoke_fails_when_the_agents_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smoke, "AGENT_CUE_WITHIN_S", 3)
    async with serving(tmp_path, manager_agent=Broken(), host_agent=Host()) as base:
        with pytest.raises(SystemExit, match="no explanation from a manager agent"):
            await smoke.smoke(base)


async def test_the_smoke_fails_when_no_agents_are_configured(tmp_path: Path) -> None:
    async with serving(tmp_path) as base:
        with pytest.raises(SystemExit, match="health"):
            await smoke.smoke(base)


class Bilingual(Manager):
    """A manager agent that writes each manager's own language, with an English copy when it is not English."""

    async def bid(self, stake: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        answer = await super().bid(stake, context)
        if context["manager"]["lang"] == "es":
            answer |= {"explanation_en": answer["explanation"], "explanation": "Eso son puntos para ti."}
        return answer


async def full_smoke(base: str) -> str:
    """The full smoke's failure message, from a session it creates."""
    with pytest.raises(SystemExit) as failed:
        await smoke.smoke(base, full=True)
    return str(failed.value)


async def test_the_full_smoke_names_what_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smoke, "RECAPS_WITHIN_S", 1)
    monkeypatch.setattr("sys.argv", ["smoke", "url", "--full"])
    async with serving(tmp_path, manager_agent=Manager(), host_agent=Host()) as base:  # no summary agent, no recap job
        gaps = await full_smoke(base)
    assert "0 of 5 recaps" in gaps
    assert "no explanation from a manager agent" not in gaps  # the fake manager agent answered
    assert "no summary from the summary agent" in gaps
    assert "rulebook" in gaps
    # #47: Leo's Spanish came only from code fallbacks (the fake agent writes English), which does not count
    assert "no Spanish text with its English copy" in gaps


async def test_spanish_from_a_manager_agent_passes_the_spanish_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(smoke, "RECAPS_WITHIN_S", 1)
    async with serving(tmp_path, manager_agent=Bilingual(), host_agent=Host()) as base:
        gaps = await full_smoke(base)
    assert "Spanish" not in gaps


async def test_a_match_that_ends_before_any_decision_still_reaches_the_list_of_gaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    monkeypatch.setattr(smoke, "RECAPS_WITHIN_S", 1)
    async with serving(tmp_path) as base, httpx.AsyncClient(base_url=base) as http:
        made = (await http.post("/api/sessions", json={"seed": "demo", "manager": "mgr_1"})).json()
        store = smoke_store[base]

        async def drop_once_watched() -> None:  # the match ends at once: no decision, so no bid latency
            while not store.sessions[made["session_id"]][0].connections():
                await asyncio.sleep(0.01)
            store._drop(made["session_id"])

        dropper = asyncio.create_task(drop_once_watched())
        with pytest.raises(SystemExit, match="no group-screen decisions"):  # #47: named, not a StatisticsError
            await smoke.full_match(base, made["session_id"], made["phone_token"])
        await dropper


async def test_when_every_screen_is_closed_the_full_smoke_stops_waiting_and_names_the_recaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(smoke, "RECAPS_WITHIN_S", 60)  # it must not wait this out
    async with serving(tmp_path, manager_agent=Manager(), host_agent=Host()) as base:
        store = smoke_store[base]
        store.linger_s = 0

        async def drop_when_finished() -> None:  # the session is reaped: every socket closes with 1000
            while not any(s.finished_at for s, _, _ in store.sessions.values()):
                await asyncio.sleep(0.05)
            store._reap()

        dropper = asyncio.create_task(drop_when_finished())
        start = time.perf_counter()
        gaps = await full_smoke(base)
        await dropper
    assert time.perf_counter() - start < 15  # not the 60 s recap wait, and no ConnectionClosedOK traceback
    assert "0 of 5 recaps" in gaps


async def test_the_full_smoke_waits_for_the_whistle_its_group_socket_has_not_read_yet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def watch(base: str, session: str, token: str, screens: dict[str, list[dict[str, Any]]],
              done: asyncio.Event) -> list[asyncio.Task[None]]:  # fmt: skip
        async def phones() -> None:  # all ten summaries at once, as when they fall back to code
            for screen in smoke.PHONES:
                screens[screen] += [{"type": "cue", "data": {"kind": "summary", "source": "fallback", "screen": screen,
                                     "payload": {"headline": h, "body": "On 231 IP.", "lang": "en"}}}
                                    for h in ("Half time", "Full time")]  # fmt: skip
            await done.wait()

        async def group() -> None:  # one collector turn behind (#59)
            await asyncio.sleep(1.2)
            screens["group"].append({"type": "event", "data": {"type": "full_time"}})
            await done.wait()

        return [asyncio.create_task(phones()), asyncio.create_task(group())]

    monkeypatch.setattr(smoke, "watch", watch)
    monkeypatch.setattr("sys.argv", ["smoke", "url", "--full", "--no-recaps"])
    with pytest.raises(SystemExit) as failed:  # no decisions or agents here: only the whistle is under test
        await smoke.full_match("http://test", "s", "t")
    assert "no full-time whistle" not in str(failed.value)


def test_the_smoke_looks_for_the_title_the_front_end_ships() -> None:
    assert smoke.TITLE in (Path(__file__).parents[2] / "frontend" / "index.html").read_text()


async def test_the_smoke_knows_the_lobby_by_its_title(tmp_path: Path) -> None:
    async with serving(tmp_path, "Couch Gaffer", manager_agent=Manager(), host_agent=Host()) as base:
        await smoke.smoke(base)


@pytest.mark.parametrize("title", ["Mini-league watch party", "Mini-league watch party</title><p>Couch Gaffer"])
async def test_the_smoke_refuses_the_old_title_and_the_name_outside_the_title(tmp_path: Path, title: str) -> None:
    async with serving(tmp_path, title, manager_agent=Manager(), host_agent=Host()) as base:  # #120
        with pytest.raises(SystemExit, match="lobby page"):
            await smoke.smoke(base)


async def test_the_smoke_fails_on_a_page_that_is_not_the_lobby(tmp_path: Path) -> None:
    async with serving(tmp_path, "Some other site", manager_agent=Manager(), host_agent=Host()) as base:
        with pytest.raises(SystemExit, match="lobby page"):
            await smoke.smoke(base)
