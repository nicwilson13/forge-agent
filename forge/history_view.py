"""
History view for the Forge dashboard.

Shows past build records with expandable details and log entries.
This module imports only stdlib. No forge imports at module level.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from forge.nav_shell import page_shell


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _send_json(handler, data: dict, status: int = 200) -> None:
    """Send a JSON response."""
    body = json.dumps(data).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


# ---------------------------------------------------------------------------
# Data layer
# ---------------------------------------------------------------------------

def save_build_record(
    project_dir: Path,
    state,
    health_grade: str,
    total_cost: float,
    duration_seconds: int,
    vercel_url: str = "",
    github_pr: str = "",
) -> None:
    """
    Write a build record to .forge/builds/{timestamp}.json.
    Creates .forge/builds/ directory if needed.
    Never raises.
    """
    try:
        builds_dir = project_dir / ".forge" / "builds"
        builds_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc)
        timestamp_str = now.strftime("%Y%m%dT%H%M%S")

        # Build phase summaries from state
        phase_summaries = []
        phases_completed = 0
        tasks_completed = 0
        project_name = project_dir.name

        if state is not None:
            project_name = getattr(state, "project_name", "") or project_dir.name
            tasks_completed = getattr(state, "tasks_completed", 0)
            phases = getattr(state, "phases", [])
            phases_completed = len(phases)
            for p in phases:
                phase_summaries.append({
                    "title": getattr(p, "title", ""),
                    "status": str(getattr(p, "status", "")),
                    "task_count": len(getattr(p, "tasks", [])),
                })

        record = {
            "timestamp": now.isoformat(),
            "project": project_name,
            "phases_completed": phases_completed,
            "tasks_completed": tasks_completed,
            "health_grade": health_grade,
            "total_cost": total_cost,
            "duration_seconds": duration_seconds,
            "vercel_url": vercel_url,
            "github_pr": github_pr,
            "phase_summaries": phase_summaries,
        }

        target = builds_dir / f"{timestamp_str}.json"
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
        tmp.replace(target)
    except Exception:
        pass


def load_build_history(project_dir: Path) -> list:
    """
    Load all build records from .forge/builds/*.json.
    Returns list sorted by timestamp descending (newest first).
    Returns empty list if directory missing or empty.
    Never raises.
    """
    try:
        builds_dir = project_dir / ".forge" / "builds"
        if not builds_dir.is_dir():
            return []

        records = []
        for f in builds_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                records.append(data)
            except Exception:
                continue

        records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return records
    except Exception:
        return []


def load_build_log(project_dir: Path, limit: int = 200) -> list:
    """
    Load last {limit} entries from .forge/build.log (JSONL).
    Returns list of parsed JSON objects.
    Returns empty list on any error.
    Never raises.
    """
    try:
        log_path = project_dir / ".forge" / "build.log"
        if not log_path.is_file():
            return []

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        tail = lines[-limit:] if len(lines) > limit else lines

        records = []
        for line in tail:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                continue
        return records
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

def handle_history_get(handler) -> None:
    """Serve HISTORY_HTML."""
    content = HISTORY_HTML.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(content)))
    handler.end_headers()
    handler.wfile.write(content)


def handle_history_data(handler, project_dir) -> None:
    """GET /history/data -> {"builds": [...], "count": N}"""
    try:
        builds = load_build_history(project_dir) if project_dir else []
        _send_json(handler, {"builds": builds, "count": len(builds)})
    except Exception:
        _send_json(handler, {"builds": [], "count": 0})


def handle_history_build(handler, project_dir, build_index) -> None:
    """
    GET /history/build/{n} -> {"build": record, "log": [...]}
    build_index is newest-first (0 = most recent).
    """
    try:
        idx = int(build_index)
        builds = load_build_history(project_dir) if project_dir else []
        if idx < 0 or idx >= len(builds):
            _send_json(handler, {"error": "Build not found"}, status=404)
            return
        record = builds[idx]
        log = load_build_log(project_dir)
        _send_json(handler, {"build": record, "log": log})
    except Exception:
        _send_json(handler, {"error": "Build not found"}, status=404)


# ---------------------------------------------------------------------------
# History HTML
# ---------------------------------------------------------------------------

_HISTORY_HEAD = """
<style>
  .grade-A { color: #00e5a0; }
  .grade-B { color: #22d3ee; }
  .grade-C { color: #facc15; }
  .grade-D { color: #f97316; }
  .grade-F { color: #ef4444; }
  .build-row { cursor: pointer; transition: background 0.15s; }
  .build-row:hover { background: #1a1a1a; }
  .detail-panel { display: none; background: #141414; border-top: 1px solid #2a2a2a; }
  .detail-panel.open { display: block; }
</style>
"""

_HISTORY_CONTENT = """
<div style="padding-top:56px;max-width:960px;margin:0 auto;padding-left:16px;padding-right:16px">

  <div style="display:flex;align-items:center;gap:12px;margin-bottom:24px">
    <h1 style="font-size:20px;font-weight:700;color:#fff;margin:0">Build History</h1>
    <span id="build-count" style="background:#00e5a0;color:#0f0f0f;font-size:11px;padding:2px 8px;border-radius:8px;font-weight:600"></span>
  </div>

  <div id="empty-state" style="display:none;text-align:center;padding:60px 20px;color:#666">
    <div style="font-size:32px;margin-bottom:12px">&#128220;</div>
    <div style="font-size:14px">No builds recorded yet</div>
    <div style="font-size:12px;margin-top:8px;color:#555">Run <code style="color:#00e5a0">forge run</code> to start your first build</div>
  </div>

  <div id="builds-table" style="display:none">
    <table style="width:100%;border-collapse:collapse">
      <thead>
        <tr style="border-bottom:1px solid #2a2a2a;font-size:11px;color:#666;text-transform:uppercase;letter-spacing:0.5px">
          <th style="text-align:left;padding:8px 12px">#</th>
          <th style="text-align:left;padding:8px 12px">Project</th>
          <th style="text-align:center;padding:8px 12px">Grade</th>
          <th style="text-align:right;padding:8px 12px">Cost</th>
          <th style="text-align:right;padding:8px 12px">Duration</th>
          <th style="text-align:right;padding:8px 12px">Date</th>
        </tr>
      </thead>
      <tbody id="builds-body"></tbody>
    </table>
  </div>

</div>
"""

_HISTORY_SCRIPTS = """
<script>
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}

function gradeClass(g) {
  return 'grade-' + (g || 'F').charAt(0).toUpperCase();
}

function formatCost(c) {
  return '$' + (c || 0).toFixed(2);
}

function formatDuration(secs) {
  if (!secs || secs < 60) return (secs || 0) + 's';
  const m = Math.floor(secs / 60);
  const s = Math.round(secs % 60);
  if (m < 60) return m + 'm ' + s + 's';
  const h = Math.floor(m / 60);
  return h + 'h ' + (m % 60) + 'm';
}

function formatDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const now = new Date();
  const diff = now - d;
  const days = Math.floor(diff / 86400000);
  const time = d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
  if (days === 0) return 'Today ' + time;
  if (days === 1) return 'Yesterday ' + time;
  if (days < 7) return d.toLocaleDateString([], {month:'short', day:'numeric'}) + ' ' + time;
  return d.toLocaleDateString([], {year:'numeric', month:'short', day:'numeric'});
}

let builds = [];

async function loadHistory() {
  try {
    const resp = await fetch('/history/data');
    const data = await resp.json();
    builds = data.builds || [];
    renderTable();
  } catch(e) {
    console.error('Failed to load history:', e);
  }
}

function renderTable() {
  const count = document.getElementById('build-count');
  const empty = document.getElementById('empty-state');
  const table = document.getElementById('builds-table');
  const body = document.getElementById('builds-body');

  count.textContent = builds.length;

  if (builds.length === 0) {
    empty.style.display = 'block';
    table.style.display = 'none';
    return;
  }
  empty.style.display = 'none';
  table.style.display = 'block';

  body.innerHTML = '';
  builds.forEach((b, i) => {
    const num = builds.length - i;
    const row = document.createElement('tr');
    row.className = 'build-row';
    row.style.borderBottom = '1px solid #1a1a1a';
    row.onclick = () => toggleDetail(i);
    row.innerHTML = `
      <td style="padding:10px 12px;font-size:13px;color:#666">${num}</td>
      <td style="padding:10px 12px;font-size:13px">${esc(b.project)}</td>
      <td style="padding:10px 12px;font-size:15px;font-weight:700;text-align:center" class="${gradeClass(b.health_grade)}">${esc(b.health_grade)}</td>
      <td style="padding:10px 12px;font-size:13px;text-align:right;color:#ccc">${formatCost(b.total_cost)}</td>
      <td style="padding:10px 12px;font-size:13px;text-align:right;color:#999">${formatDuration(b.duration_seconds)}</td>
      <td style="padding:10px 12px;font-size:12px;text-align:right;color:#666">${formatDate(b.timestamp)}</td>
    `;
    body.appendChild(row);

    const detail = document.createElement('tr');
    detail.id = 'detail-' + i;
    detail.innerHTML = '<td colspan="6" class="detail-panel" id="detail-content-' + i + '"></td>';
    body.appendChild(detail);
  });
}

async function toggleDetail(idx) {
  const cell = document.getElementById('detail-content-' + idx);
  if (!cell) return;

  if (cell.classList.contains('open')) {
    cell.classList.remove('open');
    return;
  }

  // Close all others
  document.querySelectorAll('.detail-panel').forEach(p => p.classList.remove('open'));

  const b = builds[idx];
  let html = '<div style="padding:16px 12px">';

  // Phase summaries
  if (b.phase_summaries && b.phase_summaries.length > 0) {
    html += '<div style="margin-bottom:16px"><div style="font-size:11px;color:#666;text-transform:uppercase;margin-bottom:8px">Phases</div>';
    b.phase_summaries.forEach((p, pi) => {
      const done = p.status === 'PhaseStatus.DONE' || p.status === 'DONE';
      const dot = done ? '<span style="color:#00e5a0">&#9679;</span>' : '<span style="color:#666">&#9675;</span>';
      html += '<div style="font-size:12px;margin-bottom:4px;color:#ccc">' + dot + ' ' + esc(p.title) + ' <span style="color:#666">(' + (p.task_count || 0) + ' tasks)</span></div>';
    });
    html += '</div>';
  }

  // Stats row
  html += '<div style="display:flex;gap:24px;font-size:12px;color:#999">';
  html += '<span>Tasks: ' + (b.tasks_completed || 0) + '</span>';
  html += '<span>Phases: ' + (b.phases_completed || 0) + '</span>';
  html += '<span>Duration: ' + formatDuration(b.duration_seconds) + '</span>';
  html += '<span>Cost: ' + formatCost(b.total_cost) + '</span>';
  html += '</div>';

  // Links
  if (b.vercel_url || b.github_pr) {
    html += '<div style="display:flex;gap:16px;margin-top:12px;font-size:12px">';
    if (b.vercel_url) html += '<a href="' + esc(b.vercel_url) + '" target="_blank" style="color:#00e5a0;text-decoration:none">Vercel Deployment &rarr;</a>';
    if (b.github_pr) html += '<span style="color:#ccc">GitHub PR #' + esc(String(b.github_pr)) + '</span>';
    html += '</div>';
  }

  html += '</div>';
  cell.innerHTML = html;
  cell.classList.add('open');
}

loadHistory();
</script>
"""

HISTORY_HTML = page_shell("History", "/history", _HISTORY_CONTENT, extra_head=_HISTORY_HEAD, extra_scripts=_HISTORY_SCRIPTS)
