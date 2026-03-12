"""
Cost tracking for Forge build sessions.

Tracks token usage and estimated costs at the task, phase, and
session level. Logs all usage to .forge/cost_log.jsonl for later
analysis.

Pricing is based on Anthropic's published per-million-token rates.
Builder (Claude Code) costs are estimated from context budget
calculations since the Claude Code SDK does not expose token counts.

This module imports only context_budget.estimate_tokens from forge.
"""

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

from forge.context_budget import CHARS_PER_TOKEN

# Model identifiers
MODEL_OPUS = "claude-opus-4-5"
MODEL_SONNET = "claude-sonnet-4-6"
MODEL_HAIKU = "claude-haiku-4-5-20251001"

# Pricing per million tokens (USD)
# Source: console.anthropic.com/settings/billing
# Update these when Anthropic changes pricing
PRICING = {
    MODEL_OPUS:   {"input": 15.00, "output": 75.00},
    MODEL_SONNET: {"input":  3.00, "output": 15.00},
    MODEL_HAIKU:  {"input":  0.80, "output":  4.00},
}

# Default alert thresholds
DEFAULT_TASK_TOKEN_ALERT = 40_000
DEFAULT_SESSION_COST_ALERT = 5.00


def _format_cost(amount: float) -> str:
    """Format cost: 3 decimal places under $1, 2 at/above $1."""
    if amount < 1.00:
        return f"${amount:.3f}"
    return f"${amount:.2f}"


def _accumulate(usages: list) -> "TokenUsage":
    """Sum multiple TokenUsage records into one."""
    total_input = sum(u.input_tokens for u in usages)
    total_output = sum(u.output_tokens for u in usages)
    model = usages[0].model if usages else MODEL_OPUS
    return TokenUsage(total_input, total_output, model)


@dataclass
class TokenUsage:
    """Token counts for a single API call."""
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = MODEL_OPUS

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def estimated_cost(self) -> float:
        """Return estimated cost in USD. Local models (ollama:*) are free."""
        if self.model.startswith("ollama:"):
            return 0.0
        pricing = PRICING.get(self.model, PRICING[MODEL_OPUS])
        input_cost = self.input_tokens / 1_000_000 * pricing["input"]
        output_cost = self.output_tokens / 1_000_000 * pricing["output"]
        return input_cost + output_cost


@dataclass
class TaskCost:
    """Cost record for a single completed task."""
    task_id: str
    task_title: str
    phase_index: int
    phase_title: str
    timestamp: str
    duration_secs: float
    orchestrator: TokenUsage
    builder: TokenUsage
    total_cost: float

    def to_dict(self) -> dict:
        """Serialize to dict for JSONL logging."""
        return {
            "task_id": self.task_id,
            "task_title": self.task_title,
            "phase_index": self.phase_index,
            "phase_title": self.phase_title,
            "timestamp": self.timestamp,
            "duration_secs": self.duration_secs,
            "orchestrator": {
                "input_tokens": self.orchestrator.input_tokens,
                "output_tokens": self.orchestrator.output_tokens,
                "model": self.orchestrator.model,
            },
            "builder": {
                "input_tokens": self.builder.input_tokens,
                "output_tokens": self.builder.output_tokens,
                "model": self.builder.model,
            },
            "total_cost": self.total_cost,
        }


def calculate_task_cost(
    task_id: str,
    task_title: str,
    phase_index: int,
    phase_title: str,
    duration_secs: float,
    orchestrator_usage: TokenUsage,
    builder_prompt_chars: int,
    builder_output_chars: int,
) -> TaskCost:
    """
    Calculate the full cost for a completed task.

    builder_prompt_chars and builder_output_chars are character counts.
    Uses CHARS_PER_TOKEN estimate and MODEL_SONNET pricing for builder.
    """
    builder_input = max(1, builder_prompt_chars // CHARS_PER_TOKEN)
    builder_output = max(1, builder_output_chars // CHARS_PER_TOKEN)
    builder_usage = TokenUsage(builder_input, builder_output, MODEL_SONNET)

    total = orchestrator_usage.estimated_cost + builder_usage.estimated_cost

    return TaskCost(
        task_id=task_id,
        task_title=task_title,
        phase_index=phase_index,
        phase_title=phase_title,
        timestamp=datetime.utcnow().isoformat(),
        duration_secs=duration_secs,
        orchestrator=orchestrator_usage,
        builder=builder_usage,
        total_cost=total,
    )


class CostTracker:
    """
    Session-level cost accumulator.

    Tracks token usage across all tasks in the current session.
    Writes each task record to .forge/cost_log.jsonl on completion.
    """

    def __init__(
        self,
        project_dir: Path,
        task_token_alert: int = DEFAULT_TASK_TOKEN_ALERT,
        session_cost_alert: float = DEFAULT_SESSION_COST_ALERT,
    ):
        self.project_dir = project_dir
        self.task_token_alert = task_token_alert
        self.session_cost_alert = session_cost_alert
        self._records: list[TaskCost] = []
        self._session_start = datetime.now()
        self._session_alert_fired = False

    def record_task(self, task_cost: TaskCost) -> list[str]:
        """
        Record a completed task's cost.

        Appends to records and writes to .forge/cost_log.jsonl.
        Returns list of alert messages (empty if no alerts triggered).
        """
        self._records.append(task_cost)
        self._append_to_log(task_cost)

        alerts = []

        # Task token alert
        total_task_tokens = (
            task_cost.orchestrator.total_tokens +
            task_cost.builder.total_tokens
        )
        if total_task_tokens > self.task_token_alert:
            alerts.append(
                f"High token usage: Task used {total_task_tokens:,} tokens "
                f"(threshold: {self.task_token_alert:,})\n"
                f"    Consider simplifying this task in REQUIREMENTS.md if this recurs."
            )

        # Session cost alert (fire once)
        session_total = self.session_total_cost()
        if session_total > self.session_cost_alert and not self._session_alert_fired:
            self._session_alert_fired = True
            alerts.append(
                f"Session cost alert: {_format_cost(session_total)} spent "
                f"(threshold: {_format_cost(self.session_cost_alert)})\n"
                f"    Run `forge status --cost` to review spending by phase."
            )

        return alerts

    def session_total_cost(self) -> float:
        """Return sum of all task costs in this session."""
        return sum(r.total_cost for r in self._records)

    def session_total_tokens(self) -> tuple[int, int]:
        """Return (total_input, total_output) across all tasks."""
        total_in = sum(
            r.orchestrator.input_tokens + r.builder.input_tokens
            for r in self._records
        )
        total_out = sum(
            r.orchestrator.output_tokens + r.builder.output_tokens
            for r in self._records
        )
        return total_in, total_out

    def phase_summary(self, phase_index: int) -> dict:
        """
        Return cost summary for a specific phase.

        Dict keys: tasks, input_tokens, output_tokens, cost, duration_secs
        """
        phase_records = [r for r in self._records if r.phase_index == phase_index]
        input_tokens = sum(
            r.orchestrator.input_tokens + r.builder.input_tokens
            for r in phase_records
        )
        output_tokens = sum(
            r.orchestrator.output_tokens + r.builder.output_tokens
            for r in phase_records
        )
        cost = sum(r.total_cost for r in phase_records)
        duration = sum(r.duration_secs for r in phase_records)
        return {
            "tasks": len(phase_records),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost": cost,
            "duration_secs": duration,
        }

    def format_task_line(self, task_cost: TaskCost) -> str:
        """Format the per-task cost line for terminal output."""
        total_in = task_cost.orchestrator.input_tokens + task_cost.builder.input_tokens
        total_out = task_cost.orchestrator.output_tokens + task_cost.builder.output_tokens
        cost_str = _format_cost(task_cost.total_cost)
        return f"Tokens: {total_in:,} in / {total_out:,} out  Cost: {cost_str}"

    def format_session_summary(self) -> str:
        """Format the full session summary for display on exit."""
        total_in, total_out = self.session_total_tokens()
        total_cost = self.session_total_cost()
        lines = [
            f"  Total tokens: {total_in:,} in / {total_out:,} out",
            f"  Total cost: {_format_cost(total_cost)}",
            f"  Saved to: .forge/cost_log.jsonl",
        ]
        return "\n".join(lines)

    def format_cost_report(self, state) -> str:
        """Format the forge status --cost report."""
        if not self._records:
            return "  No cost data yet."

        lines = ["", "  Cost Report"]
        divider = "  " + "\u2500" * 50
        lines.append(divider)

        # Group by phase
        phase_indices = sorted(set(r.phase_index for r in self._records))
        for pi in phase_indices:
            summary = self.phase_summary(pi)
            # Get phase title from first record with this index
            title = next(
                (r.phase_title for r in self._records if r.phase_index == pi),
                f"Phase {pi + 1}",
            )
            dur_m = int(summary["duration_secs"]) // 60
            cost_str = _format_cost(summary["cost"])
            tasks_str = f"{summary['tasks']} tasks"
            lines.append(
                f"  Phase {pi + 1}  {title:<28s} {cost_str:>8s}   {tasks_str:>8s}   {dur_m}m"
            )

        lines.append(divider)
        total_cost = _format_cost(self.session_total_cost())
        total_tasks = len(self._records)
        total_dur = int(sum(r.duration_secs for r in self._records)) // 60
        lines.append(
            f"  Session total{' ' * 29}{total_cost:>8s}   {total_tasks:>5} tasks   {total_dur}m"
        )
        lines.append("")
        return "\n".join(lines)

    def load_from_log(self) -> None:
        """Load cost records from .forge/cost_log.jsonl if it exists."""
        log_path = self.project_dir / ".forge" / "cost_log.jsonl"
        if not log_path.exists():
            return

        for line in log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                orch = data.get("orchestrator", {})
                bldr = data.get("builder", {})
                record = TaskCost(
                    task_id=data.get("task_id", ""),
                    task_title=data.get("task_title", ""),
                    phase_index=data.get("phase_index", 0),
                    phase_title=data.get("phase_title", ""),
                    timestamp=data.get("timestamp", ""),
                    duration_secs=data.get("duration_secs", 0),
                    orchestrator=TokenUsage(
                        input_tokens=orch.get("input_tokens", 0),
                        output_tokens=orch.get("output_tokens", 0),
                        model=orch.get("model", MODEL_OPUS),
                    ),
                    builder=TokenUsage(
                        input_tokens=bldr.get("input_tokens", 0),
                        output_tokens=bldr.get("output_tokens", 0),
                        model=bldr.get("model", MODEL_SONNET),
                    ),
                    total_cost=data.get("total_cost", 0),
                )
                self._records.append(record)
            except (json.JSONDecodeError, KeyError, TypeError):
                # Skip malformed lines
                continue

    def _append_to_log(self, task_cost: TaskCost) -> None:
        """Append task_cost as a JSON line to .forge/cost_log.jsonl."""
        log_dir = self.project_dir / ".forge"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "cost_log.jsonl"

        # Read existing content
        existing = ""
        if log_path.exists():
            existing = log_path.read_text(encoding="utf-8")

        new_line = json.dumps(task_cost.to_dict(), default=str) + "\n"
        new_content = existing + new_line

        # Atomic write
        tmp = log_path.with_suffix(".tmp")
        try:
            tmp.write_text(new_content, encoding="utf-8")
            tmp.replace(log_path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
