"""
Symbol and file search — FTS5 + structural fallback.
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any

# Palavras comuns que poluem buscas de código
_STOP_WORDS = {
    "a", "an", "the", "in", "on", "at", "to", "for", "of", "and", "or",
    "is", "are", "was", "be", "been", "have", "has", "do", "does", "did",
    "will", "would", "could", "should", "may", "might", "not", "no",
    "fix", "bug", "error", "issue", "problem", "why", "how", "what",
    "where", "when", "find", "show", "list", "get", "set", "add",
    "create", "update", "delete", "return", "returns", "returning",
    "file", "files", "function", "method", "module", "code",
    "write", "read", "make", "this", "that", "with", "from",
    # test/* identifiers como "tests" polui resultados com arquivos de teste
    "test", "tests",
}

# Identifiers: snake_case ou camelCase ou UPPER — extrair separado com peso maior
_IDENT_RE = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]{2,}\b")


def _build_fts_query(query: str) -> str:
    """
    Extrai termos relevantes da query para FTS5.
    Prioriza identificadores, filtra stop words e palavras curtas.
    """
    words = _IDENT_RE.findall(query)
    # Separar identificadores técnicos (contêm _ ou são camelCase) dos demais
    tech = [w for w in words if "_" in w or (w[0].islower() and any(c.isupper() for c in w))]
    natural = [w for w in words if w.lower() not in _STOP_WORDS and len(w) >= 4 and w not in tech]

    # Técnicos têm peso maior (busca exata + prefixo), naturais só prefixo
    terms = [f'"{w}"' for w in tech] + [f"{w}*" for w in natural]

    if not terms:
        # Fallback: usar todas as palavras longas
        terms = [f"{w}*" for w in words if len(w) >= 3]

    return " OR ".join(terms) if terms else ""


def search_symbols(store: Any, query: str, limit: int = 30) -> list[Any]:
    """FTS5 search com filtragem de stop words e fallback LIKE."""
    fts_query = _build_fts_query(query)
    if not fts_query:
        return []

    try:
        results = store.search_symbols_fts(fts_query, limit=limit)
        if results:
            return results
    except Exception as _fts_err:
        import warnings
        warnings.warn(f"FTS5 query failed ({_fts_err!r}), falling back to LIKE search", stacklevel=2)

    # Fallback: LIKE nos termos técnicos ou na query completa
    conn = store._conn
    tech_words = [w for w in _IDENT_RE.findall(query) if "_" in w or len(w) >= 5]
    if tech_words:
        like = f"%{tech_words[0]}%"
    else:
        like = f"%{query[:30]}%"
    return conn.execute(
        "SELECT * FROM symbols WHERE name LIKE ? OR docstring LIKE ? LIMIT ?",
        (like, like, limit),
    ).fetchall()


def find_related_paths(
    store: Any,
    symbols: list[Any],
    max_paths: int = 5,
    prefer_source: bool = True,
) -> list[str]:
    """Return unique file paths from a symbol list, most frequent first.

    Test files (tests/ prefix or test_ filename) get a 0.4x weight penalty
    so source files are preferred when match counts are similar.
    """
    from collections import Counter
    counts: Counter = Counter()
    for s in symbols:
        path = s["path"]
        weight = 1.0
        if prefer_source:
            name = path.replace("\\", "/")
            if name.startswith("tests/") or "/test_" in name or name.startswith("test_"):
                weight = 0.25
        counts[path] += weight
    return [p for p, _ in counts.most_common(max_paths)]


def find_callers(
    store: Any,
    symbol_name: str,
    root: Path,
    max_files: int = 60,
    max_results: int = 5,
) -> list[str]:
    """
    Caller finder: grep for symbol_name in indexed source files.
    Caps at max_files to stay fast on large projects.
    Returns list of paths containing the name (max max_results).
    """
    results = []
    pattern = re.compile(r"\b" + re.escape(symbol_name) + r"\b")
    indexed_paths = store.list_indexed_paths(limit=max_files)
    for p_str in indexed_paths:
        if len(results) >= max_results:
            break
        p = Path(p_str)
        if not p.exists():
            p = root / p_str
        if not p.exists():
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
            if pattern.search(content):
                results.append(p_str)
        except OSError:
            pass
    return results


def find_imported_paths(file_path: str, root: Path, indexed_paths: set[str]) -> list[str]:
    """
    Parse import statements in a Python/JS file and map them to indexed paths.
    Returns paths that are imported by this file and exist in the index.
    """
    p = Path(file_path)
    if not p.exists():
        p = root / file_path
    if not p.exists():
        return []

    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    found = []
    # Python: from .module import X  |  from ctx.db import store  |  import ctx.db.store
    py_imports = re.findall(
        r"^\s*(?:from|import)\s+([\w\.]+)", content, re.MULTILINE
    )
    for mod in py_imports:
        # converte ctx.db.store -> src/ctx/db/store.py
        mod_path = mod.replace(".", "/")
        candidates = [
            f"src/{mod_path}.py",
            f"{mod_path}.py",
            f"src/{mod_path}/__init__.py",
        ]
        for c in candidates:
            if c in indexed_paths:
                found.append(c)
                break

    # JS/TS: import ... from './path'  |  require('./path')
    js_imports = re.findall(r"""(?:from|require)\s+['"](\./[^'"]+)['"]""", content)
    try:
        base_dir = str(p.parent.relative_to(root)).replace("\\", "/")
    except ValueError:
        base_dir = str(p.parent).replace("\\", "/")
    for imp in js_imports:
        clean = imp.lstrip("./")
        for ext in [".ts", ".tsx", ".js", ".jsx"]:
            candidate = f"{base_dir}/{clean}{ext}".lstrip("/")
            if candidate in indexed_paths:
                found.append(candidate)
                break

    return list(dict.fromkeys(found))  # deduplica mantendo ordem


def expand_paths_cross_file(
    store: Any,
    root: Path,
    relevant_paths: list[str],
    top_symbols: list[Any],
    max_expand: int = 3,
) -> list[str]:
    """
    Expande relevant_paths com arquivos relacionados via imports e callers.
    Usado em bugfix/refactor para capturar bugs que cruzam múltiplos arquivos.
    """
    indexed = set(store.list_indexed_paths())
    expanded = list(relevant_paths)
    seen = set(relevant_paths)

    # 1. Arquivos importados pelo arquivo top (dependências diretas)
    if relevant_paths:
        for imp_path in find_imported_paths(relevant_paths[0], root, indexed):
            if imp_path not in seen:
                expanded.append(imp_path)
                seen.add(imp_path)
                if len(expanded) - len(relevant_paths) >= max_expand:
                    break

    # 2. Callers do símbolo mais relevante (quem chama essa função)
    if top_symbols:
        top_sym = top_symbols[0]["name"]
        for caller_path in find_callers(store, top_sym, root):
            if caller_path not in seen:
                expanded.append(caller_path)
                seen.add(caller_path)
                if len(expanded) - len(relevant_paths) >= max_expand:
                    break

    return expanded
