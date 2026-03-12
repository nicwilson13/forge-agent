"""
Sentry error monitoring integration for Forge.

Queries Sentry for unresolved errors after phase deployments and
creates fix tasks automatically. Optionally auto-configures Sentry
SDK in the project during setup.

Configuration: .forge/sentry.json (project-level)
Token: ~/.forge/profile.yaml sentry_token field (user-level)

Uses Sentry REST API: https://sentry.io/api/0/
All operations non-fatal. Build continues on any Sentry API failure.

This module imports only stdlib. No forge imports.
"""

import json
import urllib.request
import urllib.error
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class SentryConfig:
    enabled: bool = False
    org_slug: str = ""
    project_slug: str = ""
    auto_configure: bool = True
    create_fix_tasks: bool = True
    error_threshold: int = 5  # min occurrences to create fix task


def load_sentry_config(project_dir: Path) -> SentryConfig:
    """Load from .forge/sentry.json. Never raises."""
    try:
        path = project_dir / ".forge" / "sentry.json"
        if not path.exists():
            return SentryConfig()
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return SentryConfig()
        return SentryConfig(
            enabled=data.get("enabled", False),
            org_slug=data.get("org_slug", ""),
            project_slug=data.get("project_slug", ""),
            auto_configure=data.get("auto_configure", True),
            create_fix_tasks=data.get("create_fix_tasks", True),
            error_threshold=data.get("error_threshold", 5),
        )
    except Exception:
        return SentryConfig()


def save_sentry_config(project_dir: Path, config: SentryConfig) -> None:
    """Save to .forge/sentry.json. Never raises."""
    try:
        forge_dir = project_dir / ".forge"
        forge_dir.mkdir(parents=True, exist_ok=True)
        path = forge_dir / "sentry.json"
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(config), f, indent=2)
        tmp.replace(path)
    except Exception:
        pass


def get_sentry_token() -> str:
    """Read sentry_token from ~/.forge/profile.yaml. Never raises."""
    try:
        import yaml
        path = Path.home() / ".forge" / "profile.yaml"
        if not path.exists():
            return ""
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            return data.get("sentry_token", "") or ""
        return ""
    except Exception:
        return ""


def _sentry_request(
    endpoint: str,
    token: str,
    method: str = "GET",
    data: dict | None = None,
) -> dict | list | None:
    """
    Make a Sentry REST API request.

    Base URL: https://sentry.io/api/0
    Header: Authorization: Bearer {token}
    Timeout: 15s. Never raises. Returns parsed JSON or None.
    """
    try:
        url = f"https://sentry.io/api/0/{endpoint.lstrip('/')}"
        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")

        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")

        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def get_unresolved_issues(
    config: SentryConfig,
    token: str,
    limit: int = 10,
) -> list[dict]:
    """
    Fetch unresolved issues from the Sentry project.

    Filters to issues with count >= config.error_threshold.
    Returns empty list on error or if disabled.
    """
    if not config.enabled or not token or not config.org_slug or not config.project_slug:
        return []
    try:
        endpoint = (
            f"/projects/{config.org_slug}/{config.project_slug}/issues/"
            f"?query=is:unresolved&limit={limit}&sort=freq"
        )
        result = _sentry_request(endpoint, token)
        if not isinstance(result, list):
            return []

        filtered = []
        for issue in result:
            count = int(issue.get("count", 0))
            if count >= config.error_threshold:
                filtered.append({
                    "id": issue.get("id", ""),
                    "title": issue.get("title", ""),
                    "culprit": issue.get("culprit", ""),
                    "count": str(count),
                    "userCount": issue.get("userCount", 0),
                    "permalink": issue.get("permalink", ""),
                })
        return filtered
    except Exception:
        return []


def get_recent_events(
    config: SentryConfig,
    token: str,
    issue_id: str,
    limit: int = 3,
) -> list[dict]:
    """
    Fetch recent events for a Sentry issue.

    Returns list of event dicts with stack trace info.
    Returns empty list on error.
    """
    if not config.enabled or not token or not issue_id:
        return []
    try:
        endpoint = f"/issues/{issue_id}/events/?limit={limit}"
        result = _sentry_request(endpoint, token)
        if not isinstance(result, list):
            return []
        return result
    except Exception:
        return []


def format_issue_as_fix_task(
    issue: dict,
    events: list[dict],
) -> tuple[str, str]:
    """
    Convert a Sentry issue into a (task_title, task_description) pair.

    Title: "Fix: {issue title truncated to 60 chars}"
    Description includes error details, location, count, stack trace, permalink.
    """
    raw_title = issue.get("title", "Unknown error")
    truncated = raw_title[:60] + "..." if len(raw_title) > 60 else raw_title
    task_title = f"Fix: {truncated}"

    culprit = issue.get("culprit", "unknown location")
    count = issue.get("count", "?")
    user_count = issue.get("userCount", 0)
    permalink = issue.get("permalink", "")

    lines = [
        f"Fix the following Sentry error that is occurring in production.",
        f"",
        f"**Error:** {raw_title}",
        f"**Location:** {culprit}",
        f"**Occurrences:** {count} events, {user_count} users affected",
    ]

    if permalink:
        lines.append(f"**Sentry link:** {permalink}")

    # Extract stack trace from first event if available
    if events:
        first_event = events[0]
        entries = first_event.get("entries", [])
        for entry in entries:
            if entry.get("type") == "exception":
                exc_data = entry.get("data", {})
                values = exc_data.get("values", [])
                if values:
                    frames = values[0].get("stacktrace", {}).get("frames", [])
                    if frames:
                        lines.append("")
                        lines.append("**Stack trace (most recent frames):**")
                        lines.append("```")
                        for frame in frames[-5:]:
                            filename = frame.get("filename", "?")
                            lineno = frame.get("lineNo", "?")
                            func = frame.get("function", "?")
                            lines.append(f"  {filename}:{lineno} in {func}")
                        lines.append("```")
                break

    lines.append("")
    lines.append("Investigate the root cause and apply a fix. Add error handling if needed.")

    return task_title, "\n".join(lines)


def generate_sentry_setup_instructions(config: SentryConfig) -> str:
    """
    Generate setup instructions for adding Sentry to a Next.js project.

    Returns a task description for a setup task that Claude Code can execute.
    """
    org = config.org_slug or "<your-org>"
    project = config.project_slug or "<your-project>"

    return f"""Configure Sentry error monitoring for the project.

Steps:
1. Install the Sentry SDK:
   npm install @sentry/nextjs

2. Run the Sentry wizard to auto-configure:
   npx @sentry/wizard@latest -i nextjs --org {org} --project {project}

3. Add SENTRY_DSN to .env.example:
   SENTRY_DSN=https://examplePublicKey@o0.ingest.sentry.io/0

4. Set SENTRY_DSN as an environment variable in the deployment platform
   (Vercel, etc.) - do NOT hardcode the actual DSN in source code.

5. Verify that sentry.client.config.ts and sentry.server.config.ts
   were created by the wizard. If not, create them manually with
   Sentry.init() calls.

The DSN will be provided via the SENTRY_DSN environment variable at runtime.
Organization: {org}
Project: {project}
"""


def check_and_create_fix_tasks(
    config: SentryConfig,
    token: str,
) -> list[tuple[str, str]]:
    """
    Check for unresolved Sentry issues and return fix tasks.

    Returns list of (title, description) tuples.
    Returns empty list on error or if disabled.
    Never raises.
    """
    try:
        if not config.enabled or not config.create_fix_tasks:
            return []

        issues = get_unresolved_issues(config, token)
        if not issues:
            return []

        fix_tasks = []
        for issue in issues:
            events = get_recent_events(config, token, issue["id"])
            title, desc = format_issue_as_fix_task(issue, events)
            fix_tasks.append((title, desc))

        return fix_tasks
    except Exception:
        return []


def run_sentry_check(
    project_dir: Path,
) -> list[tuple[str, str]]:
    """
    Full Sentry check pipeline.

    Loads config + token, calls check_and_create_fix_tasks().
    Prints status to stdout.
    Returns list of (title, description) fix tasks, or empty list.
    Never raises.
    """
    try:
        config = load_sentry_config(project_dir)
        if not config.enabled:
            return []

        token = get_sentry_token()
        if not token:
            print("  (Sentry integration enabled but sentry_token not set)")
            return []

        if not config.org_slug or not config.project_slug:
            print("  (Sentry integration enabled but org_slug or project_slug not set)")
            return []

        print("  [sentry] Checking for new errors...")
        fix_tasks = check_and_create_fix_tasks(config, token)

        if fix_tasks:
            print(f"  [sentry] {len(fix_tasks)} unresolved issue(s) found")
            for title, _ in fix_tasks:
                print(f"    - {title}")
        else:
            print("  [sentry] No unresolved issues above threshold")

        return fix_tasks
    except Exception:
        return []
