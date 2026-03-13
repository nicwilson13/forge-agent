"""
Local web dashboard for Forge build monitoring.

Runs a lightweight HTTP server on localhost:3333 during forge run.
Serves a single-page app with live build progress via Server-Sent Events.

No external dependencies. Uses Python stdlib http.server + threading.
SSE endpoint pushes events to connected browser clients in real time.

This module imports only stdlib. No forge imports at module level.
"""

import json
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from forge.nav_shell import page_shell


# ---------------------------------------------------------------------------
# Global state (shared across threads)
# ---------------------------------------------------------------------------

_sse_clients: list = []          # list of wfile objects
_sse_lock = threading.Lock()
_dashboard_state: dict = {}      # shared state updated by forge run
_project_dir: Path | None = None
_server: HTTPServer | None = None
_stop_event = threading.Event()


# ---------------------------------------------------------------------------
# SSE push
# ---------------------------------------------------------------------------

def push_event(event_type: str, data: dict) -> None:
    """
    Push an SSE event to all connected browser clients.

    Formats as: "event: {type}\\ndata: {json}\\n\\n"
    Silently removes disconnected clients.
    Never raises.
    """
    try:
        msg = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
        encoded = msg.encode("utf-8")
        with _sse_lock:
            dead = []
            for i, client in enumerate(_sse_clients):
                try:
                    client.write(encoded)
                    client.flush()
                except Exception:
                    dead.append(i)
            for i in reversed(dead):
                _sse_clients.pop(i)
    except Exception:
        pass


def update_dashboard_state(state_update: dict) -> None:
    """
    Update the shared dashboard state dict.

    Merges state_update into _dashboard_state, then pushes
    a "state" SSE event to all clients.
    """
    global _dashboard_state
    _dashboard_state.update(state_update)
    push_event("state", _dashboard_state)


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

class DashboardHandler(BaseHTTPRequestHandler):
    """
    HTTP request handler for the Forge dashboard.

    Routes:
    GET /           -> serve HTML dashboard (redirects to /setup if no state)
    GET /state      -> return current state as JSON
    GET /events     -> SSE stream for live updates
    GET /log        -> return last 100 build log lines as JSON array
    GET /setup      -> serve setup wizard HTML
    GET /setup/status -> return setup status as JSON
    POST /setup/submit  -> receive form data, write files, start forge run
    POST /setup/ai-assist -> call Claude to draft VISION.md
    GET /tasks          -> serve tasks view HTML
    GET /tasks/data     -> return parked tasks as JSON
    POST /tasks/resolve -> resolve a parked task
    """

    def log_message(self, format, *args):
        pass  # suppress default access logs

    def do_GET(self):
        try:
            self._route_get()
        except Exception as exc:
            self._send_error(exc)

    def do_POST(self):
        try:
            self._route_post()
        except Exception as exc:
            self._send_error(exc)

    def _send_error(self, exc: Exception):
        """Send a 500 JSON error response. Never raises."""
        try:
            body = json.dumps({"status": "error", "error": str(exc)}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass

    def _route_get(self):
        if self.path == "/":
            self._serve_html()
        elif self.path == "/state":
            self._serve_state()
        elif self.path == "/events":
            self._serve_sse()
        elif self.path == "/log":
            self._serve_log()
        elif self.path == "/setup" or self.path.startswith("/setup?"):
            from forge.setup_wizard import handle_setup_get
            handle_setup_get(self)
        elif self.path == "/setup/status":
            from forge.setup_wizard import handle_setup_status
            handle_setup_status(self, _project_dir)
        elif self.path == "/tasks":
            from forge.tasks_view import handle_tasks_get
            handle_tasks_get(self)
        elif self.path == "/tasks/data":
            from forge.tasks_view import handle_tasks_data
            handle_tasks_data(self, _project_dir)
        elif self.path == "/history":
            from forge.history_view import handle_history_get
            handle_history_get(self)
        elif self.path == "/history/data":
            from forge.history_view import handle_history_data
            handle_history_data(self, _project_dir)
        elif self.path.startswith("/history/build/"):
            from forge.history_view import handle_history_build
            idx = self.path.split("/history/build/")[1]
            handle_history_build(self, _project_dir, idx)
        elif self.path == "/integrations":
            from forge.integrations_view import handle_integrations_get
            handle_integrations_get(self)
        elif self.path == "/integrations/data":
            from forge.integrations_view import handle_integrations_data
            handle_integrations_data(self, _project_dir)
        elif self.path == "/linear":
            from forge.linear_view import handle_linear_get
            handle_linear_get(self)
        elif self.path == "/linear/data":
            from forge.linear_view import handle_linear_data
            handle_linear_data(self, _project_dir)
        else:
            self.send_response(404)
            self.end_headers()

    def _route_post(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        if self.path == "/setup/submit":
            from forge.setup_wizard import handle_setup_submit
            handle_setup_submit(self, body, _project_dir)
        elif self.path == "/setup/ai-assist":
            from forge.setup_wizard import handle_ai_assist
            handle_ai_assist(self, body)
        elif self.path == "/tasks/resolve":
            from forge.tasks_view import handle_tasks_resolve
            handle_tasks_resolve(self, body, _project_dir)
        elif self.path == "/integrations/save":
            from forge.integrations_view import handle_integrations_save
            handle_integrations_save(self, body, _project_dir)
        elif self.path.startswith("/integrations/test/"):
            from forge.integrations_view import handle_integrations_test
            name = self.path.split("/integrations/test/")[1]
            handle_integrations_test(self, _project_dir, name)
        elif self.path == "/linear/sync":
            from forge.linear_view import handle_linear_sync
            handle_linear_sync(self, body, _project_dir)
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_html(self):
        # Redirect to setup wizard if no build state exists and no run in progress
        if _project_dir and not (_project_dir / ".forge" / "state.json").exists():
            # If forge run was already started (log exists), serve the dashboard
            # so the user sees the connecting/waiting state instead of a redirect loop
            if not (_project_dir / ".forge" / "run_output.log").exists():
                self.send_response(302)
                self.send_header("Location", "/setup")
                self.end_headers()
                return
        content = INDEX_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _serve_state(self):
        body = json.dumps(_dashboard_state).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        with _sse_lock:
            _sse_clients.append(self.wfile)

        # Send initial state
        try:
            msg = f"event: state\ndata: {json.dumps(_dashboard_state)}\n\n"
            self.wfile.write(msg.encode("utf-8"))
            self.wfile.flush()
        except Exception:
            return

        # Keep connection alive with heartbeats
        while not _stop_event.is_set():
            try:
                self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
                # Sleep in small increments so we can check stop_event
                for _ in range(15):
                    if _stop_event.is_set():
                        return
                    time.sleep(1)
            except Exception:
                break

    def _serve_log(self):
        records = []
        if _project_dir:
            log_path = _project_dir / ".forge" / "build.log"
            if log_path.exists():
                try:
                    lines = log_path.read_text(encoding="utf-8").splitlines()
                    for line in lines[-100:]:
                        line = line.strip()
                        if line:
                            try:
                                records.append(json.loads(line))
                            except (json.JSONDecodeError, TypeError):
                                pass
                except Exception:
                    pass

        body = json.dumps(records).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

def start_dashboard(project_dir: Path, port: int = 3333) -> threading.Thread | None:
    """
    Start the dashboard server in a background thread.

    Returns the thread, or None if port is in use.
    Never raises.
    """
    global _project_dir, _server
    _project_dir = project_dir
    _stop_event.clear()

    try:
        server = HTTPServer(("127.0.0.1", port), DashboardHandler)
    except OSError:
        print(f"  [dashboard] Warning: port {port} in use, dashboard disabled")
        return None

    _server = server
    server.timeout = 1  # allow periodic checks

    def _run():
        while not _stop_event.is_set():
            server.handle_request()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    print(f"  Dashboard: http://localhost:{port}")
    return thread


def stop_dashboard() -> None:
    """
    Signal all SSE clients to disconnect and stop the server.
    """
    global _server
    _stop_event.set()
    with _sse_lock:
        _sse_clients.clear()
    if _server:
        try:
            _server.server_close()
        except Exception:
            pass
        _server = None


# ---------------------------------------------------------------------------
# HTML Dashboard
# ---------------------------------------------------------------------------

_DASHBOARD_CONTENT = """
<div class="max-w-3xl mx-auto space-y-4" style="padding-top:56px;padding-left:16px;padding-right:16px">
  <!-- Header -->
  <div class="flex items-center justify-between px-4 py-3 rounded-lg" style="background:#1a1a1a;border:1px solid #2a2a2a">
    <div class="flex items-center gap-3">
      <span class="text-lg font-semibold" style="color:#00e5a0">Forge</span>
      <span class="text-sm" style="color:#666">&middot;</span>
      <span id="projectName" class="text-sm" style="color:#999">--</span>
    </div>
    <div class="flex items-center gap-2">
      <span id="liveIndicator" class="inline-block w-2 h-2 rounded-full bg-gray-600"></span>
      <span id="liveLabel" class="text-xs" style="color:#666">connecting</span>
    </div>
  </div>

  <!-- Phase & Task -->
  <div class="px-4 py-3 rounded-lg" style="background:#1a1a1a;border:1px solid #2a2a2a">
    <div class="flex items-center justify-between mb-2">
      <span id="phaseLabel" class="text-sm" style="color:#999">Phase --</span>
      <span id="phasePercent" class="text-xs" style="color:#666">0%</span>
    </div>
    <div class="w-full h-2 rounded-full" style="background:#2a2a2a">
      <div id="phaseBar" class="h-2 rounded-full transition-all" style="background:#00e5a0;width:0%"></div>
    </div>
    <div class="mt-2 flex items-center justify-between">
      <span id="taskLabel" class="text-sm" style="color:#999">Task --</span>
      <span id="taskStatus" class="text-xs" style="color:#666">--</span>
    </div>
  </div>

  <!-- Stats -->
  <div class="grid grid-cols-3 gap-3">
    <div class="px-4 py-3 rounded-lg text-center" style="background:#1a1a1a;border:1px solid #2a2a2a">
      <div class="text-xs mb-1" style="color:#666">Cost</div>
      <div id="costValue" class="text-sm font-semibold" style="color:#00e5a0">$0.00</div>
    </div>
    <div class="px-4 py-3 rounded-lg text-center" style="background:#1a1a1a;border:1px solid #2a2a2a">
      <div class="text-xs mb-1" style="color:#666">Health</div>
      <div id="healthValue" class="text-sm font-semibold" style="color:#00e5a0">--</div>
    </div>
    <div class="px-4 py-3 rounded-lg text-center" style="background:#1a1a1a;border:1px solid #2a2a2a">
      <div class="text-xs mb-1" style="color:#666">Tasks</div>
      <div id="tasksValue" class="text-sm font-semibold" style="color:#00e5a0">0/0</div>
    </div>
  </div>

  <!-- Integrations -->
  <div class="px-4 py-2 rounded-lg flex items-center gap-4 flex-wrap" style="background:#1a1a1a;border:1px solid #2a2a2a">
    <span class="text-xs" style="color:#666">Integrations:</span>
    <span id="intGithub" class="text-xs" style="color:#666">GitHub -</span>
    <span id="intVercel" class="text-xs" style="color:#666">Vercel -</span>
    <span id="intLinear" class="text-xs" style="color:#666">Linear -</span>
    <span id="intSentry" class="text-xs" style="color:#666">Sentry -</span>
    <span id="intFigma" class="text-xs" style="color:#666">Figma -</span>
    <span id="intOllama" class="text-xs" style="color:#666">Ollama -</span>
  </div>

  <!-- Build Log -->
  <div class="rounded-lg" style="background:#1a1a1a;border:1px solid #2a2a2a">
    <div class="px-4 py-2" style="border-bottom:1px solid #2a2a2a">
      <span class="text-xs" style="color:#666">Build Log</span>
    </div>
    <div id="logContainer" class="px-4 py-2 overflow-y-auto text-xs leading-relaxed" style="max-height:300px;color:#999">
      <div class="py-1" style="color:#666">Waiting for events...</div>
    </div>
  </div>
</div>
"""

_DASHBOARD_SCRIPTS = """
<script>
const $ = id => document.getElementById(id);
let connected = false;

function setLive(on) {
  connected = on;
  $('liveIndicator').style.background = on ? '#00e5a0' : '#666';
  $('liveLabel').textContent = on ? 'live' : 'offline';
}

function updateState(s) {
  if (s.project_name) $('projectName').textContent = s.project_name;
  if (s.phase_title) $('phaseLabel').textContent =
    'Phase ' + (s.current_phase || '-') + '/' + (s.total_phases || '-') + ': ' + s.phase_title;
  if (s.total_tasks > 0) {
    const pct = Math.round((s.tasks_done || 0) / s.total_tasks * 100);
    $('phaseBar').style.width = pct + '%';
    $('phasePercent').textContent = pct + '%';
  }
  if (s.current_task) $('taskLabel').textContent = 'Task: ' + s.current_task;
  if (s.task_status) $('taskStatus').textContent = s.task_status;
  if (s.cost != null) $('costValue').textContent = s.cost;
  if (s.health) $('healthValue').textContent = s.health;
  if (s.tasks_done != null) $('tasksValue').textContent = (s.tasks_done||0) + '/' + (s.total_tasks||0) + ' done';
  if (s.integrations) {
    const map = {github:'intGithub',vercel:'intVercel',linear:'intLinear',sentry:'intSentry',figma:'intFigma',ollama:'intOllama'};
    for (const [k,id] of Object.entries(map)) {
      const v = s.integrations[k];
      const el = $(id);
      if (v === 'ok') { el.textContent = k.charAt(0).toUpperCase()+k.slice(1)+' \\u2713'; el.style.color='#00e5a0'; }
      else { el.textContent = k.charAt(0).toUpperCase()+k.slice(1)+' -'; el.style.color='#666'; }
    }
  }
}

function appendLog(entry) {
  const c = $('logContainer');
  if (c.children.length === 1 && c.children[0].textContent === 'Waiting for events...') c.innerHTML = '';
  const div = document.createElement('div');
  div.className = 'py-0.5';
  const ts = entry.ts ? entry.ts.split('T')[1]?.split('.')[0] || '' : '';
  const evt = entry.event || '';
  const title = entry.task_title || entry.phase_title || entry.project_name || '';
  const sym = evt.includes('completed') || evt.includes('passed') ? '\\u2713' :
              evt.includes('failed') ? '\\u2717' :
              evt.includes('started') ? '\\u2192' : '\\u00b7';
  const color = evt.includes('failed') ? '#f87171' : evt.includes('completed')||evt.includes('passed') ? '#00e5a0' : '#999';
  div.innerHTML = '<span style="color:#666">['+ts+']</span> <span style="color:'+color+'">'+sym+'</span> <span style="color:#999">'+evt+'</span> <span style="color:#e5e5e5">'+title+'</span>';
  c.appendChild(div);
  if (c.children.length > 50) c.removeChild(c.children[0]);
  c.scrollTop = c.scrollHeight;
}

// SSE connection
function connect() {
  const es = new EventSource('/events');
  es.onopen = () => setLive(true);
  es.onerror = () => { setLive(false); es.close(); setTimeout(connect, 3000); };
  es.addEventListener('state', e => { try { updateState(JSON.parse(e.data)); } catch {} });
  es.addEventListener('log', e => { try { appendLog(JSON.parse(e.data)); } catch {} });
  // Also handle generic events from build_logger push_event
  es.addEventListener('session_started', e => { try { appendLog(JSON.parse(e.data)); } catch {} });
  es.addEventListener('session_ended', e => { try { appendLog(JSON.parse(e.data)); } catch {} });
  es.addEventListener('phase_started', e => { try { appendLog(JSON.parse(e.data)); } catch {} });
  es.addEventListener('phase_completed', e => { try { appendLog(JSON.parse(e.data)); } catch {} });
  es.addEventListener('phase_failed', e => { try { appendLog(JSON.parse(e.data)); } catch {} });
  es.addEventListener('task_started', e => { try { appendLog(JSON.parse(e.data)); } catch {} });
  es.addEventListener('task_completed', e => { try { appendLog(JSON.parse(e.data)); } catch {} });
  es.addEventListener('task_failed', e => { try { appendLog(JSON.parse(e.data)); } catch {} });
  es.addEventListener('task_parked', e => { try { appendLog(JSON.parse(e.data)); } catch {} });
  es.addEventListener('qa_passed', e => { try { appendLog(JSON.parse(e.data)); } catch {} });
  es.addEventListener('qa_failed', e => { try { appendLog(JSON.parse(e.data)); } catch {} });
  es.addEventListener('git_committed', e => { try { appendLog(JSON.parse(e.data)); } catch {} });
  es.addEventListener('fatal_error', e => { try { appendLog(JSON.parse(e.data)); } catch {} });
  es.addEventListener('tasks_updated', e => { try { const d=JSON.parse(e.data); const b=document.getElementById('nav-task-badge'); if(b){if(d.count>0){b.style.display='inline';b.textContent=d.count}else{b.style.display='none'}} } catch {} });
}

// Initial load
fetch('/state').then(r=>r.json()).then(updateState).catch(()=>{});
fetch('/log').then(r=>r.json()).then(entries=>entries.forEach(appendLog)).catch(()=>{});
connect();
</script>
"""

INDEX_HTML = page_shell("Build", "/", _DASHBOARD_CONTENT, extra_scripts=_DASHBOARD_SCRIPTS)
