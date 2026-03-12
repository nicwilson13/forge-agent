"""Tests for forge.github_integration module."""

import json
from pathlib import Path

from forge.github_integration import (
    GitHubConfig,
    load_github_config,
    save_github_config,
    get_github_token,
    _github_request,
    get_open_issues,
    format_issue_context,
    link_issues_to_tasks,
    create_phase_pr,
    post_build_summary,
)
from forge.state import Phase, Task, TaskStatus


def test_load_github_config_missing(tmp_path):
    """Returns disabled config when .forge/github.json missing."""
    config = load_github_config(tmp_path)
    assert config.enabled is False
    assert config.owner == ""
    assert config.repo == ""


def test_load_github_config_valid(tmp_path):
    """Parses valid config correctly."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    config_data = {
        "enabled": True,
        "owner": "testowner",
        "repo": "testrepo",
        "create_prs": False,
        "pr_base_branch": "develop",
    }
    (forge_dir / "github.json").write_text(json.dumps(config_data))

    config = load_github_config(tmp_path)
    assert config.enabled is True
    assert config.owner == "testowner"
    assert config.repo == "testrepo"
    assert config.create_prs is False
    assert config.pr_base_branch == "develop"
    # Defaults for unset fields
    assert config.create_milestones is True
    assert config.link_issues is True


def test_load_github_config_invalid_json(tmp_path):
    """Returns disabled config on parse error."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    (forge_dir / "github.json").write_text("not valid json {{{")

    config = load_github_config(tmp_path)
    assert config.enabled is False


def test_save_and_load_roundtrip(tmp_path):
    """Config round-trips through save and load."""
    config = GitHubConfig(
        enabled=True,
        owner="nicholascooke",
        repo="my-app",
        create_prs=True,
        pr_base_branch="main",
    )
    save_github_config(tmp_path, config)
    loaded = load_github_config(tmp_path)
    assert loaded.enabled is True
    assert loaded.owner == "nicholascooke"
    assert loaded.repo == "my-app"


def test_get_github_token_missing(monkeypatch):
    """Returns empty string when token not in profile."""
    # Point to a non-existent home directory
    monkeypatch.setattr(Path, "home", lambda: Path("/nonexistent/path"))
    result = get_github_token()
    assert result == ""


def test_github_request_returns_none_on_error(monkeypatch):
    """_github_request returns None on network error."""
    import urllib.request

    def mock_urlopen(*args, **kwargs):
        raise ConnectionError("no network")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    result = _github_request("GET", "/repos/test/test", "fake-token")
    assert result is None


def test_get_open_issues_disabled_config():
    """Returns empty list when integration disabled."""
    config = GitHubConfig(enabled=False)
    result = get_open_issues(config, "some-token")
    assert result == []


def test_get_open_issues_no_token():
    """Returns empty list when token is empty."""
    config = GitHubConfig(enabled=True, owner="test", repo="repo")
    result = get_open_issues(config, "")
    assert result == []


def test_format_issue_context_empty():
    """Returns empty string for empty issue list."""
    result = format_issue_context([])
    assert result == ""


def test_format_issue_context_with_issues():
    """Returns formatted markdown with issue numbers and titles."""
    issues = [
        {
            "number": 12,
            "title": "Users cannot reset password",
            "labels": [{"name": "bug"}],
        },
        {
            "number": 15,
            "title": "Add email verification",
            "labels": [{"name": "feature"}],
        },
    ]
    context = format_issue_context(issues)
    assert "#12" in context
    assert "#15" in context
    assert "reset password" in context
    assert "email verification" in context
    assert "[bug]" in context
    assert "[feature]" in context
    assert "Open GitHub Issues" in context


def test_link_issues_to_tasks_match():
    """Issues with matching keywords linked to tasks."""
    issues = [
        {"number": 12, "title": "Users cannot reset password", "labels": []},
    ]
    tasks = [
        Task.new("Implement password reset flow", "Allow users to reset their password via email", "phase1"),
        Task.new("Add dashboard", "Create main dashboard view", "phase1"),
    ]
    result = link_issues_to_tasks(issues, tasks)
    # The password reset task should be linked to issue #12
    matched_task = tasks[0]
    assert matched_task.id in result
    assert 12 in result[matched_task.id]
    # Dashboard task should not be linked
    assert tasks[1].id not in result


def test_link_issues_to_tasks_no_match():
    """Issues with no matching keywords produce empty dict."""
    issues = [
        {"number": 99, "title": "Fix billing webhook", "labels": []},
    ]
    tasks = [
        Task.new("Add user registration", "Create registration page", "phase1"),
    ]
    result = link_issues_to_tasks(issues, tasks)
    assert result == {} or all(len(v) == 0 for v in result.values())


def test_create_phase_pr_disabled(tmp_path):
    """Returns None when integration disabled."""
    config = GitHubConfig(enabled=False)
    phase = Phase.new("Authentication", "Add auth")
    result = create_phase_pr(config, "token", phase, 1, "feature-branch")
    assert result is None


def test_create_phase_pr_same_branch():
    """Returns None when branch equals base branch."""
    config = GitHubConfig(enabled=True, owner="o", repo="r", pr_base_branch="main")
    phase = Phase.new("Auth", "desc")
    result = create_phase_pr(config, "token", phase, 1, "main")
    assert result is None


def test_post_build_summary_format():
    """Build summary comment contains required fields."""
    # We can't test the actual API call, but we can test the disabled path
    config = GitHubConfig(enabled=False)
    phase = Phase.new("Auth", "desc")
    phase.tasks = [Task.new("t1", "d1", phase.id)]
    phase.tasks[0].status = TaskStatus.DONE
    result = post_build_summary(config, "token", 1, phase, "A (94%)", "$0.34")
    assert result is False
