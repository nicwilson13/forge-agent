# ARCHITECTURE.md

## High-Level System Design

Forge is an autonomous AI development agent that operates as a CLI tool. It reads project specifications (VISION.md, REQUIREMENTS.md), generates a phased build plan, and executes tasks via Claude Code SDK in an unattended loop.

```
┌─────────────┐     ┌──────────────────┐     ┌────────────┐     ┌────────────┐
│   CLI       │────▶│   Orchestrator   │────▶│  Builder   │────▶│ Git Utils  │
│  (run.py)   │     │  (Claude Opus)   │     │ (SDK exec) │     │  (commits) │
└─────────────┘     └──────────────────┘     └────────────┘     └────────────┘
       │                    │                       │                  │
       └────────────────────┴───────────────────────┴──────────────────┘
                                    │
                            ┌───────▼───────┐
                            │  Checkpoint   │
                            │ (state.json)  │
                            └───────────────┘
```

**Data Flow**: `forge run` → load state → generate phases (Orchestrator) → generate tasks per phase → execute each task (Builder) → run tests → evaluate QA → commit on success → retry/park on failure → complete phase → advance.

## Technology Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Language | Python 3.10+ | Claude Code SDK native support, rapid CLI development |
| AI API | Anthropic Claude (Opus/Sonnet/Haiku) | Best-in-class reasoning, tiered model routing for cost optimization |
| Task Execution | `claude-code-sdk` | Direct code generation with streaming output |
| State | JSON files in `.forge/` | Simple, human-readable, git-friendly, no DB dependencies |
| Dashboard | stdlib `http.server` + SSE | Zero dependencies, runs anywhere Python runs |
| Async | `asyncio` + `anyio` | Parallel task execution within waves |

## Directory Structure

```
forge/
├── commands/           # CLI entry points (run.py, new.py, status.py, etc.)
├── integrations/       # External services (GitHub, Linear, Vercel, Figma, Sentry)
├── quality/            # Quality gates (diff_review, security_scan, visual_qa, e2e)
├── dashboard/          # Web UI views and nav shell
├── skills/             # Domain knowledge packs (auth.md, payments.md, etc.)
├── orchestrator.py     # Claude Opus API wrapper
├── builder.py          # Claude Code SDK executor
├── state.py            # ForgeState → Phase → Task dataclasses
├── checkpoint.py       # Atomic state persistence
├── parallel.py         # Wave-based concurrent execution
├── router.py           # Model tier selection
└── context_budget.py   # Token allocation with priority truncation
```

**Runtime state**: `.forge/state.json`, `.forge/build.log`, `.forge/cost_log.jsonl`, `.forge/memory/`

## Key Patterns

1. **Pure utility modules**: `retry.py`, `context_budget.py`, `build_logger.py`, etc. have zero forge imports—imported by others, never reverse.

2. **Error prefix classification**: Builder stderr uses prefixes (`AUTH_ERROR:`, `RATE_LIMIT:`) parsed by `extract_error_prefix()` for retry/fatal decisions.

3. **Never-raise convention**: Quality gates, integrations, and config loaders catch all exceptions, returning safe defaults to never crash the build loop.

4. **Atomic file writes**: Write to `.tmp` then `rename()`. Used by checkpoint, cost_tracker, build_logger.

5. **Wave-based parallelism**: Tasks declare `depends_on` → `compute_execution_waves()` → parallel within waves, sequential between.

## Architecture Decisions

**ADR-1: File-based state over database**
JSON in `.forge/` is human-readable, git-friendly, and requires no setup. Trade-off: no concurrent access safety (mitigated by `ParallelLocks`).

**ADR-2: Tiered model routing**
Opus for high-stakes (QA, architecture), Sonnet for moderate (task generation), Haiku for low-stakes (phase listing). Escalation after 2 failures. Balances cost vs. quality.

**ADR-3: Quality gates are non-blocking by default**
`diff_review` flags but doesn't block; `security_scan` only blocks on confirmed critical findings. Maintains forward progress.

**ADR-4: Tokens in profile, config in project**
Sensitive tokens (`~/.forge/profile.yaml`) separate from project config (`.forge/*.json`). Project configs can be committed; tokens cannot.

**ADR-5: SSE for dashboard updates**
Server-Sent Events over WebSocket: simpler, unidirectional (server→client), no extra dependencies.