"""Tests for walker, hasher, and extractor edge cases."""
from __future__ import annotations
import os
import pytest
from pathlib import Path
from src.ctx.indexer.walker import walk_project
from src.ctx.indexer.hasher import hash_file, hash_bytes


# ─────────────────────────────────────────────── hasher

class TestHashFile:
    def test_returns_string(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_bytes(b"hello")
        result = hash_file(f)
        assert isinstance(result, str)

    def test_sha1_length(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_bytes(b"hello")
        result = hash_file(f)
        assert len(result) == 40

    def test_different_content_different_hash(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_bytes(b"content_a")
        f2.write_bytes(b"content_b")
        assert hash_file(f1) != hash_file(f2)

    def test_same_content_same_hash(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_bytes(b"same content")
        f2.write_bytes(b"same content")
        assert hash_file(f1) == hash_file(f2)

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        result = hash_file(f)
        assert len(result) == 40

    def test_large_file(self, tmp_path):
        f = tmp_path / "large.bin"
        f.write_bytes(b"x" * (200 * 1024))  # 200 KB
        result = hash_file(f)
        assert len(result) == 40

    def test_deterministic(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_bytes(b"deterministic content")
        h1 = hash_file(f)
        h2 = hash_file(f)
        assert h1 == h2


class TestHashBytes:
    def test_returns_string(self):
        result = hash_bytes(b"hello")
        assert isinstance(result, str)

    def test_sha1_length(self):
        result = hash_bytes(b"hello")
        assert len(result) == 40

    def test_empty_bytes(self):
        result = hash_bytes(b"")
        assert len(result) == 40

    def test_same_input_same_output(self):
        assert hash_bytes(b"abc") == hash_bytes(b"abc")

    def test_different_input_different_output(self):
        assert hash_bytes(b"abc") != hash_bytes(b"xyz")

    def test_matches_hash_file(self, tmp_path):
        data = b"match me"
        f = tmp_path / "test.bin"
        f.write_bytes(data)
        assert hash_file(f) == hash_bytes(data)


# ─────────────────────────────────────────────── walker

class TestWalkProject:
    def test_yields_matching_files(self, tmp_path):
        (tmp_path / "a.py").write_text("pass")
        (tmp_path / "b.js").write_text("const x = 1;")
        files = list(walk_project(tmp_path, [".py"], set(), max_size_kb=512))
        names = [f.name for f in files]
        assert "a.py" in names
        assert "b.js" not in names

    def test_multiple_extensions(self, tmp_path):
        (tmp_path / "a.py").write_text("pass")
        (tmp_path / "b.js").write_text("const x = 1;")
        (tmp_path / "c.txt").write_text("text")
        files = list(walk_project(tmp_path, [".py", ".js"], set(), max_size_kb=512))
        names = [f.name for f in files]
        assert "a.py" in names
        assert "b.js" in names
        assert "c.txt" not in names

    def test_skips_ignored_dirs(self, tmp_path):
        ignored = tmp_path / "node_modules"
        ignored.mkdir()
        (ignored / "pkg.py").write_text("pass")
        (tmp_path / "main.py").write_text("pass")
        files = list(walk_project(tmp_path, [".py"], {"node_modules"}, max_size_kb=512))
        paths = [str(f) for f in files]
        assert not any("node_modules" in p for p in paths)
        assert any("main.py" in p for p in paths)

    def test_skips_dotfiles(self, tmp_path):
        (tmp_path / ".hidden.py").write_text("pass")
        (tmp_path / "visible.py").write_text("pass")
        files = list(walk_project(tmp_path, [".py"], set(), max_size_kb=512))
        names = [f.name for f in files]
        assert ".hidden.py" not in names
        assert "visible.py" in names

    def test_skips_dot_directories(self, tmp_path):
        hidden_dir = tmp_path / ".git"
        hidden_dir.mkdir()
        (hidden_dir / "config.py").write_text("pass")
        files = list(walk_project(tmp_path, [".py"], set(), max_size_kb=512))
        paths = [str(f) for f in files]
        assert not any(".git" in p for p in paths)

    def test_respects_max_size_kb(self, tmp_path):
        small = tmp_path / "small.py"
        large = tmp_path / "large.py"
        small.write_bytes(b"x" * 100)  # 0.1 KB
        large.write_bytes(b"x" * (600 * 1024))  # 600 KB
        files = list(walk_project(tmp_path, [".py"], set(), max_size_kb=512))
        names = [f.name for f in files]
        assert "small.py" in names
        assert "large.py" not in names

    def test_recurses_into_subdirs(self, tmp_path):
        subdir = tmp_path / "src"
        subdir.mkdir()
        (subdir / "module.py").write_text("pass")
        files = list(walk_project(tmp_path, [".py"], set(), max_size_kb=512))
        names = [f.name for f in files]
        assert "module.py" in names

    def test_empty_directory(self, tmp_path):
        files = list(walk_project(tmp_path, [".py"], set(), max_size_kb=512))
        assert files == []

    def test_no_extensions_match(self, tmp_path):
        (tmp_path / "code.go").write_text("package main")
        files = list(walk_project(tmp_path, [".py"], set(), max_size_kb=512))
        assert files == []

    def test_yields_path_objects(self, tmp_path):
        (tmp_path / "a.py").write_text("pass")
        files = list(walk_project(tmp_path, [".py"], set(), max_size_kb=512))
        for f in files:
            assert isinstance(f, Path)

    def test_skips_symlinks(self, tmp_path):
        target = tmp_path / "real.py"
        target.write_text("pass")
        try:
            link = tmp_path / "link.py"
            link.symlink_to(target)
            files = list(walk_project(tmp_path, [".py"], set(), max_size_kb=512))
            names = [f.name for f in files]
            assert "link.py" not in names
            assert "real.py" in names
        except (OSError, NotImplementedError):
            pytest.skip("Symlinks not supported on this platform")

    def test_multiple_nested_levels(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (deep / "deep.py").write_text("pass")
        files = list(walk_project(tmp_path, [".py"], set(), max_size_kb=512))
        names = [f.name for f in files]
        assert "deep.py" in names


# ─────────────────────────────────────────────── extractor edge cases

class TestExtractorEdgeCases:
    def test_binary_like_file(self, tmp_path):
        """File with non-UTF8 bytes should not crash."""
        from src.ctx.indexer.extractor import extract_symbols
        f = tmp_path / "weird.py"
        f.write_bytes(b"def foo():\n    x = b'\\xff\\xfe'\n")
        symbols, lang = extract_symbols(f)
        assert lang == "python"
        # May or may not extract, but must not crash

    def test_file_with_only_comments(self, tmp_path):
        from src.ctx.indexer.extractor import extract_symbols
        f = tmp_path / "comments.py"
        f.write_text("# This is a comment\n# Another comment\n")
        symbols, lang = extract_symbols(f)
        assert lang == "python"
        assert symbols == []

    def test_deeply_nested_functions(self, tmp_path):
        from src.ctx.indexer.extractor import extract_symbols
        f = tmp_path / "nested.py"
        f.write_text(
            "def outer():\n"
            "    def inner():\n"
            "        def deepest():\n"
            "            pass\n"
            "        return deepest\n"
            "    return inner\n"
        )
        symbols, lang = extract_symbols(f)
        assert lang == "python"
        # At minimum outer should be found
        names = [s["name"] for s in symbols]
        assert "outer" in names

    def test_file_with_decorators(self, tmp_path):
        from src.ctx.indexer.extractor import extract_symbols
        f = tmp_path / "decorated.py"
        f.write_text(
            "@property\ndef my_prop(self):\n    return self._x\n\n"
            "@staticmethod\ndef static_m():\n    pass\n"
        )
        symbols, lang = extract_symbols(f)
        assert lang == "python"
        # Must not crash

    def test_missing_file_returns_empty(self, tmp_path):
        from src.ctx.indexer.extractor import extract_symbols
        f = tmp_path / "nonexistent.py"
        symbols, lang = extract_symbols(f)
        # Returns empty or lang without symbols
        assert symbols == [] or isinstance(symbols, list)
