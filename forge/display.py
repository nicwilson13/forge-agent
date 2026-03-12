"""
Display module for Forge.
Centralises all terminal output formatting: headers, progress indicators,
task/phase summaries, and dividers. Keeps run.py focused on logic.
"""

import shutil
import sys
from typing import List


def _supports_unicode() -> bool:
    """Check if stdout encoding supports Unicode box-drawing characters."""
    encoding = getattr(sys.stdout, "encoding", "") or ""
    return encoding.lower().replace("-", "") in (
        "utf8", "utf16", "utf32", "utf8sig",
    )


# Symbols with ASCII fallbacks
_UNICODE = _supports_unicode()
SYM_OK = "\u2713" if _UNICODE else "[OK]"       # ✓
SYM_FAIL = "\u2717" if _UNICODE else "[FAIL]"    # ✗
SYM_WARN = "\u26A0" if _UNICODE else "[WARN]"    # ⚠
HEAVY_CHAR = "\u2550" if _UNICODE else "="        # ═
LIGHT_CHAR = "\u2500" if _UNICODE else "-"        # ─


def _term_width() -> int:
    """Return terminal width with a sensible fallback."""
    return shutil.get_terminal_size((64, 24)).columns


def _format_duration(seconds: float) -> str:
    """
    Format a duration in seconds to a human-readable string.

    Args:
        seconds: Duration in seconds.

    Returns:
        Formatted string: '48s', '3m 12s', or '1h 24m'.
    """
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m {s}s"
    h, remainder = divmod(seconds, 3600)
    m = remainder // 60
    return f"{h}h {m}m"


def divider(style: str = "heavy") -> str:
    """
    Return a full-width divider string.

    Args:
        style: 'heavy' for ═ lines, 'light' for ─ lines.

    Returns:
        A divider string matching the terminal width.
    """
    char = HEAVY_CHAR if style == "heavy" else LIGHT_CHAR
    return char * _term_width()


def print_forge_header(project_name: str) -> None:
    """Print the Forge startup banner."""
    d = divider("heavy")
    print(f"\n{d}")
    print(f"  FORGE - Autonomous Development Agent")
    print(f"  Project: {project_name}")
    print(d)


def print_phase_header(phase_title: str, phase_index: int,
                       total_phases: int) -> None:
    """
    Print the header when starting a new phase.

    Args:
        phase_title: Title of the phase.
        phase_index: Zero-based index of the current phase.
        total_phases: Total number of phases.
    """
    d = divider("heavy")
    print(f"\n{d}")
    print(f"  Phase {phase_index + 1} of {total_phases}: {phase_title}")
    print(d)


def print_task_header(task_title: str, task_index: int,
                      total_tasks: int, phase_title: str,
                      retry_count: int = 0) -> None:
    """
    Print the header before a task executes.

    Args:
        task_title: Title of the task.
        task_index: Zero-based index of the task within its phase.
        total_tasks: Total number of tasks in the phase.
        phase_title: Title of the current phase.
        retry_count: If > 0, shows retry indicator instead of task number.
    """
    d = divider("heavy")
    if retry_count > 0:
        label = f"  Retry {retry_count} | Task {task_index + 1} of {total_tasks}: {task_title}"
    else:
        label = f"  Task {task_index + 1} of {total_tasks}: {task_title}"
    print(f"\n{d}")
    print(label)
    print(d)


def print_task_success(duration_seconds: float, tasks_done: int,
                       total_tasks: int, phase_title: str) -> None:
    """
    Print the success line after a task completes.

    Args:
        duration_seconds: How long the task took.
        tasks_done: Number of tasks completed in this phase so far.
        total_tasks: Total tasks in the phase.
        phase_title: Title of the current phase.
    """
    dur = _format_duration(duration_seconds)
    print(f"\n  {SYM_OK} Task complete  |  {dur}  |  {phase_title}: {tasks_done}/{total_tasks} tasks done")
    print(f"  {divider('light')}")


def print_task_failure(retry_count: int, max_retries: int,
                       reason: str) -> None:
    """
    Print the failure line when a task fails QA.

    Args:
        retry_count: Current retry attempt number.
        max_retries: Maximum retries before parking.
        reason: Short description of why the task failed.
    """
    reason_line = reason[:120] if reason else "Unknown failure"
    print(f"\n  {SYM_FAIL} Task failed (attempt {retry_count}/{max_retries})")
    print(f"  Reason: {reason_line}")
    print(f"  Retrying with updated context...")
    print(f"  {divider('light')}")


def print_task_parked(task_id: str) -> None:
    """
    Print the parked line when a task is sent to NEEDS_HUMAN.

    Args:
        task_id: The ID of the parked task.
    """
    print(f"\n  {SYM_WARN} Task parked - requires human input")
    print(f"  See NEEDS_HUMAN.md for details (task {task_id})")
    print(f"  {divider('light')}")


def print_phase_complete(phase_title: str, tasks_done: int,
                         tasks_parked: int,
                         duration_seconds: float,
                         next_phase_title: str | None) -> None:
    """
    Print the phase completion summary.

    Args:
        phase_title: Title of the completed phase.
        tasks_done: Number of tasks completed.
        tasks_parked: Number of tasks parked.
        duration_seconds: Total phase duration.
        next_phase_title: Title of the next phase, or None if last.
    """
    d = divider("heavy")
    dur = _format_duration(duration_seconds)
    print(f"\n{d}")
    print(f"  {SYM_OK} Phase Complete: {phase_title}")
    print(f"  Tasks: {tasks_done} done, {tasks_parked} parked")
    print(f"  Duration: {dur}")
    if next_phase_title:
        print(f"  Next: {next_phase_title}")
    print(d)


def print_build_complete(project_name: str, phases_done: int,
                         tasks_done: int,
                         duration_seconds: float) -> None:
    """
    Print the final build complete banner.

    Args:
        project_name: Name of the project.
        phases_done: Number of phases completed.
        tasks_done: Total tasks completed.
        duration_seconds: Total build duration.
    """
    d = divider("heavy")
    dur = _format_duration(duration_seconds)
    print(f"\n{d}")
    print(f"  {SYM_OK} BUILD COMPLETE")
    print(f"  Project: {project_name}")
    print(f"  Phases completed: {phases_done}")
    print(f"  Tasks completed: {tasks_done}")
    print(f"  Total duration: {dur}")
    print(d)


def print_checkin_prompt(tasks_since_checkin: int) -> None:
    """
    Print the check-in gate message.

    Args:
        tasks_since_checkin: Number of tasks completed since last check-in.
    """
    d = divider("heavy")
    print(f"\n{d}")
    print(f"  CHECK-IN POINT ({tasks_since_checkin} tasks completed)")
    print(f"  Run `forge checkin` to review progress and NEEDS_HUMAN items.")
    print(f"  Press Enter to continue autonomously, or Ctrl+C to pause.")
    print(d)


def print_dry_run_plan(phases: List) -> None:
    """
    Print the dry run phase plan.

    Args:
        phases: List of Phase objects to display.
    """
    d = divider("heavy")
    print(f"\n{d}")
    print(f"  DRY RUN PLAN")
    print(d)
    for i, phase in enumerate(phases):
        print(f"\n  Phase {i + 1}: {phase.title}")
        print(f"    {phase.description[:200]}")
    print(f"\n{d}")
