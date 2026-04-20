"""
Git integration for Context Lens v2.

Provides diff-aware context: prioritise recently changed files when
building context for bugfix / refactor queries.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _run(args: list[str], cwd: Path) -> str | None:
    """Run a git command, return stdout or None on error."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def is_git_repo(root: Path) -> bool:
    return _run(["rev-parse", "--git-dir"], root) is not None


def get_changed_files(root: Path, base: str = "HEAD") -> list[str]:
    """
    Return paths of files changed since *base* (relative, forward slashes).

    Priority order:
      1. Staged + unstaged changes vs HEAD (working tree vs last commit)
      2. Files changed in the last commit (if working tree is clean)
    """
    paths: list[str] = []

    # Staged (index vs HEAD)
    staged = _run(["diff", "--name-only", "--cached", base], root)
    if staged:
        paths.extend(staged.splitlines())

    # Unstaged (working tree vs index)
    unstaged = _run(["diff", "--name-only"], root)
    if unstaged:
        paths.extend(unstaged.splitlines())

    # If nothing changed in working tree, return last commit's files
    if not paths:
        last_commit = _run(["diff", "--name-only", "HEAD~1", "HEAD"], root)
        if last_commit:
            paths.extend(last_commit.splitlines())

    # Normalise and deduplicate
    seen: set[str] = set()
    result: list[str] = []
    for p in paths:
        norm = p.replace("\\", "/")
        if norm not in seen:
            seen.add(norm)
            result.append(norm)
    return result


def get_branch_changed_files(root: Path, branch: str) -> list[str]:
    """Files changed in *branch* relative to its merge-base with main/master."""
    # Find merge base
    for base in ("main", "master", "origin/main", "origin/master"):
        merge_base = _run(["merge-base", base, branch], root)
        if merge_base:
            diff = _run(["diff", "--name-only", merge_base, branch], root)
            if diff:
                return [p.replace("\\", "/") for p in diff.splitlines()]
    # Fallback: all files in branch vs HEAD
    diff = _run(["diff", "--name-only", "HEAD", branch], root)
    if diff:
        return [p.replace("\\", "/") for p in diff.splitlines()]
    return []


def current_branch(root: Path) -> str | None:
    return _run(["rev-parse", "--abbrev-ref", "HEAD"], root)


def last_commit_message(root: Path) -> str | None:
    return _run(["log", "-1", "--pretty=%s"], root)
