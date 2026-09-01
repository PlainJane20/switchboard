"""Declarative recurring schedules -- portable markdown, rendered into a
real launchd plist or systemd unit, and (since this module's install/
uninstall functions were added) actually installable -- but never as a
side effect of anything except an explicit `install(..., apply=True)`
call, which the CLI only reaches via `schedule-install --apply`.

Livery's README: "Portable schedules tracked as markdown, installed
explicitly as user-level launchd jobs on macOS or systemd timers on
Linux." `render_*` gives you the "portable markdown" half with zero risk
-- pure string generation, no filesystem writes outside the repo. `install`
gives you the "explicit install" half, gated behind `apply=True` so the
default call is a dry run that reports exactly what it *would* write and
where, matching every other real-side-effect action in this tool
(`dispatch --run`, the risk-tier warning). See ARCHITECTURE.md for why
writing into ~/Library/LaunchAgents is treated as a bigger deal than
dispatching an agent, and gets an extra explicit gate because of it.
"""

from __future__ import annotations

import platform
import re
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from switchboard import frontmatter
from switchboard.models import Schedule

DEFAULT_LAUNCHD_DIR = Path.home() / "Library" / "LaunchAgents"
DEFAULT_SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"

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


class InstallResult(dict):
    """Plain dict subclass so callers can do result['paths'] or
    result.paths-style access without a dedicated model for what's really
    just a summary of files touched -- not state Switchboard reads back."""


def install_launchd(
    schedule: Schedule,
    agent_invoke: str,
    apply: bool = False,
    target_dir: Path = DEFAULT_LAUNCHD_DIR,
    label_prefix: str = "com.switchboard",
) -> InstallResult:
    plist = render_launchd(schedule, agent_invoke, label_prefix=label_prefix)
    target = target_dir / f"{label_prefix}.{schedule.id}.plist"

    if not apply:
        return InstallResult(applied=False, target=target, content=plist, launchctl_ran=False)

    target_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(plist)
    proc = subprocess.run(["launchctl", "load", "-w", str(target)], capture_output=True, text=True)
    return InstallResult(
        applied=True, target=target, content=plist,
        launchctl_ran=True, launchctl_returncode=proc.returncode, launchctl_stderr=proc.stderr,
    )


def uninstall_launchd(
    schedule_id: str,
    apply: bool = False,
    target_dir: Path = DEFAULT_LAUNCHD_DIR,
    label_prefix: str = "com.switchboard",
) -> InstallResult:
    target = target_dir / f"{label_prefix}.{schedule_id}.plist"
    if not apply:
        return InstallResult(applied=False, target=target, existed=target.exists())

    existed = target.exists()
    if existed:
        subprocess.run(["launchctl", "unload", "-w", str(target)], capture_output=True, text=True)
        target.unlink()
    return InstallResult(applied=True, target=target, existed=existed)


def install_systemd(
    schedule: Schedule,
    agent_invoke: str,
    apply: bool = False,
    target_dir: Path = DEFAULT_SYSTEMD_USER_DIR,
) -> InstallResult:
    service, timer = render_systemd(schedule, agent_invoke)
    service_path = target_dir / f"switchboard-{schedule.id}.service"
    timer_path = target_dir / f"switchboard-{schedule.id}.timer"

    if not apply:
        return InstallResult(
            applied=False, service_path=service_path, timer_path=timer_path,
            service_content=service, timer_content=timer, systemctl_ran=False,
        )

    target_dir.mkdir(parents=True, exist_ok=True)
    service_path.write_text(service)
    timer_path.write_text(timer)
    proc = subprocess.run(
        ["systemctl", "--user", "enable", "--now", timer_path.name],
        capture_output=True, text=True,
    )
    return InstallResult(
        applied=True, service_path=service_path, timer_path=timer_path,
        systemctl_ran=True, systemctl_returncode=proc.returncode, systemctl_stderr=proc.stderr,
    )


def uninstall_systemd(
    schedule_id: str, apply: bool = False, target_dir: Path = DEFAULT_SYSTEMD_USER_DIR
) -> InstallResult:
    service_path = target_dir / f"switchboard-{schedule_id}.service"
    timer_path = target_dir / f"switchboard-{schedule_id}.timer"
    if not apply:
        return InstallResult(
            applied=False, service_path=service_path, timer_path=timer_path,
            existed=service_path.exists() or timer_path.exists(),
        )

    existed = service_path.exists() or timer_path.exists()
    if existed:
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", timer_path.name],
            capture_output=True, text=True,
        )
        service_path.unlink(missing_ok=True)
        timer_path.unlink(missing_ok=True)
    return InstallResult(applied=True, existed=existed)


def install_for_current_platform(
    schedule: Schedule, agent_invoke: str, apply: bool = False, target_dir: Optional[Path] = None
) -> InstallResult:
    system = platform.system()
    if system == "Darwin":
        return install_launchd(schedule, agent_invoke, apply=apply, **({"target_dir": target_dir} if target_dir else {}))
    if system == "Linux":
        return install_systemd(schedule, agent_invoke, apply=apply, **({"target_dir": target_dir} if target_dir else {}))
    raise NotImplementedError(
        f"no scheduler install implemented for platform {system!r} "
        f"-- launchd (macOS) and systemd (Linux) are supported."
    )
