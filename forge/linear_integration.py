"""
Linear API integration for Forge.

Reads open issues to inform task generation, updates issue status
on task completion, and creates issues for parked tasks.

Configuration: .forge/linear.json (project-level)
Token: ~/.forge/profile.yaml linear_token field (user-level)

Uses Linear GraphQL API: https://api.linear.app/graphql
All operations non-fatal. Build continues on any Linear API failure.

This module imports only stdlib. No forge imports.
"""

import json
import urllib.request
import urllib.error
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class LinearConfig:
    enabled: bool = False
    team_id: str = ""
    project_id: str = ""
    sync_issues: bool = True
    create_issues_for_parked: bool = True
    update_issue_status: bool = True


def load_linear_config(project_dir: Path) -> LinearConfig:
    """Load from .forge/linear.json. Never raises."""
    try:
        path = project_dir / ".forge" / "linear.json"
        if not path.exists():
            return LinearConfig()
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return LinearConfig()
        return LinearConfig(
            enabled=data.get("enabled", False),
            team_id=data.get("team_id", ""),
            project_id=data.get("project_id", ""),
            sync_issues=data.get("sync_issues", True),
            create_issues_for_parked=data.get("create_issues_for_parked", True),
            update_issue_status=data.get("update_issue_status", True),
        )
    except Exception:
        return LinearConfig()


def save_linear_config(project_dir: Path, config: LinearConfig) -> None:
    """Save to .forge/linear.json. Never raises."""
    try:
        forge_dir = project_dir / ".forge"
        forge_dir.mkdir(parents=True, exist_ok=True)
        path = forge_dir / "linear.json"
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(config), f, indent=2)
        tmp.replace(path)
    except Exception:
        pass


def get_linear_token() -> str:
    """
    Read linear_token from ~/.forge/profile.yaml.
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
            return data.get("linear_token", "") or ""
        return ""
    except Exception:
        return ""


def _linear_query(query: str, variables: dict, token: str) -> dict | None:
    """
    Execute a Linear GraphQL query.

    POST https://api.linear.app/graphql
    Headers: Authorization: {token} (no 'Bearer' prefix for Linear)
             Content-Type: application/json
    Body: {"query": query, "variables": variables}
    Timeout: 15s. Never raises. Returns data dict or None.
    """
    try:
        url = "https://api.linear.app/graphql"
        headers = {
            "Authorization": token,
            "Content-Type": "application/json",
            "User-Agent": "Forge-Agent",
        }
        body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if "data" in result:
                return result["data"]
            return None
    except Exception:
        return None


GET_ISSUES_QUERY = """
query GetTeamIssues($teamId: String!, $limit: Int!) {
  team(id: $teamId) {
    issues(
      filter: { state: { type: { nin: ["completed", "canceled"] } } }
      first: $limit
      orderBy: priority
    ) {
      nodes {
        id
        identifier
        title
        description
        priority
        labels {
          nodes {
            name
          }
        }
      }
    }
  }
}
"""


def get_open_issues(config: LinearConfig, token: str,
                    limit: int = 25) -> list[dict]:
    """
    Fetch open issues from the Linear team.

    Returns list of dicts: {id, identifier, title, description, priority, labels}
    Returns empty list on error or if disabled.
    """
    if not config.enabled or not token or not config.team_id:
        return []
    if not config.sync_issues:
        return []
    try:
        data = _linear_query(
            GET_ISSUES_QUERY,
            {"teamId": config.team_id, "limit": limit},
            token,
        )
        if not data or "team" not in data:
            return []
        team = data["team"]
        if not team or "issues" not in team:
            return []
        nodes = team["issues"].get("nodes", [])
        issues = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            label_nodes = node.get("labels", {}).get("nodes", [])
            labels = [ln.get("name", "") for ln in label_nodes if isinstance(ln, dict)]
            issues.append({
                "id": node.get("id", ""),
                "identifier": node.get("identifier", ""),
                "title": node.get("title", ""),
                "description": (node.get("description") or "")[:500],
                "priority": node.get("priority", 0),
                "labels": labels,
            })
        return issues
    except Exception:
        return []


GET_STATES_QUERY = """
query GetWorkflowStates($teamId: String!) {
  team(id: $teamId) {
    states {
      nodes {
        id
        name
      }
    }
  }
}
"""


def get_issue_states(config: LinearConfig, token: str) -> dict[str, str]:
    """
    Fetch workflow states for the team.

    Returns {state_name_lower: state_id} mapping.
    e.g. {"todo": "id1", "in progress": "id2", "done": "id3"}
    Returns empty dict on error.
    """
    if not config.enabled or not token or not config.team_id:
        return {}
    try:
        data = _linear_query(
            GET_STATES_QUERY,
            {"teamId": config.team_id},
            token,
        )
        if not data or "team" not in data:
            return {}
        team = data["team"]
        if not team or "states" not in team:
            return {}
        nodes = team["states"].get("nodes", [])
        states = {}
        for node in nodes:
            if isinstance(node, dict):
                name = node.get("name", "")
                state_id = node.get("id", "")
                if name and state_id:
                    states[name.lower()] = state_id
        return states
    except Exception:
        return {}


UPDATE_ISSUE_MUTATION = """
mutation IssueUpdate($id: String!, $stateId: String!) {
  issueUpdate(id: $id, input: { stateId: $stateId }) {
    success
  }
}
"""


def update_issue_status(config: LinearConfig, token: str,
                        issue_id: str, state_name: str) -> bool:
    """
    Update a Linear issue's workflow state.

    Looks up state_id from get_issue_states(), then updates.
    Returns True on success, False on error.
    """
    if not config.enabled or not token or not config.update_issue_status:
        return False
    try:
        states = get_issue_states(config, token)
        state_id = states.get(state_name.lower())
        if not state_id:
            return False
        data = _linear_query(
            UPDATE_ISSUE_MUTATION,
            {"id": issue_id, "stateId": state_id},
            token,
        )
        if data and "issueUpdate" in data:
            return data["issueUpdate"].get("success", False)
        return False
    except Exception:
        return False


CREATE_ISSUE_MUTATION = """
mutation IssueCreate($teamId: String!, $title: String!, $description: String!, $projectId: String) {
  issueCreate(input: {
    teamId: $teamId
    title: $title
    description: $description
    projectId: $projectId
  }) {
    success
    issue {
      id
      identifier
      url
    }
  }
}
"""


def create_issue(config: LinearConfig, token: str,
                 title: str, description: str,
                 label: str = "") -> dict | None:
    """
    Create a new Linear issue in the team/project.

    Returns created issue dict (id, identifier, url) or None on error.
    """
    if not config.enabled or not token or not config.team_id:
        return None
    try:
        variables: dict = {
            "teamId": config.team_id,
            "title": title,
            "description": description,
        }
        if config.project_id:
            variables["projectId"] = config.project_id
        else:
            variables["projectId"] = None

        data = _linear_query(CREATE_ISSUE_MUTATION, variables, token)
        if not data or "issueCreate" not in data:
            return None
        create_result = data["issueCreate"]
        if not create_result.get("success"):
            return None
        issue = create_result.get("issue")
        if not isinstance(issue, dict):
            return None
        return {
            "id": issue.get("id", ""),
            "identifier": issue.get("identifier", ""),
            "url": issue.get("url", ""),
        }
    except Exception:
        return None


def format_issues_context(issues: list[dict]) -> str:
    """
    Format Linear issues as context for task generation.

    ## Linear Issues (open work items)
    - LIN-12 [bug, high]: Users cannot reset password
    - LIN-15 [feature]: Add email verification flow
    """
    if not issues:
        return ""

    PRIORITY_NAMES = {1: "urgent", 2: "high", 3: "medium", 4: "low"}

    lines = ["## Linear Issues (open work items)"]
    for issue in issues:
        identifier = issue.get("identifier", "")
        title = issue.get("title", "")
        labels = issue.get("labels", [])
        priority = issue.get("priority", 0)

        tags = list(labels)
        if priority in PRIORITY_NAMES:
            tags.append(PRIORITY_NAMES[priority])

        tag_str = f" [{', '.join(tags)}]" if tags else ""
        lines.append(f"- {identifier}{tag_str}: {title}")

    return "\n".join(lines)


def match_issue_to_task(issues: list[dict],
                        task_title: str,
                        task_description: str) -> dict | None:
    """
    Find a Linear issue that matches a task by keyword similarity.

    Returns the best-matching issue dict, or None if no match found.
    Simple heuristic: count overlapping words between issue title
    and task title/description. Match if overlap >= 3 words.
    """
    if not issues:
        return None

    task_text = f"{task_title} {task_description}".lower()
    stopwords = {
        "the", "and", "for", "that", "this", "with", "from", "are",
        "was", "not", "can", "has", "have", "will", "but", "its",
    }

    best_match = None
    best_score = 0

    for issue in issues:
        issue_title = issue.get("title", "")
        # Extract meaningful words (3+ chars)
        import re
        words = re.findall(r"[a-z]{3,}", issue_title.lower())
        keywords = [w for w in words if w not in stopwords]

        if not keywords:
            continue

        matches = sum(1 for kw in keywords if kw in task_text)
        if matches >= 3 and matches > best_score:
            best_score = matches
            best_match = issue

    return best_match


def run_linear_integration(project_dir: Path) -> tuple[str, list[dict]]:
    """
    Run Linear integration at build start.

    Loads config, fetches open issues, formats context.
    Returns (issues_context, issues_list).
    Returns ("", []) on error or if disabled.
    Never raises. Prints status to stdout.
    """
    try:
        config = load_linear_config(project_dir)
        if not config.enabled:
            return ("", [])

        token = get_linear_token()
        if not token:
            print("  (Linear integration enabled but linear_token not set)")
            return ("", [])

        if not config.team_id:
            print("  (Linear integration enabled but team_id not set)")
            return ("", [])

        print(f"  [linear] Fetching open issues from team...")
        issues = get_open_issues(config, token)
        if issues:
            # Count by labels for summary
            bug_count = sum(
                1 for i in issues
                if any("bug" in l.lower() for l in i.get("labels", []))
            )
            feature_count = sum(
                1 for i in issues
                if any("feature" in l.lower() for l in i.get("labels", []))
            )
            parts = []
            if bug_count:
                parts.append(f"{bug_count} bug{'s' if bug_count != 1 else ''}")
            if feature_count:
                parts.append(f"{feature_count} feature{'s' if feature_count != 1 else ''}")
            label_summary = f" ({', '.join(parts)})" if parts else ""
            print(f"  [linear] {len(issues)} open issue(s){label_summary} loaded as task context")
        else:
            print("  [linear] No open issues found")

        context = format_issues_context(issues)
        return (context, issues)
    except Exception:
        return ("", [])
