"""Tests for forge/parallel.py."""

import asyncio
import os
import time

from forge.parallel import (
    ParallelExecutor,
    ParallelLocks,
    TaskResult,
    get_max_parallel,
    DEFAULT_MAX_PARALLEL,
)


def test_get_max_parallel_default():
    """Returns DEFAULT_MAX_PARALLEL when env var not set."""
    old = os.environ.pop("FORGE_MAX_PARALLEL", None)
    try:
        assert get_max_parallel() == DEFAULT_MAX_PARALLEL
    finally:
        if old is not None:
            os.environ["FORGE_MAX_PARALLEL"] = old


def test_get_max_parallel_from_env(monkeypatch):
    """Reads FORGE_MAX_PARALLEL env var."""
    monkeypatch.setenv("FORGE_MAX_PARALLEL", "5")
    assert get_max_parallel() == 5


def test_get_max_parallel_clamps_high(monkeypatch):
    """Values above 10 are clamped to 10."""
    monkeypatch.setenv("FORGE_MAX_PARALLEL", "99")
    assert get_max_parallel() == 10


def test_get_max_parallel_clamps_low(monkeypatch):
    """Values below 1 are clamped to 1."""
    monkeypatch.setenv("FORGE_MAX_PARALLEL", "0")
    assert get_max_parallel() == 1


def test_get_max_parallel_invalid_env(monkeypatch):
    """Non-integer env var falls back to default."""
    monkeypatch.setenv("FORGE_MAX_PARALLEL", "abc")
    assert get_max_parallel() == DEFAULT_MAX_PARALLEL


def test_parallel_locks_created():
    """ParallelLocks initializes all four locks."""
    locks = ParallelLocks()
    assert isinstance(locks.git, asyncio.Lock)
    assert isinstance(locks.state, asyncio.Lock)
    assert isinstance(locks.cost, asyncio.Lock)
    assert isinstance(locks.print, asyncio.Lock)


def test_task_result_fields():
    """TaskResult stores id, success, duration, error."""
    r = TaskResult(task_id="abc", success=True, duration=1.5)
    assert r.task_id == "abc"
    assert r.success is True
    assert r.duration == 1.5
    assert r.error == ""

    r2 = TaskResult(task_id="def", success=False, duration=0.5, error="boom")
    assert r2.error == "boom"


def test_parallel_executor_runs_all_tasks():
    """All tasks are executed when running in parallel."""
    async def _test():
        executor = ParallelExecutor(max_parallel=3)

        class FakeTask:
            def __init__(self, id):
                self.id = id

        async def fake_run(task, locks, **kwargs):
            await asyncio.sleep(0.01)
            return TaskResult(task.id, True, 0.01)

        tasks = [FakeTask(f"t_{i}") for i in range(6)]
        results = await executor.run_tasks(tasks, fake_run)
        assert len(results) == 6
        assert all(r.success for r in results)

    asyncio.run(_test())


def test_parallel_executor_respects_max_parallel():
    """No more than max_parallel tasks run simultaneously."""
    async def _test():
        executor = ParallelExecutor(max_parallel=2)
        concurrent_count = 0
        max_concurrent = 0

        class FakeTask:
            def __init__(self, id):
                self.id = id

        async def counting_run(task, locks, **kwargs):
            nonlocal concurrent_count, max_concurrent
            concurrent_count += 1
            max_concurrent = max(max_concurrent, concurrent_count)
            await asyncio.sleep(0.05)
            concurrent_count -= 1
            return TaskResult(task.id, True, 0.05)

        tasks = [FakeTask(f"t_{i}") for i in range(5)]
        results = await executor.run_tasks(tasks, counting_run)
        assert len(results) == 5
        assert max_concurrent <= 2

    asyncio.run(_test())


def test_parallel_executor_returns_results():
    """Results list matches number of tasks."""
    async def _test():
        executor = ParallelExecutor(max_parallel=3)

        class FakeTask:
            def __init__(self, id):
                self.id = id

        async def fake_run(task, locks, **kwargs):
            return TaskResult(task.id, True, 0.01)

        tasks = [FakeTask(f"t_{i}") for i in range(4)]
        results = await executor.run_tasks(tasks, fake_run)
        assert len(results) == 4
        ids = {r.task_id for r in results}
        assert ids == {"t_0", "t_1", "t_2", "t_3"}

    asyncio.run(_test())


def test_parallel_executor_handles_task_failure():
    """Failed tasks are recorded in results, do not crash executor."""
    async def _test():
        executor = ParallelExecutor(max_parallel=3)

        class FakeTask:
            def __init__(self, id):
                self.id = id

        async def mixed_run(task, locks, **kwargs):
            if task.id == "t_1":
                raise ValueError("intentional failure")
            return TaskResult(task.id, True, 0.01)

        tasks = [FakeTask(f"t_{i}") for i in range(3)]
        results = await executor.run_tasks(tasks, mixed_run)
        # t_1 raises exception, gather catches it with return_exceptions=True
        # So results should have 2 successful TaskResults
        successful = [r for r in results if isinstance(r, TaskResult) and r.success]
        assert len(successful) == 2

    asyncio.run(_test())


def test_locked_print_serializes(capsys):
    """locked_print acquires lock before printing."""
    async def _test():
        executor = ParallelExecutor(max_parallel=3)
        await executor.locked_print("hello from test")

    asyncio.run(_test())
    captured = capsys.readouterr()
    assert "hello from test" in captured.out
