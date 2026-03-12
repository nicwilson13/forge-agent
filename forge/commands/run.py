"""
forge run - The autonomous build loop.
"""

import sys
import time
from pathlib import Path

from forge import orchestrator, builder, git_utils, needs_human
from forge.loop_guard import LoopGuard
from forge.state import (
    ForgeState, Phase, Task, TaskStatus, PhaseStatus,
    load_state, save_state,
)


DIVIDER = "=" * 64


def run_forge(project_dir: Path, checkin_every: int = 10,
              max_retries: int = 3, dry_run: bool = False):
    _validate_project(project_dir)

    state = load_state(project_dir)
    loop_guard = LoopGuard(max_retries=max_retries)

    print(f"\n{DIVIDER}")
    print("  FORGE - Autonomous Development Agent")
    print(f"  Project: {project_dir.name}")
    print(f"  Dry run: {dry_run}")
    print(DIVIDER)

    # -----------------------------------------------------------------------
    # Phase 0: Initial setup
    # -----------------------------------------------------------------------
    if not state.initialized:
        print("\n[forge] First run - setting up project...\n")
        _initial_setup(project_dir, state, dry_run)
        save_state(project_dir, state)
        if dry_run:
            _print_dry_run_plan(state)
            return

    # -----------------------------------------------------------------------
    # Main loop
    # -----------------------------------------------------------------------
    while not state.is_complete():
        phase = state.current_phase

        # Generate tasks for this phase if not done yet
        if not phase.tasks:
            print(f"\n[forge] Planning tasks for: {phase.title}")
            tasks = orchestrator.generate_tasks(project_dir, phase, state)
            phase.tasks = tasks
            phase.status = PhaseStatus.IN_PROGRESS
            save_state(project_dir, state)
            print(f"  Generated {len(tasks)} tasks")

            # Pre-park tasks flagged as NEEDS_HUMAN by the planner
            for task in tasks:
                if task.park_reason:
                    task.status = TaskStatus.PARKED
                    needs_human.append_item(project_dir, task, task.park_reason)
            save_state(project_dir, state)

        # Get next executable task
        task = _next_task(phase)

        if task is None:
            # All tasks done or parked - review the phase
            _complete_phase(project_dir, state, phase, loop_guard)
            save_state(project_dir, state)
            continue

        # Check-in gate
        if state.tasks_since_checkin >= checkin_every:
            print(f"\n{DIVIDER}")
            print(f"  CHECK-IN POINT ({checkin_every} tasks completed)")
            print(f"  Run `forge checkin` to review progress and NEEDS_HUMAN items.")
            print(f"  Press Enter to continue autonomously, or Ctrl+C to pause.")
            print(DIVIDER)
            try:
                input()
            except KeyboardInterrupt:
                print("\n[forge] Paused. Run `forge run` to resume.")
                return
            state.tasks_since_checkin = 0
            save_state(project_dir, state)

        # Execute the task
        _execute_task(project_dir, state, phase, task, loop_guard, dry_run)
        save_state(project_dir, state)

    print(f"\n{DIVIDER}")
    print("  BUILD COMPLETE")
    print(f"  Total tasks completed: {state.tasks_completed}")
    print(DIVIDER)


# ---------------------------------------------------------------------------
# Initial setup
# ---------------------------------------------------------------------------

def _initial_setup(project_dir: Path, state: ForgeState, dry_run: bool):
    # Git setup
    if not git_utils.is_git_repo(project_dir):
        git_utils.init_repo(project_dir)
    git_utils.ensure_gitignore(project_dir)

    # Generate phases
    print("[forge] Generating development phases from VISION.md + REQUIREMENTS.md...")
    phases = orchestrator.generate_phases(project_dir)
    state.phases = phases
    state.project_name = project_dir.name
    print(f"  Generated {len(phases)} phases:")
    for i, p in enumerate(phases):
        print(f"    {i+1}. {p.title}")

    # Write ARCHITECTURE.md
    print("\n[forge] Writing ARCHITECTURE.md...")
    if not dry_run:
        orchestrator.write_architecture(project_dir, phases)
        state.architecture_written = True

    state.initialized = True

    # Initial commit
    if not dry_run:
        git_utils.commit_and_push(
            project_dir,
            "[forge] Initial commit: ARCHITECTURE.md, VISION.md, REQUIREMENTS.md"
        )


# ---------------------------------------------------------------------------
# Task execution
# ---------------------------------------------------------------------------

def _execute_task(project_dir: Path, state: ForgeState, phase: Phase,
                  task: Task, loop_guard: LoopGuard, dry_run: bool):
    print(f"\n{DIVIDER}")
    print(f"  Phase {state.current_phase_index + 1} | Task [{task.id}]: {task.title}")
    print(DIVIDER)

    task.status = TaskStatus.IN_PROGRESS
    save_state(project_dir, state)

    if dry_run:
        print(f"  [dry-run] Would execute: {task.title}")
        task.status = TaskStatus.DONE
        state.tasks_completed += 1
        state.tasks_since_checkin += 1
        return

    # Build the prompt
    prompt = orchestrator.build_task_prompt(project_dir, phase, task)

    # Call Claude Code
    success, stdout, stderr = builder.run_task(project_dir, prompt)

    # Run tests
    tests_passed, test_out, test_err = builder.run_tests(project_dir)

    # QA evaluation via orchestrator
    qa_passed, qa_summary, retry_prompt = orchestrator.evaluate_qa(
        task, test_out, stderr + test_err
    )

    print(f"  [qa] {'PASSED' if qa_passed else 'FAILED'}: {qa_summary[:120]}")

    if qa_passed:
        task.status = TaskStatus.DONE
        task.notes = qa_summary
        task.completed_at = _now()
        loop_guard.record_success(task.id)
        state.tasks_completed += 1
        state.tasks_since_checkin += 1

        # Commit
        commit_msg = f"[forge] {task.title} ({task.id})"
        hash_ = git_utils.commit_and_push(project_dir, commit_msg)
        task.commit_hash = hash_

    else:
        task.retry_count += 1
        task.notes = retry_prompt or qa_summary
        task.status = TaskStatus.FAILED
        loop_guard.record_failure(task.id, qa_summary)

        if loop_guard.is_stuck(task.id):
            reason = loop_guard.park_reason(task.id)
            task.status = TaskStatus.PARKED
            task.park_reason = reason
            needs_human.append_item(project_dir, task, reason)
            print(f"  [forge] Task parked after {max_retries_for(task)} failures.")
        else:
            print(f"  [forge] Task failed (attempt {task.retry_count}). Will retry.")


def max_retries_for(task: Task) -> int:
    return task.retry_count


# ---------------------------------------------------------------------------
# Phase completion
# ---------------------------------------------------------------------------

def _complete_phase(project_dir: Path, state: ForgeState, phase: Phase,
                    loop_guard: LoopGuard):
    print(f"\n[forge] All tasks done for: {phase.title}")
    print("[forge] Running phase QA review...")

    approved, notes = orchestrator.evaluate_phase(project_dir, phase)
    phase.qa_notes = notes
    phase.completed_at = _now()

    if approved:
        phase.status = PhaseStatus.DONE
        git_utils.tag_phase(project_dir, phase.title)
        print(f"  [forge] Phase approved: {notes[:120]}")
        state.advance_phase()
    else:
        phase.status = PhaseStatus.QA_FAILED
        print(f"  [forge] Phase QA failed: {notes[:200]}")
        # Re-queue any done tasks that are part of blocking issues
        # by resetting them - simple approach: note it in NEEDS_HUMAN
        needs_human.append_note(
            project_dir,
            f"Phase '{phase.title}' QA failed.\n\nNotes:\n{notes}"
        )
        # Advance anyway to avoid infinite loop, but flag it
        state.advance_phase()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _next_task(phase: Phase):
    for task in phase.tasks:
        if task.status in (TaskStatus.PENDING, TaskStatus.FAILED):
            return task
    return None


def _validate_project(project_dir: Path):
    if not project_dir.exists():
        print(f"[forge] ERROR: Project directory not found: {project_dir}")
        sys.exit(1)
    if not (project_dir / "VISION.md").exists():
        print("[forge] ERROR: VISION.md not found. Run `forge init` first.")
        sys.exit(1)
    if not (project_dir / "REQUIREMENTS.md").exists():
        print("[forge] WARNING: REQUIREMENTS.md not found. Continuing with VISION.md only.")


def _print_dry_run_plan(state: ForgeState):
    print(f"\n{DIVIDER}")
    print("  DRY RUN PLAN")
    print(DIVIDER)
    for i, phase in enumerate(state.phases):
        print(f"\nPhase {i+1}: {phase.title}")
        print(f"  {phase.description[:200]}")
    print(f"\n{DIVIDER}")


def _now() -> str:
    from datetime import datetime
    return datetime.utcnow().isoformat()
