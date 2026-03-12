"""
Screenshot-based visual QA for Forge.

Starts a local dev server, takes a screenshot with Playwright,
and uses Claude Vision to evaluate whether the rendered output
matches what the task asked for.

Only runs for frontend tasks when Playwright is available.
Falls back gracefully when any dependency is missing.

Imports only stdlib, anthropic SDK, and forge.cost_tracker (for model
constants and TokenUsage). No other forge imports.
"""

import base64
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import anthropic

from forge.cost_tracker import MODEL_OPUS, TokenUsage

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Viewports to capture (width, height)
VIEWPORTS = [
    (1280, 800),   # desktop
    (375, 812),    # mobile (iPhone SE)
]

# How long to wait for dev server to be ready (seconds)
DEV_SERVER_TIMEOUT = 30

# Port to attempt for dev server (tries this port first)
DEFAULT_DEV_PORT = 3000

# Visual QA signals (task must have one to trigger visual QA)
VISUAL_QA_SIGNALS = [
    "component", "page", "layout", "ui", "frontend",
    "dashboard", "form", "modal", "navbar", "sidebar",
    "landing", "hero", "card", "table", "chart",
    "responsive", "mobile", "design", "style", "css",
    "tailwind", "shadcn", "animation", "button", "input",
]

VISION_SYSTEM_PROMPT = """You are a senior frontend engineer reviewing \
a UI implementation. You will be shown screenshots of a web application \
at desktop and mobile viewports.

Evaluate whether the UI correctly implements the task requirements.
Be specific about any visual issues. Focus on:
- Layout correctness (no overflow, proper spacing)
- Responsive behavior (mobile layout works)
- Visual completeness (all required elements present)
- Design quality (looks intentional, not broken)

Respond in this exact format:
PASS or FAIL
[One sentence summary of what you see]
[If FAIL: specific issue on this line]
[If FAIL: suggested fix on this line]
"""

MAX_RETRIES = 5
BACKOFF_SCHEDULE = [5, 15, 30, 60, 120]


# ---------------------------------------------------------------------------
# Signal detection
# ---------------------------------------------------------------------------

def is_visual_task(task_title: str, task_description: str) -> bool:
    """
    Return True if the task has frontend/visual signals.
    Case-insensitive check against VISUAL_QA_SIGNALS.
    """
    combined = (task_title + " " + task_description).lower()
    return any(signal in combined for signal in VISUAL_QA_SIGNALS)


# ---------------------------------------------------------------------------
# Dependency checks
# ---------------------------------------------------------------------------

def is_playwright_available() -> bool:
    """
    Return True if Playwright is installed and chromium is available.
    Runs: python -m playwright --version
    Returns False (never raises) if not available.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def is_dev_server_running(port: int = DEFAULT_DEV_PORT) -> bool:
    """
    Return True if something is listening on the given port.
    Uses socket connection attempt with 1s timeout.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            s.connect(("127.0.0.1", port))
            return True
    except (OSError, socket.error):
        return False


# ---------------------------------------------------------------------------
# Dev server management
# ---------------------------------------------------------------------------

def start_dev_server(project_dir: Path,
                     port: int = DEFAULT_DEV_PORT) -> subprocess.Popen | None:
    """
    Start the project's dev server (npm run dev or equivalent).

    Detects the dev command from package.json scripts:
    - "dev" script: use it
    - "start" script: use it
    - neither: return None

    Returns the Popen process handle, or None if cannot start.
    Caller is responsible for terminating the process.
    """
    pkg_path = project_dir / "package.json"
    if not pkg_path.exists():
        return None

    try:
        pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    scripts = pkg.get("scripts", {})
    if "dev" in scripts:
        cmd = "dev"
    elif "start" in scripts:
        cmd = "start"
    else:
        return None

    try:
        env = os.environ.copy()
        env["PORT"] = str(port)
        proc = subprocess.Popen(
            ["npm", "run", cmd],
            cwd=str(project_dir),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return proc
    except Exception:
        return None


def wait_for_server(port: int = DEFAULT_DEV_PORT,
                    timeout: int = DEV_SERVER_TIMEOUT) -> bool:
    """
    Poll until the dev server is ready or timeout.

    Checks is_dev_server_running() every second.
    Returns True when server responds, False on timeout.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_dev_server_running(port):
            return True
        time.sleep(1)
    return False


# ---------------------------------------------------------------------------
# Screenshot capture
# ---------------------------------------------------------------------------

def take_screenshots(url: str,
                     output_dir: Path,
                     viewports: list[tuple[int, int]] | None = None,
                     ) -> list[Path]:
    """
    Take screenshots at each viewport using Playwright CLI.

    Uses the subprocess approach for reliability:
    python -m playwright screenshot --browser chromium
           --viewport-size WxH <url> <output_path>

    Returns list of paths to saved screenshots.
    Returns empty list if Playwright fails for any reason.
    """
    if viewports is None:
        viewports = VIEWPORTS

    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    paths = []

    for width, height in viewports:
        label = "desktop" if width > 500 else "mobile"
        filename = f"screenshot_{label}_{ts}.png"
        output_path = output_dir / filename

        try:
            result = subprocess.run(
                [
                    sys.executable, "-m", "playwright", "screenshot",
                    "--browser", "chromium",
                    "--viewport-size", f"{width},{height}",
                    url, str(output_path),
                ],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0 and output_path.exists():
                paths.append(output_path)
        except Exception:
            continue

    return paths


def encode_screenshot(path: Path) -> str:
    """
    Read a screenshot PNG and return base64-encoded string.
    Returns empty string if file cannot be read.
    """
    try:
        data = path.read_bytes()
        return base64.b64encode(data).decode("ascii")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Vision API evaluation
# ---------------------------------------------------------------------------

def _get_client() -> anthropic.Anthropic:
    """Create an Anthropic client."""
    return anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


def evaluate_visual(
    task_title: str,
    task_description: str,
    screenshot_paths: list[Path],
    model: str = MODEL_OPUS,
) -> tuple[bool, str, TokenUsage]:
    """
    Send screenshots to Claude Vision and evaluate visual quality.

    Builds a prompt asking Claude to evaluate whether the screenshots
    show a UI that correctly implements the task requirements.

    Returns (passed: bool, feedback: str, usage: TokenUsage).

    The feedback string:
    - On pass: brief description of what looks correct
    - On fail: specific issue + suggested fix (2-3 sentences max)

    Uses the Anthropic client directly with retry logic.
    """
    # Build content blocks
    content: list[dict] = []
    for path in screenshot_paths:
        b64 = encode_screenshot(path)
        if b64:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": b64,
                },
            })

    if not content:
        return (True, "no screenshots to evaluate", TokenUsage())

    content.append({
        "type": "text",
        "text": (
            f"Task: {task_title}\n"
            f"Requirements: {task_description}\n\n"
            f"Evaluate this UI implementation."
        ),
    })

    client = _get_client()
    last_error_str = ""

    for attempt in range(MAX_RETRIES):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=512,
                system=VISION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}],
            )

            text = response.content[0].text
            usage = TokenUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                model=model,
            )

            passed = text.strip().upper().startswith("PASS")
            return passed, text.strip(), usage

        except anthropic.AuthenticationError:
            # Fatal - don't retry
            raise
        except (
            anthropic.RateLimitError,
            anthropic.APIConnectionError,
            anthropic.APITimeoutError,
            anthropic.APIStatusError,
        ) as e:
            last_error_str = str(e)
            if attempt < MAX_RETRIES - 1:
                backoff = BACKOFF_SCHEDULE[min(attempt, len(BACKOFF_SCHEDULE) - 1)]
                time.sleep(backoff)

    return (False, f"Vision API unavailable after {MAX_RETRIES} attempts: {last_error_str}", TokenUsage())


# ---------------------------------------------------------------------------
# Full Visual QA pipeline
# ---------------------------------------------------------------------------

def run_visual_qa(
    project_dir: Path,
    task_title: str,
    task_description: str,
    port: int = DEFAULT_DEV_PORT,
) -> tuple[bool | None, str, TokenUsage]:
    """
    Run the full Visual QA pipeline for a task.

    Returns:
    - (True, feedback, usage)  - visual QA passed
    - (False, feedback, usage) - visual QA failed with feedback
    - (None, reason, empty_usage) - visual QA skipped with reason

    Never raises - all exceptions caught, return (None, error_msg, empty).
    """
    empty_usage = TokenUsage()

    try:
        # 1. Check if this is a visual task
        if not is_visual_task(task_title, task_description):
            return (None, "no frontend signals", empty_usage)

        # 2. Check Playwright availability
        if not is_playwright_available():
            return (None, "playwright not installed.\n   Run: pip install playwright && playwright install chromium", empty_usage)

        # 3. Check if dev server is already running, or start one
        server_was_running = is_dev_server_running(port)
        server_proc = None

        if not server_was_running:
            server_proc = start_dev_server(project_dir, port)
            if server_proc is None:
                return (None, "no dev server script found in package.json", empty_usage)

        screenshots_dir = project_dir / ".forge" / "screenshots"

        try:
            # 4. Wait for server
            if not server_was_running:
                if not wait_for_server(port):
                    return (None, f"dev server did not start within {DEV_SERVER_TIMEOUT}s", empty_usage)

            # 5. Take screenshots
            url = f"http://localhost:{port}"
            screenshot_paths = take_screenshots(url, screenshots_dir)

            if not screenshot_paths:
                return (None, "failed to capture screenshots", empty_usage)

            # 6. Evaluate with Vision API
            passed, feedback, usage = evaluate_visual(
                task_title, task_description, screenshot_paths,
            )

            return (passed, feedback, usage)

        finally:
            # 7. Stop dev server if we started it
            if server_proc is not None:
                try:
                    server_proc.terminate()
                    server_proc.wait(timeout=5)
                except Exception:
                    try:
                        server_proc.kill()
                    except Exception:
                        pass

            # 8. Clean up screenshots
            try:
                if screenshots_dir.exists():
                    for f in screenshots_dir.iterdir():
                        if f.suffix == ".png":
                            f.unlink(missing_ok=True)
                    # Remove dir if empty
                    try:
                        screenshots_dir.rmdir()
                    except OSError:
                        pass
            except Exception:
                pass

    except Exception as e:
        return (None, f"unexpected error: {e}", TokenUsage())
