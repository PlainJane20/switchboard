"""Dispatch attempt records -- the biggest gap between this project and
Livery's actual execution robustness, closed here.

Livery's README: "Durable dispatch attempts under
.livery/dispatch/attempts/<attempt-id>.json, with status, PID, failures...
recorded per run." A bare `subprocess.run()` with no record of what
happened is a real weakness for anything you'd trust daily -- if a
dispatch dies, "was that supposed to still be running?" needs an answer
that isn't "scroll back in your terminal history."

Attempts are ephemeral runtime state, not durable history -- see
.gitignore. Unlike tickets, agents, and routing corrections, a PID from
three dispatches ago has no value once you know whether it succeeded.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from switchboard.models import AttemptStatus, DispatchAttempt

DEFAULT_ATTEMPTS_DIR = Path(".switchboard/attempts")


def _attempt_id(ticket_id: str, started_at: datetime) -> str:
    return f"{ticket_id}-{started_at.strftime('%Y%m%dT%H%M%S')}"


def record_attempt(
    ticket_id: str,
    agent_id: str,
    command: str,
    pid: Optional[int] = None,
    attempts_dir: Path = DEFAULT_ATTEMPTS_DIR,
) -> DispatchAttempt:
    attempts_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now()
    attempt = DispatchAttempt(
        id=_attempt_id(ticket_id, started_at),
        ticket_id=ticket_id,
        agent_id=agent_id,
        command=command,
        started_at=started_at,
        pid=pid,
        status="running",
    )
    _write(attempt, attempts_dir)
    return attempt


def update_attempt(
    attempt_id: str, attempts_dir: Path = DEFAULT_ATTEMPTS_DIR, **changes
) -> DispatchAttempt:
    path = attempts_dir / f"{attempt_id}.json"
    attempt = DispatchAttempt(**json.loads(path.read_text()))
    updated = attempt.model_copy(update=changes)
    _write(updated, attempts_dir)
    return updated


def _write(attempt: DispatchAttempt, attempts_dir: Path) -> None:
    path = attempts_dir / f"{attempt.id}.json"
    path.write_text(attempt.model_dump_json(indent=2))


def list_attempts(
    ticket_id: Optional[str] = None, attempts_dir: Path = DEFAULT_ATTEMPTS_DIR
) -> List[DispatchAttempt]:
    if not attempts_dir.exists():
        return []
    attempts = [
        DispatchAttempt(**json.loads(p.read_text()))
        for p in sorted(attempts_dir.glob("*.json"))
    ]
    if ticket_id:
        attempts = [a for a in attempts if a.ticket_id == ticket_id]
    return attempts
