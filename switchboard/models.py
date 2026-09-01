"""Typed contracts for everything Switchboard reads and writes.

Both AgentEntry and Ticket are backed by plain markdown-with-frontmatter
files on disk (see registry.py / tickets.py) -- these models are the
in-memory shape, not a database schema. Git is the database.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

TicketStatus = Literal["open", "routed", "in_progress", "done", "cancelled"]
RiskTier = Literal["low", "medium", "high"]
RoutingMethod = Literal["deterministic", "ai", "manual"]
AttemptStatus = Literal["running", "succeeded", "failed"]


class AgentEntry(BaseModel):
    """One registered specialist agent -- a real repo in the portfolio,
    described declaratively so Switchboard never needs to know what's
    inside it."""

    id: str
    name: str
    repo: str
    invoke: str = Field(
        description="Shell command template used to dispatch a ticket to "
        "this agent. '{ticket_path}' is substituted with the ticket's "
        "markdown file at dispatch time."
    )
    tags: List[str] = Field(default_factory=list)
    risk_tier: RiskTier = "low"
    description: str = ""


class RouteDecision(BaseModel):
    """Output of the AI-assisted router (used only when the deterministic
    tag-match router can't confidently pick an agent)."""

    chosen_agent_id: Optional[str] = Field(
        description="The id of the best-fit registered agent, or null if "
        "none of them are actually a fit -- do not force a match."
    )
    justification: str
    confidence: Literal["low", "medium", "high"]


class RoutingRationale(BaseModel):
    """Why a ticket ended up with the assignee it has -- persisted onto the
    ticket itself, not just printed to a terminal that scrolls away.

    A 'low' confidence AI decision is recorded here as a *suggestion* even
    though the ticket's assignee stays unset -- see router.py and
    cli.py::cmd_route. That distinction (suggested vs. committed) is the
    whole point of tracking confidence at all.
    """

    method: RoutingMethod
    matched_tags: List[str] = Field(default_factory=list)
    score: Optional[int] = None
    justification: Optional[str] = None
    confidence: Optional[Literal["low", "medium", "high"]] = None
    decided_at: date


class Correction(BaseModel):
    """A human overriding a routing decision -- the raw material the AI
    router's prompt gets enriched with over time (see memory.py). This is
    the one thing Switchboard's router does that Livery's manual
    `assignee` field has no equivalent of: a growing, git-tracked record
    of what humans actually corrected, and why."""

    ticket_id: str
    from_agent: Optional[str]
    to_agent: str
    reason: str
    corrected_at: date


class DispatchAttempt(BaseModel):
    """One real execution of an agent's invoke command. Ephemeral runtime
    state (see .gitignore) -- unlike tickets, agents, and corrections,
    this isn't meant to be permanent history, just enough to answer 'did
    the last dispatch of this ticket actually succeed.'"""

    id: str
    ticket_id: str
    agent_id: str
    command: str
    started_at: datetime
    pid: Optional[int] = None
    status: AttemptStatus = "running"
    returncode: Optional[int] = None


class Ticket(BaseModel):
    id: str
    title: str
    status: TicketStatus = "open"
    created: date
    tags: List[str] = Field(default_factory=list)
    assignee: Optional[str] = None
    routing: Optional[RoutingRationale] = None
    body: str = ""
