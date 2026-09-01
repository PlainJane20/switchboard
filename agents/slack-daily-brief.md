---
id: slack-daily-brief
name: Slack Daily Brief
repo: https://github.com/PlainJane20/slack-daily-brief
invoke: "bash run_daily_brief.sh"
tags: [slack, daily-brief, decisions, blockers, follow-through, reporting]
risk_tier: medium
---

Eval-tested Slack briefing agent that summarizes decisions, actions, and
blockers, and tracks day-over-day follow-through. Normally runs on a
schedule (`launchd`), not per-ticket -- registered here mainly for
on-demand "give me today's brief now" tickets. Posts to Slack, so it's
medium risk, not low.
