"""The `python -m switchboard` command surface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from switchboard import dispatch as dispatch_mod
from switchboard import registry, router, tickets

STATUS_EMOJI = {
    "open": "⚪",
    "routed": "🔵",
    "in_progress": "🟡",
    "done": "🟢",
    "cancelled": "⚫",
}


def cmd_agents(args: argparse.Namespace) -> int:
    agents = registry.load_agents()
    if not agents:
        print("No agents registered. Add a markdown file under agents/.")
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


def cmd_route(args: argparse.Namespace) -> int:
    ticket = tickets.load_ticket(args.ticket_id)
    agents = registry.load_agents()

    agent, score = router.route_deterministic(ticket, agents)
    if agent is not None:
        print(f"Deterministic match: {agent.id} (tag overlap score {score})")
    elif args.ai:
        decision = router.route_with_ai(ticket, agents)
        print(f"AI router: {decision.chosen_agent_id} ({decision.confidence} confidence)")
        print(f"  Reasoning: {decision.justification}")
        agent = next((a for a in agents if a.id == decision.chosen_agent_id), None)
    else:
        print(
            "No deterministic match (no shared tags with any registered "
            "agent). Re-run with --ai to ask Claude, or assign manually."
        )
        return 1

    if agent is None:
        print("No suitable agent found -- leaving ticket unrouted for a human to triage.")
        return 1

    tickets.update_ticket(args.ticket_id, status="routed", assignee=agent.id)
    print(f"Ticket {args.ticket_id} routed to {agent.id}.")
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
    tickets.update_ticket(args.ticket_id, status="in_progress")
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
    parser = argparse.ArgumentParser(prog="python -m switchboard")
    sub = parser.add_subparsers(dest="command", required=True)

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

    p_route = sub.add_parser("route", help="Route a ticket to its best-fit agent")
    p_route.add_argument("ticket_id")
    p_route.add_argument("--ai", action="store_true", help="Fall back to Claude if no deterministic match")
    p_route.set_defaults(func=cmd_route)

    p_dispatch = sub.add_parser("dispatch", help="Prepare (or run) a routed ticket's dispatch command")
    p_dispatch.add_argument("ticket_id")
    p_dispatch.add_argument("--run", action="store_true", help="Actually execute, instead of only printing")
    p_dispatch.set_defaults(func=cmd_dispatch)

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
