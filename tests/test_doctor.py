"""
Tests for forge.commands.doctor module.
All subprocess and API calls are mocked.
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from forge.commands.doctor import (
    CheckStatus,
    _check_python_version,
    _check_claude_code_installed,
    _check_claude_code_authenticated,
    _check_api_key_set,
    _check_api_key_valid,
    _check_git_installed,
    _check_git_identity,
    _check_vision_md,
    _check_requirements_md,
    _check_claude_md,
    run_doctor,
)


def test_check_python_version_passes_current():
    """Current Python version should pass the 3.10+ check."""
    result = _check_python_version()
    assert result.status == CheckStatus.PASS
    assert "3.10+ required" in result.detail


def test_check_python_version_fails_old():
    """Python 2.7 should fail the version check."""
    from types import SimpleNamespace
    fake_version = SimpleNamespace(major=2, minor=7, micro=18)
    with patch.object(sys, "version_info", fake_version):
        result = _check_python_version()
    assert result.status == CheckStatus.FAIL
    assert result.fix is not None


def test_check_api_key_set_missing(monkeypatch):
    """Returns FAIL when ANTHROPIC_API_KEY not in environment."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = _check_api_key_set()
    assert result.status == CheckStatus.FAIL
    assert "not set" in result.detail


def test_check_api_key_set_present(monkeypatch):
    """Returns PASS when ANTHROPIC_API_KEY is set."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-testkey1234567890")
    result = _check_api_key_set()
    assert result.status == CheckStatus.PASS


def test_check_api_key_set_masks_key(monkeypatch):
    """Detail line shows masked key, not full key."""
    full_key = "sk-ant-api03-testkey1234567890abcdef"
    monkeypatch.setenv("ANTHROPIC_API_KEY", full_key)
    result = _check_api_key_set()
    assert full_key not in result.detail
    assert "sk-ant-api" in result.detail
    assert "..." in result.detail


def test_check_git_identity_skip_if_no_git():
    """Git identity check returns SKIP when git not on PATH."""
    result = _check_git_identity(git_installed=False)
    assert result.status == CheckStatus.SKIP


def test_check_vision_md_missing(tmp_path):
    """Returns FAIL when VISION.md does not exist."""
    result = _check_vision_md(tmp_path)
    assert result.status == CheckStatus.FAIL
    assert result.fix is not None


def test_check_vision_md_too_short(tmp_path):
    """Returns WARN when VISION.md has fewer than 150 words."""
    (tmp_path / "VISION.md").write_text(" ".join(["word"] * 50))
    result = _check_vision_md(tmp_path)
    assert result.status == CheckStatus.FAIL


def test_check_vision_md_medium(tmp_path):
    """Returns WARN when VISION.md has 150-299 words."""
    (tmp_path / "VISION.md").write_text(" ".join(["word"] * 200))
    result = _check_vision_md(tmp_path)
    assert result.status == CheckStatus.WARN


def test_check_vision_md_sufficient(tmp_path):
    """Returns PASS when VISION.md has 300+ words."""
    (tmp_path / "VISION.md").write_text(" ".join(["word"] * 350))
    result = _check_vision_md(tmp_path)
    assert result.status == CheckStatus.PASS
    assert "350 words" in result.detail


def test_check_requirements_md_counts_checkboxes(tmp_path):
    """Correctly counts - [ ] items in REQUIREMENTS.md."""
    content = "# Req\n" + "\n".join(f"- [ ] Item {i}" for i in range(15))
    (tmp_path / "REQUIREMENTS.md").write_text(content)
    result = _check_requirements_md(tmp_path)
    assert result.status == CheckStatus.PASS
    assert "15 requirements" in result.detail


def test_check_requirements_md_few(tmp_path):
    """Returns WARN when REQUIREMENTS.md has few items."""
    content = "# Req\n- [ ] Item 1\n- [ ] Item 2\n"
    (tmp_path / "REQUIREMENTS.md").write_text(content)
    result = _check_requirements_md(tmp_path)
    assert result.status == CheckStatus.WARN


def test_check_requirements_md_narrative_format(tmp_path):
    """REQUIREMENTS.md with substantial content but no checkboxes should pass."""
    content = "# Requirements\n\n" + "Feature description paragraph. " * 100
    (tmp_path / "REQUIREMENTS.md").write_text(content)
    result = _check_requirements_md(tmp_path)
    assert result.status == CheckStatus.PASS
    assert "narrative" in result.detail


def test_check_requirements_md_empty_file(tmp_path):
    """REQUIREMENTS.md that exists but is nearly empty should fail."""
    (tmp_path / "REQUIREMENTS.md").write_text("# Requirements\n")
    result = _check_requirements_md(tmp_path)
    assert result.status == CheckStatus.FAIL
    assert "empty or too brief" in result.detail


def test_check_claude_md_tech_stack(tmp_path):
    """Returns PASS when CLAUDE.md has tech stack content."""
    content = "# CLAUDE.md\n\n## Tech Stack\n- Language: Python 3.11\n- Framework: FastAPI\n"
    (tmp_path / "CLAUDE.md").write_text(content)
    result = _check_claude_md(tmp_path)
    assert result.status == CheckStatus.PASS


def test_check_claude_md_empty_stack(tmp_path):
    """Returns WARN when tech stack section is empty."""
    content = "# CLAUDE.md\n\n## Tech Stack\n\n## Other\nstuff\n"
    (tmp_path / "CLAUDE.md").write_text(content)
    result = _check_claude_md(tmp_path)
    assert result.status == CheckStatus.WARN


def test_run_doctor_exits_1_on_failure(monkeypatch, capsys):
    """run_doctor exits with code 1 when any check fails."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    # Mock all subprocess calls to avoid real CLI checks
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "git version 2.43.0"
    mock_result.stderr = ""

    def mock_run(cmd, **kwargs):
        if cmd[0] == "claude":
            raise FileNotFoundError("claude not found")
        return mock_result

    with patch("forge.commands.doctor.subprocess.run", side_effect=mock_run):
        with patch("forge.commands.doctor.shutil.which", return_value=None):
            with pytest.raises(SystemExit) as exc_info:
                run_doctor(Path("/tmp/nonexistent"))
    assert exc_info.value.code == 1


def test_run_doctor_exits_0_on_warnings_only(monkeypatch, capsys, tmp_path):
    """run_doctor exits with code 0 when only warnings (no failures)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-testkey123456")

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "1.2.3"
    mock_result.stderr = ""

    mock_git_result = MagicMock()
    mock_git_result.returncode = 0
    mock_git_result.stdout = "git version 2.43.0"
    mock_git_result.stderr = ""

    mock_email_result = MagicMock()
    mock_email_result.returncode = 0
    mock_email_result.stdout = ""  # empty = warning, not fail
    mock_email_result.stderr = ""

    def mock_run(cmd, **kwargs):
        if cmd[0] == "claude":
            if "--version" in cmd:
                return mock_result
            # test call
            return mock_result
        if cmd == ["git", "--version"]:
            return mock_git_result
        if cmd == ["git", "config", "--global", "user.email"]:
            return mock_email_result
        return mock_result

    mock_api = MagicMock()

    with patch("forge.commands.doctor.subprocess.run", side_effect=mock_run):
        with patch("forge.commands.doctor.shutil.which", return_value="/usr/bin/claude"):
            with patch.dict("sys.modules", {"anthropic": MagicMock()}):
                with patch("forge.commands.doctor._check_api_key_valid") as mock_valid:
                    mock_valid.return_value = _make_pass("API key valid", "test call succeeded")
                    with pytest.raises(SystemExit) as exc_info:
                        run_doctor(tmp_path)
    assert exc_info.value.code == 0


def _make_pass(name, detail):
    """Helper to create a PASS CheckResult."""
    from forge.commands.doctor import CheckResult, CheckStatus
    return CheckResult(name=name, status=CheckStatus.PASS, detail=detail)
