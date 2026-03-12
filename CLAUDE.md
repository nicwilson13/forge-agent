# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Forge is an autonomous AI development agent. It reads VISION.md + REQUIREMENTS.md, breaks the project into phases and tasks, executes each task via Claude Code SDK, runs tests, evaluates quality, and commits — all in an unattended loop.

## Commands

```bash
pip install -e .                    # install in dev mode
python -m pytest tests/ -v          # run all tests
python -m pytest tests/test_retry.py -v                  # run one test file
python -m pytest tests/test_retry.py::test_is_fatal_auth_error -v  # run one test
forge doctor                        # pre-flight checks
forge run --dry-run --project-dir .  # plan without executing
forge status --cost --health --log  # show build state, cost, health, logs
forge rollback --list               # list rollback points
forge new                           # guided project setup interview
forge profile                       # manage global tool preferences
```

No linter or formatter is configured. No CI pipeline exists yet.

## Architecture

The build loop flows: **run.py → orchestrator.py → builder.py → git_utils.py**, with checkpoint.py saving state at every transition.

**orchestrator.py** — Calls Anthropic API (Claude Opus) to generate phases, generate tasks, write ARCHITECTURE.md, evaluate QA, and review phases. All API calls go through `_chat()` which has built-in retry with exponential backoff. `build_task_prompt()` uses `ContextBudget` for intelligent truncation instead of hardcoded char limits.

**builder.py** — Calls Claude Code SDK (`claude_code_sdk.query()`) to execute each task. Streams output in real time. Returns `(success, stdout, stderr, duration)` with error prefixes: AUTH_ERROR, RATE_LIMIT, CONNECTION_ERROR, TIMEOUT, PROCESS_ERROR, SDK_ERROR.

**commands/run.py** — The main loop. `run_forge()` handles initial setup, then iterates phases/tasks. `_execute_task()` calls builder, runs tests, evaluates QA, commits on success, retries or parks on failure. Signal handlers save checkpoint on Ctrl+C. `FatalAPIError` exits with code 1 (auth broken), `RetryExhaustedError` exits with code 0 (resumable pause).

**state.py** — Dataclasses: `ForgeState` → `Phase` → `Task`. Task statuses: PENDING → IN_PROGRESS → DONE/FAILED/PARKED/INTERRUPTED/COMMIT_PENDING. State persists to `.forge/state.json`.

**checkpoint.py** — Atomic saves via write-to-temp-then-rename. Detects interrupted tasks on startup for automatic resume.

**context_budget.py** — Priority-based token allocation (80K budget). Non-truncatable blocks (task, notes) always included; truncatable blocks (arch, claude.md, vision, skills) trimmed lowest-priority-first at word boundaries.

**retry.py** — Exponential backoff `[5, 15, 30, 60, 120]s`. Classifies errors as retryable (RATE_LIMIT, CONNECTION_ERROR, TIMEOUT) or fatal (AUTH_ERROR). `check_connectivity()` pings api.anthropic.com.

### Observability Layer

Three modules provide post-build analytics — all are pure (no side effects except file I/O to `.forge/`):

**cost_tracker.py** — Tracks token usage and estimated costs per task/phase/session. Logs to `.forge/cost_log.jsonl`. Fires alerts when a task exceeds `DEFAULT_TASK_TOKEN_ALERT` (40K tokens) or session cost exceeds `DEFAULT_SESSION_COST_ALERT` ($5). Imports only `context_budget.CHARS_PER_TOKEN`.

**build_logger.py** — Append-only JSONL event log at `.forge/build.log`. Records session/phase/task lifecycle, QA results, git operations, retries, and errors. Each `forge run` generates a random 8-hex-char session ID. Zero forge imports (stdlib only).

**health.py** — Computes `HealthReport` (letter grade A–F) from build.log and cost_log.jsonl. Session metrics (success rate, retry rate, avg cost) and project metrics (cost trend, retry hotspots, slowest phase). Imports only `build_logger.read_log`.

### Project Setup

**memory.py** — Persistent project memory in `.forge/memory/` (decisions.md, patterns.md, failures.md). Loaded before every task, written after successful tasks. Heuristic extraction from QA summaries via signal phrases. Zero forge imports.

**commands/new.py** — Guided AI interview to generate VISION.md, REQUIREMENTS.md, CLAUDE.md. Uses Anthropic API for question generation and doc synthesis. Integrates `advanced_options.py` for optional detailed configuration (project structure, API style, linting, testing approach, etc.).

**profile.py** — User preferences at `~/.forge/profile.yaml`. Pre-fills `forge new` interview with tech stack defaults.

## Key Patterns

- **Pure utility modules** (`retry.py`, `context_budget.py`, `build_logger.py`, `memory.py`, `advanced_options.py`) have zero imports from other forge modules — they are imported by others, never the reverse.
- **Error prefixes** in builder stderr (e.g. `"RATE_LIMIT: too many requests"`) are parsed by `extract_error_prefix()` to classify errors.
- **`_classify_anthropic_error()`** in orchestrator.py is the single place mapping SDK exceptions to error prefixes.
- **Atomic file writes** throughout: write to `.tmp`, then `tmp.replace(target)`. Used by checkpoint, cost_tracker, build_logger, and memory.
- **display.py** symbols (`SYM_OK`, `SYM_FAIL`, `SYM_WARN`) have ASCII fallbacks when stdout doesn't support Unicode.
- **LoopGuard** in `loop_guard.py` detects stuck tasks (failed N times) and parks them to NEEDS_HUMAN.md.
- **`.forge/` directory** is the single location for all runtime state: `state.json`, `cost_log.jsonl`, `build.log`, `memory/`.

## Testing Conventions

- Tests use `tmp_path` fixture for file operations, `monkeypatch` for mocking.
- All `time.sleep` calls are mocked in retry tests. All network calls are mocked in connectivity tests.
- Helper factories like `_make_state_with_task(status)` create minimal test fixtures.
- Test files mirror source: `forge/retry.py` → `tests/test_retry.py`.

## Dependencies

Python 3.10+. Runtime: `anthropic>=0.40.0`, `claude-code-sdk>=0.0.9`, `anyio>=4.0.0`, `pyyaml>=6.0`. Also uses `requests` (for connectivity checks). Entry point: `forge=forge.cli:main`.
