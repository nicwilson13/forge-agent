"""Tests for forge/tasks_view.py."""

import json
import socket
import time
import urllib.request

from forge.state import ForgeState, Phase, Task, TaskStatus, load_state, save_state
from forge.tasks_view import (
    TASKS_HTML,
    get_parked_tasks,
    resolve_task,
    trigger_checkin,
)


def _reset_dashboard():
    """Reset global dashboard state between tests."""
    from forge import dashboard
    dashboard._dashboard_state.clear()
    with dashboard._sse_lock:
        dashboard._sse_clients.clear()
    dashboard._server = None
    dashboard._project_dir = None
    dashboard._stop_event.clear()


def _find_free_port():
    """Find a free port for testing."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _make_state_with_parked(tmp_path):
    """Create a state with a PARKED task and save it."""
    task_parked = Task(
        id="t_abc", title="Implement SSO", description="Add SAML SSO",
        phase_id="p_01", status=TaskStatus.PARKED,
        park_reason="needs SAML config", notes="original notes",
    )
    task_done = Task(
        id="t_def", title="Setup DB", description="Create tables",
        phase_id="p_01", status=TaskStatus.DONE,
    )
    phase = Phase(id="p_01", title="Phase 1", description="Setup", tasks=[task_parked, task_done])
    state = ForgeState(phases=[phase])
    save_state(tmp_path, state)
    return state


# ---------------------------------------------------------------------------
# HTML content tests
# ---------------------------------------------------------------------------

def test_tasks_html_contains_required_elements():
    """TASKS_HTML has task card structure and resolve button."""
    assert "Needs Your Input" in TASKS_HTML
    assert "Mark Resolved" in TASKS_HTML
    assert "Skip" in TASKS_HTML
    assert len(TASKS_HTML) > 2000


# ---------------------------------------------------------------------------
# get_parked_tasks tests
# ---------------------------------------------------------------------------

def test_get_parked_tasks_no_state(tmp_path):
    """Returns empty list when no state.json."""
    result = get_parked_tasks(tmp_path)
    assert result == []


def test_get_parked_tasks_with_parked_task(tmp_path):
    """Returns parked tasks from state.json."""
    _make_state_with_parked(tmp_path)
    result = get_parked_tasks(tmp_path)
    assert len(result) == 1
    assert result[0]["id"] == "t_abc"
    assert result[0]["title"] == "Implement SSO"
    assert result[0]["park_reason"] == "needs SAML config"
    assert result[0]["phase_title"] == "Phase 1"
    assert result[0]["phase_num"] == 1


def test_get_parked_tasks_filters_non_parked(tmp_path):
    """Only returns PARKED status tasks."""
    _make_state_with_parked(tmp_path)
    result = get_parked_tasks(tmp_path)
    # Should only have the parked task, not the DONE task
    assert len(result) == 1
    ids = [t["id"] for t in result]
    assert "t_def" not in ids


# ---------------------------------------------------------------------------
# resolve_task tests
# ---------------------------------------------------------------------------

def test_resolve_task_sets_pending(tmp_path):
    """Resolved task status becomes PENDING."""
    _make_state_with_parked(tmp_path)
    result = resolve_task(tmp_path, "t_abc", "Here is the SAML config")
    assert result is True

    state = load_state(tmp_path)
    task = state.find_task("t_abc")
    assert task.status == TaskStatus.PENDING


def test_resolve_task_skip_sets_done(tmp_path):
    """Skipped task status becomes DONE."""
    _make_state_with_parked(tmp_path)
    result = resolve_task(tmp_path, "t_abc", "", skip=True)
    assert result is True

    state = load_state(tmp_path)
    task = state.find_task("t_abc")
    assert task.status == TaskStatus.DONE


def test_resolve_task_appends_notes(tmp_path):
    """Resolution text appended to task notes."""
    _make_state_with_parked(tmp_path)
    resolve_task(tmp_path, "t_abc", "The API key is xyz123")

    state = load_state(tmp_path)
    task = state.find_task("t_abc")
    assert "Human resolution: The API key is xyz123" in task.notes
    assert "Original notes: original notes" in task.notes


def test_resolve_task_missing_id(tmp_path):
    """Returns False for unknown task ID."""
    _make_state_with_parked(tmp_path)
    result = resolve_task(tmp_path, "nonexistent", "some text")
    assert result is False


# ---------------------------------------------------------------------------
# trigger_checkin tests
# ---------------------------------------------------------------------------

def test_trigger_checkin_never_raises(tmp_path):
    """trigger_checkin does not raise on missing forge binary."""
    # Should not raise even with bad path
    result = trigger_checkin(tmp_path)
    # Result may be True or False depending on environment,
    # but it must not raise
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# HTTP integration tests
# ---------------------------------------------------------------------------

def test_tasks_route_registered(tmp_path):
    """GET /tasks returns 200."""
    _reset_dashboard()
    from forge.dashboard import start_dashboard, stop_dashboard
    port = _find_free_port()

    thread = start_dashboard(tmp_path, port=port)
    assert thread is not None
    time.sleep(0.3)

    try:
        resp = urllib.request.urlopen(
            f"http://localhost:{port}/tasks", timeout=3
        )
        html = resp.read().decode()
        assert resp.status == 200
        assert "Needs Your Input" in html
    finally:
        stop_dashboard()
        time.sleep(0.2)


def test_tasks_data_returns_json(tmp_path):
    """GET /tasks/data returns valid JSON with tasks and count."""
    _reset_dashboard()
    from forge.dashboard import start_dashboard, stop_dashboard
    port = _find_free_port()

    thread = start_dashboard(tmp_path, port=port)
    assert thread is not None
    time.sleep(0.3)

    try:
        resp = urllib.request.urlopen(
            f"http://localhost:{port}/tasks/data", timeout=3
        )
        data = json.loads(resp.read())
        assert "tasks" in data
        assert "count" in data
        assert data["count"] == 0
        assert data["tasks"] == []
    finally:
        stop_dashboard()
        time.sleep(0.2)
