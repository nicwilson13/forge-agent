"""
Git utilities for Forge.
Handles staging, committing, pushing, and reading history.
"""

import subprocess
from pathlib import Path
from typing import Optional, Tuple


def _run(cmd: list, cwd: Path) -> Tuple[int, str, str]:
    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def is_git_repo(project_dir: Path) -> bool:
    code, _, _ = _run(["git", "rev-parse", "--git-dir"], project_dir)
    return code == 0


def init_repo(project_dir: Path):
    _run(["git", "init"], project_dir)
    print("  [git] Initialized repo")


def has_remote(project_dir: Path) -> bool:
    code, out, _ = _run(["git", "remote"], project_dir)
    return code == 0 and bool(out.strip())


def stage_all(project_dir: Path):
    _run(["git", "add", "-A"], project_dir)


def commit(project_dir: Path, message: str) -> Optional[str]:
    """Stage all changes and commit. Returns commit hash or None if nothing to commit."""
    stage_all(project_dir)
    # Check if there's anything to commit
    code, out, _ = _run(["git", "status", "--porcelain"], project_dir)
    if not out.strip():
        print("  [git] Nothing to commit")
        return get_head_hash(project_dir)

    code, _, err = _run(["git", "commit", "-m", message], project_dir)
    if code != 0:
        # Try setting up git identity if missing
        _run(["git", "config", "user.email", "forge@autonomous.dev"], project_dir)
        _run(["git", "config", "user.name", "Forge Agent"], project_dir)
        code, _, err = _run(["git", "commit", "-m", message], project_dir)

    if code == 0:
        return get_head_hash(project_dir)
    else:
        print(f"  [git] Commit failed: {err}")
        return None


def push(project_dir: Path) -> bool:
    """Push to origin. Returns True on success."""
    if not has_remote(project_dir):
        print("  [git] No remote configured - skipping push")
        return False

    # Get current branch
    _, branch, _ = _run(["git", "branch", "--show-current"], project_dir)
    branch = branch.strip() or "main"

    code, _, err = _run(["git", "push", "origin", branch], project_dir)
    if code != 0:
        # Try --set-upstream first time
        code, _, err = _run(
            ["git", "push", "--set-upstream", "origin", branch], project_dir
        )
    if code == 0:
        print(f"  [git] Pushed to origin/{branch}")
        return True
    else:
        print(f"  [git] Push failed: {err}")
        return False


def commit_and_push(project_dir: Path, message: str) -> Optional[str]:
    """Convenience: commit then push. Returns commit hash."""
    hash_ = commit(project_dir, message)
    if hash_:
        push(project_dir)
    return hash_


def get_head_hash(project_dir: Path) -> Optional[str]:
    code, out, _ = _run(["git", "rev-parse", "--short", "HEAD"], project_dir)
    return out if code == 0 else None


def tag_phase(project_dir: Path, phase_title: str):
    """Create an annotated tag at phase completion."""
    tag = "phase-" + phase_title.lower().replace(" ", "-").replace(":", "")[:40]
    _run(["git", "tag", "-f", "-a", tag, "-m", f"Phase complete: {phase_title}"], project_dir)
    if has_remote(project_dir):
        _run(["git", "push", "origin", tag, "--force"], project_dir)
    print(f"  [git] Tagged: {tag}")


def get_tag_commit(project_dir: Path, tag_name: str) -> Optional[str]:
    """Return the short commit hash that a tag points to, or None if not found."""
    code, out, _ = _run(["git", "rev-parse", "--short", tag_name], project_dir)
    return out if code == 0 else None


def list_forge_tags(project_dir: Path) -> list:
    """
    Return all forge phase tags in the repo.
    Each dict: {"tag": "phase-1-...", "hash": "a3f9d12", "message": "..."}
    Returns empty list if no tags or not a git repo.
    """
    code, out, _ = _run(
        ["git", "tag", "-l", "phase-*", "--format=%(refname:short)\t%(objectname:short)\t%(subject)"],
        project_dir,
    )
    if code != 0 or not out.strip():
        return []
    tags = []
    for line in out.strip().split("\n"):
        parts = line.split("\t", 2)
        if len(parts) >= 2:
            tags.append({
                "tag": parts[0],
                "hash": parts[1],
                "message": parts[2] if len(parts) > 2 else "",
            })
    return tags


def force_push(project_dir: Path) -> bool:
    """
    Force push current branch to origin.
    WARNING: This is destructive and should only be called during rollback.
    Returns True on success, False on failure.
    """
    if not has_remote(project_dir):
        return False
    _, branch, _ = _run(["git", "branch", "--show-current"], project_dir)
    branch = branch.strip() or "main"
    code, _, err = _run(["git", "push", "origin", branch, "--force"], project_dir)
    if code != 0:
        print(f"  [git] Force push failed: {err}")
        return False
    print(f"  [git] Force pushed to origin/{branch}")
    return True


def recent_commits(project_dir: Path, n: int = 5) -> list:
    code, out, _ = _run(
        ["git", "log", f"-{n}", "--oneline"], project_dir
    )
    if code == 0 and out:
        return out.split("\n")
    return []


def get_diff(project_dir: Path, staged_only: bool = False) -> str:
    """
    Return the current git diff as a string.

    staged_only=False: git diff HEAD (all uncommitted changes)
    staged_only=True:  git diff --cached (staged changes only)
    Returns empty string on error or if no changes.
    """
    if staged_only:
        cmd = ["git", "diff", "--cached"]
    else:
        cmd = ["git", "diff", "HEAD"]
    code, out, _ = _run(cmd, project_dir)
    return out if code == 0 else ""


def count_diff_lines(diff: str) -> tuple[int, int]:
    """
    Count added and removed lines in a diff string.
    Returns (added, removed).
    Excludes diff headers (lines starting with +++, ---, @@, diff).
    """
    added = 0
    removed = 0
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return (added, removed)


def ensure_gitignore(project_dir: Path):
    """Add .forge/ to .gitignore if not already present."""
    gitignore = project_dir / ".gitignore"
    entry = ".forge/\n"
    if gitignore.exists():
        content = gitignore.read_text()
        if ".forge" not in content:
            with open(gitignore, "a") as f:
                f.write(entry)
    else:
        gitignore.write_text(entry)
