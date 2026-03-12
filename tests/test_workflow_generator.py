"""Tests for forge.workflow_generator module."""

import json
from pathlib import Path

import yaml

from forge.workflow_generator import (
    detect_package_manager,
    detect_test_runner,
    detect_has_typescript,
    detect_has_playwright,
    detect_stack,
    generate_workflow,
    write_workflow,
    generate_and_write_workflow,
)


def test_detect_package_manager_pnpm(tmp_path):
    """Detects pnpm from pnpm-lock.yaml."""
    (tmp_path / "pnpm-lock.yaml").write_text("")
    assert detect_package_manager(tmp_path) == "pnpm"


def test_detect_package_manager_npm(tmp_path):
    """Detects npm from package-lock.json."""
    (tmp_path / "package-lock.json").write_text("{}")
    assert detect_package_manager(tmp_path) == "npm"


def test_detect_package_manager_yarn(tmp_path):
    """Detects yarn from yarn.lock."""
    (tmp_path / "yarn.lock").write_text("")
    assert detect_package_manager(tmp_path) == "yarn"


def test_detect_package_manager_default(tmp_path):
    """Returns pnpm when no lock file found."""
    assert detect_package_manager(tmp_path) == "pnpm"


def test_detect_test_runner_vitest(tmp_path):
    """Detects vitest from devDependencies."""
    (tmp_path / "package.json").write_text(
        json.dumps({"devDependencies": {"vitest": "^1.0"}})
    )
    assert detect_test_runner(tmp_path) == "vitest"


def test_detect_test_runner_jest(tmp_path):
    """Detects jest from devDependencies."""
    (tmp_path / "package.json").write_text(
        json.dumps({"devDependencies": {"jest": "^29.0"}})
    )
    assert detect_test_runner(tmp_path) == "jest"


def test_detect_test_runner_pytest(tmp_path):
    """Detects pytest from requirements.txt."""
    (tmp_path / "requirements.txt").write_text("pytest>=7.0\nflask\n")
    assert detect_test_runner(tmp_path) == "pytest"


def test_detect_has_typescript_true(tmp_path):
    """Returns True when tsconfig.json exists."""
    (tmp_path / "tsconfig.json").write_text("{}")
    assert detect_has_typescript(tmp_path) is True


def test_detect_has_typescript_false(tmp_path):
    """Returns False when no tsconfig.json."""
    assert detect_has_typescript(tmp_path) is False


def test_detect_has_playwright_from_package_json(tmp_path):
    """Detects playwright from devDependencies."""
    (tmp_path / "package.json").write_text(
        json.dumps({"devDependencies": {"@playwright/test": "^1.40"}})
    )
    assert detect_has_playwright(tmp_path) is True


def test_detect_has_playwright_from_e2e_dir(tmp_path):
    """Detects playwright from tests/e2e/ directory."""
    (tmp_path / "tests" / "e2e").mkdir(parents=True)
    assert detect_has_playwright(tmp_path) is True


def test_detect_has_playwright_false(tmp_path):
    """Returns False when no playwright indicators."""
    assert detect_has_playwright(tmp_path) is False


def test_detect_stack_never_raises(tmp_path):
    """detect_stack() returns defaults on empty directory."""
    stack = detect_stack(tmp_path)
    assert "package_manager" in stack
    assert "test_runner" in stack
    assert "has_typescript" in stack
    assert "has_playwright" in stack
    assert "node_version" in stack


def test_generate_workflow_contains_required_jobs():
    """Generated YAML contains check and test jobs."""
    stack = {
        "package_manager": "pnpm",
        "test_runner": "vitest",
        "has_typescript": True,
        "has_playwright": False,
        "node_version": "20",
        "pnpm_version": "9",
    }
    result = generate_workflow(stack)
    parsed = yaml.safe_load(result)
    assert "check" in parsed["jobs"]
    assert "test" in parsed["jobs"]


def test_generate_workflow_pnpm_uses_frozen_lockfile():
    """pnpm workflow uses --frozen-lockfile."""
    stack = {
        "package_manager": "pnpm",
        "test_runner": "vitest",
        "has_typescript": False,
        "has_playwright": False,
        "node_version": "20",
        "pnpm_version": "9",
    }
    result = generate_workflow(stack)
    assert "--frozen-lockfile" in result


def test_generate_workflow_npm_uses_ci():
    """npm workflow uses --ci flag."""
    stack = {
        "package_manager": "npm",
        "test_runner": "jest",
        "has_typescript": False,
        "has_playwright": False,
        "node_version": "20",
        "pnpm_version": "9",
    }
    result = generate_workflow(stack)
    assert "--ci" in result


def test_generate_workflow_includes_e2e_when_playwright():
    """E2E job included when has_playwright is True."""
    stack = {
        "package_manager": "pnpm",
        "test_runner": "vitest",
        "has_typescript": True,
        "has_playwright": True,
        "node_version": "20",
        "pnpm_version": "9",
    }
    result = generate_workflow(stack)
    parsed = yaml.safe_load(result)
    assert "e2e" in parsed["jobs"]
    assert "playwright" in result.lower()
    assert "hashFiles" in result


def test_generate_workflow_excludes_e2e_when_no_playwright():
    """E2E job excluded when has_playwright is False."""
    stack = {
        "package_manager": "pnpm",
        "test_runner": "vitest",
        "has_typescript": True,
        "has_playwright": False,
        "node_version": "20",
        "pnpm_version": "9",
    }
    result = generate_workflow(stack)
    parsed = yaml.safe_load(result)
    assert "e2e" not in parsed["jobs"]


def test_generate_workflow_uses_v4_actions():
    """Generated workflow uses actions/checkout@v4 and actions/setup-node@v4."""
    stack = {
        "package_manager": "pnpm",
        "test_runner": "vitest",
        "has_typescript": True,
        "has_playwright": False,
        "node_version": "20",
        "pnpm_version": "9",
    }
    result = generate_workflow(stack)
    assert "actions/checkout@v4" in result
    assert "actions/setup-node@v4" in result


def test_generate_workflow_includes_type_check_when_typescript():
    """type-check step included when has_typescript is True."""
    stack = {
        "package_manager": "pnpm",
        "test_runner": "vitest",
        "has_typescript": True,
        "has_playwright": False,
        "node_version": "20",
        "pnpm_version": "9",
    }
    result = generate_workflow(stack)
    assert "type-check" in result


def test_generate_workflow_excludes_type_check_when_no_typescript():
    """type-check step excluded when has_typescript is False."""
    stack = {
        "package_manager": "pnpm",
        "test_runner": "vitest",
        "has_typescript": False,
        "has_playwright": False,
        "node_version": "20",
        "pnpm_version": "9",
    }
    result = generate_workflow(stack)
    assert "type-check" not in result


def test_generate_workflow_valid_yaml():
    """Generated workflow is valid YAML."""
    stack = {
        "package_manager": "pnpm",
        "test_runner": "vitest",
        "has_typescript": True,
        "has_playwright": True,
        "node_version": "20",
        "pnpm_version": "9",
    }
    result = generate_workflow(stack)
    parsed = yaml.safe_load(result)
    assert parsed is not None
    assert "name" in parsed
    # YAML parses bare 'on:' as boolean True key
    assert True in parsed or "on" in parsed
    assert "jobs" in parsed


def test_write_workflow_creates_directories(tmp_path):
    """Creates .github/workflows/ if not exists."""
    yaml_content = "name: CI\non: push\njobs: {}\n"
    path = write_workflow(tmp_path, yaml_content)
    assert path.exists()
    assert path.parent.name == "workflows"
    assert path.parent.parent.name == ".github"


def test_generate_and_write_workflow_never_raises(tmp_path):
    """Full pipeline never raises."""
    result = generate_and_write_workflow(tmp_path)
    # Should succeed even on empty directory (uses defaults)
    assert result is None or result.exists()
