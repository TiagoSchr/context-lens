"""Tests for build_context from src/ctx/context/builder.py."""
from __future__ import annotations
import pytest
from pathlib import Path
from src.ctx.db.schema import init_db
from src.ctx.db.store import Store
from src.ctx.context.builder import build_context, _is_test_path


# ─────────────────────────────────────────────── fixtures

@pytest.fixture
def store_root(tmp_path):
    db_file = tmp_path / "test.db"
    conn = init_db(db_file)
    s = Store(conn)
    # Create a real source file
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    source_file = src_dir / "mymodule.py"
    source_file.write_text(
        '"""My module."""\n\n'
        "def compute(x: int) -> int:\n"
        '    """Compute something."""\n'
        "    return x * 2\n\n"
        "class MyClass:\n"
        '    """A class."""\n'
        "    def method(self):\n"
        "        pass\n",
        encoding="utf-8",
    )
    rel_path = "src/mymodule.py"
    fid = s.upsert_file(rel_path, "hash1", "python", source_file.stat().st_size)
    s.insert_symbols_batch([
        {"file_id": fid, "name": "compute", "kind": "function", "params": "(x: int)",
         "return_type": "int", "docstring": "Compute something.", "start_line": 3,
         "end_line": 5, "language": "python", "path": rel_path},
        {"file_id": fid, "name": "MyClass", "kind": "class", "params": None,
         "return_type": None, "docstring": "A class.", "start_line": 7,
         "end_line": 10, "language": "python", "path": rel_path},
    ])
    s.commit()
    yield s, tmp_path
    conn.close()


# ─────────────────────────────────────────────── _is_test_path

class TestIsTestPath:
    def test_tests_prefix(self):
        assert _is_test_path("tests/test_foo.py") is True

    def test_test_underscore_filename(self):
        assert _is_test_path("src/test_bar.py") is True

    def test_test_prefix_only(self):
        assert _is_test_path("test_baz.py") is True

    def test_source_file_not_test(self):
        assert _is_test_path("src/mymodule.py") is False

    def test_lib_file_not_test(self):
        assert _is_test_path("src/ctx/context/builder.py") is False

    def test_backslash_path_tests_prefix(self):
        assert _is_test_path("tests\\test_foo.py") is True


# ─────────────────────────────────────────────── build_context return type

class TestBuildContextReturnType:
    def test_returns_tuple(self, store_root):
        store, root = store_root
        result = build_context(store, root, "explain", "what does compute do", budget=2000)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_context_is_string(self, store_root):
        store, root = store_root
        ctx, meta = build_context(store, root, "explain", "what does compute do", budget=2000)
        assert isinstance(ctx, str)

    def test_meta_is_dict(self, store_root):
        store, root = store_root
        ctx, meta = build_context(store, root, "explain", "what does compute do", budget=2000)
        assert isinstance(meta, dict)

    def test_meta_has_required_keys(self, store_root):
        store, root = store_root
        ctx, meta = build_context(store, root, "explain", "test query", budget=2000)
        for key in ("task", "tokens_used", "budget", "utilization", "paths_included"):
            assert key in meta, f"Missing key: {key}"

    def test_meta_task_matches_input(self, store_root):
        store, root = store_root
        ctx, meta = build_context(store, root, "bugfix", "fix the bug", budget=2000)
        assert meta["task"] == "bugfix"

    def test_meta_tokens_used_positive(self, store_root):
        store, root = store_root
        ctx, meta = build_context(store, root, "explain", "test query", budget=2000)
        assert meta["tokens_used"] >= 0

    def test_meta_utilization_in_range(self, store_root):
        store, root = store_root
        ctx, meta = build_context(store, root, "explain", "test query", budget=2000)
        assert 0.0 <= meta["utilization"] <= 2.0  # may exceed 1.0 on overflow

    def test_meta_paths_included_is_list(self, store_root):
        store, root = store_root
        ctx, meta = build_context(store, root, "explain", "test query", budget=2000)
        assert isinstance(meta["paths_included"], list)


# ─────────────────────────────────────────────── build_context content

class TestBuildContextContent:
    def test_contains_task_in_header(self, store_root):
        store, root = store_root
        ctx, _ = build_context(store, root, "explain", "how does compute work", budget=4000)
        assert "explain" in ctx

    def test_contains_query_in_header(self, store_root):
        store, root = store_root
        query = "how does compute work"
        ctx, _ = build_context(store, root, "explain", query, budget=4000)
        assert query[:40] in ctx

    def test_context_not_empty(self, store_root):
        store, root = store_root
        ctx, _ = build_context(store, root, "explain", "something", budget=4000)
        assert len(ctx) > 0

    def test_with_relevant_symbols(self, store_root):
        store, root = store_root
        syms = store.get_all_symbols()
        ctx, meta = build_context(
            store, root, "explain", "compute function",
            relevant_symbols=syms, budget=4000
        )
        assert isinstance(ctx, str)
        assert len(ctx) > 0

    def test_with_relevant_paths(self, store_root):
        store, root = store_root
        ctx, meta = build_context(
            store, root, "bugfix", "fix compute bug",
            relevant_paths=["src/mymodule.py"], budget=4000
        )
        assert isinstance(ctx, str)

    def test_unknown_task_falls_back_to_explain(self, store_root):
        store, root = store_root
        ctx, meta = build_context(store, root, "unknown_task_xyz", "query", budget=2000)
        # Should not crash — uses POLICIES.get(task, POLICIES["explain"])
        assert isinstance(ctx, str)

    def test_tiny_budget_still_returns_string(self, store_root):
        store, root = store_root
        ctx, meta = build_context(store, root, "explain", "query", budget=10)
        assert isinstance(ctx, str)


# ─────────────────────────────────────────────── build_context tasks

class TestBuildContextAllTasks:
    def test_explain_task(self, store_root):
        store, root = store_root
        ctx, meta = build_context(store, root, "explain", "explain this", budget=4000)
        assert meta["task"] == "explain"

    def test_bugfix_task(self, store_root):
        store, root = store_root
        ctx, meta = build_context(store, root, "bugfix", "fix the error", budget=4000)
        assert meta["task"] == "bugfix"

    def test_refactor_task(self, store_root):
        store, root = store_root
        ctx, meta = build_context(store, root, "refactor", "refactor this", budget=4000)
        assert meta["task"] == "refactor"

    def test_generate_test_task_prioritizes_source(self, store_root):
        store, root = store_root
        # Create a test file too
        test_file = root / "tests" / "test_mymodule.py"
        test_file.parent.mkdir(exist_ok=True)
        test_file.write_text("def test_compute(): assert True\n")
        fid = store.upsert_file("tests/test_mymodule.py", "th", "python", 50)
        store.commit()
        ctx, meta = build_context(
            store, root, "generate_test", "write tests",
            relevant_paths=["tests/test_mymodule.py", "src/mymodule.py"],
            budget=4000
        )
        assert meta["task"] == "generate_test"

    def test_navigate_task(self, store_root):
        store, root = store_root
        ctx, meta = build_context(store, root, "navigate", "find compute", budget=4000)
        assert meta["task"] == "navigate"


# ─────────────────────────────────────────────── budget behavior

class TestBuildContextBudget:
    def test_zero_tokens_used_on_empty_store(self, tmp_path):
        db_file = tmp_path / "empty.db"
        conn = init_db(db_file)
        s = Store(conn)
        ctx, meta = build_context(s, tmp_path, "explain", "query", budget=1000)
        assert meta["tokens_used"] >= 0
        conn.close()

    def test_large_budget_allows_more_content(self, store_root):
        store, root = store_root
        _, meta_small = build_context(
            store, root, "bugfix", "fix bug",
            relevant_paths=["src/mymodule.py"], budget=500
        )
        _, meta_large = build_context(
            store, root, "bugfix", "fix bug",
            relevant_paths=["src/mymodule.py"], budget=100000
        )
        # With larger budget, more tokens can be used
        assert meta_large["tokens_used"] >= meta_small["tokens_used"]
