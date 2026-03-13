"""Tests for forge.history_view."""

import json
import time
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

from forge.history_view import (
    HISTORY_HTML,
    save_build_record,
    load_build_history,
    load_build_log,
    handle_history_data,
)


def test_history_html_has_table_structure():
    assert "Build History" in HISTORY_HTML
    assert "loadHistory" in HISTORY_HTML
    assert "formatDuration" in HISTORY_HTML
    assert "formatDate" in HISTORY_HTML
    assert "/history/data" in HISTORY_HTML


def test_save_build_record_creates_file(tmp_path):
    # Minimal mock state
    state = MagicMock()
    state.project_name = "test-project"
    state.tasks_completed = 5
    phase = MagicMock()
    phase.title = "Phase 1"
    phase.status = "DONE"
    phase.tasks = [MagicMock()] * 3
    state.phases = [phase]

    save_build_record(tmp_path, state, "A", 1.24, 3420)

    builds_dir = tmp_path / ".forge" / "builds"
    files = list(builds_dir.glob("*.json"))
    assert len(files) == 1

    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["health_grade"] == "A"
    assert data["total_cost"] == 1.24
    assert data["duration_seconds"] == 3420
    assert data["project"] == "test-project"
    assert data["tasks_completed"] == 5
    assert len(data["phase_summaries"]) == 1


def test_save_build_record_none_state(tmp_path):
    """save_build_record works with state=None."""
    save_build_record(tmp_path, None, "B", 0.5, 100)
    builds_dir = tmp_path / ".forge" / "builds"
    files = list(builds_dir.glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["health_grade"] == "B"
    assert data["phases_completed"] == 0


def test_load_build_history_empty(tmp_path):
    result = load_build_history(tmp_path)
    assert result == []


def test_load_build_history_sorted_newest_first(tmp_path):
    builds_dir = tmp_path / ".forge" / "builds"
    builds_dir.mkdir(parents=True)

    for i, ts in enumerate(["2026-03-10T10:00:00", "2026-03-12T10:00:00", "2026-03-11T10:00:00"]):
        record = {"timestamp": ts, "project": "test", "health_grade": "A", "total_cost": i}
        (builds_dir / f"build_{i}.json").write_text(json.dumps(record))

    result = load_build_history(tmp_path)
    assert len(result) == 3
    assert result[0]["timestamp"] == "2026-03-12T10:00:00"
    assert result[1]["timestamp"] == "2026-03-11T10:00:00"
    assert result[2]["timestamp"] == "2026-03-10T10:00:00"


def test_load_build_log_empty(tmp_path):
    result = load_build_log(tmp_path)
    assert result == []


def test_load_build_log_reads_jsonl(tmp_path):
    log_dir = tmp_path / ".forge"
    log_dir.mkdir(parents=True)
    log_file = log_dir / "build.log"

    entries = [
        {"event": "session_started", "ts": "2026-03-12T10:00:00"},
        {"event": "task_completed", "ts": "2026-03-12T10:01:00"},
        {"event": "session_ended", "ts": "2026-03-12T10:02:00"},
    ]
    log_file.write_text("\n".join(json.dumps(e) for e in entries))

    result = load_build_log(tmp_path)
    assert len(result) == 3
    assert result[0]["event"] == "session_started"
    assert result[2]["event"] == "session_ended"


def test_load_build_log_respects_limit(tmp_path):
    log_dir = tmp_path / ".forge"
    log_dir.mkdir(parents=True)
    log_file = log_dir / "build.log"

    lines = [json.dumps({"event": f"e{i}", "ts": f"t{i}"}) for i in range(10)]
    log_file.write_text("\n".join(lines))

    result = load_build_log(tmp_path, limit=3)
    assert len(result) == 3
    assert result[0]["event"] == "e7"
    assert result[2]["event"] == "e9"


def test_history_data_returns_json(tmp_path):
    handler = MagicMock()
    handler.wfile = BytesIO()

    handle_history_data(handler, tmp_path)

    handler.send_response.assert_called_once_with(200)
    written = handler.wfile.getvalue()
    data = json.loads(written.decode("utf-8"))
    assert data["builds"] == []
    assert data["count"] == 0
