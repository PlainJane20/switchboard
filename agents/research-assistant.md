---
id: research-assistant
name: Research Assistant
repo: https://github.com/PlainJane20/switchboard
runtime: claude_code
cwd: null
allowed_tools: [Read, Grep, Glob]
tags: [research, general, open-ended, drafting, summarization]
risk_tier: medium
---

A live Claude Code session for open-ended research, drafting, or
summarization tickets that don't fit any narrow specialist -- the one
agent in this registry that isn't a fixed script. Restricted to read-only
tools (Read, Grep, Glob) by default: it can look things up and reason
about them, but not edit files or run arbitrary commands. Unlike every
other registered agent, this one is a real, live session, not a one-shot
invoke command -- see ARCHITECTURE.md for why it's the only one.
