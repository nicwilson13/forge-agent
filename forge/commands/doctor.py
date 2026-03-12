"""
forge doctor - Pre-flight check for Forge setup.

Runs 8 standard checks plus optional project-specific checks
when run inside a directory with Forge project docs.
Prints pass/fail/warning for each check with specific fix
instructions for any failures.
"""

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from forge.display import SYM_OK, SYM_FAIL, SYM_WARN, divider


class CheckStatus(Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    SKIP = "skip"


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    detail: str
    fix: str | None = None


# ---------------------------------------------------------------------------
# Standard checks
# ---------------------------------------------------------------------------

def _check_python_version() -> CheckResult:
    """Check Python is 3.10+."""
    v = sys.version_info
    version_str = f"Python {v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= (3, 10):
        return CheckResult("Python", CheckStatus.PASS,
                           f"{version_str}  (3.10+ required)")
    return CheckResult("Python", CheckStatus.FAIL,
                       f"{version_str} found, 3.10+ required",
                       "Download Python 3.11+ from python.org")


def _check_claude_code_installed() -> CheckResult:
    """Check Claude Code CLI is on PATH."""
    path = shutil.which("claude")
    if not path:
        return CheckResult("Claude Code CLI", CheckStatus.FAIL,
                           "not found on PATH",
                           "npm install -g @anthropic-ai/claude-code\n"
                           "     then run: claude (and complete login)")
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True, text=True, timeout=30,
        )
        version = result.stdout.strip() or "unknown version"
        return CheckResult("Claude Code CLI", CheckStatus.PASS,
                           f"v{version}  installed and reachable")
    except (subprocess.TimeoutExpired, OSError):
        return CheckResult("Claude Code CLI", CheckStatus.PASS,
                           "found on PATH (version check timed out)")


def _check_claude_code_authenticated(cli_installed: bool) -> CheckResult:
    """Check Claude Code can make a minimal successful call."""
    if not cli_installed:
        return CheckResult("Claude Code authenticated", CheckStatus.SKIP,
                           "skipped (CLI not installed)")
    try:
        result = subprocess.run(
            ["claude", "-p", "say hi", "--max-turns", "1"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return CheckResult("Claude Code authenticated", CheckStatus.PASS,
                               "test call succeeded")
        stderr = result.stderr.lower()
        if "auth" in stderr:
            return CheckResult("Claude Code authenticated", CheckStatus.FAIL,
                               "authentication failed",
                               "Run `claude` in your terminal and complete the login flow")
        return CheckResult("Claude Code authenticated", CheckStatus.FAIL,
                           f"test call failed (exit {result.returncode})",
                           "Run `claude` in your terminal and complete the login flow")
    except subprocess.TimeoutExpired:
        return CheckResult("Claude Code authenticated", CheckStatus.FAIL,
                           "test call timed out (30s)",
                           "Run `claude` in your terminal and complete the login flow")
    except OSError as e:
        return CheckResult("Claude Code authenticated", CheckStatus.FAIL,
                           f"error: {e}",
                           "Run `claude` in your terminal and complete the login flow")


def _check_api_key_set() -> CheckResult:
    """Check ANTHROPIC_API_KEY environment variable is set."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return CheckResult("ANTHROPIC_API_KEY", CheckStatus.FAIL,
                           "not set",
                           "export ANTHROPIC_API_KEY=sk-ant-your-key\n"
                           "     (Windows: set ANTHROPIC_API_KEY=sk-ant-your-key)")
    masked = key[:10] + "..." + key[-4:]
    if not key.startswith("sk-ant-"):
        return CheckResult("ANTHROPIC_API_KEY", CheckStatus.WARN,
                           f"set but format looks unexpected ({masked})",
                           "Expected key starting with sk-ant-...\n"
                           "     Check your key at console.anthropic.com/settings/keys")
    return CheckResult("ANTHROPIC_API_KEY", CheckStatus.PASS,
                       f"set ({masked})")


def _check_api_key_valid(key_set: bool) -> CheckResult:
    """Check ANTHROPIC_API_KEY works with a minimal API call."""
    if not key_set:
        return CheckResult("API key valid", CheckStatus.SKIP,
                           "skipped (key not set)")
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=5,
            messages=[{"role": "user", "content": "hi"}],
        )
        return CheckResult("API key valid", CheckStatus.PASS,
                           "test call succeeded")
    except Exception as e:
        err_str = str(e).lower()
        if "401" in err_str or "auth" in err_str:
            detail = "authentication failed - key may be invalid or expired"
        else:
            detail = f"API call failed: {type(e).__name__}"
        return CheckResult("API key valid", CheckStatus.FAIL, detail,
                           "Check your key at console.anthropic.com/settings/keys\n"
                           "     export ANTHROPIC_API_KEY=sk-ant-your-new-key")


def _check_git_installed() -> CheckResult:
    """Check git is installed."""
    try:
        result = subprocess.run(
            ["git", "--version"],
            capture_output=True, text=True, timeout=30,
        )
        version = result.stdout.strip()
        return CheckResult("Git", CheckStatus.PASS,
                           f"{version}  installed")
    except FileNotFoundError:
        return CheckResult("Git", CheckStatus.FAIL,
                           "not found on PATH",
                           "Download Git from git-scm.com")
    except (subprocess.TimeoutExpired, OSError):
        return CheckResult("Git", CheckStatus.FAIL,
                           "version check failed",
                           "Download Git from git-scm.com")


def _check_git_identity(git_installed: bool) -> CheckResult:
    """Check git user.email is configured."""
    if not git_installed:
        return CheckResult("Git identity", CheckStatus.SKIP,
                           "skipped (git not installed)")
    try:
        result = subprocess.run(
            ["git", "config", "--global", "user.email"],
            capture_output=True, text=True, timeout=30,
        )
        email = result.stdout.strip()
        if email:
            return CheckResult("Git identity", CheckStatus.PASS, email)
        return CheckResult("Git identity", CheckStatus.WARN,
                           "user.email not configured",
                           'git config --global user.email "you@example.com"\n'
                           '     git config --global user.name "Your Name"')
    except (subprocess.TimeoutExpired, OSError):
        return CheckResult("Git identity", CheckStatus.WARN,
                           "could not read git config",
                           'git config --global user.email "you@example.com"\n'
                           '     git config --global user.name "Your Name"')


def _check_skills_directory() -> CheckResult:
    """Check the skills directory and count loaded skill packs."""
    # Look for forge/skills/ relative to the installed package location
    package_dir = Path(__file__).resolve().parent.parent
    skills_dir = package_dir / "skills"
    if not skills_dir.is_dir():
        return CheckResult("Skills directory", CheckStatus.WARN,
                           "no skill packs found",
                           "Ensure forge/skills/ directory exists\n"
                           "     Re-run: pip install -e . from the forge-agent directory")
    md_files = list(skills_dir.glob("*.md"))
    count = len(md_files)
    if count == 0:
        return CheckResult("Skills directory", CheckStatus.WARN,
                           "no skill packs found",
                           "Ensure forge/skills/*.md files exist\n"
                           "     Re-run: pip install -e . from the forge-agent directory")
    return CheckResult("Skills directory", CheckStatus.PASS,
                       f"{count} skill pack(s) loaded")


def _check_playwright() -> CheckResult:
    """Check if Playwright is installed with Chromium."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            version = result.stdout.strip() or "installed"
            return CheckResult("Playwright", CheckStatus.PASS,
                               f"{version}  (visual QA enabled)")
        return CheckResult("Playwright", CheckStatus.WARN,
                           "not installed (visual QA disabled)",
                           "pip install playwright && playwright install chromium")
    except FileNotFoundError:
        return CheckResult("Playwright", CheckStatus.WARN,
                           "not installed (visual QA disabled)",
                           "pip install playwright && playwright install chromium")
    except (subprocess.TimeoutExpired, OSError):
        return CheckResult("Playwright", CheckStatus.WARN,
                           "version check failed (visual QA disabled)",
                           "pip install playwright && playwright install chromium")


def _check_npm_audit() -> CheckResult:
    """Check if npm audit is available."""
    try:
        result = subprocess.run(
            ["npm", "--version"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            version = result.stdout.strip()
            return CheckResult("npm audit", CheckStatus.PASS,
                               f"npm v{version}  (dependency audit enabled)")
        return CheckResult("npm audit", CheckStatus.WARN,
                           "npm not found - dependency audit unavailable",
                           "Install Node.js from nodejs.org")
    except FileNotFoundError:
        return CheckResult("npm audit", CheckStatus.WARN,
                           "npm not found - dependency audit unavailable",
                           "Install Node.js from nodejs.org")
    except (subprocess.TimeoutExpired, OSError):
        return CheckResult("npm audit", CheckStatus.WARN,
                           "npm check failed - dependency audit unavailable",
                           "Install Node.js from nodejs.org")


def _check_pip_audit() -> CheckResult:
    """Check if pip-audit is installed."""
    try:
        result = subprocess.run(
            ["pip-audit", "--version"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            version = result.stdout.strip()
            return CheckResult("pip-audit", CheckStatus.PASS,
                               f"{version}  (Python dependency audit enabled)")
        return CheckResult("pip-audit", CheckStatus.WARN,
                           "pip-audit not found - Python dependency audit unavailable",
                           "pip install pip-audit")
    except FileNotFoundError:
        return CheckResult("pip-audit", CheckStatus.WARN,
                           "pip-audit not found - Python dependency audit unavailable",
                           "pip install pip-audit")
    except (subprocess.TimeoutExpired, OSError):
        return CheckResult("pip-audit", CheckStatus.WARN,
                           "pip-audit check failed - Python dependency audit unavailable",
                           "pip install pip-audit")


# ---------------------------------------------------------------------------
# Project-specific checks
# ---------------------------------------------------------------------------

def _check_vision_md(project_dir: Path) -> CheckResult:
    """Check VISION.md exists and has sufficient detail."""
    path = project_dir / "VISION.md"
    if not path.exists():
        return CheckResult("VISION.md", CheckStatus.FAIL,
                           "not found",
                           "Run `forge new` or manually write VISION.md (aim for 300+ words)")
    content = path.read_text(encoding="utf-8", errors="replace")
    word_count = len(content.split())
    if word_count >= 300:
        return CheckResult("VISION.md", CheckStatus.PASS,
                           f"{word_count} words - good detail")
    if word_count >= 150:
        return CheckResult("VISION.md", CheckStatus.WARN,
                           f"{word_count} words - consider adding more detail for best results",
                           "Expand VISION.md or run `forge new` to regenerate")
    return CheckResult("VISION.md", CheckStatus.FAIL,
                       f"too brief ({word_count} words)",
                       "Run `forge new` or manually write VISION.md (aim for 300+ words)")


def _check_requirements_md(project_dir: Path) -> CheckResult:
    """Check REQUIREMENTS.md exists and has items."""
    path = project_dir / "REQUIREMENTS.md"
    if not path.exists():
        return CheckResult("REQUIREMENTS.md", CheckStatus.FAIL,
                           "not found",
                           "Run `forge new` or manually write REQUIREMENTS.md")
    content = path.read_text(encoding="utf-8", errors="replace")
    count = 0
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("- [ ]") or stripped.startswith("- [x]"):
            count += 1
    if count >= 10:
        return CheckResult("REQUIREMENTS.md", CheckStatus.PASS,
                           f"{count} requirements found")
    if count >= 1:
        return CheckResult("REQUIREMENTS.md", CheckStatus.WARN,
                           f"only {count} requirements - consider adding more",
                           "Add more - [ ] items to REQUIREMENTS.md\n"
                           "     or run `forge new` to regenerate")
    return CheckResult("REQUIREMENTS.md", CheckStatus.FAIL,
                       "not found or empty",
                       "Run `forge new` or manually write REQUIREMENTS.md")


def _check_claude_md(project_dir: Path) -> CheckResult:
    """Check CLAUDE.md exists and has tech stack filled in."""
    path = project_dir / "CLAUDE.md"
    if not path.exists():
        return CheckResult("CLAUDE.md", CheckStatus.FAIL,
                           "not found",
                           "Run `forge new` or manually write CLAUDE.md")
    content = path.read_text(encoding="utf-8", errors="replace")
    # Look for ## Tech Stack section
    match = re.search(r"##\s*Tech\s*Stack(.*?)(?=\n##|\Z)", content, re.DOTALL | re.IGNORECASE)
    if not match:
        return CheckResult("CLAUDE.md", CheckStatus.WARN,
                           "tech stack section not found",
                           "Fill in the ## Tech Stack section in CLAUDE.md\n"
                           "     or run `forge new` to regenerate all docs")
    section = match.group(1).strip()
    # Check for actual content vs placeholder
    non_empty_lines = [l for l in section.splitlines()
                       if l.strip() and not l.strip().startswith("#")]
    if len(non_empty_lines) >= 1:
        return CheckResult("CLAUDE.md", CheckStatus.PASS,
                           "tech stack configured")
    return CheckResult("CLAUDE.md", CheckStatus.WARN,
                       "tech stack section appears empty",
                       "Fill in the ## Tech Stack section in CLAUDE.md\n"
                       "     or run `forge new` to regenerate all docs")


# ---------------------------------------------------------------------------
# MCP config check
# ---------------------------------------------------------------------------

def _check_mcp_config(project_dir: Path) -> CheckResult | None:
    """
    Check .forge/mcp.json if it exists.

    Returns None if no mcp.json (not a failure - MCP is optional).
    Returns PASS if config is valid.
    Returns WARN if config has validation errors.
    """
    mcp_path = project_dir / ".forge" / "mcp.json"
    if not mcp_path.exists():
        return None

    from forge.mcp_config import load_mcp_config, validate_mcp_server

    config = load_mcp_config(project_dir)
    errors = []
    for server in config.servers:
        errors.extend(validate_mcp_server(server))

    if errors:
        return CheckResult(
            "MCP Config", CheckStatus.WARN,
            f"mcp.json has {len(errors)} validation error(s)",
            f"Edit .forge/mcp.json: {'; '.join(errors[:2])}"
        )
    return CheckResult(
        "MCP Config", CheckStatus.PASS,
        f"{len(config.servers)} server(s) configured"
    )


# ---------------------------------------------------------------------------
# GitHub config check
# ---------------------------------------------------------------------------

def _check_github_config(project_dir: Path) -> CheckResult | None:
    """
    Check .forge/github.json if present.

    Returns None if not configured (optional).
    Checks: enabled, owner/repo set, token available in profile.
    WARN if config present but token missing.
    """
    gh_path = project_dir / ".forge" / "github.json"
    if not gh_path.exists():
        return None

    from forge.github_integration import load_github_config, get_github_token

    config = load_github_config(project_dir)
    if not config.enabled:
        return CheckResult(
            "GitHub Integration", CheckStatus.WARN,
            "github.json exists but integration is disabled",
            'Set "enabled": true in .forge/github.json to activate'
        )

    issues = []
    if not config.owner or not config.repo:
        issues.append("owner or repo not set")

    token = get_github_token()
    if not token:
        issues.append("github_token missing from ~/.forge/profile.yaml")

    if issues:
        return CheckResult(
            "GitHub Integration", CheckStatus.WARN,
            f"github.json: {'; '.join(issues)}",
            "Add github_token to ~/.forge/profile.yaml and set owner/repo in .forge/github.json"
        )

    return CheckResult(
        "GitHub Integration", CheckStatus.PASS,
        f"{config.owner}/{config.repo} (token set)"
    )


# ---------------------------------------------------------------------------
# Vercel config check
# ---------------------------------------------------------------------------

def _check_vercel_config(project_dir: Path) -> CheckResult | None:
    """
    Check .forge/vercel.json if present.

    Returns None if not configured (optional).
    WARN if enabled but token missing or project_id empty.
    PASS if config valid.
    """
    vercel_path = project_dir / ".forge" / "vercel.json"
    if not vercel_path.exists():
        return None

    from forge.vercel_integration import load_vercel_config, get_vercel_token

    config = load_vercel_config(project_dir)
    if not config.enabled:
        return CheckResult(
            "Vercel Integration", CheckStatus.WARN,
            "vercel.json exists but integration is disabled",
            'Set "enabled": true in .forge/vercel.json to activate'
        )

    issues = []
    if not config.project_id:
        issues.append("project_id not set")

    token = get_vercel_token()
    if not token:
        issues.append("vercel_token missing from ~/.forge/profile.yaml")

    if issues:
        return CheckResult(
            "Vercel Integration", CheckStatus.WARN,
            f"vercel.json: {'; '.join(issues)}",
            "Add vercel_token to ~/.forge/profile.yaml and set project_id in .forge/vercel.json"
        )

    return CheckResult(
        "Vercel Integration", CheckStatus.PASS,
        f"project {config.project_id} (token set)"
    )


# ---------------------------------------------------------------------------
# Figma config check
# ---------------------------------------------------------------------------

def _check_figma_config(project_dir: Path) -> CheckResult | None:
    """
    Check .forge/figma.json if present.

    Returns None if not configured (optional).
    WARN if enabled but token missing or file_key empty.
    PASS if config valid.
    """
    figma_path = project_dir / ".forge" / "figma.json"
    if not figma_path.exists():
        return None

    from forge.figma_integration import load_figma_config, get_figma_token

    config = load_figma_config(project_dir)
    if not config.enabled:
        return CheckResult(
            "Figma Integration", CheckStatus.WARN,
            "figma.json exists but integration is disabled",
            'Set "enabled": true in .forge/figma.json to activate'
        )

    issues = []
    if not config.file_key:
        issues.append("file_key not set")

    token = get_figma_token()
    if not token:
        issues.append("figma_token missing from ~/.forge/profile.yaml")

    if issues:
        return CheckResult(
            "Figma Integration", CheckStatus.WARN,
            f"figma.json: {'; '.join(issues)}",
            "Add figma_token to ~/.forge/profile.yaml and set file_key in .forge/figma.json"
        )

    return CheckResult(
        "Figma Integration", CheckStatus.PASS,
        f"file {config.file_key} (token set)"
    )


# ---------------------------------------------------------------------------
# Linear config check
# ---------------------------------------------------------------------------

def _check_linear_config(project_dir: Path) -> CheckResult | None:
    """
    Check .forge/linear.json if present.

    Returns None if not configured (optional).
    WARN if enabled but token missing or team_id empty.
    PASS if config valid.
    """
    linear_path = project_dir / ".forge" / "linear.json"
    if not linear_path.exists():
        return None

    from forge.linear_integration import load_linear_config, get_linear_token

    config = load_linear_config(project_dir)
    if not config.enabled:
        return CheckResult(
            "Linear Integration", CheckStatus.WARN,
            "linear.json exists but integration is disabled",
            'Set "enabled": true in .forge/linear.json to activate'
        )

    issues = []
    if not config.team_id:
        issues.append("team_id not set")

    token = get_linear_token()
    if not token:
        issues.append("linear_token missing from ~/.forge/profile.yaml")

    if issues:
        return CheckResult(
            "Linear Integration", CheckStatus.WARN,
            f"linear.json: {'; '.join(issues)}",
            "Add linear_token to ~/.forge/profile.yaml and set team_id in .forge/linear.json"
        )

    return CheckResult(
        "Linear Integration", CheckStatus.PASS,
        f"team {config.team_id} (token set)"
    )


# ---------------------------------------------------------------------------
# CI workflow check
# ---------------------------------------------------------------------------

def _check_github_workflow(project_dir: Path) -> CheckResult | None:
    """
    Check .github/workflows/ci.yml if it exists.

    Returns None if no workflow (optional).
    Returns PASS if workflow file is valid YAML with jobs.
    Returns WARN if workflow file exists but is empty or invalid YAML.
    """
    workflow = project_dir / ".github" / "workflows" / "ci.yml"
    if not workflow.exists():
        return None

    try:
        import yaml
        content = workflow.read_text(encoding="utf-8")
        parsed = yaml.safe_load(content)
        if not parsed or "jobs" not in parsed:
            return CheckResult(
                "CI Workflow", CheckStatus.WARN,
                "ci.yml exists but has no jobs defined",
                "Regenerate with `forge new` or edit .github/workflows/ci.yml"
            )
        job_count = len(parsed.get("jobs", {}))
        return CheckResult(
            "CI Workflow", CheckStatus.PASS,
            f"ci.yml valid ({job_count} job(s))"
        )
    except Exception as e:
        return CheckResult(
            "CI Workflow", CheckStatus.WARN,
            f"ci.yml parse error: {e}",
            "Check .github/workflows/ci.yml for YAML syntax errors"
        )


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

_STATUS_SYMBOLS = {
    CheckStatus.PASS: SYM_OK,
    CheckStatus.FAIL: SYM_FAIL,
    CheckStatus.WARN: SYM_WARN,
    CheckStatus.SKIP: "-",
}

NAME_WIDTH = 28


def _print_report(results: list[CheckResult]) -> None:
    """Print the full doctor report and summary line."""
    for r in results:
        if r.status == CheckStatus.SKIP:
            continue
        sym = _STATUS_SYMBOLS[r.status]
        name_padded = r.name.ljust(NAME_WIDTH)
        print(f"  {sym}  {name_padded}{r.detail}")
        if r.fix and r.status in (CheckStatus.FAIL, CheckStatus.WARN):
            print(f"     Fix: {r.fix}")
            print()

    passed = sum(1 for r in results if r.status == CheckStatus.PASS)
    failed = sum(1 for r in results if r.status == CheckStatus.FAIL)
    warned = sum(1 for r in results if r.status == CheckStatus.WARN)

    parts = []
    if passed:
        parts.append(f"{passed} passed")
    if failed:
        parts.append(f"{failed} failed")
    if warned:
        parts.append(f"{warned} warning{'s' if warned != 1 else ''}")

    print(f"  {', '.join(parts)}.")

    if failed:
        print("  Fix the issues above before running forge.\n")
    else:
        print("  Ready to run. Try `forge new` to start a project.\n")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_doctor(project_dir: Path) -> None:
    """Run all checks, print the report, and exit with appropriate code."""
    d = divider("heavy")
    print(f"\n{d}")
    print(f"  FORGE DOCTOR - Pre-flight Check")
    print(f"{d}")
    print(f"\n  Checking your Forge setup...\n")

    results: list[CheckResult] = []

    # Define checks with dependency tracking
    checks_standard = [
        ("python", _check_python_version),
        ("claude_cli", _check_claude_code_installed),
        ("claude_auth", None),  # depends on claude_cli
        ("api_key_set", _check_api_key_set),
        ("api_key_valid", None),  # depends on api_key_set
        ("git", _check_git_installed),
        ("git_identity", None),  # depends on git
        ("skills", _check_skills_directory),
        ("playwright", _check_playwright),
        ("npm_audit", _check_npm_audit),
        ("pip_audit", _check_pip_audit),
    ]

    # Run standard checks
    cli_installed = False
    key_set = False
    git_installed = False

    for name, check_fn in checks_standard:
        try:
            if name == "claude_cli":
                r = _check_claude_code_installed()
                cli_installed = r.status == CheckStatus.PASS
            elif name == "claude_auth":
                r = _check_claude_code_authenticated(cli_installed)
            elif name == "api_key_set":
                r = _check_api_key_set()
                key_set = r.status in (CheckStatus.PASS, CheckStatus.WARN)
            elif name == "api_key_valid":
                r = _check_api_key_valid(key_set)
            elif name == "git":
                r = _check_git_installed()
                git_installed = r.status == CheckStatus.PASS
            elif name == "git_identity":
                r = _check_git_identity(git_installed)
            elif name == "skills":
                r = _check_skills_directory()
            else:
                r = check_fn()
            results.append(r)
        except Exception as e:
            results.append(CheckResult(name, CheckStatus.FAIL,
                                       f"unexpected error: {e}"))

    # Project-specific checks - only if any Forge doc exists
    has_project = any(
        (project_dir / f).exists()
        for f in ("VISION.md", "REQUIREMENTS.md", "CLAUDE.md")
    )

    if has_project:
        for check_fn in [_check_vision_md, _check_requirements_md, _check_claude_md]:
            try:
                results.append(check_fn(project_dir))
            except Exception as e:
                results.append(CheckResult(check_fn.__name__, CheckStatus.FAIL,
                                           f"unexpected error: {e}"))

    # MCP config check (optional - only when .forge/mcp.json exists)
    mcp_result = _check_mcp_config(project_dir)
    if mcp_result is not None:
        results.append(mcp_result)

    # GitHub config check (optional - only when .forge/github.json exists)
    gh_result = _check_github_config(project_dir)
    if gh_result is not None:
        results.append(gh_result)

    # Vercel config check (optional - only when .forge/vercel.json exists)
    vercel_result = _check_vercel_config(project_dir)
    if vercel_result is not None:
        results.append(vercel_result)

    # Figma config check (optional - only when .forge/figma.json exists)
    figma_result = _check_figma_config(project_dir)
    if figma_result is not None:
        results.append(figma_result)

    # Linear config check (optional - only when .forge/linear.json exists)
    linear_result = _check_linear_config(project_dir)
    if linear_result is not None:
        results.append(linear_result)

    # CI workflow check (optional - only when ci.yml exists)
    ci_result = _check_github_workflow(project_dir)
    if ci_result is not None:
        results.append(ci_result)

    _print_report(results)

    has_failure = any(r.status == CheckStatus.FAIL for r in results)
    sys.exit(1 if has_failure else 0)
