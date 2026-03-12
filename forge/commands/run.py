"""
forge run - The autonomous build loop.
"""

import sys
import time
from pathlib import Path

from forge import orchestrator, builder, git_utils, needs_human, display
from forge.loop_guard import LoopGuard
from forge.state import (
    ForgeState, Phase, Task, TaskStatus, PhaseStatus,
    load_state, save_state,
)


def run_forge(project_dir: Path, checkin_every: int = 10,
              max_retries: int = 3, dry_run: bool = False):
    _validate_project(project_dir)

    state = load_state(project_dir)
    loop_guard = LoopGuard(max_retries=max_retries)
    build_start_time = time.time()

    display.print_forge_header(project_dir.name)
    if dry_run:
        print(f"  Mode: dry run")

    # -----------------------------------------------------------------------
    # Phase 0: Initial setup
    # -----------------------------------------------------------------------
    if not state.initialized:
        print("\n[forge] First run - setting up project...\n")
        _initial_setup(project_dir, state, dry_run)
        save_state(project_dir, state)
        if dry_run:
            display.print_dry_run_plan(state.phases)
            return

    # -----------------------------------------------------------------------
    # Main loop
    # -----------------------------------------------------------------------
    phase_start_time = time.time()

    while not state.is_complete():
        phase = state.current_phase

        # Generate tasks for this phase if not done yet
        if not phase.tasks:
            display.print_phase_header(
                phase.title,
                state.current_phase_index,
                len(state.phases),
            )
            phase_start_time = time.time()

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
            _complete_phase(project_dir, state, phase, loop_guard, phase_start_time)
            save_state(project_dir, state)
            phase_start_time = time.time()
            continue

        # Check-in gate
        if state.tasks_since_checkin >= checkin_every:
            display.print_checkin_prompt(checkin_every)
            try:
                input()
            except KeyboardInterrupt:
                print("\n[forge] Paused. Run `forge run` to resume.")
                return
            state.tasks_since_checkin = 0
            save_state(project_dir, state)

        # Execute the task
        _execute_task(project_dir, state, phase, task, loop_guard,
                      max_retries, dry_run)
        save_state(project_dir, state)

    build_duration = time.time() - build_start_time
    display.print_build_complete(
        project_name=state.project_name or project_dir.name,
        phases_done=len(state.phases),
        tasks_done=state.tasks_completed,
        duration_seconds=build_duration,
    )


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
                  task: Task, loop_guard: LoopGuard,
                  max_retries: int, dry_run: bool):
    task_index = phase.tasks.index(task)
    total_tasks = len(phase.tasks)

    display.print_task_header(
        task_title=task.title,
        task_index=task_index,
        total_tasks=total_tasks,
        phase_title=phase.title,
        retry_count=task.retry_count,
    )

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
    success, stdout, stderr, duration = builder.run_task(project_dir, prompt)

    # Run tests
    tests_passed, test_out, test_err = builder.run_tests(project_dir)

    # QA evaluation via orchestrator
    qa_passed, qa_summary, retry_prompt = orchestrator.evaluate_qa(
        task, test_out, stderr + test_err
    )

    if qa_passed:
        task.status = TaskStatus.DONE
        task.notes = qa_summary
        task.completed_at = _now()
        loop_guard.record_success(task.id)
        state.tasks_completed += 1
        state.tasks_since_checkin += 1

        # Count done tasks in this phase
        tasks_done_in_phase = sum(
            1 for t in phase.tasks if t.status == TaskStatus.DONE
        )

        display.print_task_success(
            duration_seconds=duration,
            tasks_done=tasks_done_in_phase,
            total_tasks=total_tasks,
            phase_title=phase.title,
        )

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
            display.print_task_parked(task.id)
        else:
            display.print_task_failure(
                retry_count=task.retry_count,
                max_retries=max_retries,
                reason=qa_summary,
            )


# ---------------------------------------------------------------------------
# Phase completion
# ---------------------------------------------------------------------------

def _complete_phase(project_dir: Path, state: ForgeState, phase: Phase,
                    loop_guard: LoopGuard, phase_start_time: float):
    print(f"\n[forge] Running phase QA review...")

    approved, notes = orchestrator.evaluate_phase(project_dir, phase)
    phase.qa_notes = notes
    phase.completed_at = _now()

    tasks_done = sum(1 for t in phase.tasks if t.status == TaskStatus.DONE)
    tasks_parked = sum(1 for t in phase.tasks if t.status == TaskStatus.PARKED)
    phase_duration = time.time() - phase_start_time

    # Determine next phase title
    next_index = state.current_phase_index + 1
    next_phase_title = None
    if next_index < len(state.phases):
        next_phase_title = state.phases[next_index].title

    if approved:
        phase.status = PhaseStatus.DONE
        git_utils.tag_phase(project_dir, phase.title)
        display.print_phase_complete(
            phase_title=phase.title,
            tasks_done=tasks_done,
            tasks_parked=tasks_parked,
            duration_seconds=phase_duration,
            next_phase_title=next_phase_title,
        )
        state.advance_phase()
    else:
        phase.status = PhaseStatus.QA_FAILED
        print(f"  [forge] Phase QA failed: {notes[:200]}")
        needs_human.append_note(
            project_dir,
            f"Phase '{phase.title}' QA failed.\n\nNotes:\n{notes}"
        )
        display.print_phase_complete(
            phase_title=phase.title,
            tasks_done=tasks_done,
            tasks_parked=tasks_parked,
            duration_seconds=phase_duration,
            next_phase_title=next_phase_title,
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


def _now() -> str:
    from datetime import datetime
    return datetime.utcnow().isoformat()
