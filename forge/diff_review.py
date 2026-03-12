"""
Semantic diff review for Forge.

After each task completes, reads the git diff and uses Claude to
verify that the changes are consistent with the task requirements.

This is a lightweight sanity check, not a full code review:
- Are the changes scoped to what the task asked for?
- Are there unexpected deletions?
- Is the change volume appropriate (not massively over/under built)?

Uses Sonnet (moderate stakes, fast) rather than Opus.
Runs after build, before commit. Never blocks the build on its own -
flags issues as warnings and records them in the build log.

Imports: stdlib, forge.cost_tracker, forge.git_utils.
"""

import os
import re
import time
from pathlib import Path

import anthropic

from forge.cost_tracker import MODEL_SONNET, TokenUsage
from forge.git_utils import get_diff

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Max diff lines to send for review (larger diffs are skipped)
MAX_DIFF_LINES = 2000

# Min diff lines to bother reviewing (tiny diffs are auto-approved)
MIN_DIFF_LINES = 5

# Model for diff review (moderate stakes - Sonnet is appropriate)
REVIEW_MODEL = MODEL_SONNET

MAX_RETRIES = 3
BACKOFF_SCHEDULE = [5, 15, 30]

REVIEW_SYSTEM_PROMPT = """You are a senior engineer reviewing a code change.
You will be given a task description and the git diff of changes made.

Evaluate whether the changes are appropriate for the task.
Be concise. Focus only on significant issues:

1. SCOPE: Are the changes focused on what was asked, or is there
   unrelated code changed?
2. DELETIONS: Are any files or functions deleted that look unintentional?
3. VOLUME: Is the change size appropriate? (flag if 10x more or less
   than expected for the task)

Respond in this exact format:
APPROVED
[one sentence: what was done and looks correct]

or:

FLAGGED
[one sentence summary]
- [specific issue 1]
- [specific issue 2]

Only flag genuine concerns. Minor style or approach differences are fine.
If in doubt, approve.
"""


# ---------------------------------------------------------------------------
# Diff helpers
# ---------------------------------------------------------------------------

def get_working_diff(project_dir: Path) -> str:
    """
    Get the git diff of all uncommitted changes.

    Runs: git diff HEAD
    Returns the diff string, or empty string if no changes or error.
    Never raises.
    """
    try:
        return get_diff(project_dir, staged_only=False)
    except Exception:
        return ""


def get_staged_diff(project_dir: Path) -> str:
    """
    Get the git diff of staged changes only.

    Runs: git diff --cached
    Returns diff string or empty string.
    Never raises.
    """
    try:
        return get_diff(project_dir, staged_only=True)
    except Exception:
        return ""


def summarize_diff(diff: str) -> dict:
    """
    Summarize a diff without sending it to the API.

    Returns dict with: lines_added, lines_removed, files_changed,
    files_deleted, files_added, total_lines.
    """
    lines_added = 0
    lines_removed = 0
    files_changed: list[str] = []
    files_deleted: list[str] = []
    files_added: list[str] = []

    current_minus_file = ""
    current_plus_file = ""

    for line in diff.splitlines():
        if line.startswith("--- "):
            current_minus_file = line[4:].strip()
            # Strip a/ prefix
            if current_minus_file.startswith("a/"):
                current_minus_file = current_minus_file[2:]
        elif line.startswith("+++ "):
            current_plus_file = line[4:].strip()
            # Strip b/ prefix
            if current_plus_file.startswith("b/"):
                current_plus_file = current_plus_file[2:]

            # Determine file status
            if current_minus_file == "/dev/null":
                # New file
                files_added.append(current_plus_file)
                if current_plus_file not in files_changed:
                    files_changed.append(current_plus_file)
            elif current_plus_file == "/dev/null":
                # Deleted file
                files_deleted.append(current_minus_file)
                if current_minus_file not in files_changed:
                    files_changed.append(current_minus_file)
            else:
                # Modified file
                if current_plus_file not in files_changed:
                    files_changed.append(current_plus_file)
        elif line.startswith("+") and not line.startswith("+++"):
            lines_added += 1
        elif line.startswith("-") and not line.startswith("---"):
            lines_removed += 1

    return {
        "lines_added": lines_added,
        "lines_removed": lines_removed,
        "files_changed": files_changed,
        "files_deleted": files_deleted,
        "files_added": files_added,
        "total_lines": len(diff.splitlines()),
    }


def should_review_diff(diff: str) -> tuple[bool, str]:
    """
    Decide whether to send this diff for review.

    Returns (should_review: bool, reason: str).
    """
    if not diff.strip():
        return (False, "no changes")

    line_count = len(diff.splitlines())

    if line_count < MIN_DIFF_LINES:
        return (False, "change too small")

    if line_count > MAX_DIFF_LINES:
        return (False, f"diff too large ({line_count} lines)")

    return (True, f"{line_count} lines")


# ---------------------------------------------------------------------------
# Review API call
# ---------------------------------------------------------------------------

def _parse_review_response(text: str) -> tuple[str, list[str]]:
    """Parse APPROVED/FLAGGED response into (verdict, issues)."""
    lines = text.strip().split("\n")
    first = lines[0].strip().upper() if lines else ""

    if first == "APPROVED":
        return "approved", []

    if first == "FLAGGED":
        issues = []
        for line in lines[2:]:  # skip FLAGGED line and summary
            line = line.strip()
            if line.startswith("- "):
                issues.append(line[2:])
        return "flagged", issues

    # Unexpected format - treat as approved to avoid false negatives
    return "approved", []


def review_diff(
    project_dir: Path,
    task_title: str,
    task_description: str,
    diff: str,
    model: str = REVIEW_MODEL,
) -> tuple[str, list[str], TokenUsage]:
    """
    Send the diff to Claude for semantic review.

    Returns (verdict, issues, usage) where:
    - verdict: "approved" | "flagged" | "error"
    - issues: list of issue strings (empty if approved)
    - usage: TokenUsage from the API call

    Never raises - returns ("error", [str(exception)], empty) on failure.
    """
    try:
        # Truncate diff to MAX_DIFF_LINES
        diff_lines = diff.splitlines()
        if len(diff_lines) > MAX_DIFF_LINES:
            diff_lines = diff_lines[:MAX_DIFF_LINES]
            diff = "\n".join(diff_lines) + f"\n... (truncated at {MAX_DIFF_LINES} lines)"

        summary = summarize_diff(diff)

        user_prompt = f"""Task: {task_title}
Description: {task_description}

Diff summary: {summary['lines_added']} lines added, {summary['lines_removed']} removed, \
{len(summary['files_changed'])} files changed
Files: {', '.join(summary['files_changed'][:10])}
{f"New files: {', '.join(summary['files_added'][:5])}" if summary['files_added'] else ""}
{f"Deleted files: {', '.join(summary['files_deleted'][:5])}" if summary['files_deleted'] else ""}

Git diff:
{diff}
"""

        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        last_error = ""

        for attempt in range(MAX_RETRIES):
            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=512,
                    system=REVIEW_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                text = response.content[0].text
                usage = TokenUsage(
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    model=model,
                )
                verdict, issues = _parse_review_response(text)
                return verdict, issues, usage

            except anthropic.AuthenticationError:
                return ("error", ["API authentication failed"], TokenUsage())
            except (
                anthropic.RateLimitError,
                anthropic.APIConnectionError,
                anthropic.APITimeoutError,
                anthropic.APIStatusError,
            ) as e:
                last_error = str(e)
                if attempt < MAX_RETRIES - 1:
                    backoff = BACKOFF_SCHEDULE[min(attempt, len(BACKOFF_SCHEDULE) - 1)]
                    time.sleep(backoff)

        return ("error", [f"API unavailable: {last_error}"], TokenUsage())

    except Exception as e:
        return ("error", [str(e)], TokenUsage())


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

def run_diff_review(
    project_dir: Path,
    task_title: str,
    task_description: str,
) -> tuple[str, list[str], TokenUsage]:
    """
    Full diff review pipeline.

    Returns ("skipped", [reason], empty_usage) when skipped.
    Never raises.
    """
    try:
        diff = get_working_diff(project_dir)

        should_review, reason = should_review_diff(diff)
        if not should_review:
            return ("skipped", [reason], TokenUsage())

        return review_diff(project_dir, task_title, task_description, diff)

    except Exception as e:
        return ("error", [f"unexpected error: {e}"], TokenUsage())


# ---------------------------------------------------------------------------
# Display formatting
# ---------------------------------------------------------------------------

def format_review_output(verdict: str, issues: list[str],
                         diff_summary: dict | None = None) -> str:
    """
    Format the review result for terminal display.
    """
    if verdict == "approved":
        if diff_summary:
            files = len(diff_summary["files_changed"])
            added = diff_summary["lines_added"]
            return f"Changes look correct - {files} file(s), {added} lines added"
        return "Changes look correct"

    if verdict == "flagged":
        parts = [f"Review flagged {len(issues)} issue(s):"]
        for issue in issues[:5]:
            parts.append(f"    - {issue}")
        return "\n".join(parts)

    if verdict == "skipped":
        reason = issues[0] if issues else "unknown"
        return f"diff review skipped - {reason}"

    if verdict == "error":
        reason = issues[0] if issues else "unknown"
        return f"diff review error - {reason}"

    return f"diff review: {verdict}"
