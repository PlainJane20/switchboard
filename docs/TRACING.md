# Tracing (optional)

Switchboard can emit [OpenTelemetry](https://opentelemetry.io/) spans for routing,
dispatch and correction-memory loads. It is **off by default and changes no
behaviour**: with `opentelemetry-api` absent, or present but with no SDK
configured, every span is a no-op. The API is imported lazily, so the base
install does not need it.

## What is traced

```
switchboard.route                      one per `switchboard route`
  switchboard.route.deterministic      tag-overlap match
  switchboard.memory.load_corrections  (only when a model tier runs; sibling of the tier span)
  switchboard.route.jev                Jev typed choice, if --jev / SWITCHBOARD_BACKEND=jev
  switchboard.route.claude             Claude fallback, if --ai and earlier tiers fell through
switchboard.dispatch                   one per `switchboard dispatch`
```

| Span | Attribute | Values |
|---|---|---|
| `route` | `ticket_id` | ticket id (e.g. `0004`) |
| `route` | `route.agent_count` | registered agents |
| `route` | `route.method` | `deterministic`, `jev`, `ai`, `none` (which tier decided) |
| `route` | `route.confidence` | `high`, `medium`, `low` (bucket; absent for deterministic) |
| `route` | `route.confidence_p` | Jev's numeric confidence, 0 to 1 (absent otherwise) |
| `route` | `route.score` | tag-overlap score (deterministic only) |
| `route` | `route.agent_id` | chosen agent id, if any |
| `route` | `route.outcome` | `routed`, `suggested`, `unrouted`, `unknown_agent` |
| `route` | `route.fallthrough_reason` | `jev_unavailable`, `jev_unknown_agent`, `jev_abstained`, `jev_low_confidence`, `no_model_requested` |
| `route` | `route.exit_code` | CLI exit code |
| `route.deterministic` | `matched`, `score` | bool, int |
| `route.jev` / `route.claude` | `agents`, `outcome`, `confidence`, `confidence_p` | counts, `decided`/`abstained`/`unavailable`, bucket, number |
| `dispatch` | `agent_id`, `ticket_id`, `runtime`, `risk_tier`, `run` | ids, `command`/`claude_code`, `low`/`medium`/`high`, bool |
| `dispatch` | `dispatch.prepared_only` | true when `--run` was not passed |
| `dispatch` | `dispatch.attempt_id`, `dispatch.status`, `dispatch.exit_code` | attempt id, `succeeded`/`failed`, int (null for claude_code sessions) |
| `memory.load_corrections` | `count`, `limit` | corrections returned, requested limit |

A failing span is marked `ERROR` with `error.type` set to the exception class name.

## What is deliberately NOT recorded

- Ticket titles, bodies and tags
- Prompts, model responses and routing justifications
- The dispatch command line, `claude -p` prompt, session output or `result_text`
- Correction text (`reason`), only the count of corrections loaded
- Exception messages (only the type name; they can echo user content)
- Secrets, API keys, environment variables

String attributes are also truncated to 120 characters. A test
(`tests/test_tracing.py::test_no_sensitive_strings_in_any_span`) routes and
dispatches a ticket seeded with sensitive sample strings and asserts none appear in
any span name, attribute, status or event.

## Enabling

Install the API (and an SDK/exporter, which you choose):

```bash
pip install 'switchboard-router[tracing]'     # opentelemetry-api only
pip install opentelemetry-sdk                 # to actually record spans
```

Switchboard only uses the global tracer provider, so set one before calling it.

Console exporter (quick look):

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
trace.set_tracer_provider(provider)

from switchboard import cli
cli.main(["route", "0004", "--jev"])
```

OTLP (needs `pip install opentelemetry-exporter-otlp`):

```python
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint="http://localhost:4318/v1/traces")))
```

The `switchboard` console script does not configure a provider itself. With the
standard OpenTelemetry auto-instrumentation wrapper
(`opentelemetry-instrument switchboard route 0004`) and the usual
`OTEL_EXPORTER_OTLP_*` environment variables, spans are exported without code changes.
(Not verified, see below.)

## Limits, honestly

- Verified only with an in-memory exporter in mock/offline mode (`SWITCHBOARD_MOCK`,
  `SWITCHBOARD_JEV_MOCK`, stubbed Claude router, real `true`/`false` subprocesses for
  dispatch). Not tested against a real collector, a real OTLP endpoint, or
  `opentelemetry-instrument`.
- The live Jev and Claude calls are timed by span duration but not exercised here.
- `claude_code` dispatches are traced as one span; internal session steps are not.
- Spans are per-process; there is no context propagation to dispatched agents.
