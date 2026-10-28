"""Exports traces the way the app does, in a fresh interpreter, and prints every span as JSON (#111).

The real Azure Monitor distro is configured through watchparty.telemetry with an in-memory exporter beside its
own; the Azure exporter points at a closed local port, holds a stub credential and never gets to send (the
batch delay outlasts the run, and the process ends with os._exit). tests/test_telemetry.py reads the output.

    python -m tests.telemetry_probe            # every scenario
    python -m tests.telemetry_probe requests   # only the HTTP and WebSocket requests
"""

import asyncio
import json
import os
import sys
import time
from typing import Any

SENTINEL = "sk-PROBE-7f3a9c1e"  # looks like a secret; must never reach a span
# The caller, as the requests below present it: none of it may reach a span (#128)
CLIENT = ("198.51.100.23", 45678)
FORWARDED = "203.0.113.77"
USER_AGENT = "curl/8.7.1 probe-agent"  # curl: the instrumentation would also mark it a test tool
os.environ["APPLICATIONINSIGHTS_CONNECTION_STRING"] = (
    "InstrumentationKey=00000000-0000-0000-0000-000000000000;IngestionEndpoint=http://127.0.0.1:9/"
)
# The Azure exporter's batch never comes due, by time or by size
os.environ |= {"OTEL_BSP_SCHEDULE_DELAY": "600000", "OTEL_BSP_MAX_QUEUE_SIZE": "100000",
               "OTEL_BSP_MAX_EXPORT_BATCH_SIZE": "100000"}  # fmt: skip
os.environ["ENABLE_SENSITIVE_DATA"] = "true"  # a stray setting: the app's explicit False must win
os.environ.pop("AZURE_CLIENT_ID", None)

from azure.core.credentials import AccessToken  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402

from watchparty import telemetry  # noqa: E402


class StubCredential:
    def get_token(self, *_: Any, **__: Any) -> AccessToken:
        return AccessToken("probe", int(time.time()) + 3600)


telemetry.credential = StubCredential
memory = InMemorySpanExporter()
configured = telemetry.configure("watchparty", agents=True, span_processors=[SimpleSpanProcessor(memory)])

# Only now the app, as watchparty.app.main orders it; its own configure call finds the work done.
import httpx  # noqa: E402
from agent_framework import BaseChatClient, ChatResponse, Message  # noqa: E402
from agent_framework.observability import OBSERVABILITY_SETTINGS, ChatTelemetryLayer  # noqa: E402
from opentelemetry import trace  # noqa: E402

from tests.test_session import Host, Manager, no_sleep, stakes_for  # noqa: E402
from tests.test_session import run as play  # noqa: E402
from tests.test_summary import fake_connect  # noqa: E402
from watchparty import points  # noqa: E402
from watchparty.agents.foundry import chat_runner  # noqa: E402
from watchparty.agents.host import INSTRUCTIONS as HOST_INSTRUCTIONS  # noqa: E402
from watchparty.agents.host import HostAgent, HostAnswer  # noqa: E402
from watchparty.agents.manager import INSTRUCTIONS as MANAGER_INSTRUCTIONS  # noqa: E402
from watchparty.agents.manager import Answer, ManagerAgent  # noqa: E402
from watchparty.agents.summary import INSTRUCTIONS as SUMMARY_INSTRUCTIONS  # noqa: E402
from watchparty.agents.summary import SummaryAgent, SummaryAnswer  # noqa: E402
from watchparty.app import main, session  # noqa: E402
from watchparty.app.session import Session  # noqa: E402


class Scripted(ChatTelemetryLayer[Any], BaseChatClient[Any]):
    """The model, inside the real Agent Framework chat telemetry: answers with the sentinel in its text."""

    OTEL_PROVIDER_NAME = "probe"

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text

    async def _inner_get_response(self, *, messages: Any, stream: bool, options: Any, **kwargs: Any) -> Any:
        usage: Any = {"input_token_count": 11, "output_token_count": 7}
        return ChatResponse(messages=[Message(role="assistant", contents=[self.text])], usage_details=usage)


def framework_agents() -> dict[str, Any]:
    bid = Answer(declined=False, angle="", urgency=0.5, explanation_en="", explanation=f"Model text {SENTINEL}",
                 pitch_to_host=SENTINEL, evidence_ids=[])  # fmt: skip
    pick = HostAnswer(chosen_manager="mgr_1", because="dead_heat", reason=f"Model text {SENTINEL}")
    written = SummaryAnswer(text=f"Model text {SENTINEL}", text_en="", evidence_ids=[], rulebook_ids=[])
    summary = Scripted(written.model_dump_json())
    return {
        "manager_agent": ManagerAgent(
            chat_runner(Scripted(bid.model_dump_json()), "manager", MANAGER_INSTRUCTIONS, Answer, 600), "m"
        ),
        "host_agent": HostAgent(
            chat_runner(Scripted(pick.model_dump_json()), "host", HOST_INSTRUCTIONS, HostAnswer, 200), "m"
        ),
        # no match tools here (fake_connect): the agent answers at once, without citing anything, and falls back
        "summary_agent": SummaryAgent(
            lambda _: chat_runner(summary, "summary", SUMMARY_INSTRUCTIONS, SummaryAnswer, 500), "m"
        ),
    }


def secret_names() -> None:
    """Display names that would show in a prompt: they must not show in a span."""
    real = points.load_league

    def league(name: str = "league_v1.json") -> dict[str, Any]:
        out = real(name)
        for m in out["managers"]:
            m["display_name"] = f"{m['display_name']} {SENTINEL}"
        return out

    session.load_league = league  # type: ignore[attr-defined]


class Broken(Manager):
    async def bid(self, stake: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(f"401 Unauthorized {SENTINEL}")


class Blank(Manager):
    async def bid(self, stake: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        return {"urgency": 0.5}


class BrokenHost(Host):
    async def decide(self, moment: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
        raise RuntimeError(f"500 {SENTINEL}")


def new(manager: Any = None, host: Any = None, **kw: Any) -> Session:
    return Session(seed=267, manager="mgr_1", style="casual", lang="en", speed=30, sleep=no_sleep,
                   manager_agent=manager or Manager(), host_agent=host or Host(), **kw)  # fmt: skip


CLOSE: dict[str, float] = {"mgr_1": 6, "mgr_2": 6}  # a close call: the host is asked


async def fallbacks() -> dict[str, str]:
    """One moment per case, each in its own session; returns case -> session id."""
    alone: dict[str, float] = {"mgr_1": 6}
    cases: dict[str, tuple[Session, dict[str, float], float | None]] = {
        "bid_error": (new(Broken()), CLOSE, None),
        "bid_timeout": (new(Manager(delay=1), manager_deadline_s=0.05), alone, None),
        "bid_invalid": (new(Blank()), alone, None),
        "host_timeout": (new(host=Host(delay=1), host_deadline_s=0.05), CLOSE, None),
        "host_error": (new(host=BrokenHost()), CLOSE, None),
        "host_invalid": (new(host=Host(pick="nobody")), CLOSE, None),
        "no_time_left": (new(), CLOSE, -1.0),
        "chaos": (new(), CLOSE, None),
    }
    cases["chaos"][0].simulate_failure()
    for s, deltas, late in cases.values():
        deadline = time.monotonic() + late if late is not None else None
        await s.decide(stakes_for(s, deltas), deadline)
    return {case: s.id for case, (s, _, _) in cases.items()}


class BrokenSummary:
    async def summarise(self, *_: Any) -> dict[str, Any]:
        raise RuntimeError(f"429 {SENTINEL}")


class MadeUpSummary:
    """Fails the session's own checks: its problems quote the made-up text."""

    async def summarise(self, *_: Any) -> dict[str, Any]:
        return {"text": f"You scored 987 IP {SENTINEL}.", "text_en": "", "evidence_ids": [], "status": "ok"}


async def whole_matches() -> dict[str, str]:
    """Whole matches, for what fails outside a moment's workflow: summaries, and a moment that throws."""
    summary_error = new(summary_agent=BrokenSummary(), mcp_connect=fake_connect)
    summary_checks = new(summary_agent=MadeUpSummary(), mcp_connect=fake_connect)
    moment_error = new()

    async def throws(*_: Any) -> Any:
        raise RuntimeError(f"broken {SENTINEL}")

    moment_error._decided = throws  # type: ignore[method-assign,assignment]
    matches = {"summary_error": summary_error, "summary_checks": summary_checks, "moment_error": moment_error}
    for s in matches.values():
        await play(s)
    return {case: s.id for case, s in matches.items()}


async def concurrent(n: int = 20) -> list[str]:
    """n sessions deciding a moment at once, each with two failing bids."""
    sessions = [new(Broken()) for _ in range(n)]
    await asyncio.gather(*(s.decide(stakes_for(s, CLOSE)) for s in sessions))
    return [s.id for s in sessions]


def instrumented() -> Any:
    app = main.create_app(session_kwargs={"sleep": no_sleep})

    @app.get("/api/broken")
    async def broken() -> None:
        raise RuntimeError("our own words")

    telemetry.instrument(app)
    return app


async def requests() -> dict[str, Any]:
    app = instrumented()
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False, client=CLIENT)
    caller = {"User-Agent": USER_AGENT, "X-Forwarded-For": FORWARDED}
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=caller) as http:
        await http.get("/health")
        await http.get("/api/lobby")
        await http.get(f"/api/lobby?token={SENTINEL}")
        made = (await http.post("/api/sessions", json={"seed": "demo", "manager": "mgr_1"})).json()
        auth = {"Authorization": f"Bearer {made['phone_token']}"}
        await http.post(f"/api/sessions/{made['session_id']}/chaos", headers=auth)
        await http.get("/index.html")
        await http.get("/api/broken")
    return {"session_id": made["session_id"], "phone_token": made["phone_token"]}


def websocket() -> str:
    """A screen's WebSocket, from the same caller: the app turns it away (no such session), which it does only
    after any instrumentation around it has run."""
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    client = TestClient(instrumented(), client=CLIENT)
    caller = {"User-Agent": USER_AGENT, "X-Forwarded-For": FORWARDED}
    try:
        with client.websocket_connect("/ws/nosuchsession/group?token=x", headers=caller) as ws:
            ws.receive_json()
    except WebSocketDisconnect as closed:
        return f"closed {closed.code}"
    return "open"


def as_json(span: Any) -> dict[str, Any]:
    return {
        "name": span.name,
        "kind": span.kind.name,
        "scope": span.instrumentation_scope.name if span.instrumentation_scope else None,
        "trace_id": f"{span.context.trace_id:032x}",
        "span_id": f"{span.context.span_id:016x}",
        "parent_id": f"{span.parent.span_id:016x}" if span.parent else None,
        "status": span.status.status_code.name,
        "description": span.status.description,
        "attributes": dict(span.attributes or {}),
        "events": [{"name": e.name, "attributes": dict(e.attributes or {})} for e in span.events],
        "service": span.resource.attributes.get("service.name"),
    }


async def scenarios() -> dict[str, Any]:
    secret_names()
    agents = framework_agents()
    match = new(agents["manager_agent"], agents["host_agent"], summary_agent=agents["summary_agent"],
                mcp_connect=fake_connect)  # fmt: skip
    await play(match)
    out: dict[str, Any] = {"match": match.id}
    out["fallbacks"] = await fallbacks()
    out["concurrent"] = await concurrent()
    out["whole"] = await whole_matches()
    out["requests"] = await requests()
    return out


def main_() -> None:
    only_requests = sys.argv[1:] == ["requests"]
    out: dict[str, Any] = {"requests": asyncio.run(requests())} if only_requests else asyncio.run(scenarios())
    out["websocket"] = websocket()
    provider: Any = trace.get_tracer_provider()
    out |= {
        "configured": configured,
        "provider": f"{type(provider).__module__}.{type(provider).__name__}",
        "sampler": provider.sampler.get_description(),
        "service": provider.resource.attributes.get("service.name"),
        "sensitive": OBSERVABILITY_SETTINGS.enable_sensitive_data,
        "processors": [type(p).__name__ for p in provider._active_span_processor._span_processors],
        "modules": sorted(m for m in sys.modules if m.startswith(("azure.monitor", "opentelemetry.instrumentation"))),
        "sentinel": SENTINEL,
        "caller": [CLIENT[0], str(CLIENT[1]), FORWARDED, USER_AGENT],
        "spans": [as_json(s) for s in memory.get_finished_spans()],
    }
    sys.stdout.write(json.dumps(out, default=str))
    sys.stdout.flush()
    os._exit(0)  # skip the exporter's shutdown: it would try to send


if __name__ == "__main__":
    main_()
