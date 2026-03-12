"""
GitHub Actions workflow generator for Forge.

Generates a CI workflow tailored to the project's detected stack.
Called during `forge new` after the interview completes.

Detects:
- Package manager: pnpm (preferred), npm, yarn
- Test runner: vitest, jest, pytest
- TypeScript: presence of tsconfig.json
- Playwright: presence in devDependencies

Generates .github/workflows/ci.yml with jobs appropriate for the stack.

This module imports only stdlib + pyyaml. No forge module imports.
"""

import json
from pathlib import Path


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def detect_package_manager(project_dir: Path) -> str:
    """
    Detect the package manager in use.

    Checks for: pnpm-lock.yaml -> 'pnpm'
                yarn.lock      -> 'yarn'
                package-lock.json -> 'npm'
                (no lock file)  -> 'pnpm'  (default for new projects)
    """
    if (project_dir / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (project_dir / "yarn.lock").exists():
        return "yarn"
    if (project_dir / "package-lock.json").exists():
        return "npm"
    return "pnpm"


def detect_test_runner(project_dir: Path) -> str:
    """
    Detect the test runner from package.json scripts or devDependencies.

    Checks: 'vitest' in devDependencies -> 'vitest'
            'jest' in devDependencies   -> 'jest'
            'pytest' in requirements.txt -> 'pytest'
            fallback                     -> 'vitest'
    """
    try:
        pkg_path = project_dir / "package.json"
        if pkg_path.exists():
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
            dev_deps = pkg.get("devDependencies", {})
            deps = pkg.get("dependencies", {})
            all_deps = {**deps, **dev_deps}
            if "vitest" in all_deps:
                return "vitest"
            if "jest" in all_deps:
                return "jest"
    except Exception:
        pass

    try:
        req_path = project_dir / "requirements.txt"
        if req_path.exists():
            content = req_path.read_text(encoding="utf-8").lower()
            if "pytest" in content:
                return "pytest"
    except Exception:
        pass

    return "vitest"


def detect_has_typescript(project_dir: Path) -> bool:
    """Return True if tsconfig.json exists in project root."""
    return (project_dir / "tsconfig.json").exists()


def detect_has_playwright(project_dir: Path) -> bool:
    """
    Return True if @playwright/test is in package.json devDependencies
    or if tests/e2e/ directory exists.
    """
    if (project_dir / "tests" / "e2e").is_dir():
        return True
    try:
        pkg_path = project_dir / "package.json"
        if pkg_path.exists():
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
            dev_deps = pkg.get("devDependencies", {})
            if "@playwright/test" in dev_deps:
                return True
    except Exception:
        pass
    return False


def detect_stack(project_dir: Path) -> dict:
    """
    Return a dict summarizing detected stack.

    Never raises - returns sensible defaults on any error.
    """
    try:
        return {
            "package_manager": detect_package_manager(project_dir),
            "test_runner": detect_test_runner(project_dir),
            "has_typescript": detect_has_typescript(project_dir),
            "has_playwright": detect_has_playwright(project_dir),
            "node_version": "20",
            "pnpm_version": "9",
        }
    except Exception:
        return {
            "package_manager": "pnpm",
            "test_runner": "vitest",
            "has_typescript": False,
            "has_playwright": False,
            "node_version": "20",
            "pnpm_version": "9",
        }


# ---------------------------------------------------------------------------
# Workflow generation
# ---------------------------------------------------------------------------

INSTALL_FLAGS = {
    "pnpm": "--frozen-lockfile",
    "npm": "--ci",
    "yarn": "--frozen-lockfile",
}


def _setup_steps(stack: dict) -> str:
    """Generate the checkout + package manager setup steps as YAML."""
    pm = stack["package_manager"]
    node_ver = stack["node_version"]
    lines = []
    lines.append("      - uses: actions/checkout@v4")

    if pm == "pnpm":
        pnpm_ver = stack.get("pnpm_version", "9")
        lines.append("      - uses: pnpm/action-setup@v4")
        lines.append("        with:")
        lines.append(f"          version: {pnpm_ver}")

    lines.append("      - uses: actions/setup-node@v4")
    lines.append("        with:")
    lines.append(f"          node-version: '{node_ver}'")
    lines.append(f"          cache: '{pm}'")

    install_flag = INSTALL_FLAGS.get(pm, "")
    install_cmd = f"{pm} install {install_flag}".strip()
    lines.append(f"      - run: {install_cmd}")

    return "\n".join(lines)


def generate_workflow(stack: dict, project_dir: Path | None = None) -> str:
    """
    Generate a GitHub Actions CI workflow YAML string.

    Builds the workflow based on detected stack:
    - Always includes: checkout, install, type-check (if TS), lint, test
    - Includes E2E job if has_playwright is True
    - Uses frozen-lockfile for pnpm, --ci for npm
    - Sets up correct action for the package manager

    Returns the YAML string. Does not write to disk.
    """
    pm = stack["package_manager"]
    setup = _setup_steps(stack)

    # Check job
    check_steps = setup
    if stack.get("has_typescript"):
        check_steps += f"\n      - run: {pm} type-check"
    check_steps += f"\n      - run: {pm} lint"

    # Test job
    test_steps = setup
    test_steps += f"\n      - run: {pm} test --passWithNoTests"

    lines = []
    lines.append("name: CI")
    lines.append("")
    lines.append("on:")
    lines.append("  push:")
    lines.append("    branches: [main, develop]")
    lines.append("  pull_request:")
    lines.append("    branches: [main]")
    lines.append("")
    lines.append("jobs:")
    lines.append("  check:")
    lines.append("    name: Type Check & Lint")
    lines.append("    runs-on: ubuntu-latest")
    lines.append("    steps:")
    lines.append(check_steps)
    lines.append("")
    lines.append("  test:")
    lines.append("    name: Unit Tests")
    lines.append("    runs-on: ubuntu-latest")
    lines.append("    needs: check")
    lines.append("    steps:")
    lines.append(test_steps)

    if stack.get("has_playwright"):
        e2e_steps = setup
        e2e_steps += f"\n      - run: {pm} exec playwright install --with-deps chromium"
        e2e_steps += f"\n      - run: {pm} exec playwright test tests/e2e/"

        lines.append("")
        lines.append("  e2e:")
        lines.append("    name: E2E Tests")
        lines.append("    runs-on: ubuntu-latest")
        lines.append("    needs: test")
        lines.append("    if: ${{ hashFiles('tests/e2e/**/*.spec.ts') != '' }}")
        lines.append("    steps:")
        lines.append(e2e_steps)

    lines.append("")
    return "\n".join(lines)


def write_workflow(project_dir: Path, workflow_yaml: str) -> Path:
    """
    Write workflow to .github/workflows/ci.yml.

    Creates .github/workflows/ directory if needed.
    Returns the path to the written file.
    Never raises.
    """
    try:
        workflow_dir = project_dir / ".github" / "workflows"
        workflow_dir.mkdir(parents=True, exist_ok=True)
        path = workflow_dir / "ci.yml"
        tmp = path.with_suffix(".yml.tmp")
        tmp.write_text(workflow_yaml, encoding="utf-8")
        tmp.replace(path)
        return path
    except Exception:
        return project_dir / ".github" / "workflows" / "ci.yml"


def generate_and_write_workflow(project_dir: Path) -> Path | None:
    """
    Detect stack, generate workflow, write to disk.

    Prints detection summary to stdout.
    Returns path to written file, or None on any error.
    Never raises.
    """
    try:
        stack = detect_stack(project_dir)

        pm = stack["package_manager"]
        runner = stack["test_runner"]
        has_ts = stack["has_typescript"]
        has_pw = stack["has_playwright"]

        print(f"\n  Detecting stack...")
        if has_ts:
            print(f"    TypeScript detected")
        print(f"    {pm} detected")
        print(f"    {runner} detected")
        if has_pw:
            print(f"    Playwright detected")

        workflow_yaml = generate_workflow(stack, project_dir)
        path = write_workflow(project_dir, workflow_yaml)

        if path.exists():
            print(f"    -> Runs on every push and PR")
            if has_ts:
                print(f"    -> type-check, lint, test")
            else:
                print(f"    -> lint, test")
            if has_pw:
                print(f"    -> Playwright E2E (conditional)")
            return path
        return None
    except Exception:
        return None
