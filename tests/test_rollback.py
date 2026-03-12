"""
Tests for forge.commands.rollback module.
All git operations are mocked.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from forge.state import ForgeState, Phase, Task, TaskStatus, PhaseStatus
from forge.commands.rollback import (
    _get_rollback_points,
    _execute_rollback,
    _extract_phase_name,
    _list_rollback_points,
    run_rollback,
)
from forge import git_utils


def _make_state(num_phases=3, current_index=2):
    """Create a ForgeState with N phases, first (current_index) marked DONE."""
    phases = []
    for i in range(num_phases):
        p = Phase.new(f"Phase {i+1}: Phase {i+1} Title", f"Description {i+1}")
        if i < current_index:
            p.status = PhaseStatus.DONE
            task = Task.new(f"Task {i+1}", f"Do thing {i+1}", p.id)
            task.status = TaskStatus.DONE
            p.tasks = [task]
        phases.append(p)
    state = ForgeState(
        project_name="test",
        phases=phases,
        current_phase_index=current_index,
        initialized=True,
        tasks_completed=current_index,
    )
    return state


def test_rollback_registered_in_cli():
    """forge rollback appears in forge --help output."""
    result = subprocess.run(
        ["forge", "--help"],
        capture_output=True, text=True,
    )
    assert "rollback" in result.stdout


def test_get_rollback_points_empty_state(tmp_path):
    """Returns empty list when no phases exist."""
    state = ForgeState(project_name="test", initialized=True)
    points = _get_rollback_points(tmp_path, state)
    assert points == []


def test_get_rollback_points_with_complete_phases(tmp_path):
    """Returns correct rollback points for completed phases."""
    state = _make_state(num_phases=3, current_index=2)

    with patch.object(git_utils, "get_tag_commit", return_value="abc1234"):
        points = _get_rollback_points(tmp_path, state)

    assert len(points) == 3
    assert points[0]["available"] is True
    assert points[0]["is_complete"] is True
    assert points[1]["available"] is True
    assert points[2]["available"] is False
    assert points[2]["is_current"] is True


def test_execute_rollback_invalid_phase_too_high(tmp_path, capsys):
    """Rejects rollback to phase beyond current progress."""
    state = _make_state(num_phases=3, current_index=2)
    _execute_rollback(tmp_path, state, 99)
    captured = capsys.readouterr()
    assert "does not exist" in captured.out


def test_execute_rollback_invalid_phase_zero(tmp_path, capsys):
    """Rejects rollback to phase 0."""
    state = _make_state(num_phases=3, current_index=2)
    _execute_rollback(tmp_path, state, 0)
    captured = capsys.readouterr()
    assert "Invalid" in captured.out


def test_execute_rollback_rejects_current_phase(tmp_path, capsys):
    """Rejects rollback to the current (in-progress) phase."""
    state = _make_state(num_phases=3, current_index=2)
    _execute_rollback(tmp_path, state, 3)
    captured = capsys.readouterr()
    assert "current phase" in captured.out


def test_execute_rollback_requires_confirmation(tmp_path, monkeypatch, capsys):
    """Rollback is cancelled when confirmation text does not match."""
    (tmp_path / ".forge").mkdir()
    state = _make_state(num_phases=3, current_index=2)

    monkeypatch.setattr("builtins.input", lambda _: "wrong text")

    with patch.object(git_utils, "get_tag_commit", return_value="abc1234"):
        _execute_rollback(tmp_path, state, 1)

    captured = capsys.readouterr()
    assert "did not match" in captured.out


def test_execute_rollback_state_rewind(tmp_path, monkeypatch, capsys):
    """State is correctly rewound after rollback confirmation."""
    (tmp_path / ".forge").mkdir()
    state = _make_state(num_phases=5, current_index=4)

    phase_name = _extract_phase_name(state.phases[1].title)
    monkeypatch.setattr("builtins.input", lambda _: phase_name)

    with patch.object(git_utils, "get_tag_commit", return_value="abc1234"):
        with patch.object(git_utils, "_run", return_value=(0, "", "")):
            with patch.object(git_utils, "has_remote", return_value=False):
                _execute_rollback(tmp_path, state, 2)

    assert state.current_phase_index == 2
    assert len(state.phases) == 2
    assert state.phases[0].status == PhaseStatus.DONE
    assert state.phases[1].status == PhaseStatus.DONE


def test_state_rewind_clears_future_phases(tmp_path, monkeypatch):
    """Phases after rollback target are removed from state."""
    (tmp_path / ".forge").mkdir()
    state = _make_state(num_phases=5, current_index=4)

    phase_name = _extract_phase_name(state.phases[0].title)
    monkeypatch.setattr("builtins.input", lambda _: phase_name)

    with patch.object(git_utils, "get_tag_commit", return_value="abc1234"):
        with patch.object(git_utils, "_run", return_value=(0, "", "")):
            with patch.object(git_utils, "has_remote", return_value=False):
                _execute_rollback(tmp_path, state, 1)

    assert len(state.phases) == 1


def test_state_rewind_preserves_prior_phases(tmp_path, monkeypatch):
    """Phases before rollback target are preserved in state."""
    (tmp_path / ".forge").mkdir()
    state = _make_state(num_phases=5, current_index=4)
    original_phase_0_id = state.phases[0].id

    phase_name = _extract_phase_name(state.phases[1].title)
    monkeypatch.setattr("builtins.input", lambda _: phase_name)

    with patch.object(git_utils, "get_tag_commit", return_value="abc1234"):
        with patch.object(git_utils, "_run", return_value=(0, "", "")):
            with patch.object(git_utils, "has_remote", return_value=False):
                _execute_rollback(tmp_path, state, 2)

    assert state.phases[0].id == original_phase_0_id
    assert state.phases[0].status == PhaseStatus.DONE


def test_get_tag_commit_missing_tag(tmp_path):
    """Returns None when tag does not exist."""
    # Use a real git repo to test
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"],
                   cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=str(tmp_path), capture_output=True)
    (tmp_path / "README.md").write_text("# test")
    subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"],
                   cwd=str(tmp_path), capture_output=True)

    result = git_utils.get_tag_commit(tmp_path, "nonexistent-tag")
    assert result is None


def test_list_forge_tags_empty_repo(tmp_path):
    """Returns empty list for repo with no forge tags."""
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"],
                   cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=str(tmp_path), capture_output=True)
    (tmp_path / "README.md").write_text("# test")
    subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"],
                   cwd=str(tmp_path), capture_output=True)

    result = git_utils.list_forge_tags(tmp_path)
    assert result == []


def test_run_rollback_no_flags(tmp_path, capsys):
    """Shows usage hint when neither --list nor --to-phase given."""
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    (tmp_path / ".forge").mkdir()

    state = _make_state(num_phases=3, current_index=2)

    with patch("forge.commands.rollback.load_state", return_value=state):
        run_rollback(tmp_path, to_phase=None, list_only=False)

    captured = capsys.readouterr()
    assert "--list" in captured.out
    assert "--to-phase" in captured.out
