"""
Retry and backoff utilities for Forge.

Provides:
- Exponential backoff with jitter for API calls
- Connection polling for offline detection and recovery
- Retry decorator for wrapping API calls
- Error classification (retryable vs fatal)

This module has zero dependencies on other forge modules.
"""

import sys
import time

import requests

# Backoff schedule in seconds: 5, 15, 30, 60, 120
BACKOFF_SCHEDULE = [5, 15, 30, 60, 120]

# How long to wait between connection checks when fully offline
OFFLINE_POLL_INTERVAL = 10   # seconds

# Max connection check attempts before giving up
MAX_OFFLINE_ATTEMPTS = 10

# Known error prefixes from builder.py
_RETRYABLE_PREFIXES = {"RATE_LIMIT", "CONNECTION_ERROR", "TIMEOUT"}
_FATAL_PREFIXES = {"AUTH_ERROR"}
_KNOWN_PREFIXES = _RETRYABLE_PREFIXES | _FATAL_PREFIXES | {"PROCESS_ERROR", "SDK_ERROR"}

# Local Unicode support detection (no forge.display dependency)
_encoding = getattr(sys.stdout, "encoding", "") or ""
_UNICODE = _encoding.lower().replace("-", "") in ("utf8", "utf16", "utf32", "utf8sig")
_SYM_WARN = "\u26A0" if _UNICODE else "[WARN]"
_SYM_FAIL = "\u2717" if _UNICODE else "[FAIL]"
_SYM_OK = "\u2713" if _UNICODE else "[OK]"


class RetryExhaustedError(Exception):
    """Raised when all retry attempts have been exhausted."""

    def __init__(self, error_prefix: str, attempts: int, last_error: str):
        self.error_prefix = error_prefix
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"Exhausted {attempts} retries. Last error: {last_error}"
        )


class FatalAPIError(Exception):
    """Raised on authentication or other non-retryable errors."""

    def __init__(self, error_prefix: str, message: str, fix_instruction: str):
        self.error_prefix = error_prefix
        self.fix_instruction = fix_instruction
        super().__init__(message)


def is_retryable_error(error_prefix: str) -> bool:
    """
    Return True if the error type should be retried with backoff.

    Retryable: RATE_LIMIT, CONNECTION_ERROR, TIMEOUT
    Fatal (not retryable): AUTH_ERROR, PROCESS_ERROR
    Unknown prefixes: treat as retryable (conservative)
    """
    if error_prefix in _RETRYABLE_PREFIXES:
        return True
    if error_prefix in _FATAL_PREFIXES or error_prefix == "PROCESS_ERROR":
        return False
    # Unknown prefix - be conservative and retry
    return True


def is_fatal_error(error_prefix: str) -> bool:
    """
    Return True if the error requires immediate stop (no retry).

    Currently only AUTH_ERROR is fatal - the user must fix their key.
    PROCESS_ERROR is not fatal - it may be a transient Claude Code issue.
    """
    return error_prefix in _FATAL_PREFIXES


def extract_error_prefix(stderr: str) -> str:
    """
    Extract the error prefix from a builder.run_task() stderr string.

    Returns the prefix string (e.g. "RATE_LIMIT") or "UNKNOWN" if
    the stderr does not start with a known prefix.
    """
    if not stderr or not stderr.strip():
        return "UNKNOWN"
    # Prefixes are formatted as "PREFIX: detail message"
    first_part = stderr.strip().split(":", 1)[0].strip()
    if first_part in _KNOWN_PREFIXES:
        return first_part
    return "UNKNOWN"


def wait_with_countdown(seconds: int, message: str) -> None:
    """
    Wait for `seconds` with a live countdown printed to terminal.

    Updates the same line each second using carriage return.
    Falls back to a single line print if not a tty.
    Catches KeyboardInterrupt and re-raises it so Ctrl+C still works.
    """
    if not sys.stdout.isatty():
        print(f"  {_SYM_WARN}  {message} - {seconds}s remaining...")
        time.sleep(seconds)
        return

    try:
        for remaining in range(seconds, 0, -1):
            sys.stdout.write(f"\r  {_SYM_WARN}  {message} - {remaining}s remaining...  ")
            sys.stdout.flush()
            time.sleep(1)
        sys.stdout.write("\r" + " " * 60 + "\r")
        sys.stdout.flush()
    except KeyboardInterrupt:
        sys.stdout.write("\n")
        raise


def check_connectivity(timeout: int = 5) -> bool:
    """
    Check if the Anthropic API is reachable.

    Makes a HEAD request to https://api.anthropic.com with a short
    timeout. Returns True if reachable, False otherwise.
    Does not raise - always returns bool.
    """
    try:
        resp = requests.head("https://api.anthropic.com", timeout=timeout)
        return resp.status_code < 500
    except Exception:
        return False


def wait_for_connectivity(max_attempts: int = MAX_OFFLINE_ATTEMPTS,
                          poll_interval: int = OFFLINE_POLL_INTERVAL
                          ) -> bool:
    """
    Poll for connectivity until restored or max attempts exceeded.

    Prints progress each attempt. Returns True when connected,
    False if max attempts exceeded without connection.
    Catches KeyboardInterrupt and returns False (user wants to exit).
    """
    print(f"\n  Waiting for connection... (Ctrl+C to exit and resume later)")
    try:
        for attempt in range(1, max_attempts + 1):
            if check_connectivity():
                print(f"  {_SYM_OK} Connection restored. Resuming build.")
                return True
            print(f"  {_SYM_FAIL} Attempt {attempt}/{max_attempts} failed - no connection")
            if attempt < max_attempts:
                time.sleep(poll_interval)
        return False
    except KeyboardInterrupt:
        print("\n  [forge] Interrupted while waiting for connection.")
        return False


def with_retry(func, *args,
               max_retries: int = 5,
               error_context: str = "API call",
               **kwargs):
    """
    Call func(*args, **kwargs) with exponential backoff on failure.

    Uses BACKOFF_SCHEDULE for wait times between retries.
    Raises immediately on fatal errors (AUTH_ERROR).
    Returns the function result on success.
    Raises RetryExhaustedError after max_retries failures.

    error_context: human-readable name for the operation being retried,
    used in progress messages.
    """
    last_prefix = "UNKNOWN"
    last_error = ""

    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except FatalAPIError:
            raise
        except RetryExhaustedError:
            raise
        except Exception as e:
            last_error = str(e)
            last_prefix = extract_error_prefix(last_error)

            if is_fatal_error(last_prefix):
                raise FatalAPIError(
                    error_prefix=last_prefix,
                    message=last_error,
                    fix_instruction=(
                        "Check your key at console.anthropic.com/settings/keys\n"
                        "       export ANTHROPIC_API_KEY=sk-ant-your-new-key"
                    ),
                )

            if attempt < max_retries - 1:
                backoff = BACKOFF_SCHEDULE[min(attempt, len(BACKOFF_SCHEDULE) - 1)]
                wait_with_countdown(
                    backoff,
                    f"Retry {attempt + 1}/{max_retries} for {error_context}",
                )

    raise RetryExhaustedError(
        error_prefix=last_prefix,
        attempts=max_retries,
        last_error=last_error,
    )
