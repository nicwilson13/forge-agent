"""
State manager for Forge.
All build state lives in .forge/state.json inside the project directory.
"""

import dataclasses
import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Optional


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    PARKED = "parked"           # moved to NEEDS_HUMAN
    INTERRUPTED = "interrupted"        # was IN_PROGRESS when Forge stopped
    COMMIT_PENDING = "commit_pending"  # task done, commit not yet pushed
    WAITING = "waiting"                # has unmet dependencies, cannot start yet


class PhaseStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    QA_FAILED = "qa_failed"


@dataclass
class Task:
    id: str
    title: str
    description: str
    phase_id: str
    status: TaskStatus = TaskStatus.PENDING
    retry_count: int = 0
    notes: str = ""               # QA / failure notes
    park_reason: str = ""         # why it was moved to NEEDS_HUMAN
    checkpoint_at: Optional[str] = None      # ISO timestamp of last checkpoint write
    interrupt_reason: str = ""               # "ctrl_c", "crash", "timeout", etc.
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    completed_at: Optional[str] = None
    commit_hash: Optional[str] = None
    last_model: str = ""
    parallel_group: int = 0   # 0 = sequential, N = can run with same group
    depends_on: list[str] = field(default_factory=list)

    @staticmethod
    def new(title: str, description: str, phase_id: str) -> "Task":
        return Task(id=str(uuid.uuid4())[:8], title=title,
                    description=description, phase_id=phase_id)


@dataclass
class Phase:
    id: str
    title: str
    description: str
    status: PhaseStatus = PhaseStatus.PENDING
    tasks: List[Task] = field(default_factory=list)
    qa_notes: str = ""
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    completed_at: Optional[str] = None
    github_milestone: Optional[int] = None
    github_pr: Optional[int] = None
    vercel_deployment_url: str = ""
    vercel_deployment_status: str = ""

    @staticmethod
    def new(title: str, description: str) -> "Phase":
        return Phase(id=str(uuid.uuid4())[:8], title=title, description=description)

    def next_task(self) -> Optional[Task]:
        for t in self.tasks:
            if t.status in (TaskStatus.PENDING, TaskStatus.FAILED):
                return t
        return None

    def all_done(self) -> bool:
        return all(
            t.status in (TaskStatus.DONE, TaskStatus.PARKED)
            for t in self.tasks
        )


@dataclass
class ForgeState:
    project_name: str = ""
    phases: List[Phase] = field(default_factory=list)
    current_phase_index: int = 0
    tasks_completed: int = 0
    tasks_since_checkin: int = 0
    initialized: bool = False
    architecture_written: bool = False
    started_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    last_updated: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    @property
    def current_phase(self) -> Optional[Phase]:
        if self.current_phase_index < len(self.phases):
            return self.phases[self.current_phase_index]
        return None

    def advance_phase(self):
        self.current_phase_index += 1

    def is_complete(self) -> bool:
        return self.current_phase_index >= len(self.phases)

    def all_parked_tasks(self) -> List[Task]:
        result = []
        for phase in self.phases:
            for task in phase.tasks:
                if task.status == TaskStatus.PARKED:
                    result.append(task)
        return result

    def find_task(self, task_id: str) -> Optional[Task]:
        for phase in self.phases:
            for task in phase.tasks:
                if task.id == task_id:
                    return task
        return None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _state_path(project_dir: Path) -> Path:
    return project_dir / ".forge" / "state.json"


def _filter_fields(cls, data: dict) -> dict:
    """Filter dict to only keys that are valid dataclass fields for cls."""
    valid = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in data.items() if k in valid}


def _backup_path(project_dir: Path) -> Path:
    return project_dir / ".forge" / "state.json.bak"


def _try_load_from_file(path: Path) -> Optional[ForgeState]:
    """Try to load state from a specific file. Returns None on failure."""
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, ValueError, OSError):
        return None

    phases = []
    for p_raw in raw.get("phases", []):
        tasks = [Task(**_filter_fields(Task, t)) for t in p_raw.pop("tasks", [])]
        phase = Phase(**_filter_fields(Phase, p_raw))
        phase.tasks = tasks
        phases.append(phase)

    state = ForgeState(**_filter_fields(ForgeState, {k: v for k, v in raw.items() if k != "phases"}))
    state.phases = phases
    return state


def load_state(project_dir: Path) -> ForgeState:
    path = _state_path(project_dir)

    # Try primary state file
    state = _try_load_from_file(path)
    if state is not None:
        return state

    # Try backup
    backup = _backup_path(project_dir)
    if backup.exists():
        print("  [state] WARNING: state.json missing or corrupted, recovering from backup")
        state = _try_load_from_file(backup)
        if state is not None:
            # Restore the backup as the primary
            try:
                import shutil
                shutil.copy2(backup, path)
            except OSError:
                pass
            return state
        print("  [state] WARNING: Backup also corrupted")

    return ForgeState()


def save_state(project_dir: Path, state: ForgeState):
    state.last_updated = datetime.utcnow().isoformat()
    path = _state_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Back up current state.json before overwriting
    if path.exists():
        try:
            import shutil
            shutil.copy2(path, path.with_suffix(".json.bak"))
        except OSError:
            pass

    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(state), f, indent=2, default=str)
