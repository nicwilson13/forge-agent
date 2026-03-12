"""
Ollama local LLM integration for Forge.

Routes qualifying orchestrator planning calls to a local Ollama instance
instead of the Anthropic API. Builder calls (file writing) always use
Claude Code SDK - Ollama cannot replace that.

Configuration: .forge/ollama.json (project-level, checked in is fine)
No token required - Ollama runs locally.

Ollama API: http://localhost:11434 (default)
Compatible with Ollama REST API /api/chat endpoint.

This module imports only stdlib. No forge imports.
"""

import json
import urllib.request
import urllib.error
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class OllamaConfig:
    enabled: bool = False
    host: str = "http://localhost:11434"
    model: str = "llama3.1:8b"
    use_for_planning: bool = True
    use_for_evaluation: bool = False
    timeout: int = 120


def load_ollama_config(project_dir: Path) -> OllamaConfig:
    """Load from .forge/ollama.json. Never raises."""
    try:
        path = project_dir / ".forge" / "ollama.json"
        if not path.exists():
            return OllamaConfig()
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return OllamaConfig()
        return OllamaConfig(
            enabled=data.get("enabled", False),
            host=data.get("host", "http://localhost:11434"),
            model=data.get("model", "llama3.1:8b"),
            use_for_planning=data.get("use_for_planning", True),
            use_for_evaluation=data.get("use_for_evaluation", False),
            timeout=data.get("timeout", 120),
        )
    except Exception:
        return OllamaConfig()


def save_ollama_config(project_dir: Path, config: OllamaConfig) -> None:
    """Save to .forge/ollama.json. Never raises."""
    try:
        forge_dir = project_dir / ".forge"
        forge_dir.mkdir(parents=True, exist_ok=True)
        path = forge_dir / "ollama.json"
        data = asdict(config)
        tmp_path = path.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        tmp_path.replace(path)
    except Exception:
        pass


def is_ollama_reachable(config: OllamaConfig) -> bool:
    """
    Check if Ollama is running and the configured model is available.

    GET {host}/api/tags - lists available models.
    Returns True if reachable and model present in response.
    Returns False on any error (connection refused, timeout, etc).
    Timeout: 5s (fast check for doctor command).
    Never raises.
    """
    try:
        url = f"{config.host.rstrip('/')}/api/tags"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        models = data.get("models", [])
        # Check if configured model is available (match by name prefix)
        model_name = config.model
        for m in models:
            name = m.get("name", "")
            if name == model_name or name.startswith(model_name.split(":")[0] + ":"):
                return True
        # If no models listed but server responded, still consider reachable
        # if model list is empty (user may need to pull)
        return False
    except Exception:
        return False


def chat_with_ollama(
    config: OllamaConfig,
    messages: list[dict],
    system: str = "",
) -> str | None:
    """
    Send a chat request to Ollama and return the response text.

    POST {host}/api/chat
    Body: {
        "model": config.model,
        "messages": messages,  (OpenAI format: [{role, content}])
        "stream": false,
        "options": {"temperature": 0.3}
    }

    If system is provided, prepend as {"role": "system", "content": system}.
    Returns response text string, or None on any error.
    Timeout: config.timeout seconds.
    Never raises.
    """
    try:
        url = f"{config.host.rstrip('/')}/api/chat"
        all_messages = list(messages)
        if system:
            all_messages = [{"role": "system", "content": system}] + all_messages

        body = {
            "model": config.model,
            "messages": all_messages,
            "stream": False,
            "options": {"temperature": 0.3},
        }

        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=config.timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        message = result.get("message", {})
        content = message.get("content", "")
        return content if content else None
    except Exception:
        return None


def should_use_ollama(
    config: OllamaConfig,
    operation: str,
) -> bool:
    """
    Determine if an orchestrator operation should use Ollama.

    Planning operations (use_for_planning):
    - "generate_phases"
    - "generate_tasks"

    Evaluation operations (use_for_evaluation):
    - "evaluate_phase"
    - "evaluate_qa"

    Never routes to Ollama:
    - "write_architecture"  (always Opus)
    - Any unknown operation

    Returns False if Ollama disabled or not configured.
    """
    if not config.enabled:
        return False

    planning_ops = {"generate_phases", "generate_tasks"}
    evaluation_ops = {"evaluate_phase", "evaluate_qa"}

    if operation in planning_ops:
        return config.use_for_planning
    if operation in evaluation_ops:
        return config.use_for_evaluation

    return False


def ollama_chat_with_token_estimate(
    config: OllamaConfig,
    messages: list[dict],
    system: str = "",
) -> tuple:
    """
    Call chat_with_ollama and return (response, TokenUsage).

    Since Ollama doesn't report token counts in a standard way,
    estimate using word count * 1.3 approximation:
    - prompt_tokens: sum of len(m['content'].split()) * 1.3 for all messages
    - completion_tokens: len(response.split()) * 1.3
    - cost: 0.0 (local inference)
    - model: f"ollama:{config.model}"

    Returns (None, zero_usage) on error.
    """
    # Import here to avoid circular imports
    from forge.cost_tracker import TokenUsage

    model_name = f"ollama:{config.model}"
    zero_usage = TokenUsage(input_tokens=0, output_tokens=0, model=model_name)

    response = chat_with_ollama(config, messages, system)
    if response is None:
        return None, zero_usage

    # Estimate token counts from word counts
    prompt_words = 0
    if system:
        prompt_words += len(system.split())
    for m in messages:
        content = m.get("content", "")
        prompt_words += len(content.split())

    completion_words = len(response.split())

    prompt_tokens = int(prompt_words * 1.3)
    completion_tokens = int(completion_words * 1.3)

    usage = TokenUsage(
        input_tokens=prompt_tokens,
        output_tokens=completion_tokens,
        model=model_name,
    )

    return response, usage
