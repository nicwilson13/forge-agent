"""Tests for forge.health module."""

import json
from pathlib import Path

import pytest

from forge.health import (
    SessionMetrics,
    ProjectMetrics,
    HealthReport,
    grade_health,
    compute_session_metrics,
    compute_project_metrics,
    compute_health_report,
    format_health_report,
    format_health_summary_line,
)


def _make_session(success_rate=0.90, retry_rate=0.10, avg_cost=0.04,
                  tasks=10, session_id="test1234"):
    """Helper to build a SessionMetrics."""
    first_pass = int(success_rate * tasks)
    retried = int(retry_rate * tasks)
    return SessionMetrics(
        tasks_attempted=tasks,
        tasks_first_pass=first_pass,
        tasks_retried=retried,
        tasks_failed=tasks - first_pass - retried,
        success_rate=success_rate,
        retry_rate=retry_rate,
        avg_task_duration=45.0,
        avg_task_cost=avg_cost,
        total_cost=avg_cost * tasks,
        total_tokens_in=50000,
        total_tokens_out=10000,
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# grade_health
# ---------------------------------------------------------------------------

def test_grade_health_a():
    """High success rate, low retry rate grades as A."""
    s = _make_session(success_rate=0.97, retry_rate=0.03, avg_cost=0.04)
    assert grade_health(s) == "A"


def test_grade_health_b():
    """Good but not perfect success rate grades as B."""
    s = _make_session(success_rate=0.88, retry_rate=0.12, avg_cost=0.08)
    assert grade_health(s) == "B"


def test_grade_health_c():
    """Moderate success rate grades as C."""
    s = _make_session(success_rate=0.72, retry_rate=0.28, avg_cost=0.15)
    assert grade_health(s) == "C"


def test_grade_health_f():
    """Below 50% success rate grades as F."""
    s = _make_session(success_rate=0.40, retry_rate=0.60, avg_cost=0.50)
    assert grade_health(s) == "F"


# ---------------------------------------------------------------------------
# compute_session_metrics
# ---------------------------------------------------------------------------

def test_compute_session_metrics_empty(tmp_path):
    """Returns None when no data for session_id."""
    result = compute_session_metrics(tmp_path, "nonexistent")
    assert result is None


def test_compute_session_metrics_basic(tmp_path):
    """Computes correct success rate from build log events."""
    _write_build_events(tmp_path, "sess01", [
        {"event": "task_started", "task": "t1"},
        {"event": "task_completed", "task": "t1", "duration_secs": 30, "cost": 0.02, "tokens_in": 5000, "tokens_out": 1000},
        {"event": "task_started", "task": "t2"},
        {"event": "task_completed", "task": "t2", "duration_secs": 40, "cost": 0.03, "tokens_in": 6000, "tokens_out": 1200},
    ])

    result = compute_session_metrics(tmp_path, "sess01")
    assert result is not None
    assert result.tasks_attempted == 2
    assert result.tasks_first_pass == 2
    assert result.success_rate == 1.0
    assert result.total_cost == pytest.approx(0.05)


def test_compute_session_metrics_retry_rate(tmp_path):
    """Retry rate computed correctly from failed+completed events."""
    _write_build_events(tmp_path, "sess02", [
        {"event": "task_started", "task": "t1"},
        {"event": "task_failed", "task": "t1", "task_title": "Task 1"},
        {"event": "task_started", "task": "t1"},
        {"event": "task_completed", "task": "t1", "duration_secs": 30, "cost": 0.02, "tokens_in": 5000, "tokens_out": 1000},
        {"event": "task_started", "task": "t2"},
        {"event": "task_completed", "task": "t2", "duration_secs": 40, "cost": 0.03, "tokens_in": 6000, "tokens_out": 1200},
    ])

    result = compute_session_metrics(tmp_path, "sess02")
    assert result is not None
    assert result.tasks_attempted == 2
    assert result.tasks_retried == 1
    assert result.retry_rate == 0.5


# ---------------------------------------------------------------------------
# compute_project_metrics
# ---------------------------------------------------------------------------

def test_compute_project_metrics_empty(tmp_path):
    """Returns default ProjectMetrics when no cost log."""
    result = compute_project_metrics(tmp_path)
    assert result.total_cost == 0.0
    assert result.session_count == 0
    assert result.cost_trend == "not enough data"


def test_compute_project_metrics_aggregates_sessions(tmp_path):
    """Aggregates cost and task counts across multiple sessions."""
    _write_cost_records(tmp_path, [
        {"total_cost": 0.10, "duration_secs": 30, "phase_title": "Phase 1"},
        {"total_cost": 0.20, "duration_secs": 45, "phase_title": "Phase 1"},
        {"total_cost": 0.30, "duration_secs": 60, "phase_title": "Phase 2"},
    ])
    _write_build_events(tmp_path, "sess01", [
        {"event": "session_started"},
        {"event": "task_completed", "task": "t1", "cost": 0.10},
    ])

    result = compute_project_metrics(tmp_path)
    assert result.total_cost == pytest.approx(0.60)
    assert result.total_tasks == 3


# ---------------------------------------------------------------------------
# cost trend
# ---------------------------------------------------------------------------

def test_cost_trend_increasing(tmp_path):
    """Reports 'increasing' when later sessions cost more."""
    _write_build_events_multi(tmp_path, [
        ("sess01", [
            {"event": "session_started"},
            {"event": "task_completed", "task": "t1", "cost": 0.01},
        ]),
        ("sess02", [
            {"event": "session_started"},
            {"event": "task_completed", "task": "t2", "cost": 0.05},
        ]),
    ])

    result = compute_project_metrics(tmp_path)
    assert result.cost_trend == "increasing"


def test_cost_trend_stable(tmp_path):
    """Reports 'stable' when costs are consistent."""
    _write_build_events_multi(tmp_path, [
        ("sess01", [
            {"event": "session_started"},
            {"event": "task_completed", "task": "t1", "cost": 0.03},
        ]),
        ("sess02", [
            {"event": "session_started"},
            {"event": "task_completed", "task": "t2", "cost": 0.03},
        ]),
    ])

    result = compute_project_metrics(tmp_path)
    assert result.cost_trend == "stable"


# ---------------------------------------------------------------------------
# retry hotspots
# ---------------------------------------------------------------------------

def test_retry_hotspots_threshold(tmp_path):
    """Only tasks with 3+ failures appear in hotspots."""
    events = [{"event": "session_started"}]
    # Task A fails 3 times -> hotspot
    for _ in range(3):
        events.append({"event": "task_failed", "task": "tA", "task_title": "Task A"})
    # Task B fails 2 times -> NOT a hotspot
    for _ in range(2):
        events.append({"event": "task_failed", "task": "tB", "task_title": "Task B"})

    _write_build_events(tmp_path, "sess01", events)

    result = compute_project_metrics(tmp_path)
    titles = [t for t, _ in result.retry_hotspots]
    assert "Task A" in titles
    assert "Task B" not in titles


# ---------------------------------------------------------------------------
# format functions
# ---------------------------------------------------------------------------

def test_format_health_summary_line():
    """Summary line contains grade, success rate, cost, duration."""
    s = _make_session(success_rate=0.91, retry_rate=0.09, avg_cost=0.034, tasks=11)
    report = HealthReport(grade="B", session=s, project=ProjectMetrics())
    line = format_health_summary_line(report)
    assert "Build Health: B" in line
    assert "91% success" in line
    assert "$" in line


def test_format_health_report_contains_grade():
    """Full report string contains the letter grade."""
    s = _make_session()
    report = HealthReport(grade="A", session=s, project=ProjectMetrics())
    output = format_health_report(report)
    assert "Build Health: A" in output
    assert "Session (current)" in output


# ---------------------------------------------------------------------------
# compute_health_report
# ---------------------------------------------------------------------------

def test_health_report_no_crash_on_empty_project(tmp_path):
    """compute_health_report returns valid result for fresh project."""
    report = compute_health_report(tmp_path, "nonexistent")
    assert report.grade == "?"
    assert isinstance(report.session, SessionMetrics)
    assert isinstance(report.project, ProjectMetrics)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_build_events(tmp_path, session_id, events):
    """Write build log events for a single session."""
    log_dir = tmp_path / ".forge"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / "build.log"

    existing = ""
    if log_path.exists():
        existing = log_path.read_text(encoding="utf-8")

    lines = []
    for e in events:
        record = {
            "ts": "2026-01-01T00:00:00+00:00",
            "event": e.get("event", "unknown"),
            "session": session_id,
            "phase": e.get("phase"),
            "task": e.get("task"),
            **{k: v for k, v in e.items() if k not in ("event", "phase", "task")},
        }
        lines.append(json.dumps(record))

    log_path.write_text(existing + "\n".join(lines) + "\n", encoding="utf-8")


def _write_build_events_multi(tmp_path, session_events_list):
    """Write build log events for multiple sessions."""
    for session_id, events in session_events_list:
        _write_build_events(tmp_path, session_id, events)


def _write_cost_records(tmp_path, records):
    """Write cost_log.jsonl records."""
    log_dir = tmp_path / ".forge"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / "cost_log.jsonl"
    lines = [json.dumps(r) for r in records]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
