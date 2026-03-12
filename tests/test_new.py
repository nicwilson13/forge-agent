"""
Tests for forge.commands.new module.
Uses unittest.mock to mock all Anthropic API calls.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


def test_count_requirements_empty():
    """Returns 0 for empty content."""
    from forge.commands.new import _count_requirements

    assert _count_requirements("") == 0
    assert _count_requirements("No checkboxes here\nJust text") == 0


def test_count_requirements_with_items():
    """Counts checkbox items correctly."""
    from forge.commands.new import _count_requirements

    content = """# Requirements
- [ ] First requirement
- [ ] Second requirement
- [x] Third completed
- [ ] Fourth requirement
"""
    assert _count_requirements(content) == 4


def test_count_requirements_ignores_non_checkbox():
    """Does not count non-checkbox lines."""
    from forge.commands.new import _count_requirements

    content = """# Requirements
- First bullet (no checkbox)
- Second bullet
- [ ] Real checkbox item
* Star bullet
  - [ ] Indented checkbox
"""
    assert _count_requirements(content) == 2


def test_run_new_registered_in_cli():
    """forge new appears in forge --help output."""
    result = subprocess.run(
        ["forge", "--help"],
        capture_output=True,
        text=True,
    )
    assert "new" in result.stdout


def test_existing_docs_detected(tmp_path: Path):
    """Returns True when VISION.md exists in project dir."""
    from forge.commands.new import _has_existing_docs

    assert _has_existing_docs(tmp_path) is False

    (tmp_path / "VISION.md").write_text("# Vision")
    assert _has_existing_docs(tmp_path) is True


def test_generated_vision_minimum_length():
    """Mock API response: VISION.md content is at least 350 words."""
    from forge.commands.new import _generate_docs

    # A 400-word vision doc
    vision_content = "# VISION.md\n\n" + " ".join(["word"] * 400)
    requirements_content = "# REQUIREMENTS.md\n" + "\n".join(
        [f"- [ ] Requirement {i}" for i in range(25)]
    )
    claude_content = "# CLAUDE.md\n\nThis file is read by Forge.\n\n## Tech Stack\n- Language: TypeScript"

    call_count = 0
    def mock_chat(system, user, max_tokens=4096):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return vision_content
        elif call_count == 2:
            return requirements_content
        else:
            return claude_content

    with patch("forge.commands.new._chat", side_effect=mock_chat):
        answers = {
            "description": "a todo app",
            "q1": "Who?", "a1": "Teams",
            "q2": "Stack?", "a2": "Next.js",
            "q3": "Features?", "a3": "Tasks, lists",
            "q4": "Deploy?", "a4": "Vercel",
            "q5": "Design?", "a5": "Clean",
        }
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            generated = _generate_docs(Path(tmp), "a todo app", answers)

        assert len(generated["VISION.md"].split()) >= 350


def test_generated_requirements_minimum_count():
    """Mock API response: REQUIREMENTS.md has at least 20 items."""
    from forge.commands.new import _generate_docs, _count_requirements

    vision_content = "# VISION.md\n\n" + " ".join(["word"] * 400)
    requirements_content = "# REQUIREMENTS.md\n" + "\n".join(
        [f"- [ ] Requirement {i}" for i in range(25)]
    )
    claude_content = "# CLAUDE.md\n\nThis file is read by Forge.\n\n## Tech Stack\n- Language: Python"

    call_count = 0
    def mock_chat(system, user, max_tokens=4096):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return vision_content
        elif call_count == 2:
            return requirements_content
        else:
            return claude_content

    with patch("forge.commands.new._chat", side_effect=mock_chat):
        answers = {
            "description": "a CLI tool",
            "q1": "Who?", "a1": "Developers",
            "q2": "Stack?", "a2": "Python",
            "q3": "Features?", "a3": "Parse, format",
            "q4": "Deploy?", "a4": "PyPI",
            "q5": "Design?", "a5": "Minimal",
        }
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            generated = _generate_docs(Path(tmp), "a CLI tool", answers)

        assert _count_requirements(generated["REQUIREMENTS.md"]) >= 20


def test_interview_dict_has_required_keys():
    """Interview result dict contains description, q1-q5, a1-a5."""
    from forge.commands.new import _conduct_interview

    mock_questions = [
        "Who uses this?",
        "What stack?",
        "Key features?",
        "Where deployed?",
        "Design style?",
    ]

    input_answers = iter(["Teams", "Next.js", "Tasks, boards", "Vercel", "Clean"])

    with patch("forge.commands.new._json_chat", return_value=mock_questions):
        with patch("forge.commands.new._prompt", side_effect=input_answers):
            result = _conduct_interview("a project management tool")

    assert result["description"] == "a project management tool"
    for i in range(1, 6):
        assert f"q{i}" in result
        assert f"a{i}" in result
        assert result[f"q{i}"] == mock_questions[i - 1]
    assert result["a1"] == "Teams"
    assert result["a5"] == "Clean"
