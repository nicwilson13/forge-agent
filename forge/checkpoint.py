"""
Checkpoint manager for Forge.

Handles atomic state saves, interrupt detection on startup,
and resume logic for tasks that were in progress when Forge stopped.

All state mutations go through this module to ensure atomicity.
The pattern: write to .forge/state.tmp, then rename to .forge/state.json.
Rename is atomic on all major OS platforms.
"""

import json
import os
import shutil
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import List

from forge.state import ForgeState, Task, TaskStatus


def _state_path(project_dir: Path) -> Path:
    return project_dir / ".forge" / "state.json"


def _tmp_path(project_dir: Path) -> Path:
    return project_dir / ".forge" / "state.tmp"


def atomic_save(project_dir: Path, state: ForgeState) -> None:
    """
    Save state atomically using write-to-temp-then-rename.

    Writes to .forge/state.tmp first, then renames to .forge/state.json.
    This ensures the state file is never partially written.
    If the write fails, the existing state.json is untouched.
    """
    state.last_updated = datetime.utcnow().isoformat()
    forge_dir = project_dir / ".forge"
    forge_dir.mkdir(parents=True, exist_ok=True)

    tmp = _tmp_path(project_dir)
    target = _state_path(project_dir)

    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(state), f, indent=2, default=str)
        # Back up current state.json before overwriting
        if target.exists():
            try:
                shutil.copy2(target, target.with_suffix(".json.bak"))
            except OSError:
                pass  # backup failure is non-fatal
        tmp.replace(target)
    finally:
        # Always clean up tmp if it still exists (rename failed)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def mark_task_started(project_dir: Path, state: ForgeState,
                      task: Task) -> None:
    """
    Mark a task as IN_PROGRESS and save checkpoint.

    Sets task.status = IN_PROGRESS, records checkpoint timestamp,
    and saves state atomically.
    """
    task.status = TaskStatus.IN_PROGRESS
    task.checkpoint_at = datetime.utcnow().isoformat()
    atomic_save(project_dir, state)


def mark_task_interrupted(project_dir: Path, state: ForgeState,
                          task: Task, reason: str = "interrupt") -> None:
    """
    Mark a task as INTERRUPTED and save checkpoint.

    Called when Forge is interrupted (Ctrl+C, signal, timeout).
    """
    task.status = TaskStatus.INTERRUPTED
    task.interrupt_reason = reason
    task.checkpoint_at = datetime.utcnow().isoformat()
    atomic_save(project_dir, state)


def mark_task_commit_pending(project_dir: Path, state: ForgeState,
                             task: Task) -> None:
    """
    Mark a task as COMMIT_PENDING and save checkpoint.

    Called when a task passes QA but the git commit/push fails.
    The task output is good - we just need to retry the commit.
    """
    task.status = TaskStatus.COMMIT_PENDING
    task.checkpoint_at = datetime.utcnow().isoformat()
    atomic_save(project_dir, state)


def detect_interrupted_tasks(state: ForgeState) -> List[Task]:
    """
    Find all tasks that were interrupted on a previous run.

    Returns tasks with status IN_PROGRESS, INTERRUPTED, or COMMIT_PENDING.
    These are tasks that need special handling on resume.
    """
    interrupted = []
    for phase in state.phases:
        for task in phase.tasks:
            if task.status in (
                TaskStatus.IN_PROGRESS,
                TaskStatus.INTERRUPTED,
                TaskStatus.COMMIT_PENDING,
            ):
                interrupted.append(task)
    return interrupted


def resume_message(interrupted_tasks: List[Task]) -> str:
    """
    Generate a human-readable resume message.

    Returns a string describing what was interrupted and what
    will happen next. Used for display on startup.
    """
    if not interrupted_tasks:
        return ""

    lines = ["  [forge] Resuming from interrupted task..."]
    for task in interrupted_tasks:
        if task.status == TaskStatus.COMMIT_PENDING:
            lines.append(
                f'  [forge] Task "{task.title}" completed but not committed - retrying commit.'
            )
        else:
            reason = f" ({task.interrupt_reason})" if task.interrupt_reason else ""
            lines.append(
                f'  [forge] Task "{task.title}" was interrupted{reason} - retrying.'
            )
    return "\n".join(lines)
