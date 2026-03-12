"""Tests for the database skill pack and its injection into task prompts."""

from pathlib import Path

import pytest

from forge.state import Phase, Task, ForgeState


SKILL_PATH = Path(__file__).resolve().parent.parent / "forge" / "skills" / "database.md"


def _read_skill():
    return SKILL_PATH.read_text()


# ---------------------------------------------------------------------------
# Skill file content tests
# ---------------------------------------------------------------------------


def test_database_skill_file_exists():
    """forge/skills/database.md exists."""
    assert SKILL_PATH.exists(), "database.md not found"


def test_database_skill_has_required_sections():
    """Skill file contains all 8 required topic sections."""
    content = _read_skill().lower()
    sections = [
        "schema design",
        "migration",
        "index",
        "rls",
        "query pattern",
        "orm",
        "seed",
        "anti-pattern",
    ]
    for section in sections:
        assert section in content, f"Missing section: {section}"


def test_database_skill_mentions_uuid():
    """Skill mentions UUID primary keys."""
    content = _read_skill().lower()
    assert "uuid" in content


def test_database_skill_mentions_rls():
    """Skill mentions Row Level Security."""
    content = _read_skill().lower()
    assert "row level security" in content or "rls" in content


def test_database_skill_mentions_drizzle():
    """Skill mentions Drizzle ORM patterns."""
    content = _read_skill().lower()
    assert "drizzle" in content


def test_database_skill_mentions_migrations():
    """Skill covers migration patterns."""
    content = _read_skill().lower()
    assert "migration" in content
    assert "reversible" in content or "down migration" in content


def test_database_skill_mentions_n_plus_one():
    """Skill covers N+1 query prevention."""
    content = _read_skill().lower()
    assert "n+1" in content or "n plus 1" in content


def test_database_skill_mentions_rls_default_deny():
    """Skill mentions RLS default-deny pattern."""
    content = _read_skill().lower()
    assert "default deny" in content


# ---------------------------------------------------------------------------
# Skill injection tests
# ---------------------------------------------------------------------------


def _make_phase_and_task(title, description=""):
    phase = Phase.new("Phase 1: Setup", "Set up the project")
    task = Task.new(title, description, phase.id)
    return phase, task


def test_build_task_prompt_injects_db_skill(tmp_path, monkeypatch):
    """build_task_prompt() includes database skill for DB tasks."""
    # Create minimal project files
    (tmp_path / "VISION.md").write_text("Test project vision")
    (tmp_path / "ARCHITECTURE.md").write_text("Test arch")
    (tmp_path / "CLAUDE.md").write_text("Test standards")

    # Ensure memory dir exists
    (tmp_path / ".forge" / "memory").mkdir(parents=True, exist_ok=True)

    phase, task = _make_phase_and_task(
        "Set up Supabase schema with user tables",
        "Create database schema and migrations for the users table",
    )

    from forge.orchestrator import build_task_prompt

    prompt = build_task_prompt(tmp_path, phase, task)
    # Database skill content should be injected
    assert "uuid" in prompt.lower() or "UUID" in prompt
    assert "row level security" in prompt.lower() or "RLS" in prompt


def test_build_task_prompt_no_db_skill_for_frontend(tmp_path, monkeypatch):
    """Database skill not injected for purely frontend tasks."""
    (tmp_path / "VISION.md").write_text("Test project vision")
    (tmp_path / "ARCHITECTURE.md").write_text("Test arch")
    (tmp_path / "CLAUDE.md").write_text("Test standards")
    (tmp_path / ".forge" / "memory").mkdir(parents=True, exist_ok=True)

    phase, task = _make_phase_and_task(
        "Build navigation component with Tailwind",
        "Create responsive navbar with mobile hamburger menu",
    )

    from forge.orchestrator import build_task_prompt

    prompt = build_task_prompt(tmp_path, phase, task)
    # Database skill content should NOT be present
    assert "row level security" not in prompt.lower()
    assert "n+1" not in prompt.lower()


def test_both_skills_injected_for_fullstack_task(tmp_path, monkeypatch):
    """Both frontend and database skills injected when task spans both."""
    (tmp_path / "VISION.md").write_text("Test project vision")
    (tmp_path / "ARCHITECTURE.md").write_text("Test arch")
    (tmp_path / "CLAUDE.md").write_text("Test standards")
    (tmp_path / ".forge" / "memory").mkdir(parents=True, exist_ok=True)

    # Create a frontend skill to test both inject
    skills_dir = Path(__file__).resolve().parent.parent / "forge" / "skills"
    fe_skill = skills_dir / "frontend-design.md"
    fe_existed = fe_skill.exists()
    if not fe_existed:
        fe_skill.write_text("# Frontend Design Skill\nUse Tailwind for styling.\n")

    try:
        phase, task = _make_phase_and_task(
            "Build user dashboard with database queries",
            "Create React component that queries the database for user data",
        )

        from forge.orchestrator import build_task_prompt

        prompt = build_task_prompt(tmp_path, phase, task)
        # Database skill should be present
        assert "row level security" in prompt.lower() or "uuid" in prompt.lower()
        # Frontend skill should also be present (task has "component" + "react")
        assert "frontend" in prompt.lower() or "tailwind" in prompt.lower()
    finally:
        if not fe_existed:
            fe_skill.unlink()
