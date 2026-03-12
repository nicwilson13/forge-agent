"""
forge profile - Manage your global Forge preferences.

Stores preferred tools and stack choices at ~/.forge/profile.yaml.
Used by forge new to pre-fill interview defaults.
"""

import shutil
from pathlib import Path

from forge.display import SYM_OK, SYM_WARN, divider
from forge.profile import (
    PROFILE_CATEGORIES, _DISPLAY_LABELS,
    load_profile, save_profile, has_profile, profile_path,
)


def _format_suggestions(suggestions: list[str] | None) -> str:
    """Format suggestions list wrapped to terminal width."""
    if not suggestions:
        return ""
    width = shutil.get_terminal_size((64, 24)).columns - 4
    prefix = "  Popular choices: "
    continuation = " " * len(prefix)
    lines = []
    current = prefix

    for i, s in enumerate(suggestions):
        addition = s if i == 0 else f", {s}"
        if len(current) + len(addition) > width and current != prefix:
            lines.append(current + ",")
            current = continuation + s
        else:
            current += addition

    lines.append(current)
    return "\n".join(lines)


def _prompt_input(question: str, default: str | None = None) -> str:
    """
    Prompt for input. If default is set, empty input returns the default.
    Otherwise loops until non-empty input is provided.
    """
    if default:
        answer = input(f"{question} [{default}]\n  > ").strip()
        return answer if answer else default
    while True:
        answer = input(f"{question}\n  > ").strip()
        if answer:
            return answer
        if answer == "":
            # Allow "skip" behavior for optional fields
            return ""


def _run_setup(existing: dict | None = None) -> None:
    """Run the interactive profile setup."""
    d = divider("heavy")
    print(f"\n{d}")
    print(f"  FORGE PROFILE - Your Preferred Toolkit")
    print(f"{d}")

    if existing:
        print(f"\n  Editing profile. Press Enter to keep current values.\n")
    else:
        print(f"\n  Set your preferences once. Forge will use them as defaults")
        print(f"  on every new project.\n")

    profile = {}

    try:
        for cat in PROFILE_CATEGORIES:
            key = cat["key"]
            label = cat["label"]
            suggestions = cat.get("suggestions")
            hint = cat.get("hint")

            # Section header
            d_light = divider("light")
            print(f"  {label}")
            print(f"  {d_light}")

            # Show suggestions if available
            if suggestions:
                print(_format_suggestions(suggestions))

            # Show hint if available
            if hint:
                print(f"  {hint}")

            # Get input
            current = existing.get(key, "") if existing else ""
            if current:
                answer = _prompt_input(f"  Your choice (or 'skip'):", default=current)
            else:
                answer = _prompt_input(f"  Your choice (or 'skip'):")

            if answer.lower() == "skip":
                answer = ""

            if answer:
                profile[key] = answer

            print()

    except KeyboardInterrupt:
        print("\n\n  Profile setup cancelled.\n")
        return

    # Save
    if existing:
        # Preserve timestamps from existing
        if "created_at" in existing:
            profile["created_at"] = existing["created_at"]

    path = save_profile(profile)
    print(f"  {SYM_OK} Profile saved to: {path}")
    print(f"\n  This will be used as defaults for all new projects.")
    print(f"  Run `forge profile --show` to review or `forge profile --edit`")
    print(f"  to update any time.\n")


def _show_profile(profile: dict) -> None:
    """Display the current profile in a formatted table."""
    d = divider("heavy")
    print(f"\n{d}")
    print(f"  FORGE PROFILE")
    print(f"{d}\n")

    has_any = False
    for key, label in _DISPLAY_LABELS.items():
        val = profile.get(key, "")
        if val:
            has_any = True
            print(f"  {label + ':':<16}{val}")

    if not has_any:
        print(f"  (empty profile)")

    print(f"\n  To update: forge profile --edit")
    print(f"  To reset:  forge profile --reset\n")


def _reset_profile() -> None:
    """Delete the profile after confirmation."""
    path = profile_path()
    if not path.exists():
        print("\n  No profile found. Nothing to reset.\n")
        return

    try:
        confirm = input("\n  Are you sure? This cannot be undone. (yes/no): ").strip().lower()
    except KeyboardInterrupt:
        print("\n\n  Reset cancelled.\n")
        return

    if confirm != "yes":
        print("\n  Reset cancelled.\n")
        return

    try:
        path.unlink()
        print(f"\n  {SYM_OK} Profile deleted. Run `forge profile` to set up again.\n")
    except OSError as e:
        print(f"\n  {SYM_WARN} Could not delete profile: {e}\n")


def run_profile(show: bool = False, edit: bool = False,
                reset: bool = False) -> None:
    """Entry point for forge profile command."""
    if reset:
        _reset_profile()
        return

    if show:
        profile = load_profile()
        if not profile:
            print("\n  No profile found. Run `forge profile` to set one up.\n")
            return
        _show_profile(profile)
        return

    if edit:
        existing = load_profile()
        if not existing:
            print("\n  No existing profile found. Starting fresh setup.\n")
        _run_setup(existing=existing if existing else None)
        return

    # No flags: setup if no profile, show if profile exists
    if has_profile():
        _show_profile(load_profile())
    else:
        _run_setup()
