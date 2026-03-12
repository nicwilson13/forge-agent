"""
forge run - The autonomous build loop.
"""

import signal
import sys
import time
from pathlib import Path

from forge import orchestrator, builder, git_utils, needs_human, display, checkpoint
from forge.router import route_task, log_route
from forge.build_logger import BuildLogger
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
                        task: Task | None, error: FatalAPIError,
                        logger: BuildLogger | None = None) -> None:
    """Handle a non-retryable error - save state and exit."""
    if task is not None:
        task.status = TaskStatus.INTERRUPTED
        task.interrupt_reason = error.error_prefix
    checkpoint.atomic_save(project_dir, state)

    if logger:
        logger.fatal_error(error.error_prefix, str(error))

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
    logger = BuildLogger(project_dir)
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
            _handle_fatal_error(project_dir, state, None, e, logger)
        except RetryExhaustedError as e:
            _handle_retry_exhausted(project_dir, state, None, e)
        checkpoint.atomic_save(project_dir, state)
        if dry_run:
            display.print_dry_run_plan(state.phases)
            return

    logger.session_started(
        project_name=state.project_name or project_dir.name,
        phase_count=len(state.phases),
    )

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
                _handle_fatal_error(project_dir, state, None, e, logger)
            except RetryExhaustedError as e:
                _handle_retry_exhausted(project_dir, state, None, e)
            phase.tasks = tasks
            phase.status = PhaseStatus.IN_PROGRESS
            checkpoint.atomic_save(project_dir, state)
            print(f"  Generated {len(tasks)} tasks")
            logger.phase_started(state.current_phase_index, phase.title, len(tasks))

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
            _complete_phase(project_dir, state, phase, loop_guard, phase_start_time, tracker, logger)
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
                      max_retries, dry_run, tracker, logger)
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
    logger.session_ended(
        tasks_completed=state.tasks_completed,
        total_cost=tracker.session_total_cost(),
        duration_secs=build_duration,
    )

    from forge.health import compute_health_report, format_health_summary_line
    report = compute_health_report(project_dir, logger.session_id)
    print()
    print(f"  {format_health_summary_line(report)}")


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
                  tracker: CostTracker | None = None,
                  logger: BuildLogger | None = None):
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
    if logger:
        logger.task_started(state.current_phase_index, task.id,
                            task.title, task.retry_count)

    if dry_run:
        _clear_interrupt_handler()
        print(f"  [dry-run] Would execute: {task.title}")
        task.status = TaskStatus.DONE
        state.tasks_completed += 1
        state.tasks_since_checkin += 1
        return

    # Build the prompt
    prompt = orchestrator.build_task_prompt(project_dir, phase, task)

    # Determine model for this task
    model, reason = route_task(
        task.title,
        task.description,
        retry_count=task.retry_count,
        previous_model=task.last_model or None,
    )
    log_route(f"task: {task.title[:20]}", model, reason)
    task.last_model = model

    # Call Claude Code
    try:
        success, stdout, stderr, duration = builder.run_task(project_dir, prompt, model=model)
    except FatalAPIError as e:
        _clear_interrupt_handler()
        _handle_fatal_error(project_dir, state, task, e, logger)
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
                logger,
            )
            return

    # Semantic diff review (after build, before tests/commit)
    from forge.diff_review import run_diff_review, format_review_output
    review_verdict, review_issues, review_usage = run_diff_review(
        project_dir, task.title, task.description
    )
    review_line = format_review_output(review_verdict, review_issues)
    if review_verdict == "flagged":
        print(f"  {display.SYM_WARN} {review_line}")
        if logger:
            logger.log("diff_review_flagged", phase=state.current_phase_index,
                       task=task.id, issues=review_issues[:3])
        if review_issues:
            task.notes = (task.notes or "") + \
                f"\nDiff review flags: {'; '.join(review_issues)}"
    elif review_verdict == "approved":
        print(f"  {display.SYM_OK} {review_line}")
        if logger:
            logger.log("diff_review_approved", phase=state.current_phase_index,
                       task=task.id)
    elif review_verdict == "skipped":
        reason = review_issues[0] if review_issues else "unknown"
        if reason not in ("no changes", "change too small"):
            print(f"  ({review_line})")
        if logger:
            logger.log("diff_review_skipped", phase=state.current_phase_index,
                       task=task.id, reason=reason)

    # Run tests
    tests_passed, test_out, test_err = builder.run_tests(project_dir)

    # QA evaluation via orchestrator
    try:
        qa_passed, qa_summary, retry_prompt, qa_usage = orchestrator.evaluate_qa(
            task, test_out, stderr + test_err
        )
    except FatalAPIError as e:
        _handle_fatal_error(project_dir, state, task, e, logger)
        return
    except RetryExhaustedError as e:
        _handle_retry_exhausted(project_dir, state, task, e)
        return

    if qa_passed:
        if logger:
            logger.qa_passed(state.current_phase_index, task.id,
                             task.title, qa_summary or "")

        # Run visual QA for frontend tasks (after code QA passes)
        from forge.visual_qa import run_visual_qa
        visual_result, visual_feedback, visual_usage = run_visual_qa(
            project_dir, task.title, task.description
        )

        if visual_result is None:
            # Skipped - log the reason quietly
            if visual_feedback:
                print(f"  (visual QA skipped - {visual_feedback})")
        elif visual_result:
            print(f"  {display.SYM_OK} Visual QA passed - {visual_feedback[:120]}")
            if logger:
                logger.visual_qa_passed(state.current_phase_index,
                                        task.id, task.title,
                                        visual_feedback)
        else:
            print(f"  {display.SYM_FAIL} Visual QA failed - {visual_feedback[:120]}")
            if logger:
                logger.visual_qa_failed(state.current_phase_index,
                                        task.id, task.title,
                                        visual_feedback)
            # Treat as QA failure - add feedback to task notes and retry
            task.notes = f"Visual QA failed: {visual_feedback}"
            qa_passed = False

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
        task_cost_val = None
        if tracker:
            task_cost_val = calculate_task_cost(
                task_id=task.id,
                task_title=task.title,
                phase_index=state.current_phase_index,
                phase_title=phase.title,
                duration_secs=duration,
                orchestrator_usage=qa_usage,
                builder_prompt_chars=len(prompt),
                builder_output_chars=len(stdout),
            )
            alerts = tracker.record_task(task_cost_val)
            print(f"  {tracker.format_task_line(task_cost_val)}")
            for alert in alerts:
                print(f"\n  {display.SYM_WARN} {alert}")

        if logger:
            cost = task_cost_val.total_cost if task_cost_val else 0.0
            tokens_in = (task_cost_val.orchestrator.input_tokens + task_cost_val.builder.input_tokens) if task_cost_val else 0
            tokens_out = (task_cost_val.orchestrator.output_tokens + task_cost_val.builder.output_tokens) if task_cost_val else 0
            logger.task_completed(state.current_phase_index, task.id,
                                  task.title, duration, cost,
                                  tokens_in, tokens_out)

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
                if logger:
                    logger.memory_recorded("decision", title)
            for name, description in memory_items.get("patterns", []):
                record_pattern(project_dir, name, description)
                if logger:
                    logger.memory_recorded("pattern", name)
            for what, why, instead in memory_items.get("failures", []):
                record_failure(
                    project_dir, what, why, instead,
                    phase_title=phase.title
                )
                if logger:
                    logger.memory_recorded("failure", what)

        # Commit
        commit_msg = f"[forge] {task.title} ({task.id})"
        hash_ = git_utils.commit_and_push(project_dir, commit_msg)
        if hash_:
            task.commit_hash = hash_
            if logger:
                logger.git_committed(hash_, commit_msg)
        else:
            # Commit failed - mark as commit pending
            checkpoint.mark_task_commit_pending(project_dir, state, task)
            print("  [forge] Checkpoint saved: task done, commit pending.")
            print("  Run `forge run` to retry the commit.")

    else:
        if logger:
            logger.qa_failed(state.current_phase_index, task.id,
                             task.title, qa_summary or "")

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
            if logger:
                logger.task_parked(state.current_phase_index, task.id,
                                   task.title, reason)
        else:
            display.print_task_failure(
                retry_count=task.retry_count,
                max_retries=max_retries,
                reason=qa_summary,
            )
            if logger:
                logger.task_failed(
                    state.current_phase_index, task.id, task.title,
                    qa_summary or "", task.retry_count,
                    will_retry=task.retry_count < max_retries,
                )


# ---------------------------------------------------------------------------
# Phase completion
# ---------------------------------------------------------------------------

def _complete_phase(project_dir: Path, state: ForgeState, phase: Phase,
                    loop_guard: LoopGuard, phase_start_time: float,
                    tracker: CostTracker | None = None,
                    logger: BuildLogger | None = None):
    # Run E2E tests before phase QA evaluation
    from forge.e2e_generator import (
        should_generate_e2e, generate_e2e_tests,
        run_e2e_tests, e2e_failure_context,
    )
    from forge.visual_qa import is_playwright_available

    e2e_passed = None
    e2e_summary = ""
    phase_index = state.current_phase_index

    if is_playwright_available() and should_generate_e2e(phase.title, phase.tasks):
        print(f"\n  [forge] Generating E2E tests for {phase.title}...")
        try:
            test_file, gen_summary = generate_e2e_tests(project_dir, phase)
            if test_file:
                print(f"  {display.SYM_OK} Generated E2E tests: {gen_summary[:120]}")
                e2e_passed, e2e_output, failed = run_e2e_tests(
                    project_dir, test_file
                )
                if e2e_passed:
                    print(f"  {display.SYM_OK} E2E tests passed")
                    if logger:
                        logger.log("e2e_passed", phase=phase_index,
                                   summary=gen_summary[:100])
                else:
                    failed_str = ", ".join(failed[:3]) if failed else "unknown"
                    print(f"  {display.SYM_FAIL} E2E tests failed: {failed_str}")
                    if logger:
                        logger.log("e2e_failed", phase=phase_index,
                                   failed_tests=failed[:5])
                    e2e_summary = e2e_failure_context(test_file, failed, e2e_output)
            else:
                print(f"  (E2E generation skipped - {gen_summary})")
        except (FatalAPIError, RetryExhaustedError):
            raise
        except Exception as e:
            print(f"  (E2E tests skipped - unexpected error: {e})")
    else:
        if not is_playwright_available():
            print("  (E2E tests skipped - playwright not available)")

    print(f"\n[forge] Running phase QA review...")

    try:
        approved, notes, _ = orchestrator.evaluate_phase(
            project_dir, phase,
            e2e_passed=e2e_passed,
            e2e_summary=e2e_summary,
        )
    except FatalAPIError as e:
        _handle_fatal_error(project_dir, state, None, e, logger)
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

    phase_cost = 0.0
    if tracker:
        ps = tracker.phase_summary(state.current_phase_index)
        phase_cost = ps["cost"]

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
        if logger:
            logger.phase_completed(state.current_phase_index, phase.title,
                                   tasks_done, phase_duration, phase_cost)
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
        if logger:
            logger.phase_failed(state.current_phase_index, phase.title,
                                notes or "QA review did not approve")
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
