"""
Tests for forge.display module.
"""

from forge.display import (
    divider, _format_duration,
    print_task_header, print_task_success, print_build_complete,
)


def test_divider_heavy_is_wide():
    """Heavy divider should be at least 60 chars."""
    d = divider("heavy")
    assert len(d) >= 60


def test_divider_light_is_wide():
    """Light divider should be at least 60 chars."""
    d = divider("light")
    assert len(d) >= 60


def test_format_duration_seconds():
    """48.3 seconds formats as '48s'."""
    assert _format_duration(48.3) == "48s"


def test_format_duration_minutes():
    """192.0 seconds formats as '3m 12s'."""
    assert _format_duration(192.0) == "3m 12s"


def test_format_duration_hours():
    """5040.0 seconds formats as '1h 24m'."""
    assert _format_duration(5040.0) == "1h 24m"


def test_print_task_header_no_crash(capsys):
    """print_task_header runs without exception."""
    print_task_header("Build auth flow", 2, 8, "Core Features")
    captured = capsys.readouterr()
    assert "Build auth flow" in captured.out
    assert "3 of 8" in captured.out


def test_print_task_success_no_crash(capsys):
    """print_task_success runs without exception."""
    print_task_success(52.0, 3, 8, "Core Features")
    captured = capsys.readouterr()
    assert "52s" in captured.out
    assert "3/8" in captured.out


def test_print_build_complete_no_crash(capsys):
    """print_build_complete runs without exception."""
    print_build_complete("my-app", 5, 34, 4980.0)
    captured = capsys.readouterr()
    assert "my-app" in captured.out
    assert "BUILD COMPLETE" in captured.out
    assert "34" in captured.out
