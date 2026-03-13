"""
Security scan for Forge.

Scans the project codebase for common vulnerability patterns after
each phase completes. Critical findings are reviewed by Claude to
filter false positives before blocking the phase commit.

Two scan modes:
1. Pattern scan: fast regex-based scan of all source files
2. Dependency audit: npm audit / pip audit for known CVEs

Critical findings route to Claude for confirmation before blocking.
Warnings are logged but never block the build.
"""

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from forge.cost_tracker import TokenUsage, MODEL_SONNET

def _supports_unicode() -> bool:
    encoding = getattr(sys.stdout, "encoding", "") or ""
    return encoding.lower().replace("-", "") in ("utf8", "utf16", "utf32", "utf8sig")

_SYM_OK = "\u2713" if _supports_unicode() else "[OK]"
_SYM_FAIL = "\u2717" if _supports_unicode() else "[FAIL]"
_SYM_WARN = "\u26a0" if _supports_unicode() else "[WARN]"


# ---------------------------------------------------------------------------
# Finding dataclass
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    severity: str       # "critical" | "warning"
    category: str       # e.g. "hardcoded_secret", "sql_injection"
    file_path: str      # relative to project root
    line_number: int
    line_content: str   # the matching line (truncated to 120 chars)
    pattern: str        # which pattern matched


# ---------------------------------------------------------------------------
# Vulnerability patterns - compiled once at module level
# ---------------------------------------------------------------------------

_CRITICAL_RAW = {
    "hardcoded_secret": [
        r'(?i)(password|passwd|secret|api_key|apikey|token)\s*=\s*["\'][^"\']{8,}["\']',
        r'(?i)sk-[a-zA-Z0-9]{20,}',
        r'(?i)Bearer\s+[a-zA-Z0-9\-._~+/]+=*',
    ],
    "sql_injection": [
        r'execute\s*\(\s*["\'].*\+',
        r'query\s*\(\s*f["\']',
        r'\.raw\s*\(\s*["\'].*\{',
    ],
    "eval_usage": [
        r'\beval\s*\(',
        r'\bnew\s+Function\s*\(',
        r'__import__\s*\(',
    ],
    "path_traversal": [
        r'open\s*\(\s*.*\+\s*(?:request|req|user|input)',
        r'readFile\s*\(\s*.*\+',
    ],
}

_WARNING_RAW = {
    "console_log_secrets": [
        r'(?i)console\.(log|warn|error)\s*\(.*(?:password|secret|token|key)',
    ],
    "http_not_https": [
        r'http://(?!localhost|127\.0\.0\.1|0\.0\.0\.0)',
    ],
    "todo_security": [
        r'(?i)#\s*TODO.*(?:auth|security|secret|password|fix)',
        r'(?i)//\s*TODO.*(?:auth|security|secret|password|fix)',
    ],
    "disabled_ssl": [
        r'(?i)verify\s*=\s*False',
        r'(?i)rejectUnauthorized\s*:\s*false',
    ],
    "weak_crypto": [
        r'(?i)md5\s*\(',
        r'(?i)sha1\s*\(',
        r'(?i)DES\s*\(',
    ],
}

# Compile all patterns once
CRITICAL_PATTERNS: dict[str, list[re.Pattern]] = {
    cat: [re.compile(p) for p in patterns]
    for cat, patterns in _CRITICAL_RAW.items()
}

WARNING_PATTERNS: dict[str, list[re.Pattern]] = {
    cat: [re.compile(p) for p in patterns]
    for cat, patterns in _WARNING_RAW.items()
}

# ---------------------------------------------------------------------------
# Skip patterns
# ---------------------------------------------------------------------------

SKIP_PATTERNS = [
    "node_modules/",
    ".git/",
    "__pycache__/",
    ".forge/",
    "*.min.js",
    "*.lock",
    "*.png", "*.jpg", "*.svg",
    "tests/", "test/", "spec/",
]

_MAX_FILE_SIZE = 500 * 1024  # 500KB


# ---------------------------------------------------------------------------
# File scanning
# ---------------------------------------------------------------------------

def should_skip_file(file_path: Path, project_root: Path) -> bool:
    """
    Return True if this file should be skipped during scanning.

    Checks against SKIP_PATTERNS (path contains any skip pattern).
    Also skips files larger than 500KB.
    Also skips non-text files (try reading first 512 bytes as UTF-8).
    """
    try:
        rel = str(file_path.relative_to(project_root)).replace("\\", "/")
    except ValueError:
        rel = str(file_path).replace("\\", "/")

    for pattern in SKIP_PATTERNS:
        if pattern.startswith("*."):
            # Extension match
            if rel.endswith(pattern[1:]):
                return True
        elif pattern in rel:
            return True

    # Skip large files (only check if file exists on disk)
    try:
        size = file_path.stat().st_size
        if size > _MAX_FILE_SIZE:
            return True
    except OSError:
        # File doesn't exist or can't be stat'd - don't skip on path alone
        return False

    # Skip binary files
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(512)
        chunk.decode("utf-8")
    except UnicodeDecodeError:
        return True
    except OSError:
        return False

    return False


def scan_file(file_path: Path, project_root: Path) -> list[Finding]:
    """
    Scan a single file for vulnerability patterns.

    Checks CRITICAL_PATTERNS and WARNING_PATTERNS.
    Returns list of Finding objects.
    Skips binary files (check if file is text-readable).
    Truncates line_content to 120 chars.
    Never raises - returns [] on any error.
    """
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    try:
        rel_path = str(file_path.relative_to(project_root)).replace("\\", "/")
    except ValueError:
        rel_path = str(file_path).replace("\\", "/")

    findings: list[Finding] = []
    lines = content.splitlines()

    for line_num, line in enumerate(lines, start=1):
        # Check critical patterns
        for category, patterns in CRITICAL_PATTERNS.items():
            for pat in patterns:
                if pat.search(line):
                    findings.append(Finding(
                        severity="critical",
                        category=category,
                        file_path=rel_path,
                        line_number=line_num,
                        line_content=line[:120],
                        pattern=pat.pattern,
                    ))
                    break  # one match per category per line

        # Check warning patterns
        for category, patterns in WARNING_PATTERNS.items():
            for pat in patterns:
                if pat.search(line):
                    findings.append(Finding(
                        severity="warning",
                        category=category,
                        file_path=rel_path,
                        line_number=line_num,
                        line_content=line[:120],
                        pattern=pat.pattern,
                    ))
                    break

    return findings


def scan_project(project_root: Path) -> list[Finding]:
    """
    Scan all files in the project for vulnerability patterns.

    Walks the project directory tree, skipping SKIP_PATTERNS.
    Returns all findings sorted by severity (critical first).
    Never raises.
    """
    findings: list[Finding] = []
    try:
        for file_path in project_root.rglob("*"):
            if not file_path.is_file():
                continue
            if should_skip_file(file_path, project_root):
                continue
            findings.extend(scan_file(file_path, project_root))
    except Exception:
        pass

    # Sort: critical first, then warning
    severity_order = {"critical": 0, "warning": 1}
    findings.sort(key=lambda f: severity_order.get(f.severity, 2))
    return findings


# ---------------------------------------------------------------------------
# Dependency audit
# ---------------------------------------------------------------------------

def run_npm_audit(project_root: Path) -> tuple[list[str], list[str]]:
    """
    Run npm audit and parse the results.

    Returns (critical_vulns, moderate_vulns) as lists of strings.
    Returns ([], []) if npm is not available or no package.json.
    Timeout: 30 seconds.
    Never raises.
    """
    if not (project_root / "package.json").exists():
        return [], []

    try:
        result = subprocess.run(
            ["npm", "audit", "--json"],
            cwd=str(project_root),
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired,
            json.JSONDecodeError, OSError):
        return [], []

    critical: list[str] = []
    moderate: list[str] = []

    # npm audit --json returns vulnerabilities dict
    vulns = data.get("vulnerabilities", {})
    for name, info in vulns.items():
        severity = info.get("severity", "unknown")
        title = info.get("title", name)
        via = info.get("via", [])
        desc = title if isinstance(title, str) else name
        if isinstance(via, list) and via:
            first = via[0]
            if isinstance(first, dict):
                desc = first.get("title", desc)

        line = f"{name}: {desc} ({severity})"
        if severity in ("critical", "high"):
            critical.append(line)
        else:
            moderate.append(line)

    return critical, moderate


def run_pip_audit(project_root: Path) -> tuple[list[str], list[str]]:
    """
    Run pip-audit and parse the results.

    Returns (critical_vulns, moderate_vulns).
    Returns ([], []) if pip-audit is not installed or no requirements.txt.
    Timeout: 30 seconds.
    Never raises.
    """
    has_reqs = (
        (project_root / "requirements.txt").exists()
        or (project_root / "setup.py").exists()
        or (project_root / "pyproject.toml").exists()
    )
    if not has_reqs:
        return [], []

    try:
        result = subprocess.run(
            ["pip-audit", "--format", "json"],
            cwd=str(project_root),
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired,
            json.JSONDecodeError, OSError):
        return [], []

    critical: list[str] = []
    moderate: list[str] = []

    # pip-audit --format json returns a list of vulnerability dicts
    deps = data if isinstance(data, list) else data.get("dependencies", [])
    for dep in deps:
        name = dep.get("name", "unknown")
        version = dep.get("version", "?")
        vulns = dep.get("vulns", [])
        for vuln in vulns:
            vuln_id = vuln.get("id", "unknown")
            desc = vuln.get("description", "")[:80]
            fix = vuln.get("fix_versions", [])
            fix_str = f" (fix: {', '.join(fix)})" if fix else ""
            line = f"{name}@{version}: {vuln_id} - {desc}{fix_str}"
            # pip-audit doesn't always provide severity, treat all as moderate
            moderate.append(line)

    return critical, moderate


# ---------------------------------------------------------------------------
# Claude review of findings
# ---------------------------------------------------------------------------

def get_file_context(file_path: Path, line_number: int,
                     context_lines: int = 5) -> str:
    """
    Return the file content around a specific line number.

    Returns lines (line_number - context_lines) to
    (line_number + context_lines), with the flagged line marked.
    Returns empty string if file cannot be read.
    """
    try:
        lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return ""

    start = max(0, line_number - context_lines - 1)
    end = min(len(lines), line_number + context_lines)

    result_lines = []
    for i in range(start, end):
        num = i + 1
        marker = " >>> " if num == line_number else "     "
        result_lines.append(f"{marker}{num}: {lines[i]}")

    return "\n".join(result_lines)


_REVIEW_SYSTEM = """\
You are a security reviewer. You will be shown code snippets flagged by \
an automated scanner. For each finding, determine if it is a genuine \
security vulnerability or a false positive.

Respond with valid JSON only. No prose, no markdown fences.

Format:
{
  "findings": [
    {
      "index": 0,
      "genuine": true,
      "reason": "brief explanation"
    }
  ]
}

A finding is a FALSE POSITIVE if:
- The value is a placeholder, example, or test value (e.g. "your-api-key-here")
- The code is in a comment or documentation
- The pattern matched a variable name, not an actual secret
- The code is a configuration template (e.g. .env.example)

A finding is GENUINE if:
- It contains what appears to be a real secret, key, or password
- It performs unsafe string interpolation in a database query
- It uses eval() or equivalent on user-controllable input
- It opens files based on user input without sanitization
"""


def review_findings_with_claude(
    findings: list[Finding],
    project_root: Path,
    model: str = MODEL_SONNET,
) -> tuple[list[Finding], list[Finding], TokenUsage]:
    """
    Ask Claude to review critical findings and filter false positives.

    Returns (confirmed, dismissed, usage).
    Never raises - returns (findings, [], empty_usage) on error
    (conservative: keep all findings if Claude review fails).
    """
    if not findings:
        return [], [], TokenUsage()

    # Build review prompt
    parts = []
    for i, f in enumerate(findings):
        full_path = project_root / f.file_path
        context = get_file_context(full_path, f.line_number)
        parts.append(
            f"Finding {i}: [{f.category}] {f.file_path}:{f.line_number}\n"
            f"Matched: {f.line_content}\n"
            f"Context:\n{context}\n"
        )

    user_prompt = (
        "Review these flagged security findings and determine which are "
        "genuine vulnerabilities vs false positives.\n\n"
        + "\n---\n".join(parts)
    )

    try:
        from forge.orchestrator import _chat
        response_text, usage = _chat(
            _REVIEW_SYSTEM, user_prompt,
            max_tokens=2048, model=model,
        )

        data = json.loads(response_text)
        review_results = data.get("findings", [])

        confirmed: list[Finding] = []
        dismissed: list[Finding] = []

        reviewed_indices = {r["index"]: r for r in review_results
                           if isinstance(r, dict) and "index" in r}

        for i, f in enumerate(findings):
            review = reviewed_indices.get(i)
            if review and not review.get("genuine", True):
                dismissed.append(f)
            else:
                confirmed.append(f)

        return confirmed, dismissed, usage

    except Exception:
        # Conservative: keep all findings if review fails
        return list(findings), [], TokenUsage()


# ---------------------------------------------------------------------------
# Main scan pipeline
# ---------------------------------------------------------------------------

def run_security_scan(
    project_root: Path,
    run_audit: bool = True,
) -> tuple[list[Finding], list[Finding], list[str], TokenUsage]:
    """
    Run the full security scan pipeline.

    1. scan_project() for pattern findings
    2. Separate into critical and warning findings
    3. If critical findings exist: review_findings_with_claude()
    4. run_npm_audit() and run_pip_audit() if run_audit=True
    5. Return (confirmed_critical, warnings, audit_vulns, usage)

    Never raises.
    """
    try:
        all_findings = scan_project(project_root)

        critical = [f for f in all_findings if f.severity == "critical"]
        warnings = [f for f in all_findings if f.severity == "warning"]

        usage = TokenUsage()
        confirmed = critical

        if critical:
            confirmed, _dismissed, usage = review_findings_with_claude(
                critical, project_root
            )

        audit_vulns: list[str] = []
        if run_audit:
            npm_crit, npm_mod = run_npm_audit(project_root)
            pip_crit, pip_mod = run_pip_audit(project_root)
            audit_vulns = npm_crit + pip_crit + npm_mod + pip_mod

        return confirmed, warnings, audit_vulns, usage

    except Exception:
        return [], [], [], TokenUsage()


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_scan_results(
    confirmed: list[Finding],
    warnings: list[Finding],
    audit_vulns: list[str],
    files_scanned: int,
) -> str:
    """
    Format scan results for terminal display.
    """
    parts: list[str] = []

    if confirmed:
        parts.append(f"{_SYM_FAIL} Security scan: {len(confirmed)} critical finding(s)")
        for f in confirmed:
            parts.append(f"    - {f.category} in {f.file_path}:{f.line_number}")

    if warnings:
        parts.append(f"{_SYM_WARN} Security scan: {len(warnings)} warning(s) (not blocking)")
        for f in warnings:
            parts.append(f"    - {f.category} in {f.file_path}:{f.line_number}")

    if audit_vulns:
        parts.append(f"{_SYM_WARN} {len(audit_vulns)} dependency vulnerability(ies)")
        for v in audit_vulns:
            parts.append(f"    - {v}")

    if not confirmed and not warnings and not audit_vulns:
        parts.append(f"{_SYM_OK} Security scan passed (0 findings, {files_scanned} files scanned)")

    return "\n".join(parts)
