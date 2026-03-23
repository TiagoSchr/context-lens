"""
Tree-sitter parser pool — API tree-sitter >= 0.22 (QueryCursor).

Grammars carregadas lazy por linguagem e reutilizadas.
Fallback gracioso se os pacotes não estiverem instalados.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any

_parsers: dict[str, Any] = {}   # lang_name → tree_sitter.Parser
_languages: dict[str, Any] = {} # lang_name → tree_sitter.Language

# Extensão → nome interno de linguagem
EXT_TO_LANG: dict[str, str] = {
    ".py":    "python",
    ".js":    "javascript",
    ".jsx":   "javascript",
    ".ts":    "typescript",
    ".tsx":   "tsx",
    ".go":    "go",
    ".rs":    "rust",
    ".java":  "java",
    ".c":     "c",
    ".cpp":   "cpp",
    ".h":     "c",
    ".rb":    "ruby",
    ".cs":    "c_sharp",
    ".kt":    "kotlin",
    ".swift": "swift",
    ".php":   "php",
}

# Mapa: nome interno → função que retorna o capsule de linguagem
def _make_lang_loaders() -> dict[str, Any]:
    loaders: dict[str, Any] = {}
    try:
        import tree_sitter_python as _m
        loaders["python"] = _m.language
    except ImportError:
        pass
    try:
        import tree_sitter_javascript as _m
        loaders["javascript"] = _m.language
    except ImportError:
        pass
    try:
        import tree_sitter_typescript as _m
        loaders["typescript"] = _m.language_typescript
        loaders["tsx"] = _m.language_tsx
    except ImportError:
        pass
    try:
        import tree_sitter_go as _m
        loaders["go"] = _m.language
    except ImportError:
        pass
    try:
        import tree_sitter_rust as _m
        loaders["rust"] = _m.language
    except ImportError:
        pass
    try:
        import tree_sitter_java as _m
        loaders["java"] = _m.language
    except ImportError:
        pass
    try:
        import tree_sitter_c as _m
        loaders["c"] = _m.language
    except ImportError:
        pass
    try:
        import tree_sitter_cpp as _m
        loaders["cpp"] = _m.language
    except ImportError:
        pass
    return loaders

_LANG_LOADERS: dict[str, Any] | None = None


def _get_loaders() -> dict[str, Any]:
    global _LANG_LOADERS
    if _LANG_LOADERS is None:
        _LANG_LOADERS = _make_lang_loaders()
    return _LANG_LOADERS


def get_language(lang_name: str) -> Any | None:
    """Retorna tree_sitter.Language ou None se não disponível."""
    if lang_name in _languages:
        return _languages[lang_name]
    loaders = _get_loaders()
    loader = loaders.get(lang_name)
    if loader is None:
        _languages[lang_name] = None
        return None
    try:
        from tree_sitter import Language
        lang = Language(loader())
        _languages[lang_name] = lang
        return lang
    except Exception:
        _languages[lang_name] = None
        return None


def get_parser(lang_name: str) -> Any | None:
    """Retorna tree_sitter.Parser configurado ou None."""
    if lang_name in _parsers:
        return _parsers[lang_name]
    lang = get_language(lang_name)
    if lang is None:
        _parsers[lang_name] = None
        return None
    try:
        from tree_sitter import Parser
        parser = Parser(lang)
        _parsers[lang_name] = parser
        return parser
    except Exception:
        _parsers[lang_name] = None
        return None


def lang_for_path(path: Path) -> str | None:
    return EXT_TO_LANG.get(path.suffix)


def parse_file(path: Path) -> tuple[Any | None, bytes, str | None]:
    """
    Retorna (tree, source_bytes, language_name).
    tree é None se parsing não disponível ou falha.
    """
    lang_name = lang_for_path(path)
    if not lang_name:
        return None, b"", None

    try:
        source = path.read_bytes()
    except OSError:
        return None, b"", lang_name

    parser = get_parser(lang_name)
    if parser is None:
        return None, source, lang_name

    try:
        tree = parser.parse(source)
        return tree, source, lang_name
    except Exception:
        return None, source, lang_name


def is_available(lang_name: str) -> bool:
    """Retorna True se o grammar para a linguagem está instalado."""
    return get_language(lang_name) is not None
