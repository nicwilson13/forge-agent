"""
End-to-end test generator for Forge.

After each phase completes, analyzes what was built and generates
Playwright TypeScript E2E tests covering the user-facing flows.

Tests are written to: tests/e2e/phase-N-<slug>.spec.ts
They run via: npx playwright test tests/e2e/phase-N-<slug>.spec.ts

Generation uses Claude (Opus) to write realistic Playwright tests
based on the phase description, completed tasks, and ARCHITECTURE.md.

Only runs when:
1. Playwright is available (reuses visual_qa.is_playwright_available)
2. The phase has user-facing frontend tasks (signal detection)
3. A dev server can be started

Imports: stdlib, anthropic SDK, forge.cost_tracker (model constants),
forge.visual_qa (Playwright/dev server utilities).
"""

import os
import re
import subprocess
import time
from pathlib import Path

import anthropic

from forge.cost_tracker import MODEL_OPUS, TokenUsage
from forge.visual_qa import (
    is_playwright_available,
    is_dev_server_running,
    start_dev_server,
    wait_for_server,
    DEFAULT_DEV_PORT,
    DEV_SERVER_TIMEOUT,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Signals indicating a phase has user-facing flows worth E2E testing
E2E_PHASE_SIGNALS = [
    "auth", "login", "register", "signup", "onboarding",
    "checkout", "payment", "subscription", "billing",
    "dashboard", "profile", "settings", "account",
    "search", "filter", "upload", "form", "wizard",
    "api", "integration", "flow", "feature", "core",
    "frontend", "ui", "page", "route",
]

# Max number of test scenarios to generate per phase
MAX_SCENARIOS_PER_PHASE = 6

# E2E test output directory (relative to project root)
E2E_TEST_DIR = "tests/e2e"

# Playwright test runner command
PLAYWRIGHT_RUN_CMD = ["npx", "playwright", "test"]

E2E_GENERATION_SYSTEM = """You are an expert at writing Playwright end-to-end \
tests for web applications. You write realistic, maintainable tests that \
cover real user flows.

Rules:
- Use TypeScript
- Use @playwright/test imports
- Use page.goto(), page.fill(), page.click(), expect(page)
- Use data-testid attributes where possible, fall back to role selectors
- Each test is independent (no shared state between tests)
- Maximum {max_scenarios} test scenarios
- Tests should be realistic - test what a real user would do
- Include both happy path and one error case per major flow
- Use descriptive test names: "User can complete checkout flow"

Output ONLY the TypeScript test file content. No explanation, no markdown \
fences. Start with: import {{ test, expect }} from '@playwright/test';
"""

MAX_RETRIES = 5
BACKOFF_SCHEDULE = [5, 15, 30, 60, 120]


# ---------------------------------------------------------------------------
# Signal detection
# ---------------------------------------------------------------------------

def should_generate_e2e(phase_title: str, tasks: list) -> bool:
    """
    Return True if this phase warrants E2E test generation.

    Checks phase title and all task titles against E2E_PHASE_SIGNALS.
    Returns True if any signal matches.
    """
    combined = phase_title.lower()
    for task in tasks:
        title = getattr(task, "title", str(task))
        combined += " " + title.lower()

    return any(signal in combined for signal in E2E_PHASE_SIGNALS)


# ---------------------------------------------------------------------------
# Slug generation
# ---------------------------------------------------------------------------

def _make_e2e_slug(phase_title: str) -> str:
    """
    Convert phase title to a filename-safe slug.
    'Core Features' -> 'core-features'
    'Phase 2: Auth & Security' -> 'phase-2-auth-security'
    """
    slug = phase_title.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug.strip())
    slug = re.sub(r"-+", "-", slug)
    return slug


# ---------------------------------------------------------------------------
# Architecture reading
# ---------------------------------------------------------------------------

def _read_architecture(project_dir: Path, max_chars: int = 2000) -> str:
    """Read ARCHITECTURE.md, truncated to max_chars."""
    arch_path = project_dir / "ARCHITECTURE.md"
    if not arch_path.exists():
        return "(No ARCHITECTURE.md found)"
    try:
        content = arch_path.read_text(encoding="utf-8", errors="replace")
        return content[:max_chars]
    except Exception:
        return "(Could not read ARCHITECTURE.md)"


# ---------------------------------------------------------------------------
# E2E test generation
# ---------------------------------------------------------------------------

def _get_client() -> anthropic.Anthropic:
    """Create an Anthropic client."""
    return anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


def generate_e2e_tests(
    project_dir: Path,
    phase,
    architecture: str = "",
    model: str = MODEL_OPUS,
) -> tuple[Path | None, str]:
    """
    Generate a Playwright E2E test file for the completed phase.

    Uses Claude to write realistic tests based on:
    - phase.title and phase.description
    - Completed task titles and descriptions
    - ARCHITECTURE.md content (truncated to 2000 chars)

    Returns (test_file_path, summary_of_scenarios).
    Returns (None, error_reason) if generation fails.
    """
    if not architecture:
        architecture = _read_architecture(project_dir)

    # Build task summary
    task_lines = []
    for t in phase.tasks:
        status = getattr(t, "status", "unknown")
        if str(status) in ("done", "TaskStatus.DONE"):
            task_lines.append(f"- {t.title}: {getattr(t, 'description', '')[:200]}")

    if not task_lines:
        return (None, "no completed tasks in phase")

    task_summary = "\n".join(task_lines)

    system = E2E_GENERATION_SYSTEM.format(max_scenarios=MAX_SCENARIOS_PER_PHASE)
    user_prompt = f"""Phase: {phase.title}
Description: {phase.description}

Completed tasks:
{task_summary}

Architecture:
{architecture[:2000]}

Generate Playwright E2E tests for the user-facing flows in this phase.
"""

    client = _get_client()
    last_error = ""

    for attempt in range(MAX_RETRIES):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": user_prompt}],
            )
            content = response.content[0].text.strip()
            break
        except anthropic.AuthenticationError:
            return (None, "API authentication failed")
        except (
            anthropic.RateLimitError,
            anthropic.APIConnectionError,
            anthropic.APITimeoutError,
            anthropic.APIStatusError,
        ) as e:
            last_error = str(e)
            if attempt < MAX_RETRIES - 1:
                backoff = BACKOFF_SCHEDULE[min(attempt, len(BACKOFF_SCHEDULE) - 1)]
                time.sleep(backoff)
    else:
        return (None, f"API unavailable after {MAX_RETRIES} attempts: {last_error}")

    # Strip markdown fences if present
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content
    if content.endswith("```"):
        content = content.rsplit("```", 1)[0]
    content = content.strip()

    # Validate output looks like a TypeScript test file
    if "import" not in content or "test(" not in content:
        return (None, "generation produced invalid output")

    # Count test scenarios for summary
    scenario_count = content.count("test(")
    scenario_names = re.findall(r"test\(['\"](.+?)['\"]", content)
    summary = f"{scenario_count} test scenario(s)"
    if scenario_names:
        summary += ": " + "; ".join(scenario_names[:4])
        if len(scenario_names) > 4:
            summary += f" (+{len(scenario_names) - 4} more)"

    # Write test file
    phase_index = getattr(phase, "id", "0")
    slug = _make_e2e_slug(phase.title)
    test_dir = project_dir / E2E_TEST_DIR
    test_dir.mkdir(parents=True, exist_ok=True)
    test_file = test_dir / f"phase-{phase_index}-{slug}.spec.ts"

    try:
        test_file.write_text(content, encoding="utf-8")
    except Exception as e:
        return (None, f"failed to write test file: {e}")

    return (test_file, summary)


# ---------------------------------------------------------------------------
# Playwright output parsing
# ---------------------------------------------------------------------------

def parse_playwright_output(output: str) -> tuple[int, int, list[str]]:
    """
    Parse Playwright test runner output.

    Returns (passed_count, failed_count, failed_test_names).

    Handles multiple Playwright output formats:
    - "4 passed (12s)" or "4 passed"
    - "1 failed"
    - Checkmark/cross lines for individual tests
    """
    passed = 0
    failed = 0
    failed_names: list[str] = []

    # Match "N passed" with optional timing
    passed_match = re.search(r"(\d+)\s+passed", output)
    if passed_match:
        passed = int(passed_match.group(1))

    # Match "N failed" with optional timing
    failed_match = re.search(r"(\d+)\s+failed", output)
    if failed_match:
        failed = int(failed_match.group(1))

    # Extract failed test names from cross-mark lines
    # Playwright uses various markers: ✗, ✘, ×, [FAIL], or lines with "failed"
    for line in output.splitlines():
        line = line.strip()
        # Match lines like "✗ User can register" or "✘ Test name"
        cross_match = re.match(r"[\u2717\u2718\u00d7✗✘×]\s+(.+?)(?:\s+\(\d+[ms]+\))?$", line)
        if cross_match:
            failed_names.append(cross_match.group(1).strip())
            continue
        # Match lines like "[FAIL] Test name"
        fail_match = re.match(r"\[FAIL\]\s+(.+?)(?:\s+\(\d+[ms]+\))?$", line, re.IGNORECASE)
        if fail_match:
            failed_names.append(fail_match.group(1).strip())
            continue
        # Playwright v1.40+ format: "  ✗  1 [chromium] > test name"
        new_format = re.match(r"[\u2717\u2718\u00d7✗✘×]\s+\d+\s+\[.+?\]\s+[>›]\s+(.+?)(?:\s+\(\d+[ms]+\))?$", line)
        if new_format:
            failed_names.append(new_format.group(1).strip())

    return (passed, failed, failed_names)


# ---------------------------------------------------------------------------
# E2E test execution
# ---------------------------------------------------------------------------

def run_e2e_tests(
    project_dir: Path,
    test_file: Path,
    port: int = DEFAULT_DEV_PORT,
) -> tuple[bool, str, list[str]]:
    """
    Run the generated E2E test file.

    Starts a dev server if one is not already running.
    Runs: npx playwright test <test_file>
    Returns (passed: bool, output: str, failed_tests: list[str]).

    Never raises - all exceptions caught.
    """
    try:
        server_was_running = is_dev_server_running(port)
    except Exception:
        server_was_running = False
    server_proc = None

    try:
        if not server_was_running:
            server_proc = start_dev_server(project_dir, port)
            if server_proc is None:
                return (False, "no dev server script found in package.json", [])
            if not wait_for_server(port):
                return (False, f"dev server did not start within {DEV_SERVER_TIMEOUT}s", [])

        # Run Playwright tests
        env = os.environ.copy()
        env["BASE_URL"] = f"http://localhost:{port}"

        try:
            result = subprocess.run(
                PLAYWRIGHT_RUN_CMD + [str(test_file)],
                cwd=str(project_dir),
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )
            output = result.stdout + "\n" + result.stderr
            passed_count, failed_count, failed_names = parse_playwright_output(output)
            all_passed = result.returncode == 0 and failed_count == 0

            return (all_passed, output, failed_names)
        except subprocess.TimeoutExpired:
            return (False, "E2E tests timed out after 120s", [])
        except Exception as e:
            return (False, f"E2E test execution error: {e}", [])

    except Exception as e:
        return (False, f"unexpected error: {e}", [])
    finally:
        if server_proc is not None:
            try:
                server_proc.terminate()
                server_proc.wait(timeout=5)
            except Exception:
                try:
                    server_proc.kill()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Failure context for Claude Code
# ---------------------------------------------------------------------------

def e2e_failure_context(
    test_file: Path,
    failed_tests: list[str],
    output: str,
) -> str:
    """
    Build a context string for Claude Code to fix E2E failures.

    Returns a string containing:
    - The failing test names
    - The relevant error output (truncated to 1000 chars)
    - The test file content
    - Instructions for the fix
    """
    parts = ["E2E Test Failures:"]

    if failed_tests:
        parts.append("Failed tests:")
        for name in failed_tests[:5]:
            parts.append(f"  - {name}")

    parts.append(f"\nTest output (truncated):\n{output[-1000:]}")

    try:
        test_content = test_file.read_text(encoding="utf-8")
        parts.append(f"\nTest file ({test_file.name}):\n{test_content[:2000]}")
    except Exception:
        parts.append(f"\n(Could not read test file: {test_file})")

    parts.append(
        "\nFix the application code so these E2E tests pass. "
        "Do NOT modify the test file - fix the implementation instead."
    )

    return "\n".join(parts)
