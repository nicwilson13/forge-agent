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


def find_claude_cli() -> str | None:
    """Locate the Claude Code CLI executable.

    Tries shutil.which() first, then platform-specific fallback paths
    for when the CLI is installed globally via npm but not on the
    current PATH (e.g. inside a Python virtualenv).

    Returns the absolute path string, or None if not found.
    """
    import os

    # 1. Standard PATH lookup
    path = shutil.which("claude")
    if path:
        return path

    # 2. On Windows, npm installs .cmd wrappers
    if sys.platform == "win32":
        path = shutil.which("claude.cmd")
        if path:
            return path

    # 3. Platform-specific fallback locations
    candidates: list[Path] = []

    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            candidates.append(Path(appdata) / "npm" / "claude.cmd")
            candidates.append(Path(appdata) / "npm" / "claude")
        progfiles = os.environ.get("PROGRAMFILES", "")
        if progfiles:
            candidates.append(Path(progfiles) / "nodejs" / "claude.cmd")
    else:
        home = Path.home()
        candidates.extend([
            home / ".npm-global" / "bin" / "claude",
            Path("/usr/local/bin/claude"),
            home / ".local" / "bin" / "claude",
            home / ".yarn" / "bin" / "claude",
        ])

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    return None


def _resolve_cli_for_transport(cli_path: str | None) -> str | None:
    """Resolve CLI path for SubprocessCLITransport compatibility.

    On Windows, .cmd wrappers may not be directly executable by
    anyio.open_process. If we find a .cmd, look for the real
    executable (claude.exe) alongside it.
    """
    if cli_path is None or sys.platform != "win32":
        return cli_path
    if not cli_path.lower().endswith(".cmd"):
        return cli_path
    import os
    cli_dir = os.path.dirname(cli_path)
    for name in ["claude.exe", "claude"]:
        candidate = os.path.join(cli_dir, name)
        if os.path.isfile(candidate) and candidate.lower() != cli_path.lower():
            return candidate
    return cli_path  # fallback to original


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


def _format_stream_line(message_type: str, content: str) -> str | None:
    """
    Format a streaming message for terminal display.

    Args:
        message_type: Type of message ('text', 'write', 'read', 'run', 'tool', 'error').
        content: The message content.

    Returns:
        Formatted line to print, or None if the message should be suppressed.
    """
    if not content or not content.strip():
        return None
    content = content.strip()
    if len(content) > 120:
        content = content[:117] + "..."
    if message_type == "text":
        return f"  [claude] {content}"
    if message_type == "write":
        return f"  [claude] Writing: {content}"
    if message_type == "read":
        return f"  [claude] Reading: {content}"
    if message_type == "run":
        return f"  [claude] Running: {content}"
    if message_type == "tool":
        return f"  [claude] Tool: {content}"
    if message_type == "error":
        return f"  [claude] Error: {content}"
    return None


async def _run_task_async(project_dir: Path, prompt: str,
                         model: str | None = None) -> Tuple[bool, str, str, float]:
    """
    Execute a single task via the Claude Code SDK async query().

    Streams each message to the terminal as it arrives, accumulates
    the full output, and returns a structured result.

    Args:
        project_dir: The project directory to run in.
        prompt: The full task prompt for Claude Code.
        model: Optional model override for Claude Code.

    Returns:
        A tuple of (success, full_output, error_message, duration_seconds).
    """
    import shutil
    from claude_code_sdk import (
        query, ClaudeCodeOptions,
        AssistantMessage, ResultMessage, SystemMessage,
        TextBlock, ToolUseBlock, ToolResultBlock,
        ClaudeSDKError, CLINotFoundError, CLIConnectionError, ProcessError,
    )
    from claude_code_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    options = ClaudeCodeOptions(
        cwd=str(project_dir),
        max_turns=MAX_TURNS,
        model=model,
    )

    # Resolve CLI path once so parallel tasks don't race on shutil.which
    cli_path = _resolve_cli_for_transport(find_claude_cli())

    output_parts: list[str] = []
    start_time = time.time()

    try:
        transport = SubprocessCLITransport(
            prompt=prompt, options=options, cli_path=cli_path
        ) if cli_path else None
        async for message in query(prompt=prompt, options=options,
                                   transport=transport):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text = block.text.strip()
                        if text:
                            for line in text.splitlines():
                                formatted = _format_stream_line("text", line)
                                if formatted:
                                    print(formatted)
                            output_parts.append(text)
                    elif isinstance(block, ToolUseBlock):
                        tool_name = block.name
                        tool_input = block.input
                        if tool_name in ("Write", "Edit", "MultiEdit"):
                            file_path = tool_input.get("file_path", "")
                            filename = Path(file_path).name if file_path else "unknown"
                            formatted = _format_stream_line("write", filename)
                        elif tool_name == "Read":
                            file_path = tool_input.get("file_path", "")
                            filename = Path(file_path).name if file_path else "unknown"
                            formatted = _format_stream_line("read", filename)
                        elif tool_name == "Bash":
                            cmd = tool_input.get("command", "")
                            formatted = _format_stream_line("run", cmd[:80])
                        else:
                            formatted = _format_stream_line("tool", tool_name)
                        if formatted:
                            print(formatted)
                    elif isinstance(block, ToolResultBlock):
                        if block.is_error:
                            formatted = _format_stream_line("error", "tool result failed")
                            if formatted:
                                print(formatted)

            elif isinstance(message, ResultMessage):
                elapsed = time.time() - start_time
                cost_str = f"${message.total_cost_usd:.4f}" if message.total_cost_usd else "n/a"
                print(f"  [builder] Finished in {elapsed:.0f}s | "
                      f"turns: {message.num_turns} | cost: {cost_str}")

                if message.is_error:
                    error_text = message.result or "Unknown error"
                    return False, "\n".join(output_parts), error_text, elapsed

                if message.result:
                    output_parts.append(message.result)

            elif isinstance(message, SystemMessage):
                if message.subtype == "error":
                    error_data = message.data.get("message", str(message.data))
                    formatted = _format_stream_line("error", error_data)
                    if formatted:
                        print(formatted)

        elapsed = time.time() - start_time
        full_output = "\n".join(output_parts)
        return True, full_output, "", elapsed

    except CLINotFoundError as e:
        cause = e.__cause__ or e.__context__
        print(f"  [debug] CLINotFoundError: {e}")
        print(f"  [debug]   cli_path={cli_path}")
        if cause:
            print(f"  [debug]   cause={type(cause).__name__}: {cause}")
        else:
            print(f"  [debug]   no cause chain")
        print(f"  [debug]   cwd={project_dir} exists={project_dir.exists()}")
        return False, "", (
            "PROCESS_ERROR: Claude Code CLI not found or failed to start. "
            "Install/verify: https://docs.anthropic.com/en/docs/claude-code"
        ), time.time() - start_time
    except CLIConnectionError as e:
        return False, "", f"CONNECTION_ERROR: {e}", time.time() - start_time
    except ProcessError as e:
        elapsed = time.time() - start_time
        stderr_text = e.stderr or ""
        if "auth" in stderr_text.lower() or "api key" in stderr_text.lower():
            return False, "", (
                "AUTH_ERROR: Invalid API credentials. "
                "Check your ANTHROPIC_API_KEY environment variable."
            ), elapsed
        if "rate limit" in stderr_text.lower() or "429" in stderr_text:
            return False, "", f"RATE_LIMIT: {e}", elapsed
        return False, "", f"PROCESS_ERROR: {e}", elapsed
    except ClaudeSDKError as e:
        return False, "", f"SDK_ERROR: {e}", time.time() - start_time
    except TimeoutError:
        return False, "", f"TIMEOUT: Claude Code timed out after {TASK_TIMEOUT}s", time.time() - start_time
    except OSError as e:
        return False, "", f"CONNECTION_ERROR: {e}", time.time() - start_time


def run_task(project_dir: Path, prompt: str,
             model: str | None = None) -> Tuple[bool, str, str, float]:
    """
    Run a single task via Claude Code SDK with streaming output.

    This is the synchronous entry point that wraps the async SDK call.
    Output is streamed to the terminal in real time as Claude works.

    Args:
        project_dir: The project directory to execute in.
        prompt: The full task prompt for Claude Code.
        model: Optional model override for Claude Code SDK.

    Returns:
        A tuple of (success, stdout, stderr, duration_seconds) where:
        - success: True if the task completed without errors.
        - stdout: The accumulated text output from Claude.
        - stderr: Error message if failed, empty string if success.
                  Prefixed with error type (AUTH_ERROR, TIMEOUT, etc.)
                  for structured error handling by the caller.
        - duration_seconds: Elapsed wall-clock time for the task.
    """
    _check_sdk_available()

    import anyio

    print(f"\n  [builder] Invoking Claude Code (SDK streaming)...")

    async def _run():
        return await _run_task_async(project_dir, prompt, model=model)

    return anyio.run(_run)


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
            data = json.loads(pkg.read_text(encoding="utf-8"))
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
            data = json.loads(pkg.read_text(encoding="utf-8"))
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
