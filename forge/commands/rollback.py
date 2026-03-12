"""
forge rollback - Roll back to a previous completed phase.

Uses git phase tags created at phase completion to identify rollback
points. Rolls back both git history and Forge build state atomically.
Requires explicit confirmation (type the phase name) before executing.
"""

import sys
from pathlib import Path

from forge import git_utils, checkpoint
from forge.display import SYM_OK, SYM_FAIL, divider
from forge.state import ForgeState, PhaseStatus, load_state


def _make_tag_name(phase_title: str) -> str:
    """Reproduce the tag naming logic from git_utils.tag_phase()."""
    return "phase-" + phase_title.lower().replace(" ", "-").replace(":", "")[:40]


def _extract_phase_name(phase_title: str) -> str:
    """Extract the part after the colon for confirmation matching.

    'Phase 2: Core Features' -> 'core features'
    'Core Features' -> 'core features'
    """
    if ":" in phase_title:
        return phase_title.split(":", 1)[1].strip().lower()
    return phase_title.strip().lower()


def _get_rollback_points(project_dir: Path, state: ForgeState) -> list[dict]:
    """Return a list of dicts describing each phase as a rollback point."""
    points = []
    for i, phase in enumerate(state.phases):
        tag_name = _make_tag_name(phase.title)
        commit_hash = git_utils.get_tag_commit(project_dir, tag_name)
        is_complete = phase.status == PhaseStatus.DONE
        is_current = i == state.current_phase_index
        is_pending = i > state.current_phase_index

        points.append({
            "phase_index": i,
            "phase_number": i + 1,
            "phase_title": phase.title,
            "tag_name": tag_name,
            "commit_hash": commit_hash,
            "available": is_complete and commit_hash is not None,
            "is_complete": is_complete,
            "is_current": is_current,
            "is_pending": is_pending,
        })
    return points


def _list_rollback_points(project_dir: Path, state: ForgeState) -> None:
    """Display the table of available rollback points."""
    if not state.phases:
        print("\n  No rollback points available yet.")
        print("  Run `forge run` to start building phases.\n")
        return

    points = _get_rollback_points(project_dir, state)
    has_any = any(p["available"] for p in points)

    if not has_any:
        print("\n  No rollback points available yet.")
        print("  Phase tags are created when each phase passes QA.")
        print("  Complete at least one phase before rolling back.\n")
        return

    d = divider("light")
    print(f"\n  Available rollback points:")
    print(f"  {d}")
    print()

    for p in points:
        num = f"Phase {p['phase_number']}"
        if p["is_complete"]:
            tag_str = p["tag_name"]
            hash_str = p["commit_hash"] or "?"
            print(f"  {num:<10} {SYM_OK} complete   {tag_str:<40} {hash_str}")
        elif p["is_current"]:
            print(f"  {num:<10} > current    (in progress - cannot roll back to here)")
        else:
            print(f"  {num:<10} . pending    (not started)")

    print(f"\n  To roll back: forge rollback --to-phase N\n")


def _execute_rollback(project_dir: Path, state: ForgeState,
                      to_phase_number: int) -> None:
    """Execute rollback to the end of the given phase number (1-indexed)."""
    # Validate phase number
    if to_phase_number < 1:
        print(f"\n  {SYM_FAIL} Invalid phase number: {to_phase_number}. Phase numbers start at 1.\n")
        return

    if to_phase_number > len(state.phases):
        print(f"\n  {SYM_FAIL} Phase {to_phase_number} does not exist. "
              f"There are {len(state.phases)} phases.\n")
        return

    target_index = to_phase_number - 1

    if target_index >= state.current_phase_index:
        if target_index == state.current_phase_index:
            print(f"\n  {SYM_FAIL} Cannot roll back to Phase {to_phase_number} - it is the current phase.")
        else:
            print(f"\n  {SYM_FAIL} Cannot roll back to Phase {to_phase_number} - it has not been completed yet.")
        if state.current_phase_index > 0:
            print(f"    Use `forge rollback --to-phase {state.current_phase_index}` to roll back before it.\n")
        else:
            print(f"    No completed phases to roll back to.\n")
        return

    target_phase = state.phases[target_index]
    tag_name = _make_tag_name(target_phase.title)
    commit_hash = git_utils.get_tag_commit(project_dir, tag_name)

    if not commit_hash:
        print(f"\n  {SYM_FAIL} Tag '{tag_name}' not found in git history.")
        print(f"    Phase {to_phase_number} may not have been tagged properly.\n")
        return

    # Print rollback summary
    d = divider("heavy")
    print(f"\n{d}")
    print(f"  FORGE ROLLBACK")
    print(f"{d}")

    print(f"\n  Rolling back to end of Phase {to_phase_number}: {_extract_phase_name(target_phase.title).title()}")
    print(f"  Tag: {tag_name}  (commit {commit_hash})")

    phases_to_clear = len(state.phases) - to_phase_number
    phase_nums = ", ".join(str(i) for i in range(to_phase_number + 1, len(state.phases) + 1))

    print(f"\n  This will permanently:")
    print(f"    {SYM_FAIL} Delete git history after commit {commit_hash}")
    if phase_nums:
        print(f"    {SYM_FAIL} Remove build progress for Phase(s) {phase_nums}")
    print(f"    {SYM_OK} Restore project to the state after Phase {to_phase_number} passed QA")

    # Require confirmation
    expected = _extract_phase_name(target_phase.title)
    print(f"\n  Type the phase name to confirm: {expected}")
    try:
        confirmation = input("  > ").strip().lower()
    except KeyboardInterrupt:
        print("\n\n  Rollback cancelled.\n")
        return

    if confirmation != expected:
        print(f"\n  Confirmation text did not match. Rollback cancelled.\n")
        return

    # Execute rollback
    print(f"\n  Rolling back...")

    # Step 1: Git reset
    code, _, err = git_utils._run(
        ["git", "reset", "--hard", tag_name], project_dir
    )
    if code != 0:
        print(f"  {SYM_FAIL} Git reset failed: {err}")
        print(f"  Rollback aborted. State not modified.\n")
        return
    print(f"  {SYM_OK} Git reset to {tag_name} ({commit_hash})")

    # Step 2: Force push if remote exists
    if git_utils.has_remote(project_dir):
        print(f"  [forge] Force pushing to remote (this rewrites remote history)...")
        pushed = git_utils.force_push(project_dir)
        if not pushed:
            print(f"  {SYM_FAIL} Force push failed. Local reset succeeded.")
            print(f"    Push manually: git push origin main --force")

    # Step 3: Rewind state
    # Keep phases 0..target_index as DONE, remove everything after
    state.phases = state.phases[:to_phase_number]
    state.current_phase_index = to_phase_number  # points to next phase (will be re-planned)

    # Recount completed tasks from preserved phases
    state.tasks_completed = sum(
        1 for phase in state.phases
        for task in phase.tasks
        if task.status.value == "done"
    )
    state.tasks_since_checkin = 0

    # Step 4: Atomic save
    checkpoint.atomic_save(project_dir, state)
    print(f"  {SYM_OK} Build state rolled back to Phase {to_phase_number}")
    if phases_to_clear > 0:
        print(f"  {SYM_OK} Phase(s) {phase_nums} cleared from state")

    print(f"\n  Ready to continue from Phase {to_phase_number + 1}.")
    print(f"  Run `forge run` to resume building.\n")


def run_rollback(project_dir: Path, to_phase: int | None,
                 list_only: bool) -> None:
    """Entry point for forge rollback command."""
    if not git_utils.is_git_repo(project_dir):
        print("\n  Not a git repository. Initialize git first.\n")
        return

    state = load_state(project_dir)

    if not state.initialized:
        print("\n  No build state found. Run `forge run` first.\n")
        return

    if list_only:
        _list_rollback_points(project_dir, state)
        return

    if to_phase is not None:
        _execute_rollback(project_dir, state, to_phase)
        return

    # Neither flag provided
    print("\n  forge rollback: specify --list to see options or --to-phase N to roll back.\n")
