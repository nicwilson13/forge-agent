"""Tests for forge.e2e_generator module."""

from pathlib import Path

from forge.e2e_generator import (
    should_generate_e2e,
    parse_playwright_output,
    e2e_failure_context,
    run_e2e_tests,
    _make_e2e_slug,
    E2E_PHASE_SIGNALS,
)


# ---------------------------------------------------------------------------
# Fake task helper
# ---------------------------------------------------------------------------

class _FakeTask:
    def __init__(self, title, description="", status="done"):
        self.title = title
        self.description = description
        self.status = status


# ---------------------------------------------------------------------------
# should_generate_e2e
# ---------------------------------------------------------------------------

def test_should_generate_e2e_auth_signal():
    """Phase with 'auth' in title warrants E2E generation."""
    assert should_generate_e2e("Auth & Security", [_FakeTask("Setup")]) is True


def test_should_generate_e2e_task_signal():
    """Phase warrants E2E if any task title has a signal."""
    tasks = [_FakeTask("Build login page"), _FakeTask("Configure eslint")]
    assert should_generate_e2e("Dev setup", tasks) is True


def test_should_generate_e2e_no_signal():
    """Phase with no signals returns False."""
    tasks = [_FakeTask("Configure eslint"), _FakeTask("Add gitignore")]
    assert should_generate_e2e("Dev setup", tasks) is False


# ---------------------------------------------------------------------------
# _make_e2e_slug
# ---------------------------------------------------------------------------

def test_make_e2e_slug_basic():
    """'Core Features' -> 'core-features'"""
    assert _make_e2e_slug("Core Features") == "core-features"


def test_make_e2e_slug_special_chars():
    """Special characters removed from slug."""
    assert _make_e2e_slug("Phase 2: Auth & Security") == "phase-2-auth-security"


# ---------------------------------------------------------------------------
# parse_playwright_output
# ---------------------------------------------------------------------------

def test_parse_playwright_output_all_pass():
    """Parses '4 passed' correctly."""
    output = "  4 passed (12s)\n"
    passed, failed, names = parse_playwright_output(output)
    assert passed == 4
    assert failed == 0
    assert names == []


def test_parse_playwright_output_with_failure():
    """Parses mixed pass/fail output correctly."""
    output = """
  Running 4 tests using 1 worker
  3 passed, 1 failed (8s)
"""
    passed, failed, names = parse_playwright_output(output)
    assert passed == 3
    assert failed == 1


def test_parse_playwright_output_extracts_failed_names():
    """Failed test names extracted from output."""
    output = """
  \u2713 User can log in (2s)
  \u2713 Login fails with wrong password (1s)
  \u2717 User can register
  \u2713 User is redirected after login (1s)
  3 passed, 1 failed (8s)
"""
    passed, failed, names = parse_playwright_output(output)
    assert failed == 1
    assert "User can register" in names


# ---------------------------------------------------------------------------
# e2e_failure_context
# ---------------------------------------------------------------------------

def test_e2e_failure_context_contains_test_names(tmp_path):
    """Failure context includes the failing test names."""
    test_file = tmp_path / "test.spec.ts"
    test_file.write_text("import { test } from '@playwright/test';", encoding="utf-8")

    context = e2e_failure_context(test_file, ["User can register"], "some output")
    assert "User can register" in context
    assert "some output" in context


# ---------------------------------------------------------------------------
# generate_e2e_tests / run_e2e_tests
# ---------------------------------------------------------------------------

def test_generate_e2e_tests_skips_when_no_playwright(monkeypatch):
    """Returns (None, reason) when API auth fails."""
    import httpx

    def _raise_auth(**kw):
        resp = httpx.Response(status_code=401, request=httpx.Request("POST", "https://api.anthropic.com"))
        raise __import__("anthropic").AuthenticationError(
            message="bad key", response=resp, body=None,
        )

    fake_messages = type("M", (), {"create": staticmethod(_raise_auth)})()
    fake_client = type("C", (), {"messages": fake_messages})()
    monkeypatch.setattr("forge.e2e_generator._get_client", lambda: fake_client)

    from forge.e2e_generator import generate_e2e_tests

    class FakePhase:
        title = "Core Features"
        description = "Build core features"
        id = "1"
        tasks = [_FakeTask("Build login page", "Create login form", "done")]

    result, reason = generate_e2e_tests(Path("/tmp/fake"), FakePhase())
    assert result is None
    assert "auth" in reason.lower()


def test_run_e2e_tests_never_raises(monkeypatch, tmp_path):
    """run_e2e_tests catches all exceptions."""
    def _boom(port):
        raise RuntimeError("boom")

    monkeypatch.setattr("forge.e2e_generator.is_dev_server_running", _boom)

    test_file = tmp_path / "test.spec.ts"
    test_file.write_text("test content", encoding="utf-8")

    passed, output, failed = run_e2e_tests(tmp_path, test_file)
    assert passed is False
