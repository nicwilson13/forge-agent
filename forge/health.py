"""
Build health metrics for Forge.

Computes health metrics from .forge/build.log and .forge/cost_log.jsonl.
Produces a letter grade (A-F) and detailed breakdown of session and
project-level metrics.

All computation is pure: takes file paths, returns structured data.
No side effects, no terminal output.

Imports only stdlib and forge.build_logger.read_log.
"""

import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

def _supports_unicode() -> bool:
    encoding = getattr(sys.stdout, "encoding", "") or ""
    return encoding.lower().replace("-", "") in ("utf8", "utf16", "utf32", "utf8sig")

_UNICODE = _supports_unicode()
_HEAVY = "\u2550" if _UNICODE else "="
_LIGHT = "\u2500" if _UNICODE else "-"
_WARN = "\u26a0" if _UNICODE else "[WARN]"


@dataclass
class SessionMetrics:
    tasks_attempted: int = 0
    tasks_first_pass: int = 0
    tasks_retried: int = 0
    tasks_failed: int = 0
    success_rate: float = 0.0
    retry_rate: float = 0.0
    avg_task_duration: float = 0.0
    avg_task_cost: float = 0.0
    total_cost: float = 0.0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    session_id: str = ""


@dataclass
class ProjectMetrics:
    total_cost: float = 0.0
    total_build_secs: float = 0.0
    overall_success_rate: float = 0.0
    most_expensive_phase: str | None = None
    slowest_phase: str | None = None
    retry_hotspots: list = field(default_factory=list)
    cost_trend: str = "not enough data"
    session_count: int = 0
    total_tasks: int = 0
    cost_per_session: list = field(default_factory=list)


@dataclass
class HealthReport:
    grade: str = "?"
    session: SessionMetrics = field(default_factory=SessionMetrics)
    project: ProjectMetrics = field(default_factory=ProjectMetrics)


def compute_session_metrics(project_dir: Path,
                            session_id: str) -> SessionMetrics | None:
    """
    Compute metrics for a specific session from build.log.

    Uses task_started, task_completed, task_failed events filtered
    by session_id to compute all session-level metrics.
    Returns None if no data found for session_id.
    """
    from forge.build_logger import read_log

    events = read_log(project_dir, session_filter=session_id)
    if not events:
        return None

    # Track per-task: which tasks were started, completed, failed
    task_started_ids = set()
    task_completed_ids = set()
    task_failed_ids = set()
    durations = []
    costs = []
    total_tokens_in = 0
    total_tokens_out = 0

    for e in events:
        evt = e.get("event", "")
        tid = e.get("task")
        if evt == "task_started" and tid:
            task_started_ids.add(tid)
        elif evt == "task_completed" and tid:
            task_completed_ids.add(tid)
            durations.append(e.get("duration_secs", 0))
            costs.append(e.get("cost", 0))
            total_tokens_in += e.get("tokens_in", 0)
            total_tokens_out += e.get("tokens_out", 0)
        elif evt == "task_failed" and tid:
            task_failed_ids.add(tid)

    tasks_attempted = len(task_started_ids)
    if tasks_attempted == 0:
        return None

    # First pass = completed without ever appearing in failed
    tasks_first_pass = len(task_completed_ids - task_failed_ids)
    tasks_retried = len(task_completed_ids & task_failed_ids)
    tasks_ultimately_failed = len(task_failed_ids - task_completed_ids)

    success_rate = tasks_first_pass / tasks_attempted if tasks_attempted else 0.0
    retry_rate = tasks_retried / tasks_attempted if tasks_attempted else 0.0
    avg_duration = sum(durations) / len(durations) if durations else 0.0
    avg_cost = sum(costs) / len(costs) if costs else 0.0
    total_cost = sum(costs)

    return SessionMetrics(
        tasks_attempted=tasks_attempted,
        tasks_first_pass=tasks_first_pass,
        tasks_retried=tasks_retried,
        tasks_failed=tasks_ultimately_failed,
        success_rate=success_rate,
        retry_rate=retry_rate,
        avg_task_duration=avg_duration,
        avg_task_cost=avg_cost,
        total_cost=total_cost,
        total_tokens_in=total_tokens_in,
        total_tokens_out=total_tokens_out,
        session_id=session_id,
    )


def compute_project_metrics(project_dir: Path) -> ProjectMetrics:
    """
    Compute project-wide metrics from cost_log.jsonl and build.log.

    Aggregates across all sessions.
    """
    cost_records = _load_cost_records(project_dir)
    build_events = _load_build_events(project_dir)

    total_cost = sum(r.get("total_cost", 0) for r in cost_records)
    total_build_secs = sum(r.get("duration_secs", 0) for r in cost_records)
    total_tasks = len(cost_records)

    # Overall success rate from build events
    task_completed_count = sum(1 for e in build_events if e.get("event") == "task_completed")
    task_failed_count = sum(1 for e in build_events if e.get("event") == "task_failed")
    total_attempts = task_completed_count + task_failed_count
    overall_success_rate = task_completed_count / total_attempts if total_attempts else 0.0

    # Most expensive phase
    phase_costs: dict[str, float] = {}
    phase_durations: dict[str, list[float]] = {}
    for r in cost_records:
        pt = r.get("phase_title", "Unknown")
        phase_costs[pt] = phase_costs.get(pt, 0) + r.get("total_cost", 0)
        phase_durations.setdefault(pt, []).append(r.get("duration_secs", 0))

    most_expensive_phase = None
    if phase_costs:
        most_expensive_phase = max(phase_costs, key=phase_costs.get)

    # Slowest phase (by avg task duration)
    slowest_phase = None
    if phase_durations:
        avg_by_phase = {
            pt: sum(durs) / len(durs) for pt, durs in phase_durations.items()
        }
        slowest_phase = max(avg_by_phase, key=avg_by_phase.get)

    # Retry hotspots: tasks with 3+ task_failed events
    failure_counter: Counter = Counter()
    for e in build_events:
        if e.get("event") == "task_failed":
            title = e.get("task_title", "")
            if title:
                failure_counter[title] += 1
    retry_hotspots = [
        (title, count) for title, count in failure_counter.most_common()
        if count >= 3
    ]

    # Cost trend: compare avg cost/task first half vs second half of sessions
    session_ids = []
    seen = set()
    for e in build_events:
        if e.get("event") == "session_started":
            sid = e.get("session", "")
            if sid and sid not in seen:
                session_ids.append(sid)
                seen.add(sid)

    cost_per_session = []
    for sid in session_ids:
        session_costs = [
            r.get("cost", 0) for r in build_events
            if r.get("session") == sid and r.get("event") == "task_completed"
        ]
        if session_costs:
            cost_per_session.append(sum(session_costs) / len(session_costs))
        else:
            cost_per_session.append(0.0)

    cost_trend = "not enough data"
    if len(cost_per_session) >= 2:
        mid = len(cost_per_session) // 2
        first_half_avg = sum(cost_per_session[:mid]) / mid if mid else 0
        second_half_avg = sum(cost_per_session[mid:]) / (len(cost_per_session) - mid)
        if first_half_avg > 0:
            change = (second_half_avg - first_half_avg) / first_half_avg
            if change > 0.10:
                cost_trend = "increasing"
            elif change < -0.10:
                cost_trend = "decreasing"
            else:
                cost_trend = "stable"
        else:
            cost_trend = "stable"

    return ProjectMetrics(
        total_cost=total_cost,
        total_build_secs=total_build_secs,
        overall_success_rate=overall_success_rate,
        most_expensive_phase=most_expensive_phase,
        slowest_phase=slowest_phase,
        retry_hotspots=retry_hotspots,
        cost_trend=cost_trend,
        session_count=len(session_ids),
        total_tasks=total_tasks,
        cost_per_session=cost_per_session,
    )


def grade_health(session: SessionMetrics) -> str:
    """
    Compute letter grade from session metrics.

    A: success_rate >= 95%, retry_rate <= 5%,  avg_task_cost <= $0.05
    B: success_rate >= 85%, retry_rate <= 15%, avg_task_cost <= $0.10
    C: success_rate >= 70%, retry_rate <= 30%, avg_task_cost <= $0.20
    D: success_rate >= 50%
    F: success_rate < 50%
    """
    sr = session.success_rate
    rr = session.retry_rate
    ac = session.avg_task_cost

    if sr >= 0.95 and rr <= 0.05 and ac <= 0.05:
        return "A"
    if sr >= 0.85 and rr <= 0.15 and ac <= 0.10:
        return "B"
    if sr >= 0.70 and rr <= 0.30 and ac <= 0.20:
        return "C"
    if sr >= 0.50:
        return "D"
    return "F"


def compute_health_report(project_dir: Path,
                          session_id: str) -> HealthReport:
    """
    Compute full health report for display.

    Calls compute_session_metrics and compute_project_metrics,
    then grades the session. Returns HealthReport.
    If no session data: returns a minimal report with grade "?"
    and empty metrics.

    Never raises.
    """
    try:
        session = compute_session_metrics(project_dir, session_id)
        project = compute_project_metrics(project_dir)

        if session is None:
            return HealthReport(
                grade="?",
                session=SessionMetrics(session_id=session_id),
                project=project,
            )

        grade = grade_health(session)
        return HealthReport(grade=grade, session=session, project=project)
    except Exception:
        return HealthReport()


def format_health_report(report: HealthReport) -> str:
    """
    Format the full health report as a multi-line string.

    Used by forge status --health.
    """
    lines = []
    lines.append("")
    lines.append("  Health Report")
    lines.append("  " + _HEAVY * 56)
    lines.append("")
    lines.append(f"  Build Health: {report.grade}")

    s = report.session
    if s.tasks_attempted > 0:
        lines.append("")
        lines.append("  Session (current)")
        lines.append("  " + _LIGHT * 56)
        sr_pct = int(s.success_rate * 100)
        rr_pct = int(s.retry_rate * 100)
        lines.append(f"  Success rate:  {sr_pct:>3d}%  ({s.tasks_first_pass}/{s.tasks_attempted} tasks passed first attempt)")
        lines.append(f"  Retry rate:    {rr_pct:>3d}%  ({s.tasks_retried} task{'s' if s.tasks_retried != 1 else ''} needed a retry)")
        lines.append(f"  Avg task time: {_format_duration(s.avg_task_duration)}")
        lines.append(f"  Avg task cost: {_format_cost(s.avg_task_cost)}")
        lines.append(f"  Session cost:  {_format_cost(s.total_cost)}")
        total_tokens = s.total_tokens_in + s.total_tokens_out
        lines.append(f"  Session tokens: {total_tokens:,}")

    p = report.project
    if p.session_count > 0:
        lines.append("")
        lines.append("  Project (all sessions)")
        lines.append("  " + _LIGHT * 56)
        lines.append(f"  Total cost:    {_format_cost(p.total_cost)}  across {p.session_count} session{'s' if p.session_count != 1 else ''}")
        lines.append(f"  Total time:    {_format_duration(p.total_build_secs)}  across {p.total_tasks} tasks")
        osr_pct = int(p.overall_success_rate * 100)
        lines.append(f"  Success rate:  {osr_pct:>3d}%   overall")

        # Cost trend with per-session values
        if p.cost_per_session and len(p.cost_per_session) >= 2:
            trend_vals = " -> ".join(
                _format_cost(c) for c in p.cost_per_session[-3:]
            )
            lines.append(f"  Cost trend:    {p.cost_trend}  ({trend_vals} per task)")
        else:
            lines.append(f"  Cost trend:    {p.cost_trend}")

        lines.append("")
        if p.most_expensive_phase:
            lines.append(f"  Most expensive phase:  {p.most_expensive_phase}")
        if p.slowest_phase:
            lines.append(f"  Slowest phase:         {p.slowest_phase}")

        lines.append("")
        if p.retry_hotspots:
            lines.append("  Retry hotspots:")
            for title, count in p.retry_hotspots:
                lines.append(f"  {_WARN}  \"{title}\"  failed {count}x")
        else:
            lines.append("  Retry hotspots:  none")

    lines.append("")
    return "\n".join(lines)


def format_health_summary_line(report: HealthReport) -> str:
    """
    Format the single-line health summary for end-of-session display.

    Example: "Build Health: B  |  91% success  |  $0.41  |  47s avg/task"
    """
    s = report.session
    sr_pct = int(s.success_rate * 100) if s.tasks_attempted else 0
    cost_str = _format_cost(s.total_cost)
    dur_str = _format_duration(s.avg_task_duration)
    return f"Build Health: {report.grade}  |  {sr_pct}% success  |  {cost_str}  |  {dur_str} avg/task"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_cost_records(project_dir: Path) -> list[dict]:
    """Load all records from cost_log.jsonl. Returns [] on error."""
    log_path = project_dir / ".forge" / "cost_log.jsonl"
    if not log_path.exists():
        return []
    records = []
    try:
        for line in log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except (json.JSONDecodeError, TypeError):
                continue
    except Exception:
        pass
    return records


def _load_build_events(project_dir: Path,
                       event_types: list[str] | None = None) -> list[dict]:
    """Load build log events, optionally filtered by type."""
    from forge.build_logger import read_log
    events = read_log(project_dir)
    if event_types:
        events = [e for e in events if e.get("event") in event_types]
    return events


def _format_duration(seconds: float) -> str:
    """Format seconds as human-readable: '0s', '47s', '3m 12s', '1h 4m'."""
    seconds = max(0, int(seconds))
    if seconds == 0:
        return "0s"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m {s}s" if s else f"{m}m"
    h, remainder = divmod(seconds, 3600)
    m = remainder // 60
    return f"{h}h {m}m"


def _format_cost(amount: float) -> str:
    """Format cost: 3 decimal places under $1, 2 at/above $1."""
    if amount < 1.00:
        return f"${amount:.3f}"
    return f"${amount:.2f}"
