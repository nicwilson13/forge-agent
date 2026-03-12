"""
Orchestrator Agent
Uses the Anthropic API to:
  - Generate phases from VISION.md + REQUIREMENTS.md
  - Generate granular tasks per phase
  - Write/update ARCHITECTURE.md
  - Evaluate whether a task needs human input
  - Decide if a QA failure is fixable or should be parked
"""

import json
import os
from pathlib import Path
from typing import List, Tuple

import anthropic

from forge.context_budget import ContextBudget, ContentBlock, DEFAULT_BUDGET, estimate_tokens
from forge.cost_tracker import TokenUsage, MODEL_OPUS
from forge.memory import load_memory_context, ensure_memory_dir
from forge.router import route_orchestrator, log_route
from forge.retry import (
    FatalAPIError,
    RetryExhaustedError,
    BACKOFF_SCHEDULE,
    wait_with_countdown,
)
from forge.state import Phase, Task, ForgeState

_CLIENT = None

MAX_RETRIES = 5   # max retry attempts for any single API call
DEFAULT_PROMPT_BUDGET = DEFAULT_BUDGET  # tokens for task prompts


def _client() -> anthropic.Anthropic:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    return _CLIENT


def _classify_anthropic_error(exc: Exception) -> tuple[str, bool, str]:
    """
    Map an Anthropic SDK exception to (error_prefix, is_fatal, fix_instruction).

    Centralises all exception-to-prefix mapping in one place.
    """
    if isinstance(exc, anthropic.AuthenticationError):
        return (
            "AUTH_ERROR",
            True,
            "Check your key at console.anthropic.com/settings/keys\n"
            "       export ANTHROPIC_API_KEY=sk-ant-your-new-key",
        )
    if isinstance(exc, anthropic.RateLimitError):
        return ("RATE_LIMIT", False, "")
    if isinstance(exc, anthropic.APIConnectionError):
        return ("CONNECTION_ERROR", False, "")
    if isinstance(exc, anthropic.APITimeoutError):
        return ("TIMEOUT", False, "")
    if isinstance(exc, anthropic.APIStatusError):
        code = getattr(exc, "status_code", 0)
        if code == 529 or code >= 500:
            return ("CONNECTION_ERROR", False, "")
        # 4xx client errors (other than auth/rate limit already caught)
        return (
            "AUTH_ERROR",
            True,
            f"API returned client error {code}. Check your request configuration.",
        )
    return ("UNKNOWN", False, "")


def _chat(system: str, user: str, max_tokens: int = 4096,
          model: str = MODEL_OPUS,
          mcp_servers: list[dict] | None = None) -> tuple[str, TokenUsage]:
    last_error_str = ""
    last_prefix = "UNKNOWN"

    for attempt in range(MAX_RETRIES):
        try:
            kwargs = dict(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            if mcp_servers:
                kwargs["mcp_servers"] = mcp_servers
            resp = _client().messages.create(**kwargs)
            usage = TokenUsage(
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                model=model,
            )
            return resp.content[0].text.strip(), usage
        except (
            anthropic.AuthenticationError,
            anthropic.RateLimitError,
            anthropic.APIConnectionError,
            anthropic.APITimeoutError,
            anthropic.APIStatusError,
        ) as e:
            prefix, is_fatal, fix_instruction = _classify_anthropic_error(e)
            last_prefix = prefix
            last_error_str = str(e)

            if is_fatal:
                raise FatalAPIError(
                    error_prefix=prefix,
                    message=last_error_str,
                    fix_instruction=fix_instruction,
                )

            if attempt < MAX_RETRIES - 1:
                backoff = BACKOFF_SCHEDULE[min(attempt, len(BACKOFF_SCHEDULE) - 1)]
                wait_with_countdown(
                    backoff,
                    f"Retry {attempt + 1}/{MAX_RETRIES}",
                )

    raise RetryExhaustedError(
        error_prefix=last_prefix,
        attempts=MAX_RETRIES,
        last_error=last_error_str,
    )


def _json_chat(system: str, user: str, max_tokens: int = 4096,
               model: str = MODEL_OPUS,
               mcp_servers: list[dict] | None = None) -> tuple[dict | list, TokenUsage]:
    """Call API and parse JSON from the response. Returns (parsed_json, usage)."""
    system_with_json = system + "\n\nYou MUST respond with valid JSON only. No prose, no markdown fences."
    raw, usage = _chat(system_with_json, user, max_tokens, model=model,
                       mcp_servers=mcp_servers)
    # Strip accidental markdown fences
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    return json.loads(raw), usage


# ---------------------------------------------------------------------------
# Phase generation
# ---------------------------------------------------------------------------

PHASE_SYSTEM = """
You are the lead architect on an autonomous software project.
Given a VISION and REQUIREMENTS document, you will break the project into
ordered development phases that move from foundation to completion.

Rules:
- 4 to 8 phases maximum
- Each phase must be independently deployable / testable
- Phase 1 is always project scaffolding, config, CI, and repo setup
- Final phase is always polish, performance, and production hardening
- Phases must be sequenced so later phases build on earlier ones

Return a JSON array of phase objects:
[
  {
    "title": "Phase 1: Project Scaffolding",
    "description": "Detailed description of what gets built in this phase, the tech decisions made, and acceptance criteria."
  },
  ...
]
"""


def generate_phases(project_dir: Path, mcp_config=None) -> Tuple[List[Phase], TokenUsage]:
    model = route_orchestrator("generate_phases")
    log_route("generate_phases", model, "structured list")

    vision = _read_doc(project_dir, "VISION.md")
    requirements = _read_doc(project_dir, "REQUIREMENTS.md")
    claude_md = _read_doc(project_dir, "CLAUDE.md")

    user = f"""
VISION.md:
{vision}

REQUIREMENTS.md:
{requirements}

CLAUDE.md (constraints + preferences):
{claude_md}
"""
    mcp_servers = mcp_config.to_api_format("task_generation") if mcp_config else None
    raw_phases, usage = _json_chat(PHASE_SYSTEM, user, model=model,
                                   mcp_servers=mcp_servers or None)
    return [Phase.new(p["title"], p["description"]) for p in raw_phases], usage


# ---------------------------------------------------------------------------
# Task generation
# ---------------------------------------------------------------------------

TASK_SYSTEM = """
You are a senior software engineer breaking a development phase into discrete,
executable coding tasks for an AI coding agent.

Rules:
- 3 to 10 tasks per phase
- Each task is a single, focused unit of work (one feature, one component, one config, etc.)
- Tasks must be ordered: infrastructure before features, features before tests, tests before polish
- Each task description must be detailed enough for an AI to execute without ambiguity
- Include what files to create/modify, what the output should look like, and what tests to write
- Flag any task that requires external secrets, manual configuration, or human judgment
  by including "NEEDS_HUMAN: <reason>" at the start of the description

For each task, declare any dependencies on other tasks in the same phase.
A task depends on another if it uses code, data, or setup from that task.

Return a JSON array:
[
  {
    "id": "t_01",
    "title": "Short task title",
    "description": "Detailed instructions for the AI coding agent.",
    "needs_human": false,
    "depends_on": []
  },
  {
    "id": "t_02",
    "title": "Another task",
    "description": "Detailed instructions...",
    "needs_human": false,
    "depends_on": ["t_01"]
  }
]

Rules for depends_on:
- Only list DIRECT dependencies (not transitive)
- Only reference task IDs within the same phase
- Keep dependencies minimal - only declare what is strictly required
- Prefer parallel execution (fewer deps = faster builds)
- Foundation tasks (project setup, config) that everything needs: list them
- UI tasks that need the foundation: depend on foundation
- Independent features (e.g. separate API routes): no deps on each other
"""


def generate_tasks(project_dir: Path, phase: Phase, state: ForgeState,
                   mcp_config=None,
                   github_issues_context: str = "",
                   figma_context: str = "") -> Tuple[List[Task], TokenUsage]:
    model = route_orchestrator("generate_tasks")
    log_route("generate_tasks", model, "moderate complexity")

    vision = _read_doc(project_dir, "VISION.md")
    arch = _read_doc(project_dir, "ARCHITECTURE.md")
    claude_md = _read_doc(project_dir, "CLAUDE.md")

    # Summarise completed phases for context
    completed_summary = ""
    for i, p in enumerate(state.phases):
        if i < state.current_phase_index:
            done_tasks = [t.title for t in p.tasks if t.status == "done"]
            completed_summary += f"\n- {p.title}: completed tasks: {', '.join(done_tasks)}"

    user = f"""
VISION.md:
{vision}

ARCHITECTURE.md:
{arch}

CLAUDE.md:
{claude_md}

Completed phases so far:
{completed_summary or 'None yet'}

Now generate tasks for this phase:
Title: {phase.title}
Description: {phase.description}

{github_issues_context}

{figma_context}
"""
    mcp_servers = mcp_config.to_api_format("task_generation") if mcp_config else None
    raw_tasks, usage = _json_chat(TASK_SYSTEM, user, model=model,
                                  mcp_servers=mcp_servers or None)
    tasks = []
    # Map API-generated IDs (e.g. t_01) to real UUIDs
    id_map: dict[str, str] = {}
    for t in raw_tasks:
        desc = t["description"]
        task = Task.new(t["title"], desc, phase.id)
        # Track mapping from API ID to real task ID
        api_id = t.get("id", "")
        if api_id:
            id_map[api_id] = task.id
        # Pre-flag NEEDS_HUMAN tasks
        if t.get("needs_human") or desc.strip().upper().startswith("NEEDS_HUMAN"):
            task.park_reason = desc.split("\n")[0].replace("NEEDS_HUMAN:", "").strip()
        # Store raw depends_on for remapping after all tasks created
        task._raw_deps = t.get("depends_on", [])
        tasks.append(task)
    # Remap depends_on from API IDs to real task IDs
    for task in tasks:
        raw_deps = getattr(task, "_raw_deps", [])
        task.depends_on = [id_map[d] for d in raw_deps if d in id_map]
        if hasattr(task, "_raw_deps"):
            del task._raw_deps
    return tasks, usage


# ---------------------------------------------------------------------------
# Architecture document
# ---------------------------------------------------------------------------

ARCH_SYSTEM = """
You are a principal software architect.
Write a concise ARCHITECTURE.md for this project that covers:
- High-level system design and component relationships
- Technology stack and rationale
- Directory structure
- Data flow
- Key patterns used (state management, auth, API design, etc.)
- Decisions made and why (ADR-lite format)

This document will be re-read by AI agents on every task to maintain consistency.
Keep it focused and scannable. Max 600 words.
"""


def write_architecture(project_dir: Path, phases: List[Phase],
                       mcp_config=None) -> TokenUsage:
    model = route_orchestrator("write_architecture")
    log_route("write_architecture", model, "high stakes")

    vision = _read_doc(project_dir, "VISION.md")
    requirements = _read_doc(project_dir, "REQUIREMENTS.md")
    claude_md = _read_doc(project_dir, "CLAUDE.md")
    phases_summary = "\n".join(f"- {p.title}: {p.description[:120]}..." for p in phases)

    user = f"""
VISION.md:
{vision}

REQUIREMENTS.md:
{requirements}

CLAUDE.md:
{claude_md}

Planned phases:
{phases_summary}
"""
    mcp_servers = mcp_config.to_api_format("architecture") if mcp_config else None
    arch_content, usage = _chat(ARCH_SYSTEM, user, model=model,
                                mcp_servers=mcp_servers or None)
    arch_path = project_dir / "ARCHITECTURE.md"
    arch_path.write_text(arch_content)
    print(f"  [forge] Wrote ARCHITECTURE.md")
    return usage


# ---------------------------------------------------------------------------
# Task prompt builder (what we send to Claude Code)
# ---------------------------------------------------------------------------

BUILDER_SYSTEM_TEMPLATE = """
You are an expert full-stack software engineer working autonomously on a project.

PROJECT CONTEXT:
{vision}

ARCHITECTURE:
{arch}

CODING STANDARDS (from CLAUDE.md):
{claude_md}

{memory_section}

{skills_section}

CURRENT PHASE: {phase_title}
{phase_desc}

YOUR TASK:
{task_title}
{task_desc}

REQUIREMENTS:
1. Write world-class, production-quality code. No shortcuts, no TODO stubs.
2. Follow the architecture and coding standards exactly.
3. Write or update tests for everything you build. Tests must pass.
4. Apply exceptional UI/UX design where frontend work is involved.
5. Use cutting-edge, modern patterns for the stack in use.
6. After completing the task, run: npm test / pytest / cargo test (whichever applies).
7. If tests pass, stage and commit with: git add -A && git commit -m "[forge] {task_title}"
8. Do not ask clarifying questions. Make the best decision and document it in a comment.
9. If you encounter a hard blocker requiring human input, write it to NEEDS_HUMAN.md and stop.

Previous task notes (if any): {notes}
"""


def build_task_prompt(project_dir: Path, phase: Phase, task: Task) -> str:
    """Build the task execution prompt with intelligent context budgeting."""
    vision = _read_doc(project_dir, "VISION.md")
    arch = _read_doc(project_dir, "ARCHITECTURE.md")
    claude_md = _read_doc(project_dir, "CLAUDE.md")

    # Build non-negotiable task context (never truncated)
    task_context = (
        f"CURRENT PHASE: {phase.title}\n{phase.description}\n\n"
        f"YOUR TASK:\n{task.title}\n{task.description}"
    )
    notes_context = f"Previous task notes: {task.notes or 'None'}"

    # Allocate budget across all content blocks
    budget = ContextBudget(max_tokens=DEFAULT_PROMPT_BUDGET)
    budget.add(ContentBlock("task", task_context, priority=1, truncatable=False))
    budget.add(ContentBlock("notes", notes_context, priority=2, truncatable=False))
    budget.add(ContentBlock("arch", arch, priority=3, truncatable=True))
    budget.add(ContentBlock("claude", claude_md, priority=4, truncatable=True))

    # Load memory context
    ensure_memory_dir(project_dir)
    memory = load_memory_context(project_dir, max_chars=3000)
    if memory:
        budget.add(ContentBlock("memory", memory, priority=5, truncatable=True))

    budget.add(ContentBlock("vision", vision, priority=6, truncatable=True))
    # Skill injection based on task content signals
    task_text = f"{task.title} {task.description}".lower()

    FRONTEND_SIGNALS = [
        "frontend", "ui", "ux", "component", "css", "tailwind", "react",
        "next.js", "nextjs", "vue", "svelte", "html", "responsive",
        "layout", "design", "style", "animation", "page", "modal",
        "form", "button", "navigation", "sidebar", "header", "footer",
    ]

    DATABASE_SIGNALS = [
        "database", "schema", "migration", "table", "query", "sql",
        "postgres", "supabase", "drizzle", "prisma", "orm", "seed",
        "redis", "mongodb", "sqlite", "mysql", "index", "relation",
        "transaction", "foreign key", "column", "postgresql", "typeorm",
        "sequelize", "mongoose", "fixture",
    ]

    skills_dir = Path(__file__).parent / "skills"

    if any(sig in task_text for sig in FRONTEND_SIGNALS):
        fe_skill_path = skills_dir / "frontend-design.md"
        if fe_skill_path.exists():
            fe_skill = fe_skill_path.read_text()
            budget.add(ContentBlock(
                name="frontend_skill",
                content=fe_skill,
                priority=7,
                truncatable=True,
            ))

    if any(sig in task_text for sig in DATABASE_SIGNALS):
        db_skill_path = skills_dir / "database.md"
        if db_skill_path.exists():
            db_skill = db_skill_path.read_text()
            budget.add(ContentBlock(
                name="database_skill",
                content=db_skill,
                priority=7,
                truncatable=True,
            ))

    AUTH_SIGNALS = [
        "auth", "authentication", "authorization", "login", "logout",
        "signup", "register", "password", "session", "token", "jwt",
        "oauth", "permission", "role", "rbac", "middleware",
        "supabase auth", "nextauth", "clerk", "csrf", "cookie",
    ]

    if any(sig in task_text for sig in AUTH_SIGNALS):
        auth_skill_path = skills_dir / "auth.md"
        if auth_skill_path.exists():
            auth_skill = auth_skill_path.read_text()
            budget.add(ContentBlock(
                name="auth_skill",
                content=auth_skill,
                priority=7,
                truncatable=True,
            ))

    PAYMENTS_SIGNALS = [
        "payment", "stripe", "billing", "subscription", "checkout",
        "invoice", "charge", "refund", "webhook", "price", "plan",
        "trial", "coupon", "customer", "revenue", "mrr",
    ]

    if any(sig in task_text for sig in PAYMENTS_SIGNALS):
        payments_skill_path = skills_dir / "payments.md"
        if payments_skill_path.exists():
            payments_skill = payments_skill_path.read_text()
            budget.add(ContentBlock(
                name="payments_skill",
                content=payments_skill,
                priority=7,
                truncatable=True,
            ))

    DEPLOY_SIGNALS = [
        "deploy", "vercel", "production", "environment variable", "env var",
        "ci", "cd", "pipeline", "build", "preview", "staging",
        "domain", "dns", "edge", "serverless", "cron",
        "github actions", "workflow", "monitoring", "sentry",
    ]

    if any(sig in task_text for sig in DEPLOY_SIGNALS):
        deploy_skill_path = skills_dir / "deploy.md"
        if deploy_skill_path.exists():
            deploy_skill = deploy_skill_path.read_text()
            budget.add(ContentBlock(
                name="deploy_skill",
                content=deploy_skill,
                priority=7,
                truncatable=True,
            ))

    UI_SIGNALS = [
        "component", "shadcn", "tailwind", "button", "input", "form",
        "modal", "dialog", "dropdown", "table", "toast", "navbar",
        "sidebar", "layout", "card", "tabs", "accordion", "accessible",
        "aria", "dark mode", "theme", "responsive",
    ]

    if any(sig in task_text for sig in UI_SIGNALS):
        ui_skill_path = skills_dir / "ui-components.md"
        if ui_skill_path.exists():
            ui_skill = ui_skill_path.read_text()
            budget.add(ContentBlock(
                name="ui_skill",
                content=ui_skill,
                priority=7,
                truncatable=True,
            ))

    allocated = budget.allocate()

    # Collect injected skill content
    skill_parts = []
    for key in ("frontend_skill", "database_skill", "auth_skill", "payments_skill", "deploy_skill", "ui_skill"):
        content = allocated.get(key, "")
        if content:
            skill_parts.append(content)
    skills_section = "\n\n".join(skill_parts) if skill_parts else ""

    return BUILDER_SYSTEM_TEMPLATE.format(
        vision=allocated["vision"],
        arch=allocated["arch"],
        claude_md=allocated["claude"],
        memory_section=allocated.get("memory", ""),
        skills_section=skills_section,
        phase_title=phase.title,
        phase_desc=phase.description,
        task_title=task.title,
        task_desc=task.description,
        notes=task.notes or "None",
    )


# ---------------------------------------------------------------------------
# QA evaluation
# ---------------------------------------------------------------------------

QA_EVAL_SYSTEM = """
You are a senior QA engineer reviewing an AI-generated commit.
Given the task description, test output, and any error messages, determine:
1. Did the task complete successfully?
2. Are there any quality issues, missing tests, or broken functionality?
3. Should we proceed or retry?

Return JSON:
{
  "passed": true | false,
  "summary": "brief summary of findings",
  "retry_prompt": "if failed: specific instructions to fix this, otherwise null"
}
"""


def evaluate_qa(task: Task, test_output: str, error_output: str,
                mcp_config=None) -> Tuple[bool, str, str, TokenUsage]:
    model = route_orchestrator("evaluate_qa")
    log_route("evaluate_qa", model, "high stakes")

    user = f"""
Task: {task.title}
Description: {task.description}

Test output:
{test_output[-3000:] if test_output else 'No test output'}

Errors:
{error_output[-2000:] if error_output else 'None'}
"""
    mcp_servers = mcp_config.to_api_format("qa_evaluation") if mcp_config else None
    result, usage = _json_chat(QA_EVAL_SYSTEM, user, model=model,
                               mcp_servers=mcp_servers or None)
    passed = result.get("passed", False)
    summary = result.get("summary", "")
    retry_prompt = result.get("retry_prompt") or ""
    return passed, summary, retry_prompt, usage


# ---------------------------------------------------------------------------
# Phase QA review
# ---------------------------------------------------------------------------

PHASE_QA_SYSTEM = """
You are a technical lead reviewing the completion of a development phase.
Given the phase goals and the tasks that were completed, assess:
1. Is the phase truly done?
2. Are there gaps that would block the next phase?
3. Any critical issues to fix before proceeding?

Return JSON:
{
  "approved": true | false,
  "notes": "summary",
  "blocking_issues": ["issue1", "issue2"] or []
}
"""


def evaluate_phase(project_dir: Path, phase: Phase,
                   e2e_passed: bool | None = None,
                   e2e_summary: str = "",
                   security_critical: int = 0,
                   security_warnings: int = 0,
                   mcp_config=None) -> Tuple[bool, str, TokenUsage]:
    model = route_orchestrator("evaluate_phase")
    log_route("evaluate_phase", model, "moderate")

    done_tasks = [t for t in phase.tasks if t.status == "done"]
    task_summary = "\n".join(f"- {t.title}: {t.notes or 'completed'}" for t in done_tasks)
    arch = _read_doc(project_dir, "ARCHITECTURE.md")

    # Budget-aware truncation for architecture in phase review
    budget = ContextBudget(max_tokens=DEFAULT_PROMPT_BUDGET)
    budget.add(ContentBlock("phase", f"{phase.title}\n{phase.description}\n{task_summary}",
                            priority=1, truncatable=False))
    budget.add(ContentBlock("arch", arch, priority=3, truncatable=True))
    allocated = budget.allocate()

    # Include E2E results when available
    e2e_section = ""
    if e2e_passed is not None:
        e2e_status = "PASSED" if e2e_passed else "FAILED"
        e2e_section = f"\nE2E Test Results: {e2e_status}\n{e2e_summary[:500]}\n"

    # Include security scan results when available
    security_section = ""
    if security_critical > 0 or security_warnings > 0:
        security_section = (
            f"\nSecurity Scan: {security_critical} confirmed critical finding(s), "
            f"{security_warnings} warning(s)\n"
        )

    user = f"""
Phase: {phase.title}
Phase goal: {phase.description}

Completed tasks:
{task_summary}

Architecture:
{allocated["arch"]}
{e2e_section}{security_section}"""
    mcp_servers = mcp_config.to_api_format("phase_evaluation") if mcp_config else None
    result, usage = _json_chat(PHASE_QA_SYSTEM, user, model=model,
                               mcp_servers=mcp_servers or None)
    approved = result.get("approved", False)
    notes = result.get("notes", "")
    if result.get("blocking_issues"):
        notes += "\nBlocking issues:\n" + "\n".join(f"- {i}" for i in result["blocking_issues"])
    return approved, notes, usage


# ---------------------------------------------------------------------------
# Visual QA evaluation
# ---------------------------------------------------------------------------

VISION_SYSTEM_PROMPT = None  # imported lazily from visual_qa


def evaluate_visual_qa(
    task_title: str,
    task_description: str,
    screenshot_paths: list[Path],
) -> tuple[bool, str, TokenUsage]:
    """
    Evaluate screenshots using Claude Vision.

    This is a direct Anthropic API call (not via _chat) because it
    requires image content blocks in the message. Wrapped with retry.
    """
    from forge.visual_qa import encode_screenshot, VISION_SYSTEM_PROMPT as _VISION_PROMPT

    # Build content blocks
    content: list[dict] = []
    for path in screenshot_paths:
        b64 = encode_screenshot(path)
        if b64:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": b64,
                },
            })

    content.append({
        "type": "text",
        "text": f"Task: {task_title}\nRequirements: {task_description}\n\nEvaluate this UI implementation.",
    })

    # Route to opus for visual evaluation (high stakes)
    model = MODEL_OPUS
    last_error_str = ""
    last_prefix = "UNKNOWN"

    for attempt in range(MAX_RETRIES):
        try:
            response = _client().messages.create(
                model=model,
                max_tokens=512,
                system=_VISION_PROMPT,
                messages=[{"role": "user", "content": content}],
            )

            text = response.content[0].text
            usage = TokenUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                model=model,
            )

            passed = text.strip().upper().startswith("PASS")
            return passed, text.strip(), usage

        except (
            anthropic.AuthenticationError,
            anthropic.RateLimitError,
            anthropic.APIConnectionError,
            anthropic.APITimeoutError,
            anthropic.APIStatusError,
        ) as e:
            prefix, is_fatal, fix_instruction = _classify_anthropic_error(e)
            last_prefix = prefix
            last_error_str = str(e)

            if is_fatal:
                raise FatalAPIError(
                    error_prefix=prefix,
                    message=last_error_str,
                    fix_instruction=fix_instruction,
                )

            if attempt < MAX_RETRIES - 1:
                backoff = BACKOFF_SCHEDULE[min(attempt, len(BACKOFF_SCHEDULE) - 1)]
                wait_with_countdown(
                    backoff,
                    f"Visual QA retry {attempt + 1}/{MAX_RETRIES}",
                )

    raise RetryExhaustedError(
        error_prefix=last_prefix,
        attempts=MAX_RETRIES,
        last_error=last_error_str,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_doc(project_dir: Path, filename: str) -> str:
    path = project_dir / filename
    if path.exists():
        return path.read_text()
    return f"(No {filename} found)"
