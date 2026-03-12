"""
forge linear-plan command.

Generates a complete Linear project plan from the Forge build plan.
Creates milestones (one per phase) and issues (one per task) in Linear.

If a .forge/state.json exists, uses the existing plan.
Otherwise, generates phases and tasks using the orchestrator.
"""

import sys
from pathlib import Path

from forge.display import SYM_FAIL, SYM_OK, SYM_WARN
from forge.linear_integration import (
    load_linear_config,
    get_linear_token,
    sync_plan_to_linear,
)
from forge.state import ForgeState, load_state


def run_linear_plan(project_dir: Path) -> None:
    """
    Entry point for `forge linear-plan`.

    1. Check Linear config - exit with message if not configured
    2. Check for existing .forge/state.json
       - If exists: offer to use it
       - If not: generate phases and tasks via orchestrator
    3. Call sync_plan_to_linear() with the phases
    4. Print summary
    """
    config = load_linear_config(project_dir)
    token = get_linear_token()

    if not config.enabled or not config.team_id:
        print(f"{SYM_FAIL} Linear integration not configured.")
        print("  Run `forge new` to set up Linear, or create .forge/linear.json manually.")
        sys.exit(1)

    if not token:
        print(f"{SYM_FAIL} Linear token not found in ~/.forge/profile.yaml.")
        print("  Add: linear_token: your-token-here")
        sys.exit(1)

    # Check for existing plan
    state = load_state(project_dir)
    phases = None

    if state and state.phases:
        total_tasks = sum(len(p.tasks) for p in state.phases)
        print(f"\n  Found existing build plan ({len(state.phases)} phases, "
              f"{total_tasks} tasks).")
        print("  Use existing plan? [Y/n]: ", end="", flush=True)
        answer = input().strip().lower()
        if answer in ("", "y", "yes"):
            phases = state.phases

    if phases is None:
        # Generate plan from docs
        _generate_plan(project_dir, config, token)
        return

    # Write to Linear
    print("\n  Writing to Linear...")
    summary = sync_plan_to_linear(config, token, phases)

    total_issues = summary["issues_created"]
    total_milestones = summary["milestones_created"]
    print(f"\n  Linear project plan complete.")
    print(f"  {total_issues} issues across {total_milestones} milestones.")

    if summary["errors"]:
        print(f"  {SYM_WARN} {len(summary['errors'])} error(s) during sync:")
        for err in summary["errors"][:3]:
            print(f"    - {err}")


def _generate_plan(project_dir: Path, config, token: str) -> None:
    """
    Generate phases and tasks from project docs, then sync to Linear.
    Uses the orchestrator to generate phases and tasks.
    """
    vision_path = project_dir / "VISION.md"

    if not vision_path.exists():
        print(f"{SYM_FAIL} VISION.md not found. Run `forge new` first.")
        sys.exit(1)

    from forge.orchestrator import generate_phases, generate_tasks

    print("\n  Reading VISION.md - REQUIREMENTS.md - CLAUDE.md")
    print("  Linear: generating project plan...\n")

    print("  Generating phases with Claude...")
    phases, _ = generate_phases(project_dir)

    state = ForgeState(project_name="linear-plan-gen", phases=phases)

    all_phases = []
    for i, phase in enumerate(phases, 1):
        print(f"  Generating tasks for Phase {i}: {phase.title}...")
        tasks, _ = generate_tasks(project_dir, phase, state)
        phase.tasks = tasks
        all_phases.append(phase)
        print(f"  {SYM_OK} Phase {i}: {phase.title}  ({len(tasks)} tasks)")

    print("\n  Writing to Linear...")
    summary = sync_plan_to_linear(config, token, all_phases)
    print(f"\n  Linear project plan complete.")
    print(f"  {summary['issues_created']} issues across "
          f"{summary['milestones_created']} milestones.")

    if summary["errors"]:
        print(f"  {SYM_WARN} {len(summary['errors'])} error(s) during sync:")
        for err in summary["errors"][:3]:
            print(f"    - {err}")
