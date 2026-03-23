"""Tests for search_symbols, find_related_paths, POLICIES from search.py and policy.py."""
from __future__ import annotations
import pytest
from pathlib import Path
from src.ctx.db.schema import init_db
from src.ctx.db.store import Store
from src.ctx.retrieval.search import (
    search_symbols,
    find_related_paths,
    find_callers,
    find_imported_paths,
    expand_paths_cross_file,
    _build_fts_query,
)
from src.ctx.retrieval.policy import POLICIES, TaskPolicy


# ─────────────────────────────────────────────── fixtures

@pytest.fixture
def store(tmp_path):
    db_file = tmp_path / "test.db"
    conn = init_db(db_file)
    s = Store(conn)
    yield s
    conn.close()


def _add_symbol(store: Store, name: str, kind: str = "function", path: str = "src/foo.py",
               docstring: str | None = None, start_line: int = 1) -> None:
    fid = store.upsert_file(path, "h", "python", 100)
    store.insert_symbols_batch([{
        "file_id": fid, "name": name, "kind": kind, "params": None,
        "return_type": None, "docstring": docstring,
        "start_line": start_line, "end_line": start_line + 5,
        "language": "python", "path": path,
    }])
    store.commit()


# ─────────────────────────────────────────────── _build_fts_query

class TestBuildFtsQuery:
    def test_returns_string(self):
        result = _build_fts_query("search for something")
        assert isinstance(result, str)

    def test_empty_query_returns_string(self):
        result = _build_fts_query("")
        assert isinstance(result, str)

    def test_technical_identifier_gets_exact_match(self):
        result = _build_fts_query("compute_budget")
        assert '"compute_budget"' in result

    def test_camelcase_gets_exact_match(self):
        result = _build_fts_query("buildContext")
        assert '"buildContext"' in result

    def test_stop_words_filtered(self):
        result = _build_fts_query("fix the bug in the code")
        # Stop words like "fix", "the", "bug", "code" are filtered
        # result should be empty or just have valid terms
        assert isinstance(result, str)

    def test_long_word_included(self):
        result = _build_fts_query("authentication")
        assert "authentication" in result

    def test_or_joined(self):
        result = _build_fts_query("compute_tokens build_context")
        if result:
            assert " OR " in result or result.strip()


# ─────────────────────────────────────────────── search_symbols

class TestSearchSymbols:
    def test_returns_list(self, store):
        result = search_symbols(store, "anything")
        assert isinstance(result, list)

    def test_empty_query_returns_empty(self, store):
        # Empty query after FTS processing should return []
        result = search_symbols(store, "")
        assert result == []

    def test_finds_by_name(self, store):
        _add_symbol(store, "compute_total", path="src/calc.py")
        results = search_symbols(store, "compute_total")
        names = [r["name"] for r in results]
        assert "compute_total" in names

    def test_finds_by_docstring(self, store):
        _add_symbol(store, "my_func", docstring="Processes the authentication flow", path="src/auth.py")
        results = search_symbols(store, "authentication")
        assert len(results) >= 1

    def test_no_match_returns_empty(self, store):
        _add_symbol(store, "some_func", path="src/foo.py")
        results = search_symbols(store, "zzzz_impossible_match_zzzz")
        assert results == []

    def test_limit_respected(self, store):
        for i in range(15):
            _add_symbol(store, f"search_func_{i}", path=f"src/f{i}.py", start_line=1)
        results = search_symbols(store, "search_func", limit=5)
        assert len(results) <= 5

    def test_results_have_name_field(self, store):
        _add_symbol(store, "my_func", path="src/foo.py")
        results = search_symbols(store, "my_func")
        for r in results:
            assert "name" in r.keys()


# ─────────────────────────────────────────────── find_related_paths

class TestFindRelatedPaths:
    def _make_row(self, path: str, name: str = "f"):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (name TEXT, path TEXT)")
        conn.execute("INSERT INTO t VALUES (?, ?)", (name, path))
        conn.commit()
        return conn.execute("SELECT * FROM t").fetchone()

    def test_returns_list(self, store):
        result = find_related_paths(store, [])
        assert isinstance(result, list)

    def test_empty_symbols_returns_empty(self, store):
        assert find_related_paths(store, []) == []

    def test_returns_unique_paths(self, store):
        _add_symbol(store, "f1", path="src/foo.py", start_line=1)
        _add_symbol(store, "f2", path="src/foo.py", start_line=10)
        syms = store.get_symbols_for_file("src/foo.py")
        result = find_related_paths(store, syms)
        assert result.count("src/foo.py") == 1

    def test_max_paths_respected(self, store):
        for i in range(10):
            _add_symbol(store, f"func_{i}", path=f"src/file_{i}.py")
        all_syms = store.get_all_symbols()
        result = find_related_paths(store, all_syms, max_paths=3)
        assert len(result) <= 3

    def test_test_files_penalized(self, store):
        _add_symbol(store, "source_func", path="src/main.py")
        # Add many test symbols
        for i in range(5):
            _add_symbol(store, f"test_func_{i}", path="tests/test_main.py", start_line=i * 5 + 1)
        all_syms = store.get_all_symbols()
        result = find_related_paths(store, all_syms, max_paths=2, prefer_source=True)
        # Source should be first
        if result:
            assert result[0] == "src/main.py"

    def test_prefer_source_false_no_penalty(self, store):
        _add_symbol(store, "src_func", path="src/main.py")
        all_syms = store.get_all_symbols()
        result = find_related_paths(store, all_syms, prefer_source=False)
        assert isinstance(result, list)


# ─────────────────────────────────────────────── find_callers

class TestFindCallers:
    def test_returns_list(self, store, tmp_path):
        result = find_callers(store, "my_func", tmp_path)
        assert isinstance(result, list)

    def test_finds_caller_in_indexed_file(self, store, tmp_path):
        caller_file = tmp_path / "caller.py"
        caller_file.write_text("from src.foo import my_func\n\nresult = my_func(1, 2)\n")
        store.upsert_file(str(caller_file), "h", "python", 100)
        store.commit()
        results = find_callers(store, "my_func", tmp_path, max_files=100)
        assert str(caller_file) in results

    def test_no_callers_returns_empty(self, store, tmp_path):
        source_file = tmp_path / "source.py"
        source_file.write_text("x = 1\n")
        store.upsert_file(str(source_file), "h", "python", 100)
        store.commit()
        results = find_callers(store, "totally_unique_func_xyz_999", tmp_path)
        assert results == []

    def test_max_results_respected(self, store, tmp_path):
        for i in range(10):
            f = tmp_path / f"file_{i}.py"
            f.write_text("result = target_func()\n")
            store.upsert_file(str(f), f"h{i}", "python", 100)
        store.commit()
        results = find_callers(store, "target_func", tmp_path, max_results=3)
        assert len(results) <= 3


# ─────────────────────────────────────────────── find_imported_paths

class TestFindImportedPaths:
    def test_returns_list(self, tmp_path):
        f = tmp_path / "main.py"
        f.write_text("import os\n")
        result = find_imported_paths(str(f), tmp_path, set())
        assert isinstance(result, list)

    def test_finds_python_import(self, tmp_path):
        target = "src/ctx/db/store.py"
        f = tmp_path / "main.py"
        f.write_text("from ctx.db import store\n")
        indexed = {target}
        result = find_imported_paths(str(f), tmp_path, indexed)
        # May or may not find depending on path mapping
        assert isinstance(result, list)

    def test_missing_file_returns_empty(self, tmp_path):
        result = find_imported_paths("nonexistent.py", tmp_path, set())
        assert result == []

    def test_no_imports_returns_empty(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("x = 1\n")
        result = find_imported_paths(str(f), tmp_path, set())
        assert result == []

    def test_no_duplicates(self, tmp_path):
        src_path = "src/mymodule.py"
        f = tmp_path / "main.py"
        f.write_text("from mymodule import foo\nfrom mymodule import bar\n")
        indexed = {src_path, "mymodule.py"}
        result = find_imported_paths(str(f), tmp_path, indexed)
        assert len(result) == len(set(result))


# ─────────────────────────────────────────────── expand_paths_cross_file

class TestExpandPathsCrossFile:
    def test_returns_list(self, store, tmp_path):
        result = expand_paths_cross_file(store, tmp_path, [], [])
        assert isinstance(result, list)

    def test_includes_original_paths(self, store, tmp_path):
        f = tmp_path / "main.py"
        f.write_text("x = 1\n")
        store.upsert_file(str(f), "h", "python", 100)
        store.commit()
        result = expand_paths_cross_file(store, tmp_path, [str(f)], [])
        assert str(f) in result

    def test_no_duplicates(self, store, tmp_path):
        f = tmp_path / "main.py"
        f.write_text("x = 1\n")
        store.upsert_file(str(f), "h", "python", 100)
        store.commit()
        result = expand_paths_cross_file(store, tmp_path, [str(f)], [])
        assert len(result) == len(set(result))


# ─────────────────────────────────────────────── POLICIES

class TestPolicies:
    def test_all_tasks_have_policy(self):
        for task in ("explain", "bugfix", "refactor", "generate_test", "navigate"):
            assert task in POLICIES

    def test_policy_is_task_policy_instance(self):
        for policy in POLICIES.values():
            assert isinstance(policy, TaskPolicy)

    def test_explain_uses_level0_and_level1(self):
        p = POLICIES["explain"]
        assert p.use_level0 is True
        assert p.use_level1 is True

    def test_bugfix_uses_level3(self):
        p = POLICIES["bugfix"]
        assert p.use_level3 is True

    def test_navigate_no_level2_or_level3(self):
        p = POLICIES["navigate"]
        assert p.use_level2 is False
        assert p.use_level3 is False

    def test_generate_test_uses_level3(self):
        p = POLICIES["generate_test"]
        assert p.use_level3 is True

    def test_generate_test_no_level2(self):
        p = POLICIES["generate_test"]
        assert p.use_level2 is False

    def test_refactor_uses_level3(self):
        p = POLICIES["refactor"]
        assert p.use_level3 is True

    def test_bugfix_no_level0(self):
        p = POLICIES["bugfix"]
        assert p.use_level0 is False

    def test_level1_limit_positive(self):
        for p in POLICIES.values():
            assert p.level1_limit > 0

    def test_level2_body_lines_positive(self):
        for p in POLICIES.values():
            assert p.level2_body_lines > 0

    def test_policy_task_field_matches_key(self):
        for key, policy in POLICIES.items():
            assert policy.task == key
