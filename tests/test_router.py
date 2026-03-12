"""Tests for forge.router module."""

import os

from forge.cost_tracker import MODEL_OPUS, MODEL_SONNET, MODEL_HAIKU
from forge.router import (
    route_orchestrator,
    route_task,
    escalate_model,
    log_route,
    ROUTING_RULES,
    MODEL_TIERS,
)


# ---------------------------------------------------------------------------
# route_orchestrator
# ---------------------------------------------------------------------------

def test_route_orchestrator_known_function():
    """generate_phases routes to haiku."""
    assert route_orchestrator("generate_phases") == MODEL_HAIKU


def test_route_orchestrator_evaluate_qa_always_opus():
    """evaluate_qa always routes to opus."""
    assert route_orchestrator("evaluate_qa") == MODEL_OPUS


def test_route_orchestrator_unknown_falls_back_to_opus():
    """Unknown function name returns opus (safe default)."""
    assert route_orchestrator("unknown_func") == MODEL_OPUS


# ---------------------------------------------------------------------------
# route_task
# ---------------------------------------------------------------------------

def test_route_task_high_complexity_signal():
    """Task with 'payment' in title routes to opus."""
    model, reason = route_task("Set up Stripe payment integration", "")
    assert model == MODEL_OPUS
    assert "payment" in reason


def test_route_task_high_complexity_in_description():
    """High complexity signal in description routes to opus."""
    model, reason = route_task("Build module", "Handle JWT token validation")
    assert model == MODEL_OPUS
    assert "jwt" in reason


def test_route_task_low_complexity_signal():
    """Task with 'readme' in title routes to haiku."""
    model, reason = route_task("Update README with setup instructions", "")
    assert model == MODEL_HAIKU
    assert "readme" in reason


def test_route_task_default_is_sonnet():
    """Task with no signals routes to sonnet."""
    model, reason = route_task("Build user dashboard", "Create the main UI")
    assert model == MODEL_SONNET
    assert reason == "default"


def test_route_task_escalation_haiku_to_sonnet():
    """After 2 haiku failures, escalates to sonnet."""
    model, reason = route_task(
        "Update README", "",
        retry_count=2, previous_model=MODEL_HAIKU,
    )
    assert model == MODEL_SONNET
    assert "escalated" in reason.lower()


def test_route_task_escalation_sonnet_to_opus():
    """After 2 sonnet failures, escalates to opus."""
    model, reason = route_task(
        "Build user dashboard", "",
        retry_count=2, previous_model=MODEL_SONNET,
    )
    assert model == MODEL_OPUS
    assert "escalated" in reason.lower()


def test_route_task_no_escalation_beyond_opus():
    """Opus does not escalate further, falls back to signal matching."""
    model, reason = route_task(
        "Build user dashboard", "",
        retry_count=3, previous_model=MODEL_OPUS,
    )
    # No escalation possible, so falls through to signal matching / default
    assert model == MODEL_SONNET
    assert reason == "default"


def test_route_task_returns_reason_string():
    """route_task returns non-empty reason string."""
    _, reason = route_task("Some task", "some description")
    assert isinstance(reason, str)
    assert len(reason) > 0


def test_route_task_high_priority_over_low():
    """If both high and low signals present, high wins."""
    model, reason = route_task(
        "Update documentation for auth system",
        "Add documentation for the JWT authentication flow",
    )
    assert model == MODEL_OPUS
    # Should match a high signal (auth or jwt), not low (documentation)


# ---------------------------------------------------------------------------
# escalate_model
# ---------------------------------------------------------------------------

def test_escalate_model_haiku():
    """escalate_model(haiku) returns sonnet."""
    assert escalate_model(MODEL_HAIKU) == MODEL_SONNET


def test_escalate_model_sonnet():
    """escalate_model(sonnet) returns opus."""
    assert escalate_model(MODEL_SONNET) == MODEL_OPUS


def test_escalate_model_opus_returns_none():
    """escalate_model(opus) returns None."""
    assert escalate_model(MODEL_OPUS) is None


# ---------------------------------------------------------------------------
# log_route
# ---------------------------------------------------------------------------

def test_log_route_prints_with_verbose(capsys, monkeypatch):
    """log_route prints when FORGE_VERBOSE is set."""
    monkeypatch.setenv("FORGE_VERBOSE", "1")
    log_route("generate_phases", MODEL_HAIKU, "structured list")
    captured = capsys.readouterr()
    assert "[route]" in captured.out
    assert "haiku" in captured.out
    assert "structured list" in captured.out
