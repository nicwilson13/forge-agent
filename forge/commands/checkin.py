"""forge checkin command - interactive resolution of NEEDS_HUMAN items"""
from pathlib import Path
from forge.state import load_state, save_state, TaskStatus
from forge import needs_human


def run_checkin(project_dir: Path):
    state = load_state(project_dir)
    parked = state.all_parked_tasks()

    if not parked:
        print("[forge] No parked tasks. Nothing to check in.")
        return

    print(f"\n[forge] {len(parked)} parked task(s) in NEEDS_HUMAN.md\n")

    resolutions = needs_human.parse_resolutions(project_dir)

    resolved_count = 0
    for task in parked:
        if task.id in resolutions:
            resolution = resolutions[task.id]
            print(f"  Resolving [{task.id}]: {task.title}")
            print(f"  Resolution: {resolution[:100]}")

            # Inject resolution into task notes and reset to PENDING
            task.notes = f"Human resolution: {resolution}\n\nOriginal notes: {task.notes}"
            task.status = TaskStatus.PENDING
            task.park_reason = ""
            task.retry_count = 0
            needs_human.mark_resolved(project_dir, task.id)
            resolved_count += 1

    save_state(project_dir, state)

    if resolved_count:
        print(f"\n[forge] {resolved_count} task(s) unparked. Run `forge run` to continue.")
    else:
        print("[forge] No resolutions found in NEEDS_HUMAN.md yet.")
        print("  Fill in the 'Resolution' fields in NEEDS_HUMAN.md, then run `forge checkin` again.")
