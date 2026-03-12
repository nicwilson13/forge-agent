"""
MCP (Model Context Protocol) configuration for Forge.

Reads .forge/mcp.json and provides server configs to orchestrator
calls. MCP servers give Claude access to live project data: GitHub
issues, database schemas, Linear tickets, and more.

MCP connection failures are always non-fatal. The build continues
with standard context when MCP is unavailable.

Zero forge module imports - self-contained config layer.
"""

import json
import os
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path


VALID_OPERATIONS = frozenset({
    "task_generation",
    "qa_evaluation",
    "architecture",
    "phase_evaluation",
})


KNOWN_MCP_STARTERS = {
    "github": {
        "name": "github",
        "url": "https://api.github.com/mcp",
        "description": "GitHub issues, PRs, milestones",
        "use_for": ["task_generation", "qa_evaluation"],
    },
    "supabase": {
        "name": "supabase",
        "url": "https://mcp.supabase.com/sse",
        "description": "Database schema and table inspection",
        "use_for": ["task_generation", "architecture"],
    },
    "linear": {
        "name": "linear",
        "url": "https://mcp.linear.app/sse",
        "description": "Linear tickets and project requirements",
        "use_for": ["task_generation"],
    },
    "filesystem": {
        "name": "filesystem",
        "url": "mcp://filesystem",
        "description": "Read project source files",
        "use_for": ["task_generation", "qa_evaluation", "architecture"],
    },
}


@dataclass
class MCPServer:
    name: str
    url: str
    description: str = ""
    use_for: list[str] = field(default_factory=list)


@dataclass
class MCPConfig:
    servers: list[MCPServer] = field(default_factory=list)

    def for_operation(self, operation: str) -> list[MCPServer]:
        """
        Return servers relevant to a given operation name.

        operation: "task_generation" | "qa_evaluation" |
                   "architecture" | "phase_evaluation"

        Returns servers where use_for is empty (all operations)
        or operation is in use_for.
        """
        result = []
        for server in self.servers:
            if not server.use_for or operation in server.use_for:
                result.append(server)
        return result

    def to_api_format(self, operation: str) -> list[dict]:
        """
        Return servers in Anthropic API mcp_servers format.

        [{"type": "url", "url": "...", "name": "..."}]

        Only includes servers relevant to the given operation.
        """
        servers = self.for_operation(operation)
        return [
            {"type": "url", "url": s.url, "name": s.name}
            for s in servers
        ]

    @property
    def is_empty(self) -> bool:
        return len(self.servers) == 0


def load_mcp_config(project_dir: Path) -> MCPConfig:
    """
    Load MCP config from .forge/mcp.json.

    Returns empty MCPConfig if file doesn't exist or is invalid.
    Never raises.
    """
    mcp_path = project_dir / ".forge" / "mcp.json"
    try:
        if not mcp_path.exists():
            return MCPConfig()
        with open(mcp_path, encoding="utf-8") as f:
            raw = json.load(f)
        servers = []
        for s in raw.get("servers", []):
            servers.append(MCPServer(
                name=s.get("name", ""),
                url=s.get("url", ""),
                description=s.get("description", ""),
                use_for=s.get("use_for", []),
            ))
        return MCPConfig(servers=servers)
    except Exception:
        return MCPConfig()


def save_mcp_config(project_dir: Path, config: MCPConfig) -> None:
    """
    Save MCP config to .forge/mcp.json.

    Creates .forge/ directory if needed.
    Never raises.
    """
    try:
        forge_dir = project_dir / ".forge"
        forge_dir.mkdir(parents=True, exist_ok=True)
        mcp_path = forge_dir / "mcp.json"
        data = {
            "servers": [asdict(s) for s in config.servers]
        }
        with open(mcp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def log_mcp_status(config: MCPConfig) -> None:
    """
    Print MCP status line at build start.

    Active: "  MCP: github (issues) · supabase (schema) · N servers active"
    Empty:  "  (no MCP servers configured - add .forge/mcp.json to enable)"
    """
    if not (sys.stdout.isatty() or os.environ.get("FORGE_VERBOSE")):
        return

    if config.is_empty:
        print("  (no MCP servers configured - add .forge/mcp.json to enable)")
        return

    parts = []
    for s in config.servers:
        desc = s.description.split(",")[0].strip() if s.description else s.name
        parts.append(f"{s.name} ({desc})")
    summary = " · ".join(parts)
    print(f"  MCP: {summary} · {len(config.servers)} server(s) active")


def validate_mcp_server(server: MCPServer) -> list[str]:
    """
    Validate an MCPServer config. Returns list of error strings.

    Checks:
    - name is non-empty string
    - url starts with http:// or https:// (or mcp:// for local)
    - use_for values are valid operation names
    """
    errors = []
    if not server.name or not server.name.strip():
        errors.append("server name is empty")
    if not server.url or not server.url.strip():
        errors.append(f"server '{server.name}': url is empty")
    elif not (server.url.startswith("http://") or
              server.url.startswith("https://") or
              server.url.startswith("mcp://")):
        errors.append(
            f"server '{server.name}': url must start with "
            f"http://, https://, or mcp:// (got '{server.url[:30]}')"
        )
    for op in server.use_for:
        if op not in VALID_OPERATIONS:
            errors.append(
                f"server '{server.name}': unknown use_for value '{op}' "
                f"(valid: {', '.join(sorted(VALID_OPERATIONS))})"
            )
    return errors
