"""
Project memory manager for Forge.

Stores architectural decisions, code patterns, and failed approaches
in .forge/memory/ as human-readable, human-editable markdown files.

Memory is read before every task execution and written after every
successful task. This prevents context drift across sessions.

Files:
  .forge/memory/decisions.md  - architectural decisions with rationale
  .forge/memory/patterns.md   - code patterns established in the project
  .forge/memory/failures.md   - failed approaches to never retry

This module has zero imports from other forge modules.
"""

import os
import re
from datetime import datetime
from pathlib import Path

DECISIONS_HEADER = """# Architectural Decisions

This file is maintained by Forge. It records architectural decisions
made during the build with their rationale. Read before every task.

---

"""

PATTERNS_HEADER = """# Code Patterns

This file is maintained by Forge. It records code patterns and
conventions established in this project. Follow these consistently.

---

"""

FAILURES_HEADER = """# Failed Approaches - Do Not Retry

This file is maintained by Forge. It records approaches that were
tried and failed, with the reason. Never retry these approaches.

---

"""

# Signal phrases for heuristic memory extraction
DECISION_SIGNALS = [
    "decided to", "chose to", "using x instead",
    "approach:", "we will use", "going with",
]

PATTERN_SIGNALS = [
    "always ", "convention:", "structure:", "follow the",
    "pattern:", "established", "standard approach",
]

FAILURE_SIGNALS = [
    "failed", "broke ", "caused issues", "do not use",
    "avoid ", "instead use", "should not", "never use",
]


def ensure_memory_dir(project_dir: Path) -> Path:
    """
    Create .forge/memory/ directory if it does not exist.
    Initialize the three memory files with their headers if missing.
    Returns the memory directory path.
    """
    memory_dir = project_dir / ".forge" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    _init_file(memory_dir / "decisions.md", DECISIONS_HEADER)
    _init_file(memory_dir / "patterns.md", PATTERNS_HEADER)
    _init_file(memory_dir / "failures.md", FAILURES_HEADER)

    return memory_dir


def record_decision(project_dir: Path, title: str, decision: str,
                    rationale: str, phase_title: str = "",
                    task_title: str = "") -> None:
    """
    Append an architectural decision to decisions.md.
    Writes atomically: read existing, append, write-temp-then-rename.
    """
    date = datetime.utcnow().strftime("%Y-%m-%d")
    entry = f"\n## {title}\n"
    entry += f"**Decision:** {decision}\n"
    entry += f"**Rationale:** {rationale}\n"
    if phase_title or task_title:
        parts = []
        if phase_title:
            parts.append(f"**Phase:** {phase_title}")
        if task_title:
            parts.append(f"**Task:** {task_title}")
        parts.append(f"**Date:** {date}")
        entry += " | ".join(parts) + "\n"
    else:
        entry += f"**Date:** {date}\n"
    entry += "\n---\n"

    filepath = project_dir / ".forge" / "memory" / "decisions.md"
    ensure_memory_dir(project_dir)
    _atomic_append(filepath, entry)


def record_pattern(project_dir: Path, name: str,
                   description: str) -> None:
    """
    Append a code pattern to patterns.md.
    Only records patterns that are not already present.
    """
    filepath = project_dir / ".forge" / "memory" / "patterns.md"
    ensure_memory_dir(project_dir)

    existing = _read_memory_file(project_dir, "patterns.md")
    if name in existing:
        return

    entry = f"\n## {name}\n{description}\n\n---\n"
    _atomic_append(filepath, entry)


def record_failure(project_dir: Path, what_was_tried: str,
                   why_it_failed: str, what_to_do_instead: str,
                   phase_title: str = "") -> None:
    """
    Append a failed approach to failures.md.
    """
    date = datetime.utcnow().strftime("%Y-%m-%d")
    entry = f"\n## {what_was_tried[:80]}\n"
    entry += f"**What was tried:** {what_was_tried}\n"
    entry += f"**Why it failed:** {why_it_failed}\n"
    entry += f"**What to do instead:** {what_to_do_instead}\n"
    if phase_title:
        entry += f"**Phase:** {phase_title} | **Date:** {date}\n"
    else:
        entry += f"**Date:** {date}\n"
    entry += "\n---\n"

    filepath = project_dir / ".forge" / "memory" / "failures.md"
    ensure_memory_dir(project_dir)
    _atomic_append(filepath, entry)


def load_memory_context(project_dir: Path,
                        max_chars: int = 3000) -> str:
    """
    Load all memory files and return a condensed context string.

    Prioritizes: failures first (most critical), then decisions,
    then patterns. Truncates to max_chars total.
    Returns empty string if no memory files exist yet.
    """
    try:
        failures_raw = _read_memory_file(project_dir, "failures.md")
        decisions_raw = _read_memory_file(project_dir, "decisions.md")
        patterns_raw = _read_memory_file(project_dir, "patterns.md")

        failure_entries = _extract_entries(failures_raw)
        decision_entries = _extract_entries(decisions_raw)
        pattern_entries = _extract_entries(patterns_raw)

        # Nothing recorded yet
        if not failure_entries and not decision_entries and not pattern_entries:
            return ""

        sections = []

        if failure_entries:
            recent = failure_entries[-3:]  # most recent 3
            sections.append(
                "### Failed Approaches (Do Not Retry)\n" +
                "\n---\n".join(recent)
            )

        if decision_entries:
            recent = decision_entries[-5:]  # most recent 5
            sections.append(
                "### Architectural Decisions\n" +
                "\n---\n".join(recent)
            )

        if pattern_entries:
            recent = pattern_entries[-5:]  # most recent 5
            sections.append(
                "### Established Patterns\n" +
                "\n---\n".join(recent)
            )

        body = "\n\n".join(sections)

        # Truncate if needed
        if len(body) > max_chars:
            body = body[:max_chars].rsplit("\n", 1)[0]

        return f"## PROJECT MEMORY\n\n{body}"
    except Exception:
        return ""


def extract_memory_from_qa(qa_summary: str, task_title: str,
                            task_description: str) -> dict:
    """
    Parse QA summary text to extract memory-worthy items.

    Returns a dict with keys:
    - "decisions": list of (title, decision, rationale) tuples
    - "patterns": list of (name, description) tuples
    - "failures": list of (what, why, instead) tuples

    Uses simple heuristic detection only - no API call.
    Never raises.
    """
    try:
        result = {"decisions": [], "patterns": [], "failures": []}
        if not qa_summary:
            return result

        lower = qa_summary.lower()
        sentences = _split_sentences(qa_summary)

        for sentence in sentences:
            s_lower = sentence.lower()
            trimmed = sentence.strip()[:200]

            # Check for decision signals
            for signal in DECISION_SIGNALS:
                if signal in s_lower:
                    result["decisions"].append((
                        task_title,
                        trimmed,
                        f"Detected during: {task_title}",
                    ))
                    break

            # Check for pattern signals
            for signal in PATTERN_SIGNALS:
                if signal in s_lower:
                    result["patterns"].append((
                        task_title,
                        trimmed,
                    ))
                    break

            # Check for failure signals
            for signal in FAILURE_SIGNALS:
                if signal in s_lower:
                    result["failures"].append((
                        trimmed,
                        f"Detected during: {task_title}",
                        "See task notes for alternative approach",
                    ))
                    break

        return result
    except Exception:
        return {"decisions": [], "patterns": [], "failures": []}


def count_entries(project_dir: Path) -> dict:
    """
    Count entries in each memory file.

    Returns dict with keys 'decisions', 'patterns', 'failures'
    mapping to integer counts. Returns all zeros if files don't exist.
    """
    counts = {"decisions": 0, "patterns": 0, "failures": 0}
    for key, filename in [("decisions", "decisions.md"),
                          ("patterns", "patterns.md"),
                          ("failures", "failures.md")]:
        raw = _read_memory_file(project_dir, filename)
        entries = _extract_entries(raw)
        counts[key] = len(entries)
    return counts


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_memory_file(project_dir: Path, filename: str) -> str:
    """Read a memory file, returning empty string if not found."""
    filepath = project_dir / ".forge" / "memory" / filename
    if filepath.exists():
        return filepath.read_text(encoding="utf-8")
    return ""


def _atomic_append(filepath: Path, content: str) -> None:
    """
    Append content to a file.
    Uses append mode to avoid read-modify-write race conditions
    when multiple parallel tasks write memory simultaneously.
    """
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(content)


def _init_file(filepath: Path, header: str) -> None:
    """Create a memory file with header if it does not exist."""
    if not filepath.exists():
        filepath.write_text(header, encoding="utf-8")


def _extract_entries(raw: str) -> list:
    """
    Split a memory file into individual entries.
    Entries are separated by '---' lines. Strips headers.
    """
    if not raw:
        return []
    # Split on --- separators
    parts = re.split(r'\n---\n', raw)
    entries = []
    for part in parts:
        stripped = part.strip()
        # Skip the header (starts with # ) and empty parts
        if not stripped or stripped.startswith("# "):
            continue
        # Skip parts that are just "This file is maintained..."
        if stripped.startswith("This file is maintained"):
            continue
        entries.append(stripped)
    return entries


def _split_sentences(text: str) -> list:
    """Split text into sentences on period boundaries."""
    # Split on periods followed by space or end of string
    parts = re.split(r'\.(?:\s|$)', text)
    return [p.strip() for p in parts if p.strip()]
