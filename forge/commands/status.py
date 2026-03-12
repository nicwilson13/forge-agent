"""forge status command"""
from pathlib import Path
from forge.state import load_state, TaskStatus, PhaseStatus
from forge import git_utils


def run_status(project_dir: Path):
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
                    "interrupted": "↺", "commit_pending": "⏳"}.get(task.status, "?")
            print(f"         {icon} [{task.id}] {task.title}")

    parked = state.all_parked_tasks()
    if parked:
        print(f"\n  ⚠  {len(parked)} task(s) parked in NEEDS_HUMAN.md")

    if git_utils.is_git_repo(project_dir):
        commits = git_utils.recent_commits(project_dir)
        if commits:
            print(f"\n  Recent commits:")
            for c in commits[:3]:
                print(f"    {c}")

    print()
