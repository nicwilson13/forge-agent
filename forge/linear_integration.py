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


# ---------------------------------------------------------------------------
# Project planning — proactive Linear sync
# ---------------------------------------------------------------------------

LINEAR_PRIORITY = {
    "urgent": 1,   # Linear: Urgent
    "high":   2,   # Linear: High
    "medium": 3,   # Linear: Medium
    "low":    4,   # Linear: Low
    "none":   0,   # Linear: No priority
}

HIGH_PRIORITY_SIGNALS = [
    "auth", "security", "payment", "stripe", "database schema",
    "data model", "migration", "core", "foundation", "architecture",
]

URGENT_SIGNALS = [
    "critical", "breaking", "production", "hotfix", "breach",
]


def infer_task_priority(task_title: str, task_description: str) -> int:
    """
    Infer Linear priority (1-4) from task title and description.

    Checks URGENT_SIGNALS → 1 (Urgent)
    Checks HIGH_PRIORITY_SIGNALS → 2 (High)
    Default → 3 (Medium)
    """
    text = f"{task_title} {task_description}".lower()
    for signal in URGENT_SIGNALS:
        if signal in text:
            return 1
    for signal in HIGH_PRIORITY_SIGNALS:
        if signal in text:
            return 2
    return 3


CREATE_ISSUE_WITH_PRIORITY_MUTATION = """
mutation IssueCreate($teamId: String!, $title: String!, $description: String!, $projectId: String, $priority: Int) {
  issueCreate(input: {
    teamId: $teamId
    title: $title
    description: $description
    projectId: $projectId
    priority: $priority
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

CREATE_MILESTONE_MUTATION = """
mutation ProjectMilestoneCreate($projectId: String!, $name: String!, $description: String) {
  projectMilestoneCreate(input: {
    projectId: $projectId
    name: $name
    description: $description
  }) {
    success
    projectMilestone {
      id
      name
    }
  }
}
"""

CREATE_CYCLE_MUTATION = """
mutation CycleCreate($teamId: String!, $name: String!, $description: String, $startsAt: DateTime!, $endsAt: DateTime!) {
  cycleCreate(input: {
    teamId: $teamId
    name: $name
    description: $description
    startsAt: $startsAt
    endsAt: $endsAt
  }) {
    success
    cycle {
      id
      name
      url
    }
  }
}
"""


def create_milestone_for_phase(
    config: LinearConfig,
    token: str,
    phase_title: str,
    phase_num: int,
    description: str = "",
) -> dict | None:
    """
    Create a Linear milestone for a Forge phase.

    Tries projectMilestoneCreate first (requires project_id),
    falls back to cycleCreate.

    Returns {id, name, url} or None on error.
    """
    if not config.enabled or not token:
        return None
    try:
        name = f"Phase {phase_num}: {phase_title}"

        # Try project milestone if project_id is set
        if config.project_id:
            data = _linear_query(
                CREATE_MILESTONE_MUTATION,
                {
                    "projectId": config.project_id,
                    "name": name,
                    "description": description,
                },
                token,
            )
            if data and "projectMilestoneCreate" in data:
                result = data["projectMilestoneCreate"]
                if result.get("success"):
                    milestone = result.get("projectMilestone", {})
                    return {
                        "id": milestone.get("id", ""),
                        "name": milestone.get("name", ""),
                        "url": "",
                    }

        # Fallback: create a cycle
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        start = now + timedelta(weeks=phase_num - 1)
        end = start + timedelta(weeks=1)

        data = _linear_query(
            CREATE_CYCLE_MUTATION,
            {
                "teamId": config.team_id,
                "name": name,
                "description": description,
                "startsAt": start.isoformat() + "Z",
                "endsAt": end.isoformat() + "Z",
            },
            token,
        )
        if data and "cycleCreate" in data:
            result = data["cycleCreate"]
            if result.get("success"):
                cycle = result.get("cycle", {})
                return {
                    "id": cycle.get("id", ""),
                    "name": cycle.get("name", ""),
                    "url": cycle.get("url", ""),
                }
        return None
    except Exception:
        return None


def create_issue_for_task(
    config: LinearConfig,
    token: str,
    task_title: str,
    task_description: str,
    milestone_id: str | None = None,
    priority: int = 3,
) -> dict | None:
    """
    Create a Linear issue for a Forge task with priority.

    Returns {id, identifier, url} or None on error.
    """
    if not config.enabled or not token or not config.team_id:
        return None
    try:
        variables: dict = {
            "teamId": config.team_id,
            "title": task_title,
            "description": task_description,
            "priority": priority,
        }
        if config.project_id:
            variables["projectId"] = config.project_id
        else:
            variables["projectId"] = None

        data = _linear_query(CREATE_ISSUE_WITH_PRIORITY_MUTATION, variables, token)
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


def bulk_create_phase_issues(
    config: LinearConfig,
    token: str,
    phase_title: str,
    phase_num: int,
    tasks: list,
    milestone_id: str | None = None,
) -> list[dict]:
    """
    Create Linear issues for all tasks in a phase.

    Never raises - skips failed creates and continues.
    """
    created = []
    try:
        for task in tasks:
            title = task.title if hasattr(task, "title") else str(task)
            description = task.description if hasattr(task, "description") else ""
            priority = infer_task_priority(title, description)
            result = create_issue_for_task(
                config, token, title, description,
                milestone_id=milestone_id, priority=priority,
            )
            if result:
                created.append(result)
        print(f"  [OK] {len(created)} issues created for {phase_title}")
    except Exception:
        print(f"  [OK] {len(created)} issues created for {phase_title}")
    return created


def sync_plan_to_linear(
    config: LinearConfig,
    token: str,
    phases: list,
) -> dict:
    """
    Write the full Forge build plan to Linear.

    For each phase:
    1. create_milestone_for_phase()
    2. bulk_create_phase_issues() with the milestone ID

    Returns summary dict with milestones_created, issues_created, errors.
    Never raises.
    """
    summary: dict = {
        "milestones_created": 0,
        "issues_created": 0,
        "errors": [],
    }
    try:
        for i, phase in enumerate(phases, 1):
            title = phase.title if hasattr(phase, "title") else str(phase)
            description = phase.description if hasattr(phase, "description") else ""
            tasks = phase.tasks if hasattr(phase, "tasks") else []

            milestone = create_milestone_for_phase(
                config, token, title, i, description,
            )
            milestone_id = milestone["id"] if milestone else None
            if milestone:
                summary["milestones_created"] += 1
                print(f"  [OK] Milestone: \"{milestone['name']}\"")
            else:
                summary["errors"].append(f"Failed to create milestone for phase {i}")

            created = bulk_create_phase_issues(
                config, token, title, i, tasks,
                milestone_id=milestone_id,
            )
            summary["issues_created"] += len(created)
    except Exception as exc:
        summary["errors"].append(str(exc))
    return summary
