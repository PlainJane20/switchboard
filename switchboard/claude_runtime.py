"""The one real, verified live-runtime adapter: `claude -p`, spawned as an
actual Claude Code session with tool access -- not a canned script.

This is Switchboard's answer to Livery's biggest structural advantage
(agents are live conversational processes, not command strings). Rather
than implement five adapters against five CLIs' documented-but-unverified
behavior, this implements one, checked against the real installed CLI
(`claude --help`) and one real invocation before a single line of parsing
logic was written -- see ARCHITECTURE.md for the actual sample JSON that
shaped this module's fields.

Deliberately NOT using --bare: on this machine, --bare requires
ANTHROPIC_API_KEY-only auth and fails outright under managed/enterprise
settings that pin first-party OAuth login. A "verified" adapter that only
works in one auth configuration isn't actually verified for general use.

Deliberately NOT using --dangerously-skip-permissions: that flag disables
Claude Code's own safety checks entirely. An unattended dispatch tool
should never need that -- --permission-mode plus an explicit --allowedTools
allowlist is the scoped equivalent, and is what this module uses.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel


class ClaudeCodeResult(BaseModel):
    is_error: bool
    result_text: str
    session_id: Optional[str] = None
    cost_usd: Optional[float] = None
    returncode: int
    pid: int


def _parse_result_json(stdout: str) -> dict:
    """`claude -p --output-format json` should emit one JSON object on
    stdout. Defensively falls back to the last non-empty line in case a
    warning (e.g. an MCP-server-blocked notice, observed for real during
    development) gets interleaved -- but never silently swallows a
    genuinely unparseable response."""
    stdout = stdout.strip()
    if not stdout:
        raise ValueError("claude -p produced no stdout at all")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        last_line = stdout.splitlines()[-1]
        try:
            return json.loads(last_line)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"could not parse claude -p output as JSON. Raw stdout:\n{stdout[:500]}"
            ) from e


def run_session(
    prompt: str,
    cwd: Optional[str] = None,
    system_prompt: Optional[str] = None,
    permission_mode: str = "dontAsk",
    allowed_tools: Optional[List[str]] = None,
    timeout: int = 600,
    pid_callback=None,
) -> ClaudeCodeResult:
    """Spawn one real `claude -p` session and block until it finishes.

    `pid_callback`, if given, is invoked with the child PID the instant
    the process starts -- the same "capture PID at start, not after"
    pattern dispatch.py already uses for command-runtime agents, so
    DispatchAttempt tracking is uniform across both runtimes.
    """
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--permission-mode", permission_mode]
    if system_prompt:
        cmd += ["--append-system-prompt", system_prompt]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]

    process = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    if pid_callback:
        pid_callback(process.pid)

    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        raise TimeoutError(
            f"claude -p session exceeded {timeout}s and was killed. stderr: {stderr[:500]}"
        )

    if process.returncode != 0:
        raise RuntimeError(
            f"claude -p exited {process.returncode}. stderr: {stderr[:1000] or '(empty)'}"
        )

    data = _parse_result_json(stdout)
    return ClaudeCodeResult(
        is_error=data.get("is_error", False),
        result_text=data.get("result", ""),
        session_id=data.get("session_id"),
        cost_usd=data.get("total_cost_usd"),
        returncode=process.returncode,
        pid=process.pid,
    )
