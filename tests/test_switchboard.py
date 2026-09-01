"""Offline tests -- no API key, no network. AI-router coverage runs under
SWITCHBOARD_MOCK=1 with a canned fixture, the same convention used across
this portfolio (see tpm-agent-os's TPM_AGENT_MOCK).

Every test gets its own tmp_path for agents/ and tickets/ so nothing here
touches the repo's real, committed example board.
"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["SWITCHBOARD_MOCK"] = "1"

from unittest.mock import MagicMock, patch  # noqa: E402

from switchboard import attempts as attempts_mod  # noqa: E402
from switchboard import claude_runtime  # noqa: E402
from switchboard import debate as debate_mod  # noqa: E402
from switchboard import dispatch as dispatch_mod  # noqa: E402
from switchboard import memory, registry, router  # noqa: E402
from switchboard import schedule as schedule_mod  # noqa: E402
from switchboard import talk, tickets  # noqa: E402
from switchboard.models import AgentEntry, Correction, RouteDecision, Schedule  # noqa: E402

AGENT_FIXTURE = """---
id: test-agent
name: Test Agent
repo: https://example.com/test-agent
invoke: "echo {ticket_path}"
tags: [alpha, beta]
risk_tier: low
---

A fixture agent for tests.
"""


@pytest.fixture
def workspace(tmp_path):
    agents_dir = tmp_path / "agents"
    tickets_dir = tmp_path / "tickets"
    agents_dir.mkdir()
    tickets_dir.mkdir()
    (agents_dir / "test-agent.md").write_text(AGENT_FIXTURE)
    return agents_dir, tickets_dir


def test_registry_loads_agent(workspace):
    agents_dir, _ = workspace
    agents = registry.load_agents(agents_dir)
    assert len(agents) == 1
    assert agents[0].id == "test-agent"
    assert agents[0].tags == ["alpha", "beta"]


def test_ticket_lifecycle(workspace):
    _, tickets_dir = workspace
    ticket = tickets.new_ticket(
        title="Do the thing", tags=["alpha"], body="Body text.", tickets_dir=tickets_dir
    )
    assert ticket.id == "0001"
    assert ticket.status == "open"

    loaded = tickets.load_ticket(ticket.id, tickets_dir)
    assert loaded.title == "Do the thing"
    assert loaded.body == "Body text."

    updated = tickets.update_ticket(ticket.id, tickets_dir, status="routed", assignee="test-agent")
    assert updated.status == "routed"
    assert updated.assignee == "test-agent"

    all_open = tickets.list_tickets(tickets_dir, status="open")
    assert all_open == []
    all_routed = tickets.list_tickets(tickets_dir, status="routed")
    assert len(all_routed) == 1


def test_ticket_ids_increment(workspace):
    _, tickets_dir = workspace
    t1 = tickets.new_ticket(title="First", tickets_dir=tickets_dir)
    t2 = tickets.new_ticket(title="Second", tickets_dir=tickets_dir)
    assert t1.id == "0001"
    assert t2.id == "0002"


def test_concurrent_ticket_creation_never_collides(workspace):
    """Ten threads filing tickets at once must still produce ten unique
    ids -- this is what the O_CREAT|O_EXCL lock in tickets.py exists to
    guarantee. Without it, two threads can both read the same 'next id'
    before either writes, and one ticket silently overwrites the other."""
    _, tickets_dir = workspace

    def _file_one(i):
        return tickets.new_ticket(title=f"Concurrent ticket {i}", tickets_dir=tickets_dir)

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(_file_one, range(10)))

    ids = [t.id for t in results]
    assert len(ids) == len(set(ids)), f"collision: {ids}"
    assert len(tickets.list_tickets(tickets_dir)) == 10


def test_deterministic_router_matches_on_tag_overlap(workspace):
    agents_dir, tickets_dir = workspace
    agents = registry.load_agents(agents_dir)
    ticket = tickets.new_ticket(title="Needs alpha work", tags=["alpha"], tickets_dir=tickets_dir)

    agent, score = router.route_deterministic(ticket, agents)
    assert agent.id == "test-agent"
    assert score == 1


def test_deterministic_router_refuses_to_force_a_bad_match(workspace):
    """No shared tags -- the router must return None, not the least-bad
    guess. Forcing a match here is the exact failure mode this system is
    designed to avoid."""
    agents_dir, tickets_dir = workspace
    agents = registry.load_agents(agents_dir)
    ticket = tickets.new_ticket(
        title="Completely unrelated work", tags=["zeta"], tickets_dir=tickets_dir
    )

    agent, score = router.route_deterministic(ticket, agents)
    assert agent is None
    assert score == 0


def test_ai_router_mock_mode_returns_fixture(workspace):
    agents_dir, tickets_dir = workspace
    agents = registry.load_agents(agents_dir)
    ticket = tickets.new_ticket(title="Ambiguous ticket", tickets_dir=tickets_dir)

    fixture = RouteDecision(
        chosen_agent_id="test-agent", justification="Only agent registered.", confidence="low"
    )
    decision = router.route_with_ai(ticket, agents, mock_fixture=fixture)
    assert decision.chosen_agent_id == "test-agent"
    assert decision.confidence == "low"


def test_ai_router_includes_corrections_in_prompt_formatting():
    """format_corrections is what actually threads memory into the
    prompt -- test the formatting directly since MOCK_MODE bypasses the
    real API call entirely."""
    correction = Correction(
        ticket_id="0099", from_agent=None, to_agent="test-agent",
        reason="It fit better.", corrected_at=date(2026, 1, 1),
    )
    text = router._format_corrections([correction])
    assert "0099" in text
    assert "test-agent" in text
    assert "It fit better." in text
    assert router._format_corrections([]) == "(none yet)"


def test_ledger_is_append_only(workspace, tmp_path):
    _, tickets_dir = workspace
    ledger_path = tmp_path / "ledger.md"
    ticket = tickets.new_ticket(title="Close me", tickets_dir=tickets_dir)

    tickets.append_ledger(ticket, "First close.", ledger_path=ledger_path)
    tickets.append_ledger(ticket, "Second close.", ledger_path=ledger_path)

    content = ledger_path.read_text()
    assert "First close." in content
    assert "Second close." in content
    assert content.index("First close.") < content.index("Second close.")


def test_correction_memory_round_trips(tmp_path):
    path = tmp_path / "routing_corrections.jsonl"
    c1 = Correction(ticket_id="0001", from_agent="a", to_agent="b", reason="r1", corrected_at=date(2026, 1, 1))
    c2 = Correction(ticket_id="0002", from_agent=None, to_agent="c", reason="r2", corrected_at=date(2026, 1, 2))

    memory.append_correction(c1, path=path)
    memory.append_correction(c2, path=path)

    loaded = memory.load_corrections(path=path)
    assert [c.ticket_id for c in loaded] == ["0001", "0002"]

    limited = memory.load_corrections(path=path, limit=1)
    assert [c.ticket_id for c in limited] == ["0002"]


def test_correction_memory_missing_file_returns_empty(tmp_path):
    assert memory.load_corrections(path=tmp_path / "nope.jsonl") == []


def test_dispatch_attempt_lifecycle(tmp_path):
    attempts_dir = tmp_path / "attempts"
    attempt = attempts_mod.record_attempt(
        ticket_id="0001", agent_id="test-agent", command="echo hi", pid=12345,
        attempts_dir=attempts_dir,
    )
    assert attempt.status == "running"
    assert attempt.pid == 12345

    updated = attempts_mod.update_attempt(
        attempt.id, attempts_dir=attempts_dir, status="succeeded", returncode=0
    )
    assert updated.status == "succeeded"
    assert updated.returncode == 0

    all_attempts = attempts_mod.list_attempts(attempts_dir=attempts_dir)
    assert len(all_attempts) == 1
    assert all_attempts[0].status == "succeeded"

    ticket_attempts = attempts_mod.list_attempts(ticket_id="0001", attempts_dir=attempts_dir)
    assert len(ticket_attempts) == 1
    no_attempts = attempts_mod.list_attempts(ticket_id="nonexistent", attempts_dir=attempts_dir)
    assert no_attempts == []


def test_list_attempts_on_missing_dir_returns_empty(tmp_path):
    assert attempts_mod.list_attempts(attempts_dir=tmp_path / "does-not-exist") == []


def test_talk_mock_mode_returns_fixture(workspace):
    agents_dir, _ = workspace
    agent = registry.load_agents(agents_dir)[0]
    reply = talk.ask(agent, "Would you handle a spec review?", mock_fixture="Not my job -- try spec-review-agent.")
    assert "spec-review-agent" in reply


def test_talk_transcript_is_append_only(tmp_path):
    talk_dir = tmp_path / "talk"
    talk.append_transcript("test-agent", "Q1?", "A1.", talk_dir=talk_dir)
    talk.append_transcript("test-agent", "Q2?", "A2.", talk_dir=talk_dir)

    content = (talk_dir / "test-agent.md").read_text()
    assert "Q1?" in content and "A1." in content
    assert "Q2?" in content and "A2." in content
    assert content.index("Q1?") < content.index("Q2?")


# --- schedule.py -----------------------------------------------------------

def test_parse_daily_cron_valid():
    minute, hours = schedule_mod.parse_daily_cron("30 8,13,18 * * *")
    assert minute == 30
    assert hours == [8, 13, 18]


def test_parse_daily_cron_rejects_unsupported_shapes():
    with pytest.raises(ValueError, match="unsupported cron"):
        schedule_mod.parse_daily_cron("*/15 * * * *")
    with pytest.raises(ValueError, match="unsupported cron"):
        schedule_mod.parse_daily_cron("0 9 * * MON")


def test_parse_daily_cron_rejects_out_of_range():
    with pytest.raises(ValueError, match="out of range"):
        schedule_mod.parse_daily_cron("0 25 * * *")


def test_schedule_round_trips_through_disk(tmp_path):
    schedules_dir = tmp_path / "schedules"
    sched = Schedule(
        id="daily-rollup", description="Morning exec rollup",
        agent_id="exec-status-rollup", cron="0 8 * * *",
    )
    schedule_mod.new_schedule(sched, schedules_dir=schedules_dir)

    loaded = schedule_mod.load_schedules(schedules_dir)
    assert len(loaded) == 1
    assert loaded[0].id == "daily-rollup"
    assert loaded[0].cron == "0 8 * * *"


def test_render_launchd_contains_expected_times():
    sched = Schedule(id="brief", description="Daily brief", agent_id="a", cron="0 8,13,18 * * *")
    plist = schedule_mod.render_launchd(sched, agent_invoke="bash run_daily_brief.sh")
    assert plist.count("<key>Hour</key><integer>8</integer>") == 1
    assert plist.count("<key>Hour</key><integer>13</integer>") == 1
    assert plist.count("<key>Hour</key><integer>18</integer>") == 1
    assert "run_daily_brief.sh" in plist


def test_render_systemd_contains_oncalendar_lines():
    sched = Schedule(id="brief", description="Daily brief", agent_id="a", cron="0 8,18 * * *")
    service, timer = schedule_mod.render_systemd(sched, agent_invoke="bash run_daily_brief.sh")
    assert "OnCalendar=*-*-* 08:00:00" in timer
    assert "OnCalendar=*-*-* 18:00:00" in timer
    assert "run_daily_brief.sh" in service


def test_render_strips_ticket_path_placeholder_for_scheduled_runs():
    sched = Schedule(id="s", description="d", agent_id="a", cron="0 8 * * *")
    service, _ = schedule_mod.render_systemd(sched, agent_invoke="python run_demo.py {ticket_path}")
    assert "{ticket_path}" not in service


# --- notify.py ---------------------------------------------------------------

def test_notify_never_raises_with_no_webhook_configured(monkeypatch):
    monkeypatch.delenv("SWITCHBOARD_WEBHOOK_URL", raising=False)
    from switchboard import notify

    notify.notify("test message")  # must not raise, on any platform


def test_notify_webhook_failure_is_swallowed(monkeypatch):
    monkeypatch.setenv("SWITCHBOARD_WEBHOOK_URL", "http://127.0.0.1:1/definitely-not-listening")
    from switchboard import notify

    notify.notify("test message")  # connection refused internally -- must not raise


# --- debate.py ---------------------------------------------------------------

def test_debate_alternates_agents_across_rounds(workspace):
    agents_dir, tickets_dir = workspace
    (agents_dir / "test-agent-b.md").write_text(
        AGENT_FIXTURE.replace("test-agent", "test-agent-b")
    )
    agents = {a.id: a for a in registry.load_agents(agents_dir)}
    ticket = tickets.new_ticket(title="Who handles this?", tickets_dir=tickets_dir)

    transcript = debate_mod.run_debate(
        ticket, agents["test-agent"], agents["test-agent-b"], rounds=2,
        mock_fixtures=["A-r1", "B-r1", "A-r2", "B-r2"],
    )

    assert [t.agent_id for t in transcript] == [
        "test-agent", "test-agent-b", "test-agent", "test-agent-b",
    ]
    assert [t.round for t in transcript] == [1, 1, 2, 2]
    assert [t.text for t in transcript] == ["A-r1", "B-r1", "A-r2", "B-r2"]


def test_debate_transcript_is_written(tmp_path):
    from switchboard.models import DebateTurn

    walkie_dir = tmp_path / "walkie-talkie"
    transcript = [
        DebateTurn(agent_id="a", round=1, text="I'll take it."),
        DebateTurn(agent_id="b", round=1, text="Disagree, here's why."),
    ]
    path = debate_mod.append_transcript("0001", transcript, walkie_dir=walkie_dir)

    content = path.read_text()
    assert "I'll take it." in content
    assert "Disagree, here's why." in content


# --- claude_runtime.py -------------------------------------------------------
# Fixtures below are real captured output from actual `claude -p` invocations
# during development (see ARCHITECTURE.md), not hand-authored guesses.

REAL_CAPTURED_JSON = (
    '{"is_error":false,"duration_api_ms":4124,"num_turns":1,"stop_reason":"end_turn",'
    '"session_id":"0cc73525-7969-45c1-868d-28a64836c35d","total_cost_usd":0.030188120000000002,'
    '"result":"hello world","type":"result"}'
)

REAL_WARNING_PREFIXED_OUTPUT = (
    "Warning: claude.ai MCP server blocked by enterprise policy: claude.ai Google Drive\n"
    + REAL_CAPTURED_JSON
)


def test_parse_result_json_on_real_captured_output():
    data = claude_runtime._parse_result_json(REAL_CAPTURED_JSON)
    assert data["result"] == "hello world"
    assert data["is_error"] is False
    assert data["session_id"] == "0cc73525-7969-45c1-868d-28a64836c35d"


def test_parse_result_json_falls_back_past_a_warning_line():
    """A real MCP-blocked warning was observed printing to the same stream
    ahead of the JSON during development -- the parser must not choke on it."""
    data = claude_runtime._parse_result_json(REAL_WARNING_PREFIXED_OUTPUT)
    assert data["result"] == "hello world"


def test_parse_result_json_raises_clearly_on_garbage():
    with pytest.raises(ValueError, match="could not parse"):
        claude_runtime._parse_result_json("not json at all")


def test_parse_result_json_raises_on_empty_output():
    with pytest.raises(ValueError, match="no stdout"):
        claude_runtime._parse_result_json("   ")


def test_run_session_raises_on_nonzero_exit():
    fake_process = MagicMock()
    fake_process.pid = 4242
    fake_process.communicate.return_value = ("", "some real error")
    fake_process.returncode = 1

    with patch("subprocess.Popen", return_value=fake_process):
        with pytest.raises(RuntimeError, match="exited 1"):
            claude_runtime.run_session(prompt="hi")


def test_run_session_invokes_pid_callback_before_communicate():
    fake_process = MagicMock()
    fake_process.pid = 9999
    fake_process.communicate.return_value = (REAL_CAPTURED_JSON, "")
    fake_process.returncode = 0

    seen_pids = []
    with patch("subprocess.Popen", return_value=fake_process):
        result = claude_runtime.run_session(prompt="hi", pid_callback=seen_pids.append)

    assert seen_pids == [9999]
    assert result.result_text == "hello world"
    assert result.session_id == "0cc73525-7969-45c1-868d-28a64836c35d"


def test_run_session_builds_expected_command_with_options():
    fake_process = MagicMock()
    fake_process.pid = 1
    fake_process.communicate.return_value = (REAL_CAPTURED_JSON, "")
    fake_process.returncode = 0

    with patch("subprocess.Popen", return_value=fake_process) as mock_popen:
        claude_runtime.run_session(
            prompt="do the thing", system_prompt="You are X.", allowed_tools=["Read", "Grep"]
        )

    args = mock_popen.call_args[0][0]
    assert args[:3] == ["claude", "-p", "do the thing"]
    assert "--append-system-prompt" in args and "You are X." in args
    assert "--allowedTools" in args and "Read,Grep" in args
    # never the full-bypass flag, regardless of options passed
    assert "--dangerously-skip-permissions" not in args


# --- dispatch.py (claude_code runtime) ---------------------------------------

CLAUDE_CODE_AGENT = AgentEntry(
    id="research-assistant", name="Research Assistant", repo="https://example.com/r",
    runtime="claude_code", cwd=None, allowed_tools=["Read"], tags=["research"], risk_tier="medium",
)


def test_dispatch_claude_code_prints_without_running(capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    ticket = tickets.new_ticket(title="Research this", tags=["research"])
    result = dispatch_mod.dispatch(CLAUDE_CODE_AGENT, ticket, ticket_path=None, run=False)
    assert result is None
    assert "Prepared" in capsys.readouterr().out


def test_dispatch_claude_code_records_successful_attempt(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    ticket = tickets.new_ticket(title="Research this", tags=["research"])

    fake_result = claude_runtime.ClaudeCodeResult(
        is_error=False, result_text="The answer.", session_id="sess-1",
        cost_usd=0.01, returncode=0, pid=555,
    )

    def fake_run_session(*, prompt, cwd, system_prompt, allowed_tools, pid_callback=None, **kw):
        if pid_callback:
            pid_callback(555)
        return fake_result

    with patch.object(claude_runtime, "run_session", side_effect=fake_run_session):
        attempt = dispatch_mod.dispatch(CLAUDE_CODE_AGENT, ticket, ticket_path=Path("x"), run=True)

    assert attempt.status == "succeeded"
    assert attempt.result_text == "The answer."
    assert attempt.session_id == "sess-1"
    assert attempt.cost_usd == 0.01
    assert attempt.pid == 555


def test_dispatch_claude_code_records_failed_attempt_when_session_raises(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    ticket = tickets.new_ticket(title="Research this", tags=["research"])

    with patch.object(claude_runtime, "run_session", side_effect=RuntimeError("claude -p exited 1")):
        attempt = dispatch_mod.dispatch(CLAUDE_CODE_AGENT, ticket, ticket_path=Path("x"), run=True)

    assert attempt.status == "failed"
    assert "exited 1" in attempt.result_text


# --- schedule.py install/uninstall -------------------------------------------

INSTALL_SCHEDULE = Schedule(
    id="test-sched", description="Test schedule", agent_id="a", cron="0 8 * * *"
)


def test_install_launchd_dry_run_writes_nothing(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    result = schedule_mod.install_launchd(INSTALL_SCHEDULE, "python run.py", apply=False, target_dir=target_dir)
    assert result["applied"] is False
    assert not target_dir.exists()  # dry run creates nothing, not even the dir


def test_install_launchd_apply_writes_file_and_calls_launchctl(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        result = schedule_mod.install_launchd(INSTALL_SCHEDULE, "python run.py", apply=True, target_dir=target_dir)

    assert result["applied"] is True
    assert result["target"].exists()
    assert "com.switchboard.test-sched" in result["target"].read_text()
    mock_run.assert_called_once()
    assert mock_run.call_args[0][0][:2] == ["launchctl", "load"]


def test_uninstall_launchd_apply_removes_file(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    plist_path = target_dir / "com.switchboard.test-sched.plist"
    plist_path.write_text("<plist/>")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        result = schedule_mod.uninstall_launchd("test-sched", apply=True, target_dir=target_dir)

    assert result["applied"] is True
    assert result["existed"] is True
    assert not plist_path.exists()


def test_uninstall_launchd_dry_run_reports_existence_without_removing(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    plist_path = target_dir / "com.switchboard.test-sched.plist"
    plist_path.write_text("<plist/>")

    result = schedule_mod.uninstall_launchd("test-sched", apply=False, target_dir=target_dir)

    assert result["applied"] is False
    assert result["existed"] is True
    assert plist_path.exists()  # dry run never removes


def test_install_systemd_apply_writes_both_units(tmp_path):
    target_dir = tmp_path / "systemd-user"
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        result = schedule_mod.install_systemd(INSTALL_SCHEDULE, "python run.py", apply=True, target_dir=target_dir)

    assert result["service_path"].exists()
    assert result["timer_path"].exists()
    assert mock_run.call_args[0][0][:3] == ["systemctl", "--user", "enable"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
