"""Tests for forge.figma_integration module."""

import json
from pathlib import Path

from forge.figma_integration import (
    FigmaConfig,
    load_figma_config,
    save_figma_config,
    get_figma_token,
    _figma_request,
    get_file_components,
    get_design_variables,
    format_components_context,
    generate_token_file,
    run_figma_integration,
)


def test_load_figma_config_missing(tmp_path):
    """Returns disabled config when .forge/figma.json missing."""
    config = load_figma_config(tmp_path)
    assert config.enabled is False
    assert config.file_key == ""


def test_load_figma_config_valid(tmp_path):
    """Parses valid config correctly."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    config_data = {
        "enabled": True,
        "file_key": "abc123XYZ",
        "generate_tokens": True,
        "export_frames": False,
        "frame_ids": ["1:2", "3:4"],
    }
    (forge_dir / "figma.json").write_text(json.dumps(config_data))

    config = load_figma_config(tmp_path)
    assert config.enabled is True
    assert config.file_key == "abc123XYZ"
    assert config.generate_tokens is True
    assert config.export_frames is False
    assert config.frame_ids == ["1:2", "3:4"]


def test_load_figma_config_invalid_json(tmp_path):
    """Returns disabled config on parse error."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    (forge_dir / "figma.json").write_text("not valid json {{{")

    config = load_figma_config(tmp_path)
    assert config.enabled is False


def test_get_figma_token_missing(monkeypatch):
    """Returns empty string when token not in profile."""
    monkeypatch.setattr(Path, "home", lambda: Path("/nonexistent/path"))
    result = get_figma_token()
    assert result == ""


def test_figma_request_returns_none_on_error(monkeypatch):
    """_figma_request returns None on network error."""
    import urllib.request

    def mock_urlopen(*args, **kwargs):
        raise ConnectionError("no network")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    result = _figma_request("/files/abc123", "fake-token")
    assert result is None


def test_get_file_components_disabled():
    """Returns empty list when integration disabled."""
    config = FigmaConfig(enabled=False)
    result = get_file_components(config, "some-token")
    assert result == []


def test_get_file_components_no_token():
    """Returns empty list when token empty."""
    config = FigmaConfig(enabled=True, file_key="abc123")
    result = get_file_components(config, "")
    assert result == []


def test_get_design_variables_disabled():
    """Returns empty dict when disabled."""
    config = FigmaConfig(enabled=False)
    result = get_design_variables(config, "some-token")
    assert result == {}


def test_get_design_variables_no_token():
    """Returns empty dict when token empty."""
    config = FigmaConfig(enabled=True, file_key="abc123")
    result = get_design_variables(config, "")
    assert result == {}


def test_format_components_context_empty():
    """Returns empty string for empty component list."""
    result = format_components_context([])
    assert result == ""


def test_format_components_context_with_components():
    """Returns markdown with component names."""
    components = [
        {"id": "1", "name": "Button", "description": "Primary button"},
        {"id": "2", "name": "Card", "description": ""},
        {"id": "3", "name": "Modal", "description": "Dialog overlay"},
    ]
    result = format_components_context(components)
    assert "Figma Components" in result
    assert "Button: Primary button" in result
    assert "- Card" in result
    assert "Modal: Dialog overlay" in result


def test_generate_token_file_empty_variables(tmp_path):
    """Returns None for empty variables dict."""
    result = generate_token_file({}, tmp_path)
    assert result is None


def test_generate_token_file_with_colors(tmp_path):
    """Writes valid TypeScript file with color tokens."""
    variables = {
        "colors": {"primary": "#6366f1", "background": "#ffffff"},
        "spacing": {"sm": 8, "md": 16, "lg": 24},
    }
    result = generate_token_file(variables, tmp_path)
    assert result is not None
    assert result.exists()
    content = result.read_text(encoding="utf-8")
    assert "primary" in content
    assert "#6366f1" in content
    assert "as const" in content
    assert "export const colors" in content
    assert "export const spacing" in content
    assert "sm: 8" in content


def test_generate_token_file_creates_directory(tmp_path):
    """Creates src/lib/ directory if it doesn't exist."""
    variables = {"colors": {"primary": "#000000"}}
    result = generate_token_file(variables, tmp_path)
    assert result is not None
    assert (tmp_path / "src" / "lib").is_dir()


def test_run_figma_integration_disabled(tmp_path):
    """Returns ('', []) when not configured."""
    ctx, components = run_figma_integration(tmp_path)
    assert ctx == ""
    assert components == []


def test_run_figma_integration_never_raises(monkeypatch, tmp_path):
    """run_figma_integration never raises even with broken state."""
    # Write enabled config
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    config_data = {
        "enabled": True,
        "file_key": "abc123",
        "generate_tokens": True,
    }
    (forge_dir / "figma.json").write_text(json.dumps(config_data))

    # Mock token to return a value
    monkeypatch.setattr(
        "forge.figma_integration.get_figma_token", lambda: "fake-token"
    )

    # Mock get_file_components to raise
    def mock_components(*args, **kwargs):
        raise RuntimeError("unexpected error")

    monkeypatch.setattr(
        "forge.figma_integration.get_file_components", mock_components
    )

    # Should not raise
    ctx, components = run_figma_integration(tmp_path)
    assert ctx == ""
    assert components == []


def test_save_load_roundtrip(tmp_path):
    """Config round-trips through save and load."""
    config = FigmaConfig(
        enabled=True,
        file_key="abc123XYZ",
        generate_tokens=True,
        export_frames=True,
        frame_ids=["1:2"],
    )
    save_figma_config(tmp_path, config)
    loaded = load_figma_config(tmp_path)
    assert loaded.enabled is True
    assert loaded.file_key == "abc123XYZ"
    assert loaded.generate_tokens is True
    assert loaded.export_frames is True
    assert loaded.frame_ids == ["1:2"]
