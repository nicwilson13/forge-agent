"""
Vercel API integration for Forge.

Checks deployment status after each phase commit. Surfaces preview URLs
and feeds build failures back to Claude Code for automatic fixing.

Configuration: .forge/vercel.json (project-level)
Token: ~/.forge/profile.yaml vercel_token field (user-level)

All operations non-fatal. Build continues on any Vercel API failure.

This module imports only stdlib. No forge imports.
"""

import json
import sys
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, asdict
from pathlib import Path

def _supports_unicode() -> bool:
    encoding = getattr(sys.stdout, "encoding", "") or ""
    return encoding.lower().replace("-", "") in ("utf8", "utf16", "utf32", "utf8sig")

_SYM_OK = "\u2713" if _supports_unicode() else "[OK]"
_SYM_FAIL = "\u2717" if _supports_unicode() else "[FAIL]"
_SYM_WARN = "\u26a0" if _supports_unicode() else "[WARN]"


@dataclass
class VercelConfig:
    enabled: bool = False
    project_id: str = ""
    team_id: str = ""
    check_deployments: bool = True
    deployment_timeout: int = 120  # seconds to wait for deployment


def load_vercel_config(project_dir: Path) -> VercelConfig:
    """
    Load from .forge/vercel.json. Never raises.
    Returns disabled VercelConfig if file missing or invalid.
    """
    try:
        path = project_dir / ".forge" / "vercel.json"
        if not path.exists():
            return VercelConfig()
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return VercelConfig()
        return VercelConfig(
            enabled=data.get("enabled", False),
            project_id=data.get("project_id", ""),
            team_id=data.get("team_id", ""),
            check_deployments=data.get("check_deployments", True),
            deployment_timeout=data.get("deployment_timeout", 120),
        )
    except Exception:
        return VercelConfig()


def save_vercel_config(project_dir: Path, config: VercelConfig) -> None:
    """Save to .forge/vercel.json. Never raises."""
    try:
        forge_dir = project_dir / ".forge"
        forge_dir.mkdir(parents=True, exist_ok=True)
        path = forge_dir / "vercel.json"
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(config), f, indent=2)
        tmp.replace(path)
    except Exception:
        pass


def get_vercel_token() -> str:
    """
    Read vercel_token from ~/.forge/profile.yaml.
    Returns empty string if not set. Never raises.
    """
    try:
        import yaml
        path = Path.home() / ".forge" / "profile.yaml"
        if not path.exists():
            return ""
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            return data.get("vercel_token", "") or ""
        return ""
    except Exception:
        return ""


def _vercel_request(
    method: str,
    endpoint: str,
    token: str,
    team_id: str = "",
    data: dict | None = None,
) -> dict | None:
    """
    Make a Vercel REST API request.

    Base URL: https://api.vercel.com
    If team_id set, append ?teamId={team_id} to all requests.
    Timeout: 15s. Never raises.
    """
    try:
        url = f"https://api.vercel.com{endpoint}"
        if team_id:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}teamId={team_id}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "Forge-Agent",
        }
        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def get_latest_deployment(
    config: VercelConfig,
    token: str,
    git_sha: str = "",
) -> dict | None:
    """
    Get the latest deployment for the project.

    GET /v6/deployments?projectId={id}&limit=1
    If git_sha provided, filter by meta.githubCommitSha.
    Returns deployment dict or None.
    """
    if not config.enabled or not token or not config.project_id:
        return None
    try:
        endpoint = f"/v6/deployments?projectId={config.project_id}&limit=5"
        result = _vercel_request("GET", endpoint, token, team_id=config.team_id)
        if not result or "deployments" not in result:
            return None
        deployments = result["deployments"]
        if not deployments:
            return None

        if git_sha:
            for dep in deployments:
                meta = dep.get("meta", {})
                if meta.get("githubCommitSha", "") == git_sha:
                    return dep
            # Fall back to most recent if SHA not found yet
            return deployments[0]

        return deployments[0]
    except Exception:
        return None


def wait_for_deployment(
    config: VercelConfig,
    token: str,
    git_sha: str = "",
) -> tuple[str, str]:
    """
    Poll until deployment is READY or ERROR, or timeout.

    Polls every 5 seconds up to config.deployment_timeout.
    Returns (status, url) where:
    - status: "ready" | "error" | "timeout" | "skipped"
    - url: deployment URL if ready, error message if error, "" otherwise

    Prints progress dots while waiting.
    Never raises.
    """
    if not config.enabled or not config.check_deployments:
        return ("skipped", "")
    if not token or not config.project_id:
        return ("skipped", "")

    try:
        elapsed = 0
        poll_interval = 5

        while elapsed < config.deployment_timeout:
            dep = get_latest_deployment(config, token, git_sha)
            if dep:
                state = dep.get("state", "").upper()
                ready_state = dep.get("readyState", "").upper()
                effective_state = ready_state or state

                if effective_state == "READY":
                    url = dep.get("url", "")
                    if url and not url.startswith("https://"):
                        url = f"https://{url}"
                    return ("ready", url)

                if effective_state in ("ERROR", "CANCELED"):
                    # Try to get error message
                    error_msg = ""
                    if dep.get("errorMessage"):
                        error_msg = dep["errorMessage"]
                    elif dep.get("errorCode"):
                        error_msg = dep["errorCode"]
                    return ("error", error_msg)

            print(".", end="", flush=True)
            time.sleep(poll_interval)
            elapsed += poll_interval

        print()
        return ("timeout", "")
    except Exception:
        return ("skipped", "")


def get_deployment_build_logs(
    config: VercelConfig,
    token: str,
    deployment_id: str,
) -> str:
    """
    Fetch build logs for a failed deployment.

    GET /v2/deployments/{id}/events
    Returns the last 50 lines of build output as a string.
    Used to feed build errors back to Claude Code.
    Never raises.
    """
    if not token or not deployment_id:
        return ""
    try:
        endpoint = f"/v2/deployments/{deployment_id}/events"
        result = _vercel_request("GET", endpoint, token, team_id=config.team_id)
        if not result or not isinstance(result, list):
            return ""

        lines = []
        for event in result:
            if isinstance(event, dict):
                text = event.get("text", "")
                if text:
                    lines.append(text)

        # Return last 50 lines
        return "\n".join(lines[-50:])
    except Exception:
        return ""


def run_vercel_check(
    project_dir: Path,
    git_sha: str = "",
) -> tuple[str, str, str]:
    """
    Full Vercel deployment check pipeline.

    1. Load config and token
    2. wait_for_deployment()
    3. On error: get_deployment_build_logs()

    Returns (status, url_or_message, build_logs).
    status: "ready" | "error" | "timeout" | "disabled" | "skipped"
    Never raises.
    """
    try:
        config = load_vercel_config(project_dir)
        if not config.enabled:
            return ("disabled", "", "")

        token = get_vercel_token()
        if not token:
            return ("skipped", "vercel_token not set", "")

        if not config.project_id:
            return ("skipped", "project_id not set", "")

        status, url_or_msg = wait_for_deployment(config, token, git_sha)

        build_logs = ""
        if status == "error":
            # Try to fetch build logs
            dep = get_latest_deployment(config, token, git_sha)
            if dep:
                dep_id = dep.get("uid", "") or dep.get("id", "")
                if dep_id:
                    build_logs = get_deployment_build_logs(config, token, dep_id)

        return (status, url_or_msg, build_logs)
    except Exception:
        return ("skipped", "", "")


def format_vercel_status(status: str, url_or_msg: str) -> str:
    """
    Format deployment status for terminal display.

    ready:    "✓ Vercel: deployment ready → {url}"
    error:    "✗ Vercel: deployment failed"
    timeout:  "⚠ Vercel: deployment check timed out"
    disabled: "(Vercel integration not configured)"
    skipped:  "(Vercel check skipped)"
    """
    _arrow = "\u2192" if _supports_unicode() else "->"
    if status == "ready":
        if url_or_msg:
            return f"{_SYM_OK} Vercel: deployment ready {_arrow} {url_or_msg}"
        return f"{_SYM_OK} Vercel: deployment ready"
    elif status == "error":
        if url_or_msg:
            return f"{_SYM_FAIL} Vercel: deployment failed - {url_or_msg}"
        return f"{_SYM_FAIL} Vercel: deployment failed"
    elif status == "timeout":
        return f"{_SYM_WARN} Vercel: deployment check timed out"
    elif status == "disabled":
        return "(Vercel integration not configured)"
    else:
        if url_or_msg:
            return f"(Vercel check skipped - {url_or_msg})"
        return "(Vercel check skipped)"
