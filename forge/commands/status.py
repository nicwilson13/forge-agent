"""forge status command"""
from pathlib import Path
from forge.state import load_state, TaskStatus, PhaseStatus
from forge import git_utils
from forge.memory import count_entries
from forge.cost_tracker import CostTracker
from forge.build_logger import read_log


def run_status(project_dir: Path, show_cost: bool = False,
               show_log: bool = False, log_tail: int = 20,
               show_health: bool = False):
    state = load_state(project_dir)

    if not state.initialized:
        print("[forge] Not initialized. Run `forge run` to start.")
        return

    print(f"\n{'='*60}")
    print(f"  FORGE STATUS - {project_dir.name}")
    print(f"{'='*60}")
    print(f"  Tasks completed : {state.tasks_completed}")
    print(f"  Phase progress  : {state.current_phase_index}/{len(state.phases)}")

    for i, phase in enumerate(state.phases):
        marker = ">" if i == state.current_phase_index else \
                 "✓" if phase.status == PhaseStatus.DONE else " "
        done = sum(1 for t in phase.tasks if t.status == TaskStatus.DONE)
        total = len(phase.tasks)
        print(f"\n  [{marker}] Phase {i+1}: {phase.title}")
        if total:
            print(f"       Tasks: {done}/{total} done")
        for task in phase.tasks:
            icon = {"done": "✓", "parked": "⚠", "failed": "✗",
                    "in_progress": "→", "pending": "·",
                    "interrupted": "↺", "commit_pending": "⏳",
                    "waiting": "⏸"}.get(task.status, "?")
            if task.status == TaskStatus.WAITING and task.depends_on:
                deps_done = sum(
                    1 for d in task.depends_on
                    if any(t.id == d and t.status == TaskStatus.DONE
                           for t in phase.tasks)
                )
                print(f"         {icon} [{task.id}] {task.title}"
                      f"  (waiting: {deps_done}/{len(task.depends_on)} deps done)")
            else:
                print(f"         {icon} [{task.id}] {task.title}")

    # Memory summary
    mem = count_entries(project_dir)
    total_mem = mem["decisions"] + mem["patterns"] + mem["failures"]
    if total_mem > 0:
        print(f"\n  Memory: {mem['decisions']} decisions · {mem['patterns']} patterns · {mem['failures']} failures recorded")

    parked = state.all_parked_tasks()
    if parked:
        print(f"\n  ⚠  {len(parked)} task(s) parked in NEEDS_HUMAN.md")

    if git_utils.is_git_repo(project_dir):
        commits = git_utils.recent_commits(project_dir)
        if commits:
            print(f"\n  Recent commits:")
            for c in commits[:3]:
                print(f"    {c}")

    # Cost report
    if show_cost:
        tracker = CostTracker(project_dir)
        tracker.load_from_log()
        print(tracker.format_cost_report(state))

    # Health report
    if show_health:
        from forge.health import compute_health_report, format_health_report
        events = read_log(project_dir, event_filter="session_started", limit=1)
        session_id = events[-1]["session"] if events else "unknown"
        report = compute_health_report(project_dir, session_id)
        print(format_health_report(report))

    # Build log (after cost report, before final newline)
    if show_log:
        records = read_log(project_dir, limit=log_tail)
        if not records:
            print("\n  No build log yet. Run `forge run` to generate events.")
        else:
            print(f"\n  Recent Build Events (last {log_tail})")
            print("  " + "-" * 58)
            for r in records:
                ts = r.get("ts", "")
                # Extract HH:MM:SS from ISO timestamp
                time_part = ts[11:19] if len(ts) >= 19 else ts[:8]
                event = r.get("event", "unknown")
                detail = _format_log_detail(event, r)
                print(f"  {time_part}  {event:<20s} {detail}")

    print()


def _format_log_detail(event: str, record: dict) -> str:
    """Format event-specific detail for the log table."""
    if event == "session_started":
        return f"Project: {record.get('project_name', '?')}  Phases: {record.get('phase_count', '?')}"
    if event == "session_ended":
        return f"Tasks: {record.get('tasks_completed', 0)}  Cost: ${record.get('total_cost', 0):.2f}"
    if event == "phase_started":
        return f"Phase {(record.get('phase', 0) or 0) + 1}: {record.get('phase_title', '')}"
    if event == "phase_completed":
        return f"{record.get('phase_title', '')}  {record.get('task_count', 0)} tasks"
    if event == "phase_failed":
        return record.get("phase_title", "")
    if event == "task_started":
        return record.get("task_title", "")
    if event == "task_completed":
        dur = int(record.get("duration_secs", 0))
        cost = record.get("cost", 0)
        t_in = record.get("tokens_in", 0)
        t_out = record.get("tokens_out", 0)
        return f"{dur}s  ${cost:.3f}  {t_in:,}/{t_out:,} tokens"
    if event == "task_failed":
        return f"{record.get('task_title', '')} (retry {record.get('retry_count', 0)})"
    if event == "task_parked":
        return record.get("task_title", "")
    if event in ("qa_passed", "qa_failed"):
        return record.get("task_title", "")
    if event == "git_committed":
        h = record.get("commit_hash", "")[:7]
        return f"{h}: {record.get('message_preview', '')}"
    if event == "rate_limit_hit":
        return f"Waiting {record.get('wait_secs', 0)}s (attempt {record.get('attempt', 0)})"
    if event == "fatal_error":
        return f"{record.get('error_type', '')}: {record.get('message', '')[:60]}"
    if event == "memory_recorded":
        return f"{record.get('memory_type', '')}: {record.get('title', '')}"
    return ""
