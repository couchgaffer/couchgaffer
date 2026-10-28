"""Traces to Application Insights (#111).

The end-to-end cases run tests/telemetry_probe.py in a fresh interpreter: configuring OpenTelemetry sets
process-wide providers, which must not leak into the other tests.
"""

import json
import logging
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from tests.test_session import Host, Manager, session, stakes_for
from watchparty import telemetry
from watchparty.recap import worker
from watchparty.recap.bundle import VERSION

BACKEND = Path(__file__).parents[1]
# Written out, not read from telemetry.ENVIRONMENT, so dropping a line there fails here.
EXPECTED_ENVIRONMENT = {
    "OTEL_LOGS_EXPORTER": "none",
    "OTEL_METRICS_EXPORTER": "none",
    "OTEL_EXPERIMENTAL_RESOURCE_DETECTORS": "otel",
    "APPLICATIONINSIGHTS_STATSBEAT_DISABLED_ALL": "true",
    "APPLICATIONINSIGHTS_SDKSTATS_DISABLED": "true",
    "APPLICATIONINSIGHTS_CONTROLPLANE_DISABLED": "true",
    "APPLICATIONINSIGHTS_OPENTELEMETRY_RESOURCE_METRIC_DISABLED": "true",
}
Span = dict[str, Any]


def python(code_or_module: list[str], **env: str) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items() if k != telemetry.SETTING and not k.startswith("OTEL_")}
    return subprocess.run(  # noqa: S603 (our own interpreter and code)
        [sys.executable, *code_or_module], cwd=BACKEND, env=clean | env, capture_output=True, text=True, timeout=120
    )


@pytest.fixture(scope="module")
def probe() -> dict[str, Any]:
    done = python(["-m", "tests.telemetry_probe"])
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout)  # type: ignore[no-any-return]


@pytest.fixture
def clean_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """For in-process cases: whatever configure touches is put back."""
    monkeypatch.setattr(telemetry, "_configured", [])
    for key in (*telemetry.ENVIRONMENT, "AZURE_CLIENT_ID", telemetry.SETTING):
        monkeypatch.delenv(key, raising=False)


def parents(span: Span, spans: list[Span]) -> list[Span]:
    by_id = {s["span_id"]: s for s in spans}
    chain = []
    while span["parent_id"] in by_id:
        span = by_id[span["parent_id"]]
        chain.append(span)
    return chain


def of_session(probe: dict[str, Any], session_id: str, name: str) -> list[Span]:
    return [s for s in probe["spans"] if s["name"] == name and s["attributes"].get("session_id") == session_id]


# --- case 1: unset ----------------------------------------------------------------------------------------


def test_with_the_setting_unset_nothing_is_imported_or_configured() -> None:
    code = (
        "import json, sys; import watchparty.app.main as m; from opentelemetry import trace; "
        "print(json.dumps({'modules': [n for n in sys.modules if n.startswith(('azure.monitor', "
        "'opentelemetry.instrumentation', 'opentelemetry.sdk.trace'))], "
        "'provider': type(trace.get_tracer_provider()).__name__, 'configured': m.telemetry._configured}))"
    )
    done = python(["-c", code])
    assert done.returncode == 0, done.stderr
    got = json.loads(done.stdout)
    assert got == {"modules": [], "provider": "ProxyTracerProvider", "configured": []}


# --- the provider and what it exports --------------------------------------------------------------------


def test_the_provider_is_the_one_configured_here_and_keeps_every_trace(probe: dict[str, Any]) -> None:
    assert probe["configured"] is True
    assert probe["provider"] == "opentelemetry.sdk.trace.TracerProvider"
    assert probe["service"] == "watchparty"
    assert probe["sampler"] == "ApplicationInsightsSampler1.0"  # not the distro's 5 traces a second
    # Azure's batch exporter and the probe's own; no live metrics or performance counter processors
    assert probe["processors"] == ["_GenAIMainAgentSpanProcessor", "SimpleSpanProcessor", "BatchSpanProcessor"]
    assert probe["sensitive"] is False  # ENABLE_SENSITIVE_DATA=true was in the probe's environment
    assert all(s["service"] == "watchparty" for s in probe["spans"])


def test_one_demo_match_exports_our_spans_and_the_frameworks_and_nothing_else(probe: dict[str, Any]) -> None:
    traces = {s["trace_id"] for s in of_session(probe, probe["match"], "moment")}
    match = [s for s in probe["spans"] if s["trace_id"] in traces]
    names = Counter(s["name"].split(" ")[0] for s in match)
    assert set(names) == {"key_moment", "moment", "workflow.run", "executor.process", "edge_group.process",
                          "message.send", "manager_bid", "host_decide", "invoke_agent", "chat"}  # fmt: skip
    assert names["moment"] == len(traces) > 0  # one trace per key moment
    assert {s["scope"] for s in probe["spans"]} == {
        "watchparty.session", "watchparty.workflow", "agent_framework", "opentelemetry.instrumentation.fastapi"
    }  # fmt: skip
    assert {s["kind"] for s in probe["spans"]} == {"INTERNAL", "PRODUCER", "SERVER"}  # no outgoing calls traced
    chats = [s for s in match if s["name"].startswith("chat")]
    assert chats
    assert all(s["attributes"]["gen_ai.usage.input_tokens"] == 11 for s in chats)
    assert all(s["attributes"]["gen_ai.usage.output_tokens"] == 7 for s in chats)
    for span in match:
        if span["name"] in ("manager_bid", "host_decide"):
            assert [p["name"] for p in parents(span, probe["spans"])][-2:] == ["moment", "key_moment"]


def test_no_span_carries_model_text_display_names_or_tokens(probe: dict[str, Any]) -> None:
    secrets = [probe["sentinel"], probe["requests"]["phone_token"]]
    for span in probe["spans"]:
        text = json.dumps(span)
        assert not [s for s in secrets if s in text], span["name"]
        assert not [k for k in span["attributes"] if re.match(r"gen_ai\.(input|output)\.messages|gen_ai\.system", k)]


def test_only_api_requests_become_spans(probe: dict[str, Any]) -> None:
    served = sorted(s["name"] for s in probe["spans"] if s["kind"] == "SERVER")
    # not /health (probed every 10 s), not static files, no per-message send or receive spans
    assert served == ["GET /api/broken", "GET /api/lobby", "GET /api/lobby", "POST /api/sessions",
                      "POST /api/sessions/{session_id}/chaos"]  # fmt: skip
    assert not [s for s in probe["spans"] if s["scope"].endswith("fastapi") and s["kind"] != "SERVER"]


def test_request_spans_keep_no_query_string(probe: dict[str, Any]) -> None:
    lobby = [s["attributes"] for s in probe["spans"] if s["name"] == "GET /api/lobby"]
    assert [(a["http.url"], a["http.target"]) for a in lobby] == [("http://test/api/lobby", "/api/lobby")] * 2


# --- nothing about the caller (#128) -----------------------------------------------------------------------

# Every name the installed instrumentation could give the caller's address, port or user agent, old and new
CALLER_KEYS = {"net.peer.ip", "net.peer.port", "net.peer.name", "net.sock.peer.addr", "net.sock.peer.port",
               "http.client_ip", "client.address", "client.port", "network.peer.address", "network.peer.port",
               "http.user_agent", "user_agent.original", "user_agent.synthetic.type"}  # fmt: skip


@pytest.fixture(scope="module", params=["http", "http/dup"])
def new_names(request: pytest.FixtureRequest) -> dict[str, Any]:
    """The requests alone, with the new HTTP names (alone, then beside the old) and request header capture on."""
    capture = {"OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_REQUEST": "user-agent,x-forwarded-for,host"}
    done = python(["-m", "tests.telemetry_probe", "requests"], OTEL_SEMCONV_STABILITY_OPT_IN=request.param, **capture)
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout)  # type: ignore[no-any-return]


def assert_no_caller(spans: list[Span], caller: list[str]) -> None:
    requests = [s for s in spans if s["kind"] == "SERVER"]
    assert len(requests) == 5
    address, _, forwarded, agent = caller
    for span in requests:
        assert not CALLER_KEYS & set(span["attributes"]), span["name"]
        assert not [k for k in span["attributes"] if k.startswith("http.request.header.")], span["name"]
        text = json.dumps(span)
        assert not [s for s in (address, forwarded, agent, "probe-agent") if s in text], span["name"]


def test_request_spans_carry_nothing_about_the_caller(probe: dict[str, Any]) -> None:
    assert_no_caller(probe["spans"], probe["caller"])
    lobby = [s["attributes"] for s in probe["spans"] if s["name"] == "GET /api/lobby"]
    got = [(a["http.method"], a["http.route"], a["http.status_code"]) for a in lobby]
    assert got == [("GET", "/api/lobby", 200)] * 2


def test_nor_under_the_new_names_or_with_header_capture_on(new_names: dict[str, Any]) -> None:
    assert_no_caller(new_names["spans"], new_names["caller"])
    lobby = [s["attributes"] for s in new_names["spans"] if s["name"] == "GET /api/lobby"]
    # the new names are on, so their absence above means something
    assert [(a["http.request.method"], a["url.path"], a["http.response.status_code"]) for a in lobby] == [
        ("GET", "/api/lobby", 200)
    ] * 2


def test_a_screens_websocket_is_never_a_span(probe: dict[str, Any], new_names: dict[str, Any]) -> None:
    for got in (probe, new_names):
        assert_no_websocket_span(got)


def assert_no_websocket_span(got: dict[str, Any]) -> None:
    assert got["websocket"] == "closed 1008"  # it reached the app and was turned away
    assert not [s for s in got["spans"] if s["scope"].endswith("fastapi") and "/ws/" in s["name"]]


class Unrecorded:
    """A span whose SDK attribute map is not where drop_client looks for it."""

    def __init__(self) -> None:
        self.attributes = {"net.peer.ip": "198.51.100.23", "user_agent.original": "curl/8", "http.method": "GET",
                           "http.request.header.x_forwarded_for": ("203.0.113.77",)}  # fmt: skip

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value


def test_without_the_sdk_map_the_caller_is_blanked_not_kept() -> None:
    span = Unrecorded()
    telemetry.drop_client(span, {})  # type: ignore[arg-type]
    assert span.attributes == {"net.peer.ip": "", "user_agent.original": "", "http.method": "GET",
                               "http.request.header.x_forwarded_for": ""}  # fmt: skip


def test_an_unhandled_api_error_keeps_its_exception_on_the_request_span(probe: dict[str, Any]) -> None:
    [span] = [s for s in probe["spans"] if s["name"] == "GET /api/broken"]
    assert span["status"] == "ERROR"
    assert span["attributes"]["http.status_code"] == 500
    assert [(e["name"], e["attributes"]["exception.type"]) for e in span["events"]] == [("exception", "RuntimeError")]


# --- what fails outside a moment's workflow ---------------------------------------------------------------


@pytest.mark.parametrize(("case", "error"), [("summary_error", "RuntimeError"), ("summary_checks", "FailedChecks")])
def test_a_summary_fallback_lands_on_its_summary_span(probe: dict[str, Any], case: str, error: str) -> None:
    spans = of_session(probe, probe["whole"][case], "summary")
    assert len(spans) == 10  # five managers, half time and full time
    assert {(s["attributes"]["manager"], s["attributes"]["phase"]) for s in spans} == {
        (f"mgr_{n}", phase) for n in range(1, 6) for phase in ("half_time", "full_time")
    }
    for span in spans:
        assert (span["status"], span["description"], span["attributes"]["source"]) == ("ERROR", error, "fallback")
        assert span["events"] == [{"name": "exception", "attributes": {"exception.type": error}}]


def test_a_moment_that_throws_lands_on_its_key_moment_span(probe: dict[str, Any]) -> None:
    spans = of_session(probe, probe["whole"]["moment_error"], "key_moment")
    assert spans
    assert {(s["status"], s["description"]) for s in spans} == {("ERROR", "RuntimeError")}
    assert all(s["attributes"]["moment_id"] and s["parent_id"] is None for s in spans)


def test_the_summary_agents_spans_nest_under_its_summary(probe: dict[str, Any]) -> None:
    summaries = {s["span_id"] for s in of_session(probe, probe["match"], "summary")}
    agents = [s for s in probe["spans"] if s["name"] == "invoke_agent summary"]
    assert len(summaries) == 10
    assert agents
    assert all(s["parent_id"] in summaries for s in agents)


# --- fallbacks --------------------------------------------------------------------------------------------

FALLBACKS = {
    # case: (span, its fallback attribute, the error type on it, or None when nothing failed)
    "bid_error": ("manager_bid", "error", "RuntimeError"),
    "bid_timeout": ("manager_bid", "timeout", "TimeoutError"),
    "bid_invalid": ("manager_bid", "invalid answer", "InvalidAnswer"),
    "host_timeout": ("host_decide", "timeout", "TimeoutError"),
    "host_error": ("host_decide", "error", "RuntimeError"),
    "host_invalid": ("host_decide", "invalid answer", "InvalidAnswer"),
    "no_time_left": ("manager_bid", "no time left", None),
    "chaos": ("manager_bid", "chaos", None),
}


@pytest.mark.parametrize("case", FALLBACKS)
def test_each_fallback_is_on_its_span(probe: dict[str, Any], case: str) -> None:
    name, kind, error = FALLBACKS[case]
    session_id = probe["fallbacks"][case]
    spans = of_session(probe, session_id, name)
    assert spans
    for span in spans:
        attributes = span["attributes"]
        assert attributes["fallback"] == kind
        assert isinstance(attributes["latency_ms"], int)
        assert re.fullmatch(r"mgr_[1-5]", attributes["manager"])
        assert attributes["moment_id"]
        assert span["status"] == ("ERROR" if error else "UNSET")
        assert span["description"] == error
        assert span["events"] == ([{"name": "exception", "attributes": {"exception.type": error}}] if error else [])
        [moment] = [p for p in parents(span, probe["spans"]) if p["name"] == "moment"]
        assert moment["attributes"]["session_id"] == session_id


def test_every_fallback_span_is_kept_with_concurrent_sessions(probe: dict[str, Any]) -> None:
    sessions = probe["concurrent"]
    bids = [s for s in probe["spans"] if s["name"] == "manager_bid" and s["attributes"]["session_id"] in sessions]
    failed = {(s["attributes"]["session_id"], s["attributes"]["manager"]) for s in bids if s["status"] == "ERROR"}
    assert failed == {(s, m) for s in sessions for m in ("mgr_1", "mgr_2")}  # 20 sessions at once, two bids each


@pytest.mark.anyio
@pytest.mark.parametrize("who", ["manager", "host"])
async def test_a_timeout_logs_who_fell_back_and_after_how_long(caplog: pytest.LogCaptureFixture, who: str) -> None:
    slow: dict[str, Any] = {"manager": {"manager": Manager(delay=1), "manager_deadline_s": 0.05},
            "host": {"host": Host(delay=1), "host_deadline_s": 0.05}}[who]  # fmt: skip
    s = session(**slow)
    with caplog.at_level(logging.WARNING, logger="watchparty.app.workflow"):
        await s.decide(stakes_for(s, {"mgr_1": 6, "mgr_2": 6}))
    lines = [r.getMessage() for r in caplog.records]
    pattern = {"manager": r"manager mgr_[12]", "host": r"host \(the scorer picked mgr_[12]\)"}[who]
    assert lines
    assert all(re.fullmatch(pattern + r" fell back to code: timeout after 0\.\d s", line) for line in lines), lines


# --- configuration, in process: configure_azure_monitor itself is replaced --------------------------------


class Recorded:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}
        self.sensitive: list[Any] = []

    def configure(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def enable(self, **kwargs: Any) -> None:
        self.sensitive.append(kwargs.get("enable_sensitive_data", "missing"))


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch, clean_state: None) -> Recorded:
    import agent_framework.observability
    import azure.monitor.opentelemetry

    got = Recorded()
    monkeypatch.setattr(azure.monitor.opentelemetry, "configure_azure_monitor", got.configure)
    monkeypatch.setattr(agent_framework.observability, "enable_instrumentation", got.enable)
    monkeypatch.setenv(telemetry.SETTING, "InstrumentationKey=00000000-0000-0000-0000-000000000000")
    return got


def test_on_azure_the_apps_own_managed_identity_signs_in(recorded: Recorded, monkeypatch: pytest.MonkeyPatch) -> None:
    import azure.identity

    class Identity(azure.identity.ManagedIdentityCredential):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.kwargs = kwargs

    monkeypatch.setattr(azure.identity, "ManagedIdentityCredential", Identity)
    monkeypatch.setenv("AZURE_CLIENT_ID", "11111111-2222-3333-4444-555555555555")
    assert telemetry.configure("watchparty", agents=True)
    credential = recorded.kwargs["credential"]
    assert isinstance(credential, Identity)
    assert credential.kwargs == {"client_id": "11111111-2222-3333-4444-555555555555"}


def test_on_a_laptop_the_az_login_signs_in(recorded: Recorded) -> None:
    from azure.identity import DefaultAzureCredential

    assert telemetry.configure("watchparty", agents=True)
    assert isinstance(recorded.kwargs["credential"], DefaultAzureCredential)


def test_only_traces_are_exported_every_one_and_no_library_is_instrumented(recorded: Recorded) -> None:
    assert telemetry.configure("watchparty", agents=True)
    kwargs = recorded.kwargs
    assert kwargs["sampling_ratio"] == 1.0
    assert (kwargs["enable_live_metrics"], kwargs["enable_performance_counters"]) == (False, False)
    assert {name for name, o in kwargs["instrumentation_options"].items() if not o["enabled"]} >= {
        "azure_sdk", "fastapi", "httpx", "requests", "urllib", "urllib3"
    }  # fmt: skip
    assert {k: os.environ.get(k) for k in EXPECTED_ENVIRONMENT} == EXPECTED_ENVIRONMENT
    assert recorded.sensitive == [False]


def test_the_recap_job_does_not_turn_on_agent_spans(recorded: Recorded) -> None:
    assert telemetry.configure("watchparty-recap", agents=False)
    assert recorded.sensitive == []
    assert recorded.kwargs["resource"].attributes["service.name"] == "watchparty-recap"


# --- failure isolation ------------------------------------------------------------------------------------


def broken(*_: Any, **__: Any) -> Any:
    raise RuntimeError("no luck")


def test_a_failure_to_configure_leaves_the_app_running(
    recorded: Recorded, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import azure.monitor.opentelemetry

    monkeypatch.setattr(azure.monitor.opentelemetry, "configure_azure_monitor", broken)
    with caplog.at_level(logging.WARNING, logger="watchparty.telemetry"):
        assert telemetry.configure("watchparty", agents=True) is False
    assert telemetry._configured == []
    assert [r.getMessage() for r in caplog.records] == ["traces are not exported: RuntimeError no luck"]


def test_a_failure_to_make_the_credential_leaves_the_app_running(
    recorded: Recorded, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(telemetry, "credential", broken)
    assert telemetry.configure("watchparty", agents=True) is False
    assert recorded.kwargs == {}


def test_a_failure_to_instrument_or_flush_never_raises(monkeypatch: pytest.MonkeyPatch, clean_state: None) -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    monkeypatch.setattr(telemetry, "_configured", ["watchparty"])
    monkeypatch.setattr(FastAPIInstrumentor, "instrument_app", broken)
    from watchparty.app.main import create_app

    app = create_app()
    telemetry.instrument(app)
    assert TestClient(app).get("/health").status_code == 200
    monkeypatch.setattr(trace, "get_tracer_provider", broken)
    telemetry.flush()


def test_a_bad_connection_string_leaves_the_real_app_serving() -> None:
    code = (
        "from fastapi.testclient import TestClient; import watchparty.app.main as m; "
        "print(m.telemetry._configured, TestClient(m.app).get('/health').status_code)"
    )
    done = python(["-c", code], **{telemetry.SETTING: "not a connection string"})
    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == ["[]", "200"]
    assert "traces are not exported" in done.stderr


def test_the_recap_job_flushes_before_it_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    monkeypatch.setattr(telemetry, "configure", lambda *_, **__: order.append("configure"))
    monkeypatch.setattr(telemetry, "flush", lambda: order.append("flush"))

    def run(_: list[str]) -> int:
        order.append("run")
        raise RuntimeError("the render failed")

    monkeypatch.setattr(worker, "_run", run)
    with pytest.raises(RuntimeError):
        worker.main([])
    assert order == ["configure", "run", "flush"]  # even when the run fails


@pytest.mark.parametrize(("flushed", "warned"), [(True, []), (False, ["traces were not flushed within 10 s"])])
def test_a_flush_that_times_out_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, flushed: bool, warned: list[str]
) -> None:
    class Provider:
        def force_flush(self, timeout_millis: int) -> bool:
            assert timeout_millis == 10_000
            return flushed

    monkeypatch.setattr(telemetry, "_configured", ["watchparty-recap"])
    monkeypatch.setattr(trace, "get_tracer_provider", Provider)
    with caplog.at_level(logging.WARNING, logger="watchparty.telemetry"):
        telemetry.flush()
    assert [r.getMessage() for r in caplog.records] == warned


def test_the_recap_job_exits_with_its_own_code_when_flushing_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telemetry, "_configured", ["watchparty-recap"])  # configure returns at once
    monkeypatch.setattr(trace, "get_tracer_provider", broken)
    monkeypatch.setattr(worker, "_run", lambda _: 3)
    assert worker.main([]) == 3


# --- the recap job's span ---------------------------------------------------------------------------------

SID = "s" * 16


class Queue:
    def __init__(self) -> None:
        self.deleted = False

    def receive_message(self, visibility_timeout: int) -> Any:
        return type("Message", (), {"content": json.dumps({"session_id": SID}), "dequeue_count": 1})()

    def delete_message(self, message: Any) -> None:
        self.deleted = True


@pytest.fixture
def recap_spans(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    """The recap tracer on a provider of its own: the process-wide one is left alone."""
    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    monkeypatch.setattr(worker, "tracer", provider.get_tracer("watchparty.recap"))
    return memory


def test_a_render_is_one_span_with_the_session(
    recap_spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    done = {"managers": ["mgr_1", "mgr_2"], "render_s": 1.0, "total_s": 2.0}
    monkeypatch.setattr(worker, "_render_and_upload", lambda *_: done)
    queue = Queue()
    assert worker.process(queue, None, dict) == 0
    [span] = recap_spans.get_finished_spans()
    assert span.name == "recap_render"
    assert dict(span.attributes or {}) == {"session_id": SID, "try": 1, "managers": 2, "render_s": 1.0,
                                           "total_s": 2.0}  # fmt: skip
    assert queue.deleted


def test_a_failed_render_is_an_error_with_its_type_only(
    recap_spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fails(*_: Any) -> Any:
        raise ValueError(f"the bundle is for another session {SID}x")

    monkeypatch.setattr(worker, "_render_and_upload", fails)
    queue = Queue()
    with pytest.raises(ValueError, match="another session"):
        worker.process(queue, None, dict)
    [span] = recap_spans.get_finished_spans()
    assert span.status.status_code == StatusCode.ERROR
    assert span.status.description == "ValueError"
    events = [(e.name, dict(e.attributes or {})) for e in span.events]
    assert events == [("exception", {"exception.type": "ValueError"})]
    assert not queue.deleted  # it comes back for another try


def test_the_render_pool_spawns_rather_than_forks(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    class Pool:
        def __init__(self, max_workers: int, mp_context: Any) -> None:
            seen.append(mp_context.get_start_method())

        def __enter__(self) -> "Pool":
            return self

        def __exit__(self, *_: Any) -> None: ...

        def map(self, fn: Any, bundles: list[Any], managers: list[str], outs: list[Path]) -> list[Path]:
            return [out / f"{m}.mp4" for m, out in zip(managers, outs, strict=True)]

    monkeypatch.setattr(worker, "ProcessPoolExecutor", Pool)
    monkeypatch.setattr(worker, "check", lambda bundle: None)  # a stub bundle: only the pool is under test
    bundle = {"v": VERSION, "league": {"managers": [{"id": "mgr_1"}]}}
    assert worker.render_all(bundle, Path("out")) == {"mgr_1": Path("out/mgr_1.mp4")}
    assert seen == ["spawn"]
