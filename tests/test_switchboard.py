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

from switchboard import attempts as attempts_mod  # noqa: E402
from switchboard import memory, registry, router, talk, tickets  # noqa: E402
from switchboard.models import Correction, RouteDecision  # noqa: E402

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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
