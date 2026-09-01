"""Composes the shell command that would send a ticket to its assigned
agent -- and, by default, only prints it.

This mirrors Livery's `dispatch prep` and inbox-marshal's `--scan`-before
`--apply` posture: Switchboard routes and recommends, a human decides
whether an agent with real side effects (Slack posts, Jira writes,
archiving email) actually runs. `--run` is opt-in, not the default, and
higher-risk agents get a visible warning either way.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Optional

from switchboard.models import AgentEntry, Ticket


def compose_command(agent: AgentEntry, ticket_path: Path) -> str:
    return agent.invoke.format(ticket_path=str(ticket_path))


def dispatch(
    agent: AgentEntry, ticket: Ticket, ticket_path: Path, run: bool = False
) -> Optional[subprocess.CompletedProcess]:
    command = compose_command(agent, ticket_path)

    if agent.risk_tier in ("medium", "high") and run:
        print(
            f"WARNING: {agent.id} is a {agent.risk_tier}-risk agent "
            f"(may perform write side effects). Running anyway because "
            f"--run was passed."
        )

    if not run:
        print(f"Prepared -- run this yourself:\n\n  {command}\n")
        return None

    return subprocess.run(shlex.split(command), check=False)
