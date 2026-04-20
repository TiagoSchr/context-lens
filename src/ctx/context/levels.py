"""
Context level builders (level0 through level3).

level0 - project map (structure, deps, commands)
level1 - canonical signatures of all symbols
level2 - structural skeleton (sig + leading body lines)
level3 - raw source
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Any


# ─────────────────────────────────────────────────────────────── helpers

def _fmt_symbol(row: sqlite3.Row) -> str:
    """Render a symbol row as a compact canonical signature."""
    parts = [f"[{row['kind']}] {row['name']}"]
    if row["params"]:
        parts[0] += row["params"]
    if row["return_type"]:
        parts[0] += f" -> {row['return_type']}"
    if row["docstring"]:
        first_line = row["docstring"].split("\n")[0][:120]
        parts.append(f"  # {first_line}")
    parts.append(f"  @ {row['path']}:{row['start_line']}")
    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────── file index

def build_file_index(store: Any, max_symbols_per_file: int = 6) -> str:
    """Build a compact index of ALL indexed files with key symbol names + lines.

    This gives the model a complete map of every file in the project with
    the most important symbols and their line numbers, so it can jump directly
    to ``read_file(path, line)`` without needing ``grep_search`` or
    ``file_search`` first.

    Shows classes first, then functions/methods, up to *max_symbols_per_file*.
    """
    all_paths = store.list_indexed_paths()
    if not all_paths:
        return "=== FILE INDEX ===\n(no files indexed)"

    lines = [f"=== FILE INDEX ({len(all_paths)} files) ==="]
    for p in sorted(all_paths):
        syms = store.get_symbols_for_file(p)
        if not syms:
            lines.append(f"  {p}")
            continue
        # Prioritize: classes first, then functions, then methods
        priority = {"class": 0, "function": 1, "method": 2}
        ranked = sorted(syms, key=lambda s: (priority.get(s["kind"], 9), s["start_line"]))
        top = ranked[:max_symbols_per_file]
        sym_str = ", ".join(f"{s['name']}:{s['start_line']}" for s in top)
        extra = len(syms) - len(top)
        if extra > 0:
            sym_str += f" +{extra}"
        lines.append(f"  {p} | {sym_str}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────── level0

def build_level0(store: Any, root: Path) -> str:
    """Build project map: directory tree, deps, entry points."""
    lines = ["=== PROJECT MAP ==="]

    # From project_map table
    pm = store.get_all_project_map()
    if pm:
        for k, v in pm.items():
            lines.append(f"{k}: {v}")
    else:
        lines.append(f"root: {root}")
        # Derive from file system
        top_dirs = sorted(
            p.name for p in root.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        )[:20]
        if top_dirs:
            lines.append(f"dirs: {', '.join(top_dirs)}")

        # Try to read key project files
        for name in ("pyproject.toml", "package.json", "Cargo.toml", "go.mod", "setup.py"):
            f = root / name
            if f.exists():
                lines.append(f"project_file: {name}")
                try:
                    content = f.read_text(encoding="utf-8", errors="replace")
                    lines.append(content[:800])
                except OSError:
                    pass
                break

        # README first 15 lines
        for name in ("README.md", "README.rst", "README.txt", "readme.md"):
            f = root / name
            if f.exists():
                try:
                    readme_lines = f.read_text(encoding="utf-8", errors="replace").splitlines()[:15]
                    lines.append("--- README (first 15 lines) ---")
                    lines.extend(readme_lines)
                except OSError:
                    pass
                break

    # Stats from index
    stats = store.stats()
    lines.append(f"\nindex: {stats['files']} files, {stats['symbols']} symbols")
    if stats["by_language"]:
        lang_summary = ", ".join(f"{lang}({n})" for lang, n in list(stats["by_language"].items())[:6])
        lines.append(f"languages: {lang_summary}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────── level1

def build_level1(store: Any, symbols: list[sqlite3.Row] | None = None, limit: int = 200) -> str:
    """Build canonical signature list from symbols (level1)."""
    if symbols is None:
        symbols = store.get_all_symbols(limit=limit)
    if not symbols:
        return "=== SYMBOLS (level1) ===\n(no symbols indexed)"

    lines = ["=== SYMBOLS (level1) ==="]
    current_path = None
    for row in symbols:
        if row["path"] != current_path:
            current_path = row["path"]
            lines.append(f"\n--- {current_path} ---")
        lines.append(_fmt_symbol(row))

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────── level2

def _read_source(path: Path) -> list[str] | None:
    """Read file lines, returning None on error."""
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None


def build_level2(
    path: Path,
    symbols: list[sqlite3.Row],
    max_body_lines: int = 8,
    source_lines: list[str] | None = None,
) -> str:
    """Build structural skeleton: signature + first N body lines."""
    if source_lines is None:
        source_lines = _read_source(path)
    if source_lines is None:
        return f"=== {path} (unreadable) ==="

    lines = [f"=== {path} (level2 skeleton) ==="]
    for row in symbols:
        start = max(0, row["start_line"] - 1)
        end = min(row["end_line"] - 1, len(source_lines) - 1)
        body_end = min(start + max_body_lines, end)

        sig_lines = source_lines[start:body_end + 1]
        lines.append("")
        lines.extend(sig_lines)
        if body_end < end:
            lines.append("    ...")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────── level3

def build_level3(
    path: Path,
    max_lines: int = 300,
    source_lines: list[str] | None = None,
) -> str:
    """Return raw file source (level3), capped to max_lines."""
    if source_lines is None:
        source_lines = _read_source(path)
    if source_lines is None:
        return f"=== {path} (unreadable) ==="

    total = len(source_lines)
    capped = source_lines[:max_lines]
    lines = [f"=== {path} ({total} lines) ==="]
    lines.extend(capped)
    if total > max_lines:
        lines.append(f"... ({total - max_lines} more lines)")
    return "\n".join(lines)
