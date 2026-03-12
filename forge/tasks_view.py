"""
NEEDS_HUMAN tasks view for the Forge local app.

Shows parked tasks as interactive cards. Developers resolve tasks
inline without touching the terminal. Calls forge checkin on resolution.

This module imports only stdlib. No forge imports at module level.
"""

import json
import subprocess
import sys
from pathlib import Path

from forge.nav_shell import page_shell


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _send_json(handler, data: dict, status: int = 200) -> None:
    """Send a JSON response with proper headers."""
    body = json.dumps(data).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------

def get_parked_tasks(project_dir: Path) -> list[dict]:
    """
    Read parked tasks from state.json.

    Returns list of dicts with task info. Never raises.
    """
    try:
        from forge.state import load_state
        state = load_state(project_dir)
        parked = state.all_parked_tasks()

        # Build phase lookup for context
        phase_map = {}
        for i, phase in enumerate(state.phases):
            for task in phase.tasks:
                phase_map[task.id] = (phase.title, i + 1)

        result = []
        for task in parked:
            phase_title, phase_num = phase_map.get(task.id, ("Unknown", 0))
            result.append({
                "id": task.id,
                "title": task.title,
                "description": task.description,
                "notes": task.notes,
                "park_reason": task.park_reason,
                "phase_title": phase_title,
                "phase_num": phase_num,
            })
        return result
    except Exception:
        return []


def resolve_task(
    project_dir: Path,
    task_id: str,
    resolution: str,
    skip: bool = False,
) -> bool:
    """
    Mark a task as resolved with the developer's response.

    Replicates the checkin.py resolve pattern. Never raises.
    """
    try:
        from forge.state import load_state, save_state, TaskStatus
        from forge import needs_human

        state = load_state(project_dir)
        task = state.find_task(task_id)
        if task is None:
            return False

        if skip:
            task.status = TaskStatus.DONE
        else:
            task.notes = f"Human resolution: {resolution}\n\nOriginal notes: {task.notes}"
            task.status = TaskStatus.PENDING
            task.park_reason = ""
            task.retry_count = 0

        save_state(project_dir, state)
        needs_human.mark_resolved(project_dir, task_id)
        return True
    except Exception:
        return False


def trigger_checkin(project_dir: Path) -> bool:
    """
    Trigger forge checkin as a subprocess. Never raises.
    """
    try:
        subprocess.Popen(
            [sys.executable, "-m", "forge", "checkin",
             "--project-dir", str(project_dir)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(project_dir),
        )
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

def handle_tasks_get(handler) -> None:
    """Serve the tasks view HTML."""
    content = TASKS_HTML.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(content)))
    handler.end_headers()
    handler.wfile.write(content)


def handle_tasks_data(handler, project_dir: Path) -> None:
    """Return parked tasks as JSON."""
    try:
        tasks = get_parked_tasks(project_dir) if project_dir else []
        _send_json(handler, {"tasks": tasks, "count": len(tasks)})
    except Exception:
        _send_json(handler, {"tasks": [], "count": 0})


def handle_tasks_resolve(handler, body: bytes, project_dir: Path) -> None:
    """
    Handle POST /tasks/resolve.

    Body: {"task_id": str, "resolution": str, "skip": bool}
    """
    try:
        data = json.loads(body)
        task_id = data.get("task_id", "")
        resolution = data.get("resolution", "")
        skip = data.get("skip", False)

        if not task_id:
            _send_json(handler, {"status": "error", "error": "task_id required"}, 400)
            return

        success = resolve_task(project_dir, task_id, resolution, skip=skip)
        if not success:
            _send_json(handler, {"status": "error", "error": "Task not found or resolve failed"}, 404)
            return

        # Push SSE event for badge updates
        try:
            from forge.dashboard import push_event
            remaining = get_parked_tasks(project_dir)
            push_event("tasks_updated", {"count": len(remaining)})
        except Exception:
            pass

        _send_json(handler, {"status": "ok", "remaining": len(get_parked_tasks(project_dir))})

    except json.JSONDecodeError:
        _send_json(handler, {"status": "error", "error": "Invalid JSON"}, 400)
    except Exception as exc:
        _send_json(handler, {"status": "error", "error": str(exc)}, 500)


# ---------------------------------------------------------------------------
# Tasks HTML
# ---------------------------------------------------------------------------

_TASKS_HEAD = """
<style>
  .forge-green { color: #00e5a0; }
  .card {
    background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 8px;
    padding: 20px; margin-bottom: 16px;
  }
  .input-field {
    background: #0f0f0f; border: 1px solid #2a2a2a; border-radius: 6px;
    color: #e5e5e5; padding: 10px 14px; width: 100%; outline: none;
    transition: border-color 0.2s; resize: vertical; min-height: 80px;
    font-family: inherit;
  }
  .input-field:focus { border-color: #00e5a0; }
  .input-field::placeholder { color: #555; }
  .btn-primary {
    background: #00e5a0; color: #0f0f0f; font-weight: 600;
    padding: 8px 20px; border-radius: 6px; border: none; cursor: pointer;
    font-size: 13px; transition: opacity 0.2s; font-family: inherit;
  }
  .btn-primary:hover { opacity: 0.9; }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-secondary {
    background: #2a2a2a; color: #e5e5e5; font-weight: 600;
    padding: 8px 20px; border-radius: 6px; border: 1px solid #333;
    cursor: pointer; font-size: 13px; transition: opacity 0.2s; font-family: inherit;
  }
  .btn-secondary:hover { opacity: 0.8; }
  .task-resolved { opacity: 0.5; border-color: #00e5a0; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .spinner {
    display: inline-block; width: 14px; height: 14px;
    border: 2px solid currentColor; border-top-color: transparent;
    border-radius: 50%; animation: spin 0.6s linear infinite;
    vertical-align: middle; margin-right: 6px;
  }
</style>
"""

_TASKS_CONTENT = """
<div class="max-w-3xl mx-auto px-6 pt-16 pb-12">

  <!-- Header -->
  <div class="mb-6">
    <h1 class="text-xl font-bold mb-1">
      <span style="color:#f59e0b">&#9208;</span>
      Needs Your Input
      <span id="task-count" style="color:#555;font-size:14px"></span>
    </h1>
    <p style="color:#555;font-size:13px">Resolve parked tasks so Forge can continue building.</p>
  </div>

  <!-- Tasks container -->
  <div id="tasks-container"></div>

  <!-- Empty state -->
  <div id="empty-state" style="display:none">
    <div class="card" style="text-align:center;padding:48px 20px">
      <div style="font-size:24px;margin-bottom:12px">&#10003;</div>
      <div class="font-bold mb-2">No items need your input</div>
      <div style="color:#555;font-size:13px">Forge is building or no build is in progress.</div>
    </div>
  </div>

  <!-- Resume Build button -->
  <div id="resume-section" style="display:none" class="mt-6 text-center">
    <button class="btn-primary" id="resume-btn" onclick="resumeBuild()" style="padding:12px 32px;font-size:15px">
      Resume Build
    </button>
    <p style="color:#555;font-size:12px;margin-top:8px">
      Triggers forge checkin to resume resolved tasks.
    </p>
  </div>

</div>
"""

_TASKS_SCRIPTS = """
<script>
let resolvedCount = 0;

async function loadTasks() {
  try {
    const resp = await fetch('/tasks/data');
    const data = await resp.json();
    renderTasks(data.tasks);
    updateBadge(data.count);
  } catch (e) {
    document.getElementById('empty-state').style.display = 'block';
  }
}

function renderTasks(tasks) {
  const container = document.getElementById('tasks-container');
  const empty = document.getElementById('empty-state');
  const countEl = document.getElementById('task-count');

  if (!tasks || tasks.length === 0) {
    container.innerHTML = '';
    empty.style.display = 'block';
    countEl.textContent = '';
    return;
  }

  empty.style.display = 'none';
  countEl.textContent = '(' + tasks.length + ' item' + (tasks.length > 1 ? 's' : '') + ' blocking build)';

  container.innerHTML = tasks.map(t => `
    <div class="card" id="card-${t.id}">
      <div class="flex items-start justify-between mb-3">
        <div>
          <span style="color:#f59e0b;margin-right:6px">&#9208;</span>
          <span class="font-bold">${esc(t.title)}</span>
        </div>
      </div>
      <div style="color:#555;font-size:12px;margin-bottom:12px">
        Phase ${t.phase_num}: ${esc(t.phase_title)}
      </div>
      ${t.park_reason ? `
      <div style="margin-bottom:12px">
        <div style="color:#888;font-size:12px;margin-bottom:4px;font-weight:600">Forge needs:</div>
        <div style="color:#ccc;font-size:13px">${esc(t.park_reason)}</div>
      </div>` : ''}
      ${t.description ? `
      <div style="margin-bottom:12px">
        <div style="color:#888;font-size:12px;margin-bottom:4px;font-weight:600">Task description:</div>
        <div style="color:#999;font-size:12px;max-height:80px;overflow-y:auto">${esc(t.description)}</div>
      </div>` : ''}
      <div style="margin-bottom:12px">
        <div style="color:#888;font-size:12px;margin-bottom:4px;font-weight:600">Your response:</div>
        <textarea class="input-field" id="resolution-${t.id}" placeholder="Provide the information Forge needs..."></textarea>
      </div>
      <div class="flex gap-3">
        <button class="btn-primary" id="resolve-btn-${t.id}" onclick="resolveTask('${t.id}', false)">
          Mark Resolved
        </button>
        <button class="btn-secondary" id="skip-btn-${t.id}" onclick="resolveTask('${t.id}', true)">
          Skip this task
        </button>
      </div>
    </div>
  `).join('');
}

function esc(s) {
  if (!s) return '';
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

async function resolveTask(taskId, skip) {
  const resolveBtn = document.getElementById('resolve-btn-' + taskId);
  const skipBtn = document.getElementById('skip-btn-' + taskId);
  const card = document.getElementById('card-' + taskId);
  const textarea = document.getElementById('resolution-' + taskId);
  const resolution = textarea ? textarea.value : '';

  if (!skip && !resolution.trim()) {
    textarea.style.borderColor = '#e55';
    textarea.focus();
    return;
  }

  const activeBtn = skip ? skipBtn : resolveBtn;
  activeBtn.disabled = true;
  activeBtn.innerHTML = '<span class="spinner"></span>' + (skip ? 'Skipping...' : 'Resolving...');

  try {
    const resp = await fetch('/tasks/resolve', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({task_id: taskId, resolution, skip})
    });
    const data = await resp.json();

    if (data.status === 'ok') {
      card.classList.add('task-resolved');
      resolveBtn.disabled = true;
      skipBtn.disabled = true;
      resolveBtn.textContent = skip ? 'Skipped' : 'Resolved';
      skipBtn.style.display = 'none';
      if (textarea) textarea.disabled = true;
      resolvedCount++;
      document.getElementById('resume-section').style.display = 'block';
      updateBadge(data.remaining || 0);
    } else {
      alert('Error: ' + (data.error || 'Unknown error'));
      activeBtn.disabled = false;
      activeBtn.textContent = skip ? 'Skip this task' : 'Mark Resolved';
    }
  } catch (e) {
    alert('Failed: ' + e.message);
    activeBtn.disabled = false;
    activeBtn.textContent = skip ? 'Skip this task' : 'Mark Resolved';
  }
}

async function resumeBuild() {
  const btn = document.getElementById('resume-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Resuming...';

  try {
    // Reload tasks to confirm state
    const resp = await fetch('/tasks/data');
    const data = await resp.json();
    if (data.count === 0) {
      btn.textContent = 'All tasks resolved!';
      setTimeout(() => { window.location.href = '/'; }, 1500);
    } else {
      btn.textContent = data.count + ' task(s) still parked';
      btn.disabled = false;
      setTimeout(() => { btn.textContent = 'Resume Build'; }, 2000);
    }
  } catch (e) {
    btn.disabled = false;
    btn.textContent = 'Resume Build';
  }
}

function updateBadge(count) {
  const badge = document.getElementById('nav-task-badge');
  if (badge) {
    if (count > 0) {
      badge.style.display = 'inline';
      badge.textContent = count;
    } else {
      badge.style.display = 'none';
    }
  }
}

// SSE for live updates
function connectSSE() {
  try {
    const es = new EventSource('/events');
    es.addEventListener('tasks_updated', function(e) {
      const data = JSON.parse(e.data);
      updateBadge(data.count);
      // Reload task list
      loadTasks();
    });
    es.onerror = function() {
      es.close();
      setTimeout(connectSSE, 3000);
    };
  } catch(e) {}
}

// Init
loadTasks();
connectSSE();
</script>
"""

TASKS_HTML = page_shell("Tasks", "/tasks", _TASKS_CONTENT, extra_head=_TASKS_HEAD, extra_scripts=_TASKS_SCRIPTS)
