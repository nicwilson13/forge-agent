"""
Parallel task execution for Forge.

Runs independent tasks concurrently using asyncio, up to max_parallel
simultaneous executions. Serializes git commits and shared state writes
using asyncio locks.

Default max_parallel: 3 (configurable via FORGE_MAX_PARALLEL env var)

Tasks are only run in parallel when they have no declared dependencies
on each other. The dependency graph is managed by Improvement 19.
This module only provides the execution infrastructure.
"""

import asyncio
import os
from dataclasses import dataclass, field
from typing import Callable, Awaitable


DEFAULT_MAX_PARALLEL = 3


def get_max_parallel() -> int:
    """
    Return the configured max parallel task count.

    Reads FORGE_MAX_PARALLEL env var, falls back to DEFAULT_MAX_PARALLEL.
    Clamps to range [1, 10].
    """
    try:
        val = int(os.environ.get("FORGE_MAX_PARALLEL", DEFAULT_MAX_PARALLEL))
        return max(1, min(10, val))
    except (ValueError, TypeError):
        return DEFAULT_MAX_PARALLEL


@dataclass
class TaskResult:
    """Result of a single parallel task execution."""
    task_id: str
    success: bool
    duration: float
    error: str = ""


@dataclass
class ParallelLocks:
    """
    Shared asyncio locks for resources that cannot be accessed in parallel.

    One instance shared across all concurrent task executions.
    """
    git: asyncio.Lock = field(default_factory=asyncio.Lock)
    state: asyncio.Lock = field(default_factory=asyncio.Lock)
    cost: asyncio.Lock = field(default_factory=asyncio.Lock)
    print: asyncio.Lock = field(default_factory=asyncio.Lock)


class ParallelExecutor:
    """
    Executes tasks concurrently up to max_parallel simultaneous tasks.

    Uses asyncio.Semaphore to limit concurrency.
    Uses ParallelLocks to serialize shared resource access.
    """

    def __init__(self, max_parallel: int | None = None):
        self.max_parallel = max_parallel or get_max_parallel()
        self.locks = ParallelLocks()
        self._semaphore: asyncio.Semaphore | None = None

    async def run_tasks(
        self,
        tasks: list,
        task_func: Callable[..., Awaitable[TaskResult]],
        **shared_kwargs,
    ) -> list[TaskResult]:
        """
        Run all tasks concurrently, up to max_parallel at once.

        tasks: list of Task objects to execute
        task_func: async function with signature:
                   (task, locks, **shared_kwargs) -> TaskResult
        shared_kwargs: passed to every task_func call

        Returns list of TaskResult in completion order.
        """
        self._semaphore = asyncio.Semaphore(self.max_parallel)
        results: list[TaskResult] = []
        result_lock = asyncio.Lock()

        async def run_one(task):
            async with self._semaphore:
                result = await task_func(
                    task, self.locks, **shared_kwargs
                )
                async with result_lock:
                    results.append(result)
            return result

        await asyncio.gather(*[run_one(t) for t in tasks],
                             return_exceptions=True)
        return results

    async def locked_print(self, message: str) -> None:
        """Print a message with the print lock held."""
        async with self.locks.print:
            print(message)

    async def locked_git_commit(self, commit_func: Callable,
                                *args, **kwargs):
        """Execute a git commit with the git lock held."""
        async with self.locks.git:
            return commit_func(*args, **kwargs)

    async def locked_state_save(self, save_func: Callable,
                                *args, **kwargs):
        """Save state with the state lock held."""
        async with self.locks.state:
            return save_func(*args, **kwargs)
