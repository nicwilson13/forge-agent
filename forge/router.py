"""
Model routing for Forge.

Assigns the right Claude model to each orchestrator function and
builder task based on complexity, stakes, and failure history.

Routing tiers:
  Opus   - high stakes (QA evaluation, architecture, complex tasks)
  Sonnet - moderate complexity (task generation, most builder tasks)
  Haiku  - low stakes (phase listing, documentation tasks, simple work)

Escalation: after 2 failures on assigned model, escalate to next tier.

Imports only cost_tracker for model constants - no other forge imports.
"""

import os
import sys

from forge.cost_tracker import MODEL_OPUS, MODEL_SONNET, MODEL_HAIKU


def _supports_unicode() -> bool:
    """Check if stdout encoding supports Unicode."""
    encoding = getattr(sys.stdout, "encoding", "") or ""
    return encoding.lower().replace("-", "") in (
        "utf8", "utf16", "utf32", "utf8sig",
    )


SYM_ARROW = "\u2192" if _supports_unicode() else "->"


ROUTING_RULES: dict[str, str] = {
    "generate_phases": MODEL_HAIKU,
    "generate_tasks": MODEL_SONNET,
    "evaluate_qa": MODEL_OPUS,
    "write_architecture": MODEL_OPUS,
    "evaluate_phase": MODEL_SONNET,
}

HIGH_COMPLEXITY_SIGNALS: list[str] = [
    "payment", "stripe", "billing", "subscription",
    "auth", "oauth", "jwt", "security", "encrypt",
    "database schema", "data model", "migration",
    "architecture", "refactor", "performance",
    "core", "foundation", "critical",
]

LOW_COMPLEXITY_SIGNALS: list[str] = [
    "readme", "documentation", "comment", "docstring",
    "rename", "move file", "update config",
    "add test", "fix typo", "update copy",
    "favicon", "logo", "placeholder",
    "env example", ".gitignore",
]

# Ordered lowest to highest
MODEL_TIERS: list[str] = [MODEL_HAIKU, MODEL_SONNET, MODEL_OPUS]


def route_orchestrator(function_name: str) -> str:
    """
    Return the model to use for an orchestrator function.

    Looks up function_name in ROUTING_RULES.
    Falls back to MODEL_OPUS for unknown functions (safe default).
    """
    return ROUTING_RULES.get(function_name, MODEL_OPUS)


def route_task(task_title: str,
               task_description: str,
               retry_count: int = 0,
               previous_model: str | None = None) -> tuple[str, str]:
    """
    Return (model, reason) for a builder task.

    1. If retry_count >= 2 and previous_model is set, escalate first.
    2. Check title+description against HIGH_COMPLEXITY_SIGNALS (wins over low).
    3. Check against LOW_COMPLEXITY_SIGNALS.
    4. Default: Sonnet.
    """
    # Escalation takes priority
    if retry_count >= 2 and previous_model:
        escalated = escalate_model(previous_model)
        if escalated is not None:
            return escalated, f"escalated from {_model_short(previous_model)} after {retry_count} failures"

    combined = (task_title + " " + task_description).lower()

    # High complexity signals take priority over low
    for signal in HIGH_COMPLEXITY_SIGNALS:
        if signal in combined:
            return MODEL_OPUS, f"complexity signal: {signal}"

    for signal in LOW_COMPLEXITY_SIGNALS:
        if signal in combined:
            return MODEL_HAIKU, f"complexity signal: {signal}"

    return MODEL_SONNET, "default"


def escalate_model(current_model: str) -> str | None:
    """
    Return the next model tier above current_model.
    Returns None if already at the highest tier (Opus).
    """
    try:
        idx = MODEL_TIERS.index(current_model)
    except ValueError:
        return None
    if idx >= len(MODEL_TIERS) - 1:
        return None
    return MODEL_TIERS[idx + 1]


def log_route(operation: str, model: str, reason: str) -> None:
    """
    Print a routing log line to stdout.

    Only prints if stdout is a tty or FORGE_VERBOSE env var is set.
    """
    if not (sys.stdout.isatty() or os.environ.get("FORGE_VERBOSE")):
        return
    short = _model_short(model)
    print(f"  [route] {operation:<24} {SYM_ARROW} {short:<8}  ({reason})")


def _model_short(model: str) -> str:
    """Return a short display name for a model identifier."""
    if model == MODEL_HAIKU:
        return "haiku"
    if model == MODEL_SONNET:
        return "sonnet"
    if model == MODEL_OPUS:
        return "opus"
    return model
