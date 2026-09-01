<p align="center">
  <img src="docs/switchboard-banner.svg" alt="switchboard -- git-native ticket router for a fleet of specialist AI agents" width="100%" />
</p>

<div align="center">

# switchboard

### A git-native ticket router for a fleet of specialist AI agents

File a ticket. Get connected to the right agent, automatically — or told honestly that none fit.

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Powered by Claude](https://img.shields.io/badge/Powered_by-Claude-D97757?logo=anthropic&logoColor=white)](https://www.anthropic.com/)
[![License: MIT](https://img.shields.io/badge/license-MIT-2ea44f.svg)](LICENSE)
[![Status: Reference Implementation](https://img.shields.io/badge/status-reference%20implementation-6f42c1)](#whats-next)

</div>

---

<div align="center">

| 7 registered agents | 2 routing tiers | 6 gaps closed vs. Livery | 26 tests |
|:---:|:---:|:---:|:---:|
| One markdown file each | Deterministic tag-match → Claude fallback | Attempts · concurrency · Talk · Debate · Schedule · Notify | Fully offline, zero API key |

</div>

## Overview

Switchboard is a single-user, git-native ticket-and-dispatch layer for a
fleet of independent, single-purpose AI agents. It's inspired by
[Livery](https://github.com/sohailmamdani/livery)'s "agents and tickets
are files" model — built independently from scratch, no shared code — and
aimed at doing the one thing Livery leaves to a human: **deciding which
agent a ticket should go to**, automatically, better than a manually-set
`assignee` field can.

### Why this exists

Manually deciding "which agent should handle this" doesn't scale past a
handful of agents, and asking an LLM every single time is slow and costs
money for decisions that are usually obvious. Switchboard's router is
deterministic by default (instant, free, tag-overlap matching), escalates
to Claude only when that's genuinely ambiguous, refuses to force a bad
match either way, and — the part that doesn't exist anywhere else in this
space — **remembers every time a human corrects it**, so the next
ambiguous ticket benefits from that correction as prompt context.

**Explore:** [vs. Livery](#how-this-compares-to-livery) · [How it works](#how-it-works) · [Architecture](#architecture) · [Real findings](#real-findings-from-building-and-testing-this) · [Setup](#setup) · [Usage](#usage)

---

## How this compares to Livery

Livery is a broader, more mature tool — this isn't a claim to have built
something bigger. It's narrower on purpose, and better than Livery
specifically at the one job it does:

| | Livery | Switchboard |
|---|---|---|
| **Agent-to-ticket assignment** | Manual — you set `assignee` yourself | **Automatic** — deterministic tag-match first, Claude-assisted fallback, honest refusal if nothing fits |
| **Learns from correction** | No feedback loop on assignment quality | **Yes** — every `reroute` is recorded and fed back into the AI router's prompt as ground truth |
| **Talk mode** (advisory Q&A) | Yes | **Yes** — `switchboard talk <agent-id> "question"` |
| **Walkie-Talkie** (AI-to-AI debate) | Two real hired agents, each in their own runtime | **Adapted** — two agent *personas* debate a ticket; honest about the difference below |
| **Scheduling** | `launchd`/`systemd` jobs, installed by the CLI | **Declared + rendered**, never auto-installed |
| **Notifications** | Telegram-specific | **Generic** — desktop notification plus any webhook (Slack, Discord, Telegram, plain HTTP) |
| **Routing rationale on the ticket** | Not applicable (no auto-routing) | Every routed ticket carries *why*, as permanent git-diffable history |
| **Runtime breadth** | 5 real adapters (Claude Code, Codex, Cursor, LM Studio, Ollama) | Shells out to a command string — the one place Livery still wins structurally |

The honest summary: Livery still wins on runtime breadth — its agents are
live conversational processes, Switchboard's are one-shot command
strings — and on actually installing what it schedules. Everything else
in this table, Switchboard either matches or is ahead on.

---

## How it works

1. File a ticket — a markdown file with a title, tags, and a body, created via one CLI call
2. The **deterministic router** scores every registered agent by tag overlap and assigns the best match — no API call, no network
3. If no agent shares a single tag, the ticket stays **unrouted** unless you explicitly ask the **AI router** (Claude) — enriched with recent human corrections as few-shot context, and still allowed to say none of them fit
4. A **low-confidence** AI decision is recorded as a *suggestion*, not an assignment — the ticket stays open until a human commits to it
5. **`reroute`** lets a human override any decision, and permanently records why — that correction improves every future ambiguous routing call
6. **Dispatch** composes the exact shell command that would send the ticket to its assigned agent, and prints it by default — `--run` executes it for real, with a durable attempt record and a warning for medium/high-risk agents
7. **Close** a ticket and it's appended to `ledger.md` — an append-only audit trail, never rewritten
8. **Talk** to any registered agent directly — no ticket filed, nothing dispatched
9. **Debate** a ticket between two agent personas when it's genuinely unclear whose job it is
10. **Schedule** a recurring dispatch declaratively, then render it into a real `launchd`/`systemd` unit — Switchboard never touches your OS scheduler itself
11. Get **notified** — desktop notification or webhook — the moment a ticket needs a human or a dispatch fails

## Architecture

```mermaid
flowchart TD
    Ticket["New ticket<br/>markdown + tags"] --> Router{"Deterministic<br/>tag-match router"}
    Router -->|"score > 0"| Assign["Assign + record<br/>routing rationale"]
    Router -->|"score = 0"| AIGate{"--ai flag?"}
    AIGate -->|yes| Memory[("memory/routing_corrections.jsonl")]
    Memory --> AIRouter["Claude-assisted router<br/>(sees past corrections)"]
    AIGate -->|no| Human["Unrouted -- human triage"]
    AIRouter -->|"high/medium confidence"| Assign
    AIRouter -->|"low confidence"| Suggest["Suggestion only --<br/>stays open"]
    AIRouter -->|"null"| Human
    Human -.->|"reroute --to --reason"| Correction["Append correction"]
    Correction --> Memory
    Correction --> Assign
    Assign --> Dispatch{"dispatch:<br/>risk_tier check"}
    Dispatch -->|"default, no --run"| Print["Print the command only"]
    Dispatch -->|"--run"| Run["Execute + record<br/>DispatchAttempt (pid, status)"]
    Print -.-> Close["close --summary"]
    Run --> Close
    Close --> Ledger[("ledger.md<br/>append-only")]
```

Full agent-by-agent, decision-by-decision design rationale — including a
line-by-line comparison against Livery's actual documented behavior — is
in [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Real findings from building and testing this

| Finding | What changed |
|---|---|
| A router that always answers trains you to stop checking the answer | `route_deterministic` returns `None` below a real tag match instead of always producing its highest-scoring guess |
| Confidence has to gate the action, not just get logged next to it | Only medium/high AI confidence commits an assignment; low confidence is a recorded suggestion, ticket stays open |
| A fire-and-forget `subprocess.run` is a real robustness gap | `--run` now uses `Popen` (PID captured at start) through to a `DispatchAttempt` record — proven against a real failure, not mocked (see below) |
| Concurrent ticket creation needs an actual lock | `O_CREAT\|O_EXCL` advisory lock around id allocation; a 10-thread test proves it |
| Not every repo fits the same abstraction | Three real repos deliberately excluded from the agent registry — forcing them in would look complete while being wrong |

The dispatch-attempt claim, proven, not asserted — dispatching a ticket to
an agent whose repo isn't cloned locally correctly recorded a failure
instead of a silent hang:

```json
{
  "id": "0002-20260831T193217",
  "ticket_id": "0002",
  "agent_id": "tpm-agent-os",
  "pid": 70794,
  "status": "failed",
  "returncode": 2
}
```

---

## What's next

- [x] Automatic routing (deterministic + AI fallback)
- [x] Routing-correction memory
- [x] Durable dispatch attempts
- [x] Concurrency-safe ticket creation
- [x] Talk mode
- [x] Walkie-Talkie-style debate (persona-adapted)
- [x] Scheduling (declared + rendered, not installed)
- [x] Generic notifications (desktop + webhook)
- [ ] Real runtime adapters — agents are command strings, not live sessions, by design
- [ ] Automatic schedule installation — a deliberate stop-short; see ARCHITECTURE.md
- [ ] Cron beyond "fixed time(s), daily" — anything else raises a clear error instead of guessing

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"      # installs the real `switchboard` command
cp .env.example .env          # only needed for `route --ai` and `talk`
pytest tests/ -v              # fully offline, no API key required
```

## Usage

```bash
# Starting from scratch? Scaffold agents/, tickets/, memory/
switchboard init

# See what's registered
switchboard agents

# File a ticket
switchboard new --title "Clean up Gmail before the demo" \
  --tags "email,cleanup" --body "Archive spam, leave receipts labeled."

# Route it -- deterministic tag-match, no API call, rationale persisted on the ticket
switchboard route 0001

# No tag overlap with anything registered? Ask Claude -- it sees past corrections too.
switchboard route 0005 --ai

# Disagree with a routing call? Override it, and teach the router why.
switchboard reroute 0005 --to exec-status-rollup --reason "..."

# See the exact command that would run -- prints by default, doesn't execute
switchboard dispatch 0001

# Actually run it -- tracked as a durable attempt (pid, status, exit code)
switchboard dispatch 0001 --run
switchboard attempts 0001

# Full ticket detail: routing rationale + every dispatch attempt
switchboard show 0001

# Advisory question to an agent -- no ticket filed, nothing dispatched
switchboard talk inbox-marshal "Would you touch an email that's already labeled by Gmail's own filters?"

# Genuinely unclear whether it's one agent's job? Have two argue about it.
switchboard debate 0005 --agent-a exec-status-rollup --agent-b critical-path-radar --rounds 2

# Declare a recurring schedule -- nothing installed yet
switchboard schedule-new --id daily-exec-rollup \
  --description "Morning executive portfolio rollup" \
  --agent exec-status-rollup --cron "0 8 * * *"

# Render it as a real launchd plist (macOS) or systemd unit (Linux) -- prints only
switchboard schedule-render daily-exec-rollup

# Close it out -- appended to ledger.md, never rewritten
switchboard close 0001 --summary "Inbox clean, demo-ready."

# Full board, grouped by status
switchboard board
```

Set `SWITCHBOARD_WEBHOOK_URL` to any endpoint that accepts a JSON
`{"text": "..."}` POST (a Slack incoming webhook, Discord, or your own) to
get notified when a ticket needs triage or a dispatch fails. macOS also
gets a local desktop notification automatically, no configuration needed.

The repository ships with a real, already-populated board — five tickets
spanning open, routed, in-progress, and done, including one that was
deliberately unrouted by the deterministic pass and then manually
corrected with `reroute`, now in `memory/routing_corrections.jsonl` for
the AI router to learn from on the next ambiguous ticket.

---

## Repository map

```text
switchboard/
├── switchboard/                 The package
│   ├── models.py                AgentEntry, Ticket, RoutingRationale, Correction, DispatchAttempt
│   ├── frontmatter.py           Minimal YAML-frontmatter markdown parsing
│   ├── registry.py              Loads agents/*.md
│   ├── tickets.py                Create/list/update/close tickets; lock-guarded id allocation; append-only ledger
│   ├── router.py                Deterministic tag-match + Claude-assisted fallback, correction-aware
│   ├── memory.py                Append-only routing-correction log the AI router reads back
│   ├── attempts.py               Durable dispatch attempt records (PID, status, exit code)
│   ├── talk.py                   Advisory Q&A with an agent -- no ticket, no dispatch
│   ├── debate.py                 Walkie-Talkie-style two-agent-persona debate
│   ├── schedule.py               Declare + render recurring dispatches (launchd/systemd) -- never installs
│   ├── notify.py                 Best-effort desktop + webhook notifications, never raises
│   ├── dispatch.py               Composes and (optionally) runs the invoke command
│   └── cli.py                    `switchboard <command>`
├── agents/                       Seven real specialist agents, one file each
├── tickets/                      A live, populated example board
├── memory/routing_corrections.jsonl   Git-tracked correction history
├── talk/                         Per-agent advisory transcripts (created on first use)
├── walkie-talkie/                Debate transcripts (created on first use)
├── schedules/                    Declared recurring dispatches, one file each
├── ledger.md                     Append-only record of closed tickets
├── tests/                        Fully offline (SWITCHBOARD_MOCK=1 for the AI router)
└── ARCHITECTURE.md               Design rationale, decision by decision
```

---

## Contact

<div align="center">

### Navi Sohi

*Technical Program Manager & Automation Engineer*

<a href="https://www.linkedin.com/in/navisohi/"><img src="https://img.shields.io/badge/LINKEDIN-0A66C2?style=for-the-badge&logo=linkedin&logoColor=white" alt="LinkedIn" /></a>
<a href="https://github.com/PlainJane20"><img src="https://img.shields.io/badge/GITHUB-181717?style=for-the-badge&logo=github&logoColor=white" alt="GitHub" /></a>
<a href="mailto:nks.ai.dev@gmail.com"><img src="https://img.shields.io/badge/EMAIL-EA4335?style=for-the-badge&logo=gmail&logoColor=white" alt="Email" /></a>

</div>

## License

Copyright © 2026 Navi Sohi.

This project is distributed under the [MIT License](LICENSE). Reuse is permitted under the
license terms, provided the copyright and license notice are retained in copies or substantial
portions of the software.
