"""The `switchboard` command surface (also runnable as `python -m switchboard`)."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from switchboard import attempts as attempts_mod
from switchboard import dispatch as dispatch_mod
from switchboard import memory, registry, router, talk, tickets
from switchboard.models import Correction, RoutingRationale

STATUS_EMOJI = {
    "open": "⚪",
    "routed": "🔵",
    "in_progress": "🟡",
    "done": "🟢",
    "cancelled": "⚫",
}

INIT_AGENTS_README = """\
Add one markdown file per agent you want Switchboard to route tickets to.

Minimal shape:

---
id: my-agent
name: My Agent
repo: https://github.com/you/my-agent
invoke: "python run.py {ticket_path}"
tags: [some, keywords, that, describe, what, it, does]
risk_tier: low   # or medium/high if it has real side effects
---

A one-paragraph description of what this agent does and when to route to it.

No restart needed -- the registry is read fresh on every command.
"""


def cmd_init(args: argparse.Namespace) -> int:
    """Scaffold an empty workspace: agents/, tickets/, memory/, and a
    starter agents/README.md -- so this is usable as a general-purpose
    tool from a cold start, not just pre-wired to one portfolio."""
    for d in ("agents", "tickets", "memory"):
        Path(d).mkdir(exist_ok=True)
    readme_path = Path("agents/README.md")
    if not readme_path.exists():
        readme_path.write_text(INIT_AGENTS_README)
    print("Initialized: agents/, tickets/, memory/")
    print("Add your first agent: create agents/<id>.md (see agents/README.md)")
    print("Then: switchboard new --title \"...\" --tags a,b,c")
    return 0


def cmd_agents(args: argparse.Namespace) -> int:
    agents = registry.load_agents()
    if not agents:
        print("No agents registered. Run `switchboard init`, then add a markdown file under agents/.")
        return 0
    for a in agents:
        print(f"{a.id:<26} {a.name:<28} [{a.risk_tier:<6}] tags={', '.join(a.tags)}")
    return 0


def cmd_ticket_new(args: argparse.Namespace) -> int:
    tags = [t.strip() for t in args.tags.split(",")] if args.tags else []
    ticket = tickets.new_ticket(title=args.title, tags=tags, body=args.body or "")
    print(f"Created ticket {ticket.id}: {ticket.title}")
    return 0


def cmd_ticket_list(args: argparse.Namespace) -> int:
    for t in tickets.list_tickets(status=args.status):
        emoji = STATUS_EMOJI.get(t.status, "?")
        assignee = t.assignee or "-"
        print(f"{emoji} {t.id}  [{t.status:<11}] {t.title}  (assignee: {assignee})")
    return 0


def cmd_ticket_show(args: argparse.Namespace) -> int:
    ticket = tickets.load_ticket(args.ticket_id)
    print(f"{ticket.id}: {ticket.title}")
    print(f"  status:   {ticket.status}")
    print(f"  tags:     {', '.join(ticket.tags) or '(none)'}")
    print(f"  assignee: {ticket.assignee or '(unassigned)'}")
    if ticket.routing:
        r = ticket.routing
        print(f"  routing:  method={r.method} confidence={r.confidence or '-'} score={r.score if r.score is not None else '-'}")
        if r.justification:
            print(f"            {r.justification}")
    if ticket.body:
        print(f"\n{ticket.body}")
    ticket_attempts = attempts_mod.list_attempts(ticket_id=ticket.id)
    if ticket_attempts:
        print("\n  dispatch attempts:")
        for a in ticket_attempts:
            print(f"    {a.id}  {a.status:<10} pid={a.pid} exit={a.returncode}")
    return 0


def cmd_route(args: argparse.Namespace) -> int:
    ticket = tickets.load_ticket(args.ticket_id)
    agents = registry.load_agents()
    today = date.today()

    agent, score = router.route_deterministic(ticket, agents)
    if agent is not None:
        print(f"Deterministic match: {agent.id} (tag overlap score {score})")
        matched = sorted(set(t.lower() for t in ticket.tags) & set(t.lower() for t in agent.tags))
        rationale = RoutingRationale(
            method="deterministic", matched_tags=matched, score=score, decided_at=today
        )
        tickets.update_ticket(args.ticket_id, status="routed", assignee=agent.id, routing=rationale)
        print(f"Ticket {args.ticket_id} routed to {agent.id}.")
        return 0

    if not args.ai:
        print(
            "No deterministic match (no shared tags with any registered "
            "agent). Re-run with --ai to ask Claude, or assign manually with "
            "`switchboard reroute`."
        )
        return 1

    corrections = memory.load_corrections(limit=10)
    decision = router.route_with_ai(ticket, agents, corrections=corrections)
    print(f"AI router: {decision.chosen_agent_id} ({decision.confidence} confidence)")
    print(f"  Reasoning: {decision.justification}")

    rationale = RoutingRationale(
        method="ai",
        justification=decision.justification,
        confidence=decision.confidence,
        decided_at=today,
    )

    if decision.chosen_agent_id is None:
        print("AI router found no suitable agent -- leaving unrouted for a human to triage.")
        tickets.update_ticket(args.ticket_id, routing=rationale)
        return 1

    if decision.confidence == "low":
        print(
            "Confidence is low -- recording this as a suggestion, NOT an "
            "assignment. The ticket stays open. Use `switchboard reroute` "
            "to actually commit to an agent."
        )
        tickets.update_ticket(args.ticket_id, routing=rationale)
        return 1

    matched_agent = next((a for a in agents if a.id == decision.chosen_agent_id), None)
    if matched_agent is None:
        print(f"AI router chose {decision.chosen_agent_id!r}, which isn't a registered agent id.")
        return 1

    tickets.update_ticket(
        args.ticket_id, status="routed", assignee=matched_agent.id, routing=rationale
    )
    print(f"Ticket {args.ticket_id} routed to {matched_agent.id}.")
    return 0


def cmd_reroute(args: argparse.Namespace) -> int:
    ticket = tickets.load_ticket(args.ticket_id)
    agents_map = registry.agents_by_id()
    if args.to not in agents_map:
        print(f"{args.to!r} is not a registered agent id. See `switchboard agents`.")
        return 1

    correction = Correction(
        ticket_id=ticket.id,
        from_agent=ticket.assignee,
        to_agent=args.to,
        reason=args.reason,
        corrected_at=date.today(),
    )
    memory.append_correction(correction)

    rationale = RoutingRationale(
        method="manual", justification=args.reason, decided_at=date.today()
    )
    tickets.update_ticket(args.ticket_id, status="routed", assignee=args.to, routing=rationale)
    print(f"Ticket {args.ticket_id} rerouted to {args.to}. Recorded in memory/routing_corrections.jsonl")
    print("Future ambiguous tickets routed with --ai will see this correction.")
    return 0


def cmd_dispatch(args: argparse.Namespace) -> int:
    ticket = tickets.load_ticket(args.ticket_id)
    if ticket.assignee is None:
        print(f"Ticket {args.ticket_id} has no assignee -- run `route` first.")
        return 1

    agents_map = registry.agents_by_id()
    agent = agents_map.get(ticket.assignee)
    if agent is None:
        print(f"Assignee {ticket.assignee!r} is not a registered agent.")
        return 1

    ticket_path = tickets.path_for(tickets.DEFAULT_TICKETS_DIR, args.ticket_id)
    dispatch_mod.dispatch(agent, ticket, ticket_path, run=args.run)
    if args.run:
        tickets.update_ticket(args.ticket_id, status="in_progress")
    return 0


def cmd_talk(args: argparse.Namespace) -> int:
    agents_map = registry.agents_by_id()
    agent = agents_map.get(args.agent_id)
    if agent is None:
        print(f"{args.agent_id!r} is not a registered agent id. See `switchboard agents`.")
        return 1

    reply = talk.ask(agent, args.question)
    print(reply)
    path = talk.append_transcript(agent.id, args.question, reply)
    print(f"\n(appended to {path})")
    return 0


def cmd_attempts(args: argparse.Namespace) -> int:
    ticket_attempts = attempts_mod.list_attempts(ticket_id=args.ticket_id)
    if not ticket_attempts:
        print("No dispatch attempts recorded" + (f" for {args.ticket_id}." if args.ticket_id else "."))
        return 0
    for a in ticket_attempts:
        print(f"{a.id}  ticket={a.ticket_id} agent={a.agent_id} status={a.status:<10} pid={a.pid} exit={a.returncode}")
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    ticket = tickets.update_ticket(args.ticket_id, status="done")
    tickets.append_ledger(ticket, args.summary)
    print(f"Closed {args.ticket_id}. Recorded in ledger.md.")
    return 0


def cmd_board(args: argparse.Namespace) -> int:
    all_tickets = tickets.list_tickets()
    by_status = {}
    for t in all_tickets:
        by_status.setdefault(t.status, []).append(t)

    for status in ("open", "routed", "in_progress", "done", "cancelled"):
        group = by_status.get(status, [])
        if not group:
            continue
        print(f"\n{STATUS_EMOJI[status]} {status.upper()} ({len(group)})")
        for t in group:
            assignee = f" -> {t.assignee}" if t.assignee else ""
            print(f"  {t.id}  {t.title}{assignee}")
    print()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="switchboard")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Scaffold agents/, tickets/, memory/ in the current directory").set_defaults(func=cmd_init)
    sub.add_parser("agents", help="List registered specialist agents").set_defaults(func=cmd_agents)
    sub.add_parser("board", help="Show the full ticket board grouped by status").set_defaults(func=cmd_board)

    p_new = sub.add_parser("new", help="File a new ticket")
    p_new.add_argument("--title", required=True)
    p_new.add_argument("--tags", default="")
    p_new.add_argument("--body", default="")
    p_new.set_defaults(func=cmd_ticket_new)

    p_list = sub.add_parser("list", help="List tickets")
    p_list.add_argument("--status", default=None)
    p_list.set_defaults(func=cmd_ticket_list)

    p_show = sub.add_parser("show", help="Show a ticket's full detail, including routing rationale and attempts")
    p_show.add_argument("ticket_id")
    p_show.set_defaults(func=cmd_ticket_show)

    p_route = sub.add_parser("route", help="Route a ticket to its best-fit agent")
    p_route.add_argument("ticket_id")
    p_route.add_argument("--ai", action="store_true", help="Fall back to Claude if no deterministic match")
    p_route.set_defaults(func=cmd_route)

    p_reroute = sub.add_parser("reroute", help="Manually override a routing decision; records why for future routing")
    p_reroute.add_argument("ticket_id")
    p_reroute.add_argument("--to", required=True, help="Agent id to assign")
    p_reroute.add_argument("--reason", required=True)
    p_reroute.set_defaults(func=cmd_reroute)

    p_dispatch = sub.add_parser("dispatch", help="Prepare (or run) a routed ticket's dispatch command")
    p_dispatch.add_argument("ticket_id")
    p_dispatch.add_argument("--run", action="store_true", help="Actually execute, instead of only printing")
    p_dispatch.set_defaults(func=cmd_dispatch)

    p_talk = sub.add_parser("talk", help="Advisory Q&A with an agent -- no ticket, no dispatch")
    p_talk.add_argument("agent_id")
    p_talk.add_argument("question")
    p_talk.set_defaults(func=cmd_talk)

    p_attempts = sub.add_parser("attempts", help="List dispatch attempts (optionally for one ticket)")
    p_attempts.add_argument("ticket_id", nargs="?", default=None)
    p_attempts.set_defaults(func=cmd_attempts)

    p_close = sub.add_parser("close", help="Close a ticket and record it in the ledger")
    p_close.add_argument("ticket_id")
    p_close.add_argument("--summary", required=True)
    p_close.set_defaults(func=cmd_close)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
