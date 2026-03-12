"""
Task dependency graph for Forge parallel execution.

Analyzes declared task dependencies and produces execution waves -
groups of tasks that can safely run in parallel because all their
prerequisites are complete.

Detects circular dependencies and falls back to sequential execution.

Imports only forge.state for Task/TaskStatus type hints.
"""

import warnings
from forge.state import Task, TaskStatus


def build_dependency_graph(tasks: list) -> dict[str, list[str]]:
    """
    Build an adjacency dict from task depends_on declarations.

    Returns: {task_id: [task_ids_this_depends_on]}
    Validates that all referenced task IDs exist in the task list.
    Replaces unknown dependency IDs with empty list (warns but continues).
    """
    task_ids = {t.id for t in tasks}
    graph: dict[str, list[str]] = {}
    for task in tasks:
        valid_deps = []
        for dep_id in getattr(task, "depends_on", []):
            if dep_id in task_ids:
                valid_deps.append(dep_id)
            else:
                warnings.warn(
                    f"Task '{task.id}' depends on unknown task '{dep_id}' — ignoring",
                    stacklevel=2,
                )
        graph[task.id] = valid_deps
    return graph


def detect_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    """
    Detect circular dependencies using iterative DFS.

    Returns the cycle as a list of task IDs if found, None if clean.
    Example: ['t_04', 't_05', 't_04'] if t_04 -> t_05 -> t_04.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in graph}
    parent: dict[str, str | None] = {node: None for node in graph}

    for start in graph:
        if color[start] != WHITE:
            continue
        stack = [start]
        while stack:
            node = stack[-1]
            if color[node] == WHITE:
                color[node] = GRAY
                for dep in graph.get(node, []):
                    if dep not in color:
                        continue
                    if color[dep] == GRAY:
                        # Found cycle — reconstruct it
                        cycle = [dep, node]
                        cur = node
                        while cur != dep:
                            cur = parent.get(cur)
                            if cur is None or cur == dep:
                                break
                            cycle.insert(1, cur)
                        cycle.append(dep)
                        return cycle
                    if color[dep] == WHITE:
                        parent[dep] = node
                        stack.append(dep)
            else:
                stack.pop()
                color[node] = BLACK

    return None


def compute_execution_waves(tasks: list) -> list[list]:
    """
    Compute ordered waves of tasks for parallel execution.

    Each wave contains tasks whose dependencies are all satisfied
    by tasks in previous waves.

    Returns list of lists of Task objects.
    Wave 0: tasks with no dependencies.
    Wave 1: tasks whose deps are all in Wave 0.
    Wave N: tasks whose deps are all in Waves 0..N-1.

    If a cycle is detected, returns [[task1, task2, ...]] (one wave,
    sequential order) and logs the cycle as a warning.

    If all tasks have empty depends_on, returns one wave with all tasks.
    """
    if not tasks:
        return []

    graph = build_dependency_graph(tasks)

    # Check for cycles
    cycle = detect_cycle(graph)
    if cycle is not None:
        warnings.warn(
            f"Circular dependency detected: {' -> '.join(cycle)}. "
            f"Falling back to sequential execution.",
            stacklevel=2,
        )
        return [list(tasks)]

    task_by_id = {t.id: t for t in tasks}
    assigned: set[str] = set()
    waves: list[list] = []

    remaining = set(task_by_id.keys())
    while remaining:
        wave = []
        for tid in list(remaining):
            deps = graph.get(tid, [])
            if all(d in assigned or d not in remaining for d in deps):
                wave.append(task_by_id[tid])
        if not wave:
            # Safety: shouldn't happen if cycle detection works,
            # but fall back to sequential
            wave = [task_by_id[tid] for tid in remaining]
            waves.append(wave)
            break
        waves.append(wave)
        for t in wave:
            assigned.add(t.id)
            remaining.discard(t.id)

    return waves


def get_ready_tasks(all_tasks: list, completed_ids: set[str]) -> list:
    """
    Return tasks that are ready to start given a set of completed IDs.

    A task is ready when:
    1. Its status is PENDING, INTERRUPTED, or FAILED
    2. All task IDs in its depends_on are in completed_ids
    """
    ready = []
    runnable_statuses = (TaskStatus.PENDING, TaskStatus.INTERRUPTED, TaskStatus.FAILED)
    for task in all_tasks:
        if task.status not in runnable_statuses:
            continue
        if all(dep_id in completed_ids for dep_id in getattr(task, "depends_on", [])):
            ready.append(task)
    return ready


def format_wave_plan(waves: list[list]) -> str:
    """
    Format the wave execution plan for terminal display.

    Example:
    Wave 1: t_01, t_02  (2 tasks, no deps)
    Wave 2: t_03, t_04  (2 tasks)
    Wave 3: t_05        (1 task)
    """
    if len(waves) <= 1:
        return ""

    lines = []
    for i, wave in enumerate(waves):
        ids = ", ".join(t.id for t in wave)
        count = len(wave)
        suffix = "task" if count == 1 else "tasks"
        extra = ", no deps" if i == 0 else ""
        lines.append(f"  Wave {i + 1}: {ids}  ({count} {suffix}{extra})")
    return "\n".join(lines)
