"""
Builder Agent
Executes tasks by calling the `claude` CLI (Claude Code) in non-interactive mode.
Captures output, runs tests, and returns results.
"""

import subprocess
import shutil
import sys
import time
from pathlib import Path
from typing import Tuple


# Max time in seconds for a single Claude Code task run
CLAUDE_TIMEOUT = 600   # 10 minutes


def _check_claude_available():
    if not shutil.which("claude"):
        print("\n[forge] ERROR: `claude` CLI not found.")
        print("Install Claude Code: https://docs.anthropic.com/en/docs/claude-code")
        sys.exit(1)


def run_task(project_dir: Path, prompt: str) -> Tuple[bool, str, str]:
    """
    Run a single task via `claude -p <prompt>` in the project directory.
    Returns (success, stdout, stderr).
    """
    _check_claude_available()

    cmd = ["claude", "--print", prompt]

    print(f"\n  [builder] Invoking Claude Code...")
    try:
        result = subprocess.run(
            cmd,
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT,
        )
        success = result.returncode == 0
        return success, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", f"Claude Code timed out after {CLAUDE_TIMEOUT}s"
    except Exception as e:
        return False, "", str(e)


def run_tests(project_dir: Path) -> Tuple[bool, str, str]:
    """
    Auto-detect and run the project's test suite.
    Returns (passed, stdout, stderr).
    """
    test_cmd = _detect_test_command(project_dir)
    if not test_cmd:
        print("  [builder] No test runner detected, skipping tests.")
        return True, "No test runner found - skipped", ""

    print(f"  [builder] Running tests: {' '.join(test_cmd)}")
    try:
        result = subprocess.run(
            test_cmd,
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=300,
        )
        passed = result.returncode == 0
        return passed, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "Test suite timed out after 300s"
    except Exception as e:
        return False, "", str(e)


def _detect_test_command(project_dir: Path) -> list:
    """Detect the appropriate test command for this project."""
    p = project_dir

    # JavaScript / TypeScript
    pkg = p / "package.json"
    if pkg.exists():
        import json
        try:
            data = json.loads(pkg.read_text())
            scripts = data.get("scripts", {})
            if "test" in scripts:
                mgr = "pnpm" if (p / "pnpm-lock.yaml").exists() else \
                      "yarn" if (p / "yarn.lock").exists() else "npm"
                return [mgr, "test", "--", "--passWithNoTests"]
        except Exception:
            pass

    # Python
    if (p / "pytest.ini").exists() or (p / "pyproject.toml").exists() or \
       (p / "setup.py").exists() or list(p.glob("**/test_*.py")):
        return ["python", "-m", "pytest", "--tb=short", "-q"]

    # Rust
    if (p / "Cargo.toml").exists():
        return ["cargo", "test"]

    # Go
    if (p / "go.mod").exists():
        return ["go", "test", "./..."]

    return []


def run_build(project_dir: Path) -> Tuple[bool, str, str]:
    """Run the project build if a build command is detectable."""
    build_cmd = _detect_build_command(project_dir)
    if not build_cmd:
        return True, "No build command detected - skipped", ""

    print(f"  [builder] Running build: {' '.join(build_cmd)}")
    try:
        result = subprocess.run(
            build_cmd,
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=300,
        )
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "Build timed out"
    except Exception as e:
        return False, "", str(e)


def _detect_build_command(project_dir: Path) -> list:
    p = project_dir
    pkg = p / "package.json"
    if pkg.exists():
        import json
        try:
            data = json.loads(pkg.read_text())
            scripts = data.get("scripts", {})
            if "build" in scripts:
                mgr = "pnpm" if (p / "pnpm-lock.yaml").exists() else \
                      "yarn" if (p / "yarn.lock").exists() else "npm"
                return [mgr, "run", "build"]
        except Exception:
            pass
    if (p / "Cargo.toml").exists():
        return ["cargo", "build"]
    if (p / "go.mod").exists():
        return ["go", "build", "./..."]
    return []
