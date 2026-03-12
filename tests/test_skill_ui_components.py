"""Tests for the UI components skill pack and its injection into task prompts."""

from pathlib import Path

import pytest

from forge.state import Phase, Task


SKILL_PATH = Path(__file__).resolve().parent.parent / "forge" / "skills" / "ui-components.md"


def _read_skill():
    return SKILL_PATH.read_text()


# ---------------------------------------------------------------------------
# Skill file content tests
# ---------------------------------------------------------------------------


def test_ui_skill_file_exists():
    """forge/skills/ui-components.md exists."""
    assert SKILL_PATH.exists(), "ui-components.md not found"


def test_ui_skill_has_required_sections():
    """All 10 sections present."""
    content = _read_skill().lower()
    sections = [
        "shadcn",
        "tailwind",
        "component architecture",
        "accessibility",
        "form pattern",
        "data display",
        "state management",
        "performance",
        "file organization",
        "anti-pattern",
    ]
    for section in sections:
        assert section in content, f"Missing section: {section}"


def test_ui_skill_mentions_cn_utility():
    """Mentions cn() for className merging."""
    content = _read_skill()
    assert "cn(" in content


def test_ui_skill_mentions_cva():
    """Mentions cva() for component variants."""
    content = _read_skill()
    assert "cva(" in content or "cva()" in content


def test_ui_skill_mentions_forward_ref():
    """Mentions React.forwardRef requirement."""
    content = _read_skill()
    assert "forwardRef" in content


def test_ui_skill_mentions_accessibility():
    """Covers focus management, ARIA, contrast."""
    content = _read_skill().lower()
    assert "aria" in content
    assert "focus" in content
    assert "contrast" in content


def test_ui_skill_mentions_react_hook_form():
    """Skill covers react-hook-form for forms."""
    content = _read_skill().lower()
    assert "react-hook-form" in content


def test_ui_skill_mentions_tanstack_query():
    """Server state via React Query, not useEffect."""
    content = _read_skill().lower()
    assert "tanstack" in content or "react query" in content


def test_ui_skill_mentions_next_dynamic():
    """Code splitting for heavy components."""
    content = _read_skill()
    assert "next/dynamic" in content


# ---------------------------------------------------------------------------
# Skill injection tests
# ---------------------------------------------------------------------------


def _make_phase_and_task(title, description=""):
    phase = Phase.new("Phase 1: Setup", "Set up the project")
    task = Task.new(title, description, phase.id)
    return phase, task


def _setup_project(tmp_path):
    (tmp_path / "VISION.md").write_text("Test project vision")
    (tmp_path / "ARCHITECTURE.md").write_text("Test arch")
    (tmp_path / "CLAUDE.md").write_text("Test standards")
    (tmp_path / ".forge" / "memory").mkdir(parents=True, exist_ok=True)


def test_build_task_prompt_injects_ui_skill(tmp_path, monkeypatch):
    """build_task_prompt() includes UI skill for component tasks."""
    _setup_project(tmp_path)

    phase, task = _make_phase_and_task(
        "Build accessible data table component with sorting",
        "Create a shadcn DataTable with column sorting and pagination",
    )

    from forge.orchestrator import build_task_prompt

    prompt = build_task_prompt(tmp_path, phase, task)
    assert "cn(" in prompt
    assert "forwardref" in prompt.lower()


def test_build_task_prompt_no_ui_skill_for_api(tmp_path, monkeypatch):
    """UI skill not injected for pure API/backend tasks."""
    _setup_project(tmp_path)

    phase, task = _make_phase_and_task(
        "Set up REST API endpoints for user management",
        "Create CRUD API routes with validation and error handling",
    )

    from forge.orchestrator import build_task_prompt

    prompt = build_task_prompt(tmp_path, phase, task)
    assert "cn(" not in prompt
    assert "cva(" not in prompt


def test_ui_and_frontend_design_both_inject(tmp_path, monkeypatch):
    """Both ui-components and frontend-design inject for component tasks."""
    _setup_project(tmp_path)

    # Ensure frontend skill exists for the test
    skills_dir = Path(__file__).resolve().parent.parent / "forge" / "skills"
    fe_skill = skills_dir / "frontend-design.md"
    fe_existed = fe_skill.exists()
    if not fe_existed:
        fe_skill.write_text("# Frontend Design Skill\nUse Tailwind for styling.\n")

    try:
        phase, task = _make_phase_and_task(
            "Build responsive dashboard layout with sidebar navigation",
            "Create a React layout component with sidebar and responsive design",
        )

        from forge.orchestrator import build_task_prompt

        prompt = build_task_prompt(tmp_path, phase, task)
        # UI skill present (component, layout, sidebar, responsive)
        assert "forwardref" in prompt.lower()
        # Frontend skill present (layout, responsive, design)
        assert "frontend" in prompt.lower() or "tailwind" in prompt.lower()
    finally:
        if not fe_existed:
            fe_skill.unlink()
