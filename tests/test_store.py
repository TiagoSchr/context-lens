"""Tests for Store from src/ctx/db/store.py (upsert, fts, memory, stats)."""
from __future__ import annotations
import sqlite3
import time
import pytest
from pathlib import Path
from src.ctx.db.schema import init_db
from src.ctx.db.store import Store


@pytest.fixture
def store(tmp_path):
    """Create an in-memory store for testing."""
    db_file = tmp_path / "test.db"
    conn = init_db(db_file)
    s = Store(conn)
    yield s
    conn.close()


def _insert_file(store: Store, path: str = "src/foo.py", language: str = "python") -> int:
    file_id = store.upsert_file(path, "abc123", language, 100)
    store.commit()
    return file_id


def _insert_symbol(store: Store, file_id: int, name: str = "my_func", kind: str = "function",
                   path: str = "src/foo.py", start_line: int = 1, end_line: int = 10,
                   docstring: str | None = None) -> None:
    store.insert_symbols_batch([{
        "file_id": file_id,
        "name": name,
        "kind": kind,
        "params": "(x, y)",
        "return_type": "str",
        "docstring": docstring,
        "start_line": start_line,
        "end_line": end_line,
        "language": "python",
        "path": path,
    }])
    store.commit()


# ─────────────────────────────────────────────── files

class TestStoreFiles:
    def test_upsert_file_returns_id(self, store):
        fid = store.upsert_file("src/foo.py", "hash1", "python", 200)
        assert isinstance(fid, int)
        assert fid > 0

    def test_upsert_file_get_hash(self, store):
        store.upsert_file("src/foo.py", "hash_xyz", "python", 200)
        store.commit()
        h = store.get_file_hash("src/foo.py")
        assert h == "hash_xyz"

    def test_upsert_file_updates_on_conflict(self, store):
        store.upsert_file("src/foo.py", "hash_v1", "python", 100)
        store.commit()
        store.upsert_file("src/foo.py", "hash_v2", "python", 200)
        store.commit()
        h = store.get_file_hash("src/foo.py")
        assert h == "hash_v2"

    def test_get_file_hash_missing(self, store):
        h = store.get_file_hash("nonexistent.py")
        assert h is None

    def test_get_file_id_existing(self, store):
        fid = store.upsert_file("src/bar.py", "h", "python", 10)
        store.commit()
        returned_id = store.get_file_id("src/bar.py")
        assert returned_id == fid

    def test_get_file_id_missing(self, store):
        result = store.get_file_id("missing.py")
        assert result == -1

    def test_delete_file(self, store):
        store.upsert_file("src/del.py", "h", "python", 10)
        store.commit()
        store.delete_file("src/del.py")
        store.commit()
        assert store.get_file_hash("src/del.py") is None

    def test_list_indexed_paths_empty(self, store):
        result = store.list_indexed_paths()
        assert result == []

    def test_list_indexed_paths_returns_all(self, store):
        store.upsert_file("src/a.py", "h1", "python", 10)
        store.upsert_file("src/b.py", "h2", "python", 20)
        store.commit()
        paths = store.list_indexed_paths()
        assert "src/a.py" in paths
        assert "src/b.py" in paths

    def test_upsert_file_clears_symbols(self, store):
        fid = store.upsert_file("src/foo.py", "h1", "python", 100)
        store.insert_symbols_batch([{
            "file_id": fid, "name": "func_a", "kind": "function",
            "params": None, "return_type": None, "docstring": None,
            "start_line": 1, "end_line": 5, "language": "python", "path": "src/foo.py",
        }])
        store.commit()
        # Re-upsert file — should clear symbols
        store.upsert_file("src/foo.py", "h2", "python", 200)
        store.commit()
        syms = store.get_symbols_for_file("src/foo.py")
        assert syms == []


# ─────────────────────────────────────────────── symbols

class TestStoreSymbols:
    def test_insert_and_get_symbols_for_file(self, store):
        fid = _insert_file(store)
        _insert_symbol(store, fid, "my_func", path="src/foo.py")
        syms = store.get_symbols_for_file("src/foo.py")
        assert len(syms) >= 1
        assert syms[0]["name"] == "my_func"

    def test_get_all_symbols(self, store):
        fid = _insert_file(store)
        _insert_symbol(store, fid, "alpha", path="src/foo.py")
        _insert_symbol(store, fid, "beta", path="src/foo.py", start_line=20, end_line=30)
        syms = store.get_all_symbols()
        names = [s["name"] for s in syms]
        assert "alpha" in names
        assert "beta" in names

    def test_get_all_symbols_limit(self, store):
        fid = _insert_file(store)
        for i in range(10):
            store.insert_symbols_batch([{
                "file_id": fid, "name": f"func_{i}", "kind": "function",
                "params": None, "return_type": None, "docstring": None,
                "start_line": i * 5 + 1, "end_line": i * 5 + 4,
                "language": "python", "path": "src/foo.py",
            }])
        store.commit()
        syms = store.get_all_symbols(limit=5)
        assert len(syms) <= 5

    def test_get_symbols_by_name(self, store):
        fid = _insert_file(store)
        _insert_symbol(store, fid, "special_func", path="src/foo.py")
        result = store.get_symbols_by_name("special_func")
        assert len(result) >= 1
        assert result[0]["name"] == "special_func"

    def test_get_symbols_by_name_missing(self, store):
        result = store.get_symbols_by_name("nonexistent_func_xyz")
        assert result == []

    def test_get_symbols_by_kind(self, store):
        fid = _insert_file(store)
        _insert_symbol(store, fid, "MyClass", kind="class", path="src/foo.py")
        _insert_symbol(store, fid, "my_func", kind="function", path="src/foo.py", start_line=20, end_line=30)
        classes = store.get_symbols_by_kind("class")
        assert any(s["name"] == "MyClass" for s in classes)
        funcs = store.get_symbols_by_kind("function")
        assert any(s["name"] == "my_func" for s in funcs)

    def test_symbols_ordered_by_start_line(self, store):
        fid = _insert_file(store)
        _insert_symbol(store, fid, "func_b", start_line=30, end_line=40, path="src/foo.py")
        _insert_symbol(store, fid, "func_a", start_line=5, end_line=15, path="src/foo.py")
        syms = store.get_symbols_for_file("src/foo.py")
        lines = [s["start_line"] for s in syms]
        assert lines == sorted(lines)


# ─────────────────────────────────────────────── FTS

class TestStoreFTS:
    def test_fts_search_by_name(self, store):
        fid = _insert_file(store)
        _insert_symbol(store, fid, "compute_budget", path="src/foo.py")
        results = store.search_symbols_fts("compute_budget")
        assert len(results) >= 1
        assert results[0]["name"] == "compute_budget"

    def test_fts_search_by_docstring(self, store):
        fid = _insert_file(store)
        _insert_symbol(store, fid, "my_func", docstring="Calculates the final result", path="src/foo.py")
        results = store.search_symbols_fts("Calculates")
        assert len(results) >= 1

    def test_fts_search_no_match(self, store):
        fid = _insert_file(store)
        _insert_symbol(store, fid, "foo_bar", path="src/foo.py")
        results = store.search_symbols_fts("zzzzz_not_exist_zzzzz")
        assert results == []

    def test_fts_search_limit(self, store):
        fid = _insert_file(store)
        for i in range(10):
            _insert_symbol(store, fid, f"search_func_{i}", path="src/foo.py",
                          start_line=i * 10 + 1, end_line=i * 10 + 5)
        results = store.search_symbols_fts("search_func", limit=3)
        assert len(results) <= 3


# ─────────────────────────────────────────────── project map

class TestStoreProjectMap:
    def test_set_and_get_project_map(self, store):
        store.set_project_map("root", "/my/project")
        store.commit()
        assert store.get_project_map("root") == "/my/project"

    def test_get_project_map_missing(self, store):
        assert store.get_project_map("nonexistent_key") is None

    def test_set_project_map_updates_on_conflict(self, store):
        store.set_project_map("key", "v1")
        store.commit()
        store.set_project_map("key", "v2")
        store.commit()
        assert store.get_project_map("key") == "v2"

    def test_get_all_project_map_empty(self, store):
        result = store.get_all_project_map()
        assert isinstance(result, dict)
        assert result == {}

    def test_get_all_project_map_returns_all(self, store):
        store.set_project_map("k1", "v1")
        store.set_project_map("k2", "v2")
        store.commit()
        result = store.get_all_project_map()
        assert result["k1"] == "v1"
        assert result["k2"] == "v2"


# ─────────────────────────────────────────────── memory

class TestStoreMemory:
    def test_memory_set_and_get(self, store):
        store.memory_set("note", "key1", "some value")
        store.commit()
        rows = store.memory_get("note", "key1")
        assert len(rows) >= 1
        assert rows[0]["value"] == "some value"

    def test_memory_get_by_kind_only(self, store):
        store.memory_set("ref", "a", "val_a")
        store.memory_set("ref", "b", "val_b")
        store.memory_set("note", "c", "val_c")
        store.commit()
        refs = store.memory_get("ref")
        assert len(refs) == 2

    def test_memory_get_missing_key(self, store):
        result = store.memory_get("note", "missing_key_xyz")
        assert result == []

    def test_memory_list(self, store):
        store.memory_set("map", "k1", "v1")
        store.memory_set("hotspot", "k2", "v2")
        store.commit()
        all_mem = store.memory_list()
        assert len(all_mem) >= 2

    def test_memory_delete(self, store):
        store.memory_set("note", "del_key", "to delete")
        store.commit()
        rows = store.memory_get("note", "del_key")
        assert len(rows) >= 1
        mem_id = rows[0]["id"]
        store.memory_delete(mem_id)
        store.commit()
        after = store.memory_get("note", "del_key")
        assert after == []

    def test_memory_with_ttl_expires(self, store):
        store.memory_set("note", "expiring", "value", ttl=1)
        store.commit()
        # Should be visible immediately
        rows = store.memory_get("note", "expiring")
        assert len(rows) >= 1
        # After 2 seconds it should expire — we test purge instead
        # Manually set expires_at in the past
        store._conn.execute(
            "UPDATE memory_lite SET expires_at = ? WHERE key = 'expiring'",
            (time.time() - 1,)
        )
        store.commit()
        purged = store.memory_purge_expired()
        assert purged >= 1
        after = store.memory_get("note", "expiring")
        assert after == []

    def test_memory_purge_expired_no_op_when_empty(self, store):
        purged = store.memory_purge_expired()
        assert purged == 0


# ─────────────────────────────────────────────── stats

class TestStoreStats:
    def test_stats_empty(self, store):
        s = store.stats()
        assert s["files"] == 0
        assert s["symbols"] == 0
        assert s["by_kind"] == {}
        assert s["by_language"] == {}

    def test_stats_files_count(self, store):
        store.upsert_file("src/a.py", "h", "python", 10)
        store.upsert_file("src/b.py", "h2", "python", 20)
        store.commit()
        s = store.stats()
        assert s["files"] == 2

    def test_stats_symbols_count(self, store):
        fid = _insert_file(store)
        _insert_symbol(store, fid, "func1")
        _insert_symbol(store, fid, "func2", start_line=20, end_line=30)
        s = store.stats()
        assert s["symbols"] == 2

    def test_stats_by_kind(self, store):
        fid = _insert_file(store)
        _insert_symbol(store, fid, "MyClass", kind="class")
        _insert_symbol(store, fid, "my_func", kind="function", start_line=20, end_line=30)
        s = store.stats()
        assert "function" in s["by_kind"]
        assert "class" in s["by_kind"]

    def test_stats_by_language(self, store):
        store.upsert_file("src/a.py", "h1", "python", 10)
        store.upsert_file("src/b.js", "h2", "javascript", 20)
        store.commit()
        s = store.stats()
        assert "python" in s["by_language"]
        assert "javascript" in s["by_language"]

    def test_stats_last_indexed_none_when_empty(self, store):
        s = store.stats()
        assert s["last_indexed"] is None

    def test_stats_last_indexed_set_after_file(self, store):
        store.upsert_file("src/a.py", "h", "python", 10)
        store.commit()
        s = store.stats()
        assert s["last_indexed"] is not None
