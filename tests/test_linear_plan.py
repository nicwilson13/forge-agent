"""Tests for forge linear-plan command and planning functions."""

import json
from pathlib import Path

from forge.linear_integration import (
    LinearConfig,
    infer_task_priority,
    create_issue_for_task,
    bulk_create_phase_issues,
    sync_plan_to_linear,
    create_milestone_for_phase,
)
from forge.state import Phase, Task, TaskStatus


# ---------------------------------------------------------------------------
# infer_task_priority
# ---------------------------------------------------------------------------

def test_infer_task_priority_urgent():
    """Task with 'critical' signal returns priority 1."""
    assert infer_task_priority("CRITICAL: fix production outage", "") == 1
    assert infer_task_priority("Hotfix for login", "") == 1
    assert infer_task_priority("Fix breaking change", "production issue") == 1


def test_infer_task_priority_high():
    """Task with 'auth' signal returns priority 2."""
    assert infer_task_priority("Set up auth with Supabase", "") == 2
    assert infer_task_priority("Database schema design", "") == 2
    assert infer_task_priority("Stripe payment integration", "") == 2
    assert infer_task_priority("Core architecture setup", "") == 2


def test_infer_task_priority_default():
    """Task with no signals returns priority 3."""
    assert infer_task_priority("Add tooltip to help icon", "") == 3
    assert infer_task_priority("Update README", "") == 3
    assert infer_task_priority("Style the footer component", "") == 3


# ---------------------------------------------------------------------------
# create_issue_for_task
# ---------------------------------------------------------------------------

def test_create_issue_for_task_disabled():
    """Returns None when config disabled."""
    config = LinearConfig(enabled=False, team_id="TEAM")
    result = create_issue_for_task(config, "tok", "Title", "Desc")
    assert result is None


def test_create_issue_for_task_no_token():
    """Returns None when token empty."""
    config = LinearConfig(enabled=True, team_id="TEAM")
    result = create_issue_for_task(config, "", "Title", "Desc")
    assert result is None


# ---------------------------------------------------------------------------
# bulk_create_phase_issues
# ---------------------------------------------------------------------------

def test_bulk_create_phase_issues_never_raises(monkeypatch):
    """Continues when individual creates fail."""
    config = LinearConfig(enabled=True, team_id="TEAM")

    call_count = {"n": 0}

    def mock_query(query, variables, token):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise ConnectionError("network fail")
        return {
            "issueCreate": {
                "success": True,
                "issue": {
                    "id": f"id-{call_count['n']}",
                    "identifier": f"LIN-{call_count['n']}",
                    "url": f"https://linear.app/issue/LIN-{call_count['n']}",
                },
            }
        }

    monkeypatch.setattr("forge.linear_integration._linear_query", mock_query)

    tasks = [
        Task.new("Task 1", "First task", "phase_1"),
        Task.new("Task 2", "Second task", "phase_1"),
        Task.new("Task 3", "Third task", "phase_1"),
    ]

    result = bulk_create_phase_issues(config, "tok", "Phase 1", 1, tasks)
    # Task 2 fails but function continues — should get 2 created
    assert len(result) == 2
    assert result[0]["identifier"] == "LIN-1"


# ---------------------------------------------------------------------------
# sync_plan_to_linear
# ---------------------------------------------------------------------------

def test_sync_plan_to_linear_returns_summary(monkeypatch):
    """Returns dict with milestones_created, issues_created, errors."""
    config = LinearConfig(enabled=True, team_id="TEAM", project_id="PROJ")

    def mock_query(query, variables, token):
        if "projectMilestoneCreate" in query:
            return {
                "projectMilestoneCreate": {
                    "success": True,
                    "projectMilestone": {"id": "ms-1", "name": variables["name"]},
                }
            }
        if "issueCreate" in query or "IssueCreate" in query:
            return {
                "issueCreate": {
                    "success": True,
                    "issue": {
                        "id": "iss-1",
                        "identifier": "LIN-1",
                        "url": "https://linear.app/issue/LIN-1",
                    },
                }
            }
        return None

    monkeypatch.setattr("forge.linear_integration._linear_query", mock_query)

    phases = [
        Phase(
            id="p1", title="Scaffolding", description="Setup",
            tasks=[
                Task.new("Init project", "npm init", "p1"),
                Task.new("Add linter", "eslint setup", "p1"),
            ],
        ),
        Phase(
            id="p2", title="Auth", description="Authentication",
            tasks=[
                Task.new("Login page", "Build login", "p2"),
            ],
        ),
    ]

    summary = sync_plan_to_linear(config, "tok", phases)
    assert summary["milestones_created"] == 2
    assert summary["issues_created"] == 3
    assert isinstance(summary["errors"], list)


def test_sync_plan_empty_phases(monkeypatch):
    """Handles empty phase list gracefully."""
    config = LinearConfig(enabled=True, team_id="TEAM")

    summary = sync_plan_to_linear(config, "tok", [])
    assert summary["milestones_created"] == 0
    assert summary["issues_created"] == 0
    assert summary["errors"] == []


# ---------------------------------------------------------------------------
# run_linear_plan command
# ---------------------------------------------------------------------------

def test_run_linear_plan_no_config(tmp_path, monkeypatch, capsys):
    """Exits with message when Linear not configured."""
    # No .forge/linear.json → disabled config
    monkeypatch.setattr(
        "forge.commands.linear_plan.get_linear_token", lambda: "tok"
    )

    from forge.commands.linear_plan import run_linear_plan
    import pytest

    with pytest.raises(SystemExit) as exc_info:
        run_linear_plan(tmp_path)
    assert exc_info.value.code == 1

    captured = capsys.readouterr()
    assert "not configured" in captured.out


def test_run_linear_plan_no_token(tmp_path, monkeypatch, capsys):
    """Exits with message when token missing."""
    # Create a valid config
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    config_data = {
        "enabled": True,
        "team_id": "TEAM_ABC",
    }
    (forge_dir / "linear.json").write_text(json.dumps(config_data))

    monkeypatch.setattr(
        "forge.commands.linear_plan.get_linear_token", lambda: ""
    )

    from forge.commands.linear_plan import run_linear_plan
    import pytest

    with pytest.raises(SystemExit) as exc_info:
        run_linear_plan(tmp_path)
    assert exc_info.value.code == 1

    captured = capsys.readouterr()
    assert "token not found" in captured.out
