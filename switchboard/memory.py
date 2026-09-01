"""Routing-correction memory: the one thing this router does that Livery's
manual `assignee` field has no equivalent of.

Every time a human reroutes a ticket (`switchboard reroute`), that
correction is appended -- never rewritten -- to a git-tracked JSONL file,
the same append-only convention agent-control-tower uses for its audit
log. The AI router then reads recent corrections back in as few-shot
context on future ambiguous tickets (see router.py). This isn't model
fine-tuning or embeddings -- it's plain prompt context sourced from git
history, which is exactly why it's inspectable: `git log -p
memory/routing_corrections.jsonl` *is* the training signal, in full,
forever.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from switchboard.models import Correction

DEFAULT_CORRECTIONS_PATH = Path("memory/routing_corrections.jsonl")


def append_correction(
    correction: Correction, path: Path = DEFAULT_CORRECTIONS_PATH
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(correction.model_dump_json() + "\n")


def load_corrections(
    path: Path = DEFAULT_CORRECTIONS_PATH, limit: Optional[int] = None
) -> List[Correction]:
    if not path.exists():
        return []
    corrections = [
        Correction(**json.loads(line))
        for line in path.read_text().splitlines()
        if line.strip()
    ]
    return corrections[-limit:] if limit else corrections
