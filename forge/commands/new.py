"""
forge new - Generate project docs via a guided AI interview.

Accepts an optional product description as a CLI argument.
If not provided, prompts interactively.
Conducts a 5-question interview tailored to the product idea,
then uses the Anthropic API to generate VISION.md, REQUIREMENTS.md,
and CLAUDE.md from the full interview context.
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

import anthropic

from forge.advanced_options import (
    collect_advanced_options,
    advanced_options_to_context,
    advanced_options_to_claude_md_section,
)
from forge.display import SYM_OK, SYM_WARN, divider


_CLIENT = None


def _client() -> anthropic.Anthropic:
    """Return a shared Anthropic client instance."""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    return _CLIENT


def _chat(system: str, user: str, max_tokens: int = 4096) -> str:
    """
    Make a single Anthropic API call.

    Args:
        system: System prompt.
        user: User message.
        max_tokens: Maximum tokens in the response.

    Returns:
        The text content of the response.
    """
    resp = _client().messages.create(
        model="claude-opus-4-5",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text.strip()


def _json_chat(system: str, user: str, max_tokens: int = 4096) -> dict | list:
    """
    Call the Anthropic API and parse JSON from the response.

    Args:
        system: System prompt.
        user: User message.
        max_tokens: Maximum tokens in the response.

    Returns:
        Parsed JSON as a dict or list.
    """
    system_with_json = system + "\n\nYou MUST respond with valid JSON only. No prose, no markdown fences."
    raw = _chat(system_with_json, user, max_tokens)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  [new] Warning: Could not parse AI response as JSON: {e}")
        return {}


def _prompt(question: str) -> str:
    """
    Prompt the user for input, looping until non-empty input is provided.

    Args:
        question: The question to display.

    Returns:
        The user's stripped, non-empty response.
    """
    while True:
        answer = input(question).strip()
        if answer:
            return answer
        print("  Please provide an answer.")


def _prompt_with_default(question: str, default: str) -> str:
    """
    Prompt with a pre-filled default. Empty input accepts the default.

    Args:
        question: The question to display.
        default: Default value shown to the user; returned on empty input.

    Returns:
        The user's answer, or the default if they pressed Enter.
    """
    answer = input(question).strip()
    return answer if answer else default


def _has_existing_docs(project_dir: Path) -> bool:
    """
    Check if any Forge project docs already exist.

    Args:
        project_dir: The project directory to check.

    Returns:
        True if VISION.md, REQUIREMENTS.md, or CLAUDE.md exists.
    """
    return any(
        (project_dir / f).exists()
        for f in ("VISION.md", "REQUIREMENTS.md", "CLAUDE.md")
    )


def _count_requirements(content: str) -> int:
    """
    Count checkbox items in REQUIREMENTS.md content.

    Args:
        content: The full text content of REQUIREMENTS.md.

    Returns:
        Number of '- [ ]' or '- [x]' lines found.
    """
    count = 0
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("- [ ]") or stripped.startswith("- [x]"):
            count += 1
    return count


# ---------------------------------------------------------------------------
# Interview
# ---------------------------------------------------------------------------

QUESTION_SYSTEM = """You are a technical product strategist helping someone
describe their software idea in enough detail for an AI agent to build it.
Generate exactly 5 interview questions tailored specifically to the product
described. Questions should be conversational, not technical, and extract
the information an AI builder needs most.

The 5 questions must cover these topics (but phrased specifically for this product):
1. Primary users / audience
2. Preferred tech stack (suggest they type "you decide" if unsure)
3. Must-have features for v1 (ask for 3-5)
4. Deployment target / hosting
5. Design direction / visual style

Return a JSON array of exactly 5 question strings. Nothing else."""


def _conduct_interview(description: str, profile: dict | None = None) -> dict:
    """
    Conduct a 5-question interview tailored to the product description.

    Calls the Anthropic API to generate tailored questions, then prompts
    the user for each answer interactively. If a profile is provided,
    the stack question (Q2) is auto-filled and deployment/design questions
    (Q4/Q5) show pre-filled defaults.

    Args:
        description: The product description from the user.
        profile: Optional user profile dict with stack preferences.

    Returns:
        A dict with keys: description, q1-q5, a1-a5.
    """
    print("\n  Generating tailored questions...\n")

    questions = _json_chat(QUESTION_SYSTEM, description)

    if not isinstance(questions, list) or len(questions) < 5:
        # Fallback to generic questions
        questions = [
            "Who is the primary user of this product?",
            "What's your preferred tech stack? (e.g. Next.js + Supabase, Django + Postgres, or type \"you decide\")",
            "What are the 3-5 must-have features for your first version?",
            "Where will this be deployed? (e.g. Vercel, AWS, self-hosted)",
            "Describe the design direction in a few words (e.g. \"clean and minimal\", \"bold and energetic\", \"like Linear\")",
        ]

    result = {"description": description}

    # Determine if profile has stack to auto-fill Q2
    has_stack = False
    stack_summary = ""
    if profile:
        from forge.profile import get_stack_summary
        stack_summary = get_stack_summary(profile)
        has_stack = bool(stack_summary)

    shown = 0
    total_shown = 5 - (1 if has_stack else 0)

    for i, question in enumerate(questions[:5], 1):
        result[f"q{i}"] = question

        # Q2 (stack): auto-fill from profile if available
        if i == 2 and has_stack:
            result[f"a{i}"] = stack_summary
            print(f"  {SYM_OK}  Stack from profile: {stack_summary}")
            print(f"     Enter to keep, or type \"customize\" to change:")
            try:
                override = input("  > ").strip()
            except KeyboardInterrupt:
                raise
            if override.lower() == "customize":
                answer = _prompt(f"  Your stack:\n  > ")
                result[f"a{i}"] = answer
            elif override:
                result[f"a{i}"] = override
            print()
            continue

        shown += 1

        # Q4 (deployment) / Q5 (design): show profile default
        default = None
        if i == 4 and profile and profile.get("deployment"):
            default = profile["deployment"]
        elif i == 5 and profile and profile.get("design_direction"):
            default = profile["design_direction"]

        if default:
            print(f"  {shown}/{total_shown}  {question}")
            print(f"       Profile default: {default}")
            answer = _prompt_with_default(
                f"       Enter to keep, or type override:\n  > ", default
            )
        else:
            answer = _prompt(f"  {shown}/{total_shown}  {question}\n  > ")

        result[f"a{i}"] = answer
        print()

    # Advanced options block
    print()
    advanced = collect_advanced_options()
    result["advanced"] = advanced

    return result


# ---------------------------------------------------------------------------
# Document generation
# ---------------------------------------------------------------------------

def _build_interview_context(answers: dict, profile: dict | None = None) -> str:
    """
    Build a formatted string of the full interview for API prompts.

    Args:
        answers: The interview dict with description, q1-q5, a1-a5.
        profile: Optional user profile dict to prepend as context.

    Returns:
        Formatted interview context string.
    """
    lines = []
    if profile:
        from forge.profile import profile_to_claude_md_context
        profile_ctx = profile_to_claude_md_context(profile)
        if profile_ctx:
            lines.append(profile_ctx)
            lines.append("")
    lines.append(f"Product description: {answers['description']}\n")
    for i in range(1, 6):
        q = answers.get(f"q{i}", "")
        a = answers.get(f"a{i}", "")
        lines.append(f"Q{i}: {q}")
        lines.append(f"A{i}: {a}\n")

    advanced = answers.get("advanced", {})
    if advanced:
        lines.append(advanced_options_to_context(advanced))

    return "\n".join(lines)


VISION_SYSTEM = """You are a senior product manager writing a VISION.md
document for an autonomous AI development agent to build from.

Write the document in present tense as if the product already exists.
Include these sections with markdown headers:
- Product Summary (one paragraph: what it does, who uses it, what problem it solves)
- Core User Experience (walk through the primary user journey)
- Key Screens / Interfaces (list the main UI surfaces or CLI commands)
- Integrations (external services, APIs, or systems it connects to)
- Success Criteria (how to know it's "done")

Requirements:
- Reference the specific features and design direction from the interview
- Minimum 350 words - be substantive, not vague
- Tone: confident product brief, not a template
- Start with '# VISION.md' as the first line"""


REQUIREMENTS_SYSTEM = """You are a senior software architect writing
REQUIREMENTS.md for an autonomous AI development agent to build from.

Include these sections:
- Functional Requirements: numbered checkbox list (- [ ]) expanding the
  must-have features from the interview with implicit requirements
- Non-Functional Requirements: checkbox list for performance, security,
  accessibility, inferred from the product type and deployment target
- Out of Scope: bullet list of what v1 explicitly will NOT include
- Technical Constraints: derived from the stack answer
- Design Direction: from the interview answer

Requirements:
- Minimum 20 checkbox items total across functional and non-functional
- Each item must be specific and testable, not vague
- Start with '# REQUIREMENTS.md' as the first line"""


CLAUDE_SYSTEM = """You are a senior developer writing CLAUDE.md - a
configuration file that an autonomous AI coding agent reads on every task
to maintain consistency.

Based on the interview answers, fill in every section with real content:

Sections to include:
- Tech Stack: specific versions and tools based on the stack answer.
  If the user said "you decide", choose a modern, well-supported stack
  appropriate for the product type (SaaS -> Next.js 15 + Supabase,
  CLI tool -> Python, mobile -> React Native + Expo, etc.)
- Code Quality Standards: specific to the chosen stack
- UI / UX Standards: incorporate the design direction from the interview
- Git Conventions: standard Forge conventions
- Architecture Principles: appropriate for the stack
- Autonomous Decision Rules: stack-specific preferences
- DO NOT: stack-specific anti-patterns

Requirements:
- Every bullet point must contain real content, not placeholders
- Pre-fill the testing framework based on the stack
- Include deployment-specific config notes if deployment target was mentioned
- Start with '# CLAUDE.md' as the first line
- Include the line "This file is read by Forge (and Claude Code) on every task."
  right after the heading"""


def _generate_docs(project_dir: Path, description: str, answers: dict,
                   profile: dict | None = None) -> dict:
    """
    Generate VISION.md, REQUIREMENTS.md, and CLAUDE.md from interview context.

    Makes three separate API calls. Writes files atomically - if any call
    fails, no partial files are left behind.

    Args:
        project_dir: The project directory to write files into.
        description: The original product description.
        answers: The full interview dict.
        profile: Optional user profile dict for enriched context.

    Returns:
        A dict mapping filename to content for summary display.

    Raises:
        Exception: If any API call fails, with a clear error message.
    """
    context = _build_interview_context(answers, profile=profile)

    print("  Generating your project docs...\n")

    # Generate all three documents, storing in memory first
    generated = {}

    try:
        generated["VISION.md"] = _chat(VISION_SYSTEM, context, max_tokens=4096)
    except Exception as e:
        print(f"\n  [forge] ERROR: Failed to generate VISION.md: {e}")
        print("  Please check your ANTHROPIC_API_KEY and try again.")
        sys.exit(1)

    try:
        generated["REQUIREMENTS.md"] = _chat(REQUIREMENTS_SYSTEM, context, max_tokens=4096)
    except Exception as e:
        print(f"\n  [forge] ERROR: Failed to generate REQUIREMENTS.md: {e}")
        print("  Please check your ANTHROPIC_API_KEY and try again.")
        sys.exit(1)

    try:
        generated["CLAUDE.md"] = _chat(CLAUDE_SYSTEM, context, max_tokens=4096)
    except Exception as e:
        print(f"\n  [forge] ERROR: Failed to generate CLAUDE.md: {e}")
        print("  Please check your ANTHROPIC_API_KEY and try again.")
        sys.exit(1)

    # Insert ## Project Configuration section into CLAUDE.md if advanced options answered
    config_section = advanced_options_to_claude_md_section(answers.get("advanced", {}))
    if config_section:
        claude_content = generated["CLAUDE.md"]
        insert_idx = claude_content.find("\n## ")
        if insert_idx != -1:
            generated["CLAUDE.md"] = (
                claude_content[:insert_idx] + "\n\n" + config_section +
                claude_content[insert_idx:]
            )
        else:
            generated["CLAUDE.md"] = claude_content + "\n\n" + config_section

    # Write atomically: temp file then rename
    for filename, content in generated.items():
        target = project_dir / filename
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(project_dir), suffix=f".{filename}.tmp", prefix="forge_"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(content)
            Path(tmp_path).replace(target)
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    return generated


# ---------------------------------------------------------------------------
# Optional MCP setup
# ---------------------------------------------------------------------------

def _offer_mcp_setup(project_dir: Path) -> None:
    """
    Offer optional MCP server setup at end of interview.

    If the user types a known service name, writes a starter .forge/mcp.json.
    Press Enter to skip.
    """
    from forge.mcp_config import KNOWN_MCP_STARTERS, MCPServer, MCPConfig, save_mcp_config

    print("\n  Would you like to connect external tools via MCP?")
    print("  MCP lets Forge read live data from GitHub, Supabase, Linear, and more")
    print("  during the build. Press Enter to skip, or type a service name to add it.")
    print(f"  Common options: {', '.join(KNOWN_MCP_STARTERS.keys())}")

    try:
        answer = input("  > ").strip().lower()
    except KeyboardInterrupt:
        print()
        return

    if not answer:
        return

    servers = []
    for name in answer.replace(",", " ").split():
        name = name.strip()
        if name in KNOWN_MCP_STARTERS:
            starter = KNOWN_MCP_STARTERS[name]
            servers.append(MCPServer(**starter))
            print(f"  {SYM_OK} Added {name} MCP server")
        else:
            print(f"  {SYM_WARN} Unknown service '{name}' - skipped")

    if servers:
        config = MCPConfig(servers=servers)
        save_mcp_config(project_dir, config)
        print(f"  Wrote .forge/mcp.json with {len(servers)} server(s)")
    print()


# ---------------------------------------------------------------------------
# Optional GitHub integration setup
# ---------------------------------------------------------------------------

def _offer_github_setup(project_dir: Path) -> None:
    """
    Offer optional GitHub integration setup at end of interview.

    If the user enters owner/repo, writes a starter .forge/github.json.
    Prompts for GitHub token and saves to ~/.forge/profile.yaml.
    """
    from forge.github_integration import GitHubConfig, save_github_config

    print("\n  Would you like to enable GitHub integration?")
    print("  Forge can create PRs, milestones, and build summaries automatically.")
    print("  Enter your GitHub repo (e.g. owner/repo), or press Enter to skip:")

    try:
        answer = input("  > ").strip()
    except KeyboardInterrupt:
        print()
        return

    if not answer or "/" not in answer:
        return

    parts = answer.split("/", 1)
    owner = parts[0].strip()
    repo = parts[1].strip()

    if not owner or not repo:
        print(f"  {SYM_WARN} Invalid format - expected owner/repo")
        return

    config = GitHubConfig(
        enabled=True,
        owner=owner,
        repo=repo,
    )
    save_github_config(project_dir, config)
    print(f"  {SYM_OK} Wrote .forge/github.json for {owner}/{repo}")

    # Check for existing token
    from forge.github_integration import get_github_token
    existing_token = get_github_token()
    if existing_token:
        print(f"  {SYM_OK} GitHub token already set in profile")
        return

    print("\n  Enter your GitHub personal access token (or press Enter to skip):")
    print("  (Create one at github.com/settings/tokens with repo scope)")

    try:
        token = input("  > ").strip()
    except KeyboardInterrupt:
        print()
        return

    if token:
        from forge.profile import load_profile, save_profile
        profile = load_profile()
        profile["github_token"] = token
        save_profile(profile)
        print(f"  {SYM_OK} GitHub token saved to ~/.forge/profile.yaml")
    else:
        print(f"  {SYM_WARN} No token set - add github_token to ~/.forge/profile.yaml later")

    print()


# ---------------------------------------------------------------------------
# Optional GitHub Actions workflow generation
# ---------------------------------------------------------------------------

def _offer_workflow_generation(project_dir: Path) -> None:
    """
    Offer optional GitHub Actions CI workflow generation.

    Detects the project stack and generates .github/workflows/ci.yml.
    Press Enter to accept (default Y), or 'n' to skip.
    """
    from forge.workflow_generator import generate_and_write_workflow

    print("\n  Would you like to generate a GitHub Actions CI workflow? [Y/n]: ", end="")
    try:
        answer = input().strip().lower()
    except KeyboardInterrupt:
        print()
        return

    if answer in ("n", "no"):
        return

    workflow_path = generate_and_write_workflow(project_dir)
    if workflow_path:
        try:
            rel = workflow_path.relative_to(project_dir)
        except ValueError:
            rel = workflow_path
        print(f"  {SYM_OK} Generated: {rel}")
    else:
        print("  (workflow generation skipped)")
    print()


# ---------------------------------------------------------------------------
# Optional Vercel integration setup
# ---------------------------------------------------------------------------

def _offer_vercel_setup(project_dir: Path) -> None:
    """
    Offer optional Vercel integration setup at end of interview.

    If the user enters a project ID, writes .forge/vercel.json.
    Prompts for Vercel token and saves to ~/.forge/profile.yaml.
    """
    from forge.vercel_integration import VercelConfig, save_vercel_config, get_vercel_token

    print("\n  Would you like to connect Vercel for deployment status checks?")
    print("  Forge can monitor deployments and auto-fix build failures.")
    print("  Enter your Vercel project ID (from Project Settings), or press Enter to skip:")

    try:
        answer = input("  > ").strip()
    except KeyboardInterrupt:
        print()
        return

    if not answer:
        return

    project_id = answer

    # Optionally ask for team ID
    print("  Enter your Vercel team ID (or press Enter to skip):")
    try:
        team_id = input("  > ").strip()
    except KeyboardInterrupt:
        print()
        team_id = ""

    config = VercelConfig(
        enabled=True,
        project_id=project_id,
        team_id=team_id,
    )
    save_vercel_config(project_dir, config)
    print(f"  {SYM_OK} Wrote .forge/vercel.json for project {project_id}")

    # Check for existing token
    existing_token = get_vercel_token()
    if existing_token:
        print(f"  {SYM_OK} Vercel token already set in profile")
        print()
        return

    print("\n  Enter your Vercel API token (or press Enter to skip):")
    print("  (Create one at vercel.com/account/tokens)")

    try:
        token = input("  > ").strip()
    except KeyboardInterrupt:
        print()
        return

    if token:
        from forge.profile import load_profile, save_profile
        profile = load_profile()
        profile["vercel_token"] = token
        save_profile(profile)
        print(f"  {SYM_OK} Vercel token saved to ~/.forge/profile.yaml")
    else:
        print(f"  {SYM_WARN} No token set - add vercel_token to ~/.forge/profile.yaml later")

    print()


# ---------------------------------------------------------------------------
# Optional Figma integration setup
# ---------------------------------------------------------------------------

def _offer_figma_setup(project_dir: Path) -> None:
    """
    Offer optional Figma integration setup at end of interview.

    If the user enters a file key, writes .forge/figma.json.
    Prompts for Figma token and saves to ~/.forge/profile.yaml.
    """
    from forge.figma_integration import FigmaConfig, save_figma_config, get_figma_token

    print("\n  Would you like to connect a Figma file for design token extraction?")
    print("  Forge will read your design tokens and component names to build closer to your actual design.")
    print("  Enter your Figma file key (from the URL), or press Enter to skip:")

    try:
        answer = input("  > ").strip()
    except KeyboardInterrupt:
        print()
        return

    if not answer:
        return

    file_key = answer

    config = FigmaConfig(
        enabled=True,
        file_key=file_key,
    )
    save_figma_config(project_dir, config)
    print(f"  {SYM_OK} Wrote .forge/figma.json for file {file_key}")

    # Check for existing token
    existing_token = get_figma_token()
    if existing_token:
        print(f"  {SYM_OK} Figma token already set in profile")
        print()
        return

    print("\n  Enter your Figma personal access token (or press Enter to skip):")
    print("  (Create one at figma.com/settings under Personal access tokens)")

    try:
        token = input("  > ").strip()
    except KeyboardInterrupt:
        print()
        return

    if token:
        from forge.profile import load_profile, save_profile
        profile = load_profile()
        profile["figma_token"] = token
        save_profile(profile)
        print(f"  {SYM_OK} Figma token saved to ~/.forge/profile.yaml")
    else:
        print(f"  {SYM_WARN} No token set - add figma_token to ~/.forge/profile.yaml later")

    print()


# ---------------------------------------------------------------------------
# Optional Linear integration setup
# ---------------------------------------------------------------------------

def _offer_linear_setup(project_dir: Path) -> None:
    """
    Offer optional Linear integration setup at end of interview.

    If the user enters a team ID, writes .forge/linear.json.
    Prompts for Linear token and saves to ~/.forge/profile.yaml.
    """
    from forge.linear_integration import LinearConfig, save_linear_config, get_linear_token

    print("\n  Would you like to connect Linear for issue tracking integration?")
    print("  Forge will read open issues and update status as tasks complete.")
    print("  Enter your Linear team ID, or press Enter to skip:")

    try:
        answer = input("  > ").strip()
    except KeyboardInterrupt:
        print()
        return

    if not answer:
        return

    team_id = answer

    # Optionally ask for project ID
    print("  Enter your Linear project ID (or press Enter to skip):")
    try:
        project_id = input("  > ").strip()
    except KeyboardInterrupt:
        print()
        project_id = ""

    config = LinearConfig(
        enabled=True,
        team_id=team_id,
        project_id=project_id,
    )
    save_linear_config(project_dir, config)
    print(f"  {SYM_OK} Wrote .forge/linear.json for team {team_id}")

    # Check for existing token
    existing_token = get_linear_token()
    if existing_token:
        print(f"  {SYM_OK} Linear token already set in profile")
        print()
        return

    print("\n  Enter your Linear API key (or press Enter to skip):")
    print("  (Create one at linear.app/settings/api)")

    try:
        token = input("  > ").strip()
    except KeyboardInterrupt:
        print()
        return

    if token:
        from forge.profile import load_profile, save_profile
        profile = load_profile()
        profile["linear_token"] = token
        save_profile(profile)
        print(f"  {SYM_OK} Linear token saved to ~/.forge/profile.yaml")
    else:
        print(f"  {SYM_WARN} No token set - add linear_token to ~/.forge/profile.yaml later")

    print()


def _offer_sentry_setup(project_dir: Path) -> None:
    """
    Offer optional Sentry integration setup at end of interview.

    If the user enters an org slug, writes .forge/sentry.json.
    Prompts for Sentry auth token and saves to ~/.forge/profile.yaml.
    """
    from forge.sentry_integration import SentryConfig, save_sentry_config, get_sentry_token

    print("\n  Would you like to connect Sentry for error monitoring?")
    print("  Forge will check for runtime errors after each deployment and auto-fix them.")
    print("  Enter your Sentry org slug (from sentry.io URL), or press Enter to skip:")

    try:
        answer = input("  > ").strip()
    except KeyboardInterrupt:
        print()
        return

    if not answer:
        return

    org_slug = answer

    print("  Enter your Sentry project slug:")
    try:
        project_slug = input("  > ").strip()
    except KeyboardInterrupt:
        print()
        project_slug = ""

    if not project_slug:
        print(f"  {SYM_WARN} Project slug required - skipping Sentry setup")
        return

    config = SentryConfig(
        enabled=True,
        org_slug=org_slug,
        project_slug=project_slug,
    )
    save_sentry_config(project_dir, config)
    print(f"  {SYM_OK} Wrote .forge/sentry.json for {org_slug}/{project_slug}")

    # Check for existing token
    existing_token = get_sentry_token()
    if existing_token:
        print(f"  {SYM_OK} Sentry token already set in profile")
        print()
        return

    print("\n  Enter your Sentry auth token (or press Enter to skip):")
    print("  (Create one at sentry.io/settings/auth-tokens/)")

    try:
        token = input("  > ").strip()
    except KeyboardInterrupt:
        print()
        return

    if token:
        from forge.profile import load_profile, save_profile
        profile = load_profile()
        profile["sentry_token"] = token
        save_profile(profile)
        print(f"  {SYM_OK} Sentry token saved to ~/.forge/profile.yaml")
    else:
        print(f"  {SYM_WARN} No token set - add sentry_token to ~/.forge/profile.yaml later")

    print()


# ---------------------------------------------------------------------------
# Optional Ollama local LLM setup
# ---------------------------------------------------------------------------

def _offer_ollama_setup(project_dir: Path) -> None:
    """
    Offer optional Ollama local LLM setup at end of interview.

    If the user enters a model name, writes .forge/ollama.json with defaults.
    Press Enter to skip.
    """
    from forge.ollama_integration import OllamaConfig, save_ollama_config

    print("\n  Would you like to use Ollama for local LLM inference?")
    print("  Forge can route planning tasks to a local model to reduce API costs.")
    print("  Requires Ollama running at localhost:11434. Press Enter to skip.")
    print("  Enter Ollama model name (e.g. llama3.1:8b, mistral, codestral):")

    try:
        answer = input("  > ").strip()
    except KeyboardInterrupt:
        print()
        return

    if not answer:
        return

    config = OllamaConfig(
        enabled=True,
        model=answer,
    )
    save_ollama_config(project_dir, config)
    print(f"  {SYM_OK} Wrote .forge/ollama.json for model {answer}")
    print()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_new(project_dir: Path, description: Optional[str] = None) -> None:
    """
    Run the forge new command.

    Opens a browser-based setup wizard at localhost:3333/setup.
    Falls back to terminal interview if browser cannot be opened.
    """
    project_dir = Path(project_dir).resolve()
    project_dir.mkdir(parents=True, exist_ok=True)

    # Try browser wizard first
    try:
        from forge.dashboard import start_dashboard, stop_dashboard
        import webbrowser

        thread = start_dashboard(project_dir)
        if thread is not None:
            url = "http://localhost:3333/setup"
            if description:
                import urllib.parse
                url += f"?description={urllib.parse.quote(description)}"

            print(f"\n  Setup wizard: {url}")
            print("  Opening browser...")
            webbrowser.open(url)

            print("  Press Ctrl+C to cancel\n")
            try:
                while True:
                    time.sleep(1)
                    run_log = project_dir / ".forge" / "run_output.log"
                    if run_log.exists():
                        print("  Build started. Dashboard: http://localhost:3333")
                        return
            except KeyboardInterrupt:
                print("\n  Setup cancelled. Falling back to terminal interview.")
                stop_dashboard()
                # Fall through to terminal interview
        # If start_dashboard returned None (port in use), fall through
    except Exception:
        pass  # Fall through to terminal interview

    _run_terminal_interview(project_dir, description)


def _run_terminal_interview(project_dir: Path, description: Optional[str] = None) -> None:
    """
    Run the terminal-based forge new interview (fallback).

    Args:
        project_dir: The target project directory.
        description: Optional product description. If None, prompts interactively.
    """
    project_dir = Path(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)

    # Header
    d = divider("heavy")
    print(f"\n{d}")
    print(f"  FORGE NEW - Project Setup Interview")
    print(d)

    # Check for existing docs
    if _has_existing_docs(project_dir):
        print(f"\n  {SYM_WARN}  This directory already has project docs.")
        print(f"     Regenerating will overwrite VISION.md, REQUIREMENTS.md, CLAUDE.md.")
        try:
            confirm = input("     Continue? (yes/no): ").strip().lower()
        except KeyboardInterrupt:
            print("\n\n[forge] Interview cancelled.")
            return
        if confirm != "yes":
            print("\n[forge] Cancelled. Existing docs unchanged.")
            return
        print()

    # Load profile
    from forge.profile import load_profile, has_profile, get_stack_summary
    profile = load_profile() if has_profile() else None
    if profile:
        stack = get_stack_summary(profile)
        if stack:
            print(f"\n  {SYM_OK}  Profile loaded: {stack}")
            print(f"     (run `forge profile --edit` to change defaults)")

    # Get description
    try:
        if description is None:
            print("\n  What are you building? Describe your product idea:")
            description = _prompt("  > ")
            print()

        q_count = "Four" if profile and get_stack_summary(profile) else "Five"
        print(f"  Great idea. {q_count} quick questions to tailor your build.")

        # Conduct interview
        answers = _conduct_interview(description, profile=profile)

        # Generate documents
        generated = _generate_docs(project_dir, description, answers, profile=profile)

    except KeyboardInterrupt:
        print("\n\n[forge] Interview cancelled.")
        return

    # Optional MCP setup
    _offer_mcp_setup(project_dir)

    # Optional GitHub integration setup
    _offer_github_setup(project_dir)

    # Optional GitHub Actions workflow generation
    _offer_workflow_generation(project_dir)

    # Optional Vercel integration setup
    _offer_vercel_setup(project_dir)

    # Optional Figma integration setup
    _offer_figma_setup(project_dir)

    # Optional Linear integration setup
    _offer_linear_setup(project_dir)

    # Optional Sentry integration setup
    _offer_sentry_setup(project_dir)

    # Optional Ollama local LLM setup
    _offer_ollama_setup(project_dir)

    # Create .forge dir
    forge_dir = project_dir / ".forge"
    forge_dir.mkdir(exist_ok=True)

    # Print summary
    vision_words = len(generated["VISION.md"].split())
    req_count = _count_requirements(generated["REQUIREMENTS.md"])
    claude_content = generated["CLAUDE.md"]

    # Extract tech stack hint from CLAUDE.md
    stack_line = ""
    for line in claude_content.splitlines():
        if "language:" in line.lower() or "framework:" in line.lower():
            stack_line = line.strip().lstrip("- ").strip()
            break
    if not stack_line:
        stack_line = "See CLAUDE.md for details"

    print(f"  {SYM_OK} VISION.md         ({vision_words} words)")
    print(f"  {SYM_OK} REQUIREMENTS.md   ({req_count} requirements)")
    print(f"  {SYM_OK} CLAUDE.md         ({stack_line})")

    print(f"\n  Your project is ready. Run `forge run` to start building.\n")
