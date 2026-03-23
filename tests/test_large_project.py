"""Performance and scale tests with large projects."""
from __future__ import annotations
import time
import pytest
from pathlib import Path
from src.ctx.db.schema import init_db
from src.ctx.db.store import Store
from src.ctx.indexer.extractor import extract_symbols
from src.ctx.indexer.hasher import hash_file, hash_bytes
from src.ctx.indexer.walker import walk_project
from src.ctx.retrieval.search import search_symbols, find_related_paths
from src.ctx.context.builder import build_context
from src.ctx.context.budget import Budget, count_tokens


# ─────────────────────────────────────────────── helpers

def _make_large_project(tmp_path: Path, n_files: int = 50, funcs_per_file: int = 10) -> tuple[Store, Path]:
    """Create a large synthetic project and index it."""
    src = tmp_path / "src"
    src.mkdir()

    db_path = tmp_path / "large.db"
    conn = init_db(db_path)
    store = Store(conn)

    for i in range(n_files):
        module = src / f"module_{i:03d}.py"
        lines = [f'"""Module {i} docstring."""\n\n']
        for j in range(funcs_per_file):
            lines.append(
                f"def func_{i}_{j}(x: int, y: int = 0) -> int:\n"
                f'    """Function {j} in module {i}. Computes result."""\n'
                f"    return x + y + {j}\n\n"
            )
        module.write_text("".join(lines), encoding="utf-8")

        rel = f"src/module_{i:03d}.py"
        symbols, lang = extract_symbols(module)
        h = hash_file(module)
        fid = store.upsert_file(rel, h, lang, module.stat().st_size)
        if symbols:
            for s in symbols:
                s["file_id"] = fid
                s["path"] = rel
            store.insert_symbols_batch(symbols)

    store.commit()
    return store, tmp_path


# ─────────────────────────────────────────────── scale tests

class TestLargeProjectIndexing:
    def test_index_50_files(self, tmp_path):
        store, root = _make_large_project(tmp_path, n_files=50, funcs_per_file=5)
        s = store.stats()
        assert s["files"] == 50
        assert s["symbols"] >= 50 * 5

    def test_index_performance(self, tmp_path):
        start = time.perf_counter()
        store, root = _make_large_project(tmp_path, n_files=30, funcs_per_file=10)
        elapsed = time.perf_counter() - start
        # Should index 30 files with 10 functions each in under 30 seconds
        assert elapsed < 30.0, f"Indexing took too long: {elapsed:.2f}s"

    def test_all_symbols_indexed(self, tmp_path):
        store, root = _make_large_project(tmp_path, n_files=10, funcs_per_file=8)
        s = store.stats()
        assert s["symbols"] >= 10 * 8

    def test_list_indexed_paths_count(self, tmp_path):
        store, root = _make_large_project(tmp_path, n_files=20, funcs_per_file=3)
        paths = store.list_indexed_paths()
        assert len(paths) == 20

    def test_no_symbol_duplicates(self, tmp_path):
        store, root = _make_large_project(tmp_path, n_files=5, funcs_per_file=5)
        syms = store.get_all_symbols()
        # Check no duplicate (path, start_line) combos
        keys = [(s["path"], s["start_line"]) for s in syms]
        assert len(keys) == len(set(keys))


# ─────────────────────────────────────────────── search at scale

class TestLargeProjectSearch:
    def test_fts_search_performance(self, tmp_path):
        store, root = _make_large_project(tmp_path, n_files=20, funcs_per_file=10)
        start = time.perf_counter()
        results = search_symbols(store, "func_5_3")
        elapsed = time.perf_counter() - start
        assert elapsed < 5.0, f"FTS search took too long: {elapsed:.2f}s"

    def test_fts_search_returns_relevant_result(self, tmp_path):
        store, root = _make_large_project(tmp_path, n_files=10, funcs_per_file=5)
        results = search_symbols(store, "func_3_2")
        names = [r["name"] for r in results]
        assert "func_3_2" in names

    def test_find_related_paths_from_large_set(self, tmp_path):
        store, root = _make_large_project(tmp_path, n_files=10, funcs_per_file=5)
        # Search for functions in module_005
        results = search_symbols(store, "func_5", limit=30)
        paths = find_related_paths(store, results, max_paths=5)
        assert len(paths) <= 5
        assert isinstance(paths, list)

    def test_get_all_symbols_limit_respected(self, tmp_path):
        store, root = _make_large_project(tmp_path, n_files=10, funcs_per_file=10)
        syms = store.get_all_symbols(limit=50)
        assert len(syms) <= 50


# ─────────────────────────────────────────────── context builder at scale

class TestLargeProjectContext:
    def test_build_context_large_budget(self, tmp_path):
        store, root = _make_large_project(tmp_path, n_files=5, funcs_per_file=5)
        start = time.perf_counter()
        ctx, meta = build_context(
            store, root, "explain", "understand the module structure",
            budget=50000
        )
        elapsed = time.perf_counter() - start
        assert isinstance(ctx, str)
        assert elapsed < 10.0

    def test_build_context_small_budget(self, tmp_path):
        store, root = _make_large_project(tmp_path, n_files=5, funcs_per_file=5)
        ctx, meta = build_context(
            store, root, "navigate", "find functions",
            budget=500
        )
        assert isinstance(ctx, str)
        # Budget should be respected (some overflow allowed for header)
        assert meta["tokens_used"] <= meta["budget"] + 200

    def test_build_context_with_paths(self, tmp_path):
        store, root = _make_large_project(tmp_path, n_files=5, funcs_per_file=3)
        ctx, meta = build_context(
            store, root, "bugfix", "fix the bug in module_001",
            relevant_paths=["src/module_001.py"],
            budget=8000
        )
        assert meta["task"] == "bugfix"

    def test_budget_not_exceeded_by_much(self, tmp_path):
        store, root = _make_large_project(tmp_path, n_files=5, funcs_per_file=5)
        budget = 1000
        ctx, meta = build_context(store, root, "explain", "query", budget=budget)
        # budget available = int(1000 * 0.88) = 880
        # tokens_used should be ≤ available (consume returns False when over)
        assert meta["tokens_used"] <= meta["budget"]


# ─────────────────────────────────────────────── walker at scale

class TestLargeProjectWalker:
    def test_walk_large_tree(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        for i in range(10):
            sub = src / f"pkg_{i}"
            sub.mkdir()
            for j in range(5):
                (sub / f"module_{j}.py").write_text("def f(): pass\n")

        files = list(walk_project(tmp_path, [".py"], set(), max_size_kb=512))
        assert len(files) == 50

    def test_walk_with_multiple_ignored(self, tmp_path):
        src = tmp_path / "src"
        ignore1 = tmp_path / "node_modules"
        ignore2 = tmp_path / "__pycache__"
        src.mkdir(); ignore1.mkdir(); ignore2.mkdir()
        (src / "keep.py").write_text("pass")
        (ignore1 / "skip.py").write_text("pass")
        (ignore2 / "skip.py").write_text("pass")
        files = list(walk_project(tmp_path, [".py"], {"node_modules", "__pycache__"}, max_size_kb=512))
        names = [f.name for f in files]
        assert "keep.py" in names
        assert len([n for n in names if n == "skip.py"]) == 0

    def test_walk_performance(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        for i in range(100):
            (src / f"f{i}.py").write_text("x = 1\n")
        start = time.perf_counter()
        files = list(walk_project(tmp_path, [".py"], set(), max_size_kb=512))
        elapsed = time.perf_counter() - start
        assert len(files) == 100
        assert elapsed < 5.0


# ─────────────────────────────────────────────── budget at scale

class TestBudgetAtScale:
    def test_count_tokens_large_text(self):
        text = "word " * 10000
        result = count_tokens(text)
        assert result >= 1000  # at least 1000 tokens for 10000 words

    def test_budget_handles_many_consumes(self):
        b = Budget(100000)
        consumed = 0
        for _ in range(1000):
            if b.consume("short text"):
                consumed += 1
        assert consumed > 0
        assert b.utilization() > 0.0

    def test_budget_stops_when_full(self):
        b = Budget(100, buffer_ratio=0.0)
        # Fill budget
        results = []
        for _ in range(100):
            results.append(b.consume("word word word word"))
        # After some point, should return False
        assert False in results
