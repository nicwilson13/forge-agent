# ARCHITECTURE.md

## System Overview

Forge is an autonomous AI development agent that converts project vision documents into working code through an orchestrated build loop. It operates as a **stateful finite state machine** where each task progresses through defined states (PENDING → IN_PROGRESS → DONE/FAILED/PARKED).

```
┌─────────────┐    ┌──────────────┐    ┌───────────┐    ┌────────────┐
│   run.py    │───▶│orchestrator.py│───▶│ builder.py│───▶│git_utils.py│
│ (main loop) │    │ (Anthropic)  │    │(Claude SDK)│   │  (commits) │
└─────────────┘    └──────────────┘    └───────────┘    └────────────┘
       │                  │                   │
       ▼                  ▼                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    checkpoint.py (atomic state saves)               │
│                         .forge/state.json                           │
└─────────────────────────────────────────────────────────────────────┘
```

## Technology Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Language | Python 3.10+ | Rich SDK ecosystem, async support, wide adoption |
| AI Orchestration | Anthropic API (Claude Opus/Sonnet/Haiku) | Best-in-class reasoning for planning |
| Task Execution | Claude Code SDK | Direct code generation with file system access |
| State Persistence | JSON files in `.forge/` | No database dependency, human-readable, git-friendly |
| CLI Framework | Click (implied via `forge.cli:main`) | Standard Python CLI tooling |
| Async Runtime | anyio | Cross-platform async primitives for parallel execution |

## Directory Structure

```
forge/
├── cli.py                 # Entry point, Click command routing
├── state.py               # ForgeState/Phase/Task dataclasses
├── orchestrator.py        # Anthropic API calls (_chat, _json_chat)
├── builder.py             # Claude Code SDK execution
├── checkpoint.py          # Atomic state persistence
├── run.py                 # Main build loop
├── parallel.py            # Concurrent task execution
├── dependency_graph.py    # Task dependency analysis
├── router.py              # Model tier routing (Opus/Sonnet/Haiku)
├── context_budget.py      # Token allocation & truncation
├── retry.py               # Exponential backoff & error classification
├── commands/              # CLI subcommands (new, run, status, etc.)
├── skills/                # Domain-specific markdown knowledge packs
└── *_integration.py       # External service connectors

.forge/                    # Project runtime state (gitignored)
├── state.json             # Current build state
├── build.log              # JSONL event log
├── cost_log.jsonl         # Token usage tracking
├── memory/                # Persistent decisions/patterns
└── *.json                 # Integration configs
```

## Data Flow

1. **Planning**: `orchestrator.generate_phases()` → `generate_tasks()` → state.json
2. **Execution**: `run._execute_task()` → `builder.query()` → stdout/stderr capture
3. **Evaluation**: `orchestrator.evaluate_qa()` → grade assignment
4. **Commit**: On PASS, `git_utils.commit()` → checkpoint save
5. **Phase Completion**: E2E tests → security scan → `evaluate_phase()` → GitHub PR

## Key Patterns

**State Machine**: Tasks transition atomically through `TaskStatus` enum. Interrupted tasks resume on restart via checkpoint detection.

**Never-Raise Integrations**: All external service modules (GitHub, Linear, Figma, Vercel, Sentry) catch exceptions and return safe defaults—build loop never crashes from API failures.

**Pure Utility Modules**: `retry.py`, `context_budget.py`, `build_logger.py` have zero internal imports—they're leaf dependencies.

**Atomic File Writes**: Write to `.tmp` then `rename()` for crash-safe persistence.

**Error Prefix Protocol**: Builder stderr prefixes (`RATE_LIMIT:`, `AUTH_ERROR:`) enable structured error classification.

**Wave-Based Parallelism**: `dependency_graph.compute_execution_waves()` groups independent tasks for concurrent execution.

## Architectural Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| State in JSON files vs DB | JSON files | Zero setup, human-inspectable, version control friendly |
| Separate orchestrator/builder | Yes | Opus for planning (expensive, smart), SDK for execution (streaming, file access) |
| Token stored in `~/.forge/` | Yes | Sensitive data outside project directory, shareable across projects |
| MCP servers per-operation | Yes | Fine-grained control over which tools are available for different AI operations |
| Quality gates don't block | Flags only | Human review for nuanced issues; automated for clear failures |

## Integration Points

- **Anthropic API**: Planning, QA evaluation, architecture generation
- **Claude Code SDK**: Task code generation
- **GitHub API**: Milestones, PRs, issue linking
- **Linear API**: Issue sync, Kanban planning
- **Vercel API**: Deployment status polling
- **Sentry API**: Error tracking → auto-fix tasks
- **Ollama**: Optional local model routing for planning