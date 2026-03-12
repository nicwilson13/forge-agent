# Forge

**Your personal developer.** Forge is an autonomous AI coding agent that builds production-grade applications from a vision document and requirements. It plans, codes, tests, deploys, and monitors, phase by phase, while you watch from a browser dashboard.

## Quick Demo

```
$ forge run

  ▸ Phase 1: Authentication & User Management
  [route] Task "Implement Supabase auth with email/password" → claude-sonnet-4-5 (auth signal)
  ✓ Task 1/4 completed in 2m 14s
  ✓ Task 2/4 completed in 1m 48s
  ✓ Task 3/4 completed in 3m 02s
  ✓ Task 4/4 completed in 1m 31s
  ✓ Visual QA: desktop + mobile passed
  ✓ Security scan: 0 critical, 2 warnings
  ✓ E2E tests: 4 passed, 0 failed
  ✓ Vercel: https://my-app-abc123.vercel.app (READY)
  ✓ GitHub PR: https://github.com/user/my-app/pull/3
  ▸ Phase 2: Dashboard & Analytics ...
```

## What Forge Builds

Forge works best for full-stack TypeScript projects. Next.js + Supabase SaaS apps, dashboards, admin panels, and anything in the skill pack domain: auth flows, payment integrations, database schemas, deployment pipelines, and UI components.

## Install

```bash
pip install forge-agent
```

Or from source:

```bash
git clone https://github.com/nicholascooke/forge-agent
cd forge-agent
pip install -e .
```

**Prerequisites:** Python 3.10+, Claude Code (`npm install -g @anthropic-ai/claude-code`), Node.js 20+, Git.

## Quick Start

```bash
# 1. Create a new project
forge new "a SaaS for project management"

# 2. Edit VISION.md and REQUIREMENTS.md

# 3. Run the build
forge run

# 4. Watch in browser
# Dashboard auto-opens at http://localhost:3333
```

## How It Works

1. **Plan** - Forge reads your vision and requirements, generates phases and tasks, and writes ARCHITECTURE.md.
2. **Build** - Claude Code executes each task. Tests run after every task. Passing tasks are committed.
3. **Verify** - Visual QA, E2E tests, diff review, and security scan run after each phase.
4. **Ship** - Vercel deployment check, GitHub PR created, Linear milestone closed.

## Commands

| Command | Description |
|---------|-------------|
| `forge new` | Create a new project with guided AI interview |
| `forge init` | Scaffold template files (VISION.md, REQUIREMENTS.md, CLAUDE.md) |
| `forge run` | Run the full build pipeline |
| `forge status` | Show current build status, cost, health, and logs |
| `forge checkin` | Resume after human review of parked tasks |
| `forge reset-task <id>` | Retry a parked task by ID |
| `forge rollback` | Roll back to a previous phase |
| `forge doctor` | Check environment, integrations, and doc quality |
| `forge dashboard` | View last build in browser (read-only) |
| `forge linear-plan` | Generate a Linear project plan from the build plan |
| `forge profile` | Manage global tool preferences and tech stack defaults |

## Quality Layer

Four quality gates run after every phase to catch issues before they compound.

- **Visual QA** - Playwright captures screenshots at desktop (1280x800) and mobile (375x812). Claude Vision evaluates layout, responsiveness, and completeness.
- **E2E tests** - Playwright TypeScript tests generated per phase, committed permanently, and run in CI.
- **Semantic diff review** - Claude reviews each task's diff for unexpected deletions and regressions.
- **Security scan** - Regex patterns detect hardcoded secrets, SQL injection, eval(), and path traversal. Claude filters false positives. npm audit and pip-audit check dependencies for CVEs.

## Skill Packs

Domain-specific best practices injected into task generation context.

- **Database** - UUID PKs, RLS, reversible migrations, cursor pagination, N+1 prevention
- **Auth** - HttpOnly cookies, Supabase `getUser()`, RBAC, CSRF protection, OAuth/OIDC
- **Payments** - Stripe idempotency keys, webhook-driven state, PCI compliance (SAQ A)
- **Deploy** - Vercel config, env vars, CI/CD, health checks, `maxDuration` settings
- **UI Components** - shadcn/ui, Tailwind design tokens, accessibility (WCAG), react-hook-form + Zod

## Integrations

| Integration | What Forge Does |
|-------------|-----------------|
| **GitHub** | Creates PRs and milestones per phase, posts build summaries, links issues to tasks |
| **Vercel** | Polls deployment status, fetches build logs on failure, auto-injects fix tasks |
| **Linear** | Reads issues for task planning, updates status on completion, syncs full plan via `forge linear-plan` |
| **Sentry** | Queries runtime errors after deploys, creates fix tasks for unresolved issues |
| **Figma** | Extracts design variables (colors, typography, spacing), generates `design-tokens.ts` |
| **Ollama** | Routes planning tasks to a local LLM for $0.00 inference cost |

All integrations are configured during `forge new` and validated by `forge doctor`. Tokens are stored in `~/.forge/profile.yaml`, never in the project directory.

## Dashboard

Forge starts a live web dashboard at `http://localhost:3333` during `forge run`. It shows phase progress, current task, cumulative cost, and health grade in real time. An integration status row displays the state of GitHub, Vercel, Linear, Sentry, Figma, and Ollama connections. A scrolling build log streams events as they happen. Run `forge dashboard` after a build for read-only review.

## Model Routing

Forge routes each operation to the right Claude model. Opus handles architecture generation and QA evaluation. Sonnet handles task generation and most builder tasks. Haiku handles lightweight calls like phase listing. Ollama can replace Sonnet for planning tasks when configured, running locally at zero cost. After 2 failures on the assigned model, Forge automatically escalates to the next tier.

## Configuration Files

```
project/
├── VISION.md              # What you're building
├── REQUIREMENTS.md        # Detailed requirements
├── CLAUDE.md              # Tech stack and coding standards
├── ARCHITECTURE.md        # Auto-generated system design
├── NEEDS_HUMAN.md         # Parked tasks for human review
└── .forge/
    ├── state.json         # Build state (phases, tasks, progress)
    ├── build.log          # Full event log (JSONL)
    ├── cost_log.jsonl     # Token usage and cost per task
    ├── memory/            # Project memory (decisions, patterns, failures)
    ├── mcp.json           # MCP server configuration
    ├── github.json        # GitHub integration
    ├── vercel.json        # Vercel integration
    ├── linear.json        # Linear integration
    ├── sentry.json        # Sentry integration
    ├── figma.json         # Figma integration
    └── ollama.json        # Ollama configuration
```

## Health Grades

Forge computes a health grade (A through F) from build metrics. An A grade requires 95% or higher task success rate and average cost under $0.05 per task. The grade factors in retry rate, cost trends, and retry hotspots. Run `forge status --health` to see the current grade.

## Contributing

Contributions are welcome. Open an issue or submit a pull request at [github.com/nicholascooke/forge-agent](https://github.com/nicholascooke/forge-agent).

## License

MIT
