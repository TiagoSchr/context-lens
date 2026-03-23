"""End-to-end integration tests: index project, search, build context."""
from __future__ import annotations
import pytest
from pathlib import Path
from src.ctx.db.schema import init_db
from src.ctx.db.store import Store
from src.ctx.indexer.extractor import extract_symbols
from src.ctx.indexer.hasher import hash_file
from src.ctx.indexer.walker import walk_project
from src.ctx.retrieval.intent import classify_intent
from src.ctx.retrieval.search import search_symbols, find_related_paths
from src.ctx.context.builder import build_context


# ─────────────────────────────────────────────── helpers

def _index_file(store: Store, path: Path, rel_path: str) -> None:
    """Index a single file into the store."""
    symbols, lang = extract_symbols(path)
    h = hash_file(path)
    fid = store.upsert_file(rel_path, h, lang, path.stat().st_size)
    if symbols:
        for s in symbols:
            s["file_id"] = fid
            s["path"] = rel_path
        store.insert_symbols_batch(symbols)
    store.commit()


def _build_mini_project(tmp_path: Path) -> tuple[Store, Path]:
    """Build a mini project with multiple files and index them."""
    # Create project structure
    src = tmp_path / "src"
    src.mkdir()
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()

    # auth.py
    auth_file = src / "auth.py"
    auth_file.write_text(
        '"""Authentication module."""\n\n'
        "class AuthManager:\n"
        '    """Manages user authentication."""\n\n'
        "    def __init__(self, secret: str):\n"
        "        self.secret = secret\n\n"
        "    def verify_token(self, token: str) -> bool:\n"
        '        """Verify a JWT token."""\n'
        "        return token == self.secret\n\n"
        "def generate_token(user_id: int) -> str:\n"
        '    """Generate authentication token."""\n'
        "    return f'token_{user_id}'\n",
        encoding="utf-8",
    )

    # db.py
    db_file = src / "db.py"
    db_file.write_text(
        '"""Database module."""\n\n'
        "class DatabaseConnection:\n"
        '    """SQLite connection wrapper."""\n\n'
        "    def __init__(self, path: str):\n"
        "        self.path = path\n\n"
        "    def query(self, sql: str) -> list:\n"
        '        """Execute a query."""\n'
        "        return []\n\n"
        "def connect(url: str):\n"
        '    """Create a new connection."""\n'
        "    return DatabaseConnection(url)\n",
        encoding="utf-8",
    )

    # test_auth.py
    test_file = tests_dir / "test_auth.py"
    test_file.write_text(
        "def test_verify_token():\n"
        "    from src.auth import AuthManager\n"
        "    mgr = AuthManager('secret')\n"
        "    assert mgr.verify_token('secret')\n",
        encoding="utf-8",
    )

    # Index everything
    db_path = tmp_path / "index.db"
    conn = init_db(db_path)
    store = Store(conn)

    for f in [auth_file, db_file, test_file]:
        rel = str(f.relative_to(tmp_path)).replace("\\", "/")
        _index_file(store, f, rel)

    return store, tmp_path


# ─────────────────────────────────────────────── end-to-end indexing

class TestEndToEndIndexing:
    def test_index_creates_files_in_store(self, tmp_path):
        store, root = _build_mini_project(tmp_path)
        paths = store.list_indexed_paths()
        assert len(paths) == 3

    def test_index_extracts_symbols(self, tmp_path):
        store, root = _build_mini_project(tmp_path)
        all_syms = store.get_all_symbols()
        assert len(all_syms) >= 4  # AuthManager, verify_token, generate_token, DatabaseConnection, etc.

    def test_index_stores_classes(self, tmp_path):
        store, root = _build_mini_project(tmp_path)
        classes = store.get_symbols_by_kind("class")
        names = [s["name"] for s in classes]
        assert "AuthManager" in names
        assert "DatabaseConnection" in names

    def test_index_stores_functions(self, tmp_path):
        store, root = _build_mini_project(tmp_path)
        funcs = store.get_symbols_by_kind("function")
        names = [s["name"] for s in funcs]
        assert "generate_token" in names or "connect" in names

    def test_stats_after_indexing(self, tmp_path):
        store, root = _build_mini_project(tmp_path)
        s = store.stats()
        assert s["files"] == 3
        assert s["symbols"] >= 4
        assert "python" in s["by_language"]

    def test_hash_stored_correctly(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        f = src / "code.py"
        f.write_text("def foo(): pass\n")
        db_path = tmp_path / "idx.db"
        conn = init_db(db_path)
        store = Store(conn)
        _index_file(store, f, "src/code.py")
        stored_hash = store.get_file_hash("src/code.py")
        assert stored_hash == hash_file(f)
        conn.close()

    def test_reindex_updates_symbols(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        f = src / "code.py"
        f.write_text("def foo(): pass\n")
        db_path = tmp_path / "idx.db"
        conn = init_db(db_path)
        store = Store(conn)
        _index_file(store, f, "src/code.py")
        assert len(store.get_symbols_for_file("src/code.py")) >= 1
        # Update file with new content
        f.write_text("def foo(): pass\ndef bar(): pass\n")
        _index_file(store, f, "src/code.py")
        syms = store.get_symbols_for_file("src/code.py")
        names = [s["name"] for s in syms]
        assert "bar" in names
        conn.close()


# ─────────────────────────────────────────────── end-to-end search

class TestEndToEndSearch:
    def test_search_finds_indexed_symbol(self, tmp_path):
        store, root = _build_mini_project(tmp_path)
        results = search_symbols(store, "verify_token")
        names = [r["name"] for r in results]
        assert "verify_token" in names

    def test_search_by_class_name(self, tmp_path):
        store, root = _build_mini_project(tmp_path)
        results = search_symbols(store, "AuthManager")
        names = [r["name"] for r in results]
        assert "AuthManager" in names

    def test_search_by_docstring_term(self, tmp_path):
        store, root = _build_mini_project(tmp_path)
        results = search_symbols(store, "authentication")
        assert len(results) >= 1

    def test_find_related_paths_from_search(self, tmp_path):
        store, root = _build_mini_project(tmp_path)
        results = search_symbols(store, "AuthManager")
        paths = find_related_paths(store, results, max_paths=5)
        assert any("auth.py" in p for p in paths)


# ─────────────────────────────────────────────── end-to-end intent + context

class TestEndToEndIntentAndContext:
    def test_classify_then_build(self, tmp_path):
        store, root = _build_mini_project(tmp_path)
        query = "explain how authentication works"
        task, conf = classify_intent(query)
        assert task == "explain"
        ctx, meta = build_context(store, root, task, query, budget=8000)
        assert isinstance(ctx, str)
        assert len(ctx) > 0

    def test_bugfix_flow(self, tmp_path):
        store, root = _build_mini_project(tmp_path)
        query = "fix the bug in verify_token"
        task, conf = classify_intent(query)
        assert task == "bugfix"
        syms = search_symbols(store, "verify_token")
        paths = find_related_paths(store, syms, max_paths=3)
        ctx, meta = build_context(
            store, root, task, query,
            relevant_symbols=syms, relevant_paths=paths, budget=8000
        )
        assert meta["task"] == "bugfix"

    def test_generate_test_flow(self, tmp_path):
        store, root = _build_mini_project(tmp_path)
        query = "write tests for generate_token"
        task, conf = classify_intent(query)
        assert task == "generate_test"
        syms = search_symbols(store, "generate_token")
        paths = find_related_paths(store, syms, max_paths=3)
        ctx, meta = build_context(
            store, root, task, query,
            relevant_symbols=syms, relevant_paths=paths, budget=8000
        )
        assert meta["task"] == "generate_test"

    def test_navigate_flow(self, tmp_path):
        store, root = _build_mini_project(tmp_path)
        query = "where is DatabaseConnection defined"
        task, conf = classify_intent(query)
        syms = search_symbols(store, "DatabaseConnection")
        paths = find_related_paths(store, syms)
        assert len(paths) >= 1

    def test_full_pipeline_no_crash(self, tmp_path):
        store, root = _build_mini_project(tmp_path)
        for query in [
            "explain the auth module",
            "fix the connection bug",
            "refactor the database class",
            "write tests for the auth manager",
            "find where tokens are generated",
        ]:
            task, _ = classify_intent(query)
            syms = search_symbols(store, query)
            paths = find_related_paths(store, syms, max_paths=3)
            ctx, meta = build_context(
                store, root, task, query,
                relevant_symbols=syms, relevant_paths=paths, budget=4000
            )
            assert isinstance(ctx, str)
            assert isinstance(meta, dict)


# ─────────────────────────────────────────────── walker integration

class TestWalkerIntegration:
    def test_walk_and_index_real_project(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        for i in range(5):
            (src / f"module_{i}.py").write_text(f"def func_{i}(): pass\n")
        (src / "ignored").mkdir()
        (src / "ignored" / "skip.py").write_text("def skip(): pass\n")

        db_path = tmp_path / "idx.db"
        conn = init_db(db_path)
        store = Store(conn)

        files = list(walk_project(tmp_path, [".py"], {"ignored"}, max_size_kb=512))
        for f in files:
            rel = str(f.relative_to(tmp_path)).replace("\\", "/")
            _index_file(store, f, rel)

        stats = store.stats()
        assert stats["files"] == 5
        names = [s["name"] for s in store.get_all_symbols()]
        assert all(f"func_{i}" in names for i in range(5))
        conn.close()
