"""Tests for forge.memory module."""

import pytest
from pathlib import Path

from forge.memory import (
    ensure_memory_dir,
    record_decision,
    record_pattern,
    record_failure,
    load_memory_context,
    extract_memory_from_qa,
    count_entries,
)


# ---------------------------------------------------------------------------
# ensure_memory_dir
# ---------------------------------------------------------------------------

def test_ensure_memory_dir_creates_directory(tmp_path):
    """ensure_memory_dir creates .forge/memory/ directory."""
    memory_dir = ensure_memory_dir(tmp_path)
    assert memory_dir.exists()
    assert memory_dir == tmp_path / ".forge" / "memory"


def test_ensure_memory_dir_creates_header_files(tmp_path):
    """ensure_memory_dir creates all three memory files with headers."""
    memory_dir = ensure_memory_dir(tmp_path)
    files = sorted(f.name for f in memory_dir.glob("*.md"))
    assert files == ["decisions.md", "failures.md", "patterns.md"]

    decisions = (memory_dir / "decisions.md").read_text()
    assert "# Architectural Decisions" in decisions

    patterns = (memory_dir / "patterns.md").read_text()
    assert "# Code Patterns" in patterns

    failures = (memory_dir / "failures.md").read_text()
    assert "# Failed Approaches" in failures


def test_ensure_memory_dir_idempotent(tmp_path):
    """Running ensure_memory_dir twice does not duplicate headers."""
    ensure_memory_dir(tmp_path)
    content_before = (tmp_path / ".forge" / "memory" / "decisions.md").read_text()
    ensure_memory_dir(tmp_path)
    content_after = (tmp_path / ".forge" / "memory" / "decisions.md").read_text()
    assert content_before == content_after


# ---------------------------------------------------------------------------
# record_decision
# ---------------------------------------------------------------------------

def test_record_decision_creates_entry(tmp_path):
    """record_decision appends a formatted entry to decisions.md."""
    ensure_memory_dir(tmp_path)
    record_decision(tmp_path, "Auth approach", "Use Supabase Auth",
                    "Simpler than custom auth")
    content = (tmp_path / ".forge" / "memory" / "decisions.md").read_text()
    assert "## Auth approach" in content
    assert "Use Supabase Auth" in content


def test_record_decision_includes_all_fields(tmp_path):
    """Entry includes title, decision, rationale, phase, task."""
    ensure_memory_dir(tmp_path)
    record_decision(tmp_path, "State mgmt", "Use Zustand",
                    "Avoids Redux complexity",
                    phase_title="Phase 3", task_title="Build dashboard")
    content = (tmp_path / ".forge" / "memory" / "decisions.md").read_text()
    assert "## State mgmt" in content
    assert "**Decision:** Use Zustand" in content
    assert "**Rationale:** Avoids Redux complexity" in content
    assert "**Phase:** Phase 3" in content
    assert "**Task:** Build dashboard" in content
    assert "**Date:**" in content


# ---------------------------------------------------------------------------
# record_pattern
# ---------------------------------------------------------------------------

def test_record_pattern_creates_entry(tmp_path):
    """record_pattern appends a formatted entry to patterns.md."""
    ensure_memory_dir(tmp_path)
    record_pattern(tmp_path, "API route structure",
                   "All API routes follow /app/api/[resource]/route.ts")
    content = (tmp_path / ".forge" / "memory" / "patterns.md").read_text()
    assert "## API route structure" in content
    assert "/app/api/[resource]/route.ts" in content


def test_record_pattern_deduplicates(tmp_path):
    """record_pattern does not add a pattern already in the file."""
    ensure_memory_dir(tmp_path)
    record_pattern(tmp_path, "API route structure", "Description 1")
    record_pattern(tmp_path, "API route structure", "Description 2")
    content = (tmp_path / ".forge" / "memory" / "patterns.md").read_text()
    assert content.count("## API route structure") == 1


# ---------------------------------------------------------------------------
# record_failure
# ---------------------------------------------------------------------------

def test_record_failure_creates_entry(tmp_path):
    """record_failure appends a formatted entry to failures.md."""
    ensure_memory_dir(tmp_path)
    record_failure(tmp_path,
                   "Direct DB calls from client",
                   "Exposed service role key",
                   "Use API routes instead",
                   phase_title="Phase 2")
    content = (tmp_path / ".forge" / "memory" / "failures.md").read_text()
    assert "Direct DB calls from client" in content
    assert "Exposed service role key" in content
    assert "Use API routes instead" in content
    assert "Phase 2" in content


# ---------------------------------------------------------------------------
# load_memory_context
# ---------------------------------------------------------------------------

def test_load_memory_context_empty(tmp_path):
    """Returns empty string when no memory files exist."""
    result = load_memory_context(tmp_path)
    assert result == ""


def test_load_memory_context_with_content(tmp_path):
    """Returns formatted context string with all three sections."""
    ensure_memory_dir(tmp_path)
    record_decision(tmp_path, "Use React", "React for frontend",
                    "Industry standard")
    record_pattern(tmp_path, "Component style", "Named exports only")
    record_failure(tmp_path, "jQuery approach", "Too old",
                   "Use React instead")

    context = load_memory_context(tmp_path)
    assert "## PROJECT MEMORY" in context
    assert "Failed Approaches" in context
    assert "Architectural Decisions" in context
    assert "Established Patterns" in context


def test_load_memory_context_respects_max_chars(tmp_path):
    """Truncates to max_chars when content is large."""
    ensure_memory_dir(tmp_path)
    # Add many entries to exceed limit
    for i in range(20):
        record_decision(tmp_path, f"Decision {i}",
                        f"Long decision text {i} " * 20,
                        f"Rationale {i} " * 20)
    context = load_memory_context(tmp_path, max_chars=500)
    assert len(context) <= 600  # some slack for the header line


def test_load_memory_context_prioritizes_failures(tmp_path):
    """Failures section appears before decisions in returned string."""
    ensure_memory_dir(tmp_path)
    record_decision(tmp_path, "A decision", "Something", "Reason")
    record_failure(tmp_path, "A failure", "Bad reason", "Do this instead")

    context = load_memory_context(tmp_path)
    fail_pos = context.index("Failed Approaches")
    dec_pos = context.index("Architectural Decisions")
    assert fail_pos < dec_pos


# ---------------------------------------------------------------------------
# extract_memory_from_qa
# ---------------------------------------------------------------------------

def test_extract_memory_decision_signal():
    """Detects 'decided to' signal in QA summary."""
    qa = "The implementation decided to use PostgreSQL for persistence."
    result = extract_memory_from_qa(qa, "Setup DB", "Set up database")
    assert len(result["decisions"]) > 0
    assert "PostgreSQL" in result["decisions"][0][1]


def test_extract_memory_failure_signal():
    """Detects 'avoid' signal in QA summary."""
    qa = "Should avoid using global mutable state in handlers."
    result = extract_memory_from_qa(qa, "Fix handlers", "Fix request handlers")
    assert len(result["failures"]) > 0


def test_extract_memory_no_signal_returns_empty():
    """Returns empty lists when QA summary has no memory signals."""
    qa = "All tests pass. Code looks good. No issues found."
    result = extract_memory_from_qa(qa, "Simple task", "A simple task")
    assert result["decisions"] == []
    assert result["patterns"] == []
    assert result["failures"] == []


def test_extract_memory_never_raises():
    """extract_memory_from_qa never raises, even on bad input."""
    result = extract_memory_from_qa(None, "", "")
    assert result == {"decisions": [], "patterns": [], "failures": []}


# ---------------------------------------------------------------------------
# count_entries
# ---------------------------------------------------------------------------

def test_count_entries_empty(tmp_path):
    """Returns zeros when no memory files exist."""
    counts = count_entries(tmp_path)
    assert counts == {"decisions": 0, "patterns": 0, "failures": 0}


def test_count_entries_with_content(tmp_path):
    """Returns correct counts after recording entries."""
    ensure_memory_dir(tmp_path)
    record_decision(tmp_path, "D1", "dec", "rat")
    record_decision(tmp_path, "D2", "dec", "rat")
    record_pattern(tmp_path, "P1", "pattern")
    record_failure(tmp_path, "F1", "why", "instead")

    counts = count_entries(tmp_path)
    assert counts["decisions"] == 2
    assert counts["patterns"] == 1
    assert counts["failures"] == 1
