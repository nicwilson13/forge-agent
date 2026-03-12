"""Tests for forge/dependency_graph.py."""

import warnings

from forge.dependency_graph import (
    build_dependency_graph,
    detect_cycle,
    compute_execution_waves,
    get_ready_tasks,
    format_wave_plan,
)
from forge.state import Task, TaskStatus


def _make_task(id, deps=None, status=TaskStatus.PENDING):
    t = Task(id=id, title=id, description="", phase_id="p1", status=status)
    t.depends_on = deps or []
    return t


# -----------------------------------------------------------------------
# build_dependency_graph
# -----------------------------------------------------------------------

def test_build_graph_no_deps():
    """Tasks with no depends_on produce empty adjacency lists."""
    tasks = [_make_task("t_01"), _make_task("t_02")]
    graph = build_dependency_graph(tasks)
    assert graph == {"t_01": [], "t_02": []}


def test_build_graph_with_deps():
    """Tasks with depends_on produce correct adjacency."""
    tasks = [
        _make_task("t_01"),
        _make_task("t_02", ["t_01"]),
        _make_task("t_03", ["t_01", "t_02"]),
    ]
    graph = build_dependency_graph(tasks)
    assert graph["t_01"] == []
    assert graph["t_02"] == ["t_01"]
    assert set(graph["t_03"]) == {"t_01", "t_02"}


def test_build_graph_unknown_dep_ignored():
    """Unknown dependency ID is ignored without crashing."""
    tasks = [_make_task("t_01", ["nonexistent"])]
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        graph = build_dependency_graph(tasks)
        assert graph["t_01"] == []
        assert len(w) == 1
        assert "nonexistent" in str(w[0].message)


# -----------------------------------------------------------------------
# detect_cycle
# -----------------------------------------------------------------------

def test_detect_cycle_none_when_clean():
    """Returns None for a DAG with no cycles."""
    graph = {"t_01": [], "t_02": ["t_01"], "t_03": ["t_02"]}
    assert detect_cycle(graph) is None


def test_detect_cycle_finds_simple_cycle():
    """Detects t_01 -> t_02 -> t_01 cycle."""
    graph = {"t_01": ["t_02"], "t_02": ["t_01"]}
    cycle = detect_cycle(graph)
    assert cycle is not None
    # Cycle should contain both nodes
    assert "t_01" in cycle
    assert "t_02" in cycle


def test_detect_cycle_finds_longer_cycle():
    """Detects cycles involving 3+ nodes."""
    graph = {"t_01": ["t_03"], "t_02": ["t_01"], "t_03": ["t_02"]}
    cycle = detect_cycle(graph)
    assert cycle is not None
    assert len(cycle) >= 3


# -----------------------------------------------------------------------
# compute_execution_waves
# -----------------------------------------------------------------------

def test_compute_waves_no_deps():
    """All tasks in one wave when no dependencies."""
    tasks = [_make_task("t_01"), _make_task("t_02"), _make_task("t_03")]
    waves = compute_execution_waves(tasks)
    assert len(waves) == 1
    assert len(waves[0]) == 3


def test_compute_waves_linear_chain():
    """t_01 -> t_02 -> t_03 produces three waves of one."""
    tasks = [
        _make_task("t_01"),
        _make_task("t_02", ["t_01"]),
        _make_task("t_03", ["t_02"]),
    ]
    waves = compute_execution_waves(tasks)
    assert len(waves) == 3
    assert waves[0][0].id == "t_01"
    assert waves[1][0].id == "t_02"
    assert waves[2][0].id == "t_03"


def test_compute_waves_diamond():
    """t_01 -> (t_02, t_03) -> t_04 produces three waves."""
    tasks = [
        _make_task("t_01"),
        _make_task("t_02", ["t_01"]),
        _make_task("t_03", ["t_01"]),
        _make_task("t_04", ["t_02", "t_03"]),
    ]
    waves = compute_execution_waves(tasks)
    assert len(waves) == 3
    assert {t.id for t in waves[0]} == {"t_01"}
    assert {t.id for t in waves[1]} == {"t_02", "t_03"}
    assert {t.id for t in waves[2]} == {"t_04"}


def test_compute_waves_cycle_falls_back():
    """Cycle detected: returns single wave (sequential fallback)."""
    tasks = [
        _make_task("t_01", ["t_02"]),
        _make_task("t_02", ["t_01"]),
    ]
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        waves = compute_execution_waves(tasks)
    assert len(waves) == 1
    assert len(waves[0]) == 2


# -----------------------------------------------------------------------
# get_ready_tasks
# -----------------------------------------------------------------------

def test_get_ready_tasks_no_deps():
    """Tasks with empty depends_on always ready."""
    tasks = [_make_task("t_01"), _make_task("t_02")]
    ready = get_ready_tasks(tasks, set())
    assert len(ready) == 2


def test_get_ready_tasks_dep_not_done():
    """Task with unfinished dep not in ready list."""
    tasks = [
        _make_task("t_01"),
        _make_task("t_02", ["t_01"]),
    ]
    ready = get_ready_tasks(tasks, set())
    assert len(ready) == 1
    assert ready[0].id == "t_01"


def test_get_ready_tasks_dep_done():
    """Task with all deps in completed_ids is ready."""
    tasks = [
        _make_task("t_01", status=TaskStatus.DONE),
        _make_task("t_02", ["t_01"]),
    ]
    ready = get_ready_tasks(tasks, {"t_01"})
    # t_01 is DONE so not in ready, t_02 has dep satisfied
    assert len(ready) == 1
    assert ready[0].id == "t_02"


# -----------------------------------------------------------------------
# format_wave_plan
# -----------------------------------------------------------------------

def test_format_wave_plan_single_wave():
    """Single wave shows no wave numbers."""
    tasks = [_make_task("t_01"), _make_task("t_02")]
    waves = [tasks]
    result = format_wave_plan(waves)
    assert result == ""


def test_format_wave_plan_multiple_waves():
    """Multiple waves display with wave numbers."""
    wave1 = [_make_task("t_01"), _make_task("t_02")]
    wave2 = [_make_task("t_03")]
    result = format_wave_plan([wave1, wave2])
    assert "Wave 1" in result
    assert "Wave 2" in result
    assert "t_01" in result
    assert "t_03" in result
    assert "2 tasks" in result
    assert "1 task" in result
    assert "no deps" in result
