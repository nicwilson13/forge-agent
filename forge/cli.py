#!/usr/bin/env python3
"""
Forge - Autonomous AI Development Agent
Usage:
  forge init              - Initialize project templates
  forge run               - Start autonomous build loop
  forge status            - Show current build status
  forge checkin           - Review NEEDS_HUMAN items interactively
  forge reset-task <id>   - Retry a parked task
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

    # forge status
    subparsers.add_parser("status", help="Print current build state")

    # forge checkin
    subparsers.add_parser("checkin", help="Interactively resolve NEEDS_HUMAN items")

    # forge reset-task
    rt_p = subparsers.add_parser("reset-task", help="Retry a parked task by ID")
    rt_p.add_argument("task_id", help="Task ID to reset")

    args = parser.parse_args()
    project_dir = Path(args.project_dir).resolve()

    if args.command == "init":
        from forge.commands.init import run_init
        run_init(project_dir)

    elif args.command == "run":
        from forge.commands.run import run_forge
        run_forge(project_dir, checkin_every=args.checkin_every,
                  max_retries=args.max_retries, dry_run=args.dry_run)

    elif args.command == "status":
        from forge.commands.status import run_status
        run_status(project_dir)

    elif args.command == "checkin":
        from forge.commands.checkin import run_checkin
        run_checkin(project_dir)

    elif args.command == "reset-task":
        from forge.commands.reset_task import run_reset_task
        run_reset_task(project_dir, args.task_id)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
