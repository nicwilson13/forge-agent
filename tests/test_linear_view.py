"""Tests for forge.linear_view."""

import json
from io import BytesIO
from unittest.mock import MagicMock, patch

from forge.linear_view import (
    LINEAR_HTML,
    get_linear_board_data,
    handle_linear_data,
)


def test_linear_html_contains_columns():
    """LINEAR_HTML has todo, in_progress, done columns."""
    assert "Todo" in LINEAR_HTML
    assert "In Progress" in LINEAR_HTML
    assert "Done" in LINEAR_HTML
    assert "/linear/data" in LINEAR_HTML
    assert "Sync with Forge Plan" in LINEAR_HTML


def test_get_linear_board_data_not_configured(tmp_path):
    """Returns configured=False when no linear.json."""
    result = get_linear_board_data(tmp_path)
    assert result["configured"] is False


def test_get_linear_board_data_groups_by_state(tmp_path, monkeypatch):
    """Issues grouped into todo/in_progress/done based on forge state."""
    from forge.linear_integration import LinearConfig

    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir(parents=True)
    (forge_dir / "linear.json").write_text(json.dumps({
        "enabled": True,
        "team_id": "TEAM-1",
        "project_id": "PROJ-1",
    }))

    mock_issues = [
        {"id": "1", "identifier": "LIN-1", "title": "Setup database schema", "priority": 1, "labels": []},
        {"id": "2", "identifier": "LIN-2", "title": "Build login form", "priority": 2, "labels": []},
    ]

    monkeypatch.setattr(
        "forge.linear_integration.get_linear_token",
        lambda: "test-token",
    )
    monkeypatch.setattr(
        "forge.linear_integration.get_open_issues",
        lambda config, token, limit=25: mock_issues,
    )

    result = get_linear_board_data(tmp_path)
    assert result["configured"] is True
    assert result["total"] == 2
    # Without matching forge state, all go to todo
    assert len(result["todo"]) == 2


def test_linear_route_registered(tmp_path):
    """GET /linear returns 200 via dashboard."""
    import tempfile
    import time
    import urllib.request
    from forge.dashboard import start_dashboard, stop_dashboard

    start_dashboard(tmp_path, port=3341)
    time.sleep(0.3)
    try:
        resp = urllib.request.urlopen("http://localhost:3341/linear", timeout=3)
        assert resp.status == 200
    finally:
        stop_dashboard()


def test_linear_data_returns_json(tmp_path):
    """handle_linear_data returns valid JSON."""
    handler = MagicMock()
    handler.wfile = BytesIO()

    handle_linear_data(handler, tmp_path)

    handler.send_response.assert_called_once_with(200)
    written = handler.wfile.getvalue()
    data = json.loads(written.decode("utf-8"))
    assert "configured" in data
