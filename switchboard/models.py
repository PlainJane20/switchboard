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


AgentRuntime = Literal["command", "claude_code"]


class AgentEntry(BaseModel):
    """One registered specialist agent.

    Two runtimes, not five: `command` (the default -- shells out to
    `invoke`, exactly like every agent registered before this field
    existed) and `claude_code` (spawns a real, live `claude -p` session
    with tool access, closer to what Livery's agents actually are). See
    ARCHITECTURE.md for why only one live runtime is implemented, verified
    against the real CLI, instead of five unverified ones."""

    id: str
    name: str
    repo: str
    invoke: str = Field(
        default="",
        description="Shell command template for runtime='command'. "
        "'{ticket_path}' is substituted with the ticket's markdown file "
        "at dispatch time. Ignored for runtime='claude_code'.",
    )
    runtime: AgentRuntime = "command"
    cwd: Optional[str] = Field(
        default=None,
        description="Working directory for a claude_code session -- where "
        "its Read/Edit/Bash tools actually operate. Unset means the "
        "directory Switchboard itself is run from.",
    )
    allowed_tools: List[str] = Field(
        default_factory=list,
        description="Tool allowlist for a claude_code session (e.g. "
        "['Read', 'Grep']). Empty means Claude Code's own defaults for "
        "the chosen permission_mode -- not 'no restrictions.'",
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
    result_text: Optional[str] = Field(
        default=None, description="claude_code runtime only -- the session's final response."
    )
    session_id: Optional[str] = None
    cost_usd: Optional[float] = None


class Schedule(BaseModel):
    """A declared recurring dispatch -- portable markdown, same as
    tickets and agents, until it's explicitly rendered into a real
    platform scheduler unit. Switchboard never installs or loads a
    schedule itself; see schedule.py for why.

    `cron` supports one deliberately narrow pattern: 'M H1,H2,H3 * * *'
    -- a fixed minute, one or more fixed hours, every day. That's the
    actual shape of every real recurring job in this portfolio
    (slack-daily-brief's own plist runs at 8am/1pm/6pm daily). A general
    cron parser is a much bigger, mostly-unused surface for a tool this
    scoped -- see ARCHITECTURE.md."""

    id: str
    description: str
    agent_id: str
    cron: str
    command: Optional[str] = Field(
        default=None,
        description="Overrides the agent's invoke command for scheduled "
        "runs, which aren't tied to one ticket_path. Defaults to the "
        "agent's invoke with no substitution if the template has no "
        "other placeholders.",
    )


class DebateTurn(BaseModel):
    """One turn in a Walkie-Talkie-style debate between two agent
    personas about how a ticket should be handled."""

    agent_id: str
    round: int
    text: str


class Ticket(BaseModel):
    id: str
    title: str
    status: TicketStatus = "open"
    created: date
    tags: List[str] = Field(default_factory=list)
    assignee: Optional[str] = None
    routing: Optional[RoutingRationale] = None
    body: str = ""
