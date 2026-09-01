"""Composes the shell command that would send a ticket to its assigned
agent -- and, by default, only prints it.

This mirrors Livery's `dispatch prep` and inbox-marshal's `--scan`-before
`--apply` posture: Switchboard routes and recommends, a human decides
whether an agent with real side effects (Slack posts, Jira writes,
archiving email, or -- for a claude_code agent -- editing files) actually
runs. `--run` is opt-in, not the default, and higher-risk agents get a
visible warning either way.

When `--run` is used, the dispatch is tracked as a DispatchAttempt from
the moment the process starts (PID recorded immediately, not after the
fact) through its exit status -- see attempts.py for why this exists.
Both runtimes (command, claude_code) go through the same attempt-tracking
path, so `switchboard attempts`/`show` don't need to know which kind of
agent ran.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Optional

from switchboard import attempts as attempts_mod
from switchboard import claude_runtime
from switchboard import notify as notify_mod
from switchboard.models import AgentEntry, DispatchAttempt, Ticket


def compose_command(agent: AgentEntry, ticket_path: Path) -> str:
    return agent.invoke.format(ticket_path=str(ticket_path))


def _build_claude_code_prompt(ticket: Ticket) -> str:
    return f"Ticket {ticket.id}: {ticket.title}\n\n{ticket.body}"


def dispatch(
    agent: AgentEntry, ticket: Ticket, ticket_path: Path, run: bool = False
) -> Optional[DispatchAttempt]:
    if agent.runtime == "claude_code":
        return _dispatch_claude_code(agent, ticket, run=run)
    return _dispatch_command(agent, ticket, ticket_path, run=run)


def _dispatch_command(
    agent: AgentEntry, ticket: Ticket, ticket_path: Path, run: bool
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
    if status == "failed":
        notify_mod.notify(
            f"Dispatch of ticket {ticket.id} to {agent.id} failed (exit {returncode}).",
            title="Switchboard: dispatch failed",
        )
    return attempt


def _dispatch_claude_code(
    agent: AgentEntry, ticket: Ticket, run: bool
) -> Optional[DispatchAttempt]:
    prompt = _build_claude_code_prompt(ticket)
    display_command = f'claude -p "<ticket {ticket.id} prompt>" (runtime=claude_code, cwd={agent.cwd or "."})'

    if agent.risk_tier in ("medium", "high") and run:
        print(
            f"WARNING: {agent.id} is a {agent.risk_tier}-risk agent running "
            f"as a live claude_code session (real tool access). Running "
            f"anyway because --run was passed."
        )

    if not run:
        print(f"Prepared -- run this yourself (or pass --run):\n\n  {display_command}\n")
        return None

    attempt_holder = {}

    def _on_pid(pid: int) -> None:
        attempt_holder["attempt"] = attempts_mod.record_attempt(
            ticket_id=ticket.id, agent_id=agent.id, command=display_command, pid=pid
        )
        print(f"Dispatched (attempt {attempt_holder['attempt'].id}, pid {pid}). Waiting for it to finish...")

    try:
        result = claude_runtime.run_session(
            prompt=prompt,
            cwd=agent.cwd,
            system_prompt=agent.description or None,
            allowed_tools=agent.allowed_tools or None,
            pid_callback=_on_pid,
        )
    except (RuntimeError, TimeoutError, ValueError) as e:
        status = "failed"
        attempt = attempt_holder.get("attempt")
        if attempt is None:
            # The session never even started (e.g. `claude` not on PATH) --
            # still record an attempt so `attempts`/`show` don't show a
            # silent gap where this dispatch should be.
            attempt = attempts_mod.record_attempt(
                ticket_id=ticket.id, agent_id=agent.id, command=display_command, pid=None
            )
        attempt = attempts_mod.update_attempt(attempt.id, status=status, returncode=None, result_text=str(e))
        print(f"Attempt {attempt.id}: failed -- {e}")
        notify_mod.notify(
            f"Dispatch of ticket {ticket.id} to {agent.id} failed: {e}",
            title="Switchboard: dispatch failed",
        )
        return attempt

    status = "failed" if result.is_error else "succeeded"
    attempt = attempts_mod.update_attempt(
        attempt_holder["attempt"].id,
        status=status,
        returncode=result.returncode,
        result_text=result.result_text,
        session_id=result.session_id,
        cost_usd=result.cost_usd,
    )
    print(f"Attempt {attempt.id}: {status}.\n\n{result.result_text}")
    if status == "failed":
        notify_mod.notify(
            f"Dispatch of ticket {ticket.id} to {agent.id} failed.",
            title="Switchboard: dispatch failed",
        )
    return attempt
