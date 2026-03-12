"""Tests for forge.integrations_view."""

import json
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from forge.integrations_view import (
    INTEGRATIONS_HTML,
    get_all_integration_statuses,
    save_integration_config,
    check_integration_connection,
    handle_integrations_data,
)


def test_integrations_html_has_all_six():
    """INTEGRATIONS_HTML mentions all 6 integration names."""
    # The HTML loads integration data dynamically via JS, but the page title
    # and key UI elements should be present in the static HTML
    assert "Integrations" in INTEGRATIONS_HTML
    assert "/integrations/data" in INTEGRATIONS_HTML
    assert "Test Connection" in INTEGRATIONS_HTML
    assert "saveIntegration" in INTEGRATIONS_HTML
    assert "testConnection" in INTEGRATIONS_HTML
    # Integration names appear in the JS order array
    for name in ["github", "vercel", "linear", "sentry", "figma", "ollama"]:
        assert name in INTEGRATIONS_HTML
    assert "Test Connection" in INTEGRATIONS_HTML
    assert "Save" in INTEGRATIONS_HTML


def test_get_all_integration_statuses_all_missing(tmp_path):
    """Returns all disabled when no configs present."""
    with patch("forge.integrations_view._get_token", return_value=""):
        result = get_all_integration_statuses(tmp_path)

    assert len(result) == 6
    for name, status in result.items():
        assert status["enabled"] is False
        assert status["has_token"] is False


def test_get_all_integration_statuses_with_config(tmp_path):
    """Enabled integration shows as enabled."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir(parents=True)
    (forge_dir / "github.json").write_text(json.dumps({
        "enabled": True,
        "owner": "testuser",
        "repo": "testrepo",
    }))

    with patch("forge.integrations_view._get_token", return_value=""):
        result = get_all_integration_statuses(tmp_path)

    assert result["github"]["enabled"] is True
    assert result["github"]["config"]["owner"] == "testuser"


def test_get_all_integration_statuses_redacts_tokens(tmp_path):
    """No raw tokens appear in returned config."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir(parents=True)
    (forge_dir / "github.json").write_text(json.dumps({
        "enabled": True,
        "github_token": "ghp_secret123",
    }))

    with patch("forge.integrations_view._get_token", return_value="ghp_secret123"):
        result = get_all_integration_statuses(tmp_path)

    config_str = json.dumps(result)
    assert "ghp_secret123" not in config_str


def test_save_integration_config_writes_json(tmp_path):
    config_data = {"enabled": True, "owner": "myorg", "repo": "myrepo"}

    with patch("forge.integrations_view.save_integration_config.__module__", "forge.integrations_view"):
        result = save_integration_config(tmp_path, "github", config_data)

    assert result is True
    written = json.loads((tmp_path / ".forge" / "github.json").read_text())
    assert written["owner"] == "myorg"
    assert written["enabled"] is True


def test_save_integration_config_saves_token(tmp_path, monkeypatch):
    """Token is saved to profile when provided."""
    saved_profiles = []

    def mock_load():
        return {}

    def mock_save(profile):
        saved_profiles.append(dict(profile))
        return Path.home() / ".forge" / "profile.yaml"

    monkeypatch.setattr("forge.profile.load_profile", mock_load)
    monkeypatch.setattr("forge.profile.save_profile", mock_save)

    result = save_integration_config(
        tmp_path, "github", {"enabled": True}, token="ghp_test123"
    )

    assert result is True
    assert len(saved_profiles) == 1
    assert saved_profiles[0]["github_token"] == "ghp_test123"


def test_save_integration_config_unknown_name(tmp_path):
    result = save_integration_config(tmp_path, "unknown", {"enabled": True})
    assert result is False


def test_check_integration_connection_ollama_unreachable(tmp_path, monkeypatch):
    """Returns (False, message) when Ollama not running."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir(parents=True)
    (forge_dir / "ollama.json").write_text(json.dumps({"enabled": True}))

    def mock_reachable(config):
        return False

    monkeypatch.setattr(
        "forge.ollama_integration.is_ollama_reachable", mock_reachable
    )

    success, msg = check_integration_connection(tmp_path, "ollama")
    assert success is False
    assert "not reachable" in msg.lower() or "ollama" in msg.lower()


def test_integrations_data_returns_json(tmp_path):
    handler = MagicMock()
    handler.wfile = BytesIO()

    with patch("forge.integrations_view._get_token", return_value=""):
        handle_integrations_data(handler, tmp_path)

    handler.send_response.assert_called_once_with(200)
    written = handler.wfile.getvalue()
    data = json.loads(written.decode("utf-8"))
    assert "integrations" in data
    assert len(data["integrations"]) == 6
