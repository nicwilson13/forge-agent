"""
Setup wizard HTML and request handlers for the Forge local app.

Provides browser-based project setup as an alternative to the terminal
interview. Adds /setup routes to the existing DashboardHandler.

Called by forge new instead of the terminal interview when a browser
is available.

This module imports only stdlib. No forge imports at module level.
"""

import json
import os
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


def _write_file_atomic(path: Path, content: str) -> None:
    """Write content to path atomically via temp file then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------

def format_requirements_md(form_data) -> str:
    """
    Convert wizard form data to REQUIREMENTS.md content.

    Accepts a string (single textarea) or a dict (legacy multi-field form).
    """
    if isinstance(form_data, str):
        text = form_data.strip()
        if text:
            return f"# REQUIREMENTS.md\n\n{text}\n"
        return "# REQUIREMENTS.md\n"

    # Legacy dict format: keys core_features, pages_routes, etc.
    sections = [
        ("Core Features", form_data.get("core_features", "")),
        ("Pages and Routes", form_data.get("pages_routes", "")),
        ("Data Models", form_data.get("data_models", "")),
        ("Non-Functional Requirements", form_data.get("non_functional", "")),
    ]

    lines = ["# REQUIREMENTS.md", ""]
    for title, content in sections:
        lines.append(f"## {title}")
        lines.append("")
        if content and content.strip():
            for line in content.strip().splitlines():
                line = line.strip()
                if line:
                    lines.append(f"- [ ] {line}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Integration config writers
# ---------------------------------------------------------------------------

_INTEGRATION_DEFAULTS = {
    "github": {
        "config_file": "github.json",
        "token_key": "github_token",
        "fields": ["owner", "repo"],
        "defaults": {
            "create_prs": True,
            "create_milestones": True,
            "link_issues": True,
            "pr_base_branch": "main",
            "post_build_summary": True,
        },
    },
    "vercel": {
        "config_file": "vercel.json",
        "token_key": "vercel_token",
        "fields": ["project_id", "team_id"],
        "defaults": {
            "check_deployments": True,
            "deployment_timeout": 120,
        },
    },
    "linear": {
        "config_file": "linear.json",
        "token_key": "linear_token",
        "fields": ["team_id", "project_id"],
        "defaults": {
            "sync_issues": True,
            "create_issues_for_parked": True,
            "update_issue_status": True,
        },
    },
    "sentry": {
        "config_file": "sentry.json",
        "token_key": "sentry_token",
        "fields": ["org_slug", "project_slug"],
        "defaults": {
            "auto_configure": True,
            "create_fix_tasks": True,
            "error_threshold": 5,
        },
    },
    "figma": {
        "config_file": "figma.json",
        "token_key": "figma_token",
        "fields": ["file_key"],
        "defaults": {
            "generate_tokens": True,
            "export_frames": False,
            "frame_ids": [],
        },
    },
    "ollama": {
        "config_file": "ollama.json",
        "token_key": None,
        "fields": ["model"],
        "defaults": {
            "host": "http://localhost:11434",
            "use_for_planning": True,
            "use_for_evaluation": False,
            "timeout": 120,
        },
    },
}


def _write_integration_configs(project_dir: Path, integrations: dict) -> dict:
    """
    Write .forge/<name>.json for each enabled integration.

    Returns a dict of token_key -> token_value for saving to profile.
    """
    forge_dir = project_dir / ".forge"
    forge_dir.mkdir(parents=True, exist_ok=True)
    tokens = {}

    for name, spec in _INTEGRATION_DEFAULTS.items():
        int_data = integrations.get(name, {})
        if not int_data.get("enabled", False):
            continue

        config = {"enabled": True}
        # Copy user-provided fields
        for field in spec["fields"]:
            config[field] = int_data.get(field, "")
        # Merge defaults
        config.update(spec["defaults"])

        config_path = forge_dir / spec["config_file"]
        _write_file_atomic(config_path, json.dumps(config, indent=2) + "\n")

        # Collect token if provided
        if spec["token_key"] and int_data.get("token"):
            tokens[spec["token_key"]] = int_data["token"]

    return tokens


# ---------------------------------------------------------------------------
# Subprocess launcher
# ---------------------------------------------------------------------------

def start_forge_run_subprocess(project_dir: Path) -> None:
    """
    Start forge run as a background subprocess.

    Uses subprocess.Popen (non-blocking). Redirects output to
    .forge/run_output.log. Never raises.
    """
    try:
        log_path = project_dir / ".forge" / "run_output.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "w", encoding="utf-8")  # noqa: SIM115
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.CREATE_NO_WINDOW
            )
        proc = subprocess.Popen(
            [sys.executable, "-m", "forge",
             "--project-dir", str(project_dir), "run"],
            stdout=log_file,
            stderr=log_file,
            cwd=str(project_dir),
            **kwargs,
        )
        log_file.close()  # subprocess inherits the handle; avoid leak
    except Exception:
        # Create the log file anyway so the wait loop detects it
        try:
            log_path = project_dir / ".forge" / "run_output.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("forge run failed to start\n", encoding="utf-8")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

def handle_setup_get(handler) -> None:
    """Serve the setup wizard HTML."""
    content = SETUP_HTML.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(content)))
    handler.end_headers()
    handler.wfile.write(content)


def handle_setup_submit(handler, body: bytes, project_dir: Path) -> None:
    """
    Handle POST /setup/submit.

    Parses JSON body, writes VISION.md, REQUIREMENTS.md, integration
    configs, saves tokens to profile, starts forge run subprocess.
    """
    try:
        data = json.loads(body)

        project_dir = Path(project_dir).resolve()
        project_dir.mkdir(parents=True, exist_ok=True)

        # Write VISION.md
        vision = data.get("vision", "")
        if vision.strip():
            _write_file_atomic(project_dir / "VISION.md", vision)

        # Write REQUIREMENTS.md
        requirements = data.get("requirements", {})
        req_md = format_requirements_md(requirements)
        _write_file_atomic(project_dir / "REQUIREMENTS.md", req_md)

        # Write integration configs
        integrations = data.get("integrations", {})
        tokens = _write_integration_configs(project_dir, integrations)

        # Save tokens to profile
        if tokens:
            from forge.profile import load_profile, save_profile
            profile = load_profile()
            profile.update(tokens)
            save_profile(profile)

        # Start forge run
        start_forge_run_subprocess(project_dir)

        _send_json(handler, {"status": "ok", "redirect": "/"})

    except json.JSONDecodeError:
        _send_json(handler, {"status": "error", "error": "Invalid JSON"}, 400)
    except Exception as exc:
        _send_json(handler, {"status": "error", "error": str(exc)}, 500)


def handle_ai_assist(handler, body: bytes) -> None:
    """
    Handle POST /setup/ai-assist.

    Calls Claude API to draft VISION.md content from name + description.
    Returns {"vision": "...markdown..."} or {"error": "..."}.
    """
    try:
        data = json.loads(body)
        name = data.get("name", "")
        description = data.get("description", "")

        if not description.strip():
            _send_json(handler, {"error": "Description is required"}, 400)
            return

        import anthropic
        client = anthropic.Anthropic()

        system_prompt = (
            "You are helping a developer write a product vision document. "
            "Write a clear, concrete VISION.md for their project. "
            "Cover: what the product is, who it's for, core problem solved, "
            "key features, and success criteria. Be specific, not generic. "
            "Output markdown only."
        )

        user_msg = f"Project name: {name}\nDescription: {description}"

        response = client.messages.create(
            model="claude-sonnet-4-5-20241022",
            max_tokens=2048,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )

        vision_text = response.content[0].text
        _send_json(handler, {"vision": vision_text})

    except json.JSONDecodeError:
        _send_json(handler, {"error": "Invalid JSON"}, 400)
    except Exception as exc:
        _send_json(handler, {"error": f"AI assist failed: {exc}"}, 500)


def handle_setup_status(handler, project_dir: Path) -> None:
    """
    Handle GET /setup/status.

    Returns {"status": "idle"|"starting"|"running"}.
    """
    try:
        if project_dir is None:
            _send_json(handler, {"status": "idle"})
            return

        state_file = project_dir / ".forge" / "state.json"
        run_log = project_dir / ".forge" / "run_output.log"

        if state_file.exists():
            status = "running"
        elif run_log.exists():
            status = "starting"
        else:
            status = "idle"

        _send_json(handler, {"status": status})
    except Exception:
        _send_json(handler, {"status": "idle"})


# ---------------------------------------------------------------------------
# Setup Wizard HTML
# ---------------------------------------------------------------------------

_SETUP_HEAD = """
<style>
  .forge-green { color: #00e5a0; }
  .forge-green-bg { background: #00e5a0; }
  .card { background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 8px; }
  .input-field {
    background: #0f0f0f; border: 1px solid #2a2a2a; border-radius: 6px;
    color: #e5e5e5; padding: 10px 14px; width: 100%; outline: none;
    transition: border-color 0.2s;
  }
  .input-field:focus { border-color: #00e5a0; }
  .input-field::placeholder { color: #555; }
  textarea.input-field { resize: vertical; min-height: 120px; }
  .btn-primary {
    background: #00e5a0; color: #0f0f0f; font-weight: 600;
    padding: 10px 24px; border-radius: 6px; border: none; cursor: pointer;
    transition: opacity 0.2s;
  }
  .btn-primary:hover { opacity: 0.9; }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-secondary {
    background: #2a2a2a; color: #e5e5e5; font-weight: 600;
    padding: 10px 24px; border-radius: 6px; border: 1px solid #333;
    cursor: pointer; transition: opacity 0.2s;
  }
  .btn-secondary:hover { opacity: 0.8; }
  .step-indicator {
    display: flex; gap: 8px; justify-content: center; margin-bottom: 32px;
  }
  .step-dot {
    width: 10px; height: 10px; border-radius: 50%;
    background: #2a2a2a; transition: background 0.3s;
  }
  .step-dot.active { background: #00e5a0; }
  .step-dot.done { background: #00e5a0; opacity: 0.5; }
  .step-label { font-size: 12px; color: #555; text-align: center; }
  .step-label.active { color: #00e5a0; }
  .toggle-card {
    background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 8px;
    padding: 16px; cursor: pointer; transition: border-color 0.2s;
  }
  .toggle-card.enabled { border-color: #00e5a0; }
  .toggle-switch {
    width: 44px; height: 24px; border-radius: 12px;
    background: #2a2a2a; position: relative; transition: background 0.2s;
    flex-shrink: 0;
  }
  .toggle-switch.on { background: #00e5a0; }
  .toggle-switch::after {
    content: ''; position: absolute; width: 18px; height: 18px;
    border-radius: 50%; background: #e5e5e5; top: 3px; left: 3px;
    transition: transform 0.2s;
  }
  .toggle-switch.on::after { transform: translateX(20px); }
  .config-fields { max-height: 0; overflow: hidden; transition: max-height 0.3s; }
  .config-fields.open { max-height: 300px; }
  .step-panel { display: none; }
  .step-panel.active { display: block; }
  .preview-block {
    background: #0f0f0f; border: 1px solid #2a2a2a; border-radius: 6px;
    padding: 16px; white-space: pre-wrap; font-size: 13px;
    max-height: 300px; overflow-y: auto;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .spinner {
    display: inline-block; width: 16px; height: 16px;
    border: 2px solid #0f0f0f; border-top-color: transparent;
    border-radius: 50%; animation: spin 0.6s linear infinite;
    vertical-align: middle; margin-right: 8px;
  }
</style>
"""

_SETUP_CONTENT = """
<div class="max-w-2xl mx-auto px-6 pt-16 pb-12">

  <!-- Header -->
  <div class="text-center mb-8">
    <h1 class="text-3xl font-bold forge-green mb-2">Forge</h1>
    <p class="text-sm" style="color:#555">Project Setup Wizard</p>
  </div>

  <!-- Progress -->
  <div class="step-indicator" id="progress">
    <div class="text-center">
      <div class="step-dot active" id="dot-1"></div>
      <div class="step-label active" id="lbl-1">Basics</div>
    </div>
    <div style="flex:1;display:flex;align-items:center;padding-bottom:18px">
      <div style="height:1px;width:100%;background:#2a2a2a"></div>
    </div>
    <div class="text-center">
      <div class="step-dot" id="dot-2"></div>
      <div class="step-label" id="lbl-2">Vision</div>
    </div>
    <div style="flex:1;display:flex;align-items:center;padding-bottom:18px">
      <div style="height:1px;width:100%;background:#2a2a2a"></div>
    </div>
    <div class="text-center">
      <div class="step-dot" id="dot-3"></div>
      <div class="step-label" id="lbl-3">Requirements</div>
    </div>
    <div style="flex:1;display:flex;align-items:center;padding-bottom:18px">
      <div style="height:1px;width:100%;background:#2a2a2a"></div>
    </div>
    <div class="text-center">
      <div class="step-dot" id="dot-4"></div>
      <div class="step-label" id="lbl-4">Integrations</div>
    </div>
    <div style="flex:1;display:flex;align-items:center;padding-bottom:18px">
      <div style="height:1px;width:100%;background:#2a2a2a"></div>
    </div>
    <div class="text-center">
      <div class="step-dot" id="dot-5"></div>
      <div class="step-label" id="lbl-5">Launch</div>
    </div>
  </div>

  <!-- Step 1: Project Basics -->
  <div class="step-panel active" id="step-1">
    <div class="card p-6">
      <h2 class="text-lg font-bold mb-4">Step 1 <span style="color:#555">/ Project Basics</span></h2>
      <div class="mb-4">
        <label class="block text-sm mb-2" style="color:#888">App Name <span style="color:#e55">*</span></label>
        <input type="text" id="app-name" class="input-field" placeholder="My Awesome App" required>
      </div>
      <div class="mb-4">
        <label class="block text-sm mb-2" style="color:#888">One-sentence description <span style="color:#e55">*</span></label>
        <input type="text" id="app-description" class="input-field"
               placeholder="A platform that helps teams collaborate on design projects in real time">
      </div>
      <div class="mb-4">
        <label class="block text-sm mb-2" style="color:#888">Project Directory</label>
        <input type="text" id="project-dir" class="input-field" placeholder="(uses current directory)" disabled
               style="opacity:0.5">
      </div>
      <div class="flex justify-end mt-6">
        <button class="btn-primary" onclick="nextStep(1)">Next &rarr;</button>
      </div>
    </div>
  </div>

  <!-- Step 2: Vision -->
  <div class="step-panel" id="step-2">
    <div class="card p-6">
      <h2 class="text-lg font-bold mb-4">Step 2 <span style="color:#555">/ Vision</span></h2>
      <p class="text-sm mb-4" style="color:#888">
        Describe what you're building, who it's for, and the core problem it solves.
      </p>
      <div class="mb-3 flex justify-end">
        <button class="btn-secondary text-sm" id="ai-assist-btn" onclick="draftWithAI()"
                style="padding:6px 16px;font-size:13px">
          Draft with AI
        </button>
      </div>
      <textarea id="vision-text" class="input-field" style="min-height:200px"
                placeholder="Describe what you're building, who it's for, and the core problem it solves..."></textarea>
      <div class="text-right mt-1" style="color:#555;font-size:12px">
        <span id="vision-chars">0</span> characters
      </div>
      <div class="flex justify-between mt-6">
        <button class="btn-secondary" onclick="prevStep(2)">&larr; Back</button>
        <button class="btn-primary" onclick="nextStep(2)">Next &rarr;</button>
      </div>
    </div>
  </div>

  <!-- Step 3: Requirements -->
  <div class="step-panel" id="step-3">
    <div class="card p-6">
      <h2 class="text-lg font-bold mb-4">Step 3 <span style="color:#555">/ Requirements</span></h2>
      <p class="text-sm mb-4" style="color:#888">
        Describe what the app must do &mdash; features, pages, data models, constraints.
        Paste an existing requirements doc or write freeform.
      </p>
      <div class="mb-4">
        <label class="block text-sm mb-2" style="color:#888">Requirements</label>
        <textarea id="req-text" class="input-field" style="min-height:200px"
                  placeholder="## Core Features&#10;- User registration and login&#10;- Create and edit projects&#10;&#10;## Pages&#10;- Landing page&#10;- Dashboard&#10;&#10;## Data Models&#10;- User (email, name, role)&#10;- Project (title, description, members)"></textarea>
      </div>
      <div class="flex justify-between mt-6">
        <button class="btn-secondary" onclick="prevStep(3)">&larr; Back</button>
        <button class="btn-primary" onclick="nextStep(3)">Next &rarr;</button>
      </div>
    </div>
  </div>

  <!-- Step 4: Integrations -->
  <div class="step-panel" id="step-4">
    <div class="card p-6">
      <h2 class="text-lg font-bold mb-4">Step 4 <span style="color:#555">/ Integrations</span></h2>
      <p class="text-sm mb-4" style="color:#888">
        All optional. Toggle on any integrations you want to configure.
      </p>
      <div class="grid grid-cols-2 gap-4" id="integrations-grid">

        <!-- GitHub -->
        <div class="toggle-card" id="card-github">
          <div class="flex items-center justify-between mb-2" onclick="toggleIntegration('github')">
            <div>
              <div class="font-bold text-sm">GitHub</div>
              <div style="color:#555;font-size:11px">PRs, milestones, issues</div>
            </div>
            <div class="toggle-switch" id="toggle-github"></div>
          </div>
          <div class="config-fields" id="fields-github">
            <div class="mt-3">
              <input type="text" class="input-field text-sm mb-2" id="github-owner" placeholder="Owner (org or user)" style="padding:8px 10px;font-size:12px">
              <input type="text" class="input-field text-sm mb-2" id="github-repo" placeholder="Repository name" style="padding:8px 10px;font-size:12px">
              <input type="password" class="input-field text-sm" id="github-token" placeholder="GitHub token" style="padding:8px 10px;font-size:12px">
            </div>
          </div>
        </div>

        <!-- Vercel -->
        <div class="toggle-card" id="card-vercel">
          <div class="flex items-center justify-between mb-2" onclick="toggleIntegration('vercel')">
            <div>
              <div class="font-bold text-sm">Vercel</div>
              <div style="color:#555;font-size:11px">Deploy monitoring</div>
            </div>
            <div class="toggle-switch" id="toggle-vercel"></div>
          </div>
          <div class="config-fields" id="fields-vercel">
            <div class="mt-3">
              <input type="text" class="input-field text-sm mb-2" id="vercel-project_id" placeholder="Project ID" style="padding:8px 10px;font-size:12px">
              <input type="text" class="input-field text-sm mb-2" id="vercel-team_id" placeholder="Team ID (optional)" style="padding:8px 10px;font-size:12px">
              <input type="password" class="input-field text-sm" id="vercel-token" placeholder="Vercel token" style="padding:8px 10px;font-size:12px">
            </div>
          </div>
        </div>

        <!-- Linear -->
        <div class="toggle-card" id="card-linear">
          <div class="flex items-center justify-between mb-2" onclick="toggleIntegration('linear')">
            <div>
              <div class="font-bold text-sm">Linear</div>
              <div style="color:#555;font-size:11px">Issue tracking</div>
            </div>
            <div class="toggle-switch" id="toggle-linear"></div>
          </div>
          <div class="config-fields" id="fields-linear">
            <div class="mt-3">
              <input type="text" class="input-field text-sm mb-2" id="linear-team_id" placeholder="Team ID" style="padding:8px 10px;font-size:12px">
              <input type="text" class="input-field text-sm mb-2" id="linear-project_id" placeholder="Project ID (optional)" style="padding:8px 10px;font-size:12px">
              <input type="password" class="input-field text-sm" id="linear-token" placeholder="Linear API key" style="padding:8px 10px;font-size:12px">
            </div>
          </div>
        </div>

        <!-- Sentry -->
        <div class="toggle-card" id="card-sentry">
          <div class="flex items-center justify-between mb-2" onclick="toggleIntegration('sentry')">
            <div>
              <div class="font-bold text-sm">Sentry</div>
              <div style="color:#555;font-size:11px">Error monitoring</div>
            </div>
            <div class="toggle-switch" id="toggle-sentry"></div>
          </div>
          <div class="config-fields" id="fields-sentry">
            <div class="mt-3">
              <input type="text" class="input-field text-sm mb-2" id="sentry-org_slug" placeholder="Organization slug" style="padding:8px 10px;font-size:12px">
              <input type="text" class="input-field text-sm mb-2" id="sentry-project_slug" placeholder="Project slug" style="padding:8px 10px;font-size:12px">
              <input type="password" class="input-field text-sm" id="sentry-token" placeholder="Sentry auth token" style="padding:8px 10px;font-size:12px">
            </div>
          </div>
        </div>

        <!-- Figma -->
        <div class="toggle-card" id="card-figma">
          <div class="flex items-center justify-between mb-2" onclick="toggleIntegration('figma')">
            <div>
              <div class="font-bold text-sm">Figma</div>
              <div style="color:#555;font-size:11px">Design tokens</div>
            </div>
            <div class="toggle-switch" id="toggle-figma"></div>
          </div>
          <div class="config-fields" id="fields-figma">
            <div class="mt-3">
              <input type="text" class="input-field text-sm mb-2" id="figma-file_key" placeholder="Figma file key" style="padding:8px 10px;font-size:12px">
              <input type="password" class="input-field text-sm" id="figma-token" placeholder="Figma access token" style="padding:8px 10px;font-size:12px">
            </div>
          </div>
        </div>

        <!-- Ollama -->
        <div class="toggle-card" id="card-ollama">
          <div class="flex items-center justify-between mb-2" onclick="toggleIntegration('ollama')">
            <div>
              <div class="font-bold text-sm">Ollama</div>
              <div style="color:#555;font-size:11px">Local LLM</div>
            </div>
            <div class="toggle-switch" id="toggle-ollama"></div>
          </div>
          <div class="config-fields" id="fields-ollama">
            <div class="mt-3">
              <input type="text" class="input-field text-sm" id="ollama-model" placeholder="Model (e.g. llama3.1:8b)" style="padding:8px 10px;font-size:12px" value="llama3.1:8b">
            </div>
          </div>
        </div>

      </div>
      <div class="flex justify-between mt-6">
        <button class="btn-secondary" onclick="prevStep(4)">&larr; Back</button>
        <button class="btn-primary" onclick="nextStep(4)">Next &rarr;</button>
      </div>
    </div>
  </div>

  <!-- Step 5: Review & Launch -->
  <div class="step-panel" id="step-5">
    <div class="card p-6">
      <h2 class="text-lg font-bold mb-4">Step 5 <span style="color:#555">/ Review &amp; Launch</span></h2>

      <div class="mb-4">
        <h3 class="text-sm font-bold mb-2" style="color:#888">VISION.md Preview</h3>
        <div class="preview-block" id="preview-vision"></div>
      </div>

      <div class="mb-4">
        <h3 class="text-sm font-bold mb-2" style="color:#888">REQUIREMENTS.md Preview</h3>
        <div class="preview-block" id="preview-requirements"></div>
      </div>

      <div class="mb-4">
        <h3 class="text-sm font-bold mb-2" style="color:#888">Integrations</h3>
        <div id="preview-integrations" style="color:#888;font-size:13px">None enabled</div>
      </div>

      <div class="flex justify-between mt-6">
        <button class="btn-secondary" onclick="prevStep(5)">&larr; Back</button>
        <button class="btn-primary" id="launch-btn" onclick="startBuilding()"
                style="padding:12px 32px;font-size:16px">
          Start Building
        </button>
      </div>
    </div>
  </div>

</div>
"""

_SETUP_SCRIPTS = """
<script>
// State
let currentStep = 1;
const integrations = {};

// Pre-fill description from query param
(function() {
  const params = new URLSearchParams(window.location.search);
  const desc = params.get('description');
  if (desc) document.getElementById('app-description').value = desc;
})();

// Step navigation
function showStep(n) {
  for (let i = 1; i <= 5; i++) {
    document.getElementById('step-' + i).classList.toggle('active', i === n);
    const dot = document.getElementById('dot-' + i);
    const lbl = document.getElementById('lbl-' + i);
    dot.className = 'step-dot' + (i === n ? ' active' : (i < n ? ' done' : ''));
    lbl.className = 'step-label' + (i === n ? ' active' : '');
  }
  currentStep = n;
  if (n === 5) buildPreview();
}

function nextStep(from) {
  // Validate
  if (from === 1) {
    if (!document.getElementById('app-name').value.trim()) {
      document.getElementById('app-name').focus();
      return;
    }
    if (!document.getElementById('app-description').value.trim()) {
      document.getElementById('app-description').focus();
      return;
    }
  }
  showStep(from + 1);
}

function prevStep(from) {
  showStep(from - 1);
}

// Character count for vision
document.getElementById('vision-text').addEventListener('input', function() {
  document.getElementById('vision-chars').textContent = this.value.length;
});

// AI Assist
async function draftWithAI() {
  const btn = document.getElementById('ai-assist-btn');
  const name = document.getElementById('app-name').value;
  const description = document.getElementById('app-description').value;

  if (!description.trim()) {
    alert('Please enter a description in Step 1 first.');
    return;
  }

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Drafting...';

  try {
    const resp = await fetch('/setup/ai-assist', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name, description})
    });
    const data = await resp.json();
    if (data.vision) {
      document.getElementById('vision-text').value = data.vision;
      document.getElementById('vision-chars').textContent = data.vision.length;
    } else if (data.error) {
      alert('AI assist error: ' + data.error);
    }
  } catch (e) {
    alert('Failed to connect: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = 'Draft with AI';
  }
}

// Integration toggles
function toggleIntegration(name) {
  const enabled = !integrations[name];
  integrations[name] = enabled;
  document.getElementById('card-' + name).classList.toggle('enabled', enabled);
  document.getElementById('toggle-' + name).classList.toggle('on', enabled);
  document.getElementById('fields-' + name).classList.toggle('open', enabled);
}

// Build preview for Step 5
function buildPreview() {
  const vision = document.getElementById('vision-text').value || '(No vision provided)';
  document.getElementById('preview-vision').textContent = vision;

  // Build requirements preview
  const reqText = document.getElementById('req-text').value || '(No requirements provided)';
  document.getElementById('preview-requirements').textContent = reqText;

  // Integration summary
  const enabled = Object.entries(integrations).filter(([,v]) => v).map(([k]) => k);
  document.getElementById('preview-integrations').textContent =
    enabled.length ? enabled.map(n => n.charAt(0).toUpperCase() + n.slice(1)).join(', ') : 'None enabled';
}

// Collect integration data
function collectIntegrations() {
  const result = {};
  const fieldMap = {
    github: ['owner', 'repo', 'token'],
    vercel: ['project_id', 'team_id', 'token'],
    linear: ['team_id', 'project_id', 'token'],
    sentry: ['org_slug', 'project_slug', 'token'],
    figma: ['file_key', 'token'],
    ollama: ['model']
  };
  for (const [name, fields] of Object.entries(fieldMap)) {
    if (!integrations[name]) continue;
    const data = {enabled: true};
    for (const f of fields) {
      const el = document.getElementById(name + '-' + f);
      if (el) data[f] = el.value;
    }
    result[name] = data;
  }
  return result;
}

// Submit
async function startBuilding() {
  const btn = document.getElementById('launch-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Starting...';

  const payload = {
    name: document.getElementById('app-name').value,
    description: document.getElementById('app-description').value,
    vision: document.getElementById('vision-text').value,
    requirements: document.getElementById('req-text').value,
    integrations: collectIntegrations()
  };

  try {
    const resp = await fetch('/setup/submit', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await resp.json();
    if (data.status === 'ok') {
      btn.innerHTML = 'Redirecting...';
      setTimeout(() => { window.location.href = data.redirect || '/'; }, 1500);
    } else {
      alert('Error: ' + (data.error || 'Unknown error'));
      btn.disabled = false;
      btn.innerHTML = 'Start Building';
    }
  } catch (e) {
    alert('Failed to submit: ' + e.message);
    btn.disabled = false;
    btn.innerHTML = 'Start Building';
  }
}
</script>
"""

SETUP_HTML = page_shell("Setup", "/setup", _SETUP_CONTENT, extra_head=_SETUP_HEAD, extra_scripts=_SETUP_SCRIPTS)
