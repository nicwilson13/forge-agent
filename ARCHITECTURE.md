# ARCHITECTURE.md

## High-Level Design

Forge is an autonomous AI development agent that operates in an unattended build loop:

```
CLI (forge run) → Orchestrator (planning) → Builder (execution) → Quality Gates → Git Commit
      ↑                                                                    ↓
      └────────────────── Checkpoint (resume on failure) ←─────────────────┘
```

**Core Components:**
- **Orchestrator** (`orchestrator.py`): Calls Anthropic API for planning—phase/task generation, architecture docs, QA evaluation
- **Builder** (`builder.py`): Executes tasks via Claude Code SDK, streams output, classifies errors
- **Run Loop** (`commands/run.py`): Main state machine—iterates phases/tasks, handles retries, commits
- **State** (`state.py`): Dataclasses (`ForgeState` → `Phase` → `Task`) persisted to `.forge/state.json`
- **Checkpoint** (`checkpoint.py`): Atomic saves for crash recovery

## Technology Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Runtime | Python 3.10+ | SDK availability, rapid prototyping |
| AI Planning | Anthropic API (Opus/Sonnet/Haiku) | Model routing by complexity |
| AI Execution | Claude Code SDK | Direct code generation with tool use |
| State | JSON files in `.forge/` | No external DB, portable, git-friendly |
| Dashboard | stdlib `http.server` + SSE | Zero dependencies, localhost only |
| Integrations | urllib (no requests) | Minimize dependencies in utility modules |

## Directory Structure

```
forge/
├── cli.py                 # Entry point, command router
├── orchestrator.py        # Anthropic API planning calls
├── builder.py             # Claude Code SDK execution
├── state.py               # ForgeState/Phase/Task dataclasses
├── checkpoint.py          # Atomic state persistence
├── parallel.py            # Async task execution with locks
├── dependency_graph.py    # Task wave computation
├── router.py              # Model tier selection
├── retry.py               # Backoff logic, error classification
├── context_budget.py      # Token allocation (80K budget)
├── commands/              # CLI command implementations
│   ├── run.py             # Main build loop
│   ├── new.py             # Project setup interview
│   └── linear_plan.py     # Linear sync command
├── skills/                # Domain-specific markdown guides
└── [integrations]         # GitHub, Linear, Figma, Vercel, Sentry, Ollama
```

**Runtime artifacts** (`.forge/`): `state.json`, `cost_log.jsonl`, `build.log`, `memory/`, integration configs

## Data Flow

1. **Planning**: `generate_phases()` → `generate_tasks()` → tasks with `depends_on` declarations
2. **Execution**: `compute_execution_waves()` → parallel task execution within waves, sequential between waves
3. **Quality**: Per-task (`diff_review`) → Per-phase (`e2e_generator`, `security_scan`)
4. **Persistence**: Every state change → atomic checkpoint write

## Key Patterns

- **Pure utility modules**: Integration modules (`*_integration.py`) import only stdlib—never crash the build loop
- **Error prefix protocol**: Builder stderr uses prefixes (`AUTH_ERROR:`, `RATE_LIMIT:`) parsed by `extract_error_prefix()`
- **Never-raise convention**: Quality gates and integrations catch all exceptions, return safe defaults
- **Wave-based parallelism**: Tasks grouped by dependency into waves; each wave runs concurrently
- **Atomic writes**: All file operations use write-to-temp-then-rename

## Key Decisions

**ADR-1: Single `.forge/` directory**
All runtime state in one location. Simplifies backup, .gitignore, and debugging.

**ADR-2: Sensitive tokens in `~/.forge/profile.yaml`**
Never store API keys in project directory. Prevents accidental commits.

**ADR-3: Model routing by tier**
Opus for high-stakes (QA, architecture), Sonnet for tasks, Haiku for docs. Escalate after 2 failures.

**ADR-4: Integration modules are stdlib-only**
Zero forge imports ensures failures are isolated. All return safe defaults.

**ADR-5: Task dependencies via `depends_on` field**
Explicit declaration in task generation. IDs remapped from API placeholders to real UUIDs.

**ADR-6: Phase completion gates**
E2E → Security scan → Phase evaluation → GitHub/Vercel/Sentry checks. All results fed to `evaluate_phase()`.