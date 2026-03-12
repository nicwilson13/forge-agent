"""Tests for the auth skill pack and its injection into task prompts."""

from pathlib import Path

import pytest

from forge.state import Phase, Task


SKILL_PATH = Path(__file__).resolve().parent.parent / "forge" / "skills" / "auth.md"


def _read_skill():
    return SKILL_PATH.read_text()


# ---------------------------------------------------------------------------
# Skill file content tests
# ---------------------------------------------------------------------------


def test_auth_skill_file_exists():
    """forge/skills/auth.md exists."""
    assert SKILL_PATH.exists(), "auth.md not found"


def test_auth_skill_has_required_sections():
    """All 10 sections present."""
    content = _read_skill().lower()
    sections = [
        "auth architecture",
        "session",
        "token management",
        "password",
        "supabase auth",
        "nextauth",
        "authorization",
        "attack vector",
        "environment variable",
        "testing auth",
        "anti-pattern",
    ]
    for section in sections:
        assert section in content, f"Missing section: {section}"


def test_auth_skill_mentions_httponly():
    """Skill mentions HttpOnly cookies."""
    content = _read_skill().lower()
    assert "httponly" in content


def test_auth_skill_mentions_localstorage_danger():
    """Skill warns against localStorage for tokens."""
    content = _read_skill().lower()
    assert "localstorage" in content
    # Should warn against it, not recommend it
    assert "never" in content and "localstorage" in content


def test_auth_skill_mentions_supabase_getuser():
    """Mentions getUser() over getSession() for server-side."""
    content = _read_skill()
    assert "getUser()" in content
    assert "getSession()" in content


def test_auth_skill_mentions_rls():
    """Skill mentions RLS for data-level auth."""
    content = _read_skill().lower()
    assert "rls" in content


def test_auth_skill_mentions_rate_limiting():
    """Skill covers rate limiting for auth endpoints."""
    content = _read_skill().lower()
    assert "rate limit" in content


def test_auth_skill_mentions_csrf():
    """Skill covers CSRF protection."""
    content = _read_skill().lower()
    assert "csrf" in content


def test_auth_skill_mentions_rbac():
    """Skill covers RBAC."""
    content = _read_skill().lower()
    assert "rbac" in content


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


def test_build_task_prompt_injects_auth_skill(tmp_path, monkeypatch):
    """build_task_prompt() includes auth skill for auth tasks."""
    _setup_project(tmp_path)

    phase, task = _make_phase_and_task(
        "Implement Supabase Auth with email login",
        "Set up authentication with JWT refresh tokens",
    )

    from forge.orchestrator import build_task_prompt

    prompt = build_task_prompt(tmp_path, phase, task)
    assert "httponly" in prompt.lower()
    assert "getuser" in prompt.lower()


def test_build_task_prompt_no_auth_skill_for_ui(tmp_path, monkeypatch):
    """Auth skill not injected for purely UI tasks."""
    _setup_project(tmp_path)

    phase, task = _make_phase_and_task(
        "Build hero section with animated gradient background",
        "Create a visually stunning landing page hero with Tailwind CSS",
    )

    from forge.orchestrator import build_task_prompt

    prompt = build_task_prompt(tmp_path, phase, task)
    assert "httponly" not in prompt.lower()
    assert "getuser" not in prompt.lower()


def test_all_three_skills_can_inject_simultaneously(tmp_path, monkeypatch):
    """Frontend, database, and auth skills all inject for a task that spans all three."""
    _setup_project(tmp_path)

    # Ensure frontend skill exists for the test
    skills_dir = Path(__file__).resolve().parent.parent / "forge" / "skills"
    fe_skill = skills_dir / "frontend-design.md"
    fe_existed = fe_skill.exists()
    if not fe_existed:
        fe_skill.write_text("# Frontend Design Skill\nUse Tailwind for styling.\n")

    try:
        phase, task = _make_phase_and_task(
            "Build login form with database session storage",
            "Create React component for auth login with Supabase database queries",
        )

        from forge.orchestrator import build_task_prompt

        prompt = build_task_prompt(tmp_path, phase, task)
        # Auth skill present (login, auth)
        assert "httponly" in prompt.lower()
        # Database skill present (database)
        assert "uuid" in prompt.lower() or "n+1" in prompt.lower()
        # Frontend skill present (react, component)
        assert "frontend" in prompt.lower() or "tailwind" in prompt.lower()
    finally:
        if not fe_existed:
            fe_skill.unlink()
