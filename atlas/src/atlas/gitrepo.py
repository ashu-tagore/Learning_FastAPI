"""Read-only Git operations on the worker's local clones of tracked repositories.

Atlas never writes to GitHub: these functions only clone, fetch and read.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Changes:
    """Files that differ between two commits, as paths relative to the repository root."""

    changed: list[str]  # added or modified: (re)index these
    deleted: list[str]  # gone: remove these from the index


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def clone_or_update(url: str, path: Path) -> str:
    """Make path a copy of url's default branch at its newest commit; return that commit."""
    is_cloned = (path / ".git").exists()
    if is_cloned:
        git("fetch", "--quiet", "origin", cwd=path)
        # The clone is only ever read, so throwing away local state is safe.
        git("reset", "--quiet", "--hard", "origin/HEAD", cwd=path)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        # blob:none: full history of commits and trees, file contents fetched only when
        # needed. Diffing two commits by file name needs history but no contents.
        git("clone", "--quiet", "--filter=blob:none", url, str(path), cwd=path.parent)
    return git("rev-parse", "HEAD", cwd=path)


def changes_between(path: Path, old_commit: str, new_commit: str) -> Changes:
    # --no-renames: a renamed file is reported as deleted plus added, which is exactly
    # what re-indexing needs (the old path's chunks go, the new path's come in).
    output = git("diff", "--name-status", "--no-renames", old_commit, new_commit, cwd=path)
    changed: list[str] = []
    deleted: list[str] = []
    for line in output.splitlines():
        status, file_path = line.split("\t", 1)
        if status == "D":
            deleted.append(file_path)
        else:
            changed.append(file_path)
    return Changes(changed=changed, deleted=deleted)


def has_commit(path: Path, commit: str) -> bool:
    """False if the clone doesn't know the commit, for example after a force-push upstream."""
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=path, capture_output=True
    )
    return result.returncode == 0
