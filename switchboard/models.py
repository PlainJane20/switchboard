"""Typed contracts for everything Switchboard reads and writes.

Both AgentEntry and Ticket are backed by plain markdown-with-frontmatter
files on disk (see registry.py / tickets.py) -- these models are the
in-memory shape, not a database schema. Git is the database.
"""

from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

TicketStatus = Literal["open", "routed", "in_progress", "done", "cancelled"]
RiskTier = Literal["low", "medium", "high"]


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


class Ticket(BaseModel):
    id: str
    title: str
    status: TicketStatus = "open"
    created: date
    tags: List[str] = Field(default_factory=list)
    assignee: Optional[str] = None
    body: str = ""
