"""
Tests for forge.retry module.
All time.sleep calls are mocked. No real network requests.
"""

import pytest

from forge.retry import (
    is_retryable_error,
    is_fatal_error,
    extract_error_prefix,
    check_connectivity,
    with_retry,
    RetryExhaustedError,
    FatalAPIError,
    BACKOFF_SCHEDULE,
)


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

def test_is_retryable_rate_limit():
    """RATE_LIMIT is retryable."""
    assert is_retryable_error("RATE_LIMIT") is True


def test_is_retryable_connection_error():
    """CONNECTION_ERROR is retryable."""
    assert is_retryable_error("CONNECTION_ERROR") is True


def test_is_retryable_timeout():
    """TIMEOUT is retryable."""
    assert is_retryable_error("TIMEOUT") is True


def test_is_fatal_auth_error():
    """AUTH_ERROR is fatal."""
    assert is_fatal_error("AUTH_ERROR") is True


def test_is_not_fatal_process_error():
    """PROCESS_ERROR is not fatal (but also not retryable)."""
    assert is_fatal_error("PROCESS_ERROR") is False
    assert is_retryable_error("PROCESS_ERROR") is False


# ---------------------------------------------------------------------------
# Error prefix extraction
# ---------------------------------------------------------------------------

def test_extract_error_prefix_known():
    """Extracts known prefix from stderr string."""
    assert extract_error_prefix("RATE_LIMIT: too many requests") == "RATE_LIMIT"
    assert extract_error_prefix("AUTH_ERROR: invalid key") == "AUTH_ERROR"
    assert extract_error_prefix("CONNECTION_ERROR: network down") == "CONNECTION_ERROR"
    assert extract_error_prefix("TIMEOUT: timed out after 600s") == "TIMEOUT"


def test_extract_error_prefix_unknown():
    """Returns UNKNOWN for unrecognized stderr."""
    assert extract_error_prefix("Something went wrong") == "UNKNOWN"
    assert extract_error_prefix("random error text") == "UNKNOWN"


def test_extract_error_prefix_empty():
    """Returns UNKNOWN for empty stderr."""
    assert extract_error_prefix("") == "UNKNOWN"
    assert extract_error_prefix("   ") == "UNKNOWN"


# ---------------------------------------------------------------------------
# Connectivity check
# ---------------------------------------------------------------------------

def test_check_connectivity_returns_bool(monkeypatch):
    """check_connectivity always returns bool, never raises."""
    import requests as req_mod

    # Simulate successful connection
    class FakeResp:
        status_code = 200
    monkeypatch.setattr(req_mod, "head", lambda *a, **kw: FakeResp())
    assert check_connectivity() is True

    # Simulate connection failure
    monkeypatch.setattr(req_mod, "head", lambda *a, **kw: (_ for _ in ()).throw(ConnectionError("no network")))
    assert check_connectivity() is False


# ---------------------------------------------------------------------------
# with_retry
# ---------------------------------------------------------------------------

def test_with_retry_succeeds_first_attempt(monkeypatch):
    """Returns result immediately when func succeeds first try."""
    monkeypatch.setattr("time.sleep", lambda s: None)
    result = with_retry(lambda: 42, max_retries=5, error_context="test")
    assert result == 42


def test_with_retry_retries_on_rate_limit(monkeypatch):
    """Retries the correct number of times on RATE_LIMIT."""
    monkeypatch.setattr("time.sleep", lambda s: None)
    # Also mock isatty to avoid terminal output issues in test
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    call_count = 0

    def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise Exception("RATE_LIMIT: too many requests")
        return "success"

    result = with_retry(flaky, max_retries=5, error_context="test")
    assert result == "success"
    assert call_count == 3


def test_with_retry_raises_on_auth_error(monkeypatch):
    """Raises FatalAPIError immediately on AUTH_ERROR."""
    monkeypatch.setattr("time.sleep", lambda s: None)

    def always_auth_fail():
        raise Exception("AUTH_ERROR: invalid credentials")

    with pytest.raises(FatalAPIError) as exc_info:
        with_retry(always_auth_fail, max_retries=5, error_context="test")
    assert exc_info.value.error_prefix == "AUTH_ERROR"


def test_with_retry_raises_retry_exhausted(monkeypatch):
    """Raises RetryExhaustedError after max attempts."""
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    call_count = 0

    def always_fail():
        nonlocal call_count
        call_count += 1
        raise Exception("CONNECTION_ERROR: network down")

    with pytest.raises(RetryExhaustedError) as exc_info:
        with_retry(always_fail, max_retries=3, error_context="test")
    assert exc_info.value.attempts == 3
    assert call_count == 3


# ---------------------------------------------------------------------------
# Exception fields
# ---------------------------------------------------------------------------

def test_retry_exhausted_error_has_prefix():
    """RetryExhaustedError carries the error_prefix field."""
    err = RetryExhaustedError(
        error_prefix="RATE_LIMIT",
        attempts=5,
        last_error="too many requests",
    )
    assert err.error_prefix == "RATE_LIMIT"
    assert err.attempts == 5
    assert err.last_error == "too many requests"
    assert "5 retries" in str(err)


def test_fatal_api_error_has_fix_instruction():
    """FatalAPIError carries fix_instruction field."""
    err = FatalAPIError(
        error_prefix="AUTH_ERROR",
        message="Invalid API key",
        fix_instruction="export ANTHROPIC_API_KEY=sk-ant-...",
    )
    assert err.error_prefix == "AUTH_ERROR"
    assert err.fix_instruction == "export ANTHROPIC_API_KEY=sk-ant-..."
    assert "Invalid API key" in str(err)
