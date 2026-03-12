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
forge run --checkin-every 5          # pause for human review every N tasks
forge status --cost --health --log  # show build state, cost, health, logs
forge rollback --list               # list rollback points
forge new                           # guided project setup interview
forge profile                       # manage global tool preferences
forge checkin                       # interactively resolve NEEDS_HUMAN items
forge reset-task <id>               # retry a parked task by ID
```

No linter or formatter is configured. No CI pipeline exists yet.

## Architecture

The build loop flows: **run.py → orchestrator.py → builder.py → git_utils.py**, with checkpoint.py saving state at every transition.

**orchestrator.py** — Calls Anthropic API (Claude Opus) to generate phases, generate tasks, write ARCHITECTURE.md, evaluate QA, and review phases. All API calls go through `_chat()` which has built-in retry with exponential backoff and optional `mcp_servers` parameter for MCP integration. `_json_chat()` wraps `_chat()` with JSON parsing. `build_task_prompt()` uses `ContextBudget` for intelligent truncation instead of hardcoded char limits. All public functions (`generate_phases`, `generate_tasks`, `write_architecture`, `evaluate_qa`, `evaluate_phase`) accept an optional `mcp_config` parameter.

**builder.py** — Calls Claude Code SDK (`claude_code_sdk.query()`) to execute each task. Streams output in real time. Returns `(success, stdout, stderr, duration)` with error prefixes: AUTH_ERROR, RATE_LIMIT, CONNECTION_ERROR, TIMEOUT, PROCESS_ERROR, SDK_ERROR.

**commands/run.py** — The main loop. `run_forge()` handles initial setup, then iterates phases/tasks. `_execute_task()` calls builder, runs tests, evaluates QA, commits on success, retries or parks on failure. `_complete_phase()` runs E2E tests, security scan, and phase QA evaluation before advancing. Signal handlers save checkpoint on Ctrl+C. `FatalAPIError` exits with code 1 (auth broken), `RetryExhaustedError` exits with code 0 (resumable pause). Loads `MCPConfig` at startup and threads it through all orchestrator calls.

**state.py** — Dataclasses: `ForgeState` → `Phase` → `Task`. Task statuses: PENDING → IN_PROGRESS → DONE/FAILED/PARKED/INTERRUPTED/COMMIT_PENDING/WAITING. State persists to `.forge/state.json`. Task IDs are 8-char UUID substrings via `Task.new()`. `Task.depends_on: list[str]` declares dependencies on other task IDs within the same phase.

**checkpoint.py** — Atomic saves via write-to-temp-then-rename. Detects interrupted tasks on startup for automatic resume.

**context_budget.py** — Priority-based token allocation (80K budget). Non-truncatable blocks (task, notes) always included; truncatable blocks (arch, claude.md, vision, skills) trimmed lowest-priority-first at word boundaries.

**retry.py** — Exponential backoff `[5, 15, 30, 60, 120]s`. Classifies errors as retryable (RATE_LIMIT, CONNECTION_ERROR, TIMEOUT) or fatal (AUTH_ERROR). `check_connectivity()` pings api.anthropic.com.

### Parallel Execution & Dependencies

**parallel.py** — `ParallelExecutor` runs tasks concurrently using asyncio, up to `max_parallel` (default 3, env `FORGE_MAX_PARALLEL`, clamped 1–10). Uses `ParallelLocks` to serialize git commits, state saves, cost tracking, and print output. `run_tasks()` executes wave-by-wave: computes execution waves from dependency graph, runs each wave in parallel, waits for wave completion before starting next.

**dependency_graph.py** — Analyzes `Task.depends_on` declarations to produce execution waves. `build_dependency_graph()` constructs adjacency dict, ignoring unknown dep IDs with a warning. `detect_cycle()` uses iterative DFS (not recursive). `compute_execution_waves()` returns ordered lists of tasks that can run in parallel. Cycle detected → falls back to single-wave sequential. `get_ready_tasks()` for live readiness checks. Imports only `forge.state`.

### Model Routing

**router.py** — Assigns Claude models to orchestrator functions and builder tasks based on complexity and failure history. Three tiers: Opus (high stakes: QA eval, architecture), Sonnet (moderate: task generation, most builder tasks), Haiku (low stakes: phase listing, docs). After 2 failures on assigned model, escalates to next tier. `route_orchestrator()` for API calls, `route_task()` for builder tasks. Imports only `cost_tracker` for model constants.

### MCP Integration

**mcp_config.py** — Reads `.forge/mcp.json` for MCP (Model Context Protocol) server configs. `MCPConfig.for_operation(op)` filters servers by operation name (`task_generation`, `qa_evaluation`, `architecture`, `phase_evaluation`); empty `use_for` means all operations. `MCPConfig.to_api_format(op)` returns the Anthropic API `mcp_servers` format. `load_mcp_config()` never raises — returns empty config on any error. `KNOWN_MCP_STARTERS` has presets for github, supabase, linear, filesystem. Zero forge imports (stdlib only).

### Quality Gates

Four modules run quality checks at different stages of the build:

**diff_review.py** — Runs after each task. Semantic diff review using Sonnet. Verdicts: `approved`, `flagged`, `error`, `skipped`. Flagged issues are reported but do not block. Uses `_parse_review_response()` for structured output parsing.

**visual_qa.py** — Runs after frontend tasks. Takes screenshots via Playwright and sends to Claude Vision for visual assessment. Requires Playwright with Chromium (`is_playwright_available()` gates execution).

**e2e_generator.py** — Runs after each phase completes. Generates Playwright TypeScript E2E tests for phases with relevant signals (auth, payment, dashboard, etc.). `should_generate_e2e()` checks phase/task titles for trigger signals. Results passed to `evaluate_phase()`.

**security_scan.py** — Runs after each phase completes. Pattern-based regex scan for hardcoded secrets, SQL injection, eval(), path traversal (critical) and http URLs, TODO security, disabled SSL, weak crypto (warning). `review_findings_with_claude()` filters false positives before blocking. `run_npm_audit()` / `run_pip_audit()` for dependency CVEs. Critical confirmed findings inject a fix task into the phase.

### Observability Layer

Three modules provide post-build analytics — all are pure (no side effects except file I/O to `.forge/`):

**cost_tracker.py** — Tracks token usage and estimated costs per task/phase/session. Logs to `.forge/cost_log.jsonl`. Fires alerts when a task exceeds `DEFAULT_TASK_TOKEN_ALERT` (40K tokens) or session cost exceeds `DEFAULT_SESSION_COST_ALERT` ($5). Imports only `context_budget.CHARS_PER_TOKEN`.

**build_logger.py** — Append-only JSONL event log at `.forge/build.log`. Records session/phase/task lifecycle, QA results, git operations, retries, and errors. Each `forge run` generates a random 8-hex-char session ID. Zero forge imports (stdlib only).

**health.py** — Computes `HealthReport` (letter grade A–F) from build.log and cost_log.jsonl. Session metrics (success rate, retry rate, avg cost) and project metrics (cost trend, retry hotspots, slowest phase). Imports only `build_logger.read_log`.

### Project Setup

**memory.py** — Persistent project memory in `.forge/memory/` (decisions.md, patterns.md, failures.md). Loaded before every task, written after successful tasks. Heuristic extraction from QA summaries via signal phrases. Zero forge imports.

**commands/new.py** — Guided AI interview to generate VISION.md, REQUIREMENTS.md, CLAUDE.md. Uses Anthropic API for question generation and doc synthesis. Integrates `advanced_options.py` for optional detailed configuration. Offers optional MCP server setup at end of interview via `_offer_mcp_setup()`.

**profile.py** — User preferences at `~/.forge/profile.yaml`. Pre-fills `forge new` interview with tech stack defaults.

### Flow Control

**loop_guard.py** — `LoopGuard` detects stuck tasks (failed N times) and parks them. Prevents infinite retry loops.

**needs_human.py** — Manages `NEEDS_HUMAN.md` in the project root. Parked tasks and QA failures are appended here for human review. `forge checkin` resolves items interactively.

## Key Patterns

- **Pure utility modules** (`retry.py`, `context_budget.py`, `build_logger.py`, `memory.py`, `advanced_options.py`, `display.py`, `router.py`, `mcp_config.py`, `dependency_graph.py`) have zero imports from other forge modules (except `dependency_graph.py` which imports only `forge.state`) — they are imported by others, never the reverse.
- **Error prefixes** in builder stderr (e.g. `"RATE_LIMIT: too many requests"`) are parsed by `extract_error_prefix()` to classify errors.
- **`_classify_anthropic_error()`** in orchestrator.py is the single place mapping SDK exceptions to error prefixes.
- **Atomic file writes** throughout: write to `.tmp`, then `tmp.replace(target)`. Used by checkpoint, cost_tracker, build_logger, and memory.
- **display.py** symbols (`SYM_OK`, `SYM_FAIL`, `SYM_WARN`) have ASCII fallbacks when stdout doesn't support Unicode.
- **`.forge/` directory** is the single location for all runtime state: `state.json`, `cost_log.jsonl`, `build.log`, `memory/`, `mcp.json`.
- **Phase completion flow** in `_complete_phase()`: E2E tests → security scan → `evaluate_phase()` → tag/advance. Security and E2E results are passed to `evaluate_phase()` for the full picture.
- **Never-raise convention** for quality gate modules (`diff_review`, `security_scan`, `e2e_generator`) and MCP config loading: all public functions catch exceptions and return safe defaults so the build loop is never crashed by a quality check or missing config.
- **Wave-based parallel execution**: tasks with `depends_on` are grouped into waves by `compute_execution_waves()`. Each wave runs in parallel; waves execute sequentially. Tasks with no dependencies run in a single wave (fully parallel).
- **Task dependency ID remapping**: `generate_tasks()` maps API-generated IDs (e.g. `t_01`) to real UUID-based task IDs via `id_map`. The `depends_on` field is remapped after all tasks are created.

## Testing Conventions

- Tests use `tmp_path` fixture for file operations, `monkeypatch` for mocking.
- All `time.sleep` calls are mocked in retry tests. All network calls are mocked in connectivity tests.
- Helper factories like `_make_state_with_task(status)` create minimal test fixtures.
- Test files mirror source: `forge/retry.py` → `tests/test_retry.py`.

## Dependencies

Python 3.10+. Runtime: `anthropic>=0.40.0`, `claude-code-sdk>=0.0.9`, `anyio>=4.0.0`, `pyyaml>=6.0`. Also uses `requests` (for connectivity checks). Entry point: `forge=forge.cli:main`.
