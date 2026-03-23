"""Tests for build_level0, build_level1, build_level2, build_level3 from src/ctx/context/levels.py."""
from __future__ import annotations
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import MagicMock
from src.ctx.context.levels import (
    build_level0,
    build_level1,
    build_level2,
    build_level3,
    _fmt_symbol,
    _read_source,
)
from src.ctx.db.schema import init_db
from src.ctx.db.store import Store


# ─────────────────────────────────────────────── helpers

def _make_row(name="my_func", kind="function", params="(x, y)", return_type="str",
              docstring="Does something", path="src/foo.py", start_line=5) -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE t (
        id INTEGER, name TEXT, kind TEXT, params TEXT, return_type TEXT,
        docstring TEXT, path TEXT, start_line INTEGER, end_line INTEGER,
        language TEXT, file_id INTEGER
    )""")
    conn.execute("INSERT INTO t VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, 'python', 1)",
                 (name, kind, params, return_type, docstring, path, start_line, start_line + 10))
    conn.commit()
    row = conn.execute("SELECT * FROM t").fetchone()
    return row


@pytest.fixture
def store(tmp_path):
    db_file = tmp_path / "test.db"
    conn = init_db(db_file)
    s = Store(conn)
    yield s
    conn.close()


def _populate_store(store: Store, root: Path) -> None:
    fid = store.upsert_file("src/foo.py", "h1", "python", 100)
    store.insert_symbols_batch([
        {"file_id": fid, "name": "MyClass", "kind": "class", "params": None,
         "return_type": None, "docstring": "A sample class", "start_line": 1,
         "end_line": 20, "language": "python", "path": "src/foo.py"},
        {"file_id": fid, "name": "my_func", "kind": "function", "params": "(x, y)",
         "return_type": "str", "docstring": "Does stuff", "start_line": 22,
         "end_line": 30, "language": "python", "path": "src/foo.py"},
    ])
    store.commit()


# ─────────────────────────────────────────────── _fmt_symbol

class TestFmtSymbol:
    def test_basic_format(self):
        row = _make_row()
        result = _fmt_symbol(row)
        assert "[function]" in result
        assert "my_func" in result

    def test_includes_params(self):
        row = _make_row(params="(a: int, b: str)")
        result = _fmt_symbol(row)
        assert "(a: int, b: str)" in result

    def test_includes_return_type(self):
        row = _make_row(return_type="int")
        result = _fmt_symbol(row)
        assert "-> int" in result

    def test_includes_docstring_first_line(self):
        row = _make_row(docstring="First line\nSecond line")
        result = _fmt_symbol(row)
        assert "First line" in result
        assert "Second line" not in result

    def test_includes_path_and_line(self):
        row = _make_row(path="src/foo.py", start_line=42)
        result = _fmt_symbol(row)
        assert "src/foo.py:42" in result

    def test_no_params_no_crash(self):
        row = _make_row(params=None)
        result = _fmt_symbol(row)
        assert "[function]" in result

    def test_no_return_type_no_crash(self):
        row = _make_row(return_type=None)
        result = _fmt_symbol(row)
        assert "my_func" in result

    def test_no_docstring_no_crash(self):
        row = _make_row(docstring=None)
        result = _fmt_symbol(row)
        assert "my_func" in result


# ─────────────────────────────────────────────── _read_source

class TestReadSource:
    def test_reads_valid_file(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("line1\nline2\nline3")
        result = _read_source(f)
        assert result == ["line1", "line2", "line3"]

    def test_returns_none_for_missing_file(self, tmp_path):
        f = tmp_path / "nonexistent.py"
        result = _read_source(f)
        assert result is None


# ─────────────────────────────────────────────── build_level0

class TestBuildLevel0:
    def test_returns_string(self, store, tmp_path):
        result = build_level0(store, tmp_path)
        assert isinstance(result, str)

    def test_contains_project_map_header(self, store, tmp_path):
        result = build_level0(store, tmp_path)
        assert "PROJECT MAP" in result

    def test_contains_stats(self, store, tmp_path):
        result = build_level0(store, tmp_path)
        assert "files" in result.lower() or "symbols" in result.lower()

    def test_uses_project_map_table_when_set(self, store, tmp_path):
        store.set_project_map("root", str(tmp_path))
        store.set_project_map("framework", "pytest")
        store.commit()
        result = build_level0(store, tmp_path)
        assert "framework" in result
        assert "pytest" in result

    def test_shows_top_dirs_when_no_project_map(self, store, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        result = build_level0(store, tmp_path)
        # Either shows dirs or root path
        assert str(tmp_path) in result or "src" in result or "tests" in result

    def test_reads_pyproject_toml(self, store, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[project]\nname = 'myapp'\n")
        result = build_level0(store, tmp_path)
        assert "pyproject.toml" in result or "myapp" in result

    def test_reads_readme(self, store, tmp_path):
        readme = tmp_path / "README.md"
        readme.write_text("# My Project\nThis is a test project.\n")
        result = build_level0(store, tmp_path)
        assert "My Project" in result or "README" in result

    def test_stats_reflect_indexed_files(self, store, tmp_path):
        _populate_store(store, tmp_path)
        result = build_level0(store, tmp_path)
        assert "1" in result  # 1 file indexed


# ─────────────────────────────────────────────── build_level1

class TestBuildLevel1:
    def test_returns_string(self, store, tmp_path):
        result = build_level1(store)
        assert isinstance(result, str)

    def test_header_present(self, store, tmp_path):
        result = build_level1(store)
        assert "SYMBOLS" in result

    def test_no_symbols_message(self, store, tmp_path):
        result = build_level1(store)
        assert "no symbols" in result.lower()

    def test_with_symbols(self, store, tmp_path):
        _populate_store(store, tmp_path)
        result = build_level1(store)
        assert "MyClass" in result
        assert "my_func" in result

    def test_groups_by_file(self, store, tmp_path):
        _populate_store(store, tmp_path)
        result = build_level1(store)
        assert "src/foo.py" in result

    def test_explicit_symbols_list(self, store, tmp_path):
        _populate_store(store, tmp_path)
        all_syms = store.get_all_symbols()
        result = build_level1(store, symbols=all_syms[:1])
        assert "SYMBOLS" in result
        assert len(result) > 0

    def test_limit_respected(self, store, tmp_path):
        fid = store.upsert_file("src/big.py", "h", "python", 100)
        for i in range(20):
            store.insert_symbols_batch([{
                "file_id": fid, "name": f"func_{i}", "kind": "function",
                "params": None, "return_type": None, "docstring": None,
                "start_line": i * 5 + 1, "end_line": i * 5 + 4,
                "language": "python", "path": "src/big.py",
            }])
        store.commit()
        result = build_level1(store, limit=5)
        # Check that only 5 symbols are represented
        count = result.count("[function]")
        assert count <= 5


# ─────────────────────────────────────────────── build_level2

class TestBuildLevel2:
    def test_returns_string(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("def foo():\n    pass\n")
        row = _make_row(name="foo", start_line=1)
        result = build_level2(f, [row])
        assert isinstance(result, str)

    def test_header_contains_path(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("def foo():\n    return 1\n")
        row = _make_row(name="foo", start_line=1)
        result = build_level2(f, [row])
        assert "code.py" in result or str(f) in result

    def test_includes_source_snippet(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("def foo():\n    return 42\n")
        row = _make_row(name="foo", start_line=1)
        result = build_level2(f, [row])
        assert "def foo" in result

    def test_truncation_marker(self, tmp_path):
        f = tmp_path / "code.py"
        lines = ["def long_func():\n"] + [f"    line_{i} = {i}\n" for i in range(30)]
        f.write_text("".join(lines))
        row = _make_row(name="long_func", start_line=1)
        # end_line is 11 in row; max_body_lines=5 → truncation
        row_conn = sqlite3.connect(":memory:")
        row_conn.row_factory = sqlite3.Row
        row_conn.execute("CREATE TABLE t (id INT, name TEXT, kind TEXT, params TEXT, return_type TEXT, docstring TEXT, path TEXT, start_line INT, end_line INT, language TEXT, file_id INT)")
        row_conn.execute("INSERT INTO t VALUES (1, 'long_func', 'function', NULL, NULL, NULL, ?, 1, 30, 'python', 1)", (str(f),))
        row_conn.commit()
        big_row = row_conn.execute("SELECT * FROM t").fetchone()
        result = build_level2(f, [big_row], max_body_lines=3)
        assert "..." in result

    def test_unreadable_file_returns_message(self, tmp_path):
        f = tmp_path / "missing.py"
        row = _make_row(name="foo", start_line=1)
        result = build_level2(f, [row])
        assert "unreadable" in result

    def test_accepts_source_lines_kwarg(self, tmp_path):
        f = tmp_path / "code.py"
        source_lines = ["def foo():", "    return 1"]
        row = _make_row(name="foo", start_line=1)
        result = build_level2(f, [row], source_lines=source_lines)
        assert "def foo" in result

    def test_skeleton_header(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("def foo():\n    pass\n")
        row = _make_row(name="foo", start_line=1)
        result = build_level2(f, [row])
        assert "level2" in result or "skeleton" in result


# ─────────────────────────────────────────────── build_level3

class TestBuildLevel3:
    def test_returns_string(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("print('hello')\n")
        result = build_level3(f)
        assert isinstance(result, str)

    def test_header_contains_line_count(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("line1\nline2\nline3\n")
        result = build_level3(f)
        assert "3" in result

    def test_contains_file_content(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("MY_UNIQUE_SENTINEL_VALUE = 42\n")
        result = build_level3(f)
        assert "MY_UNIQUE_SENTINEL_VALUE" in result

    def test_max_lines_cap(self, tmp_path):
        f = tmp_path / "code.py"
        content = "\n".join([f"line_{i}" for i in range(100)])
        f.write_text(content)
        result = build_level3(f, max_lines=10)
        assert "line_9" in result
        assert "line_10" not in result or "more lines" in result

    def test_truncation_marker_when_capped(self, tmp_path):
        f = tmp_path / "code.py"
        content = "\n".join([f"line_{i}" for i in range(50)])
        f.write_text(content)
        result = build_level3(f, max_lines=10)
        assert "more lines" in result

    def test_no_truncation_marker_when_fits(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("just a few\nlines here\n")
        result = build_level3(f, max_lines=300)
        assert "more lines" not in result

    def test_unreadable_file_returns_message(self, tmp_path):
        f = tmp_path / "missing.py"
        result = build_level3(f)
        assert "unreadable" in result

    def test_accepts_source_lines_kwarg(self, tmp_path):
        f = tmp_path / "code.py"
        source_lines = ["def foo():", "    return 42"]
        result = build_level3(f, source_lines=source_lines)
        assert "def foo" in result
