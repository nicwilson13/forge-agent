"""Tests for forge.cost_tracker module."""

import json
from pathlib import Path

import pytest

from forge.cost_tracker import (
    TokenUsage,
    TaskCost,
    CostTracker,
    calculate_task_cost,
    _format_cost,
    _accumulate,
    PRICING,
    MODEL_OPUS,
    MODEL_SONNET,
    MODEL_HAIKU,
    DEFAULT_TASK_TOKEN_ALERT,
    DEFAULT_SESSION_COST_ALERT,
)


# ---------------------------------------------------------------------------
# TokenUsage
# ---------------------------------------------------------------------------

def test_token_usage_total():
    """total_tokens sums input and output."""
    u = TokenUsage(input_tokens=1000, output_tokens=500)
    assert u.total_tokens == 1500


def test_token_usage_cost_opus():
    """Opus pricing: 1M input=$15, 1M output=$75."""
    u = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000, model=MODEL_OPUS)
    assert abs(u.estimated_cost - 90.00) < 0.01


def test_token_usage_cost_sonnet():
    """Sonnet pricing: 1M input=$3, 1M output=$15."""
    u = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000, model=MODEL_SONNET)
    assert abs(u.estimated_cost - 18.00) < 0.01


def test_token_usage_unknown_model_falls_back_to_opus():
    """Unknown model falls back to Opus pricing."""
    u = TokenUsage(input_tokens=1_000_000, output_tokens=0, model="claude-unknown-9")
    assert abs(u.estimated_cost - 15.00) < 0.01


# ---------------------------------------------------------------------------
# _format_cost
# ---------------------------------------------------------------------------

def test_format_cost_below_one():
    """Under $1: 3 decimal places."""
    assert _format_cost(0.038) == "$0.038"


def test_format_cost_above_one():
    """At/above $1: 2 decimal places."""
    assert _format_cost(3.475) == "$3.48"


# ---------------------------------------------------------------------------
# calculate_task_cost
# ---------------------------------------------------------------------------

def test_calculate_task_cost_builds_correct_record():
    """calculate_task_cost creates a TaskCost with builder estimates."""
    orch = TokenUsage(input_tokens=5000, output_tokens=2000, model=MODEL_OPUS)
    tc = calculate_task_cost(
        task_id="P1-T1",
        task_title="Build UI",
        phase_index=0,
        phase_title="Phase 1",
        duration_secs=45.0,
        orchestrator_usage=orch,
        builder_prompt_chars=16000,  # ~4000 tokens at 4 chars/token
        builder_output_chars=8000,   # ~2000 tokens
    )
    assert tc.task_id == "P1-T1"
    assert tc.orchestrator == orch
    assert tc.builder.model == MODEL_SONNET
    assert tc.builder.input_tokens > 0
    assert tc.builder.output_tokens > 0
    assert tc.total_cost > 0


# ---------------------------------------------------------------------------
# TaskCost.to_dict
# ---------------------------------------------------------------------------

def test_task_cost_to_dict_serializable():
    """to_dict output is JSON-serializable."""
    tc = TaskCost(
        task_id="P1-T1",
        task_title="Test task",
        phase_index=0,
        phase_title="Phase 1",
        timestamp="2026-01-01T00:00:00",
        duration_secs=30.0,
        orchestrator=TokenUsage(1000, 500, MODEL_OPUS),
        builder=TokenUsage(2000, 1000, MODEL_SONNET),
        total_cost=0.05,
    )
    d = tc.to_dict()
    serialized = json.dumps(d)
    assert isinstance(serialized, str)
    parsed = json.loads(serialized)
    assert parsed["task_id"] == "P1-T1"
    assert parsed["orchestrator"]["input_tokens"] == 1000


# ---------------------------------------------------------------------------
# CostTracker
# ---------------------------------------------------------------------------

def _make_task_cost(task_id="P1-T1", phase_index=0,
                    orch_in=1000, orch_out=500,
                    bldr_in=2000, bldr_out=1000) -> TaskCost:
    """Helper to build a TaskCost for tests."""
    orch = TokenUsage(orch_in, orch_out, MODEL_OPUS)
    bldr = TokenUsage(bldr_in, bldr_out, MODEL_SONNET)
    return TaskCost(
        task_id=task_id,
        task_title=f"Task {task_id}",
        phase_index=phase_index,
        phase_title=f"Phase {phase_index + 1}",
        timestamp="2026-01-01T00:00:00",
        duration_secs=30.0,
        orchestrator=orch,
        builder=bldr,
        total_cost=orch.estimated_cost + bldr.estimated_cost,
    )


def test_tracker_accumulation(tmp_path):
    """Session totals accumulate across multiple tasks."""
    tracker = CostTracker(tmp_path)
    tc1 = _make_task_cost("P1-T1", orch_in=1000, orch_out=500)
    tc2 = _make_task_cost("P1-T2", orch_in=2000, orch_out=1000)
    tracker.record_task(tc1)
    tracker.record_task(tc2)

    assert tracker.session_total_cost() == pytest.approx(tc1.total_cost + tc2.total_cost)
    total_in, total_out = tracker.session_total_tokens()
    assert total_in == (1000 + 2000) + (2000 + 2000)  # orch_in + bldr_in for each
    assert total_out == (500 + 1000) + (1000 + 1000)


def test_tracker_jsonl_write(tmp_path):
    """record_task writes a JSONL entry to .forge/cost_log.jsonl."""
    tracker = CostTracker(tmp_path)
    tc = _make_task_cost()
    tracker.record_task(tc)

    log_path = tmp_path / ".forge" / "cost_log.jsonl"
    assert log_path.exists()
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["task_id"] == "P1-T1"


def test_tracker_task_token_alert(tmp_path):
    """Alert triggered when task tokens exceed threshold."""
    tracker = CostTracker(tmp_path, task_token_alert=5000)
    # Total tokens: 3000+3000 = 6000 > 5000
    tc = _make_task_cost(orch_in=2000, orch_out=1000, bldr_in=2000, bldr_out=1000)
    alerts = tracker.record_task(tc)
    assert len(alerts) >= 1
    assert "High token usage" in alerts[0]


def test_tracker_session_cost_alert(tmp_path):
    """Alert triggered when session cost exceeds threshold."""
    tracker = CostTracker(tmp_path, session_cost_alert=0.001)
    tc = _make_task_cost()
    alerts = tracker.record_task(tc)
    # Should have session cost alert
    cost_alerts = [a for a in alerts if "Session cost alert" in a]
    assert len(cost_alerts) == 1


def test_tracker_no_alert_below_threshold(tmp_path):
    """No alerts when usage is below thresholds."""
    tracker = CostTracker(tmp_path, task_token_alert=999_999, session_cost_alert=999.0)
    tc = _make_task_cost(orch_in=100, orch_out=50, bldr_in=100, bldr_out=50)
    alerts = tracker.record_task(tc)
    assert alerts == []


def test_tracker_load_from_log(tmp_path):
    """load_from_log reads existing JSONL records."""
    # Write a record manually
    log_dir = tmp_path / ".forge"
    log_dir.mkdir()
    log_path = log_dir / "cost_log.jsonl"
    record = _make_task_cost("P2-T3", phase_index=1)
    log_path.write_text(json.dumps(record.to_dict()) + "\n", encoding="utf-8")

    tracker = CostTracker(tmp_path)
    tracker.load_from_log()
    assert len(tracker._records) == 1
    assert tracker._records[0].task_id == "P2-T3"


def test_tracker_format_task_line(tmp_path):
    """format_task_line produces readable output."""
    tracker = CostTracker(tmp_path)
    tc = _make_task_cost(orch_in=5000, orch_out=2000, bldr_in=3000, bldr_out=1000)
    line = tracker.format_task_line(tc)
    assert "Tokens:" in line
    assert "Cost:" in line
    assert "$" in line


def test_tracker_format_session_summary(tmp_path):
    """format_session_summary includes totals."""
    tracker = CostTracker(tmp_path)
    tracker.record_task(_make_task_cost("P1-T1"))
    tracker.record_task(_make_task_cost("P1-T2"))
    summary = tracker.format_session_summary()
    assert "Total tokens:" in summary
    assert "Total cost:" in summary
    assert "cost_log.jsonl" in summary


def test_tracker_phase_summary_filtering(tmp_path):
    """phase_summary returns data for specific phase only."""
    tracker = CostTracker(tmp_path)
    tracker.record_task(_make_task_cost("P1-T1", phase_index=0))
    tracker.record_task(_make_task_cost("P2-T1", phase_index=1))
    tracker.record_task(_make_task_cost("P2-T2", phase_index=1))

    s0 = tracker.phase_summary(0)
    assert s0["tasks"] == 1

    s1 = tracker.phase_summary(1)
    assert s1["tasks"] == 2


# ---------------------------------------------------------------------------
# _accumulate
# ---------------------------------------------------------------------------

def test_accumulate_sums_usages():
    """_accumulate sums input/output tokens."""
    u1 = TokenUsage(1000, 500, MODEL_OPUS)
    u2 = TokenUsage(2000, 1000, MODEL_OPUS)
    result = _accumulate([u1, u2])
    assert result.input_tokens == 3000
    assert result.output_tokens == 1500


# ---------------------------------------------------------------------------
# PRICING
# ---------------------------------------------------------------------------

def test_pricing_has_three_models():
    """PRICING dict has entries for all three models."""
    assert len(PRICING) == 3
    assert MODEL_OPUS in PRICING
    assert MODEL_SONNET in PRICING
    assert MODEL_HAIKU in PRICING
