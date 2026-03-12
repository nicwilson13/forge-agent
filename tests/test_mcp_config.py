"""Tests for forge/mcp_config.py."""

import json

from forge.mcp_config import (
    MCPServer,
    MCPConfig,
    load_mcp_config,
    save_mcp_config,
    validate_mcp_server,
    log_mcp_status,
    KNOWN_MCP_STARTERS,
)


def test_load_mcp_config_missing_file(tmp_path):
    """Returns empty config when .forge/mcp.json doesn't exist."""
    config = load_mcp_config(tmp_path)
    assert config.is_empty
    assert config.servers == []


def test_load_mcp_config_valid(tmp_path):
    """Parses valid mcp.json correctly."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    mcp_path = forge_dir / "mcp.json"
    mcp_path.write_text(json.dumps({
        "servers": [
            {
                "name": "github",
                "url": "https://api.github.com/mcp",
                "description": "GitHub issues",
                "use_for": ["task_generation"]
            }
        ]
    }))
    config = load_mcp_config(tmp_path)
    assert len(config.servers) == 1
    assert config.servers[0].name == "github"
    assert config.servers[0].url == "https://api.github.com/mcp"
    assert config.servers[0].use_for == ["task_generation"]


def test_load_mcp_config_invalid_json(tmp_path):
    """Returns empty config on JSON parse error."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    mcp_path = forge_dir / "mcp.json"
    mcp_path.write_text("not valid json {{{")
    config = load_mcp_config(tmp_path)
    assert config.is_empty


def test_mcp_config_for_operation_empty_use_for():
    """Server with empty use_for included for all operations."""
    config = MCPConfig(servers=[
        MCPServer(name="all", url="https://all.com/mcp", use_for=[])
    ])
    result = config.for_operation("task_generation")
    assert len(result) == 1
    assert result[0].name == "all"

    result2 = config.for_operation("architecture")
    assert len(result2) == 1


def test_mcp_config_for_operation_specific():
    """Server with use_for only returned for matching operation."""
    config = MCPConfig(servers=[
        MCPServer(name="github", url="https://github.com/mcp",
                  use_for=["task_generation", "qa_evaluation"])
    ])
    result = config.for_operation("task_generation")
    assert len(result) == 1
    assert result[0].name == "github"


def test_mcp_config_for_operation_not_matching():
    """Server not returned when operation not in use_for."""
    config = MCPConfig(servers=[
        MCPServer(name="github", url="https://github.com/mcp",
                  use_for=["task_generation"])
    ])
    result = config.for_operation("architecture")
    assert len(result) == 0


def test_to_api_format_structure():
    """Returns list of dicts with type, url, name keys."""
    config = MCPConfig(servers=[
        MCPServer(name="github", url="https://github.com/mcp", use_for=[])
    ])
    result = config.to_api_format("task_generation")
    assert len(result) == 1
    assert result[0] == {
        "type": "url",
        "url": "https://github.com/mcp",
        "name": "github",
    }


def test_to_api_format_empty_when_no_servers():
    """Returns empty list when no servers match operation."""
    config = MCPConfig(servers=[
        MCPServer(name="github", url="https://github.com/mcp",
                  use_for=["task_generation"])
    ])
    result = config.to_api_format("architecture")
    assert result == []


def test_validate_mcp_server_valid():
    """Valid server returns empty error list."""
    server = MCPServer(
        name="github",
        url="https://api.github.com/mcp",
        use_for=["task_generation"]
    )
    errors = validate_mcp_server(server)
    assert errors == []


def test_validate_mcp_server_empty_name():
    """Empty name returns error."""
    server = MCPServer(name="", url="https://example.com")
    errors = validate_mcp_server(server)
    assert any("name" in e for e in errors)


def test_validate_mcp_server_invalid_url():
    """Non-http URL returns error (unless mcp://)."""
    server = MCPServer(name="test", url="ftp://example.com")
    errors = validate_mcp_server(server)
    assert any("url" in e for e in errors)

    # mcp:// should be valid
    server2 = MCPServer(name="test", url="mcp://filesystem")
    errors2 = validate_mcp_server(server2)
    assert not any("url" in e for e in errors2)


def test_validate_mcp_server_invalid_use_for():
    """Unknown use_for value returns error."""
    server = MCPServer(name="test", url="https://example.com",
                       use_for=["bogus_operation"])
    errors = validate_mcp_server(server)
    assert any("use_for" in e for e in errors)


def test_save_and_load_roundtrip(tmp_path):
    """Config survives save → load round-trip."""
    config = MCPConfig(servers=[
        MCPServer(name="github", url="https://api.github.com/mcp",
                  description="GitHub issues", use_for=["task_generation"]),
        MCPServer(name="supabase", url="https://mcp.supabase.com/sse",
                  use_for=[]),
    ])
    save_mcp_config(tmp_path, config)
    loaded = load_mcp_config(tmp_path)
    assert len(loaded.servers) == 2
    assert loaded.servers[0].name == "github"
    assert loaded.servers[0].use_for == ["task_generation"]
    assert loaded.servers[1].name == "supabase"
    assert loaded.servers[1].use_for == []


def test_mcp_config_is_empty():
    """is_empty True for config with no servers."""
    assert MCPConfig().is_empty
    assert not MCPConfig(servers=[
        MCPServer(name="x", url="https://x.com")
    ]).is_empty
