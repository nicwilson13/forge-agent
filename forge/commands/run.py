"""
forge run - The autonomous build loop.
"""

import signal
import sys
import time
from pathlib import Path

from forge import orchestrator, builder, git_utils, needs_human, display, checkpoint
from forge.cost_tracker import CostTracker, TokenUsage, calculate_task_cost
from forge.memory import (
    ensure_memory_dir, extract_memory_from_qa,
    record_decision, record_pattern, record_failure,
)
from forge.loop_guard import LoopGuard
from forge.retry import FatalAPIError, RetryExhaustedError, extract_error_prefix, is_fatal_error
from forge.state import (
    ForgeState, Phase, Task, TaskStatus, PhaseStatus,
    load_state,
)

# Module-level state for signal handler (must not be closures)
_current_task: Task | None = None
_current_project_dir: Path | None = None
_current_state: ForgeState | None = None


def _setup_interrupt_handler(project_dir: Path, state: ForgeState,
                             task: Task) -> None:
    """Register Ctrl+C handler for the current task."""
    global _current_task, _current_project_dir, _current_state
    _current_task = task
    _current_project_dir = project_dir
    _current_state = state

    def _handle_interrupt(signum, frame):
        print("\n")
        print("  [forge] Interrupted. Saving checkpoint...")
        if _current_task and _current_project_dir and _current_state:
            checkpoint.mark_task_interrupted(
                _current_project_dir, _current_state,
                _current_task, "ctrl_c"
            )
            print(f'  [forge] Task "{_current_task.title}" marked as interrupted.')
            print("  [forge] Run `forge run` to resume from this task.")
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_interrupt)
    # SIGTERM is Unix-only
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, _handle_interrupt)


def _clear_interrupt_handler() -> None:
    """Restore default Ctrl+C behavior after task completes."""
    global _current_task, _current_project_dir, _current_state
    _current_task = None
    _current_project_dir = None
    _current_state = None
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, signal.SIG_DFL)


def _handle_fatal_error(project_dir: Path, state: ForgeState,
                        task: Task | None, error: FatalAPIError) -> None:
    """Handle a non-retryable error - save state and exit."""
    if task is not None:
        task.status = TaskStatus.INTERRUPTED
        task.interrupt_reason = error.error_prefix
    checkpoint.atomic_save(project_dir, state)

    print(f"\n  {display.SYM_FAIL} Authentication failed. Forge cannot continue.\n")
    print(f"  {error}")
    if error.fix_instruction:
        print(f"  Fix: {error.fix_instruction}")
    print(f"\n  State saved. Run `forge run` after fixing the issue.")
    sys.exit(1)


def _handle_retry_exhausted(project_dir: Path, state: ForgeState,
                            task: Task | None,
                            error: RetryExhaustedError) -> None:
    """Handle exhausted retries - save state and exit cleanly."""
    if task is not None:
        task.status = TaskStatus.INTERRUPTED
        task.interrupt_reason = "retry_exhausted"
    checkpoint.atomic_save(project_dir, state)

    print(f"\n  {display.SYM_FAIL} API unavailable after {error.attempts} attempts. Build paused.\n")
    print(f"  State saved to .forge/state.json")
    print(f"  Run `forge run` when the API is available to resume.")
    sys.exit(0)


def run_forge(project_dir: Path, checkin_every: int = 10,
              max_retries: int = 3, dry_run: bool = False):
    _validate_project(project_dir)

    state = load_state(project_dir)
    loop_guard = LoopGuard(max_retries=max_retries)
    tracker = CostTracker(project_dir)
    build_start_time = time.time()

    display.print_forge_header(project_dir.name)
    if dry_run:
        print(f"  Mode: dry run")

    # -----------------------------------------------------------------------
    # Detect interrupted tasks from previous run
    # -----------------------------------------------------------------------
    interrupted = checkpoint.detect_interrupted_tasks(state)
    if interrupted:
        msg = checkpoint.resume_message(interrupted)
        print(f"\n{msg}")
        # Normalize any raw IN_PROGRESS tasks to INTERRUPTED
        for task in interrupted:
            if task.status == TaskStatus.IN_PROGRESS:
                task.status = TaskStatus.INTERRUPTED
                task.interrupt_reason = "crash"
        checkpoint.atomic_save(project_dir, state)

    # -----------------------------------------------------------------------
    # Phase 0: Initial setup
    # -----------------------------------------------------------------------
    if not state.initialized:
        print("\n[forge] First run - setting up project...\n")
        try:
            _initial_setup(project_dir, state, dry_run)
        except FatalAPIError as e:
            _handle_fatal_error(project_dir, state, None, e)
        except RetryExhaustedError as e:
            _handle_retry_exhausted(project_dir, state, None, e)
        checkpoint.atomic_save(project_dir, state)
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
            try:
                tasks, _ = orchestrator.generate_tasks(project_dir, phase, state)
            except FatalAPIError as e:
                _handle_fatal_error(project_dir, state, None, e)
            except RetryExhaustedError as e:
                _handle_retry_exhausted(project_dir, state, None, e)
            phase.tasks = tasks
            phase.status = PhaseStatus.IN_PROGRESS
            checkpoint.atomic_save(project_dir, state)
            print(f"  Generated {len(tasks)} tasks")

            # Pre-park tasks flagged as NEEDS_HUMAN by the planner
            for task in tasks:
                if task.park_reason:
                    task.status = TaskStatus.PARKED
                    needs_human.append_item(project_dir, task, task.park_reason)
            checkpoint.atomic_save(project_dir, state)

        # Get next executable task
        task = _next_task(phase)

        if task is None:
            # All tasks done or parked - review the phase
            _complete_phase(project_dir, state, phase, loop_guard, phase_start_time, tracker)
            checkpoint.atomic_save(project_dir, state)
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
            checkpoint.atomic_save(project_dir, state)

        # Execute the task
        _execute_task(project_dir, state, phase, task, loop_guard,
                      max_retries, dry_run, tracker)
        checkpoint.atomic_save(project_dir, state)

    build_duration = time.time() - build_start_time
    display.print_build_complete(
        project_name=state.project_name or project_dir.name,
        phases_done=len(state.phases),
        tasks_done=state.tasks_completed,
        duration_seconds=build_duration,
    )
    if tracker.session_total_cost() > 0:
        print(tracker.format_session_summary())


# ---------------------------------------------------------------------------
# Initial setup
# ---------------------------------------------------------------------------

def _initial_setup(project_dir: Path, state: ForgeState, dry_run: bool):
    # Initialize memory directory
    ensure_memory_dir(project_dir)

    # Git setup
    if not git_utils.is_git_repo(project_dir):
        git_utils.init_repo(project_dir)
    git_utils.ensure_gitignore(project_dir)

    # Generate phases
    print("[forge] Generating development phases from VISION.md + REQUIREMENTS.md...")
    phases, _ = orchestrator.generate_phases(project_dir)
    state.phases = phases
    state.project_name = project_dir.name
    print(f"  Generated {len(phases)} phases:")
    for i, p in enumerate(phases):
        print(f"    {i+1}. {p.title}")

    # Write ARCHITECTURE.md
    print("\n[forge] Writing ARCHITECTURE.md...")
    if not dry_run:
        _ = orchestrator.write_architecture(project_dir, phases)
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
                  max_retries: int, dry_run: bool,
                  tracker: CostTracker | None = None):
    task_index = phase.tasks.index(task)
    total_tasks = len(phase.tasks)

    display.print_task_header(
        task_title=task.title,
        task_index=task_index,
        total_tasks=total_tasks,
        phase_title=phase.title,
        retry_count=task.retry_count,
    )

    # Handle COMMIT_PENDING: skip Claude Code, just retry the commit
    if task.status == TaskStatus.COMMIT_PENDING:
        print("  [forge] Retrying commit and push...")
        commit_msg = f"[forge] {task.title} ({task.id})"
        hash_ = git_utils.commit_and_push(project_dir, commit_msg)
        if hash_:
            task.status = TaskStatus.DONE
            task.commit_hash = hash_
            task.completed_at = _now()
            print(f"  [forge] Committed and pushed: {hash_}")
        else:
            print("  [forge] Commit failed again. Logging to NEEDS_HUMAN.")
            task.status = TaskStatus.COMMIT_PENDING
            needs_human.append_note(
                project_dir,
                f"Task '{task.title}' ({task.id}) completed but commit keeps failing. "
                f"Manual git commit may be needed."
            )
        return

    # For INTERRUPTED tasks, bump retry count and add context
    if task.status == TaskStatus.INTERRUPTED:
        task.retry_count += 1
        if task.interrupt_reason:
            task.notes = f"Previously interrupted ({task.interrupt_reason}). {task.notes}"

    # Mark task as started with checkpoint
    _setup_interrupt_handler(project_dir, state, task)
    checkpoint.mark_task_started(project_dir, state, task)

    if dry_run:
        _clear_interrupt_handler()
        print(f"  [dry-run] Would execute: {task.title}")
        task.status = TaskStatus.DONE
        state.tasks_completed += 1
        state.tasks_since_checkin += 1
        return

    # Build the prompt
    prompt = orchestrator.build_task_prompt(project_dir, phase, task)

    # Call Claude Code
    try:
        success, stdout, stderr, duration = builder.run_task(project_dir, prompt)
    except FatalAPIError as e:
        _clear_interrupt_handler()
        _handle_fatal_error(project_dir, state, task, e)
        return
    except RetryExhaustedError as e:
        _clear_interrupt_handler()
        _handle_retry_exhausted(project_dir, state, task, e)
        return

    _clear_interrupt_handler()

    # Check for fatal errors in builder stderr
    if not success:
        prefix = extract_error_prefix(stderr)
        if is_fatal_error(prefix):
            _handle_fatal_error(
                project_dir, state, task,
                FatalAPIError(
                    error_prefix=prefix,
                    message=stderr,
                    fix_instruction=(
                        "Check your key at console.anthropic.com/settings/keys\n"
                        "       export ANTHROPIC_API_KEY=sk-ant-your-new-key"
                    ),
                ),
            )
            return

    # Run tests
    tests_passed, test_out, test_err = builder.run_tests(project_dir)

    # QA evaluation via orchestrator
    try:
        qa_passed, qa_summary, retry_prompt, qa_usage = orchestrator.evaluate_qa(
            task, test_out, stderr + test_err
        )
    except FatalAPIError as e:
        _handle_fatal_error(project_dir, state, task, e)
        return
    except RetryExhaustedError as e:
        _handle_retry_exhausted(project_dir, state, task, e)
        return

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

        # Record cost
        if tracker:
            task_cost = calculate_task_cost(
                task_id=task.id,
                task_title=task.title,
                phase_index=state.current_phase_index,
                phase_title=phase.title,
                duration_secs=duration,
                orchestrator_usage=qa_usage,
                builder_prompt_chars=len(prompt),
                builder_output_chars=len(stdout),
            )
            alerts = tracker.record_task(task_cost)
            print(f"  {tracker.format_task_line(task_cost)}")
            for alert in alerts:
                print(f"\n  {display.SYM_WARN} {alert}")

        # Extract and record memory from QA summary
        if qa_summary:
            memory_items = extract_memory_from_qa(
                qa_summary, task.title, task.description
            )
            for title, decision, rationale in memory_items.get("decisions", []):
                record_decision(
                    project_dir, title, decision, rationale,
                    phase_title=phase.title, task_title=task.title
                )
            for name, description in memory_items.get("patterns", []):
                record_pattern(project_dir, name, description)
            for what, why, instead in memory_items.get("failures", []):
                record_failure(
                    project_dir, what, why, instead,
                    phase_title=phase.title
                )

        # Commit
        commit_msg = f"[forge] {task.title} ({task.id})"
        hash_ = git_utils.commit_and_push(project_dir, commit_msg)
        if hash_:
            task.commit_hash = hash_
        else:
            # Commit failed - mark as commit pending
            checkpoint.mark_task_commit_pending(project_dir, state, task)
            print("  [forge] Checkpoint saved: task done, commit pending.")
            print("  Run `forge run` to retry the commit.")

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
                    loop_guard: LoopGuard, phase_start_time: float,
                    tracker: CostTracker | None = None):
    print(f"\n[forge] Running phase QA review...")

    try:
        approved, notes, _ = orchestrator.evaluate_phase(project_dir, phase)
    except FatalAPIError as e:
        _handle_fatal_error(project_dir, state, None, e)
    except RetryExhaustedError as e:
        _handle_retry_exhausted(project_dir, state, None, e)
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
        if tracker:
            from forge.cost_tracker import _format_cost
            ps = tracker.phase_summary(state.current_phase_index)
            if ps["tasks"] > 0:
                session_total = _format_cost(tracker.session_total_cost())
                print(f"  Tokens: {ps['input_tokens']:,} in / {ps['output_tokens']:,} out  Cost: {_format_cost(ps['cost'])}  Session total: {session_total}")
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
        if task.status in (
            TaskStatus.PENDING,
            TaskStatus.FAILED,
            TaskStatus.INTERRUPTED,
            TaskStatus.COMMIT_PENDING,
        ):
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
