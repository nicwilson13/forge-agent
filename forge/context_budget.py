"""
Context window budget manager for Forge.

Ensures task prompts stay within token limits by prioritizing
content and trimming lower-priority items when the budget is tight.

Uses a rough estimate of 4 characters per token.
This is conservative enough for budget management without
requiring the full tokenizer library.

This module has zero dependencies on other forge modules.
"""

import os
import sys
from dataclasses import dataclass

# Default token budget for task prompts
DEFAULT_BUDGET = 80_000

# Characters per token estimate (conservative)
CHARS_PER_TOKEN = 4

# Minimum characters to keep when truncating any section
# Never truncate below this - better to skip the section entirely
MIN_SECTION_CHARS = 200


def estimate_tokens(text: str) -> int:
    """Estimate token count from character length (4 chars per token)."""
    return max(1, len(text) // CHARS_PER_TOKEN)


def _truncate_at_word_boundary(text: str, max_chars: int) -> str:
    """Truncate text to max_chars, breaking at the last space if possible."""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_space = truncated.rfind(" ")
    if last_space > max_chars // 2:
        truncated = truncated[:last_space]
    return truncated


@dataclass
class ContentBlock:
    """A named block of content with a priority for budget allocation."""
    name: str           # human-readable name for logging
    content: str        # the actual text content
    priority: int       # 1 = highest (never cut), 9 = lowest (cut first)
    truncatable: bool   # False = never truncate this block
    min_chars: int = MIN_SECTION_CHARS  # minimum chars to keep if truncating


class ContextBudget:
    """
    Manages a fixed token budget across multiple content blocks.

    Usage:
        budget = ContextBudget(max_tokens=80_000)
        budget.add(ContentBlock("task", task_text, priority=1, truncatable=False))
        budget.add(ContentBlock("arch", arch_text, priority=3, truncatable=True))
        result = budget.allocate()
        # result is a dict of name -> allocated_content
    """

    def __init__(self, max_tokens: int = DEFAULT_BUDGET):
        self.max_tokens = max_tokens
        self._blocks: list[ContentBlock] = []

    def add(self, block: ContentBlock) -> None:
        """Add a content block to the budget."""
        self._blocks.append(block)

    def total_tokens(self) -> int:
        """Return total estimated tokens across all added blocks."""
        return sum(estimate_tokens(b.content) for b in self._blocks)

    def remaining_tokens(self) -> int:
        """Return remaining tokens in the budget."""
        return self.max_tokens - self.total_tokens()

    def allocate(self) -> dict[str, str]:
        """
        Allocate tokens across all blocks by priority.

        Algorithm:
        1. Sort blocks by priority (stable sort)
        2. First pass: allocate non-truncatable blocks in full
        3. Second pass: allocate truncatable blocks highest-priority first,
           truncating at word boundary when needed
        4. Skip block if remaining budget < min_chars worth of tokens
        5. Return dict of name -> final_content
        """
        sorted_blocks = sorted(self._blocks, key=lambda b: b.priority)
        allocations: dict[str, str] = {}
        used_tokens = 0

        # First pass: non-truncatable blocks always included in full
        for block in sorted_blocks:
            if not block.truncatable:
                allocations[block.name] = block.content
                used_tokens += estimate_tokens(block.content)

        # Second pass: truncatable blocks in priority order
        for block in sorted_blocks:
            if block.truncatable:
                remaining = self.max_tokens - used_tokens
                block_tokens = estimate_tokens(block.content)
                min_tokens = max(1, block.min_chars // CHARS_PER_TOKEN)

                if remaining < min_tokens:
                    # Not enough budget - skip this block
                    allocations[block.name] = ""
                elif block_tokens <= remaining:
                    # Fits entirely
                    allocations[block.name] = block.content
                    used_tokens += block_tokens
                else:
                    # Truncate to fit remaining budget
                    max_chars = remaining * CHARS_PER_TOKEN
                    truncated = _truncate_at_word_boundary(block.content, max_chars)
                    allocations[block.name] = truncated
                    used_tokens += estimate_tokens(truncated)

        self._log_usage(allocations)
        return allocations

    def _log_usage(self, allocations: dict[str, str]) -> None:
        """
        Print the context usage summary line.

        Only prints if stdout is a tty or FORGE_VERBOSE is set.
        """
        is_tty = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
        verbose = os.environ.get("FORGE_VERBOSE", "")
        if not is_tty and not verbose:
            return

        total = sum(estimate_tokens(v) for v in allocations.values())
        pct = (total * 100) // self.max_tokens if self.max_tokens > 0 else 0

        parts = []
        for name, content in allocations.items():
            tokens = estimate_tokens(content)
            if tokens > 1:
                parts.append(f"{name}: {tokens:,}")

        detail = "  ".join(parts)
        line = f"  [context] {total:,} / {self.max_tokens:,} tokens ({pct}%)  {detail}\n"
        sys.stdout.write(line)
        sys.stdout.flush()
