"""
Advanced project configuration options for forge new.

Presented as an optional block after the main interview questions.
A single Enter at the gate question skips the entire block.
Individual questions are also skippable with Enter.

Options feed into CLAUDE.md generation to produce accurate
technical constraints from day one.

This module has zero imports from other forge modules.
"""

import shutil
import sys


def _supports_unicode() -> bool:
    """Check if stdout encoding supports Unicode."""
    encoding = getattr(sys.stdout, "encoding", "") or ""
    return encoding.lower().replace("-", "") in (
        "utf8", "utf16", "utf32", "utf8sig",
    )


LIGHT_CHAR = "\u2500" if _supports_unicode() else "-"


ADVANCED_OPTIONS = [
    {
        "key": "structure",
        "label": "Project Structure",
        "options": [
            "single app",
            "monorepo (Turborepo)",
            "monorepo (Nx)",
            "monorepo (pnpm workspaces)",
        ],
    },
    {
        "key": "api_style",
        "label": "API Style",
        "options": ["REST", "GraphQL", "tRPC", "REST + GraphQL", "none"],
    },
    {
        "key": "linting",
        "label": "Linting / Formatting",
        "options": ["ESLint + Prettier", "Biome", "ESLint only", "none"],
    },
    {
        "key": "typescript_strictness",
        "label": "TypeScript Strictness",
        "options": [
            "strict (recommended)",
            "standard",
            "loose",
            "not using TypeScript",
        ],
    },
    {
        "key": "testing_approach",
        "label": "Testing Approach",
        "options": [
            "TDD",
            "coverage threshold",
            "write tests for critical paths",
            "integration tests only",
            "no testing preference",
        ],
    },
    {
        "key": "branch_strategy",
        "label": "Branch Strategy",
        "options": [
            "main only",
            "main + staging",
            "main + develop + staging",
            "gitflow",
        ],
    },
    {
        "key": "ci_cd",
        "label": "CI/CD",
        "options": ["GitHub Actions", "GitLab CI", "CircleCI", "none"],
    },
    {
        "key": "target_platforms",
        "label": "Target Platforms",
        "options": [
            "web only",
            "web + mobile (Expo)",
            "web + mobile (Capacitor)",
            "web + desktop (Tauri)",
            "mobile only (Expo)",
        ],
    },
    {
        "key": "accessibility",
        "label": "Accessibility",
        "options": [
            "WCAG 2.1 AA",
            "WCAG 2.1 AAA",
            "basic a11y",
            "none specified",
        ],
    },
    {
        "key": "i18n",
        "label": "Internationalization",
        "options": ["yes (i18n required)", "no"],
    },
    {
        "key": "security",
        "label": "Security / Compliance",
        "options": [
            "standard",
            "SOC2-conscious",
            "HIPAA-adjacent",
            "PCI-DSS",
            "none",
        ],
    },
]


# Maps raw option values to clean display labels for CLAUDE.md
LABEL_MAP = {
    # Structure
    "single app": "Single app",
    "monorepo (Turborepo)": "Monorepo (Turborepo)",
    "monorepo (Nx)": "Monorepo (Nx)",
    "monorepo (pnpm workspaces)": "Monorepo (pnpm workspaces)",
    # API
    "REST": "REST",
    "GraphQL": "GraphQL",
    "tRPC": "tRPC",
    "REST + GraphQL": "REST + GraphQL",
    # Linting
    "ESLint + Prettier": "ESLint + Prettier",
    "Biome": "Biome",
    "ESLint only": "ESLint only",
    # TypeScript
    "strict (recommended)": "Strict mode (tsconfig strict: true)",
    "standard": "Standard TypeScript",
    "loose": "Loose TypeScript",
    "not using TypeScript": "Not using TypeScript",
    # Testing
    "TDD": "Test-driven development",
    "coverage threshold": "Coverage threshold",
    "write tests for critical paths": "Tests for critical paths",
    "integration tests only": "Integration tests only",
    "no testing preference": "No testing preference",
    # Branches
    "main only": "main only",
    "main + staging": "main + staging",
    "main + develop + staging": "main + develop + staging",
    "gitflow": "Gitflow",
    # CI/CD
    "GitHub Actions": "GitHub Actions",
    "GitLab CI": "GitLab CI",
    "CircleCI": "CircleCI",
    # Platforms
    "web only": "Web only",
    "web + mobile (Expo)": "Web + mobile (Expo)",
    "web + mobile (Capacitor)": "Web + mobile (Capacitor)",
    "web + desktop (Tauri)": "Web + desktop (Tauri)",
    "mobile only (Expo)": "Mobile only (Expo)",
    # Accessibility
    "WCAG 2.1 AA": "WCAG 2.1 AA compliance required",
    "WCAG 2.1 AAA": "WCAG 2.1 AAA compliance required",
    "basic a11y": "Basic accessibility",
    "none specified": "None specified",
    # i18n
    "yes (i18n required)": "Internationalization required",
    "no": "No",
    # Security
    "standard": "Standard",
    "SOC2-conscious": "SOC2-conscious",
    "HIPAA-adjacent": "HIPAA-adjacent",
    "PCI-DSS": "PCI-DSS",
}

# Maps option keys to short display labels for CLAUDE.md section
_KEY_LABELS = {
    "structure": "Structure",
    "api_style": "API Style",
    "linting": "Linting",
    "typescript_strictness": "TypeScript",
    "testing_approach": "Testing",
    "branch_strategy": "Branches",
    "ci_cd": "CI/CD",
    "target_platforms": "Platforms",
    "accessibility": "Accessibility",
    "i18n": "Internationalization",
    "security": "Security",
    "coverage_threshold": None,  # displayed inline with testing_approach
}


def collect_advanced_options() -> dict:
    """
    Present the advanced options block interactively.

    A single Enter at the gate question skips the entire block.
    Individual questions are also skippable with Enter.
    Returns dict of key -> value for answered options only.
    Returns empty dict on EOFError/OSError (non-interactive context).
    """
    width = shutil.get_terminal_size((64, 24)).columns
    divider_width = min(width - 4, 56)

    print("  " + LIGHT_CHAR * divider_width)
    print("  Advanced Options (optional)")
    print("  Press Enter to skip, or type anything to configure:")
    try:
        gate = input("  > ").strip()
    except (EOFError, OSError):
        return {}

    if not gate:
        return {}

    result = {}
    for option in ADVANCED_OPTIONS:
        key = option["key"]
        label = option["label"]
        options = option["options"]

        print()
        print("  " + LIGHT_CHAR * divider_width)
        print(f"  {label}")
        print(f"  {format_options_for_display(options, width)}")
        answer = input("  (Enter to skip): ").strip()

        if not answer:
            continue

        result[key] = answer

        # Coverage threshold follow-up
        if key == "testing_approach" and answer.lower() == "coverage threshold":
            threshold = input("  Coverage threshold % (Enter for 80): ").strip()
            result["coverage_threshold"] = threshold if threshold else "80"

    return result


def format_options_for_display(options: list, terminal_width: int = 80) -> str:
    """
    Format an options list for display, wrapped to terminal width.

    Continuation lines align with the first item after "Options: ".
    """
    prefix = "Options: "
    indent = " " * len(prefix)
    max_line = terminal_width - 4  # account for "  " indent on both sides

    lines = []
    current_line = prefix

    for i, opt in enumerate(options):
        separator = ", " if i < len(options) - 1 else ""
        addition = opt + separator

        if i == 0:
            current_line += addition
        elif len(current_line) + len(addition) > max_line:
            lines.append(current_line)
            current_line = indent + addition
        else:
            current_line += addition

    lines.append(current_line)
    return "\n  ".join(lines)


def advanced_options_to_context(advanced: dict) -> str:
    """
    Convert advanced options dict to a context string for doc generation.
    Returns empty string if advanced dict is empty.
    """
    if not advanced:
        return ""

    lines = ["Advanced Project Configuration:"]
    for option in ADVANCED_OPTIONS:
        key = option["key"]
        if key in advanced:
            value = advanced[key]
            label = _KEY_LABELS.get(key, key)
            if label is None:
                continue
            display = value
            if key == "testing_approach" and "coverage_threshold" in advanced:
                display = f"{value} (minimum {advanced['coverage_threshold']}%)"
            lines.append(f"- {label}: {display}")

    return "\n".join(lines)


def advanced_options_to_claude_md_section(advanced: dict) -> str:
    """
    Convert advanced options to a ## Project Configuration section
    for direct inclusion in CLAUDE.md.

    Returns empty string if advanced dict is empty.
    Uses LABEL_MAP for clean display strings.
    """
    if not advanced:
        return ""

    lines = ["## Project Configuration", ""]
    for option in ADVANCED_OPTIONS:
        key = option["key"]
        if key in advanced:
            value = advanced[key]
            label = _KEY_LABELS.get(key, key)
            if label is None:
                continue

            display = LABEL_MAP.get(value, value)

            # Special: append coverage threshold
            if key == "testing_approach" and "coverage_threshold" in advanced:
                threshold = advanced["coverage_threshold"]
                display = f"{display} - minimum {threshold}%"

            lines.append(f"**{label}:** {display}")

    return "\n".join(lines)
