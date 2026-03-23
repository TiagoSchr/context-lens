"""File system walker with ignore rules."""
from __future__ import annotations
from pathlib import Path
from typing import Iterator


def walk_project(
    root: Path,
    extensions: list[str],
    ignore_dirs: set[str],
    max_size_kb: int = 512,
) -> Iterator[Path]:
    """Yield files matching extensions, skipping ignored dirs and large files.

    Uses manual recursion to prune ignored directories early instead of
    loading the full tree with rglob first.
    """
    ext_set = set(extensions)
    max_bytes = max_size_kb * 1024
    yield from _walk(root, ext_set, ignore_dirs, max_bytes)


def _walk(directory: Path, ext_set: set[str], ignore_dirs: set[str], max_bytes: int) -> Iterator[Path]:
    try:
        entries = list(directory.iterdir())
    except PermissionError:
        return

    for entry in entries:
        if entry.name.startswith("."):
            continue
        if entry.is_symlink():
            continue
        if entry.is_dir():
            if entry.name not in ignore_dirs:
                yield from _walk(entry, ext_set, ignore_dirs, max_bytes)
        elif entry.is_file() and entry.suffix in ext_set:
            try:
                if entry.stat().st_size <= max_bytes:
                    yield entry
            except OSError:
                continue
