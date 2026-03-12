# Forge

**Autonomous AI development agent powered by Claude Code.**

Forge takes a software vision and builds it autonomously - planning phases, writing code,
running tests, committing, pushing, and resolving its own decisions. It only stops when it
genuinely needs you.

---

## How It Works

```
VISION.md + REQUIREMENTS.md + CLAUDE.md
          ↓
    Orchestrator (Claude Opus)
    - Generates development phases
    - Writes ARCHITECTURE.md
    - Plans tasks per phase
          ↓
    Builder (Claude Code CLI)
    - Executes each task
    - Writes real code
    - Runs tests
          ↓
    QA Agent (Claude Opus)
    - Evaluates test output
    - Passes or retries
          ↓
    Git: commit + push after every passing task
          ↓
    Repeat until vision is complete
```

When a task fails 3 times, or requires human judgment, it goes to `NEEDS_HUMAN.md`.
Forge routes around it and keeps building. You review at check-in points.

---

## Prerequisites

1. **Python 3.10+**
2. **Claude Code CLI** installed and authenticated
   ```
   npm install -g @anthropic-ai/claude-code
   claude auth
   ```
3. **Anthropic API key** (for the orchestrator brain)
   ```
   export ANTHROPIC_API_KEY=sk-ant-...
   ```
4. **Git** configured with your identity

---

## Install Forge

```bash
git clone https://github.com/yourname/forge-agent
cd forge-agent
pip install -e .
```

This installs the `forge` CLI globally.

---

## Usage

### 1. Initialize a project

```bash
cd /path/to/my-project
forge init
```

This creates three template files:
- `VISION.md` - describe what you're building
- `REQUIREMENTS.md` - functional + non-functional requirements
- `CLAUDE.md` - tech stack, standards, and coding conventions

### 2. Fill in the docs

`VISION.md` is the most important. Write it as if the product already exists.
Be specific about screens, flows, and what "done" looks like.

`CLAUDE.md` is where you set your stack. Example:
```
## Tech Stack
- Language: TypeScript
- Framework: Next.js 15 (App Router)
- Package manager: pnpm
- CSS: Tailwind CSS + shadcn/ui
- Testing: Vitest + Playwright
```

### 3. Start the autonomous build

```bash
forge run
```

Options:
```
--checkin-every 10    Pause every N tasks for your review (default: 10)
--max-retries 3       Retries before parking a failing task (default: 3)
--dry-run             Show the plan without executing
--project-dir ./path  Target a different directory
```

### 4. Check status any time

```bash
forge status
```

### 5. Handle parked items

When Forge needs you, it writes to `NEEDS_HUMAN.md`. Fill in the Resolution fields,
then:

```bash
forge checkin
forge run      # continues from where it left off
```

### 6. Force retry a task

```bash
forge reset-task <task-id>
forge run
```

---

## Project Files

After `forge init` + `forge run`, your project will have:

| File | Purpose |
|------|---------|
| `VISION.md` | End-state description (you write this) |
| `REQUIREMENTS.md` | Feature list and constraints (you write this) |
| `CLAUDE.md` | Tech stack and coding standards (you write this) |
| `ARCHITECTURE.md` | Auto-generated system design (Forge writes this) |
| `NEEDS_HUMAN.md` | Parking lot for blocked tasks (Forge writes, you resolve) |
| `.forge/state.json` | Build state - phases, tasks, progress (Forge manages this) |

---

## Mitigations for Known Shortcomings

| Problem | Forge's mitigation |
|---|---|
| Context drift | ARCHITECTURE.md re-read on every task |
| Compounding errors | QA agent evaluates every task before committing |
| Loop traps | LoopGuard parks tasks after N failures |
| Scope creep | Tasks generated strictly from phase description |
| Bad phase QA | Phase review gate before advancing |
| Human blockers | NEEDS_HUMAN.md parking + checkin workflow |
| Silent failures | Build + test runner output evaluated by QA agent |

---

## Tips for Best Results

- **Write a detailed VISION.md.** The more specific, the better the output.
  Vague visions produce vague software.
- **Set your stack in CLAUDE.md before the first run.** Changing it mid-build is messy.
- **Check `forge status` and your repo after each phase.** This is your quality gate.
- **Keep NEEDS_HUMAN.md open in VS Code** while Forge runs. You'll see items appear in real time.
- **Run on a branch**, not main. Review and merge when a phase looks good.

---

## Architecture

```
forge/
├── forge/
│   ├── cli.py              Entry point + argument parsing
│   ├── orchestrator.py     Anthropic API: phase planning, task generation, QA eval
│   ├── builder.py          Claude Code CLI execution + test runner
│   ├── state.py            Build state persistence (.forge/state.json)
│   ├── git_utils.py        Git operations
│   ├── loop_guard.py       Retry/loop detection
│   ├── needs_human.py      NEEDS_HUMAN.md manager
│   └── commands/
│       ├── run.py          Main autonomous loop
│       ├── init.py         Template scaffolding
│       ├── status.py       Progress display
│       ├── checkin.py      Resolve parked tasks
│       └── reset_task.py   Force retry a task
└── setup.py
```

---

## Cost Estimate

Forge uses `claude-opus-4-5` for orchestration (planning and QA evaluation) and
`claude` (Claude Code) for building. A typical session:

- Simple project (10-20 tasks): ~$5-15
- Medium project (50-80 tasks): ~$25-60
- Large project (150+ tasks): ~$100-200

Tip: Use `--dry-run` to see the plan before committing to a full run.

---

## License

MIT
