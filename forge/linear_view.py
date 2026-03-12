"""
Linear board view for the Forge dashboard.

Shows a Kanban-style board with Linear issues grouped by status,
cross-referenced with Forge task state.
This module imports only stdlib. No forge imports at module level.
"""

import json
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

def get_linear_board_data(project_dir: Path) -> dict:
    """
    Fetch Linear issues grouped by state column.
    Returns {"configured": False} if Linear not set up.
    Never raises.
    """
    try:
        from forge.linear_integration import (
            load_linear_config, get_linear_token, get_open_issues,
        )

        config = load_linear_config(project_dir)
        token = get_linear_token()

        if not config.enabled or not token or not config.team_id:
            return {"configured": False}

        issues = get_open_issues(config, token, limit=50)

        # Cross-reference with Forge state for status overlay
        task_statuses = {}
        try:
            from forge.state import load_state
            state = load_state(project_dir)
            if state:
                for phase in state.phases:
                    for task in phase.tasks:
                        task_statuses[task.title.lower()] = str(task.status)
        except Exception:
            pass

        todo = []
        in_progress = []
        done = []

        for issue in issues:
            card = {
                "id": issue.get("id", ""),
                "identifier": issue.get("identifier", ""),
                "title": issue.get("title", ""),
                "priority": issue.get("priority", 0),
                "labels": issue.get("labels", []),
                "forge_status": "",
            }

            # Try to match with Forge task by title overlap
            title_lower = card["title"].lower()
            for task_title, status in task_statuses.items():
                # Simple word overlap match
                task_words = set(task_title.split())
                issue_words = set(title_lower.split())
                if len(task_words & issue_words) >= 3:
                    card["forge_status"] = status
                    break

            # Group by Linear state (issues from get_open_issues are
            # non-completed/cancelled, so they're todo or in-progress)
            if card["forge_status"] and "DONE" in card["forge_status"]:
                done.append(card)
            elif card["forge_status"] and "IN_PROGRESS" in card["forge_status"]:
                in_progress.append(card)
            else:
                todo.append(card)

        return {
            "configured": True,
            "todo": todo,
            "in_progress": in_progress,
            "done": done,
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "total": len(issues),
        }
    except Exception:
        return {"configured": False}


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

def handle_linear_get(handler) -> None:
    """Serve LINEAR_HTML."""
    content = LINEAR_HTML.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(content)))
    handler.end_headers()
    handler.wfile.write(content)


def handle_linear_data(handler, project_dir) -> None:
    """GET /linear/data -> board data as JSON."""
    try:
        data = get_linear_board_data(project_dir) if project_dir else {"configured": False}
        _send_json(handler, data)
    except Exception:
        _send_json(handler, {"configured": False})


def handle_linear_sync(handler, body: bytes, project_dir) -> None:
    """POST /linear/sync -> sync Forge state to Linear."""
    try:
        from forge.linear_integration import (
            load_linear_config, get_linear_token, sync_plan_to_linear,
        )
        from forge.state import load_state

        config = load_linear_config(project_dir)
        token = get_linear_token()

        if not config.enabled or not token:
            _send_json(handler, {"status": "error", "error": "Linear not configured"}, 400)
            return

        state = load_state(project_dir)
        if not state or not state.phases:
            _send_json(handler, {"status": "error", "error": "No build plan found"}, 400)
            return

        result = sync_plan_to_linear(config, token, state.phases)
        _send_json(handler, {
            "status": "ok",
            "milestones_created": result.get("milestones_created", 0),
            "issues_created": result.get("issues_created", 0),
        })
    except Exception as e:
        _send_json(handler, {"status": "error", "error": str(e)}, 500)


# ---------------------------------------------------------------------------
# Linear board HTML
# ---------------------------------------------------------------------------

_LINEAR_CONTENT = """
<div style="padding-top:56px;max-width:1100px;margin:0 auto;padding-left:16px;padding-right:16px">

  <div style="display:flex;align-items:center;gap:12px;margin-bottom:24px">
    <h1 style="font-size:20px;font-weight:700;color:#fff;margin:0">Linear Board</h1>
    <span id="sync-time" style="font-size:11px;color:#666;margin-left:auto"></span>
  </div>

  <div id="not-configured" style="display:none;text-align:center;padding:60px 20px;color:#666">
    <div style="font-size:32px;margin-bottom:12px">&#128279;</div>
    <div style="font-size:14px">Linear integration not configured</div>
    <div style="font-size:12px;margin-top:8px"><a href="/integrations" style="color:#00e5a0;text-decoration:none">Set it up in Integrations &rarr;</a></div>
  </div>

  <div id="board" style="display:none">
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:24px">

      <div>
        <div style="font-size:12px;color:#666;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:12px;padding-bottom:8px;border-bottom:2px solid #333">
          Todo <span id="todo-count" style="color:#999"></span>
        </div>
        <div id="col-todo" style="min-height:100px"></div>
      </div>

      <div>
        <div style="font-size:12px;color:#22d3ee;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:12px;padding-bottom:8px;border-bottom:2px solid #22d3ee">
          In Progress <span id="progress-count" style="color:#999"></span>
        </div>
        <div id="col-progress" style="min-height:100px"></div>
      </div>

      <div>
        <div style="font-size:12px;color:#00e5a0;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:12px;padding-bottom:8px;border-bottom:2px solid #00e5a0">
          Done <span id="done-count" style="color:#999"></span>
        </div>
        <div id="col-done" style="min-height:100px"></div>
      </div>

    </div>

    <div style="display:flex;gap:8px">
      <button id="sync-btn" onclick="syncPlan()" style="padding:6px 16px;border-radius:4px;font-size:12px;cursor:pointer;border:none;font-family:inherit;background:#00e5a0;color:#0f0f0f;font-weight:600">Sync with Forge Plan</button>
      <span id="sync-result" style="font-size:12px;line-height:30px;color:#999"></span>
    </div>
  </div>

</div>
"""

_LINEAR_SCRIPTS = """
<script>
function esc(s) {
  var d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}

function renderCard(issue) {
  var forgeTag = '';
  if (issue.forge_status) {
    var color = issue.forge_status.indexOf('DONE') >= 0 ? '#00e5a0' :
                issue.forge_status.indexOf('IN_PROGRESS') >= 0 ? '#22d3ee' : '#999';
    forgeTag = '<div style="font-size:10px;color:' + color + ';margin-top:6px">forge: ' + esc(issue.forge_status) + '</div>';
  }
  var labels = (issue.labels || []).map(function(l) {
    return '<span style="font-size:10px;background:#2a2a2a;color:#999;padding:1px 6px;border-radius:4px;margin-right:4px">' + esc(l) + '</span>';
  }).join('');

  return '<div style="background:#141414;border:1px solid #2a2a2a;border-radius:6px;padding:12px;margin-bottom:8px">' +
    '<div style="font-size:11px;color:#666;margin-bottom:4px">' + esc(issue.identifier) + '</div>' +
    '<div style="font-size:13px;color:#e5e5e5">' + esc(issue.title) + '</div>' +
    (labels ? '<div style="margin-top:6px">' + labels + '</div>' : '') +
    forgeTag +
    '</div>';
}

async function loadBoard() {
  try {
    var resp = await fetch('/linear/data');
    var data = await resp.json();

    if (!data.configured) {
      document.getElementById('not-configured').style.display = 'block';
      document.getElementById('board').style.display = 'none';
      return;
    }

    document.getElementById('not-configured').style.display = 'none';
    document.getElementById('board').style.display = 'block';

    var todo = data.todo || [];
    var progress = data.in_progress || [];
    var done = data.done || [];

    document.getElementById('todo-count').textContent = '(' + todo.length + ')';
    document.getElementById('progress-count').textContent = '(' + progress.length + ')';
    document.getElementById('done-count').textContent = '(' + done.length + ')';

    document.getElementById('col-todo').innerHTML = todo.map(renderCard).join('') || '<div style="color:#444;font-size:12px;padding:8px">No items</div>';
    document.getElementById('col-progress').innerHTML = progress.map(renderCard).join('') || '<div style="color:#444;font-size:12px;padding:8px">No items</div>';
    document.getElementById('col-done').innerHTML = done.map(renderCard).join('') || '<div style="color:#444;font-size:12px;padding:8px">No items</div>';

    if (data.synced_at) {
      var d = new Date(data.synced_at);
      document.getElementById('sync-time').textContent = 'synced ' + d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
    }
  } catch(e) {
    document.getElementById('not-configured').style.display = 'block';
    document.getElementById('board').style.display = 'none';
  }
}

async function syncPlan() {
  var btn = document.getElementById('sync-btn');
  var result = document.getElementById('sync-result');
  btn.disabled = true;
  btn.style.opacity = '0.6';
  result.textContent = 'Syncing...';
  result.style.color = '#999';

  try {
    var resp = await fetch('/linear/sync', {method: 'POST'});
    var data = await resp.json();
    if (data.status === 'ok') {
      result.style.color = '#00e5a0';
      result.textContent = 'Synced: ' + (data.milestones_created || 0) + ' milestones, ' + (data.issues_created || 0) + ' issues created';
      setTimeout(loadBoard, 1000);
    } else {
      result.style.color = '#ef4444';
      result.textContent = 'Error: ' + (data.error || 'unknown');
    }
  } catch(e) {
    result.style.color = '#ef4444';
    result.textContent = 'Error: ' + e.message;
  }
  btn.disabled = false;
  btn.style.opacity = '1';
}

loadBoard();
</script>
"""

LINEAR_HTML = page_shell("Linear", "/linear", _LINEAR_CONTENT, extra_scripts=_LINEAR_SCRIPTS)
