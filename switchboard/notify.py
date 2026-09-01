"""Best-effort notifications when a ticket needs a human -- unrouted after
routing, or a dispatch attempt failed.

Livery has a specific Telegram integration. This is deliberately more
generic -- a local desktop notification (macOS only; a silent no-op
elsewhere rather than an error) plus an optional webhook POST to
whatever URL is set in SWITCHBOARD_WEBHOOK_URL, which works equally for
Slack, Discord, Telegram, or a plain HTTP endpoint, since none of them
need more than 'send this JSON somewhere.' Never raises: a notification
failing should never be the reason a routing or dispatch command exits
non-zero.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request


def notify(message: str, title: str = "Switchboard") -> None:
    _desktop_notify(message, title)
    _webhook_notify(message, title)


def _desktop_notify(message: str, title: str) -> None:
    if sys.platform != "darwin":
        return
    script = f'display notification {json.dumps(message)} with title {json.dumps(title)}'
    try:
        subprocess.run(["osascript", "-e", script], check=False, timeout=5)
    except Exception:
        pass


def _webhook_notify(message: str, title: str) -> None:
    url = os.environ.get("SWITCHBOARD_WEBHOOK_URL")
    if not url:
        return
    payload = json.dumps({"text": f"{title}: {message}"}).encode()
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(request, timeout=5)
    except Exception:
        pass
