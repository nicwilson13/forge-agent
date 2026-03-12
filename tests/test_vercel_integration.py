"""Tests for forge.vercel_integration module."""

import json
from pathlib import Path

from forge.vercel_integration import (
    VercelConfig,
    load_vercel_config,
    save_vercel_config,
    get_vercel_token,
    _vercel_request,
    run_vercel_check,
    format_vercel_status,
    get_latest_deployment,
    get_deployment_build_logs,
    wait_for_deployment,
)


def test_load_vercel_config_missing(tmp_path):
    """Returns disabled config when .forge/vercel.json missing."""
    config = load_vercel_config(tmp_path)
    assert config.enabled is False
    assert config.project_id == ""
    assert config.team_id == ""


def test_load_vercel_config_valid(tmp_path):
    """Parses valid config correctly."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    config_data = {
        "enabled": True,
        "project_id": "prj_abc123",
        "team_id": "team_xyz",
        "check_deployments": True,
        "deployment_timeout": 60,
    }
    (forge_dir / "vercel.json").write_text(json.dumps(config_data))

    config = load_vercel_config(tmp_path)
    assert config.enabled is True
    assert config.project_id == "prj_abc123"
    assert config.team_id == "team_xyz"
    assert config.check_deployments is True
    assert config.deployment_timeout == 60


def test_load_vercel_config_invalid_json(tmp_path):
    """Returns disabled config on parse error."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    (forge_dir / "vercel.json").write_text("not valid json {{{")

    config = load_vercel_config(tmp_path)
    assert config.enabled is False


def test_get_vercel_token_missing(monkeypatch):
    """Returns empty string when token not in profile."""
    monkeypatch.setattr(Path, "home", lambda: Path("/nonexistent/path"))
    result = get_vercel_token()
    assert result == ""


def test_vercel_request_returns_none_on_error(monkeypatch):
    """_vercel_request returns None on network error."""
    import urllib.request

    def mock_urlopen(*args, **kwargs):
        raise ConnectionError("no network")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    result = _vercel_request("GET", "/v6/deployments", "fake-token")
    assert result is None


def test_run_vercel_check_disabled(tmp_path):
    """Returns ('disabled', ...) when not configured."""
    status, url, logs = run_vercel_check(tmp_path)
    assert status == "disabled"
    assert logs == ""


def test_format_vercel_status_ready():
    """Ready status shows URL."""
    result = format_vercel_status("ready", "https://my-app.vercel.app")
    assert "Vercel: deployment ready" in result
    assert "https://my-app.vercel.app" in result


def test_format_vercel_status_error():
    """Error status shows failure message."""
    result = format_vercel_status("error", "Build failed")
    assert "Vercel: deployment failed" in result
    assert "Build failed" in result


def test_format_vercel_status_timeout():
    """Timeout status shows warning."""
    result = format_vercel_status("timeout", "")
    assert "timed out" in result


def test_format_vercel_status_disabled():
    """Disabled status shows not configured message."""
    result = format_vercel_status("disabled", "")
    assert "not configured" in result


def test_save_load_roundtrip(tmp_path):
    """Config round-trips through save and load."""
    config = VercelConfig(
        enabled=True,
        project_id="prj_test123",
        team_id="team_test456",
        check_deployments=True,
        deployment_timeout=90,
    )
    save_vercel_config(tmp_path, config)
    loaded = load_vercel_config(tmp_path)
    assert loaded.enabled is True
    assert loaded.project_id == "prj_test123"
    assert loaded.team_id == "team_test456"
    assert loaded.deployment_timeout == 90


def test_run_vercel_check_never_raises(monkeypatch, tmp_path):
    """run_vercel_check never raises even with broken config."""
    # Write a config that's enabled but will fail on API call
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    config_data = {
        "enabled": True,
        "project_id": "prj_test",
        "team_id": "team_test",
    }
    (forge_dir / "vercel.json").write_text(json.dumps(config_data))

    # Mock get_vercel_token to return a token
    monkeypatch.setattr(
        "forge.vercel_integration.get_vercel_token", lambda: "fake-token"
    )

    # Mock wait_for_deployment to raise
    def mock_wait(*args, **kwargs):
        raise RuntimeError("unexpected error")

    monkeypatch.setattr(
        "forge.vercel_integration.wait_for_deployment", mock_wait
    )

    # Should not raise
    status, url, logs = run_vercel_check(tmp_path)
    assert status == "skipped"


def test_get_latest_deployment_disabled():
    """Returns None when config disabled."""
    config = VercelConfig(enabled=False)
    result = get_latest_deployment(config, "token", "sha123")
    assert result is None


def test_get_latest_deployment_no_token():
    """Returns None when token empty."""
    config = VercelConfig(enabled=True, project_id="prj_test")
    result = get_latest_deployment(config, "", "sha123")
    assert result is None


def test_get_deployment_build_logs_no_token():
    """Returns empty string when token empty."""
    config = VercelConfig(enabled=True, project_id="prj_test")
    result = get_deployment_build_logs(config, "", "dep123")
    assert result == ""


def test_get_deployment_build_logs_no_id():
    """Returns empty string when deployment_id empty."""
    config = VercelConfig(enabled=True, project_id="prj_test")
    result = get_deployment_build_logs(config, "token", "")
    assert result == ""


def test_wait_for_deployment_disabled():
    """Returns skipped when config disabled."""
    config = VercelConfig(enabled=False)
    status, url = wait_for_deployment(config, "token")
    assert status == "skipped"


def test_wait_for_deployment_no_token():
    """Returns skipped when token empty."""
    config = VercelConfig(enabled=True, project_id="prj_test")
    status, url = wait_for_deployment(config, "")
    assert status == "skipped"


def test_format_vercel_status_skipped_with_reason():
    """Skipped status shows reason."""
    result = format_vercel_status("skipped", "vercel_token not set")
    assert "skipped" in result
    assert "vercel_token not set" in result


def test_format_vercel_status_ready_no_url():
    """Ready status without URL still shows ready."""
    result = format_vercel_status("ready", "")
    assert "deployment ready" in result


def test_format_vercel_status_error_no_msg():
    """Error status without message still shows failed."""
    result = format_vercel_status("error", "")
    assert "deployment failed" in result
