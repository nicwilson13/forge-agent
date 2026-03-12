"""Tests for the deploy skill pack and its injection into task prompts."""

from pathlib import Path

import pytest

from forge.state import Phase, Task


SKILL_PATH = Path(__file__).resolve().parent.parent / "forge" / "skills" / "deploy.md"


def _read_skill():
    return SKILL_PATH.read_text()


# ---------------------------------------------------------------------------
# Skill file content tests
# ---------------------------------------------------------------------------


def test_deploy_skill_file_exists():
    """forge/skills/deploy.md exists."""
    assert SKILL_PATH.exists(), "deploy.md not found"


def test_deploy_skill_has_required_sections():
    """All 10 sections present."""
    content = _read_skill().lower()
    sections = [
        "vercel deployment",
        "environment variable",
        "build optimization",
        "vercel-specific",
        "preview deployment",
        "ci/cd",
        "domain",
        "monitoring",
        "rollback",
        "anti-pattern",
    ]
    for section in sections:
        assert section in content, f"Missing section: {section}"


def test_deploy_skill_mentions_env_vars():
    """Covers NEXT_PUBLIC_ convention and server-only vars."""
    content = _read_skill()
    assert "NEXT_PUBLIC_" in content
    assert "server-only" in content.lower() or "server only" in content.lower()


def test_deploy_skill_mentions_preview_deployments():
    """Skill covers preview deployments."""
    content = _read_skill().lower()
    assert "preview" in content
    assert "pr" in content


def test_deploy_skill_mentions_max_duration():
    """Mentions maxDuration for API route timeouts."""
    content = _read_skill()
    assert "maxDuration" in content


def test_deploy_skill_mentions_frozen_lockfile():
    """CI uses frozen-lockfile / --ci flag."""
    content = _read_skill()
    assert "frozen-lockfile" in content or "--ci" in content


def test_deploy_skill_mentions_rollback():
    """Skill covers rollback strategy."""
    content = _read_skill().lower()
    assert "rollback" in content


def test_deploy_skill_mentions_health_check():
    """Skill covers health check endpoint."""
    content = _read_skill().lower()
    assert "health check" in content or "/api/health" in content


def test_deploy_skill_mentions_sentry():
    """Skill covers Sentry for error tracking."""
    content = _read_skill().lower()
    assert "sentry" in content


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


def test_build_task_prompt_injects_deploy_skill(tmp_path, monkeypatch):
    """build_task_prompt() includes deploy skill for deploy tasks."""
    _setup_project(tmp_path)

    phase, task = _make_phase_and_task(
        "Configure Vercel deployment with environment variables",
        "Set up Vercel project with preview builds and production deploy",
    )

    from forge.orchestrator import build_task_prompt

    prompt = build_task_prompt(tmp_path, phase, task)
    assert "maxduration" in prompt.lower()
    assert "rollback" in prompt.lower()


def test_build_task_prompt_no_deploy_skill_for_ui(tmp_path, monkeypatch):
    """Deploy skill not injected for purely UI tasks."""
    _setup_project(tmp_path)

    phase, task = _make_phase_and_task(
        "Style the homepage hero section",
        "Create a visually stunning hero with gradient background and animations",
    )

    from forge.orchestrator import build_task_prompt

    prompt = build_task_prompt(tmp_path, phase, task)
    assert "maxduration" not in prompt.lower()
    assert "frozen-lockfile" not in prompt.lower()


def test_five_skills_can_inject_simultaneously(tmp_path, monkeypatch):
    """All five skills inject for a task spanning all domains."""
    _setup_project(tmp_path)

    # Ensure frontend skill exists for the test
    skills_dir = Path(__file__).resolve().parent.parent / "forge" / "skills"
    fe_skill = skills_dir / "frontend-design.md"
    fe_existed = fe_skill.exists()
    if not fe_existed:
        fe_skill.write_text("# Frontend Design Skill\nUse Tailwind for styling.\n")

    try:
        phase, task = _make_phase_and_task(
            "Deploy subscription checkout with auth to Vercel production",
            "Deploy React component with Stripe payment, login auth, database queries to Vercel",
        )

        from forge.orchestrator import build_task_prompt

        prompt = build_task_prompt(tmp_path, phase, task)
        # Deploy skill (deploy, vercel, production)
        assert "maxduration" in prompt.lower()
        # Payments skill (stripe, payment, subscription, checkout)
        assert "idempotency" in prompt.lower()
        # Auth skill (auth, login)
        assert "httponly" in prompt.lower()
        # Database skill (database)
        assert "uuid" in prompt.lower() or "n+1" in prompt.lower()
        # Frontend skill (react, component)
        assert "frontend" in prompt.lower() or "tailwind" in prompt.lower()
    finally:
        if not fe_existed:
            fe_skill.unlink()
