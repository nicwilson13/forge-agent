"""
Tests for forge.context_budget module.
All tests use small token budgets for fast execution.
"""

import os

import pytest

from forge.context_budget import (
    ContextBudget,
    ContentBlock,
    estimate_tokens,
    CHARS_PER_TOKEN,
    MIN_SECTION_CHARS,
)


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------

def test_estimate_tokens_basic():
    """400 chars estimates to 100 tokens."""
    assert estimate_tokens("a" * 400) == 100


def test_estimate_tokens_empty():
    """Empty string returns 1 (never 0)."""
    assert estimate_tokens("") == 1


# ---------------------------------------------------------------------------
# ContentBlock
# ---------------------------------------------------------------------------

def test_content_block_creation():
    """ContentBlock stores all fields correctly."""
    block = ContentBlock(
        name="arch", content="hello", priority=3,
        truncatable=True, min_chars=100,
    )
    assert block.name == "arch"
    assert block.content == "hello"
    assert block.priority == 3
    assert block.truncatable is True
    assert block.min_chars == 100


# ---------------------------------------------------------------------------
# ContextBudget.allocate
# ---------------------------------------------------------------------------

def test_allocate_within_budget():
    """All blocks fit - returned content matches input."""
    budget = ContextBudget(max_tokens=10_000)
    budget.add(ContentBlock("a", "short text", priority=1, truncatable=False))
    budget.add(ContentBlock("b", "also short", priority=3, truncatable=True))
    result = budget.allocate()
    assert result["a"] == "short text"
    assert result["b"] == "also short"


def test_allocate_non_truncatable_always_included():
    """Non-truncatable blocks are always in full even if tight."""
    # Budget is tiny but non-truncatable block must still be included
    big_text = "x" * 800  # 200 tokens
    budget = ContextBudget(max_tokens=50)  # way too small
    budget.add(ContentBlock("sacred", big_text, priority=1, truncatable=False))
    result = budget.allocate()
    assert result["sacred"] == big_text


def test_allocate_truncates_low_priority_first():
    """Lower priority blocks are truncated before higher priority."""
    budget = ContextBudget(max_tokens=200)
    # High priority gets ~400 chars = 100 tokens
    budget.add(ContentBlock("high", "a " * 200, priority=1, truncatable=True))
    # Low priority should be truncated
    budget.add(ContentBlock("low", "b " * 400, priority=9, truncatable=True))
    result = budget.allocate()
    assert len(result["high"]) == len("a " * 200)  # full
    assert len(result["low"]) < len("b " * 400)    # truncated


def test_allocate_skips_block_when_no_budget():
    """Block is skipped (empty string) when remaining budget < MIN."""
    budget = ContextBudget(max_tokens=100)
    # Non-truncatable block uses almost all budget
    budget.add(ContentBlock("big", "x" * 400, priority=1, truncatable=False))
    # Truncatable block has no room
    budget.add(ContentBlock("small", "y" * 400, priority=5, truncatable=True))
    result = budget.allocate()
    assert result["small"] == ""


def test_allocate_returns_all_block_names():
    """Returned dict has a key for every added block."""
    budget = ContextBudget(max_tokens=10_000)
    budget.add(ContentBlock("a", "text", priority=1, truncatable=False))
    budget.add(ContentBlock("b", "text", priority=3, truncatable=True))
    budget.add(ContentBlock("c", "text", priority=5, truncatable=True))
    result = budget.allocate()
    assert set(result.keys()) == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# total_tokens / remaining_tokens
# ---------------------------------------------------------------------------

def test_total_tokens_sums_all_blocks():
    """total_tokens() returns sum across all blocks."""
    budget = ContextBudget(max_tokens=10_000)
    budget.add(ContentBlock("a", "x" * 400, priority=1, truncatable=False))
    budget.add(ContentBlock("b", "y" * 800, priority=3, truncatable=True))
    assert budget.total_tokens() == 300  # 100 + 200


def test_remaining_tokens_before_allocate():
    """remaining_tokens() returns budget minus current block total."""
    budget = ContextBudget(max_tokens=1000)
    budget.add(ContentBlock("a", "x" * 400, priority=1, truncatable=False))
    assert budget.remaining_tokens() == 900  # 1000 - 100


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def test_budget_log_line_printed(capsys, monkeypatch):
    """allocate() prints a [context] log line to stdout when verbose."""
    monkeypatch.setenv("FORGE_VERBOSE", "1")
    budget = ContextBudget(max_tokens=10_000)
    budget.add(ContentBlock("task", "do something", priority=1, truncatable=False))
    budget.allocate()
    captured = capsys.readouterr()
    assert "[context]" in captured.out
    assert "10,000" in captured.out


def test_budget_log_includes_block_names(capsys, monkeypatch):
    """Log line includes names of non-empty allocated blocks."""
    monkeypatch.setenv("FORGE_VERBOSE", "1")
    budget = ContextBudget(max_tokens=10_000)
    budget.add(ContentBlock("arch", "architecture content here", priority=3, truncatable=True))
    budget.add(ContentBlock("vision", "project vision here", priority=6, truncatable=True))
    budget.allocate()
    captured = capsys.readouterr()
    assert "arch:" in captured.out
    assert "vision:" in captured.out
