"""
Tests for forge.builder module.
Uses unittest.mock to avoid real API calls.
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest


# ---------------------------------------------------------------------------
# Test: run_task returns the correct tuple shape
# ---------------------------------------------------------------------------

def test_run_task_returns_tuple():
    """run_task must always return (bool, str, str)."""
    from forge.builder import run_task

    # Mock anyio.from_thread.run to return a pre-built result
    with patch("anyio.run", return_value=(True, "output", "")):
        result = run_task(Path("/tmp/fake"), "do something")

        assert isinstance(result, tuple)
        assert len(result) == 3
        success, stdout, stderr = result
        assert isinstance(success, bool)
        assert isinstance(stdout, str)
        assert isinstance(stderr, str)


# ---------------------------------------------------------------------------
# Test: run_task handles missing SDK gracefully
# ---------------------------------------------------------------------------

def test_run_task_handles_import_error():
    """If SDK not available, returns structured failure not ImportError."""
    import forge.builder as builder_module

    # Reset SDK availability flag
    original = builder_module._SDK_AVAILABLE
    builder_module._SDK_AVAILABLE = None

    with patch.dict(sys.modules, {"claude_code_sdk": None}):
        with pytest.raises(SystemExit) as exc_info:
            builder_module._check_sdk_available()

        assert exc_info.value.code == 1

    # Restore
    builder_module._SDK_AVAILABLE = original


# ---------------------------------------------------------------------------
# Test: _detect_test_command for Python projects
# ---------------------------------------------------------------------------

def test_detect_test_command_python(tmp_path: Path):
    """Detects pytest for Python projects."""
    from forge.builder import _detect_test_command

    # Create a setup.py to trigger Python detection
    (tmp_path / "setup.py").write_text("from setuptools import setup; setup()")

    cmd = _detect_test_command(tmp_path)
    assert cmd == ["python", "-m", "pytest", "--tb=short", "-q"]


# ---------------------------------------------------------------------------
# Test: _detect_test_command for Node projects
# ---------------------------------------------------------------------------

def test_detect_test_command_node(tmp_path: Path):
    """Detects npm test for Node projects."""
    from forge.builder import _detect_test_command

    import json
    pkg = {"scripts": {"test": "jest"}}
    (tmp_path / "package.json").write_text(json.dumps(pkg))

    cmd = _detect_test_command(tmp_path)
    assert cmd == ["npm", "test", "--", "--passWithNoTests"]


# ---------------------------------------------------------------------------
# Test: _detect_test_command returns empty for unknown projects
# ---------------------------------------------------------------------------

def test_detect_test_command_none(tmp_path: Path):
    """Returns empty list for unknown project type."""
    from forge.builder import _detect_test_command

    cmd = _detect_test_command(tmp_path)
    assert cmd == []


# ---------------------------------------------------------------------------
# Test: run_tests returns True when no test runner found
# ---------------------------------------------------------------------------

def test_run_tests_no_runner(tmp_path: Path):
    """Returns True when no test runner found (not a failure)."""
    from forge.builder import run_tests

    passed, stdout, stderr = run_tests(tmp_path)
    assert passed is True
    assert "skipped" in stdout.lower() or "no test runner" in stdout.lower()
    assert stderr == ""


# ---------------------------------------------------------------------------
# Test: run_task with successful SDK response
# ---------------------------------------------------------------------------

def test_run_task_success_flow():
    """run_task returns success when SDK query completes without error."""
    from forge.builder import run_task

    with patch("anyio.run", return_value=(True, "Task complete.", "")):
        success, stdout, stderr = run_task(Path("/tmp/fake"), "build a thing")

        assert success is True
        assert stdout == "Task complete."
        assert stderr == ""


# ---------------------------------------------------------------------------
# Test: run_task with SDK error returns structured error
# ---------------------------------------------------------------------------

def test_run_task_error_flow():
    """run_task returns structured error on SDK failure."""
    from forge.builder import run_task

    with patch("anyio.run", return_value=(False, "", "PROCESS_ERROR: exit code 1")):
        success, stdout, stderr = run_task(Path("/tmp/fake"), "failing task")

        assert success is False
        assert "PROCESS_ERROR" in stderr
