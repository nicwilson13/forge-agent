"""
NEEDS_HUMAN Manager
Maintains NEEDS_HUMAN.md in the project root as a parking lot
for tasks that require human input or are stuck.
"""

from datetime import datetime
from pathlib import Path
from typing import List

from forge.state import Task


HEADER = """# NEEDS_HUMAN.md

This file is maintained by Forge. It contains tasks and items that require
human attention before the agent can proceed.

**How to use:**
1. Review each item below
2. Provide answers/decisions in the "Resolution" field
3. Run `forge checkin` to process your responses and unpark tasks
4. Forge will resume automatically after checkin

---

"""


def _path(project_dir: Path) -> Path:
    return project_dir / "NEEDS_HUMAN.md"


def read_raw(project_dir: Path) -> str:
    p = _path(project_dir)
    return p.read_text() if p.exists() else ""


def append_item(project_dir: Path, task: Task, reason: str):
    """Add a new item to NEEDS_HUMAN.md."""
    p = _path(project_dir)

    if not p.exists():
        p.write_text(HEADER)

    content = p.read_text()
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    entry = f"""
## [{task.id}] {task.title}
**Added:** {timestamp}
**Reason:** {reason}

**Task description:**
{task.description}

**Resolution:** *(fill this in, then run `forge checkin`)*

---
"""
    p.write_text(content + entry)
    print(f"  [needs_human] Parked task [{task.id}]: {task.title}")


def append_note(project_dir: Path, note: str):
    """Add a general note (non-task item) to NEEDS_HUMAN.md."""
    p = _path(project_dir)
    if not p.exists():
        p.write_text(HEADER)
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    content = p.read_text()
    entry = f"""
## [note] General Note - {timestamp}
{note}

---
"""
    p.write_text(content + entry)


def parse_resolutions(project_dir: Path) -> dict:
    """
    Parse NEEDS_HUMAN.md and return a dict of task_id -> resolution text
    for any items where the human has filled in a resolution.
    """
    content = read_raw(project_dir)
    resolutions = {}

    # Simple parser: look for ## [task_id] sections with filled Resolution fields
    import re
    blocks = re.split(r"\n## \[", content)
    for block in blocks[1:]:  # skip header
        # Extract task ID
        id_match = re.match(r"([^\]]+)\]", block)
        if not id_match:
            continue
        task_id = id_match.group(1)
        if task_id == "note":
            continue

        # Extract resolution
        res_match = re.search(
            r"\*\*Resolution:\*\*\s*\*?\(fill this in.*?\)\*?\s*(.*?)(?:\n---|\Z)",
            block,
            re.DOTALL,
        )
        if res_match:
            resolution = res_match.group(1).strip()
            # Only count it if the human actually wrote something
            if resolution and resolution != "*(fill this in, then run `forge checkin`)*":
                resolutions[task_id] = resolution

    return resolutions


def mark_resolved(project_dir: Path, task_id: str):
    """Mark an item as resolved in NEEDS_HUMAN.md (strike through the header)."""
    p = _path(project_dir)
    if not p.exists():
        return
    content = p.read_text()
    content = content.replace(
        f"## [{task_id}]",
        f"## ~~[{task_id}]~~ RESOLVED"
    )
    p.write_text(content)
