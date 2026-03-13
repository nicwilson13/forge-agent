"""
Tests for state backup, recovery, and re-scaffolding guard.
All file operations use tmp_path. No real API or subprocess calls.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from forge.state import ForgeState, Phase, Task, TaskStatus, load_state, save_state
from forge.checkpoint import atomic_save


def _make_state() -> ForgeState:
    """Helper to create a ForgeState with one phase and one task."""
    task = Task.new("Test task", "Do the thing", "phase-1")
    task.status = TaskStatus.DONE
    phase = Phase.new("Phase 1", "First phase")
    phase.tasks = [task]
    return ForgeState(project_name="test-project", phases=[phase], initialized=True)


def _write_state_json(forge_dir: Path, state: ForgeState):
    """Write state dict directly to state.json."""
    from dataclasses import asdict
    with open(forge_dir / "state.json", "w", encoding="utf-8") as f:
        json.dump(asdict(state), f, indent=2, default=str)


# ---- Backup on save ----

def test_atomic_save_creates_backup(tmp_path):
    """atomic_save creates state.json.bak when state.json already exists."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()

    # Write initial state
    state1 = ForgeState(project_name="original")
    atomic_save(tmp_path, state1)
    assert (forge_dir / "state.json").exists()
    assert not (forge_dir / "state.json.bak").exists()

    # Second save should create backup
    state2 = ForgeState(project_name="updated")
    atomic_save(tmp_path, state2)
    assert (forge_dir / "state.json.bak").exists()

    # Backup should contain the previous state
    bak = json.loads((forge_dir / "state.json.bak").read_text(encoding="utf-8"))
    assert bak["project_name"] == "original"

    # Primary should contain the new state
    primary = json.loads((forge_dir / "state.json").read_text(encoding="utf-8"))
    assert primary["project_name"] == "updated"


def test_save_state_creates_backup(tmp_path):
    """save_state also creates state.json.bak when overwriting."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()

    state1 = ForgeState(project_name="first")
    save_state(tmp_path, state1)

    state2 = ForgeState(project_name="second")
    save_state(tmp_path, state2)

    assert (forge_dir / "state.json.bak").exists()
    bak = json.loads((forge_dir / "state.json.bak").read_text(encoding="utf-8"))
    assert bak["project_name"] == "first"


# ---- Recovery from backup ----

def test_load_state_falls_back_to_backup(tmp_path):
    """When state.json is missing but .bak exists, recover from backup."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()

    state = _make_state()
    _write_state_json(forge_dir, state)
    # Move to backup, delete primary
    (forge_dir / "state.json").rename(forge_dir / "state.json.bak")
    assert not (forge_dir / "state.json").exists()

    recovered = load_state(tmp_path)
    assert recovered.project_name == "test-project"
    assert recovered.initialized is True
    assert len(recovered.phases) == 1
    # Should also restore as primary
    assert (forge_dir / "state.json").exists()


def test_load_state_corrupted_falls_back(tmp_path):
    """When state.json is corrupted but .bak is valid, recover from backup."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()

    # Write valid backup
    state = _make_state()
    _write_state_json(forge_dir, state)
    (forge_dir / "state.json").rename(forge_dir / "state.json.bak")

    # Write corrupted primary
    (forge_dir / "state.json").write_text("{invalid json!!!", encoding="utf-8")

    recovered = load_state(tmp_path)
    assert recovered.project_name == "test-project"
    assert recovered.initialized is True


def test_load_state_both_missing_returns_fresh(tmp_path):
    """When neither state.json nor .bak exist, return fresh ForgeState."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()

    state = load_state(tmp_path)
    assert state.project_name == ""
    assert state.initialized is False
    assert len(state.phases) == 0


def test_load_state_both_corrupted_returns_fresh(tmp_path):
    """When both state.json and .bak are corrupted, return fresh ForgeState."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()

    (forge_dir / "state.json").write_text("not json", encoding="utf-8")
    (forge_dir / "state.json.bak").write_text("also not json", encoding="utf-8")

    state = load_state(tmp_path)
    assert state.project_name == ""
    assert state.initialized is False


# ---- Re-scaffolding guard ----

def test_detect_existing_forge_project_with_commits():
    """Detects existing forge work from [forge] commits."""
    from forge.commands.run import _detect_existing_forge_project

    with patch("forge.git_utils.recent_commits", return_value=[
        "abc1234 [forge] Initial commit",
        "def5678 [forge] Phase 1 task",
    ]):
        assert _detect_existing_forge_project(Path("/fake")) is True


def test_detect_existing_forge_project_with_tags():
    """Detects existing forge work from phase tags."""
    from forge.commands.run import _detect_existing_forge_project

    with patch("forge.git_utils.recent_commits", return_value=["abc no forge here"]):
        with patch("forge.git_utils.list_forge_tags", return_value=[
            {"tag": "phase-1", "hash": "abc", "message": "Phase complete"},
        ]):
            assert _detect_existing_forge_project(Path("/fake")) is True


def test_detect_existing_forge_project_empty_repo():
    """Returns False for repos with no forge history."""
    from forge.commands.run import _detect_existing_forge_project

    with patch("forge.git_utils.recent_commits", return_value=[]):
        with patch("forge.git_utils.list_forge_tags", return_value=[]):
            assert _detect_existing_forge_project(Path("/fake")) is False


def test_detect_existing_forge_project_handles_errors():
    """Returns False when git operations fail."""
    from forge.commands.run import _detect_existing_forge_project

    with patch("forge.git_utils.recent_commits", side_effect=Exception("git broken")):
        assert _detect_existing_forge_project(Path("/fake")) is False
