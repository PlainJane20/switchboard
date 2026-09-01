# Architecture notes

The "why," not just the "what." The README is the pitch; this is what I'd
defend in a design review.

## Why tickets and agents are markdown files, not a database

Livery (the project that inspired this one) makes the same call: state that
lives in git-tracked markdown is state a reviewer can read without running
anything, diff across commits, and edit by hand when the CLI doesn't cover
a case yet. A database would need its own backup story, its own migration
story, and would make "what did this ticket look like three commits ago"
a query instead of `git log -p`. For a single-operator tool, the
complexity a database buys isn't worth what it costs.

## Why registering an agent means "add one markdown file," not a plugin API

The alternative -- a Python entry-point/plugin system agents register
against -- would mean Switchboard needs to import each agent's code, match
its dependency versions, and share a runtime. Every agent in this fleet is
a completely separate repo with its own `requirements.txt`, its own
Python version assumptions, some of them shell scripts. Declaring an agent
as `id / name / repo / invoke / tags / risk_tier` and shelling out to
`invoke` at dispatch time means Switchboard never needs to know what's
inside an agent, only how to describe and call it. Adding the eighth agent
is one new file, not a code change to Switchboard itself.

## Why routing is deterministic-first, with AI as a fallback, not the default

The same cost/judgment split as tpm-agent-os's model tiering, applied to a
different decision: most tickets tag-match an obvious agent (a ticket
tagged `email, cleanup` obviously goes to `inbox-marshal`), and spending an
API call to confirm the obvious is waste. The AI router only runs when the
deterministic pass finds zero tag overlap with any registered agent, or
when explicitly requested with `--ai`. That also means the core dispatch
loop has zero external dependency in the common case -- `agents`, `new`,
`list`, `board`, and a tag-matching `route` all work with no API key and no
network.

## Why the deterministic router returns `None` on zero overlap instead of "closest guess"

An earlier version of `route_deterministic` had no floor -- it always
returned whichever agent scored highest, even a score of zero. That's the
same failure mode called out in tpm-agent-os's ARCHITECTURE.md: a router
that always produces an answer trains you to stop checking whether the
answer is any good. `test_deterministic_router_refuses_to_force_a_bad_match`
exists specifically to keep this from regressing -- a ticket about vendor
consolidation should come back unrouted, not get force-matched to whichever
registered agent happens to share zero tags least badly.

## Why dispatch prints a command by default instead of running it

Two agents in the registry are `risk_tier: medium` because they have real
side effects -- `inbox-marshal` can archive email and fire unsubscribe
requests, `slack-daily-brief` posts to a real Slack channel. `dispatch`
composes the exact shell command and prints it; running it is an explicit
`--run` flag, and a medium/high-risk agent gets a visible warning even
then. This is the same posture inbox-marshal itself takes with
`--scan`/`--apply`, and the same reason agent-control-tower's approval
gate exists at all -- a routing layer that can silently execute
side-effecting agents is a bigger blast radius than the mistake it's
trying to prevent.

## Why agent-control-tower, signalweave-ai, and pm-automation-system aren't in the registry

This isn't an oversight -- each one fails a real fit test for "a ticket
dispatches to it":

- **agent-control-tower** governs other agents' actions (budgets,
  approvals, audit); it isn't something you hand a task to, it's
  infrastructure the agents that *do* tasks should call through. A natural
  next step is having `dispatch --run` route medium/high-risk agents
  through it instead of running them directly.
- **signalweave-ai** expects a structured, validated `ProgramScenario`
  object, not a freeform ticket body -- forcing that mapping here would
  either lose information or misrepresent what the ticket actually says.
- **pm-automation-system** is a standing webhook service, not a one-shot
  process you invoke per ticket.

Registering all three anyway, with an `invoke` command that doesn't really
fit, would have made the registry look complete while quietly being wrong.
Seven honestly-described agents beat ten where three are guesses.

## Why mock mode exists for the router specifically

`SWITCHBOARD_MOCK=1` only affects `route_with_ai` -- the one function that
calls Claude. Every other command (`agents`, `new`, `list`, `board`,
deterministic `route`, `dispatch`, `close`) never touches the network, so
they didn't need a mock path to be tested offline. Mocking only the
function that actually needs it, rather than a blanket mode switch on the
whole CLI, keeps the tests honest about what's actually being faked.

## Why low-confidence AI decisions don't auto-assign

The first version of `cmd_route` treated any non-null `chosen_agent_id`
from the AI router the same way, regardless of `confidence`. That's a real
bug, not a style choice: a "low confidence, honestly guessing" decision
and a "high confidence, obvious fit" decision looked identical on the
board -- both just an assignee. The fix makes confidence gate the action:
`medium`/`high` commits (status becomes `routed`, assignee is set); `low`
is written into the ticket's `routing` field as a visible suggestion, but
the ticket stays `open` and unassigned. A human has to `reroute` it to
actually commit. This is the same "kill/redirect calls should be explicit,
not implicit" judgment used elsewhere in this portfolio, applied to a
routing decision instead of a program decision.

## Why routing corrections are the one thing built specifically to beat Livery, not just match it

Livery's ticket model has an `assignee` field, set by the human, full
stop -- nothing in its documented behavior describes automatic
content-based routing, and there's no feedback loop on assignment quality
at all. Switchboard's `reroute` command exists to close that loop:
every manual override is appended to `memory/routing_corrections.jsonl`
(append-only, same convention as `ledger.md` and agent-control-tower's own
`audit.jsonl`), and `route --ai` reads recent corrections back in as
few-shot context on the next ambiguous ticket. This is not fine-tuning and
it is not an embedding index -- it's plain text fed into a prompt, sourced
from git history. That's a feature, not a limitation: `git log -p
memory/routing_corrections.jsonl` shows the entire "training signal" a
reviewer would need to audit, with no black box to trust.

## Why dispatch attempts exist -- the other real gap versus Livery

Livery's README is explicit: "Durable dispatch attempts under
`.livery/dispatch/attempts/<attempt-id>.json`, with status, PID, failures...
recorded per run." The first version of `dispatch.py` used a bare
`subprocess.run()` -- no record survives the call, so "did that dispatch
actually finish, and how" has no answer once the terminal scrolls. The fix
uses `Popen` so the PID is captured the instant the process starts (not
after), writes a `DispatchAttempt` record immediately with `status:
running`, then updates it to `succeeded`/`failed` with the real exit code
once the process exits. Tested against a real failure, not a synthetic
one: dispatching a ticket to `tpm-agent-os` from inside this repo (where
`run_demo.py` doesn't exist) correctly produced `status: failed,
returncode: 2` -- proof the tracking works on an actual broken command,
not just a mocked success path.

Attempts live under `.switchboard/` and are gitignored on purpose -- see
the next section for why that split matters.

## Why attempts are ephemeral but corrections and ledger entries are not

Three things get written after the fact in this system: dispatch
attempts, ledger entries, and routing corrections. Only one of them is
gitignored. A PID from an attempt three runs ago has no value once you
know whether it succeeded -- keeping it in git history forever is noise.
A ledger entry ("ticket X closed, here's why") and a routing correction
("a human said Y, not Z, because...") are exactly the opposite: their
entire value *is* being permanent, auditable history. Livery draws the
same line -- `memory/` is git-tracked, `.livery/` is not -- for the same
reason, and Switchboard's split matches it deliberately rather than by
coincidence.

## Why ticket-id allocation needs an actual lock

`new_ticket` computes the next id by looking at the highest existing
ticket file number and adding one. Two CLI invocations running at the same
moment can both read the same "current highest" before either writes its
new file, and one ticket silently overwrites the other's filename. The fix
is an advisory lock around id allocation using `os.open(path,
O_CREAT | O_EXCL)` -- atomic at the filesystem level, no dependency needed
for one lock file. `test_concurrent_ticket_creation_never_collides` fires
ten `new_ticket` calls at once from a thread pool and asserts all ten ids
come out unique; without the lock, this test fails intermittently rather
than deterministically, which is exactly the kind of bug that's easy to
ship and hard to catch without a test built to provoke it.

## Why Talk doesn't invoke the agent's real `invoke` command

`switchboard talk <agent-id> "question"` answers from the agent's registry
*description* only -- it never shells out to the agent's actual `invoke`
command. The alternative (actually running the agent to answer "would you
handle X") would mean every advisory question that starts with "would
you..." has the exact same side effects and runtime cost as a real
dispatch, including for `risk_tier: medium` agents like `inbox-marshal`
that can archive email. Livery draws the identical line between Talk
(conversation, spawns the runtime in print mode, told explicitly not to
modify files or launch long-running work) and Dispatch (an actual task) --
Switchboard's version of that boundary is simpler (no runtime spawn at
all for Talk, just a system prompt built from the registry entry) but
enforces the same guarantee: asking a question is never itself an action.

## Why `schedule-render` prints a plist/unit instead of installing it

Livery installs schedules as real `launchd`/`systemd` jobs. Switchboard
stops one step short on purpose: writing into `~/Library/LaunchAgents` or
a systemd unit directory is a change to the *machine's* state, not the
repo's -- it persists across reboots, survives `git clean`, and (unlike
every other side effect in this tool) isn't something `git log` will ever
show you happened. Every other "real" action in Switchboard -- dispatching
an agent, sending a notification -- is scoped to either this repo's own
files or a single outbound request; installing a scheduler job is a
different category, closer to "modifies your OS" than "does the thing
this tool is for." `parse_daily_cron` and the two renderers
(`render_launchd`, `render_systemd`) do the actually-hard part (getting
the plist/unit syntax right) and stop there. This is a real capability
gap versus Livery, named honestly rather than quietly worked around.

## Why cron support is deliberately narrow

`parse_daily_cron` accepts exactly one shape: a fixed minute, one or more
fixed hours, every day (`M H1,H2,H3 * * *`). That's not a partial
implementation of cron -- it's the complete implementation of the one
pattern that's actually true of every real recurring job already in this
portfolio (`slack-daily-brief`'s own `launchd` plist runs at 8am/1pm/6pm
daily, and nothing in this fleet needs day-of-week or step expressions).
A general cron parser is a much bigger surface -- day-of-month, step
values, ranges, `@reboot`-style specials -- almost all of which would go
unused here. Raising a clear `ValueError` naming exactly what's
unsupported beats silently mis-scheduling a job on an expression that
looks like it should work but doesn't.

## Why notifications are a generic webhook, not Telegram specifically

Livery integrates with Telegram by name. Switchboard's `notify()` sends to
a local desktop notification (macOS, best-effort, silent no-op elsewhere)
and, if `SWITCHBOARD_WEBHOOK_URL` is set, POSTs `{"text": "..."}` to it --
which is simultaneously a valid Slack incoming webhook payload, close
enough to Discord's, and trivially close to whatever a Telegram bot
bridge would want. Naming one provider means everyone using a different
one writes their own integration; a generic webhook means the one
integration this tool ships works for all of them, including Telegram, at
the cost of not being a first-class Telegram experience specifically.
`notify()` never raises on failure by design -- a bad webhook URL should
never be the reason a `route` or `dispatch` command exits non-zero.

## Why debate uses agent personas instead of real conversational agents

Livery's Walkie-Talkie puts two *actually hired* agents in their own
runtimes into a real back-and-forth. Switchboard's registered agents are
one-shot CLIs against separate repos -- there's no live session to have
`critical-path-radar` actually argue with `incident-postmortem-agent`,
because neither one is a conversational process at all. `debate.py`'s
honest adaptation: both sides of the debate are Claude, each one
constrained by a system prompt built from that agent's actual registered
description and tags, arguing from what's genuinely declared about it --
not a simulation dressed up as more than it is. The transcript is labeled
by `agent_id`, not by some third invented persona name, specifically so
it's never mistaken for the real agent having been invoked. This is also
why `debate` never touches `invoke` or writes a `DispatchAttempt` --
nothing about a debate is a dispatch.

## The honest comparison to Livery, restated

Livery is not a strawman here -- it has one real, structural advantage
Switchboard doesn't: multi-runtime adapters (Claude Code, Codex, Cursor,
LM Studio, Ollama) mean its agents are live conversational processes,
not one-shot command strings. That difference is *why* Livery can install
schedules and run real AI-to-AI debate, and why Switchboard's versions of
both stop one step short (render-only scheduling, persona-based debate)
rather than pretend to be the same thing. Everything else here is either
matched -- Talk mode, durable dispatch attempts, notifications -- or
ahead: automatic instead of manual routing, self-correcting instead of
static (`memory/routing_corrections.jsonl`), and auditable (`routing` on
every ticket) in ways a plain `assignee` field never was.
