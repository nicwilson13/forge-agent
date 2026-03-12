"""Tests for the payments skill pack and its injection into task prompts."""

from pathlib import Path

import pytest

from forge.state import Phase, Task


SKILL_PATH = Path(__file__).resolve().parent.parent / "forge" / "skills" / "payments.md"


def _read_skill():
    return SKILL_PATH.read_text()


# ---------------------------------------------------------------------------
# Skill file content tests
# ---------------------------------------------------------------------------


def test_payments_skill_file_exists():
    """forge/skills/payments.md exists."""
    assert SKILL_PATH.exists(), "payments.md not found"


def test_payments_skill_has_required_sections():
    """All 10 sections present."""
    content = _read_skill().lower()
    sections = [
        "stripe architecture",
        "products and prices",
        "checkout",
        "webhook",
        "subscription management",
        "customer portal",
        "refund",
        "pci",
        "testing stripe",
        "anti-pattern",
    ]
    for section in sections:
        assert section in content, f"Missing section: {section}"


def test_payments_skill_mentions_idempotency():
    """Skill mentions idempotency keys."""
    content = _read_skill().lower()
    assert "idempotency" in content


def test_payments_skill_mentions_webhook_verification():
    """Mentions constructEvent() for webhook verification."""
    content = _read_skill()
    assert "constructEvent" in content


def test_payments_skill_mentions_client_reference_id():
    """Skill mentions client_reference_id for reconciliation."""
    content = _read_skill()
    assert "client_reference_id" in content


def test_payments_skill_mentions_pci():
    """Skill covers PCI compliance."""
    content = _read_skill().lower()
    assert "pci" in content
    assert "saq a" in content or "saq-a" in content


def test_payments_skill_mentions_test_cards():
    """Skill includes Stripe test card numbers."""
    content = _read_skill()
    assert "4242 4242 4242 4242" in content


def test_payments_skill_mentions_customer_portal():
    """Skill covers Stripe Customer Portal."""
    content = _read_skill().lower()
    assert "customer portal" in content


def test_payments_skill_mentions_dunning():
    """Skill covers dunning for failed payments."""
    content = _read_skill().lower()
    assert "dunning" in content


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


def test_build_task_prompt_injects_payments_skill(tmp_path, monkeypatch):
    """build_task_prompt() includes payments skill for payment tasks."""
    _setup_project(tmp_path)

    phase, task = _make_phase_and_task(
        "Implement Stripe subscription checkout with trial period",
        "Set up Stripe billing with monthly and annual plans",
    )

    from forge.orchestrator import build_task_prompt

    prompt = build_task_prompt(tmp_path, phase, task)
    assert "idempotency" in prompt.lower()
    assert "constructevent" in prompt.lower()


def test_build_task_prompt_no_payments_skill_for_ui(tmp_path, monkeypatch):
    """Payments skill not injected for purely UI tasks."""
    _setup_project(tmp_path)

    phase, task = _make_phase_and_task(
        "Add dark mode toggle to settings page",
        "Create a toggle component that switches between light and dark themes",
    )

    from forge.orchestrator import build_task_prompt

    prompt = build_task_prompt(tmp_path, phase, task)
    assert "idempotency" not in prompt.lower()
    assert "constructevent" not in prompt.lower()


def test_four_skills_can_inject_simultaneously(tmp_path, monkeypatch):
    """Frontend, database, auth, and payments skills all inject when task spans all."""
    _setup_project(tmp_path)

    # Ensure frontend skill exists for the test
    skills_dir = Path(__file__).resolve().parent.parent / "forge" / "skills"
    fe_skill = skills_dir / "frontend-design.md"
    fe_existed = fe_skill.exists()
    if not fe_existed:
        fe_skill.write_text("# Frontend Design Skill\nUse Tailwind for styling.\n")

    try:
        phase, task = _make_phase_and_task(
            "Build subscription checkout page with login and database",
            "Create React component for Stripe payment with auth login and database queries",
        )

        from forge.orchestrator import build_task_prompt

        prompt = build_task_prompt(tmp_path, phase, task)
        # Payments skill (subscription, payment, stripe)
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
