"""
GitHub API integration for Forge.

Connects Forge to the GitHub REST API to create PRs, milestones,
and build summary comments when phases complete.

All operations are optional and non-fatal. If GitHub integration
fails for any reason, the build continues normally.

Configuration: .forge/github.json (project-level)
Token: ~/.forge/profile.yaml github_token field (user-level)

This module imports only stdlib plus forge.state for type hints.
"""

import json
import re
import urllib.request
import urllib.error
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass
class GitHubConfig:
    enabled: bool = False
    owner: str = ""
    repo: str = ""
    create_prs: bool = True
    create_milestones: bool = True
    link_issues: bool = True
    pr_base_branch: str = "main"
    post_build_summary: bool = True


def load_github_config(project_dir: Path) -> GitHubConfig:
    """
    Load GitHub config from .forge/github.json.
    Returns disabled GitHubConfig if file missing or invalid.
    Never raises.
    """
    try:
        path = project_dir / ".forge" / "github.json"
        if not path.exists():
            return GitHubConfig()
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return GitHubConfig()
        return GitHubConfig(
            enabled=data.get("enabled", False),
            owner=data.get("owner", ""),
            repo=data.get("repo", ""),
            create_prs=data.get("create_prs", True),
            create_milestones=data.get("create_milestones", True),
            link_issues=data.get("link_issues", True),
            pr_base_branch=data.get("pr_base_branch", "main"),
            post_build_summary=data.get("post_build_summary", True),
        )
    except Exception:
        return GitHubConfig()


def save_github_config(project_dir: Path, config: GitHubConfig) -> None:
    """Save GitHub config to .forge/github.json. Never raises."""
    try:
        forge_dir = project_dir / ".forge"
        forge_dir.mkdir(parents=True, exist_ok=True)
        path = forge_dir / "github.json"
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(config), f, indent=2)
        tmp.replace(path)
    except Exception:
        pass


def get_github_token() -> str:
    """
    Read GitHub token from ~/.forge/profile.yaml github_token field.
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
            return data.get("github_token", "") or ""
        return ""
    except Exception:
        return ""


def _github_request(
    method: str,
    endpoint: str,
    token: str,
    data: dict | None = None,
) -> dict | None:
    """
    Make a GitHub REST API request.

    Base URL: https://api.github.com
    Headers: Authorization: Bearer {token}, Accept: application/vnd.github+json
    Returns parsed JSON response dict, or None on any error.
    Timeout: 15 seconds.
    Never raises.
    """
    try:
        url = f"https://api.github.com{endpoint}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Forge-Agent",
        }
        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def get_open_issues(
    config: GitHubConfig, token: str, limit: int = 20
) -> list[dict]:
    """
    Fetch open issues from the GitHub repo.

    Returns list of issue dicts with: number, title, body, labels.
    Returns empty list on error or if integration disabled.
    Used by orchestrator to enrich task generation context.
    """
    if not config.enabled or not token or not config.owner or not config.repo:
        return []
    try:
        endpoint = f"/repos/{config.owner}/{config.repo}/issues?state=open&per_page={limit}"
        result = _github_request("GET", endpoint, token)
        if not isinstance(result, list):
            return []
        issues = []
        for item in result:
            # Skip pull requests (GitHub API returns PRs as issues too)
            if "pull_request" in item:
                continue
            issues.append({
                "number": item.get("number", 0),
                "title": item.get("title", ""),
                "body": (item.get("body") or "")[:500],
                "labels": [
                    {"name": lbl.get("name", "")}
                    for lbl in (item.get("labels") or [])
                ],
            })
        return issues
    except Exception:
        return []


def create_milestone(
    config: GitHubConfig, token: str, phase_title: str, phase_num: int
) -> int | None:
    """
    Create a GitHub milestone for a Forge phase.

    Title: "Phase {N}: {phase_title}"
    Returns milestone number, or None on error/disabled.
    """
    if not config.enabled or not config.create_milestones or not token:
        return None
    try:
        endpoint = f"/repos/{config.owner}/{config.repo}/milestones"
        title = f"Phase {phase_num}: {phase_title}"
        data = {"title": title}
        result = _github_request("POST", endpoint, token, data)
        if result and "number" in result:
            return result["number"]
        return None
    except Exception:
        return None


def close_milestone(
    config: GitHubConfig, token: str, milestone_number: int
) -> bool:
    """
    Mark a milestone as closed (phase complete).
    Returns True on success, False on error.
    """
    if not config.enabled or not token:
        return False
    try:
        endpoint = f"/repos/{config.owner}/{config.repo}/milestones/{milestone_number}"
        data = {"state": "closed"}
        result = _github_request("PATCH", endpoint, token, data)
        return result is not None
    except Exception:
        return False


def create_phase_pr(
    config: GitHubConfig,
    token: str,
    phase: Any,
    phase_num: int,
    branch: str,
    milestone_number: int | None = None,
) -> dict | None:
    """
    Create a GitHub PR for a completed phase.

    Title: "feat: phase-{N}-{slug}"
    Branch: current working branch
    Base: config.pr_base_branch
    Body: formatted phase summary (tasks completed, cost, health grade)
    Milestone: if provided, links PR to milestone

    Returns PR dict (includes html_url), or None on error.
    """
    if not config.enabled or not config.create_prs or not token:
        return None
    if not branch or branch == config.pr_base_branch:
        return None
    try:
        slug = re.sub(r"[^a-z0-9]+", "-", phase.title.lower()).strip("-")[:40]
        title = f"feat: phase-{phase_num}-{slug}"

        tasks_done = sum(1 for t in phase.tasks if t.status.value == "done")
        total_tasks = len(phase.tasks)

        body_lines = [
            f"## Phase {phase_num}: {phase.title}",
            "",
            f"**Tasks:** {tasks_done}/{total_tasks} complete",
            "",
            "### Completed Tasks",
        ]
        for t in phase.tasks:
            if t.status.value == "done":
                body_lines.append(f"- {t.title}")

        body = "\n".join(body_lines)

        endpoint = f"/repos/{config.owner}/{config.repo}/pulls"
        data: dict[str, Any] = {
            "title": title,
            "head": branch,
            "base": config.pr_base_branch,
            "body": body,
        }

        result = _github_request("POST", endpoint, token, data)
        if not result or "number" not in result:
            return None

        # Link milestone if provided
        if milestone_number is not None:
            pr_number = result["number"]
            issue_endpoint = f"/repos/{config.owner}/{config.repo}/issues/{pr_number}"
            _github_request("PATCH", issue_endpoint, token, {"milestone": milestone_number})

        return result
    except Exception:
        return None


def post_build_summary(
    config: GitHubConfig,
    token: str,
    pr_number: int,
    phase: Any,
    health_summary: str,
    cost_summary: str,
) -> bool:
    """
    Post a build summary comment to a PR.

    Returns True on success, False on error.
    """
    if not config.enabled or not config.post_build_summary or not token:
        return False
    try:
        tasks_done = sum(1 for t in phase.tasks if t.status.value == "done")
        total_tasks = len(phase.tasks)

        comment = (
            f"## Forge Build Summary — {phase.title}\n\n"
            f"**Tasks:** {tasks_done}/{total_tasks} complete\n"
            f"**Cost:** {cost_summary}\n"
            f"**Health:** {health_summary}\n"
        )

        endpoint = f"/repos/{config.owner}/{config.repo}/issues/{pr_number}/comments"
        data = {"body": comment}
        result = _github_request("POST", endpoint, token, data)
        return result is not None
    except Exception:
        return False


def link_issues_to_tasks(
    issues: list[dict],
    tasks: list,
) -> dict[str, list[int]]:
    """
    Match GitHub issues to tasks by title similarity.

    Simple heuristic: if key words from an issue title appear in
    a task title or description, link them.

    Returns {task_id: [issue_numbers]}
    """
    result: dict[str, list[int]] = {}
    if not issues or not tasks:
        return result

    for task in tasks:
        task_text = f"{task.title} {task.description}".lower()
        for issue in issues:
            issue_title = issue.get("title", "")
            # Extract meaningful words (3+ chars, not stopwords)
            words = re.findall(r"[a-z]{3,}", issue_title.lower())
            stopwords = {"the", "and", "for", "that", "this", "with", "from", "are", "was", "not", "can"}
            keywords = [w for w in words if w not in stopwords]

            if not keywords:
                continue

            # Match if at least half the keywords appear in the task text
            matches = sum(1 for kw in keywords if kw in task_text)
            threshold = max(1, len(keywords) // 2)

            if matches >= threshold:
                if task.id not in result:
                    result[task.id] = []
                issue_num = issue.get("number", 0)
                if issue_num and issue_num not in result[task.id]:
                    result[task.id].append(issue_num)

    return result


def format_issue_context(issues: list[dict]) -> str:
    """
    Format open GitHub issues as context for task generation.

    Returns a markdown string listing issues:
    ## Open GitHub Issues (context for task generation)
    - #12: Users cannot reset password [bug]
    - #15: Add email verification [feature]
    """
    if not issues:
        return ""

    lines = ["## Open GitHub Issues (context for task generation)"]
    for issue in issues:
        number = issue.get("number", 0)
        title = issue.get("title", "")
        labels = issue.get("labels", [])
        label_str = ""
        if labels:
            label_names = [lbl.get("name", "") for lbl in labels if lbl.get("name")]
            if label_names:
                label_str = " [" + ", ".join(label_names) + "]"
        lines.append(f"- #{number}: {title}{label_str}")

    return "\n".join(lines)
