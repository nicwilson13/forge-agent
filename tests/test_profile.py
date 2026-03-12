"""
Tests for forge.profile and forge.commands.profile modules.
All file operations use tmp_path. No real API calls.
"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import yaml

from forge.profile import (
    load_profile,
    save_profile,
    has_profile,
    get_stack_summary,
    profile_to_claude_md_context,
    profile_path,
    PROFILE_CATEGORIES,
)


def _patch_profile_path(tmp_path):
    """Return a patch that redirects profile_path() to tmp_path."""
    return patch("forge.profile.profile_path", return_value=tmp_path / "profile.yaml")


# ---------------------------------------------------------------------------
# profile.py tests
# ---------------------------------------------------------------------------

def test_profile_path_in_home_directory():
    """Profile path is inside ~/.forge/"""
    p = profile_path()
    assert ".forge" in str(p)
    assert "profile.yaml" in str(p)


def test_load_profile_missing_returns_empty(tmp_path):
    """Returns empty dict when profile file does not exist."""
    with _patch_profile_path(tmp_path):
        result = load_profile()
    assert result == {}


def test_load_profile_returns_dict(tmp_path):
    """Returns populated dict when profile file exists."""
    profile_file = tmp_path / "profile.yaml"
    yaml.safe_dump({"framework": "Next.js", "language": "TypeScript"}, open(profile_file, "w"))

    with _patch_profile_path(tmp_path):
        result = load_profile()
    assert result["framework"] == "Next.js"
    assert result["language"] == "TypeScript"


def test_save_profile_creates_directory(tmp_path):
    """Creates parent directory if it does not exist."""
    profile_file = tmp_path / "subdir" / "profile.yaml"
    with patch("forge.profile.profile_path", return_value=profile_file):
        path = save_profile({"framework": "Remix"})
    assert path.exists()
    data = yaml.safe_load(open(path))
    assert data["framework"] == "Remix"


def test_save_profile_writes_yaml(tmp_path):
    """Profile is written as valid YAML."""
    profile_file = tmp_path / "profile.yaml"
    with patch("forge.profile.profile_path", return_value=profile_file):
        save_profile({"framework": "Next.js", "language": "TypeScript"})
    data = yaml.safe_load(open(profile_file))
    assert data["framework"] == "Next.js"
    assert "updated_at" in data
    assert "created_at" in data


def test_has_profile_false_when_missing(tmp_path):
    """Returns False when no profile file exists."""
    with _patch_profile_path(tmp_path):
        assert has_profile() is False


def test_has_profile_true_when_present(tmp_path):
    """Returns True when profile file exists with content."""
    profile_file = tmp_path / "profile.yaml"
    yaml.safe_dump({"framework": "Next.js"}, open(profile_file, "w"))
    with _patch_profile_path(tmp_path):
        assert has_profile() is True


def test_get_stack_summary_full_profile():
    """Returns correct one-line summary for a complete profile."""
    profile = {
        "framework": "Next.js",
        "language": "TypeScript",
        "database": "Supabase",
        "styling": "Tailwind CSS",
        "package_manager": "pnpm",
    }
    summary = get_stack_summary(profile)
    assert "Next.js" in summary
    assert "TypeScript" in summary
    assert "Supabase" in summary
    assert "Tailwind CSS" in summary
    assert "pnpm" in summary
    assert " · " in summary


def test_get_stack_summary_partial_profile():
    """Skips empty fields in the summary."""
    profile = {"framework": "Next.js", "language": "TypeScript"}
    summary = get_stack_summary(profile)
    assert summary == "Next.js · TypeScript"


def test_profile_to_claude_md_context_non_empty():
    """Returns non-empty string for populated profile."""
    profile = {"framework": "Next.js", "deployment": "Vercel"}
    ctx = profile_to_claude_md_context(profile)
    assert "Next.js" in ctx
    assert "Vercel" in ctx
    assert "Developer Profile" in ctx


def test_profile_to_claude_md_context_empty():
    """Returns empty string for empty profile."""
    assert profile_to_claude_md_context({}) == ""


# ---------------------------------------------------------------------------
# forge new integration tests
# ---------------------------------------------------------------------------

def test_forge_new_skips_stack_question_with_profile():
    """forge new interview auto-fills stack Q when profile has stack preferences."""
    from forge.commands.new import _conduct_interview

    mock_questions = [
        "Who uses this?",
        "What stack?",
        "Key features?",
        "Where deployed?",
        "Design style?",
    ]

    profile = {
        "framework": "Next.js",
        "language": "TypeScript",
        "database": "Supabase",
        "styling": "Tailwind CSS",
        "package_manager": "pnpm",
    }

    # _prompt is called for Q1, Q3, Q4, Q5 (4 times - stack is skipped)
    prompt_answers = iter(["Teams", "Tasks, lists", "Vercel", "Clean"])

    def mock_input(q):
        # Stack confirmation prompt - user hits Enter to accept
        return ""

    with patch("forge.commands.new._json_chat", return_value=mock_questions):
        with patch("forge.commands.new._prompt", side_effect=lambda q: next(prompt_answers)):
            with patch("builtins.input", side_effect=mock_input):
                result = _conduct_interview("a todo app", profile=profile)

    # a2 should be the stack summary from profile
    assert "Next.js" in result["a2"]
    assert "TypeScript" in result["a2"]
    # All 5 q/a pairs should exist
    for i in range(1, 6):
        assert f"q{i}" in result
        assert f"a{i}" in result


def test_forge_new_uses_profile_deployment_default():
    """forge new pre-fills deployment from profile."""
    from forge.commands.new import _conduct_interview

    mock_questions = [
        "Who uses this?",
        "What stack?",
        "Key features?",
        "Where deployed?",
        "Design style?",
    ]

    profile = {"deployment": "Vercel", "design_direction": "minimal"}

    # No stack in profile, so all 5 questions are shown
    prompt_answers = iter(["Teams", "Next.js", "Tasks"])
    prompt_default_answers = iter(["", ""])  # Enter to accept defaults

    with patch("forge.commands.new._json_chat", return_value=mock_questions):
        with patch("forge.commands.new._prompt", side_effect=lambda q: next(prompt_answers)):
            with patch("forge.commands.new._prompt_with_default",
                       side_effect=lambda q, d: next(prompt_default_answers) or d):
                result = _conduct_interview("a todo app", profile=profile)

    assert result["a4"] == "Vercel"
    assert result["a5"] == "minimal"


def test_build_interview_context_with_profile():
    """Profile data appears in the interview context string."""
    from forge.commands.new import _build_interview_context

    answers = {
        "description": "a todo app",
        "q1": "Who?", "a1": "Everyone",
        "q2": "Stack?", "a2": "Next.js",
        "q3": "Features?", "a3": "Tasks",
        "q4": "Deploy?", "a4": "Vercel",
        "q5": "Design?", "a5": "Clean",
    }
    profile = {"framework": "Next.js", "deployment": "Vercel"}

    context = _build_interview_context(answers, profile=profile)
    assert "Developer Profile" in context
    assert "Next.js" in context
    assert "Product description: a todo app" in context


def test_profile_registered_in_cli():
    """forge profile appears in forge --help output."""
    import subprocess
    result = subprocess.run(
        ["forge", "--help"],
        capture_output=True, text=True,
    )
    assert "profile" in result.stdout
