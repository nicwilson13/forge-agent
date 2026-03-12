"""Tests for forge.visual_qa module."""

import base64
from pathlib import Path
from unittest.mock import patch

from forge.visual_qa import (
    is_visual_task,
    is_playwright_available,
    is_dev_server_running,
    encode_screenshot,
    run_visual_qa,
    VISUAL_QA_SIGNALS,
    VIEWPORTS,
)
from forge.cost_tracker import TokenUsage


# ---------------------------------------------------------------------------
# is_visual_task
# ---------------------------------------------------------------------------

def test_is_visual_task_component_signal():
    """Task with 'component' in title is a visual task."""
    assert is_visual_task("Build Dashboard component", "") is True


def test_is_visual_task_no_signal():
    """Task with no visual signals is not a visual task."""
    assert is_visual_task("Set up database schema", "Create PostgreSQL migrations") is False


def test_is_visual_task_case_insensitive():
    """Signal detection is case-insensitive."""
    assert is_visual_task("Build DASHBOARD Component", "") is True
    assert is_visual_task("Add CSS Styles", "") is True


def test_is_visual_task_description_signal():
    """Signal in description (not title) still triggers."""
    assert is_visual_task("Configure API routes", "add a form component") is True


# ---------------------------------------------------------------------------
# is_playwright_available
# ---------------------------------------------------------------------------

def test_is_playwright_available_not_installed(monkeypatch):
    """Returns False when playwright not on PATH."""
    monkeypatch.setattr(
        "forge.visual_qa.subprocess.run",
        lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError("not found")),
    )
    assert is_playwright_available() is False


# ---------------------------------------------------------------------------
# is_dev_server_running
# ---------------------------------------------------------------------------

def test_is_dev_server_running_false_when_nothing_listening():
    """Returns False when no server on the port."""
    # Use a port unlikely to have anything listening
    assert is_dev_server_running(port=19876) is False


# ---------------------------------------------------------------------------
# encode_screenshot
# ---------------------------------------------------------------------------

def test_encode_screenshot_missing_file():
    """Returns empty string for missing file."""
    assert encode_screenshot(Path("/nonexistent/file.png")) == ""


def test_encode_screenshot_valid_file(tmp_path):
    """Returns non-empty base64 string for valid PNG."""
    # Create a minimal file (doesn't need to be valid PNG for encoding)
    test_file = tmp_path / "test.png"
    test_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    result = encode_screenshot(test_file)
    assert result != ""
    # Verify it's valid base64
    decoded = base64.b64decode(result)
    assert decoded.startswith(b"\x89PNG")


# ---------------------------------------------------------------------------
# run_visual_qa
# ---------------------------------------------------------------------------

def test_run_visual_qa_skips_non_visual_task(tmp_path):
    """Returns (None, reason, empty_usage) for non-visual task."""
    result, reason, usage = run_visual_qa(
        tmp_path, "Set up database schema", "Create PostgreSQL migrations"
    )
    assert result is None
    assert "no frontend signals" in reason
    assert usage.total_tokens == 0


def test_run_visual_qa_skips_when_playwright_missing(monkeypatch, tmp_path):
    """Returns (None, reason, empty_usage) when playwright not available."""
    monkeypatch.setattr("forge.visual_qa.is_playwright_available", lambda: False)
    result, reason, usage = run_visual_qa(
        tmp_path, "Build Dashboard component", "Create the main UI"
    )
    assert result is None
    assert "playwright" in reason.lower()
    assert usage.total_tokens == 0


def test_run_visual_qa_never_raises(monkeypatch, tmp_path):
    """run_visual_qa() catches all exceptions and returns (None, ...)."""
    # Make is_visual_task return True, then force an exception
    monkeypatch.setattr("forge.visual_qa.is_visual_task", lambda *a: True)
    monkeypatch.setattr("forge.visual_qa.is_playwright_available",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    result, reason, usage = run_visual_qa(
        tmp_path, "Build component", "Create UI"
    )
    assert result is None
    assert "unexpected error" in reason or "boom" in reason
    assert isinstance(usage, TokenUsage)
