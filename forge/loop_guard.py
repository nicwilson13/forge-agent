"""
Loop Guard
Detects when Forge is stuck retrying the same task without progress
and escalates to NEEDS_HUMAN.
"""

from collections import defaultdict
from typing import Dict


class LoopGuard:
    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries
        self._fail_counts: Dict[str, int] = defaultdict(int)
        self._last_errors: Dict[str, str] = {}

    def record_failure(self, task_id: str, error_summary: str):
        self._fail_counts[task_id] += 1
        self._last_errors[task_id] = error_summary

    def record_success(self, task_id: str):
        self._fail_counts[task_id] = 0

    def is_stuck(self, task_id: str) -> bool:
        return self._fail_counts[task_id] >= self.max_retries

    def park_reason(self, task_id: str) -> str:
        count = self._fail_counts[task_id]
        last = self._last_errors.get(task_id, "unknown error")
        return (
            f"Failed {count} times in a row. "
            f"Last error: {last[:300]}. "
            f"Human review required."
        )

    def reset(self, task_id: str):
        self._fail_counts[task_id] = 0
        self._last_errors.pop(task_id, None)
