# ARCHITECTURE.md

## High-Level System Design

Forge is an autonomous AI development agent that operates as a **planning → execution → evaluation loop**:

```
┌─────────────────────────────────────────────────────────────────┐
│                         forge run                               │
├─────────────────────────────────────────────────────────────────┤
│  run.py (CLI) → orchestrator.py (planning) → builder.py (exec) │
│       ↑                    ↓                       ↓            │
│  checkpoint.py ←──── state.py ←──────────── git_utils.py       │
└─────────────────────────────────────────────────────────────────┘
```

**Core Loop**: Read project docs → Generate phases/tasks (Anthropic API) → Execute each task (Claude Code SDK) → Run tests → Evaluate QA → Commit or retry → Repeat.

## Technology Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Language | Python 3.10+ | Claude Code SDK requirement, broad ecosystem |
| Planning API | Anthropic (Opus/Sonnet/Haiku) | Best reasoning for code planning |
| Execution | Claude Code SDK | Agentic coding with tool use |
| State | JSON files in `.forge/` | No DB overhead, git-friendly, debuggable |
| CLI | Click (implied by entry point) | Standard Python CLI framework |
| Dashboard | Built-in HTTP + SSE | Zero external dependencies |

## Directory Structure

```
forge/
├── cli.py                 # Entry point
├── commands/
│   ├── run.py             # Main build loop
│   ├── new.py             # Project setup wizard
│   └── linear_plan.py     # Linear sync command
├── orchestrator.py        # Anthropic API calls (planning, QA)
├── builder.py             # Claude Code SDK execution
├── state.py               # ForgeState/Phase/Task dataclasses
├── checkpoint.py          # Atomic state persistence
├── context_budget.py      # Token budget allocation
├── retry.py               # Exponential backoff
├── parallel.py            # Async task execution
├── dependency_graph.py    # Task dependency resolution
├── router.py              # Model tier selection
├── *_integration.py       # External service adapters
├── dashboard.py           # Web UI server
└── skills/                # Domain knowledge packs
```

## Data Flow

1. **Input**: `VISION.md` + `REQUIREMENTS.md` + `CLAUDE.md`
2. **Planning**: `orchestrator.generate_phases()` → `generate_tasks()` per phase
3. **Execution**: `builder.execute_task()` streams Claude Code SDK output
4. **Evaluation**: Test runner → `orchestrator.evaluate_qa()` → pass/fail
5. **Persistence**: `checkpoint.save()` after every state change
6. **Output**: Git commits, `.forge/state.json`, optional PR/milestones

## Key Patterns

**State Machine**: Tasks flow `PENDING → IN_PROGRESS → DONE|FAILED|PARKED`. Interrupted state detected on restart for auto-resume.

**Error Classification**: Builder stderr prefixes (`AUTH_ERROR`, `RATE_LIMIT`, etc.) parsed by `extract_error_prefix()` to determine retry vs fatal.

**Never-Raise Convention**: Quality gates and integrations catch all exceptions, return safe defaults. Build loop never crashes from optional features.

**Atomic Writes**: All file operations write to `.tmp` then `rename()`. Prevents corruption on interrupt.

**Wave-Based Parallelism**: `dependency_graph.compute_execution_waves()` groups tasks; each wave runs concurrently, waves execute sequentially.

**Token Budget**: `ContextBudget` allocates 80K tokens with priority-based truncation. Non-truncatable (task spec) always included; truncatable (docs, skills) trimmed lowest-priority-first.

## Architectural Decisions

**ADR-1: File-based state over database**
- Decision: Store all state in `.forge/*.json`
- Context: Need persistence without external dependencies
- Consequence: Git-diffable, debuggable, but no concurrent access

**ADR-2: Separate planning (Anthropic API) from execution (Claude Code SDK)**
- Decision: Two distinct API paths
- Context: SDK is agentic (tool use), API is structured (JSON responses)
- Consequence: Better model routing, cleaner error handling

**ADR-3: Pure utility modules with zero internal imports**
- Decision: `retry.py`, `router.py`, `mcp_config.py`, etc. import nothing from forge
- Context: Prevent circular dependencies, enable isolated testing
- Consequence: Clear dependency direction, simpler refactoring

**ADR-4: Sensitive tokens in `~/.forge/profile.yaml`, not project dir**
- Decision: Global user config for API keys
- Context: Projects may be committed to git
- Consequence: Tokens never accidentally committed

**ADR-5: Quality gates are advisory, not blocking (mostly)**
- Decision: `diff_review` flags don't block; security criticals do inject fix tasks
- Context: AI judgment isn't perfect; blocking on every flag would halt progress
- Consequence: Build continues with visibility into potential issues