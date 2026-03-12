"""
Builder Agent
Executes tasks by calling Claude Code via the official Python SDK (claude-code-sdk).
Streams output in real time, handles structured errors, and returns results.

Non-Claude operations (tests, builds) remain as subprocess calls.
"""

import subprocess
import shutil
import sys
import time
from pathlib import Path
from typing import Tuple

# SDK availability check - deferred to first use
_SDK_AVAILABLE: bool | None = None

# Max time in seconds for a single Claude Code task run
TASK_TIMEOUT = 600      # 10 minutes
MAX_TURNS = 50          # max back-and-forth turns in one task


def _check_sdk_available() -> None:
    """Verify the Claude Code SDK is importable. Exit with a helpful message if not."""
    global _SDK_AVAILABLE
    if _SDK_AVAILABLE is True:
        return
    try:
        import claude_code_sdk  # noqa: F401
        _SDK_AVAILABLE = True
    except ImportError:
        _SDK_AVAILABLE = False
        print("\n[forge] ERROR: claude-code-sdk is not installed.")
        print("Install it with:  pip install claude-code-sdk anyio")
        print("Then retry:       forge run")
        sys.exit(1)


async def _run_task_async(project_dir: Path, prompt: str) -> Tuple[bool, str, str]:
    """
    Execute a single task via the Claude Code SDK async query().

    Streams each message to the terminal as it arrives, accumulates
    the full output, and returns a structured result.

    Args:
        project_dir: The project directory to run in.
        prompt: The full task prompt for Claude Code.

    Returns:
        A tuple of (success, full_output, error_message).
    """
    from claude_code_sdk import (
        query, ClaudeCodeOptions,
        AssistantMessage, ResultMessage, SystemMessage,
        TextBlock, ToolUseBlock, ToolResultBlock,
        ClaudeSDKError, CLINotFoundError, CLIConnectionError, ProcessError,
    )

    options = ClaudeCodeOptions(
        cwd=str(project_dir),
        max_turns=MAX_TURNS,
    )

    output_parts: list[str] = []
    start_time = time.time()

    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text = block.text.strip()
                        if text:
                            # Print each line with prefix
                            for line in text.splitlines():
                                print(f"  [claude] {line}")
                            output_parts.append(text)
                    elif isinstance(block, ToolUseBlock):
                        tool_name = block.name
                        tool_input = block.input
                        # Show file operations clearly
                        if tool_name in ("Write", "Edit", "MultiEdit"):
                            file_path = tool_input.get("file_path", "")
                            filename = Path(file_path).name if file_path else "unknown"
                            print(f"  [claude] Writing: {filename}")
                        elif tool_name == "Read":
                            file_path = tool_input.get("file_path", "")
                            filename = Path(file_path).name if file_path else "unknown"
                            print(f"  [claude] Reading: {filename}")
                        elif tool_name == "Bash":
                            cmd = tool_input.get("command", "")
                            print(f"  [claude] Running: {cmd[:80]}")
                        else:
                            print(f"  [claude] Tool: {tool_name}")
                    elif isinstance(block, ToolResultBlock):
                        if block.is_error:
                            print(f"  [claude] Error in tool result")
                        else:
                            # Brief acknowledgment for completed tools
                            print(f"  [claude] Done.")

            elif isinstance(message, ResultMessage):
                elapsed = time.time() - start_time
                cost_str = f"${message.total_cost_usd:.4f}" if message.total_cost_usd else "n/a"
                print(f"  [builder] Finished in {elapsed:.0f}s | "
                      f"turns: {message.num_turns} | cost: {cost_str}")

                if message.is_error:
                    error_text = message.result or "Unknown error"
                    return False, "\n".join(output_parts), error_text

                if message.result:
                    output_parts.append(message.result)

            elif isinstance(message, SystemMessage):
                # System messages are metadata, log selectively
                if message.subtype == "error":
                    error_data = message.data.get("message", str(message.data))
                    print(f"  [claude] System error: {error_data}")

        full_output = "\n".join(output_parts)
        return True, full_output, ""

    except CLINotFoundError:
        return False, "", (
            "AUTH_ERROR: Claude Code CLI not found. "
            "Install it: https://docs.anthropic.com/en/docs/claude-code"
        )
    except CLIConnectionError as e:
        return False, "", f"CONNECTION_ERROR: {e}"
    except ProcessError as e:
        stderr_text = e.stderr or ""
        # Detect auth errors from stderr content
        if "auth" in stderr_text.lower() or "api key" in stderr_text.lower():
            return False, "", (
                "AUTH_ERROR: Invalid API credentials. "
                "Check your ANTHROPIC_API_KEY environment variable."
            )
        # Detect rate limits
        if "rate limit" in stderr_text.lower() or "429" in stderr_text:
            return False, "", f"RATE_LIMIT: {e}"
        return False, "", f"PROCESS_ERROR: {e}"
    except ClaudeSDKError as e:
        return False, "", f"SDK_ERROR: {e}"
    except TimeoutError:
        return False, "", f"TIMEOUT: Claude Code timed out after {TASK_TIMEOUT}s"
    except OSError as e:
        return False, "", f"CONNECTION_ERROR: {e}"


def run_task(project_dir: Path, prompt: str) -> Tuple[bool, str, str]:
    """
    Run a single task via Claude Code SDK with streaming output.

    This is the synchronous entry point that wraps the async SDK call.
    Output is streamed to the terminal in real time as Claude works.

    Args:
        project_dir: The project directory to execute in.
        prompt: The full task prompt for Claude Code.

    Returns:
        A tuple of (success, stdout, stderr) where:
        - success: True if the task completed without errors.
        - stdout: The accumulated text output from Claude.
        - stderr: Error message if failed, empty string if success.
                  Prefixed with error type (AUTH_ERROR, TIMEOUT, etc.)
                  for structured error handling by the caller.
    """
    _check_sdk_available()

    import anyio

    print(f"\n  [builder] Invoking Claude Code (SDK streaming)...")

    return anyio.run(_run_task_async, project_dir, prompt)


def run_tests(project_dir: Path) -> Tuple[bool, str, str]:
    """
    Auto-detect and run the project's test suite.

    Args:
        project_dir: The project directory containing the test suite.

    Returns:
        A tuple of (passed, stdout, stderr).
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
    except FileNotFoundError as e:
        return False, "", f"Test runner not found: {e}"
    except OSError as e:
        return False, "", str(e)


def _detect_test_command(project_dir: Path) -> list:
    """
    Detect the appropriate test command for this project.

    Args:
        project_dir: The project root directory.

    Returns:
        A list of command parts, or an empty list if no test runner is found.
    """
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
        except (json.JSONDecodeError, OSError):
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
    """
    Run the project build if a build command is detectable.

    Args:
        project_dir: The project root directory.

    Returns:
        A tuple of (success, stdout, stderr).
    """
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
    except FileNotFoundError as e:
        return False, "", f"Build tool not found: {e}"
    except OSError as e:
        return False, "", str(e)


def _detect_build_command(project_dir: Path) -> list:
    """
    Detect the appropriate build command for this project.

    Args:
        project_dir: The project root directory.

    Returns:
        A list of command parts, or an empty list if no build tool is found.
    """
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
        except (json.JSONDecodeError, OSError):
            pass
    if (p / "Cargo.toml").exists():
        return ["cargo", "build"]
    if (p / "go.mod").exists():
        return ["go", "build", "./..."]
    return []
