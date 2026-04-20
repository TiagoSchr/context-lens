"""
File priority ranking for adaptive budget allocation.

Scores files so the context builder allocates budget to the most relevant
files first, rather than using a greedy first-come-first-served strategy.
"""
from __future__ import annotations

import re
from typing import Any


def _is_test_path(path: str) -> bool:
    p = path.replace("\\", "/")
    return p.startswith("tests/") or "/test_" in p or p.startswith("test_")


def _is_entry_point(path: str) -> bool:
    """Heuristic: entry points / core modules deserve a boost."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    keywords = ("main", "app", "cli", "server", "core", "index", "router", "api")
    return any(kw in name.lower() for kw in keywords)


def _query_term_density(path: str, symbols: list[Any], query: str) -> float:
    """Fraction of symbols in file whose name appears in the query (normalised)."""
    query_lower = query.lower()
    query_words = set(re.findall(r"[a-z][a-z0-9_]{2,}", query_lower))
    if not query_words:
        return 0.0
    file_syms = [s for s in symbols if s["path"] == path]
    if not file_syms:
        return 0.0
    matches = sum(
        1 for s in file_syms
        if any(w in s["name"].lower() for w in query_words)
    )
    return matches / len(file_syms)


def rank_paths(
    paths: list[str],
    symbols: list[Any],
    query: str,
    *,
    test_penalty: float = 0.35,
    entry_boost: float = 1.15,
) -> list[str]:
    """
    Return paths ordered by descending relevance score.

    Score components (all in [0, 1] range before boosts):
      - symbol_count_score: normalised count of symbols in file
      - density_score: fraction of file's symbols matching query terms
      - entry_point_boost: 1.15x for files with entry-point names
      - test_penalty: 0.35x for test files
    """
    if not paths:
        return []

    # Build per-file symbol counts from provided symbols list
    from collections import Counter
    sym_counts: Counter = Counter(s["path"] for s in symbols)
    max_count = max(sym_counts.values(), default=1)

    scored: list[tuple[str, float]] = []
    for path in paths:
        count = sym_counts.get(path, 0)
        count_score = count / max_count
        density = _query_term_density(path, symbols, query)

        # Combine: density matters more than raw count
        score = 0.45 * density + 0.35 * count_score + 0.20

        if _is_entry_point(path):
            score *= entry_boost
        if _is_test_path(path):
            score *= test_penalty

        scored.append((path, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [p for p, _ in scored]
