# ARCHITECTURE.md

## System Overview

Forge is an autonomous AI development agent that operates as a **deterministic build loop**:

```
┌─────────────┐    ┌──────────────────┐    ┌───────────┐    ┌────────────┐
│  VISION.md  │───▶│  orchestrator.py │───▶│ builder.py│───▶│ git_utils  │
│REQUIREMENTS │    │  (Anthropic API) │    │(Claude SDK)│   │  (commit)  │
└─────────────┘    └──────────────────┘    └───────────┘    └────────────┘
                            │                     │
                   ┌────────▼────────┐    ┌───────▼───────┐
                   │  checkpoint.py  │◀───│   state.py    │
                   │ (atomic saves)  │    │ (ForgeState)  │
                   └─────────────────┘    └───────────────┘
```

## Technology Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| **AI Planning** | Anthropic Claude (Opus/Sonnet/Haiku) | Best reasoning for architecture decisions |
| **Task Execution** | Claude Code SDK | Direct code generation with streaming |
| **Runtime** | Python 3.10+ | Async support, rich ecosystem |
| **State** | JSON files (`.forge/`) | Human-readable, git-friendly, no DB overhead |
| **Parallelism** | asyncio | Lightweight concurrency for task waves |

## Directory Structure

```
forge/
├── commands/           # CLI entry points (run.py, new.py, status.py)
├── skills/            # Markdown knowledge packs (auth.md, payments.md)
├── orchestrator.py    # Anthropic API calls for planning/evaluation
├── builder.py         # Claude Code SDK execution
├── state.py          # Dataclasses: ForgeState → Phase → Task
├── checkpoint.py     # Atomic state persistence
├── parallel.py       # Wave-based concurrent execution
├── dependency_graph.py # Task DAG analysis
├── router.py         # Model tier routing (Opus/Sonnet/Haiku)
├── *_integration.py  # External services (GitHub, Linear, Vercel, etc.)
├── dashboard.py      # Local web UI (localhost:3333)
└── quality gates     # diff_review, security_scan, visual_qa, e2e_generator
```

## Data Flow

1. **Planning**: `orchestrator.generate_phases()` → `generate_tasks()` → populates `ForgeState`
2. **Execution**: `parallel.run_tasks()` computes waves from `dependency_graph`, runs via `builder.py`
3. **Quality**: Post-task diff review → Post-phase security scan + E2E tests → `evaluate_phase()`
4. **Persistence**: `checkpoint.save()` after every state change (atomic write-then-rename)

## Key Patterns

### State Management
- Single source of truth: `.forge/state.json`
- Immutable transitions: Task statuses flow `PENDING → IN_PROGRESS → DONE/FAILED/PARKED`
- Forward compatibility: `load_state()` strips unknown fields

### Error Handling
- **Error prefixes**: Builder returns classified errors (`AUTH_ERROR`, `RATE_LIMIT`, etc.)
- **Never-raise convention**: Quality gates, integrations return safe defaults
- **Retry with backoff**: `[5, 15, 30, 60, 120]s` for transient failures

### Concurrency
- Wave-based parallelism: `depends_on` → DAG → ordered waves
- `ParallelLocks`: Serializes git commits, state saves, cost tracking

### Token Budget
- `ContextBudget`: 80K token budget, priority-based truncation
- Non-truncatable: task prompt, notes. Truncatable: arch, vision, skills

## Architecture Decisions

### ADR-1: File-based state over database
**Context**: Need persistent state across runs  
**Decision**: JSON in `.forge/` directory  
**Consequence**: Human-readable, git-trackable, but manual migration on schema changes

### ADR-2: Tiered model routing
**Context**: Cost vs. quality tradeoff  
**Decision**: Opus for QA/architecture, Sonnet for tasks, Haiku for simple operations  
**Consequence**: ~70% cost reduction with escalation on repeated failures

### ADR-3: Wave-based parallelism
**Context**: Tasks have dependencies but independent tasks should parallelize  
**Decision**: Compute execution waves from DAG, run each wave concurrently  
**Consequence**: Optimal parallelism while respecting dependencies; falls back to sequential on cycles

### ADR-4: Sensitive tokens in `~/.forge/profile.yaml`
**Context**: API tokens shouldn't be in project repos  
**Decision**: Global user config for secrets, project config for settings  
**Consequence**: Safe to commit `.forge/`, tokens travel with user

### ADR-5: Never-raise integrations
**Context**: External API failures shouldn't crash builds  
**Decision**: All integration modules catch exceptions, return empty/safe values  
**Consequence**: Builds continue even when GitHub/Linear/Vercel are down