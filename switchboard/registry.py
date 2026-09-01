"""Loads the agent registry from agents/*.md.

Registering a new specialist agent is: add one markdown file. No code
change, no restart of anything -- the registry is read fresh every time a
command runs, the same way the ticket board is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from switchboard import frontmatter
from switchboard.models import AgentEntry

DEFAULT_AGENTS_DIR = Path("agents")


def load_agents(agents_dir: Path = DEFAULT_AGENTS_DIR) -> List[AgentEntry]:
    agents = []
    for path in sorted(agents_dir.glob("*.md")):
        meta, body = frontmatter.parse(path.read_text())
        agents.append(AgentEntry(description=body, **meta))
    return agents


def agents_by_id(agents_dir: Path = DEFAULT_AGENTS_DIR) -> Dict[str, AgentEntry]:
    return {a.id: a for a in load_agents(agents_dir)}
