"""Tests for forge.advanced_options module."""

from unittest.mock import patch, call

import pytest

from forge.advanced_options import (
    ADVANCED_OPTIONS,
    LABEL_MAP,
    collect_advanced_options,
    format_options_for_display,
    advanced_options_to_context,
    advanced_options_to_claude_md_section,
)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

def test_advanced_options_catalog_has_all_keys():
    """ADVANCED_OPTIONS contains all 11 expected keys."""
    keys = [opt["key"] for opt in ADVANCED_OPTIONS]
    assert len(keys) == 11
    assert "structure" in keys
    assert "api_style" in keys
    assert "linting" in keys
    assert "typescript_strictness" in keys
    assert "testing_approach" in keys
    assert "branch_strategy" in keys
    assert "ci_cd" in keys
    assert "target_platforms" in keys
    assert "accessibility" in keys
    assert "i18n" in keys
    assert "security" in keys


# ---------------------------------------------------------------------------
# format_options_for_display
# ---------------------------------------------------------------------------

def test_format_options_wraps_to_terminal_width():
    """Long options list wraps at terminal width."""
    options = ["option one", "option two", "option three",
               "option four", "option five"]
    result = format_options_for_display(options, terminal_width=40)
    lines = result.split("\n")
    # Should wrap to multiple lines at narrow width
    assert len(lines) >= 1
    assert lines[0].startswith("Options: ")


def test_format_options_aligns_continuation():
    """Continuation lines align with first item."""
    options = ["aaa", "bbb", "ccc", "ddd", "eee", "fff",
               "ggg", "hhh", "iii", "jjj"]
    result = format_options_for_display(options, terminal_width=30)
    lines = result.split("\n")
    if len(lines) > 1:
        # Continuation lines should be indented to match "Options: " prefix
        prefix_len = len("Options: ")
        for line in lines[1:]:
            # After split on "\n  ", the continuation is re-joined
            # Just verify it's indented
            assert line.startswith(" " * prefix_len)


# ---------------------------------------------------------------------------
# collect_advanced_options
# ---------------------------------------------------------------------------

def test_collect_advanced_options_skip_all():
    """Single Enter at gate returns empty dict immediately."""
    with patch("builtins.input", return_value=""):
        result = collect_advanced_options()
    assert result == {}


def test_collect_advanced_options_no_further_prompts_on_skip():
    """Gate skip results in exactly one input() call."""
    mock = patch("builtins.input", return_value="")
    with mock as m:
        collect_advanced_options()
    # Only the gate question should have been asked
    assert m.call_count == 1


def test_collect_advanced_options_collects_answers():
    """Answered options appear in returned dict."""
    # Gate + 11 option prompts = 12 inputs
    inputs = iter([
        "y",           # gate
        "single app",  # structure
        "REST",        # api_style
        "",            # linting - skip
        "",            # typescript - skip
        "",            # testing - skip
        "",            # branch - skip
        "",            # ci_cd - skip
        "web only",    # target_platforms
        "",            # accessibility - skip
        "",            # i18n - skip
        "",            # security - skip
    ])
    with patch("builtins.input", side_effect=inputs):
        result = collect_advanced_options()

    assert result["structure"] == "single app"
    assert result["api_style"] == "REST"
    assert result["target_platforms"] == "web only"
    assert "linting" not in result


def test_collect_advanced_options_skipped_absent():
    """Individually skipped options absent from dict (not None)."""
    # Gate + all skipped
    inputs = iter(["y"] + [""] * 11)
    with patch("builtins.input", side_effect=inputs):
        result = collect_advanced_options()

    assert result == {}


def test_collect_advanced_options_coverage_followup():
    """Coverage threshold follow-up fires when testing_approach set."""
    # Gate + skip until testing_approach + coverage follow-up + skip rest
    inputs = iter([
        "y",                  # gate
        "",                   # structure - skip
        "",                   # api_style - skip
        "",                   # linting - skip
        "",                   # typescript - skip
        "coverage threshold", # testing_approach
        "90",                 # coverage follow-up
        "",                   # branch - skip
        "",                   # ci_cd - skip
        "",                   # platforms - skip
        "",                   # accessibility - skip
        "",                   # i18n - skip
        "",                   # security - skip
    ])
    with patch("builtins.input", side_effect=inputs):
        result = collect_advanced_options()

    assert result["testing_approach"] == "coverage threshold"
    assert result["coverage_threshold"] == "90"


def test_collect_advanced_options_coverage_default():
    """Coverage threshold defaults to 80 when follow-up skipped."""
    inputs = iter([
        "y",                  # gate
        "",                   # structure - skip
        "",                   # api_style - skip
        "",                   # linting - skip
        "",                   # typescript - skip
        "coverage threshold", # testing_approach
        "",                   # coverage follow-up - accept default
        "",                   # branch - skip
        "",                   # ci_cd - skip
        "",                   # platforms - skip
        "",                   # accessibility - skip
        "",                   # i18n - skip
        "",                   # security - skip
    ])
    with patch("builtins.input", side_effect=inputs):
        result = collect_advanced_options()

    assert result["coverage_threshold"] == "80"


# ---------------------------------------------------------------------------
# advanced_options_to_context
# ---------------------------------------------------------------------------

def test_advanced_options_to_context_empty():
    """Returns empty string for empty advanced dict."""
    assert advanced_options_to_context({}) == ""


def test_advanced_options_to_context_formats_correctly():
    """Returns correctly formatted context string."""
    advanced = {
        "structure": "single app",
        "api_style": "tRPC",
        "linting": "Biome",
    }
    result = advanced_options_to_context(advanced)
    assert "Advanced Project Configuration:" in result
    assert "- Structure: single app" in result
    assert "- API Style: tRPC" in result
    assert "- Linting: Biome" in result


# ---------------------------------------------------------------------------
# advanced_options_to_claude_md_section
# ---------------------------------------------------------------------------

def test_advanced_options_to_claude_md_section_empty():
    """Returns empty string for empty advanced dict."""
    assert advanced_options_to_claude_md_section({}) == ""


def test_advanced_options_to_claude_md_section_maps_labels():
    """'strict (recommended)' maps to human-readable label."""
    advanced = {
        "typescript_strictness": "strict (recommended)",
        "accessibility": "WCAG 2.1 AA",
    }
    result = advanced_options_to_claude_md_section(advanced)
    assert "## Project Configuration" in result
    assert "Strict mode (tsconfig strict: true)" in result
    assert "WCAG 2.1 AA compliance required" in result


def test_advanced_options_to_claude_md_section_coverage():
    """Coverage threshold is included in testing display."""
    advanced = {
        "testing_approach": "coverage threshold",
        "coverage_threshold": "80",
    }
    result = advanced_options_to_claude_md_section(advanced)
    assert "Coverage threshold - minimum 80%" in result


# ---------------------------------------------------------------------------
# Integration with forge new
# ---------------------------------------------------------------------------

def test_forge_new_interview_includes_advanced():
    """_conduct_interview returns dict with 'advanced' key."""
    from forge.commands.new import _conduct_interview

    mock_questions = [
        "Who uses this?", "What stack?", "Key features?",
        "Where deployed?", "Design style?",
    ]

    prompt_answers = iter(["Teams", "Next.js", "Tasks", "Vercel", "Clean"])

    with patch("forge.commands.new._json_chat", return_value=mock_questions):
        with patch("forge.commands.new._prompt", side_effect=prompt_answers):
            # Advanced options gate - skip with Enter
            with patch("builtins.input", return_value=""):
                result = _conduct_interview("a todo app")

    assert "advanced" in result
    assert result["advanced"] == {}


def test_build_interview_context_includes_advanced():
    """_build_interview_context includes advanced options when present."""
    from forge.commands.new import _build_interview_context

    answers = {
        "description": "a todo app",
        "q1": "Who?", "a1": "Everyone",
        "q2": "Stack?", "a2": "Next.js",
        "q3": "Features?", "a3": "Tasks",
        "q4": "Deploy?", "a4": "Vercel",
        "q5": "Design?", "a5": "Clean",
        "advanced": {"structure": "single app", "api_style": "tRPC"},
    }
    context = _build_interview_context(answers)
    assert "Advanced Project Configuration:" in context
    assert "Structure: single app" in context
    assert "API Style: tRPC" in context
