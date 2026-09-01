"""Composes the shell command that would send a ticket to its assigned
agent -- and, by default, only prints it.

This mirrors Livery's `dispatch prep` and inbox-marshal's `--scan`-before
`--apply` posture: Switchboard routes and recommends, a human decides
whether an agent with real side effects (Slack posts, Jira writes,
archiving email) actually runs. `--run` is opt-in, not the default, and
higher-risk agents get a visible warning either way.

When `--run` is used, the dispatch is tracked as a DispatchAttempt from
the moment the process starts (PID recorded immediately, not after the
fact) through its exit status -- see attempts.py for why this exists.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Optional

from switchboard import attempts as attempts_mod
from switchboard.models import AgentEntry, DispatchAttempt, Ticket


def compose_command(agent: AgentEntry, ticket_path: Path) -> str:
    return agent.invoke.format(ticket_path=str(ticket_path))


def dispatch(
    agent: AgentEntry, ticket: Ticket, ticket_path: Path, run: bool = False
) -> Optional[DispatchAttempt]:
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

    process = subprocess.Popen(shlex.split(command))
    attempt = attempts_mod.record_attempt(
        ticket_id=ticket.id, agent_id=agent.id, command=command, pid=process.pid
    )
    print(f"Dispatched (attempt {attempt.id}, pid {process.pid}). Waiting for it to finish...")

    returncode = process.wait()
    status = "succeeded" if returncode == 0 else "failed"
    attempt = attempts_mod.update_attempt(attempt.id, status=status, returncode=returncode)
    print(f"Attempt {attempt.id}: {status} (exit code {returncode}).")
    return attempt
