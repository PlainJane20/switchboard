"""Routes a ticket to the best-fit registered agent.

Two tiers, on purpose -- the same cost/judgment split used across this
portfolio (see tpm-agent-os's model-tiering rationale): a free, instant,
fully deterministic tag-match router handles the common case, and an
LLM-assisted router is only invoked when that's genuinely ambiguous (no
agent scores above zero) or explicitly requested with --ai. Most tickets
never need to spend a model call just to be routed.
"""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

from switchboard.models import AgentEntry, RouteDecision, Ticket

MOCK_MODE = os.environ.get("SWITCHBOARD_MOCK") == "1"


def _score(ticket: Ticket, agent: AgentEntry) -> int:
    ticket_tags = {t.lower() for t in ticket.tags}
    agent_tags = {t.lower() for t in agent.tags}
    return len(ticket_tags & agent_tags)


def route_deterministic(
    ticket: Ticket, agents: List[AgentEntry]
) -> Tuple[Optional[AgentEntry], int]:
    """Tag-overlap scoring. Ties break alphabetically by agent id for
    reproducibility -- same ticket, same agents, same answer every time."""
    if not agents:
        return None, 0
    scored = sorted(agents, key=lambda a: (-_score(ticket, a), a.id))
    best = scored[0]
    best_score = _score(ticket, best)
    return (best, best_score) if best_score > 0 else (None, 0)


ROUTER_SYSTEM = """You are the routing function for Switchboard, a ticket \
dispatch system for a fleet of narrow, single-purpose AI agents. You will \
be given a ticket and the full agent registry (id, name, tags, \
description). Pick the single best-fit agent by id, or return null if \
none of them are actually a fit -- forcing a bad match is worse than \
leaving a ticket unrouted for a human to triage. Be honest about \
confidence: 'low' if you're guessing, 'high' only if the fit is obvious."""


def route_with_ai(
    ticket: Ticket, agents: List[AgentEntry], mock_fixture: Optional[RouteDecision] = None
) -> RouteDecision:
    if MOCK_MODE:
        if mock_fixture is None:
            raise RuntimeError("SWITCHBOARD_MOCK=1 but no fixture was supplied.")
        return mock_fixture

    import anthropic

    client = anthropic.Anthropic()
    registry_text = "\n".join(
        f"- id={a.id} name={a.name!r} tags={a.tags} :: {a.description[:200]}"
        for a in agents
    )
    ticket_text = (
        f"Title: {ticket.title}\nTags: {ticket.tags}\nBody:\n{ticket.body}"
    )
    response = client.messages.parse(
        model="claude-opus-5",
        max_tokens=1024,
        system=ROUTER_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": f"Ticket:\n{ticket_text}\n\nAgent registry:\n{registry_text}",
            }
        ],
        output_format=RouteDecision,
    )
    return response.parsed_output
