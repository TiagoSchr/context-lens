"""
Extrator de símbolos via Tree-sitter (API 0.25+ com QueryCursor).

Extrai assinaturas canônicas level1: name, kind, params, return_type, docstring.
Suporta Python e JavaScript/TypeScript nativamente.
Fallback regex para outras linguagens quando tree-sitter não está disponível.
"""
from __future__ import annotations
import re
import warnings
from pathlib import Path
from typing import Any

from .parser import get_language, parse_file


# ─────────────────────────────────────────────── queries tree-sitter

PYTHON_QUERY = """
(function_definition) @function
(class_definition) @class
(decorated_definition
  definition: (function_definition)) @decorated_function
"""

JS_QUERY = """
(function_declaration) @function
(class_declaration) @class
(method_definition) @method
"""

LANG_QUERIES: dict[str, str] = {
    "python":     PYTHON_QUERY,
    "javascript": JS_QUERY,
    "typescript": JS_QUERY,
    "tsx":        JS_QUERY,
}

# ─────────────────────────────────────────────── helpers tree-sitter

def _text(node: Any) -> str:
    """Retorna o texto de um nó como str."""
    raw = node.text
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw) if raw else ""


def _get_docstring_python(body_node: Any) -> str | None:
    """Extrai docstring do primeiro statement de um body Python."""
    if body_node is None:
        return None
    for child in body_node.children:
        if child.type == "expression_statement":
            for sub in child.children:
                if sub.type in ("string", "concatenated_string"):
                    raw = _text(sub).strip()
                    for q in ('"""', "'''", '"', "'"):
                        if raw.startswith(q) and raw.endswith(q) and len(raw) > len(q) * 2:
                            raw = raw[len(q):-len(q)]
                            break
                    return raw.strip()[:300]
        break
    return None


def _run_query(lang: Any, query_src: str, root_node: Any) -> dict[str, list[Any]]:
    """Executa query e retorna dict[capture_name → [nodes]]."""
    from tree_sitter import QueryCursor
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        q = lang.query(query_src)
    cursor = QueryCursor(q)
    return cursor.captures(root_node)


def _extract_ts(path: Path, tree: Any, lang_name: str) -> list[dict]:
    """Extração via tree-sitter QueryCursor."""
    query_src = LANG_QUERIES.get(lang_name)
    if not query_src:
        return []

    lang = get_language(lang_name)
    if lang is None:
        return []

    try:
        captures: dict[str, list[Any]] = _run_query(lang, query_src, tree.root_node)
    except Exception:
        return []

    results: list[dict] = []
    path_str = str(path)
    seen_starts: set[int] = set()

    KIND_MAP = {
        "function":           "function",
        "class":              "class",
        "method":             "method",
        "decorated_function": "function",
    }

    for cap_name, nodes in captures.items():
        kind = KIND_MAP.get(cap_name)
        if kind is None:
            continue

        for node in nodes:
            start_line = node.start_point[0] + 1
            if start_line in seen_starts:
                continue
            seen_starts.add(start_line)

            # Nome do símbolo
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            name = _text(name_node)
            if not name:
                continue

            params = None
            return_type = None
            docstring = None

            if lang_name == "python":
                params_node = node.child_by_field_name("parameters")
                params = _text(params_node) if params_node else None
                rt_node = node.child_by_field_name("return_type")
                if rt_node:
                    return_type = _text(rt_node).lstrip("->:").strip() or None
                body = node.child_by_field_name("body")
                docstring = _get_docstring_python(body)
            else:
                params_node = node.child_by_field_name("parameters")
                params = _text(params_node) if params_node else None

            results.append({
                "name":        name,
                "kind":        kind,
                "params":      params,
                "return_type": return_type,
                "docstring":   docstring,
                "start_line":  start_line,
                "end_line":    node.end_point[0] + 1,
                "language":    lang_name,
                "path":        path_str,
            })

    return results


def _extract_arrow_vars(path: Path, tree: Any, lang_name: str) -> list[dict]:
    """Extrai arrow functions atribuídas a variáveis (const foo = () => ...) em JS/TS."""
    lang = get_language(lang_name)
    if lang is None:
        return []

    ARROW_QUERY = """
(lexical_declaration
  (variable_declarator
    name: (identifier) @var_name
    value: (arrow_function) @arrow))
"""
    try:
        caps = _run_query(lang, ARROW_QUERY, tree.root_node)
    except Exception:
        return []

    results = []
    path_str = str(path)
    var_names = caps.get("var_name", [])
    arrows = caps.get("arrow", [])

    for name_node, arrow_node in zip(var_names, arrows):
        name = _text(name_node)
        if not name:
            continue
        params_node = (
            arrow_node.child_by_field_name("parameters")
            or arrow_node.child_by_field_name("parameter")
        )
        params = _text(params_node) if params_node else None
        results.append({
            "name":        name,
            "kind":        "function",
            "params":      params,
            "return_type": None,
            "docstring":   None,
            "start_line":  name_node.start_point[0] + 1,
            "end_line":    arrow_node.end_point[0] + 1,
            "language":    lang_name,
            "path":        path_str,
        })
    return results


# ─────────────────────────────────────────────── regex fallback

_REGEX_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "python": [
        (r"^(?:async\s+)?def\s+(\w+)\s*(\([^)]*\))", "function"),
        (r"^class\s+(\w+)", "class"),
    ],
    "javascript": [
        (r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*(\([^)]*\))", "function"),
        (r"^(?:export\s+)?class\s+(\w+)", "class"),
        (r"^\s{2,4}(?:async\s+)?(\w+)\s*\([^)]*\)\s*\{", "method"),
    ],
    "typescript": [
        (r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*(\([^)]*\))", "function"),
        (r"^(?:export\s+)?(?:abstract\s+)?class\s+(\w+)", "class"),
        (r"^(?:export\s+)?interface\s+(\w+)", "interface"),
    ],
    "go": [
        (r"^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*(\([^)]*\))", "function"),
        (r"^type\s+(\w+)\s+struct", "struct"),
        (r"^type\s+(\w+)\s+interface", "interface"),
    ],
    "rust": [
        (r"^(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*(\([^)]*\))", "function"),
        (r"^(?:pub\s+)?struct\s+(\w+)", "struct"),
        (r"^(?:pub\s+)?(?:trait|impl)\s+(\w+)", "class"),
    ],
    "java": [
        (r"(?:public|private|protected|static|\s)+[\w<>\[\]]+\s+(\w+)\s*\(", "function"),
        (r"^(?:public\s+)?(?:abstract\s+)?class\s+(\w+)", "class"),
        (r"^(?:public\s+)?interface\s+(\w+)", "interface"),
    ],
    "c": [
        (r"^[\w\s\*]+\s+(\w+)\s*\(", "function"),
    ],
    "cpp": [
        (r"^(?:[\w:&\*<>]+\s+)+(\w+)\s*\(", "function"),
        (r"^(?:class|struct)\s+(\w+)", "class"),
    ],
}


def _extract_regex(path: Path, source: bytes, lang_name: str) -> list[dict]:
    patterns = _REGEX_PATTERNS.get(lang_name, [])
    if not patterns:
        return []

    results = []
    path_str = str(path)
    lines = source.decode("utf-8", errors="replace").splitlines()

    for lineno, line in enumerate(lines, start=1):
        for pattern, kind in patterns:
            m = re.match(pattern, line)
            if m:
                name = m.group(1)
                params = m.group(2) if m.lastindex and m.lastindex >= 2 else None
                results.append({
                    "name":        name,
                    "kind":        kind,
                    "params":      params,
                    "return_type": None,
                    "docstring":   None,
                    "start_line":  lineno,
                    "end_line":    lineno,
                    "language":    lang_name,
                    "path":        path_str,
                })
                break

    return results


# ─────────────────────────────────────────────── API pública

def extract_symbols(path: Path) -> tuple[list[dict], str | None]:
    """
    Extrai símbolos de um arquivo.
    Retorna (symbols, language_name).
    Cada símbolo é um dict compatível com a tabela `symbols` (sem file_id).
    """
    tree, source, lang_name = parse_file(path)

    if not lang_name:
        return [], None

    if not source:
        return [], lang_name

    if tree is not None and not tree.root_node.has_error:
        symbols = _extract_ts(path, tree, lang_name)
        # Para JS/TS, complementar com arrow functions
        if lang_name in ("javascript", "typescript", "tsx"):
            existing_lines = {s["start_line"] for s in symbols}
            for s in _extract_arrow_vars(path, tree, lang_name):
                if s["start_line"] not in existing_lines:
                    symbols.append(s)
        if symbols:
            return symbols, lang_name

    # Fallback regex
    return _extract_regex(path, source, lang_name), lang_name
