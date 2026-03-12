"""
forge dashboard command.

Starts the dashboard in read-only mode to review the last build's logs
and state without running a new build.
"""

import time
from pathlib import Path

from forge.dashboard import start_dashboard, update_dashboard_state


def run_dashboard_command(project_dir: Path, port: int = 3333) -> None:
    """
    Start dashboard in read-only mode.
    Reads from .forge/state.json and .forge/build_log.jsonl.
    Blocks until Ctrl+C.
    """
    print(f"\n  Dashboard: http://localhost:{port}")
    print("  (read-only mode - showing last build)")
    print("  Press Ctrl+C to stop\n")

    thread = start_dashboard(project_dir, port)
    if thread is None:
        return

    # Load existing state for display
    try:
        from forge.state import load_state
        state = load_state(project_dir)
        if state.phases:
            total_tasks = sum(len(p.tasks) for p in state.phases)
            tasks_done = sum(
                1 for p in state.phases for t in p.tasks if t.status == "done"
            )
            current_phase = state.current_phase
            update_dashboard_state({
                "project_name": state.project_name or project_dir.name,
                "current_phase": state.current_phase_index + 1,
                "total_phases": len(state.phases),
                "phase_title": current_phase.title if current_phase else "",
                "tasks_done": tasks_done,
                "total_tasks": total_tasks,
                "task_status": "read-only",
            })
    except Exception:
        pass

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n  Dashboard stopped.")
