"""Routes a ticket to the best-fit registered agent.

Tiers, on purpose -- the same cost/judgment split used across this
portfolio (see tpm-agent-os's model-tiering rationale): a free, instant,
fully deterministic tag-match router handles the common case, and an
LLM-assisted router is only invoked when the tag-match finds nothing (no
agent scores above zero) and --ai (or --jev) was passed. Most tickets
never need to spend a model call just to be routed.
"""

from __future__ import annotations

import enum
import json
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from switchboard.models import (
    AgentEntry,
    Correction,
    RouteDecision,
    Ticket,
    confidence_bucket,
)

MOCK_MODE = os.environ.get("SWITCHBOARD_MOCK") == "1"


def _score(ticket: Ticket, agent: AgentEntry) -> int:
    ticket_tags = {t.lower() for t in ticket.tags}
    agent_tags = {t.lower() for t in agent.tags}
    return len(ticket_tags & agent_tags)


def route_deterministic(
    ticket: Ticket, agents: List[AgentEntry]
) -> Tuple[Optional[AgentEntry], int]:
    """Tag-overlap scoring. Ties break alphabetically by agent id for
    reproducibility -- same ticket, same agents, same answer every time."""
    if not agents:
        return None, 0
    scored = sorted(agents, key=lambda a: (-_score(ticket, a), a.id))
    best = scored[0]
    best_score = _score(ticket, best)
    return (best, best_score) if best_score > 0 else (None, 0)


ROUTER_SYSTEM = """You are the routing function for Switchboard, a ticket \
dispatch system for a fleet of narrow, single-purpose AI agents. You will \
be given a ticket, the full agent registry (id, name, tags, description), \
and -- when available -- recent examples of humans correcting past \
routing decisions. Pick the single best-fit agent by id, or return null \
if none of them are actually a fit -- forcing a bad match is worse than \
leaving a ticket unrouted for a human to triage. Be honest about \
confidence: 'low' if you're guessing, 'high' only if the fit is obvious. \
A 'low' confidence decision will be recorded as a suggestion only, not an \
assignment -- so there's no reason to inflate it."""


def _format_corrections(corrections: List[Correction]) -> str:
    if not corrections:
        return "(none yet)"
    return "\n".join(
        f"- ticket {c.ticket_id}: corrected"
        f"{f' from {c.from_agent}' if c.from_agent else ''} to {c.to_agent} "
        f"-- {c.reason}"
        for c in corrections
    )


def route_with_ai(
    ticket: Ticket,
    agents: List[AgentEntry],
    corrections: Optional[List[Correction]] = None,
    mock_fixture: Optional[RouteDecision] = None,
) -> RouteDecision:
    if MOCK_MODE:
        if mock_fixture is None:
            raise RuntimeError("SWITCHBOARD_MOCK=1 but no fixture was supplied.")
        return mock_fixture

    import anthropic

    client = anthropic.Anthropic()
    registry_text = "\n".join(
        f"- id={a.id} name={a.name!r} tags={a.tags} :: {a.description[:200]}"
        for a in agents
    )
    ticket_text = (
        f"Title: {ticket.title}\nTags: {ticket.tags}\nBody:\n{ticket.body}"
    )
    corrections_text = _format_corrections(corrections or [])
    response = client.messages.parse(
        model="claude-opus-5",
        max_tokens=1024,
        system=ROUTER_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Ticket:\n{ticket_text}\n\n"
                    f"Agent registry:\n{registry_text}\n\n"
                    f"Recent human corrections to past routing decisions "
                    f"(weight these -- they're ground truth):\n{corrections_text}"
                ),
            }
        ],
        output_format=RouteDecision,
    )
    return response.parsed_output


# ---------------------------------------------------------------------------
# Jev backend -- sits between the deterministic router and the Claude fallback.
# ---------------------------------------------------------------------------

# Jev's typed-choice output is capped at 255 options; one slot is the
# abstain option, so more than 250 registered agents means we skip Jev.
JEV_MAX_AGENTS = 250
JEV_MODEL = "jev-latest"
JEV_ABSTAIN = "none"

# (output_enum, instructions, prompt) -> (chosen enum value, provider_details)
JevClient = Callable[[Any, str, str], Tuple[str, Dict[str, Any]]]


class JevUnavailable(RuntimeError):
    """Jev can't be used for this call (SDK/key missing, too many agents,
    or the call failed). cmd_route treats it as 'fall through'."""


def _abstain_label(agents: List[AgentEntry]) -> str:
    taken = {a.id for a in agents}
    label = JEV_ABSTAIN
    while label in taken:
        label = "_" + label
    return label


def build_jev_enum(agents: List[AgentEntry]):
    """Dynamic output Enum: one member per registered agent id plus an
    abstain option so Jev can say 'none of these'."""
    members = {a.id: a.id for a in agents}
    members[_abstain_label(agents)] = _abstain_label(agents)
    return enum.Enum("JevAgentChoice", members)


def build_jev_instructions(
    agents: List[AgentEntry], corrections: Optional[List[Correction]] = None
) -> str:
    """Jev needs the decision criteria spelled out -- with a vague prompt it
    guesses with near-uniform probabilities -- so every agent's id, tags and
    description go into the instruction text."""
    abstain = _abstain_label(agents)
    registry_text = "\n".join(
        f"- {a.id}: {a.name}. Tags: {', '.join(a.tags) or '(none)'}. "
        f"Handles: {a.description.strip()[:300] or '(no description)'}"
        for a in agents
    )
    return (
        "You route tickets for Switchboard, a dispatch system for single-purpose "
        "agents. Choose exactly one option for the ticket: the id of the agent "
        "whose described job matches what the ticket asks for, or "
        f"'{abstain}' if no agent's described job actually fits. Never force a "
        f"bad match; '{abstain}' is the right answer for work outside every "
        "agent's description.\n\n"
        f"Agents (id: name. tags. what it handles):\n{registry_text}\n\n"
        "Recent human corrections to past routing (treat as ground truth):\n"
        f"{_format_corrections(corrections or [])}"
    )


def _parse_provider_details(
    details: Dict[str, Any],
) -> Tuple[Optional[float], Optional[Dict[str, float]]]:
    """Verified shape: {"confidence": {"response": 1.0},
    "probabilities": {"response": {"billing": 1.0, ...}}, "scores": {}}.
    confidence_p = min over the confidence dict; scores = first value of the
    (question-keyed) probabilities dict."""
    details = details or {}
    conf = details.get("confidence")
    if isinstance(conf, dict) and conf:
        confidence_p: Optional[float] = float(min(conf.values()))
    elif isinstance(conf, (int, float)):
        confidence_p = float(conf)
    else:
        confidence_p = None
    probs = details.get("probabilities")
    scores: Optional[Dict[str, float]] = None
    if isinstance(probs, dict) and probs:
        first = next(iter(probs.values()))
        if isinstance(first, dict):
            scores = {str(k): float(v) for k, v in first.items()}
    return confidence_p, scores


def _jev_live_call(output_enum, instructions: str, prompt: str):
    """The only place the Jev SDK is imported (lazily, so the base install
    and the tests work without it)."""
    try:
        from pydantic_ai import Agent
        from pydantic_ai.models.typesafe import TypeSafeModel
    except ImportError as e:
        raise JevUnavailable(
            "Jev SDK not installed (pip install pydantic-ai with TypeSafe support)"
        ) from e
    if not os.environ.get("TYPESAFE_API_KEY"):
        raise JevUnavailable("TYPESAFE_API_KEY is not set")
    agent = Agent(
        TypeSafeModel(JEV_MODEL), output_type=output_enum, instructions=instructions
    )
    result = agent.run_sync(prompt)
    out = result.output
    value = out.value if isinstance(out, enum.Enum) else str(out)
    return value, dict(result.response.provider_details or {})


def _load_jev_mock() -> Optional[Dict[str, Any]]:
    path = os.environ.get("SWITCHBOARD_JEV_MOCK")
    if not path:
        return None
    with open(path) as f:
        return json.load(f)


def route_with_jev(
    ticket: Ticket,
    agents: List[AgentEntry],
    corrections: Optional[List[Correction]] = None,
    client: Optional[JevClient] = None,
    mock_fixture: Optional[Dict[str, Any]] = None,
) -> RouteDecision:
    """Ask Jev for a typed choice among registered agents (or abstain).

    Offline mock: SWITCHBOARD_JEV_MOCK=<path to JSON> (or mock_fixture) of
    the form {"choice": "<agent id or none>", "provider_details": {...}},
    with provider_details in the shape Jev really returns. `client` lets
    tests inject a stub with the same (enum, instructions, prompt) call shape.

    Raises JevUnavailable if Jev can't be used; the caller falls through."""
    if len(agents) > JEV_MAX_AGENTS:
        raise JevUnavailable(
            f"{len(agents)} agents registered; Jev supports at most "
            f"{JEV_MAX_AGENTS} (255-option cap incl. abstain)"
        )
    if not agents:
        raise JevUnavailable("no agents registered")

    output_enum = build_jev_enum(agents)
    abstain = _abstain_label(agents)
    fixture = mock_fixture if mock_fixture is not None else _load_jev_mock()
    if fixture is not None:
        choice = fixture["choice"]
        details = fixture.get("provider_details", {})
    else:
        instructions = build_jev_instructions(agents, corrections)
        prompt = f"Title: {ticket.title}\nTags: {ticket.tags}\nBody:\n{ticket.body}"
        try:
            choice, details = (client or _jev_live_call)(output_enum, instructions, prompt)
        except JevUnavailable:
            raise
        except Exception as e:  # network, auth, schema -- all mean "fall through"
            raise JevUnavailable(f"Jev call failed: {e}") from e

    confidence_p, scores = _parse_provider_details(details)
    chosen = None if choice == abstain else choice
    bucket = confidence_bucket(confidence_p)
    p_text = "n/a" if confidence_p is None else f"{confidence_p:.2f}"
    return RouteDecision(
        chosen_agent_id=chosen,
        justification=(
            f"Jev chose {chosen!r} (min confidence {p_text})"
            if chosen
            else f"Jev abstained: no agent fits (min confidence {p_text})"
        ),
        confidence=bucket,
        confidence_p=confidence_p,
        scores=scores,
    )
