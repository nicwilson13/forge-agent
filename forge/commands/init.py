"""
forge init - scaffolds Forge documentation templates into a project.
"""

from pathlib import Path


def run_init(project_dir: Path):
    project_dir = Path(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[forge] Initializing Forge in: {project_dir.resolve()}\n")

    files = {
        "VISION.md": VISION_TEMPLATE,
        "REQUIREMENTS.md": REQUIREMENTS_TEMPLATE,
        "CLAUDE.md": CLAUDE_TEMPLATE,
    }

    for filename, content in files.items():
        path = project_dir / filename
        if path.exists():
            print(f"  [skip] {filename} already exists")
        else:
            path.write_text(content, encoding="utf-8")
            print(f"  [created] {filename}")

    # Create .forge dir
    forge_dir = project_dir / ".forge"
    forge_dir.mkdir(exist_ok=True)
    (forge_dir / ".gitkeep").touch()

    print(f"""
[forge] Done. Next steps:

  1. Edit VISION.md       - describe the end-state of your software
  2. Edit REQUIREMENTS.md - add functional + non-functional requirements
  3. Edit CLAUDE.md       - set your stack preferences, standards, constraints
  4. Run: forge run       - start the autonomous build loop
""")


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

VISION_TEMPLATE = """\
# VISION.md

> **What is the end-state of this software?**
> Describe it as if it is already built and in use. Be specific.

## Product Summary

<!-- One paragraph: what does this product do, who uses it, and what problem does it solve? -->

## Core User Experience

<!-- Walk through the primary user journey from start to finish. -->

## Key Screens / Interfaces

<!-- List or describe the main UI surfaces, pages, or CLI commands. -->

## Integrations

<!-- What external services, APIs, or systems does this connect to? -->

## Success Criteria

<!-- How will you know the product is "done"? What does working look like? -->
"""

REQUIREMENTS_TEMPLATE = """\
# REQUIREMENTS.md

## Functional Requirements

<!-- List the features and behaviours the system MUST have. -->

- [ ] 
- [ ] 
- [ ] 

## Non-Functional Requirements

<!-- Performance, security, accessibility, browser support, etc. -->

- [ ] 
- [ ] 

## Out of Scope

<!-- What is explicitly NOT being built in this version? -->

-
-

## Technical Constraints

<!-- Required frameworks, hosting platforms, auth providers, databases, etc. -->

-
-

## Design Direction

<!-- Visual style, brand, reference apps, design system, etc. -->

-
-
"""

CLAUDE_TEMPLATE = """\
# CLAUDE.md

This file is read by Forge (and Claude Code) on every task.
Define your standards here and the agent will follow them consistently.

## Tech Stack

<!-- Primary language, framework, runtime, package manager -->

- Language: 
- Framework: 
- Package manager: 
- CSS approach: 
- Testing: 

## Code Quality Standards

- All functions must have types / JSDoc
- No console.log left in production code
- Prefer composition over inheritance
- Write tests for every new module
- Error states must be handled explicitly - never silently swallow errors

## UI / UX Standards

- Mobile-first, responsive design
- WCAG AA accessibility minimum
- Smooth transitions on interactive elements
- Consistent spacing system (use design tokens, not magic numbers)
- Loading, empty, and error states required on every data surface

## Git Conventions

- Commit messages: [forge] <verb> <what> (e.g. [forge] Add authentication flow)
- Commit often - at least once per completed task
- Never commit broken builds

## Architecture Principles

- Keep components small and focused
- Co-locate tests with source files
- Avoid premature optimisation
- Prefer explicit over implicit

## Autonomous Decision Rules

When the agent must choose between options without asking, prefer:
- TypeScript over JavaScript
- Named exports over default exports
- Server components where possible (Next.js)
- Functional components with hooks (React)
- Zod for validation
- Postgres over SQLite for production data

## DO NOT

- Do not use any.
- Do not skip error handling.
- Do not leave TODO comments without a corresponding NEEDS_HUMAN.md entry.
- Do not install packages without noting them in ARCHITECTURE.md.
"""
