---
id: incident-postmortem-agent
name: Incident Postmortem Agent
repo: https://github.com/PlainJane20/incident-postmortem-agent
invoke: "python run_postmortem.py"
tags: [incident, postmortem, blameless, slack, jira, retrospective]
risk_tier: low
---

Drafts grounded, blameless postmortems from Slack and Jira evidence, with
adversarial hallucination evaluations against the source threads before a
draft ships. The right agent for "we had an incident, write the retro."
