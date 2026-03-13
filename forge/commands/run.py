"""
forge run - The autonomous build loop.
"""

import asyncio
import signal
import sys
import time
from pathlib import Path

from forge import orchestrator, builder, git_utils, needs_human, display, checkpoint
from forge.parallel import get_max_parallel, ParallelExecutor, ParallelLocks, TaskResult
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
from forge.github_integration import (
    load_github_config, get_github_token, get_open_issues,
    create_milestone, close_milestone, create_phase_pr,
    post_build_summary as gh_post_build_summary,
    format_issue_context,
)
from forge.vercel_integration import run_vercel_check, format_vercel_status
from forge.figma_integration import run_figma_integration
from forge.linear_integration import (
    load_linear_config, get_linear_token, run_linear_integration,
    match_issue_to_task, update_issue_status, create_issue as linear_create_issue,
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
    """Synchronous public entry point - runs the async loop."""
    _validate_project(project_dir)

    import anyio

    async def _main():
        await _run_forge_async(project_dir, checkin_every, max_retries, dry_run)

    anyio.run(_main)


async def _run_forge_async(project_dir: Path, checkin_every: int = 10,
                           max_retries: int = 3, dry_run: bool = False):
    """Async implementation of the build loop."""
    max_parallel = get_max_parallel()

    state = load_state(project_dir)
    loop_guard = LoopGuard(max_retries=max_retries)
    tracker = CostTracker(project_dir)
    logger = BuildLogger(project_dir)
    build_start_time = time.time()

    # Load MCP configuration
    from forge.mcp_config import load_mcp_config, log_mcp_status
    mcp_config = load_mcp_config(project_dir)

    # Load GitHub integration config
    gh_config = load_github_config(project_dir)
    gh_token = get_github_token() if gh_config.enabled else ""

    # Run Figma integration at build start
    figma_context, figma_components = run_figma_integration(project_dir)

    # Run Linear integration at build start
    linear_context, linear_issues = run_linear_integration(project_dir)
    lin_config = load_linear_config(project_dir)
    lin_token = get_linear_token() if lin_config.enabled else ""

    display.print_forge_header(project_dir.name)
    log_mcp_status(mcp_config)

    # Start web dashboard
    from forge.dashboard import start_dashboard, stop_dashboard, update_dashboard_state
    dashboard_thread = start_dashboard(project_dir)

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
            _initial_setup(project_dir, state, dry_run, mcp_config=mcp_config)
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

            # Fetch GitHub issues for task generation context
            issues_context = ""
            if gh_config.enabled and gh_token:
                issues = get_open_issues(gh_config, gh_token)
                if issues:
                    print(f"  [github] {len(issues)} open issue(s) loaded for context")
                    issues_context = format_issue_context(issues)

            try:
                tasks, _ = orchestrator.generate_tasks(project_dir, phase, state,
                                                       mcp_config=mcp_config,
                                                       github_issues_context=issues_context,
                                                       figma_context=figma_context,
                                                       linear_context=linear_context)
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
            _complete_phase(project_dir, state, phase, loop_guard, phase_start_time, tracker, logger,
                           mcp_config=mcp_config,
                           gh_config=gh_config, gh_token=gh_token)
            checkpoint.atomic_save(project_dir, state)

            # Update dashboard
            total_tasks = sum(len(p.tasks) for p in state.phases)
            tasks_done = sum(1 for p in state.phases for t in p.tasks if t.status == "done")
            update_dashboard_state({
                "project_name": state.project_name or project_dir.name,
                "current_phase": state.current_phase_index + 1,
                "total_phases": len(state.phases),
                "phase_title": state.current_phase.title if state.current_phase else phase.title,
                "tasks_done": tasks_done,
                "total_tasks": total_tasks,
                "cost": f"${tracker.session_total_cost():.2f}",
                "integrations": _get_integration_statuses(project_dir),
            })

            phase_start_time = time.time()
            continue

        # Parallel execution path
        if max_parallel > 1:
            pending = [t for t in phase.tasks
                       if t.status in (TaskStatus.PENDING, TaskStatus.FAILED,
                                       TaskStatus.INTERRUPTED, TaskStatus.COMMIT_PENDING)]
            if len(pending) > 1:
                from forge.dependency_graph import compute_execution_waves, format_wave_plan
                waves = compute_execution_waves(pending)
                if len(waves) > 1:
                    print(f"\n{format_wave_plan(waves)}")
                    print(f"\n  Phase {state.current_phase_index + 1}: {phase.title}"
                          f"  ({len(pending)} tasks, {max_parallel} parallel,"
                          f" dependency-aware)\n")
                else:
                    print(f"\n  Phase {state.current_phase_index + 1}: {phase.title}"
                          f"  ({len(pending)} tasks, {min(max_parallel, len(pending))} parallel)\n")
                completed = await _run_phase_parallel(
                    project_dir, state, phase, loop_guard,
                    max_retries, dry_run, tracker, logger, max_parallel,
                    mcp_config=mcp_config,
                    lin_config=lin_config, lin_token=lin_token,
                    linear_issues=linear_issues
                )
                checkpoint.atomic_save(project_dir, state)

                # If no tasks succeeded and any hit fatal errors, stop the build
                if completed == 0:
                    interrupted = [
                        t for t in phase.tasks
                        if t.status == TaskStatus.INTERRUPTED
                        and t.interrupt_reason
                        and is_fatal_error(t.interrupt_reason)
                    ]
                    if interrupted:
                        reason = interrupted[0].interrupt_reason
                        _handle_fatal_error(
                            project_dir, state, None,
                            FatalAPIError(
                                reason,
                                f"All parallel tasks failed with fatal error: {reason}",
                                "Check your API key and credentials",
                            ),
                            logger,
                        )
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

        # Execute the task (sequential)
        await _execute_task(project_dir, state, phase, task, loop_guard,
                            max_retries, dry_run, tracker, logger,
                            mcp_config=mcp_config,
                            lin_config=lin_config, lin_token=lin_token,
                            linear_issues=linear_issues)
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

    # Save build record for history view
    try:
        from forge.history_view import save_build_record
        _last_vercel = ""
        _last_pr = ""
        for p in reversed(state.phases):
            if not _last_vercel and getattr(p, "vercel_deployment_url", ""):
                _last_vercel = p.vercel_deployment_url
            if not _last_pr and getattr(p, "github_pr", None):
                _last_pr = str(p.github_pr)
        save_build_record(
            project_dir, state, report.grade,
            tracker.session_total_cost(), int(build_duration),
            vercel_url=_last_vercel, github_pr=_last_pr,
        )
    except Exception:
        pass

    # Final dashboard update and stop
    update_dashboard_state({
        "health": report.grade,
        "task_status": "complete",
    })
    stop_dashboard()


# ---------------------------------------------------------------------------
# Dashboard helpers
# ---------------------------------------------------------------------------

def _get_integration_statuses(project_dir: Path) -> dict:
    """
    Return dict of integration names to status strings.
    "ok" if config exists and enabled, "-" if not configured.
    """
    statuses = {}
    for name, filename in [
        ("github", "github.json"),
        ("vercel", "vercel.json"),
        ("linear", "linear.json"),
        ("sentry", "sentry.json"),
        ("figma", "figma.json"),
        ("ollama", "ollama.json"),
    ]:
        config_path = project_dir / ".forge" / filename
        if config_path.exists():
            try:
                import json
                data = json.loads(config_path.read_text(encoding="utf-8"))
                statuses[name] = "ok" if data.get("enabled") else "-"
            except Exception:
                statuses[name] = "-"
        else:
            statuses[name] = "-"
    return statuses


# ---------------------------------------------------------------------------
# Initial setup
# ---------------------------------------------------------------------------

def _initial_setup(project_dir: Path, state: ForgeState, dry_run: bool,
                   mcp_config=None):
    # Initialize memory directory
    ensure_memory_dir(project_dir)

    # Git setup
    if not git_utils.is_git_repo(project_dir):
        git_utils.init_repo(project_dir)
    git_utils.ensure_gitignore(project_dir)

    # Generate phases
    print("[forge] Generating development phases from VISION.md + REQUIREMENTS.md...")
    phases, _ = orchestrator.generate_phases(project_dir, mcp_config=mcp_config)
    state.phases = phases
    state.project_name = project_dir.name
    print(f"  Generated {len(phases)} phases:")
    for i, p in enumerate(phases):
        print(f"    {i+1}. {p.title}")

    # Write ARCHITECTURE.md
    print("\n[forge] Writing ARCHITECTURE.md...")
    if not dry_run:
        _ = orchestrator.write_architecture(project_dir, phases, mcp_config=mcp_config)
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

async def _execute_task(project_dir: Path, state: ForgeState, phase: Phase,
                        task: Task, loop_guard: LoopGuard,
                        max_retries: int, dry_run: bool,
                        tracker: CostTracker | None = None,
                        logger: BuildLogger | None = None,
                        locks: ParallelLocks | None = None,
                        mcp_config=None,
                        lin_config=None, lin_token: str = "",
                        linear_issues: list | None = None):
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
        if locks:
            async with locks.git:
                hash_ = git_utils.commit_and_push(project_dir, commit_msg)
        else:
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
    if not locks:
        _setup_interrupt_handler(project_dir, state, task)
    if locks:
        async with locks.state:
            checkpoint.mark_task_started(project_dir, state, task)
    else:
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
        builder._check_sdk_available()
        print(f"\n  [builder] Invoking Claude Code (SDK streaming)...")
        success, stdout, stderr, duration = await builder._run_task_async(
            project_dir, prompt, model=model
        )
    except FatalAPIError as e:
        if not locks:
            _clear_interrupt_handler()
        _handle_fatal_error(project_dir, state, task, e, logger)
        return
    except RetryExhaustedError as e:
        if not locks:
            _clear_interrupt_handler()
        _handle_retry_exhausted(project_dir, state, task, e)
        return

    if not locks:
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
            task, test_out, stderr + test_err, mcp_config=mcp_config
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
            if locks:
                async with locks.cost:
                    alerts = tracker.record_task(task_cost_val)
            else:
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
        if locks:
            async with locks.git:
                hash_ = git_utils.commit_and_push(project_dir, commit_msg)
        else:
            hash_ = git_utils.commit_and_push(project_dir, commit_msg)
        if hash_:
            task.commit_hash = hash_
            if logger:
                logger.git_committed(hash_, commit_msg)
        else:
            # Commit failed - mark as commit pending
            if locks:
                async with locks.state:
                    checkpoint.mark_task_commit_pending(project_dir, state, task)
            else:
                checkpoint.mark_task_commit_pending(project_dir, state, task)
            print("  [forge] Checkpoint saved: task done, commit pending.")
            print("  Run `forge run` to retry the commit.")

        # Update Linear issue status on task completion
        if lin_config and lin_config.enabled and lin_token and lin_config.update_issue_status:
            matched = match_issue_to_task(
                linear_issues or [], task.title, task.description
            )
            if matched:
                update_issue_status(lin_config, lin_token, matched["id"], "done")
                print(f"  [linear] {matched['identifier']} marked Done")

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
            # Create Linear issue for parked task
            if lin_config and lin_config.enabled and lin_token and lin_config.create_issues_for_parked:
                lin_issue = linear_create_issue(
                    lin_config, lin_token,
                    title=f"NEEDS_HUMAN: {task.title}",
                    description=(
                        f"Forge parked this task and needs human input.\n\n"
                        f"**Task:** {task.title}\n"
                        f"**Reason:** {reason}\n\n"
                        f"Resolve in `NEEDS_HUMAN.md` then run `forge checkin`."
                    ),
                )
                if lin_issue:
                    print(f"  [linear] Created {lin_issue['identifier']} for parked task")
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
# Parallel phase execution
# ---------------------------------------------------------------------------

async def _run_phase_parallel(
    project_dir: Path,
    state: ForgeState,
    phase: Phase,
    loop_guard: LoopGuard,
    max_retries: int,
    dry_run: bool,
    tracker: CostTracker | None,
    logger: BuildLogger | None,
    max_parallel: int,
    mcp_config=None,
    lin_config=None,
    lin_token: str = "",
    linear_issues: list | None = None,
) -> int:
    """
    Run all pending tasks in a phase using parallel execution.

    Returns the count of successfully completed tasks.
    Uses ParallelExecutor with max_parallel concurrency.
    """
    executor = ParallelExecutor(max_parallel=max_parallel)
    pending = [t for t in phase.tasks
               if t.status in (TaskStatus.PENDING, TaskStatus.FAILED,
                               TaskStatus.INTERRUPTED, TaskStatus.COMMIT_PENDING)]

    if not pending:
        return 0

    async def run_one(task, locks, **kwargs) -> TaskResult:
        start = time.time()
        try:
            await _execute_task(
                project_dir, state, phase, task,
                loop_guard, max_retries, dry_run,
                tracker, logger, locks=locks,
                mcp_config=mcp_config,
                lin_config=lin_config, lin_token=lin_token,
                linear_issues=linear_issues
            )
            success = task.status == TaskStatus.DONE
            return TaskResult(task.id, success, time.time() - start)
        except (SystemExit, KeyboardInterrupt):
            raise  # Never swallow process-terminating signals
        except Exception as e:
            return TaskResult(task.id, False, time.time() - start,
                              error=str(e))

    results = await executor.run_tasks(pending, run_one)
    return sum(1 for r in results if r.success)


# ---------------------------------------------------------------------------
# Phase completion
# ---------------------------------------------------------------------------

def _complete_phase(project_dir: Path, state: ForgeState, phase: Phase,
                    loop_guard: LoopGuard, phase_start_time: float,
                    tracker: CostTracker | None = None,
                    logger: BuildLogger | None = None,
                    mcp_config=None,
                    gh_config=None, gh_token: str = ""):
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

    # Run security scan before phase QA evaluation
    from forge.security_scan import run_security_scan, format_scan_results, Finding

    security_critical_count = 0
    security_warnings_count = 0

    print(f"\n  [forge] Security scan...")
    try:
        confirmed, sec_warnings, audit_vulns, sec_usage = run_security_scan(
            project_dir, run_audit=True
        )
        files_scanned = sum(1 for p in project_dir.rglob("*") if p.is_file())
        scan_output = format_scan_results(
            confirmed, sec_warnings, audit_vulns, files_scanned
        )
        print(f"  {scan_output}")

        security_critical_count = len(confirmed)
        security_warnings_count = len(sec_warnings)

        if confirmed:
            if logger:
                logger.log("security_critical", phase=phase_index,
                           count=len(confirmed),
                           categories=[f.category for f in confirmed])
            # Route critical findings back to Claude Code for fixing
            fix_prompt = _build_security_fix_prompt(confirmed, project_dir)
            _inject_security_fix_task(state, phase, fix_prompt)

        if sec_warnings:
            if logger:
                logger.log("security_warnings", phase=phase_index,
                           count=len(sec_warnings))
    except (FatalAPIError, RetryExhaustedError):
        raise
    except Exception as e:
        print(f"  (Security scan skipped - unexpected error: {e})")

    print(f"\n[forge] Running phase QA review...")

    try:
        approved, notes, _ = orchestrator.evaluate_phase(
            project_dir, phase,
            e2e_passed=e2e_passed,
            e2e_summary=e2e_summary,
            security_critical=security_critical_count,
            security_warnings=security_warnings_count,
            mcp_config=mcp_config,
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

        # GitHub integration at phase completion
        if gh_config and gh_config.enabled and gh_token:
            _github_phase_complete(
                project_dir, state, phase, phase_index,
                gh_config, gh_token, tracker, logger,
            )

        # Vercel deployment check
        _vercel_phase_complete(project_dir, state, phase, phase_index, logger)

        # Sentry error check after deploy
        _sentry_phase_complete(project_dir, state, phase, phase_index, logger)

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
# Security fix helpers
# ---------------------------------------------------------------------------

def _build_security_fix_prompt(findings: list, project_dir: Path) -> str:
    """Build a task prompt for fixing confirmed security findings."""
    lines = ["Fix the following confirmed security vulnerabilities:\n"]
    for f in findings:
        lines.append(f"- [{f.category}] {f.file_path}:{f.line_number}")
        lines.append(f"  Code: {f.line_content}")
        # Include surrounding context
        from forge.security_scan import get_file_context
        ctx = get_file_context(project_dir / f.file_path, f.line_number)
        if ctx:
            lines.append(f"  Context:\n{ctx}")
        lines.append("")

    lines.append(
        "For each finding:\n"
        "- Remove or rotate any hardcoded secrets (use environment variables)\n"
        "- Fix SQL injection by using parameterized queries\n"
        "- Replace eval() with safer alternatives\n"
        "- Sanitize file paths to prevent path traversal\n"
    )
    return "\n".join(lines)


def _inject_security_fix_task(state: ForgeState, phase: Phase,
                               fix_prompt: str) -> None:
    """
    Add a security fix task to the current phase's task list.

    Creates a new Task with title 'Fix security findings' and
    the fix prompt as description. Inserts it as the next task
    to execute (before any remaining pending tasks).
    """
    new_task = Task.new(
        title="Fix security findings",
        description=fix_prompt,
        phase_id=phase.id,
    )

    # Find the first pending task and insert before it
    insert_idx = len(phase.tasks)
    for i, t in enumerate(phase.tasks):
        if t.status == TaskStatus.PENDING:
            insert_idx = i
            break

    phase.tasks.insert(insert_idx, new_task)


# ---------------------------------------------------------------------------
# GitHub integration helpers
# ---------------------------------------------------------------------------

def _get_current_branch(project_dir: Path) -> str:
    """Run git rev-parse --abbrev-ref HEAD, return branch name."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(project_dir), capture_output=True, text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _github_phase_complete(
    project_dir: Path,
    state: ForgeState,
    phase: Phase,
    phase_index: int,
    gh_config,
    gh_token: str,
    tracker: CostTracker | None,
    logger: BuildLogger | None,
) -> None:
    """Run GitHub integration steps after a phase completes successfully."""
    phase_num = phase_index + 1
    milestone_num = None

    if gh_config.create_milestones:
        print(f"  [github] Creating milestone for Phase {phase_num}...")
        milestone_num = create_milestone(
            gh_config, gh_token, phase.title, phase_num
        )
        if milestone_num:
            phase.github_milestone = milestone_num
            print(f"  [github] Milestone #{milestone_num} created")

    if gh_config.create_prs:
        current_branch = _get_current_branch(project_dir)
        if current_branch and current_branch != gh_config.pr_base_branch:
            print(f"  [github] Opening PR from {current_branch}...")
            pr = create_phase_pr(
                gh_config, gh_token, phase, phase_num,
                current_branch, milestone_num
            )
            if pr:
                phase.github_pr = pr["number"]
                pr_url = pr.get("html_url", "")
                print(f"  [github] PR #{pr['number']} created")
                if pr_url:
                    print(f"  PR: {pr_url}")
                if logger:
                    logger.log("github_pr_created", phase=phase_index,
                               pr_number=pr["number"])

                if gh_config.post_build_summary:
                    health_summary = ""
                    cost_summary = ""
                    if tracker:
                        from forge.cost_tracker import _format_cost
                        cost_summary = _format_cost(tracker.session_total_cost())
                    gh_post_build_summary(
                        gh_config, gh_token, pr["number"],
                        phase, health_summary, cost_summary
                    )
                    print(f"  [github] Build summary posted to PR #{pr['number']}")
        else:
            if not current_branch:
                print("  [github] Could not determine current branch - skipping PR")
            else:
                print(f"  [github] Branch is {gh_config.pr_base_branch} - skipping PR")

    if milestone_num and gh_config.create_milestones:
        close_milestone(gh_config, gh_token, milestone_num)
        print(f"  [github] Milestone #{milestone_num} closed")


# ---------------------------------------------------------------------------
# Vercel integration helpers
# ---------------------------------------------------------------------------

def _vercel_phase_complete(
    project_dir: Path,
    state: ForgeState,
    phase,
    phase_index: int,
    logger: BuildLogger | None,
) -> None:
    """Run Vercel deployment check after a phase completes successfully."""
    phase_num = phase_index + 1

    # Get the latest commit SHA for filtering
    import subprocess
    git_sha = ""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(project_dir), capture_output=True, text=True,
        )
        if result.returncode == 0:
            git_sha = result.stdout.strip()
    except Exception:
        pass

    print(f"\n  [vercel] Checking deployment status...")
    status, url_or_msg, build_logs = run_vercel_check(project_dir, git_sha=git_sha)
    print(f"  {format_vercel_status(status, url_or_msg)}")

    phase.vercel_deployment_url = url_or_msg if status == "ready" else ""
    phase.vercel_deployment_status = status

    if status == "error" and build_logs:
        if logger:
            logger.log("vercel_build_failed", phase=phase_num,
                       logs=build_logs[:200])
        # Inject a fix task into the phase
        fix_prompt = (
            f"Fix the Vercel build failure for Phase {phase_num}.\n\n"
            f"Build error output:\n{build_logs}\n\n"
            f"Fix the TypeScript/build errors shown above."
        )
        _inject_security_fix_task(state, phase, fix_prompt)
    elif status == "ready":
        if logger:
            logger.log("vercel_deployed", phase=phase_num, url=url_or_msg)


def _sentry_phase_complete(
    project_dir: Path,
    state: ForgeState,
    phase,
    phase_index: int,
    logger: BuildLogger | None,
) -> None:
    """Run Sentry error check after a phase completes successfully."""
    from forge.sentry_integration import run_sentry_check

    fix_tasks = run_sentry_check(project_dir)
    if fix_tasks:
        if logger:
            logger.log("sentry_fix_tasks_created", phase=phase_index + 1,
                       count=len(fix_tasks))
        for title, description in fix_tasks:
            _inject_task_into_next_phase(state, phase_index, title, description)


def _inject_task_into_next_phase(
    state: ForgeState,
    current_phase_index: int,
    task_title: str,
    task_description: str,
) -> None:
    """
    Inject a task at the start of the next phase.

    If a next phase exists, prepends the task.
    If no next phase exists, appends to current phase.
    """
    next_idx = current_phase_index + 1
    if next_idx < len(state.phases):
        target_phase = state.phases[next_idx]
    else:
        target_phase = state.phases[current_phase_index]

    new_task = Task.new(
        title=task_title,
        description=task_description,
        phase_id=target_phase.id,
    )

    # Insert before the first pending task
    insert_idx = len(target_phase.tasks)
    for i, t in enumerate(target_phase.tasks):
        if t.status == TaskStatus.PENDING:
            insert_idx = i
            break
    target_phase.tasks.insert(insert_idx, new_task)


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
