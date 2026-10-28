"""Traces to Application Insights, only when APPLICATIONINSIGHTS_CONNECTION_STRING is set.

Unset (tests, a laptop by default), nothing here imports the exporter and nothing is configured: spans go to
OpenTelemetry's no-op provider, as before.

What is exported, and only that:
- our spans: `key_moment` (around `moment`, `manager_bid` and `host_decide`), `summary`, and the recap job's
  `recap_render`, carrying ids, never display names or model text; an error on one carries the exception's
  type, never its text (span() and mark_failed below)
- Agent Framework's spans (agent and chat calls with token counts, workflow steps), with sensitive data off,
  so no prompt or answer text
- FastAPI request spans for /api/ only, with no query string and nothing about the caller (address, port, user
  agent): /health is probed every 10 s, and the WebSocket lasts a whole match and carries the phone token in its
  URL. An unhandled exception in an /api/ handler keeps the instrumentation's exception event, text included:
  that text comes from our code, never from a model, and every expected failure there is an HTTPException,
  which records nothing

Not exported: logs (the Container Apps environment already sends them to the same workspace, so a second copy
would only double ingestion against its 1 GB daily cap), metrics, live metrics, performance counters,
statsbeat, SDK stats, the resource metric, and spans for outgoing HTTP, Azure SDK and other libraries' calls.

Nothing here may stop the app or the recap job: a failure to configure, instrument or flush is logged and the
process goes on without traces.
"""

import logging
import os
from collections.abc import Iterator, Mapping, MutableMapping, Sequence
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

SETTING = "APPLICATIONINSIGHTS_CONNECTION_STRING"
# Matched against the full request URL; any URL that is not under /api/ is excluded, ws:// and wss:// included.
API_ONLY = r"^(?!https?://[^/]+/api/)"
# Every library the distro would instrument on its own. FastAPI is instrumented below, on our app only.
LIBRARIES = ("azure_sdk", "django", "fastapi", "flask", "httpx", "httpx2", "psycopg2", "requests", "urllib", "urllib3")
# The distro reads these, not its keyword arguments, to turn logs and metrics off. The resource detectors would
# otherwise probe the VM metadata endpoint at startup. Statsbeat reports the exporter's own use to Microsoft;
# SDK stats sends the same kind of counts into our Application Insights, billed; the control plane fetches
# settings from Microsoft that could change what is sent; the resource metric is one more metric per export.
ENVIRONMENT = {
    "OTEL_LOGS_EXPORTER": "none",
    "OTEL_METRICS_EXPORTER": "none",
    "OTEL_EXPERIMENTAL_RESOURCE_DETECTORS": "otel",
    "APPLICATIONINSIGHTS_STATSBEAT_DISABLED_ALL": "true",
    "APPLICATIONINSIGHTS_SDKSTATS_DISABLED": "true",
    "APPLICATIONINSIGHTS_CONTROLPLANE_DISABLED": "true",
    "APPLICATIONINSIGHTS_OPENTELEMETRY_RESOURCE_METRIC_DISABLED": "true",
}
# Request span attributes that hold the URL: their query string is dropped (strip_query).
URL_ATTRIBUTES = ("http.url", "url.full", "http.target")
# Request span attributes about the caller, under the old and the new HTTP conventions: dropped (drop_client).
# The Azure exporter would send the address as the request's client IP and the agent as its user agent.
CLIENT_ATTRIBUTES = (
    "net.peer.ip", "net.peer.port", "net.peer.name", "net.sock.peer.addr", "net.sock.peer.port", "http.client_ip",
    "client.address", "client.port", "network.peer.address", "network.peer.port",
    "http.user_agent", "user_agent.original", "user_agent.synthetic.type",
)  # fmt: skip
# Request headers the instrumentation copies onto the span when OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_
# REQUEST names them; User-Agent and X-Forwarded-For among them would undo the above.
CAPTURED_HEADERS = "http.request.header."

log = logging.getLogger(__name__)
_configured: list[str] = []  # the service name, once configured


def credential() -> Any:
    """The managed identity named by AZURE_CLIENT_ID on Azure (the app and the recap job each have their own);
    the az login session on a laptop. Application Insights has local auth off, so a sender must sign in with
    Entra and hold Monitoring Metrics Publisher; infra/main.bicep grants it to the two workload identities."""
    client_id = os.environ.get("AZURE_CLIENT_ID")
    if client_id:
        from azure.identity import ManagedIdentityCredential

        return ManagedIdentityCredential(client_id=client_id)
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()


def configure(service: str, *, agents: bool, span_processors: Sequence[Any] = ()) -> bool:
    """Export traces when the setting is present. Call once at startup, before the FastAPI app is created.
    Returns whether traces are exported. span_processors: extra processors, for the tests and a local smoke."""
    if _configured:
        return True
    connection = os.environ.get(SETTING)
    if not connection:
        return False
    try:
        _configure(service, connection, agents, span_processors)
    except Exception as exc:  # no traces is better than no app
        log.warning("traces are not exported: %s", _why(exc))
        return False
    _configured.append(service)
    return True


def _configure(service: str, connection: str, agents: bool, span_processors: Sequence[Any]) -> None:
    os.environ.update(ENVIRONMENT)
    from azure.monitor.opentelemetry import configure_azure_monitor
    from opentelemetry.sdk.resources import Resource

    configure_azure_monitor(
        connection_string=connection,
        credential=credential(),
        resource=Resource.create({"service.name": service}),
        # Every trace: a fallback must never be sampled away. The distro's default keeps 5 traces a second.
        sampling_ratio=1.0,
        enable_live_metrics=False,
        enable_performance_counters=False,
        instrumentation_options={name: {"enabled": False} for name in LIBRARIES},
        span_processors=list(span_processors),
    )
    if agents:
        from agent_framework.observability import enable_instrumentation

        # Explicitly off: an ENABLE_SENSITIVE_DATA=true in the environment must not put prompts and answers
        # into Application Insights. Agent Framework's configure_otel_providers is never called: the provider
        # is the one configured above.
        enable_instrumentation(enable_sensitive_data=False)


def instrument(app: Any) -> None:
    """Request spans for the app's /api/ routes, without a span per WebSocket message. Only when configured."""
    if not _configured:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            app, excluded_urls=API_ONLY, exclude_spans=["receive", "send"], server_request_hook=request_hook
        )
    except Exception as exc:
        log.warning("requests are not traced: %s", _why(exc))


def request_hook(span: trace.Span, scope: Mapping[str, Any]) -> None:
    """Runs on each /api/ request span once the instrumentation has set its attributes. The caller goes first:
    the instrumentation swallows an error in this hook, so a failure in strip_query must not keep the caller."""
    drop_client(span, scope)
    strip_query(span, scope)


def strip_query(span: trace.Span, scope: Mapping[str, Any]) -> None:
    """A request span's URL without its query string: no /api/ route reads one, so anything there is noise
    or something a caller should not have put in a URL."""
    attributes: Mapping[str, Any] = getattr(span, "attributes", None) or {}
    for key in URL_ATTRIBUTES:
        value = attributes.get(key)
        if isinstance(value, str) and "?" in value:
            span.set_attribute(key, value.split("?", 1)[0])
    if attributes.get("url.query"):
        span.set_attribute("url.query", "")


def drop_client(span: trace.Span, scope: Mapping[str, Any]) -> None:
    """A request span without the caller's address, port or user agent: the app needs them only for its own
    limits, never in a trace. The OpenTelemetry API cannot remove an attribute, so this deletes from the SDK
    span's own map while the span is still open (tests/test_telemetry.py reads the exported spans). Should that
    map ever change shape, each value is blanked instead: the Azure exporter sends no blank address or agent."""
    attributes: Mapping[str, Any] = getattr(span, "attributes", None) or {}
    caller = [k for k in attributes if k in CLIENT_ATTRIBUTES or k.startswith(CAPTURED_HEADERS)]
    stored = getattr(span, "_attributes", None)
    for key in caller:
        if isinstance(stored, MutableMapping):
            del stored[key]
            continue
        span.set_attribute(key, "")


def flush(timeout_s: float = 10) -> None:
    """Send what is buffered, for a process about to exit (the recap job). Never raises."""
    if not _configured:
        return
    try:
        provider: Any = trace.get_tracer_provider()
        flushed = provider.force_flush(int(timeout_s * 1000))
    except Exception as exc:
        log.warning("traces were not flushed: %s", _why(exc))
        return
    if flushed is False:
        log.warning("traces were not flushed within %.0f s", timeout_s)


@contextmanager
def span(tracer: trace.Tracer, name: str, attributes: Mapping[str, Any]) -> Iterator[trace.Span]:
    """One of our spans as the current span. An exception that escapes it marks it failed with the exception's
    type only, where OpenTelemetry's default would record the text and the stack."""
    with tracer.start_as_current_span(
        name, attributes=dict(attributes), record_exception=False, set_status_on_exception=False
    ) as current:
        try:
            yield current
        except Exception as exc:
            mark_failed(current, exc)
            raise


def mark_failed(span: trace.Span, exc: BaseException) -> None:
    """An error on a span, with the exception's type and never its text: the text can quote model output
    (FailedChecks) or a remote error body. The log line carries the text, flattened and capped."""
    kind = type(exc).__name__
    span.add_event("exception", {"exception.type": kind})
    span.set_status(Status(StatusCode.ERROR, kind))


def _why(exc: Exception) -> str:
    flat = " ".join(str(exc).split())
    return f"{type(exc).__name__} {flat[:200]}"
