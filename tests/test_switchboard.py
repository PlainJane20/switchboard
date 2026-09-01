"""Offline tests -- no API key, no network. AI-router coverage runs under
SWITCHBOARD_MOCK=1 with a canned fixture, the same convention used across
this portfolio (see tpm-agent-os's TPM_AGENT_MOCK).

Every test gets its own tmp_path for agents/ and tickets/ so nothing here
touches the repo's real, committed example board.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["SWITCHBOARD_MOCK"] = "1"

from switchboard import registry, router, tickets  # noqa: E402
from switchboard.models import RouteDecision  # noqa: E402

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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
