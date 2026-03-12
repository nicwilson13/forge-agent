"""Tests for forge.linear_integration module."""

import json
from pathlib import Path

from forge.linear_integration import (
    LinearConfig,
    load_linear_config,
    save_linear_config,
    get_linear_token,
    _linear_query,
    get_open_issues,
    get_issue_states,
    format_issues_context,
    match_issue_to_task,
    create_issue,
    run_linear_integration,
)


def test_load_linear_config_missing(tmp_path):
    """Returns disabled config when .forge/linear.json missing."""
    config = load_linear_config(tmp_path)
    assert config.enabled is False
    assert config.team_id == ""


def test_load_linear_config_valid(tmp_path):
    """Parses valid config correctly."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    config_data = {
        "enabled": True,
        "team_id": "TEAM_ABC",
        "project_id": "PROJ_123",
        "sync_issues": True,
        "create_issues_for_parked": True,
        "update_issue_status": True,
    }
    (forge_dir / "linear.json").write_text(json.dumps(config_data))

    config = load_linear_config(tmp_path)
    assert config.enabled is True
    assert config.team_id == "TEAM_ABC"
    assert config.project_id == "PROJ_123"
    assert config.sync_issues is True
    assert config.create_issues_for_parked is True
    assert config.update_issue_status is True


def test_load_linear_config_invalid_json(tmp_path):
    """Returns disabled config on parse error."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    (forge_dir / "linear.json").write_text("not valid json {{{")

    config = load_linear_config(tmp_path)
    assert config.enabled is False


def test_get_linear_token_missing(monkeypatch):
    """Returns empty string when token not in profile."""
    monkeypatch.setattr(Path, "home", lambda: Path("/nonexistent/path"))
    result = get_linear_token()
    assert result == ""


def test_linear_query_returns_none_on_error(monkeypatch):
    """_linear_query returns None on network error."""
    import urllib.request

    def mock_urlopen(*args, **kwargs):
        raise ConnectionError("no network")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    result = _linear_query("query { viewer { id } }", {}, "fake-token")
    assert result is None


def test_get_open_issues_disabled():
    """Returns empty list when integration disabled."""
    config = LinearConfig(enabled=False)
    result = get_open_issues(config, "some-token")
    assert result == []


def test_format_issues_context_empty():
    """Returns empty string for empty issues list."""
    result = format_issues_context([])
    assert result == ""


def test_format_issues_context_with_issues():
    """Contains issue identifiers and titles."""
    issues = [
        {
            "id": "id1", "identifier": "LIN-12",
            "title": "Users cannot reset password",
            "description": "", "priority": 2,
            "labels": ["bug"],
        },
        {
            "id": "id2", "identifier": "LIN-15",
            "title": "Add email verification flow",
            "description": "", "priority": 4,
            "labels": ["feature"],
        },
    ]
    result = format_issues_context(issues)
    assert "Linear Issues" in result
    assert "LIN-12" in result
    assert "Users cannot reset password" in result
    assert "LIN-15" in result
    assert "bug" in result
    assert "high" in result


def test_match_issue_to_task_match():
    """Returns issue when enough keywords overlap."""
    issues = [
        {
            "id": "id1", "identifier": "LIN-12",
            "title": "Users cannot reset password",
            "description": "", "priority": 2, "labels": [],
        },
        {
            "id": "id2", "identifier": "LIN-15",
            "title": "Add email verification flow",
            "description": "", "priority": 4, "labels": [],
        },
    ]
    result = match_issue_to_task(issues, "Fix password reset for users", "")
    assert result is not None
    assert result["identifier"] == "LIN-12"


def test_match_issue_to_task_no_match():
    """Returns None when no meaningful overlap."""
    issues = [
        {
            "id": "id1", "identifier": "LIN-12",
            "title": "Users cannot reset password",
            "description": "", "priority": 2, "labels": [],
        },
    ]
    result = match_issue_to_task(issues, "Build dashboard charts", "")
    assert result is None


def test_create_issue_disabled():
    """Returns None when integration disabled."""
    config = LinearConfig(enabled=False)
    result = create_issue(config, "some-token", "Test issue", "Description")
    assert result is None


def test_run_linear_integration_disabled(tmp_path):
    """Returns ('', []) when not configured."""
    ctx, issues = run_linear_integration(tmp_path)
    assert ctx == ""
    assert issues == []


def test_run_linear_integration_never_raises(monkeypatch, tmp_path):
    """run_linear_integration never raises even with broken state."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    config_data = {
        "enabled": True,
        "team_id": "TEAM_ABC",
    }
    (forge_dir / "linear.json").write_text(json.dumps(config_data))

    # Mock token to return a value
    monkeypatch.setattr(
        "forge.linear_integration.get_linear_token", lambda: "fake-token"
    )

    # Mock get_open_issues to raise
    def mock_issues(*args, **kwargs):
        raise RuntimeError("unexpected error")

    monkeypatch.setattr(
        "forge.linear_integration.get_open_issues", mock_issues
    )

    # Should not raise
    ctx, issues = run_linear_integration(tmp_path)
    assert ctx == ""
    assert issues == []


def test_save_load_roundtrip(tmp_path):
    """Config round-trips through save and load."""
    config = LinearConfig(
        enabled=True,
        team_id="TEAM_ABC",
        project_id="PROJ_123",
        sync_issues=True,
        create_issues_for_parked=False,
        update_issue_status=True,
    )
    save_linear_config(tmp_path, config)
    loaded = load_linear_config(tmp_path)
    assert loaded.enabled is True
    assert loaded.team_id == "TEAM_ABC"
    assert loaded.project_id == "PROJ_123"
    assert loaded.sync_issues is True
    assert loaded.create_issues_for_parked is False
    assert loaded.update_issue_status is True
