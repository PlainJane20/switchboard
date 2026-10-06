"""Tracing tests: in-memory exporter only, no collector, no network, no API keys.

Routing runs against the same offline fixtures as the rest of the suite
(SWITCHBOARD_JEV_MOCK files and a stubbed Claude router)."""

import builtins
import json
import subprocess
import sys

import pytest

pytest.importorskip("opentelemetry.sdk")

from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)

from switchboard import cli, router, tickets, tracing  # noqa: E402
from switchboard import dispatch as dispatch_mod  # noqa: E402
from switchboard import memory  # noqa: E402
from switchboard.models import AgentEntry, Correction, RouteDecision  # noqa: E402

# Reuse the board fixture and helpers from the main suite.
from test_switchboard import _args, _details, board  # noqa: E402,F401

SECRET_TITLE = "SECRET-TITLE-quarterly-layoffs"
SECRET_BODY = "SECRET-BODY-password=hunter2 sk-ant-api03-LEAK"
SECRET_REASON = "SECRET-REASON-because-ceo-said-so"


@pytest.fixture
def spans():
    exp = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exp))
    tracing.configure(provider)
    yield exp
    tracing.configure(None)


def by_name(exp, name):
    return [s for s in exp.get_finished_spans() if s.name == f"switchboard.{name}"]


def _jev(board, monkeypatch, choice, p):
    path = board / "jev.json"
    path.write_text(json.dumps({
        "choice": choice,
        "provider_details": _details({choice: p, "none": 1 - p}, p),
    }))
    monkeypatch.setenv("SWITCHBOARD_JEV_MOCK", str(path))


def _secret_ticket(tags):
    return tickets.new_ticket(title=SECRET_TITLE, tags=tags, body=SECRET_BODY)


def test_deterministic_route_span_and_child(board, spans):
    t = _secret_ticket(["alpha"])
    assert cli.cmd_route(_args(t.id, jev=False)) == 0
    [root] = by_name(spans, "route")
    [det] = by_name(spans, "route.deterministic")
    assert det.parent.span_id == root.context.span_id
    assert root.attributes["route.method"] == "deterministic"
    assert root.attributes["route.score"] == 1
    assert root.attributes["route.agent_id"] == "test-agent"
    assert root.attributes["route.outcome"] == "routed"
    assert root.attributes["ticket_id"] == t.id
    assert by_name(spans, "route.jev") == [] and by_name(spans, "route.claude") == []


def test_jev_decides_records_bucket_and_numeric_confidence(board, spans, monkeypatch):
    t = _secret_ticket(["zeta"])
    _jev(board, monkeypatch, "other-agent", 0.97)
    assert cli.cmd_route(_args(t.id)) == 0
    [root] = by_name(spans, "route")
    [jev] = by_name(spans, "route.jev")
    assert jev.parent.span_id == root.context.span_id
    assert root.attributes["route.method"] == "jev"
    assert root.attributes["route.confidence"] == "high"
    assert root.attributes["route.confidence_p"] == 0.97
    assert root.attributes["route.agent_id"] == "other-agent"
    assert "route.fallthrough_reason" not in root.attributes
    assert jev.attributes["outcome"] == "decided"


def test_jev_low_confidence_falls_through_to_claude(board, spans, monkeypatch):
    t = _secret_ticket(["zeta"])
    _jev(board, monkeypatch, "other-agent", 0.4)
    claude = RouteDecision(chosen_agent_id="test-agent", justification=SECRET_REASON, confidence="high")
    monkeypatch.setattr(router, "route_with_ai", lambda *a, **k: claude)
    assert cli.cmd_route(_args(t.id, ai=True)) == 0
    [root] = by_name(spans, "route")
    assert root.attributes["route.method"] == "ai"
    assert root.attributes["route.fallthrough_reason"] == "jev_low_confidence"
    assert root.attributes["route.confidence"] == "high"
    [claude_span] = by_name(spans, "route.claude")
    assert claude_span.parent.span_id == root.context.span_id
    # Jev span precedes the Claude span.
    [jev] = by_name(spans, "route.jev")
    assert jev.end_time <= claude_span.start_time


def test_jev_abstain_fallthrough_reason(board, spans, monkeypatch):
    t = _secret_ticket(["zeta"])
    _jev(board, monkeypatch, "none", 0.9)
    claude = RouteDecision(chosen_agent_id=None, justification="x", confidence="low")
    monkeypatch.setattr(router, "route_with_ai", lambda *a, **k: claude)
    assert cli.cmd_route(_args(t.id, ai=True)) == 1
    [root] = by_name(spans, "route")
    assert root.attributes["route.fallthrough_reason"] == "jev_abstained"
    assert root.attributes["route.outcome"] == "unrouted"
    assert root.attributes["route.exit_code"] == 1


def test_jev_unavailable_reason_and_no_model_requested(board, spans, monkeypatch):
    t = _secret_ticket(["zeta"])

    def boom(*a, **k):
        raise router.JevUnavailable(f"call failed for {SECRET_BODY}")

    monkeypatch.setattr(router, "route_with_jev", boom)
    assert cli.cmd_route(_args(t.id)) == 1
    [root] = by_name(spans, "route")
    assert root.attributes["route.fallthrough_reason"] == "jev_unavailable"
    assert root.attributes["route.method"] == "none"
    [jev] = by_name(spans, "route.jev")
    assert jev.attributes["outcome"] == "unavailable"


def test_no_model_requested(board, spans):
    t = _secret_ticket(["zeta"])
    assert cli.cmd_route(_args(t.id, jev=False)) == 1
    [root] = by_name(spans, "route")
    assert root.attributes["route.fallthrough_reason"] == "no_model_requested"


def test_correction_memory_load_records_count_only(board, spans):
    for i in range(3):
        memory.append_correction(Correction(
            ticket_id=f"{i:04d}", from_agent=None, to_agent="test-agent",
            reason=SECRET_REASON, corrected_at="2026-01-01",
        ))
    spans.clear()
    assert len(memory.load_corrections(limit=2)) == 2
    [m] = by_name(spans, "memory.load_corrections")
    assert m.attributes["count"] == 2 and m.attributes["limit"] == 2
    spans.clear()
    memory.load_corrections(path=board / "missing.jsonl")
    [m] = by_name(spans, "memory.load_corrections")
    assert m.attributes["count"] == 0


def _cmd_agent(command, risk="medium"):
    return AgentEntry(
        id="runner", name="Runner", repo="https://example.com/r",
        invoke=command + " {ticket_path}", tags=["x"], risk_tier=risk,
    )


@pytest.mark.parametrize("command,status,code", [("true", "succeeded", 0), ("false", "failed", 1)])
def test_dispatch_attempt_span(board, spans, monkeypatch, command, status, code):
    monkeypatch.setattr(dispatch_mod.notify_mod, "notify", lambda *a, **k: None)
    t = _secret_ticket(["x"])
    dispatch_mod.dispatch(_cmd_agent(command), t, board / "tickets" / "x.md", run=True)
    [d] = by_name(spans, "dispatch")
    assert d.attributes["runtime"] == "command"
    assert d.attributes["risk_tier"] == "medium"
    assert d.attributes["agent_id"] == "runner"
    assert d.attributes["dispatch.status"] == status
    assert d.attributes["dispatch.exit_code"] == code
    assert d.attributes["run"] is True
    assert command + " " not in " ".join(str(v) for v in d.attributes.values())


def test_dispatch_prepare_only_span(board, spans):
    t = _secret_ticket(["x"])
    assert dispatch_mod.dispatch(_cmd_agent("true", "low"), t, board / "x.md", run=False) is None
    [d] = by_name(spans, "dispatch")
    assert d.attributes["dispatch.prepared_only"] is True
    assert "dispatch.status" not in d.attributes


def test_error_recorded_type_only_no_message(spans):
    with pytest.raises(ValueError):
        with tracing.span("boom"):
            raise ValueError(f"message with {SECRET_BODY}")
    [s] = by_name(spans, "boom")
    assert s.status.status_code.name == "ERROR"
    assert s.attributes["error.type"] == "ValueError"
    assert SECRET_BODY not in repr(s.status) and not s.events


def test_no_sensitive_strings_in_any_span(board, spans, monkeypatch):
    monkeypatch.setattr(dispatch_mod.notify_mod, "notify", lambda *a, **k: None)
    memory.append_correction(Correction(
        ticket_id="0099", from_agent=None, to_agent="test-agent",
        reason=SECRET_REASON, corrected_at="2026-01-01",
    ))
    t = _secret_ticket(["zeta"])
    _jev(board, monkeypatch, "other-agent", 0.4)
    claude = RouteDecision(chosen_agent_id="test-agent", justification=SECRET_REASON, confidence="high")
    monkeypatch.setattr(router, "route_with_ai", lambda *a, **k: claude)
    cli.cmd_route(_args(t.id, ai=True))
    dispatch_mod.dispatch(_cmd_agent("false"), t, board / "x.md", run=True)
    finished = spans.get_finished_spans()
    assert len(finished) >= 6
    blob = json.dumps(
        [[s.name, dict(s.attributes), s.status.description,
          [(e.name, dict(e.attributes)) for e in s.events]] for s in finished],
        default=str,
    )
    for secret in (SECRET_TITLE, SECRET_BODY, SECRET_REASON, "hunter2", "sk-ant", "SECRET-"):
        assert secret not in blob


def test_long_values_are_truncated():
    exp = InMemorySpanExporter()
    p = TracerProvider()
    p.add_span_processor(SimpleSpanProcessor(exp))
    tracing.configure(p)
    try:
        with tracing.span("t", big="x" * 5000):
            pass
    finally:
        tracing.configure(None)
    assert len(exp.get_finished_spans()[0].attributes["big"]) == 120


def test_noop_by_default():
    tracing.configure(None)
    with tracing.span("x", a=1) as s:
        s.set_attribute("k", "v")
        assert not s.is_recording()


def test_noop_when_otel_api_missing(board, monkeypatch):
    """Simulate `import opentelemetry` failing: routing still works."""
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "opentelemetry" or name.startswith("opentelemetry."):
            raise ImportError("simulated: opentelemetry not installed")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with tracing.span("x", a=1) as s:
        s.set_attribute("k", "v")
    t = _secret_ticket(["alpha"])
    assert cli.cmd_route(_args(t.id, jev=False)) == 0
    assert tickets.load_ticket(t.id).assignee == "test-agent"


def test_import_switchboard_does_not_import_opentelemetry():
    code = (
        "import sys, switchboard.cli, switchboard.dispatch, switchboard.memory;"
        "from switchboard import tracing;"
        "assert not any(m.startswith('opentelemetry') for m in sys.modules), 'eager import'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_tracing_does_not_change_outputs(tmp_path, monkeypatch, capsys):
    """Same route, with and without a recording provider: identical ticket
    state and stdout."""
    from test_switchboard import AGENT_FIXTURE, SECOND_AGENT_FIXTURE

    def run(enabled):
        d = tmp_path / ("on" if enabled else "off")
        (d / "agents").mkdir(parents=True)
        (d / "tickets").mkdir()
        (d / "agents" / "a.md").write_text(AGENT_FIXTURE)
        (d / "agents" / "b.md").write_text(SECOND_AGENT_FIXTURE)
        monkeypatch.chdir(d)
        monkeypatch.setattr(cli.notify_mod, "notify", lambda *a, **k: None)
        (d / "j.json").write_text(json.dumps({
            "choice": "other-agent",
            "provider_details": _details({"other-agent": 0.9, "none": 0.1}, 0.9),
        }))
        monkeypatch.setenv("SWITCHBOARD_JEV_MOCK", str(d / "j.json"))
        if enabled:
            p = TracerProvider()
            p.add_span_processor(SimpleSpanProcessor(InMemorySpanExporter()))
            tracing.configure(p)
        else:
            tracing.configure(None)
        try:
            t = tickets.new_ticket(title="T", tags=["zeta"], body="B")
            capsys.readouterr()
            rc = cli.cmd_route(_args(t.id))
            out = capsys.readouterr().out
        finally:
            tracing.configure(None)
        loaded = tickets.load_ticket(t.id)
        return rc, out, loaded.assignee, loaded.status, loaded.routing.model_dump(mode="json")

    assert run(False) == run(True)
