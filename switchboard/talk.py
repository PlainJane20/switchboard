"""Talk: advisory Q&A with a registered agent's stated purpose, with no
ticket filed and no dispatch implied -- the same distinction Livery draws
between Talk (conversation) and Dispatch (task).

This deliberately does *not* invoke the agent's real `invoke` command --
that would actually run the agent's code for what's meant to be a quick
"would this even be the right agent for X" question. Talk answers from the
agent's registry description only.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from switchboard.models import AgentEntry

MOCK_MODE = os.environ.get("SWITCHBOARD_MOCK") == "1"
DEFAULT_TALK_DIR = Path("talk")

TALK_SYSTEM_TEMPLATE = """You are speaking on behalf of "{name}" ({id}), \
a registered specialist agent in a Switchboard fleet. Your job here is \
advisory only: answer questions about whether and how this agent would \
handle something, based on its stated purpose below. Do not claim to have \
taken any action -- you are not being dispatched a ticket, you are being \
asked for an opinion.

Agent description:
{description}

Agent tags: {tags}
Risk tier: {risk_tier}"""


def ask(agent: AgentEntry, question: str, mock_fixture: Optional[str] = None) -> str:
    if MOCK_MODE:
        if mock_fixture is None:
            raise RuntimeError("SWITCHBOARD_MOCK=1 but no fixture was supplied.")
        return mock_fixture

    import anthropic

    client = anthropic.Anthropic()
    system = TALK_SYSTEM_TEMPLATE.format(
        name=agent.name,
        id=agent.id,
        description=agent.description,
        tags=", ".join(agent.tags),
        risk_tier=agent.risk_tier,
    )
    response = client.messages.create(
        model="claude-opus-5",
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": question}],
    )
    return next(b.text for b in response.content if b.type == "text")


def append_transcript(
    agent_id: str, question: str, reply: str, talk_dir: Path = DEFAULT_TALK_DIR
) -> Path:
    talk_dir.mkdir(parents=True, exist_ok=True)
    path = talk_dir / f"{agent_id}.md"
    if not path.exists():
        path.write_text(f"# Talk transcript: {agent_id}\n\nAppend-only.\n")
    timestamp = datetime.now().isoformat(timespec="seconds")
    with path.open("a") as f:
        f.write(f"\n---\n**{timestamp}**\n\n**Q:** {question}\n\n**A:** {reply}\n")
    return path
