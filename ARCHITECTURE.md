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
