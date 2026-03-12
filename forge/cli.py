#!/usr/bin/env python3
"""
Forge - Autonomous AI Development Agent
Usage:
  forge profile           - Manage your global tool preferences
  forge new [description] - Generate project docs via guided interview
  forge init              - Initialize project templates
  forge run               - Start autonomous build loop
  forge doctor            - Run pre-flight checks on your setup
  forge rollback          - Roll back to a previous completed phase
  forge status            - Show current build status
  forge checkin           - Review NEEDS_HUMAN items interactively
  forge reset-task <id>   - Retry a parked task
  forge linear-plan       - Generate a Linear project plan from the build plan
"""

import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Forge - Autonomous AI Development Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--project-dir", default=".", help="Target project directory (default: cwd)")
    subparsers = parser.add_subparsers(dest="command")

    # forge profile
    profile_p = subparsers.add_parser(
        "profile",
        help="Manage your global tool preferences for new projects",
    )
    profile_p.add_argument(
        "--show",
        action="store_true",
        help="Display current profile",
    )
    profile_p.add_argument(
        "--edit",
        action="store_true",
        help="Edit existing profile (keeps current values as defaults)",
    )
    profile_p.add_argument(
        "--reset",
        action="store_true",
        help="Delete profile and start fresh",
    )

    # forge new
    new_p = subparsers.add_parser(
        "new",
        help="Generate project docs via a guided interview",
    )
    new_p.add_argument(
        "description",
        nargs="?",
        default=None,
        help="Product description (optional - will prompt if not provided)",
    )

    # forge doctor
    subparsers.add_parser(
        "doctor",
        help="Run pre-flight checks on your Forge setup",
    )

    # forge init
    subparsers.add_parser("init", help="Initialize Forge templates in a project")

    # forge run
    run_p = subparsers.add_parser("run", help="Start autonomous build loop")
    run_p.add_argument(
        "--checkin-every",
        type=int,
        default=10,
        help="Pause for human review every N completed tasks (default: 10)",
    )
    run_p.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Max retries before parking a failing task (default: 3)",
    )
    run_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan phases and tasks but do not execute them",
    )

    # forge rollback
    rollback_p = subparsers.add_parser(
        "rollback",
        help="Roll back to a previous completed phase",
    )
    rollback_p.add_argument(
        "--to-phase",
        type=int,
        default=None,
        metavar="N",
        help="Roll back to end of phase N (e.g. --to-phase 2)",
    )
    rollback_p.add_argument(
        "--list",
        action="store_true",
        dest="list_only",
        help="List available rollback points without rolling back",
    )

    # forge status
    status_p = subparsers.add_parser("status", help="Print current build state")
    status_p.add_argument(
        "--cost",
        action="store_true",
        help="Show cost report from .forge/cost_log.jsonl",
    )
    status_p.add_argument(
        "--log",
        action="store_true",
        help="Show recent build log entries",
    )
    status_p.add_argument(
        "--log-tail",
        type=int,
        default=20,
        metavar="N",
        help="Number of recent log entries to show (default: 20)",
    )
    status_p.add_argument(
        "--health",
        action="store_true",
        help="Show build health metrics and grade",
    )

    # forge checkin
    subparsers.add_parser("checkin", help="Interactively resolve NEEDS_HUMAN items")

    # forge reset-task
    rt_p = subparsers.add_parser("reset-task", help="Retry a parked task by ID")
    rt_p.add_argument("task_id", help="Task ID to reset")

    # forge linear-plan
    subparsers.add_parser(
        "linear-plan",
        help="Generate a Linear project plan from the Forge build plan",
    )

    args = parser.parse_args()
    project_dir = Path(args.project_dir).resolve()

    if args.command == "profile":
        from forge.commands.profile import run_profile
        run_profile(show=args.show, edit=args.edit, reset=args.reset)

    elif args.command == "doctor":
        from forge.commands.doctor import run_doctor
        run_doctor(project_dir)

    elif args.command == "new":
        from forge.commands.new import run_new
        run_new(project_dir, args.description)

    elif args.command == "init":
        from forge.commands.init import run_init
        run_init(project_dir)

    elif args.command == "run":
        from forge.commands.run import run_forge
        run_forge(project_dir, checkin_every=args.checkin_every,
                  max_retries=args.max_retries, dry_run=args.dry_run)

    elif args.command == "rollback":
        from forge.commands.rollback import run_rollback
        run_rollback(project_dir,
                     to_phase=args.to_phase,
                     list_only=args.list_only)

    elif args.command == "status":
        from forge.commands.status import run_status
        run_status(project_dir, show_cost=args.cost,
                   show_log=args.log, log_tail=args.log_tail,
                   show_health=args.health)

    elif args.command == "checkin":
        from forge.commands.checkin import run_checkin
        run_checkin(project_dir)

    elif args.command == "reset-task":
        from forge.commands.reset_task import run_reset_task
        run_reset_task(project_dir, args.task_id)

    elif args.command == "linear-plan":
        from forge.commands.linear_plan import run_linear_plan
        run_linear_plan(project_dir)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
