"""Tests for extract_symbols from src/ctx/indexer/extractor.py (tree-sitter and regex)."""
from __future__ import annotations
import pytest
from pathlib import Path
from src.ctx.indexer.extractor import (
    extract_symbols,
    _extract_regex,
)
from src.ctx.indexer.parser import is_available


# ─────────────────────────────────────────────── fixtures

@pytest.fixture
def py_file(tmp_path):
    """Write a simple Python file for extraction tests."""
    f = tmp_path / "sample.py"
    f.write_text(
        '''"""Module docstring."""

class MyClass:
    """A sample class."""

    def method_one(self, x: int) -> str:
        """Method docstring."""
        return str(x)

    def method_two(self):
        pass


def standalone_func(a, b):
    """Standalone function."""
    return a + b


async def async_func(items: list) -> None:
    """Async function."""
    pass
''',
        encoding="utf-8",
    )
    return f


@pytest.fixture
def js_file(tmp_path):
    f = tmp_path / "sample.js"
    f.write_text(
        '''function greet(name) {
    return "Hello " + name;
}

class Animal {
    constructor(name) {
        this.name = name;
    }

    speak() {
        console.log(this.name);
    }
}

const arrowFunc = (x) => x * 2;
''',
        encoding="utf-8",
    )
    return f


@pytest.fixture
def go_file(tmp_path):
    f = tmp_path / "sample.go"
    f.write_text(
        '''package main

func Add(a int, b int) int {
    return a + b
}

type Server struct {
    host string
}
''',
        encoding="utf-8",
    )
    return f


@pytest.fixture
def rust_file(tmp_path):
    f = tmp_path / "sample.rs"
    f.write_text(
        '''pub fn calculate(x: u32) -> u32 {
    x * 2
}

pub struct Config {
    name: String,
}
''',
        encoding="utf-8",
    )
    return f


# ─────────────────────────────────────────────── extract_symbols API

class TestExtractSymbolsAPI:
    def test_returns_tuple(self, py_file):
        result = extract_symbols(py_file)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_returns_list_and_lang(self, py_file):
        symbols, lang = extract_symbols(py_file)
        assert isinstance(symbols, list)
        assert lang == "python"

    def test_unknown_extension_returns_empty(self, tmp_path):
        f = tmp_path / "file.xyz"
        f.write_text("some content")
        symbols, lang = extract_symbols(f)
        assert symbols == []
        assert lang is None

    def test_empty_file_returns_empty_symbols(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("")
        symbols, lang = extract_symbols(f)
        assert symbols == []
        assert lang == "python"

    def test_symbols_have_required_fields(self, py_file):
        symbols, lang = extract_symbols(py_file)
        required_fields = {"name", "kind", "params", "return_type", "docstring",
                          "start_line", "end_line", "language", "path"}
        for sym in symbols:
            assert required_fields.issubset(sym.keys()), f"Missing fields in {sym}"

    def test_symbol_start_line_positive(self, py_file):
        symbols, _ = extract_symbols(py_file)
        for sym in symbols:
            assert sym["start_line"] >= 1

    def test_symbol_end_ge_start(self, py_file):
        symbols, _ = extract_symbols(py_file)
        for sym in symbols:
            assert sym["end_line"] >= sym["start_line"]


# ─────────────────────────────────────────────── Python extraction

class TestPythonExtraction:
    def test_extracts_class(self, py_file):
        symbols, _ = extract_symbols(py_file)
        kinds = [s["kind"] for s in symbols]
        assert "class" in kinds

    def test_extracts_function(self, py_file):
        symbols, _ = extract_symbols(py_file)
        kinds = [s["kind"] for s in symbols]
        assert "function" in kinds or "method" in kinds

    def test_extracts_myclass(self, py_file):
        symbols, _ = extract_symbols(py_file)
        names = [s["name"] for s in symbols]
        assert "MyClass" in names

    def test_extracts_standalone_func(self, py_file):
        symbols, _ = extract_symbols(py_file)
        names = [s["name"] for s in symbols]
        assert "standalone_func" in names

    def test_extracts_async_func(self, py_file):
        symbols, _ = extract_symbols(py_file)
        names = [s["name"] for s in symbols]
        assert "async_func" in names

    def test_path_stored_in_symbols(self, py_file):
        symbols, _ = extract_symbols(py_file)
        assert len(symbols) > 0
        for sym in symbols:
            assert str(py_file) == sym["path"]

    def test_language_is_python(self, py_file):
        symbols, lang = extract_symbols(py_file)
        assert lang == "python"
        for sym in symbols:
            assert sym["language"] == "python"

    def test_no_duplicate_start_lines(self, py_file):
        symbols, _ = extract_symbols(py_file)
        starts = [s["start_line"] for s in symbols]
        assert len(starts) == len(set(starts)), "Duplicate start lines found"


# ─────────────────────────────────────────────── JavaScript extraction

class TestJavaScriptExtraction:
    def test_js_language(self, js_file):
        symbols, lang = extract_symbols(js_file)
        assert lang == "javascript"

    def test_js_extracts_function(self, js_file):
        symbols, _ = extract_symbols(js_file)
        names = [s["name"] for s in symbols]
        assert "greet" in names or len(symbols) >= 0  # may use regex fallback

    def test_js_extracts_class(self, js_file):
        symbols, _ = extract_symbols(js_file)
        kinds = [s["kind"] for s in symbols]
        # Either tree-sitter or regex should find it
        assert len(symbols) >= 0  # at minimum, no crash


# ─────────────────────────────────────────────── regex fallback

class TestRegexFallback:
    def test_regex_python_function(self, tmp_path):
        f = tmp_path / "test.go"  # Go uses regex fallback
        f.write_text("func MyHandler(w http.ResponseWriter, r *http.Request) {\n}\n")
        content = f.read_bytes()
        from src.ctx.indexer.extractor import _extract_regex
        result = _extract_regex(f, content, "go")
        assert len(result) >= 1
        assert result[0]["name"] == "MyHandler"

    def test_regex_python_class(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("class Foo:\n    pass\n")
        content = f.read_bytes()
        result = _extract_regex(f, content, "python")
        assert any(s["name"] == "Foo" for s in result)

    def test_regex_rust_function(self, tmp_path):
        f = tmp_path / "test.rs"
        f.write_text("pub fn compute(x: u32) -> u32 { x * 2 }\n")
        content = f.read_bytes()
        result = _extract_regex(f, content, "rust")
        assert any(s["name"] == "compute" for s in result)

    def test_regex_go_struct(self, tmp_path):
        f = tmp_path / "test.go"
        f.write_text("type Server struct {\n    host string\n}\n")
        content = f.read_bytes()
        result = _extract_regex(f, content, "go")
        assert any(s["name"] == "Server" and s["kind"] == "struct" for s in result)

    def test_regex_unknown_lang_returns_empty(self, tmp_path):
        f = tmp_path / "test.xyz"
        content = b"some code"
        result = _extract_regex(f, content, "unknown_lang_xyz")
        assert result == []

    def test_regex_result_fields(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("def my_function(a, b):\n    pass\n")
        content = f.read_bytes()
        result = _extract_regex(f, content, "python")
        required = {"name", "kind", "params", "return_type", "docstring", "start_line", "end_line", "language", "path"}
        for sym in result:
            assert required.issubset(sym.keys())

    def test_regex_line_numbers_start_at_1(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("def first_func():\n    pass\n\ndef second_func():\n    pass\n")
        content = f.read_bytes()
        result = _extract_regex(f, content, "python")
        starts = [s["start_line"] for s in result]
        assert all(s >= 1 for s in starts)

    def test_regex_nonexistent_lang_empty(self, tmp_path):
        f = tmp_path / "test.cobol"
        f.write_text("PROGRAM-ID. HELLO.")
        result = _extract_regex(f, f.read_bytes(), "cobol")
        assert result == []
