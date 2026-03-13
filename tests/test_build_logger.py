"""Tests for forge.build_logger module."""

import json
from pathlib import Path

import pytest

from forge.build_logger import BuildLogger, read_log, new_session_id


def test_new_session_id_is_8_chars():
    """Session ID is exactly 8 hex characters."""
    sid = new_session_id()
    assert len(sid) == 8
    assert all(c in "0123456789abcdef" for c in sid)


def test_new_session_id_is_unique():
    """Two calls return different session IDs."""
    ids = {new_session_id() for _ in range(10)}
    assert len(ids) == 10


def test_build_logger_creates_log_file(tmp_path):
    """First log() call creates .forge/build.log."""
    logger = BuildLogger(tmp_path, session_id="test1234")
    logger.log("test_event")
    assert (tmp_path / ".forge" / "build.log").exists()


def test_log_writes_valid_json(tmp_path):
    """Each log line is valid JSON."""
    logger = BuildLogger(tmp_path, session_id="test1234")
    logger.session_started("my-project", 3)
    logger.task_started(0, "t_01", "Build UI")

    lines = (tmp_path / ".forge" / "build.log").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        data = json.loads(line)
        assert isinstance(data, dict)


def test_log_required_fields_present(tmp_path):
    """Every record has ts, event, session, phase, task fields."""
    logger = BuildLogger(tmp_path, session_id="abcd1234")
    logger.log("test_event", phase=1, task="t_01", extra="value")

    lines = (tmp_path / ".forge" / "build.log").read_text(encoding="utf-8").strip().splitlines()
    data = json.loads(lines[0])
    assert "ts" in data
    assert data["event"] == "test_event"
    assert data["session"] == "abcd1234"
    assert data["phase"] == 1
    assert data["task"] == "t_01"
    assert data["extra"] == "value"


def test_log_session_id_consistent(tmp_path):
    """All records from same logger share session_id."""
    logger = BuildLogger(tmp_path, session_id="sess0001")
    logger.log("event_a")
    logger.log("event_b")
    logger.log("event_c")

    records = read_log(tmp_path)
    sessions = {r["session"] for r in records}
    assert sessions == {"sess0001"}


def test_log_never_raises_on_bad_path(tmp_path):
    """log() does not raise even if write fails."""
    # Use a path that can't be written to
    bad_path = tmp_path / "nonexistent" / "deep" / "path"
    logger = BuildLogger(bad_path, session_id="test1234")
    # _ensure_log_dir may fail, but log() should not raise
    logger.log("test_event")
    # No assertion needed - just verify no exception


def test_task_completed_writes_cost(tmp_path):
    """task_completed record includes cost and token fields."""
    logger = BuildLogger(tmp_path, session_id="test1234")
    logger.task_completed(0, "t_01", "Build login", 45.0, 0.022, 12400, 2100)

    records = read_log(tmp_path)
    assert len(records) == 1
    r = records[0]
    assert r["event"] == "task_completed"
    assert r["cost"] == 0.022
    assert r["tokens_in"] == 12400
    assert r["tokens_out"] == 2100
    assert r["duration_secs"] == 45.0


def test_qa_passed_truncates_summary(tmp_path):
    """qa_passed summary truncated to 100 chars."""
    logger = BuildLogger(tmp_path, session_id="test1234")
    long_summary = "x" * 200
    logger.qa_passed(0, "t_01", "Task", long_summary)

    records = read_log(tmp_path)
    assert len(records[0]["summary_preview"]) == 100


def test_read_log_returns_all_records(tmp_path):
    """read_log() returns all written records."""
    logger = BuildLogger(tmp_path, session_id="test1234")
    logger.log("event_1")
    logger.log("event_2")
    logger.log("event_3")

    records = read_log(tmp_path)
    assert len(records) == 3
    events = [r["event"] for r in records]
    assert events == ["event_1", "event_2", "event_3"]


def test_read_log_filters_by_event(tmp_path):
    """event_filter returns only matching records."""
    logger = BuildLogger(tmp_path, session_id="test1234")
    logger.session_started("proj", 3)
    logger.task_started(0, "t_01", "Task 1")
    logger.task_completed(0, "t_01", "Task 1", 30.0, 0.01, 5000, 1000)
    logger.task_started(0, "t_02", "Task 2")

    records = read_log(tmp_path, event_filter="task_started")
    assert len(records) == 2
    assert all(r["event"] == "task_started" for r in records)


def test_read_log_limit(tmp_path):
    """limit parameter returns only last N records."""
    logger = BuildLogger(tmp_path, session_id="test1234")
    for i in range(10):
        logger.log(f"event_{i}")

    records = read_log(tmp_path, limit=3)
    assert len(records) == 3
    assert records[0]["event"] == "event_7"
    assert records[2]["event"] == "event_9"


def test_read_log_skips_malformed_lines(tmp_path):
    """Malformed JSON lines are skipped without error."""
    log_dir = tmp_path / ".forge"
    log_dir.mkdir()
    log_path = log_dir / "build.log"
    content = '{"event":"good","session":"x","ts":"t","phase":null,"task":null}\n'
    content += 'this is not json\n'
    content += '{"event":"also_good","session":"x","ts":"t","phase":null,"task":null}\n'
    log_path.write_text(content, encoding="utf-8")

    records = read_log(tmp_path)
    assert len(records) == 2
    assert records[0]["event"] == "good"
    assert records[1]["event"] == "also_good"


def test_read_log_empty_file_returns_empty(tmp_path):
    """read_log() on missing file returns empty list."""
    records = read_log(tmp_path)
    assert records == []
