"""
Context Lens — MCP Server v2 (stdio transport).

Exposes 8 tools + 4 resources to AI coding assistants via Model Context Protocol:

  TOOLS (primary use: call lens_context BEFORE reading any files)
  ──────────────────────────────────────────────────────────────
  lens_search(query, limit)              — FTS5 symbol search
  lens_context(query, task, budget)      — ⭐ assemble optimised context (use first!)
  lens_status()                          — index stats + token economy
  lens_symbols(path)                     — all symbols in a specific file
  lens_explain_symbol(name)              — deep dive on one symbol + callers
  lens_diff_context(query)               — context focused on git-changed files
  lens_reindex()                         — trigger incremental reindex
  lens_memory(action, kind, key, value)  — CRUD memory entries

  RESOURCES
  ─────────
  lens://project/map     — project structure (level0)
  lens://project/stats   — index statistics (JSON)
  lens://symbols/{path}  — symbols for a specific file
  lens://memory          — all memory entries

Usage:
    lens-mcp                      # stdio transport (default)

Auto-detection: add .claude/mcp.json, .cursor/mcp.json, etc. to project root.
Run: lens install --global         to configure all installed IDEs at once.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# ── Windows: force UTF-8 on stdin/stdout/stderr ───────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types


# ── MCP server instructions ───────────────────────────────────────────────────
# Sent during MCP handshake — the AI client includes these in its system prompt.

_MCP_INSTRUCTIONS = """\
You have access to Context Lens, a token-optimization engine for this project.

MANDATORY WORKFLOW — follow for EVERY task:
1. BEFORE reading any file, call `lens_context` with a description of your task.
   It returns an optimized context block (75-95% smaller than raw files).
2. Use the returned context as your PRIMARY information source.
3. Only use file-reading tools (Read, Grep, Glob) for specific files NOT covered
   by the context block.

WHY: Reading files directly consumes 5-20x more tokens than necessary.
     lens_context returns the same relevant code, pre-ranked and trimmed.

AVAILABLE TOOLS (in order of importance):
  • lens_context(query, task, budget) — ⭐ ALWAYS call first
  • lens_search(query)               — find symbols by name
  • lens_status()                    — check index health
  • lens_diff_context(query)         — context for git-changed files
  • lens_symbols(path)               — symbols in a specific file
  • lens_explain_symbol(name)        — deep dive on one symbol
  • lens_reindex()                   — trigger re-index if stale

If the index doesn't exist yet, call lens_reindex() first.
"""

# ── MCP server instance ───────────────────────────────────────────────────────

app = Server("context-lens", instructions=_MCP_INSTRUCTIONS)

# ── Lazy store initialisation ─────────────────────────────────────────────────
# We do NOT import heavy modules at module level — only when a tool is called.

_store: Any = None
_cfg: dict = {}
_root: Path | None = None
_log_path: Path | None = None
_session_id: int | None = None
_cli_tool_override: str | None = None  # set via --tool flag


def _init_store() -> tuple[Any, dict, Path, Path]:
    """Lazy-init: open the SQLite store and config once, then reuse."""
    global _store, _cfg, _root, _log_path, _session_id

    if _store is not None:
        try:
            if _root is not None:
                _update_session_tool(_root, _detect_tool(0))
        except Exception:
            pass
        return _store, _cfg, _root, _log_path  # type: ignore[return-value]

    from .config import find_project_root, db_path, log_path, load_config, ctx_dir
    from .db.schema import init_db
    from .db.store import Store
    from .errors import IndexNotFound, IndexCorrupted

    root = find_project_root() or Path.cwd()
    dp = db_path(root)

    if not dp.exists():
        raise IndexNotFound(str(dp))

    try:
        conn = init_db(dp)
    except Exception as exc:
        raise IndexCorrupted(str(exc)) from exc

    _store = Store(conn)
    _cfg = load_config(root)
    _root = root
    _log_path = log_path(root)

    # Create a new session for this MCP server instance (skip if resuming after reindex)
    if _session_id is None:
        try:
            n = _store.session_count() + 1
            name = f"{root.name} #{n}"
            _session_id = _store.create_session(name)
            tool = _detect_tool(0)
            _write_session_json(ctx_dir(root), _session_id, name, tool)
        except Exception:
            pass
    else:
        try:
            _update_session_tool(root, _detect_tool(0))
        except Exception:
            pass

    return _store, _cfg, _root, _log_path


def _write_session_json(ctx: Path, sid: int, name: str, tool: str = "unknown") -> None:
    """Write .ctx/session.json so the VS Code extension knows the active session."""
    import time as _t
    import os as _os
    session_file = ctx / "session.json"
    data = {"id": sid, "name": name, "started_at": _t.time(), "pid": _os.getpid(), "tool": tool}
    try:
        with open(session_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def _reset_store() -> None:
    """Clear cached store — call after reindex so next call re-opens.

    Preserves _session_id so the same MCP server keeps its session.
    """
    global _store, _cfg, _root, _log_path
    _store = None
    _cfg = {}
    _root = None
    _log_path = None


def _update_session_tool(root: Path, tool: str) -> None:
    """Update the tool field in .ctx/session.json so the extension knows which AI tool is active."""
    session_file = root / ".ctx" / "session.json"
    try:
        data: dict[str, Any] = {}
        if session_file.exists():
            loaded = json.loads(session_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        if _session_id is not None:
            data.setdefault("id", _session_id)
        data.setdefault("name", root.name if _session_id is None else f"{root.name} #{_session_id}")
        data.setdefault("started_at", time.time())
        data.setdefault("pid", os.getpid())
        if data.get("tool") == tool:
            return
        data["tool"] = tool
        session_file.parent.mkdir(parents=True, exist_ok=True)
        with open(session_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


# ── Tool/client detection ─────────────────────────────────────────────────────

def _capture_mcp_client_name() -> str | None:
    """Try to read the MCP client name from the current request context.

    The MCP protocol sends `clientInfo.name` during initialization.
    E.g.: 'claude-code', 'codex', 'vscode-copilot-chat', 'cursor'.

    We re-read on every call because a single MCP server process can be spawned
    once and proxy requests from multiple clients (e.g. Copilot acting as a
    proxy while the user chats with Codex/Claude in the same VS Code window).
    """
    try:
        ctx = app.request_context
        params = ctx.session.client_params
        if params and params.clientInfo:
            return params.clientInfo.name
    except Exception:
        pass
    return None


# Cache transcript-recency detection briefly so multiple tool calls inside the
# same retrieval round-trip don't re-walk the filesystem.
_transcript_tool_cache: tuple[float, str | None] = (0.0, None)
_TRANSCRIPT_CACHE_TTL = 3.0   # seconds
_TRANSCRIPT_MAX_AGE = 90.0    # seconds — how fresh a transcript must be to count


def _detect_active_tool_by_transcript() -> str | None:
    """Return 'codex' | 'claude' | 'copilot' based on most recent transcript mtime.

    Only considers transcripts updated within the last ~90s. Cached briefly.
    Returns None when nothing qualifies.
    """
    global _transcript_tool_cache
    now = time.time()
    ts, cached = _transcript_tool_cache
    if now - ts < _TRANSCRIPT_CACHE_TTL:
        return cached

    home = Path.home()
    best_mtime = 0.0
    best_tool: str | None = None

    def _consider(mtime: float, tool: str) -> None:
        nonlocal best_mtime, best_tool
        if mtime > best_mtime and (now - mtime) < _TRANSCRIPT_MAX_AGE:
            best_mtime = mtime
            best_tool = tool

    # Codex: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
    codex_root = home / ".codex" / "sessions"
    if codex_root.is_dir():
        # Limit scan to today + yesterday to keep this cheap.
        # Use localtime because Codex organises directories by local date.
        for delta in (0, 1):
            day = time.localtime(now - delta * 86400)
            day_dir = codex_root / f"{day.tm_year:04d}" / f"{day.tm_mon:02d}" / f"{day.tm_mday:02d}"
            if day_dir.is_dir():
                try:
                    for p in day_dir.iterdir():
                        if p.name.startswith("rollout-") and p.suffix == ".jsonl":
                            try:
                                _consider(p.stat().st_mtime, "codex")
                            except OSError:
                                pass
                except OSError:
                    pass

    # Claude: ~/.claude/projects/<sanitized-cwd>/*.jsonl
    claude_root = home / ".claude" / "projects"
    if claude_root.is_dir():
        try:
            for proj in claude_root.iterdir():
                if not proj.is_dir():
                    continue
                try:
                    for p in proj.iterdir():
                        if p.suffix == ".jsonl":
                            try:
                                _consider(p.stat().st_mtime, "claude")
                            except OSError:
                                pass
                except OSError:
                    pass
        except OSError:
            pass

    # Copilot: %APPDATA%/Code/User/workspaceStorage/*/GitHub.copilot-chat/**/*.jsonl
    copilot_roots: list[Path] = []
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            copilot_roots.append(Path(appdata) / "Code" / "User" / "workspaceStorage")
    elif sys.platform == "darwin":
        copilot_roots.append(home / "Library" / "Application Support" / "Code" / "User" / "workspaceStorage")
    else:
        copilot_roots.append(home / ".config" / "Code" / "User" / "workspaceStorage")

    for ws_root in copilot_roots:
        if not ws_root.is_dir():
            continue
        try:
            for hash_dir in ws_root.iterdir():
                copilot_dir = hash_dir / "GitHub.copilot-chat"
                if not copilot_dir.is_dir():
                    continue
                try:
                    for p in copilot_dir.rglob("*.jsonl"):
                        try:
                            _consider(p.stat().st_mtime, "copilot")
                        except OSError:
                            pass
                except OSError:
                    pass
        except OSError:
            pass

    _transcript_tool_cache = (now, best_tool)
    return best_tool


def _detect_tool(budget: int) -> str:
    """Detect which AI tool/client is calling the MCP server.

    Priority:
      1. --tool CLI flag
      2. Explicit env targets (CONTEXT_LENS_CLIENT / LENS_TARGET)
      3. Strong tool-specific env vars (CODEX_THREAD_ID, CLAUDE_CODE_*, ...)
      4. MCP client_info.name, but only if NOT 'copilot' (Copilot often proxies
         MCP requests on behalf of Codex/Claude in the same VS Code window).
      5. Transcript-recency heuristic (looks for the AI tool whose transcript
         was written most recently, within ~90s).
      6. Fallback: use MCP client_info even if 'copilot', then env default.
    """
    _ = budget
    from .config import detect_client_tool, normalize_target_name

    # 1. Explicit CLI override
    if _cli_tool_override:
        return _cli_tool_override

    # 2. Explicit env targets
    for env_var in ("CONTEXT_LENS_CLIENT", "LENS_TARGET"):
        explicit = normalize_target_name(os.environ.get(env_var))
        if explicit:
            return explicit

    # 3. Strong tool-specific env vars (Codex / Claude Code set these)
    if os.environ.get("CODEX_INTERNAL_ORIGINATOR_OVERRIDE"):
        return normalize_target_name(os.environ["CODEX_INTERNAL_ORIGINATOR_OVERRIDE"]) or "codex"
    if os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SANDBOX_ID"):
        return "codex"
    if os.environ.get("CLAUDE_CODE_SSE_PORT") or os.environ.get("CLAUDE_CODE_ENTRY_POINT"):
        return "claude"

    # 4. MCP client_info.name — trusted only if it's NOT Copilot
    client_name = _capture_mcp_client_name()
    mcp_normalized: str | None = None
    if client_name:
        mcp_normalized = normalize_target_name(client_name)
        if mcp_normalized and mcp_normalized not in ("unknown", "copilot"):
            return mcp_normalized

    # 5. Transcript-recency heuristic
    by_transcript = _detect_active_tool_by_transcript()
    if by_transcript:
        return by_transcript

    # 6. Fallbacks
    if mcp_normalized and mcp_normalized != "unknown":
        return mcp_normalized
    return detect_client_tool("unknown") or "unknown"


def _auto_budget(cfg: dict) -> int:
    """Return the right token budget for the detected tool."""
    detected = _detect_tool(0)
    target_budgets = cfg.get("target_budgets", {})
    if detected in target_budgets:
        return target_budgets[detected]
    return cfg.get("token_budget", 8000)


def _format_symbol(row: Any) -> dict:
    sig = f"[{row['kind']}] {row['name']}"
    if row["params"]:
        sig += row["params"]
    if row["return_type"]:
        sig += f" -> {row['return_type']}"
    return {
        "signature": sig,
        "path": row["path"],
        "line": row["start_line"],
        "kind": row["kind"],
        "name": row["name"],
        "docstring": (row["docstring"] or "")[:200],
    }


def _text(content: str) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=content)]


# ── Tool definitions ──────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="lens_search",
            description=(
                "Search indexed symbols (functions, classes, methods) by name or description "
                "using FTS5 full-text search. Returns signatures, file paths and line numbers."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Symbol name, keyword or natural language query."},
                    "limit": {"type": "integer", "description": "Max results (default 20).", "default": 20},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="lens_context",
            description=(
                "⭐ PRIMARY TOOL — Always call this BEFORE reading any files. "
                "Assembles an optimised context block for a given query within the configured "
                "token budget. Auto-detects task type or accepts explicit task. "
                "Saves 75-95% tokens vs reading files directly. "
                "Tasks: explain | bugfix | refactor | generate_test | navigate | "
                "document | optimize | security_review | auto."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The coding question or task description."},
                    "task": {
                        "type": "string",
                        "description": "Task type (default: auto).",
                        "default": "auto",
                        "enum": [
                            "auto", "explain", "bugfix", "refactor", "generate_test",
                            "navigate", "document", "optimize", "security_review",
                        ],
                    },
                    "budget": {"type": "integer", "description": "Token budget override (default: project config)."},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="lens_status",
            description="Return index statistics and token economy summary.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="lens_symbols",
            description="Return all symbols (functions, classes, methods) in a specific file.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path from project root."},
                },
                "required": ["path"],
            },
        ),
        types.Tool(
            name="lens_explain_symbol",
            description=(
                "Deep dive on one symbol: full source, callers, docstring. "
                "Useful for understanding a specific function or class."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Exact symbol name."},
                },
                "required": ["name"],
            },
        ),
        types.Tool(
            name="lens_diff_context",
            description=(
                "Build context focused on git-changed files (staged + unstaged + last commit). "
                "Ideal for code reviews, PR analysis, or fixing recently introduced bugs."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Task/question about the changes."},
                    "budget": {"type": "integer", "description": "Token budget override."},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="lens_reindex",
            description="Trigger incremental reindex (only changed files). Call after editing files.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="lens_memory",
            description=(
                "CRUD access to persistent project memory. "
                "Store project conventions, rules, hotspot files — included in every lens_context."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["set", "get", "list", "delete"],
                        "description": "Operation to perform.",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["map", "ref", "hotspot", "note", "rule"],
                        "description": "Memory category (required for set/get).",
                    },
                    "key": {"type": "string", "description": "Memory key."},
                    "value": {"type": "string", "description": "Memory value (required for set)."},
                    "ttl": {"type": "integer", "description": "Time-to-live in seconds (optional)."},
                },
                "required": ["action"],
            },
        ),
    ]


# ── Resource definitions ──────────────────────────────────────────────────────

@app.list_resources()
async def list_resources() -> list[types.Resource]:
    return [
        types.Resource(
            uri="lens://project/map",
            name="Project Map",
            description="Project structure, README summary and index statistics.",
            mimeType="text/plain",
        ),
        types.Resource(
            uri="lens://project/stats",
            name="Index Statistics",
            description="Files, symbols, languages and token budget info (JSON).",
            mimeType="application/json",
        ),
        types.Resource(
            uri="lens://memory",
            name="Project Memory",
            description="All stored memory entries (rules, notes, hotspots, maps, refs).",
            mimeType="text/plain",
        ),
    ]


@app.read_resource()
async def read_resource(uri: str) -> str:
    from .errors import format_error
    try:
        store, cfg, root, _ = _init_store()
    except Exception as exc:
        return format_error(exc)

    if uri == "lens://project/map":
        from .context.levels import build_level0
        return build_level0(store, root)

    if uri == "lens://project/stats":
        s = store.stats()
        from .config import db_path
        dp = db_path(root)
        s["db_kb"] = dp.stat().st_size // 1024 if dp.exists() else 0
        s["token_budget"] = cfg.get("token_budget", 8000)
        return json.dumps(s, indent=2, default=str)

    if uri == "lens://memory":
        rows = store.memory_list()
        if not rows:
            return "(no memory entries)"
        from .memory.lite import format_context_block
        return format_context_block(rows)

    if uri.startswith("lens://symbols/"):
        path = uri[len("lens://symbols/"):]
        symbols = store.get_symbols_for_file(path)
        if not symbols:
            return f"No symbols found for: {path}"
        lines = [f"Symbols in {path}:"]
        for row in symbols:
            sym = _format_symbol(row)
            lines.append(f"  {sym['signature']}")
            if sym["docstring"]:
                lines.append(f"    # {sym['docstring'].split(chr(10))[0][:100]}")
            lines.append(f"    @ line {sym['line']}")
        return "\n".join(lines)

    return f"Unknown resource: {uri}"


# ── Tool dispatcher ───────────────────────────────────────────────────────────

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    from .errors import format_error
    try:
        dispatch = {
            "lens_search": _tool_search,
            "lens_context": _tool_context,
            "lens_status": _tool_status,
            "lens_symbols": _tool_symbols,
            "lens_explain_symbol": _tool_explain_symbol,
            "lens_diff_context": _tool_diff_context,
            "lens_reindex": _tool_reindex,
            "lens_memory": _tool_memory,
        }
        handler = dispatch.get(name)
        if handler is None:
            return _text(f"Unknown tool: {name!r}")
        # Respect the enabled flag written by the VS Code extension toggle.
        # Only lens_status, lens_reindex and lens_memory bypass this check
        # (they are management tools, not data-retrieval).
        _ALWAYS_ALLOWED = {"lens_status", "lens_reindex", "lens_memory"}
        if name not in _ALWAYS_ALLOWED:
            try:
                from .config import find_project_root, config_path
                _r = find_project_root() or Path.cwd()
                _cp = config_path(_r)
                if _cp.exists():
                    import json as _json
                    _cfg_check = _json.loads(_cp.read_text(encoding="utf-8"))
                    if _cfg_check.get("enabled") is False:
                        return _text(
                            "[context-lens] Optimization is currently disabled.\n"
                            "Enable it again via the Context Lens VS Code extension or by "
                            "setting `\"enabled\": true` in .ctx/config.json."
                        )
            except Exception:
                pass
        return await handler(arguments)
    except Exception as exc:
        return _text(format_error(exc))


# ── Tool implementations ──────────────────────────────────────────────────────

async def _tool_search(args: dict) -> list[types.TextContent]:
    query: str = (args.get("query") or "").strip()
    limit: int = int(args.get("limit", 20))

    if len(query) < 2:
        return _text("Error: query must be at least 2 characters.")

    store, cfg, root, _ = _init_store()

    from .retrieval.search import search_symbols
    from .retrieval.cache import get_fts_cache, fts_key

    cache = get_fts_cache()
    key = fts_key(query, limit)
    results = cache.get(key)
    if results is None:
        results = search_symbols(store, query, limit=limit)
        cache.set(key, results)

    if not results:
        return _text(f"No symbols found for query: {query!r}")

    lines = [f"Found {len(results)} symbol(s) for {query!r}:\n"]
    for row in results:
        sym = _format_symbol(row)
        lines.append(f"  {sym['signature']}")
        if sym["docstring"]:
            first_line = sym["docstring"].split("\n")[0][:120]
            lines.append(f"    # {first_line}")
        lines.append(f"    @ {sym['path']}:{sym['line']}")
        lines.append("")

    return _text("\n".join(lines))


async def _tool_context(args: dict) -> list[types.TextContent]:
    query: str = (args.get("query") or "").strip()
    task: str = args.get("task", "auto") or "auto"
    budget_override: int | None = args.get("budget")

    if not query:
        return _text("Error: query is required.")

    store, cfg, root, lp = _init_store()

    from .retrieval.intent import classify_intent
    from .retrieval.search import search_symbols, find_related_paths
    from .context.builder import build_context
    from .retrieval.cache import get_context_cache, context_key

    if task == "auto" or not task:
        task, confidence = classify_intent(query)
    else:
        confidence = 1.0

    # Auto-select budget for the detected tool if no explicit override
    token_budget = budget_override or _auto_budget(cfg)

    # Context cache — skip for budget_override queries
    ctx_cache = get_context_cache()
    ck = context_key(query, task, token_budget)
    cached_result = None if budget_override else ctx_cache.get(ck)

    if cached_result:
        ctx_text, meta = cached_result
    else:
        relevant_symbols = search_symbols(store, query, limit=50)
        relevant_paths = find_related_paths(store, relevant_symbols)

        ctx_text, meta = build_context(
            store=store,
            root=root,
            task=task,
            query=query,
            relevant_symbols=relevant_symbols,
            relevant_paths=relevant_paths,
            budget=token_budget,
            buffer_ratio=cfg.get("budget_buffer", 0.12),
        )
        ctx_cache.set(ck, (ctx_text, meta), ttl=60.0)

    # Log
    try:
        from .log import CtxLogger
        from .context.budget import compute_tokens_raw
        logger = CtxLogger(lp)
        logger.intent(query, task, confidence, session_id=_session_id)

        included = meta.get("paths_included", [])
        tokens_raw = compute_tokens_raw(root, included, meta["tokens_used"], meta["budget"])

        tool = _detect_tool(token_budget)
        _update_session_tool(root, tool)
        logger.retrieval(
            task, included, meta["tokens_used"], meta["budget"],
            tokens_raw=tokens_raw, tool=tool, session_id=_session_id, query=query,
        )
    except Exception:
        pass

    meta_line = (
        f"\n---\ntask={meta['task']}  tokens={meta['tokens_used']}/{meta['budget']}  "
        f"utilization={meta['utilization']:.0%}  "
        f"files={len(meta.get('paths_included', []))}"
    )
    return _text(ctx_text + meta_line)


async def _tool_status(args: dict) -> list[types.TextContent]:
    store, cfg, root, lp = _init_store()

    from .config import db_path

    s = store.stats()
    dp = db_path(root)
    db_kb = dp.stat().st_size // 1024 if dp.exists() else 0

    last_ts = (
        time.strftime("%Y-%m-%d %H:%M", time.localtime(s["last_indexed"]))
        if s.get("last_indexed") else "never"
    )

    lines = [
        f"Context Lens v2 — {root.name}",
        f"  Index: {s['files']} files  {s['symbols']} symbols  {db_kb} KB",
        f"  Last indexed: {last_ts}",
        f"  Token budget: {_auto_budget(cfg)} tokens (tool: {_detect_tool(0)})",
    ]

    if s.get("by_language"):
        lines.append("  Languages: " + "  ".join(
            f"{lang}({n})" for lang, n in list(s["by_language"].items())[:6]
        ))
    if s.get("by_kind"):
        lines.append("  Symbols: " + "  ".join(
            f"{k}({n})" for k, n in list(s["by_kind"].items())[:5]
        ))

    retrievals = []
    try:
        if lp and lp.exists():
            for ln in lp.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(ln)
                    if r.get("event") == "retrieval":
                        retrievals.append(r)
                except Exception:
                    pass
    except Exception:
        pass

    if retrievals:
        raw_str = store.get_meta("project_tokens_total")
        project_tokens = int(raw_str) if raw_str else None

        def _raw(r: dict) -> int:
            raw = r.get("tokens_raw", 0)
            return raw if raw > 0 else (project_tokens or r["budget"])

        total_used = sum(r["tokens_used"] for r in retrievals)
        total_raw = sum(_raw(r) for r in retrievals)
        total_saved = max(0, total_raw - total_used)
        avg_pct = (1 - total_used / total_raw) * 100 if total_raw else 0
        lines.append(
            f"\n  Token economy ({len(retrievals)} queries): "
            f"saved ~{total_saved:,} tokens, avg {avg_pct:.0f}%"
        )
    else:
        lines.append("\n  No queries yet — run lens_context to start saving tokens.")

    return _text("\n".join(lines))


async def _tool_symbols(args: dict) -> list[types.TextContent]:
    path: str = (args.get("path") or "").strip().replace("\\", "/")
    if not path:
        return _text("Error: path is required.")

    store, cfg, root, _ = _init_store()
    symbols = store.get_symbols_for_file(path)

    if not symbols:
        return _text(f"No symbols in: {path}\n(Run lens_reindex if file was recently added.)")

    lines = [f"Symbols in {path} ({len(symbols)}):\n"]
    for row in symbols:
        sym = _format_symbol(row)
        lines.append(f"  {sym['signature']}")
        if sym["docstring"]:
            lines.append(f"    # {sym['docstring'].split(chr(10))[0][:120]}")
        lines.append(f"    @ line {sym['line']}")
        lines.append("")

    return _text("\n".join(lines))


async def _tool_explain_symbol(args: dict) -> list[types.TextContent]:
    name: str = (args.get("name") or "").strip()
    if not name:
        return _text("Error: name is required.")

    store, cfg, root, _ = _init_store()
    rows = store.get_symbols_by_name(name)

    if not rows:
        return _text(f"Symbol '{name}' not found. Use lens_search to find similar symbols.")

    lines: list[str] = []
    for row in rows:
        sym = _format_symbol(row)
        lines.append(f"## {sym['signature']}")
        lines.append(f"File: {sym['path']}  |  Line: {sym['line']}")
        if sym["docstring"]:
            lines.append(f"\nDocstring:\n  {sym['docstring']}")

        # Full source
        file_path = Path(row["path"])
        if not file_path.exists():
            file_path = root / row["path"]
        if file_path.exists():
            try:
                src = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
                start = max(0, row["start_line"] - 1)
                end = min(row["end_line"], len(src))
                body = src[start:end]
                if len(body) > 60:
                    body = body[:60] + ["    ... (truncated)"]
                lines.append(f"\nSource (lines {row['start_line']}-{row['end_line']}):")
                lines.extend(f"  {l}" for l in body)
            except OSError:
                pass

        # Callers
        from .retrieval.search import find_callers
        callers = find_callers(store, name, root, max_files=50, max_results=8)
        if callers:
            lines.append(f"\nCallers ({len(callers)}):")
            for c in callers:
                lines.append(f"  {c}")
        lines.append("")

    return _text("\n".join(lines))


async def _tool_diff_context(args: dict) -> list[types.TextContent]:
    query: str = (args.get("query") or "").strip()
    budget_override: int | None = args.get("budget")

    if not query:
        return _text("Error: query is required.")

    store, cfg, root, lp = _init_store()

    from .git import is_git_repo, get_changed_files
    if not is_git_repo(root):
        return _text("Not a git repository. Use lens_context for regular context building.")

    changed = get_changed_files(root)
    if not changed:
        return _text("No changed files detected (clean working tree). Use lens_context.")

    indexed = set(store.list_indexed_paths())
    changed_indexed = [p for p in changed if p in indexed]
    if not changed_indexed:
        return _text(
            f"Changed files ({', '.join(changed[:3])}) are not indexed. Run lens_reindex."
        )

    from .retrieval.intent import classify_intent
    from .retrieval.search import search_symbols, find_related_paths
    from .context.builder import build_context

    task, _ = classify_intent(query)
    relevant_symbols = search_symbols(store, query, limit=30)
    fts_paths = find_related_paths(store, relevant_symbols)
    all_paths = list(dict.fromkeys(changed_indexed + fts_paths))

    ctx_text, meta = build_context(
        store=store, root=root, task=task, query=query,
        relevant_symbols=relevant_symbols, relevant_paths=all_paths,
        budget=budget_override or _auto_budget(cfg),
        buffer_ratio=cfg.get("budget_buffer", 0.12),
    )

    prefix = (
        f"[diff: {len(changed_indexed)} changed file(s) prioritised: "
        f"{', '.join(changed_indexed[:3])}{'...' if len(changed_indexed) > 3 else ''}]\n\n"
    )

    # Log the retrieval so diff queries appear in economy tracking
    try:
        from .log import CtxLogger
        from .context.budget import count_tokens
        logger = CtxLogger(lp)
        logger.intent(query, task, 1.0, session_id=_session_id)
        included = meta.get("paths_included", [])
        tokens_raw = 0
        for p_str in included:
            fp = Path(p_str)
            if not fp.exists():
                fp = root / p_str
            if fp.exists():
                try:
                    tokens_raw += count_tokens(
                        fp.read_text(encoding="utf-8", errors="replace")
                    )
                except OSError:
                    pass
        tokens_raw = max(tokens_raw, meta["budget"])
        tool = _detect_tool(0)
        _update_session_tool(root, tool)
        logger.retrieval(
            task, included, meta["tokens_used"], meta["budget"],
            tokens_raw=tokens_raw, tool=tool, session_id=_session_id, query=query,
        )
    except Exception:
        pass

    meta_line = (
        f"\n---\ntask={meta['task']}  tokens={meta['tokens_used']}/{meta['budget']}  "
        f"utilization={meta['utilization']:.0%}"
    )
    return _text(prefix + ctx_text + meta_line)


async def _tool_reindex(args: dict) -> list[types.TextContent]:
    from .config import find_project_root, load_config, db_path, log_path, ctx_dir
    from .db.schema import init_db
    from .db.store import Store
    from .indexer.walker import walk_project
    from .indexer.extractor import extract_symbols
    from .indexer.hasher import hash_file
    from .log import CtxLogger
    from .retrieval.cache import invalidate_all

    try:
        store_existing, cfg, root, _ = _init_store()
    except Exception:
        root = find_project_root() or Path.cwd()
        cfg = load_config(root)

    dp = db_path(root)
    ctx_dir(root).mkdir(parents=True, exist_ok=True)
    conn = init_db(dp)
    reindex_store = Store(conn)
    logger = CtxLogger(log_path(root))

    extensions = cfg.get("index_extensions", [".py", ".js", ".ts"])
    ignore_dirs = set(cfg.get("ignore_dirs", [".git", "__pycache__", "node_modules"]))
    max_kb = cfg.get("max_file_size_kb", 512)

    t0 = time.time()
    indexed = skipped = 0

    for file_path in walk_project(root, extensions, ignore_dirs, max_kb):
        rel = file_path.relative_to(root).as_posix()
        try:
            h = hash_file(file_path)
        except OSError:
            continue
        if reindex_store.get_file_hash(rel) == h:
            skipped += 1
            continue
        symbols, lang = extract_symbols(file_path)
        file_id = reindex_store.upsert_file(rel, h, lang, file_path.stat().st_size)
        if symbols:
            for s in symbols:
                s["file_id"] = file_id
                s["path"] = rel
            reindex_store.insert_symbols_batch(symbols)
        indexed += 1
        logger.index(rel, len(symbols))

    reindex_store.commit()

    # Update project_tokens_total & write stats.json (mirrors CLI behaviour)
    try:
        from .context.budget import count_tokens
        total_tokens = 0
        for fp in walk_project(root, extensions, ignore_dirs, max_kb):
            try:
                total_tokens += count_tokens(fp.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
        reindex_store.set_meta("project_tokens_total", str(total_tokens))
        # Write stats.json for the VS Code extension
        s = reindex_store.stats()
        stats_out = {
            "files": s["files"],
            "symbols": s["symbols"],
            "by_kind": s["by_kind"],
            "by_language": s["by_language"],
            "last_indexed": s["last_indexed"],
            "total_bytes": s["total_bytes"],
            "db_kb": dp.stat().st_size // 1024 if dp.exists() else 0,
            "project_tokens": total_tokens,
        }
        stats_file = ctx_dir(root) / "stats.json"
        with open(stats_file, "w", encoding="utf-8") as f:
            json.dump(stats_out, f, indent=2)
    except Exception:
        pass

    invalidate_all()
    _reset_store()

    elapsed = time.time() - t0
    return _text(f"Reindex complete: {indexed} updated, {skipped} unchanged. ({elapsed:.1f}s)")


async def _tool_memory(args: dict) -> list[types.TextContent]:
    action: str = (args.get("action") or "").strip().lower()
    kind: str = (args.get("kind") or "").strip()
    key: str = (args.get("key") or "").strip()
    value: str = (args.get("value") or "").strip()
    ttl: int | None = args.get("ttl")

    store, cfg, root, _ = _init_store()

    if action == "list":
        rows = store.memory_list()
        if not rows:
            return _text("No memory entries.")
        lines = [f"Memory ({len(rows)} entries):"]
        for row in rows:
            lines.append(f"  [{row['kind']}] {row['key']}: {str(row['value'])[:80]}")
        return _text("\n".join(lines))

    if action == "get":
        if not kind:
            return _text("Error: kind is required for action=get.")
        rows = store.memory_get(kind, key or None)
        if not rows:
            return _text(f"No memory for kind={kind!r}" + (f", key={key!r}" if key else "") + ".")
        return _text("\n".join(f"[{r['kind']}] {r['key']}: {r['value']}" for r in rows))

    if action == "set":
        if not kind or not key or not value:
            return _text("Error: kind, key and value required for action=set.")
        store.memory_set(kind, key, value, ttl=ttl)
        store.commit()
        return _text(f"Memory set: [{kind}] {key}")

    if action == "delete":
        if not key:
            return _text("Error: key (id) required for action=delete.")
        try:
            store.memory_delete(int(key))
            store.commit()
            return _text(f"Memory entry {key} deleted.")
        except Exception as e:
            return _text(f"Error: {e}")

    return _text(f"Unknown action: {action!r}. Use: set | get | list | delete")


# ── Entry point ───────────────────────────────────────────────────────────────

def _cleanup() -> None:
    """End the active session and remove session.json on shutdown."""
    global _store, _session_id
    try:
        if _store and _session_id:
            _store.end_session(_session_id)
    except Exception:
        pass
    # Remove stale session.json
    try:
        if _root:
            from .config import ctx_dir
            sf = ctx_dir(_root) / "session.json"
            if sf.exists():
                sf.unlink()
    except Exception:
        pass


def main() -> None:
    """Entry point for the `lens-mcp` console script."""
    global _cli_tool_override

    # Parse optional --tool flag for explicit tool override
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--tool" and i + 1 < len(args):
            from .config import normalize_target_name
            _cli_tool_override = normalize_target_name(args[i + 1]) or args[i + 1]
            break
        if arg.startswith("--tool="):
            from .config import normalize_target_name
            _cli_tool_override = normalize_target_name(arg.split("=", 1)[1]) or arg.split("=", 1)[1]
            break

    async def _run():
        async with stdio_server() as (read_stream, write_stream):
            try:
                await app.run(
                    read_stream,
                    write_stream,
                    app.create_initialization_options(),
                )
            finally:
                _cleanup()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
