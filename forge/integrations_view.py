"""
Integrations view for the Forge dashboard.

Displays configuration cards for all 6 integrations with inline
config forms, token management, and connection testing.
This module imports only stdlib. No forge imports at module level.
"""

import json
import urllib.request
import urllib.error
from pathlib import Path

from forge.nav_shell import page_shell


# ---------------------------------------------------------------------------
# Integration registry
# ---------------------------------------------------------------------------

INTEGRATIONS = [
    {
        "name": "github",
        "label": "GitHub",
        "config_file": "github.json",
        "token_key": "github_token",
        "description": "PRs, milestones, issue linking",
        "fields": [
            ("enabled", "bool"), ("owner", "str"), ("repo", "str"),
            ("create_prs", "bool"), ("create_milestones", "bool"),
            ("link_issues", "bool"), ("pr_base_branch", "str"),
            ("post_build_summary", "bool"),
        ],
    },
    {
        "name": "vercel",
        "label": "Vercel",
        "config_file": "vercel.json",
        "token_key": "vercel_token",
        "description": "Deployment status checks + auto-fix build errors",
        "fields": [
            ("enabled", "bool"), ("project_id", "str"), ("team_id", "str"),
            ("check_deployments", "bool"), ("deployment_timeout", "int"),
        ],
    },
    {
        "name": "linear",
        "label": "Linear",
        "config_file": "linear.json",
        "token_key": "linear_token",
        "description": "Issue sync, status updates, parked task creation",
        "fields": [
            ("enabled", "bool"), ("team_id", "str"), ("project_id", "str"),
            ("sync_issues", "bool"), ("create_issues_for_parked", "bool"),
            ("update_issue_status", "bool"),
        ],
    },
    {
        "name": "sentry",
        "label": "Sentry",
        "config_file": "sentry.json",
        "token_key": "sentry_token",
        "description": "Error monitoring + automatic fix tasks",
        "fields": [
            ("enabled", "bool"), ("org_slug", "str"), ("project_slug", "str"),
            ("auto_configure", "bool"), ("create_fix_tasks", "bool"),
            ("error_threshold", "int"),
        ],
    },
    {
        "name": "figma",
        "label": "Figma",
        "config_file": "figma.json",
        "token_key": "figma_token",
        "description": "Design tokens + component metadata extraction",
        "fields": [
            ("enabled", "bool"), ("file_key", "str"),
            ("generate_tokens", "bool"), ("export_frames", "bool"),
        ],
    },
    {
        "name": "ollama",
        "label": "Ollama",
        "config_file": "ollama.json",
        "token_key": None,
        "description": "Local LLM for planning (no API key needed)",
        "fields": [
            ("enabled", "bool"), ("host", "str"), ("model", "str"),
            ("use_for_planning", "bool"), ("use_for_evaluation", "bool"),
            ("timeout", "int"),
        ],
    },
]

_INTEGRATION_MAP = {i["name"]: i for i in INTEGRATIONS}


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


def _read_config(project_dir: Path, config_file: str) -> dict:
    """Read a .forge/{config_file} JSON. Returns {} on error."""
    try:
        p = project_dir / ".forge" / config_file
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _get_token(token_key: str | None) -> str:
    """Read a token from ~/.forge/profile.yaml. Returns '' on error."""
    if not token_key:
        return ""
    try:
        from forge.profile import load_profile
        profile = load_profile()
        return str(profile.get(token_key, ""))
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Data layer
# ---------------------------------------------------------------------------

def get_all_integration_statuses(project_dir: Path) -> dict:
    """
    Return status and config for all 6 integrations.
    Token values are redacted. Never raises.
    """
    try:
        result = {}
        for integ in INTEGRATIONS:
            config = _read_config(project_dir, integ["config_file"])
            token = _get_token(integ["token_key"])
            has_token = bool(token)
            enabled = config.get("enabled", False)

            # Redact any token-like values in config
            safe_config = dict(config)
            for k, v in safe_config.items():
                if isinstance(v, str) and "token" in k.lower() and v:
                    safe_config[k] = "***"

            result[integ["name"]] = {
                "label": integ["label"],
                "description": integ["description"],
                "enabled": enabled,
                "has_token": has_token,
                "config": safe_config,
                "fields": integ["fields"],
                "needs_token": integ["token_key"] is not None,
            }
        return result
    except Exception:
        return {}


def save_integration_config(
    project_dir: Path,
    name: str,
    config_data: dict,
    token: str = "",
) -> bool:
    """
    Save integration config to .forge/{name}.json.
    If token provided, save to ~/.forge/profile.yaml.
    Returns True on success, False on error. Never raises.
    """
    try:
        integ = _INTEGRATION_MAP.get(name)
        if not integ:
            return False

        forge_dir = project_dir / ".forge"
        forge_dir.mkdir(parents=True, exist_ok=True)

        target = forge_dir / integ["config_file"]
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(config_data, indent=2), encoding="utf-8")
        tmp.replace(target)

        if token and integ["token_key"]:
            from forge.profile import load_profile, save_profile
            profile = load_profile()
            profile[integ["token_key"]] = token
            save_profile(profile)

        return True
    except Exception:
        return False


def check_integration_connection(
    project_dir: Path, name: str
) -> tuple[bool, str]:
    """
    Test a single integration connection.
    Returns (success, message). Never raises.
    """
    try:
        if name == "ollama":
            return _test_ollama(project_dir)

        integ = _INTEGRATION_MAP.get(name)
        if not integ:
            return False, f"Unknown integration: {name}"

        token = _get_token(integ["token_key"])
        if not token:
            return False, "No token configured"

        if name == "github":
            return _test_api(
                "https://api.github.com/user",
                {"Authorization": f"Bearer {token}", "User-Agent": "forge"},
            )
        elif name == "vercel":
            return _test_api(
                "https://api.vercel.com/v2/user",
                {"Authorization": f"Bearer {token}"},
            )
        elif name == "linear":
            return _test_graphql(
                "https://api.linear.app/graphql",
                {"Authorization": token, "Content-Type": "application/json"},
                '{"query":"{ viewer { id } }"}',
            )
        elif name == "sentry":
            return _test_api(
                "https://sentry.io/api/0/",
                {"Authorization": f"Bearer {token}"},
            )
        elif name == "figma":
            return _test_api(
                "https://api.figma.com/v1/me",
                {"X-Figma-Token": token},
            )
        else:
            return False, f"Unknown integration: {name}"
    except Exception as e:
        return False, str(e)


def _test_ollama(project_dir: Path) -> tuple[bool, str]:
    """Test Ollama connectivity."""
    try:
        from forge.ollama_integration import load_ollama_config, is_ollama_reachable
        config = load_ollama_config(project_dir)
        if is_ollama_reachable(config):
            return True, f"Connected to Ollama ({config.model})"
        return False, "Ollama not reachable"
    except Exception as e:
        return False, str(e)


def _test_api(url: str, headers: dict) -> tuple[bool, str]:
    """Test a REST API endpoint."""
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        if resp.status == 200:
            return True, "Connected successfully"
        return False, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "Authentication failed (invalid token)"
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)


def _test_graphql(url: str, headers: dict, body: str) -> tuple[bool, str]:
    """Test a GraphQL endpoint."""
    req = urllib.request.Request(
        url, data=body.encode("utf-8"), headers=headers, method="POST"
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        if resp.status == 200:
            return True, "Connected successfully"
        return False, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "Authentication failed (invalid token)"
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

def handle_integrations_get(handler) -> None:
    """Serve INTEGRATIONS_HTML."""
    content = INTEGRATIONS_HTML.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(content)))
    handler.end_headers()
    handler.wfile.write(content)


def handle_integrations_data(handler, project_dir) -> None:
    """GET /integrations/data -> all integration statuses as JSON."""
    try:
        statuses = get_all_integration_statuses(project_dir) if project_dir else {}
        _send_json(handler, {"integrations": statuses})
    except Exception:
        _send_json(handler, {"integrations": {}})


def handle_integrations_save(handler, body: bytes, project_dir) -> None:
    """POST /integrations/save -> save config + token."""
    try:
        data = json.loads(body.decode("utf-8"))
        name = data.get("name", "")
        config = data.get("config", {})
        token = data.get("token", "")

        if not name or name not in _INTEGRATION_MAP:
            _send_json(handler, {"status": "error", "error": "Unknown integration"}, 400)
            return

        ok = save_integration_config(project_dir, name, config, token)
        if ok:
            try:
                from forge.dashboard import push_event
                push_event("integration_updated", {"name": name})
            except Exception:
                pass
            _send_json(handler, {"status": "ok"})
        else:
            _send_json(handler, {"status": "error", "error": "Failed to save"}, 500)
    except Exception as e:
        _send_json(handler, {"status": "error", "error": str(e)}, 400)


def handle_integrations_test(handler, project_dir, integration_name: str) -> None:
    """POST /integrations/test/{name} -> test connection."""
    try:
        success, message = check_integration_connection(project_dir, integration_name)
        _send_json(handler, {"success": success, "message": message})
    except Exception as e:
        _send_json(handler, {"success": False, "message": str(e)})


# ---------------------------------------------------------------------------
# Integrations HTML
# ---------------------------------------------------------------------------

_INTEGRATIONS_HEAD = """
<style>
  .card { background: #141414; border: 1px solid #2a2a2a; border-radius: 8px; margin-bottom: 12px; overflow: hidden; }
  .card-header { padding: 16px; cursor: pointer; display: flex; justify-content: space-between; align-items: center; }
  .card-header:hover { background: #1a1a1a; }
  .card-body { display: none; padding: 16px; border-top: 1px solid #2a2a2a; }
  .card-body.open { display: block; }
  .field-row { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
  .field-label { font-size: 12px; color: #999; width: 180px; flex-shrink: 0; }
  .field-input { background: #1a1a1a; border: 1px solid #333; border-radius: 4px; padding: 6px 10px; color: #e5e5e5; font-size: 13px; font-family: inherit; width: 100%; }
  .field-input:focus { outline: none; border-color: #00e5a0; }
  .toggle { position: relative; width: 36px; height: 20px; background: #333; border-radius: 10px; cursor: pointer; transition: background 0.2s; flex-shrink: 0; }
  .toggle.on { background: #00e5a0; }
  .toggle::after { content: ''; position: absolute; top: 2px; left: 2px; width: 16px; height: 16px; background: #fff; border-radius: 50%; transition: transform 0.2s; }
  .toggle.on::after { transform: translateX(16px); }
  .btn { padding: 6px 16px; border-radius: 4px; font-size: 12px; cursor: pointer; border: none; font-family: inherit; }
  .btn-primary { background: #00e5a0; color: #0f0f0f; font-weight: 600; }
  .btn-primary:hover { background: #00cc8e; }
  .btn-secondary { background: #2a2a2a; color: #ccc; }
  .btn-secondary:hover { background: #333; }
  .status-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
  .status-dot.connected { background: #00e5a0; }
  .status-dot.disconnected { background: #666; }
  .test-result { font-size: 12px; margin-top: 8px; padding: 6px 10px; border-radius: 4px; }
  .test-result.success { background: rgba(0,229,160,0.1); color: #00e5a0; }
  .test-result.failure { background: rgba(239,68,68,0.1); color: #ef4444; }
</style>
"""

_INTEGRATIONS_CONTENT = """
<div style="padding-top:56px;max-width:720px;margin:0 auto;padding-left:16px;padding-right:16px">

  <h1 style="font-size:20px;font-weight:700;color:#fff;margin-bottom:8px">Integrations</h1>
  <p style="font-size:12px;color:#666;margin-bottom:24px">Configure external service connections</p>

  <div id="cards-container"></div>

</div>
"""

_INTEGRATIONS_SCRIPTS = """
<script>
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}

let integrations = {};

async function loadIntegrations() {
  try {
    const resp = await fetch('/integrations/data');
    const data = await resp.json();
    integrations = data.integrations || {};
    renderCards();
  } catch(e) {
    console.error('Failed to load integrations:', e);
  }
}

function renderCards() {
  const container = document.getElementById('cards-container');
  container.innerHTML = '';
  const order = ['github','vercel','linear','sentry','figma','ollama'];
  order.forEach(name => {
    const integ = integrations[name];
    if (!integ) return;
    const connected = integ.enabled && (integ.has_token || !integ.needs_token);
    const statusClass = connected ? 'connected' : 'disconnected';
    const statusText = connected ? 'connected' : 'not set up';

    const card = document.createElement('div');
    card.className = 'card';
    card.id = 'card-' + name;

    let fieldsHtml = '';
    (integ.fields || []).forEach(([fieldName, fieldType]) => {
      const val = integ.config[fieldName];
      if (fieldName === 'enabled') return;
      if (fieldType === 'bool') {
        const on = val ? 'on' : '';
        fieldsHtml += '<div class="field-row"><span class="field-label">' + esc(fieldName) + '</span><div class="toggle ' + on + '" data-field="' + esc(fieldName) + '" onclick="toggleField(this)"></div></div>';
      } else if (fieldType === 'int') {
        fieldsHtml += '<div class="field-row"><span class="field-label">' + esc(fieldName) + '</span><input type="number" class="field-input" data-field="' + esc(fieldName) + '" value="' + (val || 0) + '"></div>';
      } else {
        fieldsHtml += '<div class="field-row"><span class="field-label">' + esc(fieldName) + '</span><input type="text" class="field-input" data-field="' + esc(fieldName) + '" value="' + esc(val || '') + '"></div>';
      }
    });

    let tokenHtml = '';
    if (integ.needs_token) {
      const placeholder = integ.has_token ? '****** (saved)' : 'Enter token...';
      tokenHtml = '<div class="field-row"><span class="field-label">API Token</span><input type="password" class="field-input" data-field="_token" placeholder="' + placeholder + '"></div>';
    }

    const enabledOn = integ.enabled ? 'on' : '';
    card.innerHTML = `
      <div class="card-header" onclick="toggleCard('${name}')">
        <div>
          <span style="font-size:14px;font-weight:600;color:#fff">${esc(integ.label)}</span>
          <span style="font-size:12px;color:#666;margin-left:12px">${esc(integ.description)}</span>
        </div>
        <div style="display:flex;align-items:center;gap:8px">
          <span style="font-size:11px;color:#666">${statusText}</span>
          <span class="status-dot ${statusClass}"></span>
        </div>
      </div>
      <div class="card-body" id="body-${name}">
        <div class="field-row">
          <span class="field-label">enabled</span>
          <div class="toggle ${enabledOn}" data-field="enabled" onclick="toggleField(this)"></div>
        </div>
        ${fieldsHtml}
        ${tokenHtml}
        <div style="display:flex;gap:8px;margin-top:16px">
          <button class="btn btn-primary" onclick="saveIntegration('${name}')">Save</button>
          <button class="btn btn-secondary" onclick="testConnection('${name}')">Test Connection</button>
        </div>
        <div id="result-${name}"></div>
      </div>
    `;
    container.appendChild(card);
  });
}

function toggleCard(name) {
  const body = document.getElementById('body-' + name);
  if (body) body.classList.toggle('open');
}

function toggleField(el) {
  el.classList.toggle('on');
  event.stopPropagation();
}

function collectConfig(name) {
  const card = document.getElementById('card-' + name);
  if (!card) return {config: {}, token: ''};
  const config = {};
  let token = '';

  card.querySelectorAll('[data-field]').forEach(el => {
    const field = el.dataset.field;
    if (field === '_token') {
      token = el.value || '';
      return;
    }
    if (el.classList.contains('toggle')) {
      config[field] = el.classList.contains('on');
    } else if (el.type === 'number') {
      config[field] = parseInt(el.value) || 0;
    } else {
      config[field] = el.value || '';
    }
  });
  return {config, token};
}

async function saveIntegration(name) {
  const {config, token} = collectConfig(name);
  const resultDiv = document.getElementById('result-' + name);
  try {
    const resp = await fetch('/integrations/save', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name, config, token}),
    });
    const data = await resp.json();
    if (data.status === 'ok') {
      resultDiv.innerHTML = '<div class="test-result success">Configuration saved</div>';
      setTimeout(() => loadIntegrations(), 500);
    } else {
      resultDiv.innerHTML = '<div class="test-result failure">Error: ' + esc(data.error || 'unknown') + '</div>';
    }
  } catch(e) {
    resultDiv.innerHTML = '<div class="test-result failure">Error: ' + esc(e.message) + '</div>';
  }
}

async function testConnection(name) {
  const resultDiv = document.getElementById('result-' + name);
  resultDiv.innerHTML = '<div class="test-result" style="color:#999">Testing connection...</div>';
  try {
    const resp = await fetch('/integrations/test/' + name, {method: 'POST'});
    const data = await resp.json();
    if (data.success) {
      resultDiv.innerHTML = '<div class="test-result success">' + esc(data.message) + '</div>';
    } else {
      resultDiv.innerHTML = '<div class="test-result failure">' + esc(data.message) + '</div>';
    }
  } catch(e) {
    resultDiv.innerHTML = '<div class="test-result failure">' + esc(e.message) + '</div>';
  }
}

loadIntegrations();
</script>
"""

INTEGRATIONS_HTML = page_shell("Integrations", "/integrations", _INTEGRATIONS_CONTENT, extra_head=_INTEGRATIONS_HEAD, extra_scripts=_INTEGRATIONS_SCRIPTS)
