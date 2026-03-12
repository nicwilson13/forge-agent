"""Tests for forge/ollama_integration.py."""

import json
from pathlib import Path

from forge.ollama_integration import (
    OllamaConfig,
    load_ollama_config,
    save_ollama_config,
    should_use_ollama,
    is_ollama_reachable,
    chat_with_ollama,
    ollama_chat_with_token_estimate,
)


def test_load_ollama_config_missing(tmp_path):
    """Returns disabled config when .forge/ollama.json missing."""
    config = load_ollama_config(tmp_path)
    assert config.enabled is False
    assert config.host == "http://localhost:11434"
    assert config.model == "llama3.1:8b"


def test_load_ollama_config_valid(tmp_path):
    """Parses valid config correctly."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    config_path = forge_dir / "ollama.json"
    config_path.write_text(json.dumps({
        "enabled": True,
        "host": "http://myhost:11434",
        "model": "codestral:latest",
        "use_for_planning": False,
        "use_for_evaluation": True,
        "timeout": 60,
    }))
    config = load_ollama_config(tmp_path)
    assert config.enabled is True
    assert config.host == "http://myhost:11434"
    assert config.model == "codestral:latest"
    assert config.use_for_planning is False
    assert config.use_for_evaluation is True
    assert config.timeout == 60


def test_load_ollama_config_invalid_json(tmp_path):
    """Returns disabled config on parse error."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    config_path = forge_dir / "ollama.json"
    config_path.write_text("not json{{{")
    config = load_ollama_config(tmp_path)
    assert config.enabled is False


def test_should_use_ollama_disabled():
    """Returns False when config disabled."""
    config = OllamaConfig(enabled=False, use_for_planning=True)
    assert should_use_ollama(config, "generate_tasks") is False


def test_should_use_ollama_planning_enabled():
    """Returns True for generate_tasks when use_for_planning True."""
    config = OllamaConfig(enabled=True, use_for_planning=True)
    assert should_use_ollama(config, "generate_tasks") is True
    assert should_use_ollama(config, "generate_phases") is True


def test_should_use_ollama_evaluation_disabled_by_default():
    """Returns False for evaluate_phase when use_for_evaluation False."""
    config = OllamaConfig(enabled=True, use_for_planning=True, use_for_evaluation=False)
    assert should_use_ollama(config, "evaluate_phase") is False
    assert should_use_ollama(config, "evaluate_qa") is False


def test_should_use_ollama_evaluation_enabled():
    """Returns True for evaluation ops when use_for_evaluation True."""
    config = OllamaConfig(enabled=True, use_for_evaluation=True)
    assert should_use_ollama(config, "evaluate_phase") is True
    assert should_use_ollama(config, "evaluate_qa") is True


def test_should_use_ollama_never_routes_write_architecture():
    """Returns False for write_architecture regardless of config."""
    config = OllamaConfig(
        enabled=True,
        use_for_planning=True,
        use_for_evaluation=True,
    )
    assert should_use_ollama(config, "write_architecture") is False


def test_should_use_ollama_unknown_operation():
    """Returns False for unknown operations."""
    config = OllamaConfig(enabled=True, use_for_planning=True, use_for_evaluation=True)
    assert should_use_ollama(config, "some_unknown_op") is False


def test_is_ollama_reachable_returns_false_on_connection_error(monkeypatch):
    """Returns False when Ollama not running."""
    config = OllamaConfig(enabled=True, host="http://localhost:19999")
    assert is_ollama_reachable(config) is False


def test_chat_with_ollama_returns_none_on_error(monkeypatch):
    """Returns None on connection error."""
    config = OllamaConfig(enabled=True, host="http://localhost:19999")
    result = chat_with_ollama(config, [{"role": "user", "content": "hello"}])
    assert result is None


def test_ollama_chat_with_token_estimate_zero_cost(monkeypatch):
    """Returns cost=0.0 for local inference."""
    import urllib.request

    response_body = json.dumps({
        "message": {"role": "assistant", "content": "Here is a response with some words"},
    }).encode("utf-8")

    class FakeResponse:
        def read(self):
            return response_body
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: FakeResponse())

    config = OllamaConfig(enabled=True, model="testmodel")
    response, usage = ollama_chat_with_token_estimate(
        config, [{"role": "user", "content": "test message"}], system="system prompt"
    )
    assert response == "Here is a response with some words"
    assert usage.model == "ollama:testmodel"
    assert usage.estimated_cost == 0.0
    assert usage.input_tokens > 0
    assert usage.output_tokens > 0


def test_save_load_roundtrip(tmp_path):
    """Save and load produces the same config."""
    config = OllamaConfig(
        enabled=True,
        host="http://custom:1234",
        model="mistral:7b",
        use_for_planning=False,
        use_for_evaluation=True,
        timeout=90,
    )
    save_ollama_config(tmp_path, config)
    loaded = load_ollama_config(tmp_path)
    assert loaded.enabled == config.enabled
    assert loaded.host == config.host
    assert loaded.model == config.model
    assert loaded.use_for_planning == config.use_for_planning
    assert loaded.use_for_evaluation == config.use_for_evaluation
    assert loaded.timeout == config.timeout


def test_ollama_config_default_host():
    """Default host is localhost:11434."""
    config = OllamaConfig()
    assert config.host == "http://localhost:11434"
    assert config.model == "llama3.1:8b"
    assert config.enabled is False
    assert config.use_for_planning is True
    assert config.use_for_evaluation is False
    assert config.timeout == 120
