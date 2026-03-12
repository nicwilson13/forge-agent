"""Tests for Sentry error monitoring integration."""

import json
import urllib.request
from pathlib import Path

from forge.sentry_integration import (
    SentryConfig,
    load_sentry_config,
    save_sentry_config,
    get_sentry_token,
    _sentry_request,
    get_unresolved_issues,
    format_issue_as_fix_task,
    generate_sentry_setup_instructions,
    run_sentry_check,
)


# ---------------------------------------------------------------------------
# Config load/save
# ---------------------------------------------------------------------------

def test_load_sentry_config_missing(tmp_path):
    """Returns disabled config when file missing."""
    config = load_sentry_config(tmp_path)
    assert config.enabled is False
    assert config.org_slug == ""
    assert config.project_slug == ""


def test_load_sentry_config_valid(tmp_path):
    """Loads config correctly from valid JSON."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    data = {
        "enabled": True,
        "org_slug": "my-org",
        "project_slug": "my-project",
        "auto_configure": True,
        "create_fix_tasks": True,
        "error_threshold": 10,
    }
    (forge_dir / "sentry.json").write_text(json.dumps(data))

    config = load_sentry_config(tmp_path)
    assert config.enabled is True
    assert config.org_slug == "my-org"
    assert config.project_slug == "my-project"
    assert config.error_threshold == 10


def test_load_sentry_config_invalid_json(tmp_path):
    """Returns disabled config on invalid JSON."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    (forge_dir / "sentry.json").write_text("not json {{{")

    config = load_sentry_config(tmp_path)
    assert config.enabled is False


def test_save_load_roundtrip(tmp_path):
    """Config survives save/load cycle."""
    config = SentryConfig(
        enabled=True,
        org_slug="test-org",
        project_slug="test-proj",
        error_threshold=3,
    )
    save_sentry_config(tmp_path, config)
    loaded = load_sentry_config(tmp_path)
    assert loaded.enabled is True
    assert loaded.org_slug == "test-org"
    assert loaded.project_slug == "test-proj"
    assert loaded.error_threshold == 3


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------

def test_get_sentry_token_missing(monkeypatch, tmp_path):
    """Returns empty string when profile doesn't exist."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert get_sentry_token() == ""


# ---------------------------------------------------------------------------
# API request
# ---------------------------------------------------------------------------

def test_sentry_request_returns_none_on_error(monkeypatch):
    """Returns None on network error."""
    def mock_urlopen(*args, **kwargs):
        raise ConnectionError("no network")
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    result = _sentry_request("/test/endpoint", "fake-token")
    assert result is None


# ---------------------------------------------------------------------------
# Unresolved issues
# ---------------------------------------------------------------------------

def test_get_unresolved_issues_disabled():
    """Returns empty list when integration disabled."""
    config = SentryConfig(enabled=False)
    result = get_unresolved_issues(config, "tok")
    assert result == []


def test_get_unresolved_issues_filters_by_threshold(monkeypatch):
    """Issues below error_threshold excluded."""
    config = SentryConfig(
        enabled=True,
        org_slug="org",
        project_slug="proj",
        error_threshold=5,
    )

    mock_response = [
        {"id": "1", "title": "Error A", "culprit": "file.ts", "count": "10",
         "userCount": 3, "permalink": "https://sentry.io/1"},
        {"id": "2", "title": "Error B", "culprit": "other.ts", "count": "2",
         "userCount": 1, "permalink": "https://sentry.io/2"},
        {"id": "3", "title": "Error C", "culprit": "third.ts", "count": "7",
         "userCount": 5, "permalink": "https://sentry.io/3"},
    ]

    monkeypatch.setattr(
        "forge.sentry_integration._sentry_request",
        lambda endpoint, token: mock_response,
    )

    result = get_unresolved_issues(config, "tok")
    assert len(result) == 2
    assert result[0]["title"] == "Error A"
    assert result[1]["title"] == "Error C"


# ---------------------------------------------------------------------------
# Fix task formatting
# ---------------------------------------------------------------------------

def test_format_issue_as_fix_task_basic():
    """Returns (title, description) tuple with correct format."""
    issue = {
        "id": "abc123",
        "title": "TypeError: Cannot read properties of undefined (reading email)",
        "culprit": "src/app/api/auth/route.ts in POST",
        "count": "14",
        "userCount": 3,
        "permalink": "https://sentry.io/issues/abc123/",
    }
    title, desc = format_issue_as_fix_task(issue, [])

    assert title.startswith("Fix:")
    assert "sentry.io" in desc
    assert "14" in desc
    assert "auth/route.ts" in desc


def test_format_issue_as_fix_task_title_truncated():
    """Long issue titles truncated to 60 chars in task title."""
    issue = {
        "id": "xyz",
        "title": "A" * 100,
        "culprit": "",
        "count": "5",
        "userCount": 1,
        "permalink": "",
    }
    title, _ = format_issue_as_fix_task(issue, [])
    # "Fix: " (5) + 60 + "..." (3) = 68 max
    assert len(title) <= 70


# ---------------------------------------------------------------------------
# Setup instructions
# ---------------------------------------------------------------------------

def test_generate_sentry_setup_instructions_contains_package():
    """Instructions mention @sentry/nextjs."""
    config = SentryConfig(org_slug="myorg", project_slug="myproj")
    instructions = generate_sentry_setup_instructions(config)
    assert "@sentry/nextjs" in instructions
    assert "myorg" in instructions
    assert "myproj" in instructions


# ---------------------------------------------------------------------------
# run_sentry_check
# ---------------------------------------------------------------------------

def test_run_sentry_check_disabled(tmp_path, monkeypatch):
    """Returns empty list when not configured."""
    result = run_sentry_check(tmp_path)
    assert result == []


def test_run_sentry_check_never_raises(monkeypatch, tmp_path):
    """Never raises even on unexpected errors."""
    # Write a config that's enabled
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    data = {"enabled": True, "org_slug": "org", "project_slug": "proj"}
    (forge_dir / "sentry.json").write_text(json.dumps(data))

    # Mock token to return a value
    monkeypatch.setattr(
        "forge.sentry_integration.get_sentry_token",
        lambda: "fake-token",
    )

    # Mock API to raise
    def mock_request(*args, **kwargs):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(
        "forge.sentry_integration._sentry_request",
        mock_request,
    )

    result = run_sentry_check(tmp_path)
    assert result == []
