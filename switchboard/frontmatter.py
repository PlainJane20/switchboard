"""Minimal YAML-frontmatter markdown parsing.

Deliberately hand-rolled instead of pulling in `python-frontmatter`: the
format is `---\\nYAML\\n---\\nbody`, and that's genuinely all Switchboard
needs. One clear function beats a dependency for three lines of logic.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

import yaml

_DELIM = "---"


def parse(text: str) -> Tuple[Dict[str, Any], str]:
    """Split a frontmatter file into (metadata dict, body text)."""
    if not text.startswith(_DELIM):
        raise ValueError("expected file to start with '---' frontmatter delimiter")
    parts = text.split(_DELIM, 2)
    if len(parts) < 3:
        raise ValueError("frontmatter block is not closed with a second '---'")
    _, raw_meta, body = parts
    meta = yaml.safe_load(raw_meta) or {}
    return meta, body.strip("\n")


def render(meta: Dict[str, Any], body: str) -> str:
    """Serialize (metadata, body) back into a frontmatter markdown file."""
    raw_meta = yaml.safe_dump(meta, sort_keys=False, default_flow_style=False)
    return f"{_DELIM}\n{raw_meta}{_DELIM}\n\n{body.strip()}\n"
