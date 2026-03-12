"""Tests for forge.diff_review module."""

from pathlib import Path

from forge.diff_review import (
    summarize_diff,
    should_review_diff,
    format_review_output,
    run_diff_review,
    MAX_DIFF_LINES,
    MIN_DIFF_LINES,
    _parse_review_response,
)
from forge.git_utils import count_diff_lines


# ---------------------------------------------------------------------------
# Sample diffs
# ---------------------------------------------------------------------------

SAMPLE_DIFF = """\
diff --git a/src/Login.tsx b/src/Login.tsx
new file mode 100644
--- /dev/null
+++ b/src/Login.tsx
@@ -0,0 +1,4 @@
+import React from 'react'
+export function Login() {
+  return <div>Login</div>
+}
diff --git a/src/old.ts b/src/old.ts
deleted file mode 100644
--- a/src/old.ts
+++ /dev/null
@@ -1,2 +0,0 @@
-export const old = true
-export const legacy = false
"""

MODIFIED_DIFF = """\
diff --git a/src/app.ts b/src/app.ts
--- a/src/app.ts
+++ b/src/app.ts
@@ -1,3 +1,4 @@
 import express from 'express'
+import cors from 'cors'
 const app = express()
-app.listen(3000)
+app.use(cors())
+app.listen(4000)
"""


# ---------------------------------------------------------------------------
# summarize_diff
# ---------------------------------------------------------------------------

def test_summarize_diff_counts_additions():
    """Lines starting with + (not +++) counted as additions."""
    result = summarize_diff(SAMPLE_DIFF)
    assert result["lines_added"] == 4


def test_summarize_diff_counts_removals():
    """Lines starting with - (not ---) counted as removals."""
    result = summarize_diff(SAMPLE_DIFF)
    assert result["lines_removed"] == 2


def test_summarize_diff_detects_deleted_files():
    """Files deleted (/dev/null in +++) added to files_deleted."""
    result = summarize_diff(SAMPLE_DIFF)
    assert "src/old.ts" in result["files_deleted"]


def test_summarize_diff_detects_new_files():
    """Files added (/dev/null in ---) added to files_added."""
    result = summarize_diff(SAMPLE_DIFF)
    assert "src/Login.tsx" in result["files_added"]


# ---------------------------------------------------------------------------
# should_review_diff
# ---------------------------------------------------------------------------

def test_should_review_empty_diff():
    """Empty diff returns (False, 'no changes')."""
    result, reason = should_review_diff("")
    assert result is False
    assert "no changes" in reason


def test_should_review_small_diff():
    """Diff under MIN_DIFF_LINES returns (False, 'change too small')."""
    small = "+line\n" * (MIN_DIFF_LINES - 1)
    result, reason = should_review_diff(small)
    assert result is False
    assert "too small" in reason


def test_should_review_large_diff():
    """Diff over MAX_DIFF_LINES returns (False, 'diff too large...')."""
    large = "+line\n" * (MAX_DIFF_LINES + 1)
    result, reason = should_review_diff(large)
    assert result is False
    assert "too large" in reason


def test_should_review_normal_diff():
    """Diff between MIN and MAX returns (True, ...)."""
    normal = "+line\n" * 100
    result, reason = should_review_diff(normal)
    assert result is True


# ---------------------------------------------------------------------------
# format_review_output
# ---------------------------------------------------------------------------

def test_format_review_output_approved():
    """Approved verdict formats correctly."""
    output = format_review_output("approved", [])
    assert "correct" in output.lower()


def test_format_review_output_flagged():
    """Flagged verdict includes issue list."""
    output = format_review_output("flagged", ["Unexpected deletion", "Scope too broad"])
    assert "flagged" in output.lower()
    assert "Unexpected deletion" in output
    assert "Scope too broad" in output


def test_format_review_output_skipped():
    """Skipped verdict includes reason."""
    output = format_review_output("skipped", ["no changes"])
    assert "skipped" in output.lower()
    assert "no changes" in output


# ---------------------------------------------------------------------------
# _parse_review_response
# ---------------------------------------------------------------------------

def test_parse_review_approved():
    """APPROVED response parsed correctly."""
    verdict, issues = _parse_review_response("APPROVED\nLooks good, changes match the task.")
    assert verdict == "approved"
    assert issues == []


def test_parse_review_flagged():
    """FLAGGED response parsed with issues."""
    text = "FLAGGED\nSome concerns.\n- Deleted auth.ts unexpectedly\n- Too many files changed"
    verdict, issues = _parse_review_response(text)
    assert verdict == "flagged"
    assert len(issues) == 2
    assert "Deleted auth.ts" in issues[0]


def test_parse_review_unexpected_format():
    """Unexpected format defaults to approved."""
    verdict, issues = _parse_review_response("Some random text")
    assert verdict == "approved"
    assert issues == []


# ---------------------------------------------------------------------------
# run_diff_review
# ---------------------------------------------------------------------------

def test_run_diff_review_never_raises(monkeypatch, tmp_path):
    """run_diff_review catches all exceptions."""
    def _boom(project_dir, staged_only=False):
        raise RuntimeError("boom")

    monkeypatch.setattr("forge.diff_review.get_diff", _boom)

    verdict, issues, usage = run_diff_review(tmp_path, "Some task", "Some desc")
    # Should not raise - returns error or skipped
    assert verdict in ("error", "skipped")


# ---------------------------------------------------------------------------
# count_diff_lines (git_utils)
# ---------------------------------------------------------------------------

def test_count_diff_lines_basic():
    """count_diff_lines returns correct (added, removed) tuple."""
    added, removed = count_diff_lines(MODIFIED_DIFF)
    assert added == 3  # +import cors, +app.use(cors()), +app.listen(4000)
    assert removed == 1  # -app.listen(3000)
