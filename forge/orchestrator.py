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
from forge.memory import load_memory_context, ensure_memory_dir
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


def _chat(system: str, user: str, max_tokens: int = 4096) -> str:
    last_error_str = ""
    last_prefix = "UNKNOWN"

    for attempt in range(MAX_RETRIES):
        try:
            resp = _client().messages.create(
                model="claude-opus-4-5",
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return resp.content[0].text.strip()
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


def _json_chat(system: str, user: str, max_tokens: int = 4096) -> dict | list:
    """Call API and parse JSON from the response."""
    system_with_json = system + "\n\nYou MUST respond with valid JSON only. No prose, no markdown fences."
    raw = _chat(system_with_json, user, max_tokens)
    # Strip accidental markdown fences
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    return json.loads(raw)


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


def generate_phases(project_dir: Path) -> List[Phase]:
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
    raw_phases = _json_chat(PHASE_SYSTEM, user)
    return [Phase.new(p["title"], p["description"]) for p in raw_phases]


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

Return a JSON array:
[
  {
    "title": "Short task title",
    "description": "Detailed instructions for the AI coding agent.",
    "needs_human": false
  },
  ...
]
"""


def generate_tasks(project_dir: Path, phase: Phase, state: ForgeState) -> List[Task]:
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
"""
    raw_tasks = _json_chat(TASK_SYSTEM, user)
    tasks = []
    for t in raw_tasks:
        desc = t["description"]
        task = Task.new(t["title"], desc, phase.id)
        # Pre-flag NEEDS_HUMAN tasks
        if t.get("needs_human") or desc.strip().upper().startswith("NEEDS_HUMAN"):
            task.park_reason = desc.split("\n")[0].replace("NEEDS_HUMAN:", "").strip()
        tasks.append(task)
    return tasks


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


def write_architecture(project_dir: Path, phases: List[Phase]):
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
    arch_content = _chat(ARCH_SYSTEM, user)
    arch_path = project_dir / "ARCHITECTURE.md"
    arch_path.write_text(arch_content)
    print(f"  [forge] Wrote ARCHITECTURE.md")


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
    budget.add(ContentBlock("skills", "", priority=7, truncatable=True))

    allocated = budget.allocate()

    return BUILDER_SYSTEM_TEMPLATE.format(
        vision=allocated["vision"],
        arch=allocated["arch"],
        claude_md=allocated["claude"],
        memory_section=allocated.get("memory", ""),
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


def evaluate_qa(task: Task, test_output: str, error_output: str) -> Tuple[bool, str, str]:
    user = f"""
Task: {task.title}
Description: {task.description}

Test output:
{test_output[-3000:] if test_output else 'No test output'}

Errors:
{error_output[-2000:] if error_output else 'None'}
"""
    result = _json_chat(QA_EVAL_SYSTEM, user)
    passed = result.get("passed", False)
    summary = result.get("summary", "")
    retry_prompt = result.get("retry_prompt") or ""
    return passed, summary, retry_prompt


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


def evaluate_phase(project_dir: Path, phase: Phase) -> Tuple[bool, str]:
    done_tasks = [t for t in phase.tasks if t.status == "done"]
    task_summary = "\n".join(f"- {t.title}: {t.notes or 'completed'}" for t in done_tasks)
    arch = _read_doc(project_dir, "ARCHITECTURE.md")

    # Budget-aware truncation for architecture in phase review
    budget = ContextBudget(max_tokens=DEFAULT_PROMPT_BUDGET)
    budget.add(ContentBlock("phase", f"{phase.title}\n{phase.description}\n{task_summary}",
                            priority=1, truncatable=False))
    budget.add(ContentBlock("arch", arch, priority=3, truncatable=True))
    allocated = budget.allocate()

    user = f"""
Phase: {phase.title}
Phase goal: {phase.description}

Completed tasks:
{task_summary}

Architecture:
{allocated["arch"]}
"""
    result = _json_chat(PHASE_QA_SYSTEM, user)
    approved = result.get("approved", False)
    notes = result.get("notes", "")
    if result.get("blocking_issues"):
        notes += "\nBlocking issues:\n" + "\n".join(f"- {i}" for i in result["blocking_issues"])
    return approved, notes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_doc(project_dir: Path, filename: str) -> str:
    path = project_dir / filename
    if path.exists():
        return path.read_text()
    return f"(No {filename} found)"
