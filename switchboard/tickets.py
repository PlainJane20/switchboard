"""Create, list, update, and close tickets -- each one a markdown file
under tickets/. Git *is* the ticket history; there's no separate log to
keep in sync with the files."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import List, Optional

from switchboard import frontmatter
from switchboard.models import Ticket

DEFAULT_TICKETS_DIR = Path("tickets")
DEFAULT_LEDGER_PATH = Path("ledger.md")

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(title: str) -> str:
    return _SLUG_RE.sub("-", title.lower()).strip("-")[:40]


def _next_id(tickets_dir: Path) -> str:
    existing = sorted(tickets_dir.glob("[0-9][0-9][0-9][0-9]-*.md"))
    if not existing:
        return "0001"
    last_num = int(existing[-1].name[:4])
    return f"{last_num + 1:04d}"


def path_for(tickets_dir: Path, ticket_id: str) -> Path:
    matches = list(tickets_dir.glob(f"{ticket_id}-*.md"))
    if not matches:
        raise FileNotFoundError(f"no ticket found with id {ticket_id!r}")
    return matches[0]


def new_ticket(
    title: str,
    tags: Optional[List[str]] = None,
    body: str = "",
    tickets_dir: Path = DEFAULT_TICKETS_DIR,
) -> Ticket:
    tickets_dir.mkdir(parents=True, exist_ok=True)
    ticket_id = _next_id(tickets_dir)
    ticket = Ticket(
        id=ticket_id,
        title=title,
        status="open",
        created=date.today(),
        tags=tags or [],
        assignee=None,
        body=body,
    )
    _write(ticket, tickets_dir)
    return ticket


def _write(ticket: Ticket, tickets_dir: Path) -> None:
    meta = ticket.model_dump(exclude={"body"}, mode="json")
    path = tickets_dir / f"{ticket.id}-{_slugify(ticket.title)}.md"
    path.write_text(frontmatter.render(meta, ticket.body))


def load_ticket(ticket_id: str, tickets_dir: Path = DEFAULT_TICKETS_DIR) -> Ticket:
    path = path_for(tickets_dir, ticket_id)
    meta, body = frontmatter.parse(path.read_text())
    return Ticket(body=body, **meta)


def list_tickets(
    tickets_dir: Path = DEFAULT_TICKETS_DIR, status: Optional[str] = None
) -> List[Ticket]:
    tickets = []
    for path in sorted(tickets_dir.glob("*.md")):
        meta, body = frontmatter.parse(path.read_text())
        ticket = Ticket(body=body, **meta)
        if status is None or ticket.status == status:
            tickets.append(ticket)
    return tickets


def update_ticket(
    ticket_id: str,
    tickets_dir: Path = DEFAULT_TICKETS_DIR,
    **changes,
) -> Ticket:
    ticket = load_ticket(ticket_id, tickets_dir)
    updated = ticket.model_copy(update=changes)
    # Renaming isn't needed -- id and title (hence filename) don't change here.
    old_path = path_for(tickets_dir, ticket_id)
    meta = updated.model_dump(exclude={"body"}, mode="json")
    old_path.write_text(frontmatter.render(meta, updated.body))
    return updated


def append_ledger(
    ticket: Ticket, summary: str, ledger_path: Path = DEFAULT_LEDGER_PATH
) -> None:
    """Append-only closed-ticket record -- the audit trail. Never rewritten,
    only ever appended to, the same convention agent-control-tower uses for
    its audit log."""
    line = (
        f"- **{ticket.id}** *{ticket.title}* -- assigned to "
        f"`{ticket.assignee or 'unassigned'}` -- {summary}\n"
    )
    if not ledger_path.exists():
        ledger_path.write_text("# Ledger\n\nAppend-only record of closed tickets.\n\n")
    with ledger_path.open("a") as f:
        f.write(line)
