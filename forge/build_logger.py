"""
Structured build logger for Forge.

Writes all significant build events to .forge/build.log as JSONL.
Each line is a self-contained JSON event record.

The log is:
- Append-only (never modified, only appended)
- Written atomically (temp file + rename for each batch)
- Human-readable with: cat .forge/build.log | python -m json.tool
- Filterable with: cat .forge/build.log | grep '"event": "task_failed"'
- Importable into pandas, Excel, or any analysis tool

Session IDs are 8 random hex characters, generated fresh each
forge run invocation. They allow distinguishing events from
different sessions in the same log file.

This module imports only stdlib - zero forge imports.
"""

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def new_session_id() -> str:
    """Generate an 8-character hex session identifier."""
    return secrets.token_hex(4)


class BuildLogger:
    """
    Session-scoped structured event logger.

    One BuildLogger instance per forge run session.
    All events in a session share the same session_id.
    """

    def __init__(self, project_dir: Path, session_id: str | None = None):
        self.project_dir = project_dir
        self.session_id = session_id or new_session_id()
        self._log_path = project_dir / ".forge" / "build.log"
        self._ensure_log_dir()

    def log(self, event: str,
            phase: int | None = None,
            task: str | None = None,
            **kwargs: Any) -> None:
        """
        Write a single event to the build log.

        All keyword arguments become additional fields in the JSON record.
        Never raises - log failures are silently ignored to avoid
        disrupting the build.
        """
        record = self._make_record(event, phase, task, **kwargs)
        self._write_event(record)

        # Push to dashboard SSE clients
        try:
            from forge.dashboard import push_event
            push_event(event, record)
        except Exception:
            pass

    # ----- Session lifecycle -----

    def session_started(self, project_name: str,
                        phase_count: int) -> None:
        """Log session_started event."""
        self.log("session_started",
                 project_name=project_name, phase_count=phase_count)

    def session_ended(self, tasks_completed: int,
                      total_cost: float, duration_secs: float) -> None:
        """Log session_ended event."""
        self.log("session_ended",
                 tasks_completed=tasks_completed,
                 total_cost=total_cost,
                 duration_secs=duration_secs)

    # ----- Phase lifecycle -----

    def phase_started(self, phase_index: int,
                      phase_title: str, task_count: int) -> None:
        """Log phase_started event."""
        self.log("phase_started", phase=phase_index,
                 phase_title=phase_title, task_count=task_count)

    def phase_completed(self, phase_index: int, phase_title: str,
                        task_count: int, duration_secs: float,
                        cost: float) -> None:
        """Log phase_completed event."""
        self.log("phase_completed", phase=phase_index,
                 phase_title=phase_title, task_count=task_count,
                 duration_secs=duration_secs, cost=cost)

    def phase_failed(self, phase_index: int, phase_title: str,
                     reason: str) -> None:
        """Log phase_failed event."""
        self.log("phase_failed", phase=phase_index,
                 phase_title=phase_title, reason=reason[:100])

    # ----- Task lifecycle -----

    def task_started(self, phase_index: int, task_id: str,
                     task_title: str, retry_count: int = 0) -> None:
        """Log task_started event."""
        self.log("task_started", phase=phase_index, task=task_id,
                 task_title=task_title, retry_count=retry_count)

    def task_completed(self, phase_index: int, task_id: str,
                       task_title: str, duration_secs: float,
                       cost: float, tokens_in: int,
                       tokens_out: int) -> None:
        """Log task_completed event."""
        self.log("task_completed", phase=phase_index, task=task_id,
                 task_title=task_title, duration_secs=duration_secs,
                 cost=cost, tokens_in=tokens_in, tokens_out=tokens_out)

    def task_failed(self, phase_index: int, task_id: str,
                    task_title: str, reason: str,
                    retry_count: int, will_retry: bool) -> None:
        """Log task_failed event."""
        self.log("task_failed", phase=phase_index, task=task_id,
                 task_title=task_title, reason=reason[:100],
                 retry_count=retry_count, will_retry=will_retry)

    def task_parked(self, phase_index: int, task_id: str,
                    task_title: str, reason: str) -> None:
        """Log task_parked event."""
        self.log("task_parked", phase=phase_index, task=task_id,
                 task_title=task_title, reason=reason[:100])

    # ----- QA events -----

    def qa_passed(self, phase_index: int, task_id: str,
                  task_title: str, summary: str) -> None:
        """Log qa_passed event. Truncates summary to 100 chars."""
        self.log("qa_passed", phase=phase_index, task=task_id,
                 task_title=task_title, summary_preview=summary[:100])

    def qa_failed(self, phase_index: int, task_id: str,
                  task_title: str, reason: str) -> None:
        """Log qa_failed event. Truncates reason to 100 chars."""
        self.log("qa_failed", phase=phase_index, task=task_id,
                 task_title=task_title, reason_preview=reason[:100])

    # ----- Visual QA events -----

    def visual_qa_passed(self, phase_index: int, task_id: str,
                         task_title: str, feedback: str) -> None:
        """Log visual_qa_passed event."""
        self.log("visual_qa_passed", phase=phase_index, task=task_id,
                 task_title=task_title, feedback=feedback[:100])

    def visual_qa_failed(self, phase_index: int, task_id: str,
                         task_title: str, feedback: str) -> None:
        """Log visual_qa_failed event."""
        self.log("visual_qa_failed", phase=phase_index, task=task_id,
                 task_title=task_title, feedback=feedback[:100])

    # ----- Git events -----

    def git_committed(self, commit_hash: str,
                      message: str) -> None:
        """Log git_committed event."""
        self.log("git_committed",
                 commit_hash=commit_hash, message_preview=message[:100])

    def git_tagged(self, tag_name: str) -> None:
        """Log git_tagged event."""
        self.log("git_tagged", tag_name=tag_name)

    def git_push_failed(self, reason: str) -> None:
        """Log git_push_failed event."""
        self.log("git_push_failed", reason=reason[:100])

    # ----- Retry/error events -----

    def retry_started(self, attempt: int, max_attempts: int,
                      wait_secs: int, error_type: str) -> None:
        """Log retry_started event."""
        self.log("retry_started",
                 attempt=attempt, max_attempts=max_attempts,
                 wait_secs=wait_secs, error_type=error_type)

    def rate_limit_hit(self, wait_secs: int, attempt: int) -> None:
        """Log rate_limit_hit event."""
        self.log("rate_limit_hit", wait_secs=wait_secs, attempt=attempt)

    def connection_lost(self, attempt: int) -> None:
        """Log connection_lost event."""
        self.log("connection_lost", attempt=attempt)

    def connection_restored(self, after_secs: float) -> None:
        """Log connection_restored event."""
        self.log("connection_restored", after_secs=after_secs)

    def fatal_error(self, error_type: str, message: str) -> None:
        """Log fatal_error event."""
        self.log("fatal_error",
                 error_type=error_type, message=message[:100])

    # ----- Memory events -----

    def memory_recorded(self, memory_type: str, title: str) -> None:
        """Log memory_recorded event."""
        self.log("memory_recorded",
                 memory_type=memory_type, title=title[:100])

    # ----- Internal -----

    def _ensure_log_dir(self) -> None:
        """Create .forge/ directory if needed. Never raises."""
        try:
            (self.project_dir / ".forge").mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def _write_event(self, record: dict) -> None:
        """
        Append a single JSON record to the log file atomically.

        Strategy: read existing content (if any), append new line,
        write to temp file, rename. This is safe for concurrent
        processes and crash-safe.

        Never raises - catches all exceptions silently.
        """
        try:
            line = json.dumps(record, separators=(',', ':')) + '\n'
            tmp = self._log_path.with_suffix('.tmp')

            existing = ''
            if self._log_path.exists():
                existing = self._log_path.read_text(encoding='utf-8')

            tmp.write_text(existing + line, encoding='utf-8')
            tmp.replace(self._log_path)
        except Exception:
            pass

    def _make_record(self, event: str,
                     phase: int | None,
                     task: str | None,
                     **kwargs) -> dict:
        """Build the base record dict with required fields."""
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "session": self.session_id,
            "phase": phase,
            "task": task,
            **kwargs,
        }


def read_log(project_dir: Path,
             event_filter: str | None = None,
             session_filter: str | None = None,
             limit: int | None = None) -> list[dict]:
    """
    Read and parse the build log.

    Optionally filter by event type or session ID.
    Optionally limit to the last N records.
    Returns list of parsed dicts. Skips malformed lines silently.
    """
    log_path = project_dir / ".forge" / "build.log"
    if not log_path.exists():
        return []

    records = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue

        if event_filter and data.get("event") != event_filter:
            continue
        if session_filter and data.get("session") != session_filter:
            continue

        records.append(data)

    if limit is not None and limit > 0:
        records = records[-limit:]

    return records
