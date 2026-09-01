"""Declarative recurring schedules -- portable markdown, rendered into a
real launchd plist or systemd unit on request, never installed
automatically.

Livery's README: "Portable schedules tracked as markdown, installed
explicitly as user-level launchd jobs on macOS or systemd timers on
Linux." Switchboard matches the "portable markdown, explicit install"
half of that. It deliberately does *not* implement the "installed" half
as an automatic action -- writing into ~/Library/LaunchAgents or a
systemd user directory is a real, somewhat hard-to-notice change to the
machine's actual scheduler state, categorically different from dispatching
a registered agent (this tool's actual job). `schedule render` prints
exactly what would need to go where; a human copies it into place. See
ARCHITECTURE.md for the full reasoning.
"""

from __future__ import annotations

import platform
import re
from pathlib import Path
from typing import List, Tuple

from switchboard import frontmatter
from switchboard.models import Schedule

DEFAULT_SCHEDULES_DIR = Path("schedules")

_CRON_RE = re.compile(r"^(\d{1,2})\s+([\d,]+)\s+\*\s+\*\s+\*$")


def parse_daily_cron(cron: str) -> Tuple[int, List[int]]:
    """Parse the one cron shape this tool supports: 'M H1,H2,... * * *'
    -- a fixed minute, one or more fixed hours, every day. Raises
    ValueError with a clear message for anything else, rather than
    silently misinterpreting a real cron expression this doesn't
    actually implement."""
    match = _CRON_RE.match(cron.strip())
    if not match:
        raise ValueError(
            f"unsupported cron expression {cron!r} -- this tool only "
            f"supports 'MINUTE HOUR[,HOUR...] * * *' (fixed time(s), "
            f"every day). Day-of-week/month/step expressions aren't "
            f"implemented; see ARCHITECTURE.md for why."
        )
    minute = int(match.group(1))
    hours = [int(h) for h in match.group(2).split(",")]
    if not (0 <= minute <= 59) or any(not (0 <= h <= 23) for h in hours):
        raise ValueError(f"minute/hour out of range in cron expression {cron!r}")
    return minute, hours


def load_schedules(schedules_dir: Path = DEFAULT_SCHEDULES_DIR) -> List[Schedule]:
    schedules = []
    for path in sorted(schedules_dir.glob("*.md")):
        meta, _ = frontmatter.parse(path.read_text())
        schedules.append(Schedule(**meta))
    return schedules


def new_schedule(schedule: Schedule, schedules_dir: Path = DEFAULT_SCHEDULES_DIR) -> Path:
    schedules_dir.mkdir(parents=True, exist_ok=True)
    path = schedules_dir / f"{schedule.id}.md"
    meta = schedule.model_dump(mode="json")
    path.write_text(frontmatter.render(meta, schedule.description))
    return path


def _resolve_command(schedule: Schedule, agent_invoke: str) -> str:
    if schedule.command:
        return schedule.command
    # Scheduled runs aren't tied to one ticket -- strip the placeholder
    # rather than fail on .format() with a missing key.
    return agent_invoke.replace("{ticket_path}", "").strip()


def render_launchd(schedule: Schedule, agent_invoke: str, label_prefix: str = "com.switchboard") -> str:
    minute, hours = parse_daily_cron(schedule.cron)
    command = _resolve_command(schedule, agent_invoke)
    intervals = "\n".join(
        f"""        <dict>
            <key>Hour</key><integer>{h}</integer>
            <key>Minute</key><integer>{minute}</integer>
        </dict>"""
        for h in hours
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{label_prefix}.{schedule.id}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/sh</string>
        <string>-c</string>
        <string>{command}</string>
    </array>
    <key>StartCalendarInterval</key>
    <array>
{intervals}
    </array>
</dict>
</plist>
"""


def render_systemd(schedule: Schedule, agent_invoke: str) -> Tuple[str, str]:
    """Returns (service_unit, timer_unit) -- systemd needs both."""
    minute, hours = parse_daily_cron(schedule.cron)
    command = _resolve_command(schedule, agent_invoke)
    on_calendar_lines = "\n".join(f"OnCalendar=*-*-* {h:02d}:{minute:02d}:00" for h in hours)

    service = f"""[Unit]
Description=Switchboard schedule: {schedule.description}

[Service]
Type=oneshot
ExecStart=/bin/sh -c '{command}'
"""
    timer = f"""[Unit]
Description=Timer for switchboard-{schedule.id}

[Timer]
{on_calendar_lines}
Persistent=true

[Install]
WantedBy=timers.target
"""
    return service, timer


def render_for_current_platform(schedule: Schedule, agent_invoke: str) -> str:
    system = platform.system()
    if system == "Darwin":
        return render_launchd(schedule, agent_invoke)
    if system == "Linux":
        service, timer = render_systemd(schedule, agent_invoke)
        return f"# {schedule.id}.service\n{service}\n# {schedule.id}.timer\n{timer}"
    raise NotImplementedError(
        f"no scheduler rendering implemented for platform {system!r} "
        f"-- launchd (macOS) and systemd (Linux) are supported."
    )
