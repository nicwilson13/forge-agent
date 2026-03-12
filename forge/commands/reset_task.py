"""forge reset-task command - manually retry a parked task"""
from pathlib import Path
from forge.state import load_state, save_state, TaskStatus


def run_reset_task(project_dir: Path, task_id: str):
    state = load_state(project_dir)
    task = state.find_task(task_id)

    if not task:
        print(f"[forge] Task not found: {task_id}")
        return

    print(f"[forge] Resetting task [{task.id}]: {task.title}")
    task.status = TaskStatus.PENDING
    task.retry_count = 0
    task.park_reason = ""
    save_state(project_dir, state)
    print(f"  Task reset to PENDING. Run `forge run` to retry.")
