"""Walkie-Talkie-style structured debate between two agent personas about
how a ticket should be handled.

Livery's Walkie-Talkie runs two *real* hired agents against each other,
each in their own actual runtime. Switchboard's agents are external repos
with one-shot CLIs, not conversational sessions -- there's no live
adapter to have `critical-path-radar` actually argue with
`incident-postmortem-agent`. This is the honest adaptation: both sides
are Claude, speaking *as* each agent's registered persona (the same
mechanism Talk uses), arguing from what's actually declared about them --
not a simulation dressed up as more than it is.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from switchboard.models import AgentEntry, DebateTurn, Ticket

MOCK_MODE = os.environ.get("SWITCHBOARD_MOCK") == "1"
DEFAULT_WALKIE_DIR = Path("walkie-talkie")

DEBATE_SYSTEM_TEMPLATE = """You are "{name}" ({id}) in a structured debate \
with another registered agent about how to handle a ticket. Argue from \
your own stated purpose below -- claim the ticket if it's genuinely your \
job, push back and say so plainly if it isn't, or propose a specific \
division of labor if it's a mix. Keep each turn to a few sentences; this \
is a debate, not a report.

Your description: {description}
Your tags: {tags}"""


def _build_system(agent: AgentEntry) -> str:
    return DEBATE_SYSTEM_TEMPLATE.format(
        name=agent.name, id=agent.id, description=agent.description, tags=", ".join(agent.tags)
    )


def _take_turn(
    agent: AgentEntry, ticket: Ticket, transcript: List[DebateTurn], mock_fixture: Optional[str] = None
) -> str:
    if MOCK_MODE:
        if mock_fixture is None:
            raise RuntimeError("SWITCHBOARD_MOCK=1 but no fixture was supplied.")
        return mock_fixture

    import anthropic

    client = anthropic.Anthropic()
    history = "\n\n".join(f"[{t.agent_id}]: {t.text}" for t in transcript) or "(debate just started)"
    response = client.messages.create(
        model="claude-opus-5",
        max_tokens=512,
        system=_build_system(agent),
        messages=[
            {
                "role": "user",
                "content": (
                    f"Ticket: {ticket.title}\n{ticket.body}\n\n"
                    f"Debate so far:\n{history}\n\n"
                    f"Your turn."
                ),
            }
        ],
    )
    return next(b.text for b in response.content if b.type == "text")


def run_debate(
    ticket: Ticket,
    agent_a: AgentEntry,
    agent_b: AgentEntry,
    rounds: int = 2,
    mock_fixtures: Optional[List[str]] = None,
) -> List[DebateTurn]:
    transcript: List[DebateTurn] = []
    fixtures = iter(mock_fixtures or [])
    for round_num in range(1, rounds + 1):
        for agent in (agent_a, agent_b):
            fixture = next(fixtures, None) if MOCK_MODE else None
            text = _take_turn(agent, ticket, transcript, mock_fixture=fixture)
            transcript.append(DebateTurn(agent_id=agent.id, round=round_num, text=text))
    return transcript


def append_transcript(
    ticket_id: str, transcript: List[DebateTurn], walkie_dir: Path = DEFAULT_WALKIE_DIR
) -> Path:
    walkie_dir.mkdir(parents=True, exist_ok=True)
    path = walkie_dir / f"{ticket_id}.md"
    lines = [f"# Walkie-Talkie: ticket {ticket_id}\n"]
    for turn in transcript:
        lines.append(f"\n**Round {turn.round} -- {turn.agent_id}:**\n\n{turn.text}\n")
    path.write_text("\n".join(lines))
    return path
