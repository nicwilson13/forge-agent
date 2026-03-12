# ARCHITECTURE.md

## High-Level System Design

Forge is an autonomous AI development agent built as a pipeline with clear stage boundaries:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              FORGE PIPELINE                              │
├──────────────────────────────────────────────────────────────────────────┤
│  Input: VISION.md + REQUIREMENTS.md                                      │
│                         ↓                                                │
│  ┌─────────────┐   ┌──────────────┐   ┌──────────────┐   ┌────────────┐ │
│  │ Orchestrator │ → │   Builder    │ → │ Quality Gates│ → │ Git Utils  │ │
│  │ (Opus API)   │   │ (Claude SDK) │   │ (scan/test)  │   │ (commit)   │ │
│  └─────────────┘   └──────────────┘   └──────────────┘   └────────────┘ │
│                         ↓                                                │
│  Checkpoint: .forge/state.json (atomic save at every transition)         │
└──────────────────────────────────────────────────────────────────────────┘
```

**Core Flow**: `run.py` → `orchestrator.py` → `builder.py` → `git_utils.py`

## Technology Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Runtime | Python 3.10+ | Claude SDK compatibility, async support |
| AI Planning | Anthropic Claude (Opus/Sonnet/Haiku) | Best-in-class reasoning for code generation |
| AI Execution | Claude Code SDK | Streaming task execution with real-time output |
| State | JSON files in `.forge/` | Simple, git-friendly, no database dependency |
| Parallelism | asyncio + wave-based scheduling | Bounded concurrency with dependency ordering |
| Dashboard | stdlib http.server + SSE | Zero dependencies, runs anywhere |

## Directory Structure

```
forge/
├── commands/          # CLI entry points (run, new, status, checkin, etc.)
├── skills/            # Markdown knowledge packs (auth, payments, deploy)
├── orchestrator.py    # AI planning: phases, tasks, architecture, QA
├── builder.py         # Task execution via Claude Code SDK
├── state.py           # Dataclasses: ForgeState → Phase → Task
├── checkpoint.py      # Atomic state persistence
├── context_budget.py  # Token allocation with priority-based truncation
├── parallel.py        # Wave-based concurrent task execution
├── dependency_graph.py# DAG analysis for execution ordering
├── router.py          # Model routing (Opus/Sonnet/Haiku) by complexity
├── *_integration.py   # External service connectors (GitHub, Linear, etc.)
├── *_view.py          # Dashboard page renderers
└── quality gates/     # diff_review, security_scan, visual_qa, e2e_generator
```

**State Location**: All runtime data lives in `.forge/` (state, logs, configs). Tokens live in `~/.forge/profile.yaml`.

## Data Flow

1. **Planning**: Orchestrator reads `VISION.md` + `REQUIREMENTS.md` → generates phases → generates tasks per phase
2. **Execution**: Builder executes tasks via SDK → streams output → returns (success, stdout, stderr, duration)
3. **Quality**: Each task passes through diff review; each phase through security scan + E2E tests
4. **Persistence**: Checkpoint saves state atomically after every status change
5. **Integration**: GitHub milestones/PRs created per phase; Linear/Vercel/Sentry sync optional

## Key Patterns

- **Never-raise integration modules**: All external API calls return safe defaults on failure
- **Error prefix classification**: Builder stderr uses prefixes (`AUTH_ERROR:`, `RATE_LIMIT:`) for retry logic
- **Atomic writes**: All file operations use temp-then-rename pattern
- **Wave-based parallelism**: Tasks grouped by dependency into waves; waves run sequentially, tasks within waves run concurrently
- **Priority token budgeting**: 80K token budget with truncation by priority (task/notes never truncated)
- **Model escalation**: After 2 failures, task routes to higher-tier model

## Key Decisions (ADR-lite)

| Decision | Choice | Why |
|----------|--------|-----|
| No database | JSON files | Simplicity, portability, git-friendly state |
| Separate orchestrator/builder | Clear API/execution boundary | Different models, different retry semantics |
| Wave-based (not reactive) parallelism | Predictable execution order | Easier debugging, deterministic behavior |
| Quality gates don't block | Report and continue | Build completion > perfection; humans review NEEDS_HUMAN.md |
| Tokens in user home, configs in project | Security + portability | Secrets never committed; configs version-controlled |
| stdlib-only dashboard | No npm/build step | Runs anywhere Python runs |