"""
Context Lens — MCP Server (stdio transport).

Exposes 3 tools to AI coding assistants via Model Context Protocol:
  - lens_search(query)           — FTS5 symbol search
  - lens_context(query, task)    — assembles optimised context
  - lens_status()                — index stats + token economy

Usage (stdio, the MCP default for lightweight local servers):
    lens-mcp

Claude Code auto-detection: add .claude/mcp.json to your project root.
"""
from __future__ import annotations

import asyncio
import json
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


# ── MCP server instance ───────────────────────────────────────────────────────

app = Server("context-lens")

# ── Lazy store initialisation ─────────────────────────────────────────────────
# We do NOT import heavy modules at module level — only when a tool is called.

_store: Any = None
_cfg: dict = {}
_root: Path | None = None
_log_path: Path | None = None


def _init_store() -> tuple[Any, dict, Path, Path]:
    """Lazy-init: open the SQLite store and config once, then reuse."""
    global _store, _cfg, _root, _log_path

    if _store is not None:
        return _store, _cfg, _root, _log_path  # type: ignore[return-value]

    from .config import find_project_root, db_path, log_path, load_config
    from .db.schema import init_db
    from .db.store import Store

    root = find_project_root() or Path.cwd()
    dp = db_path(root)

    if not dp.exists():
        raise FileNotFoundError(
            f"No index found at {dp}. "
            "Run `lens index` inside your project first."
        )

    conn = init_db(dp)
    _store = Store(conn)
    _cfg = load_config(root)
    _root = root
    _log_path = log_path(root)
    return _store, _cfg, _root, _log_path


def _format_symbol(row: Any) -> dict:
    """Convert a sqlite3.Row symbol to a plain dict."""
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


# ── Tool definitions ──────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="lens_search",
            description=(
                "Search indexed symbols (functions, classes, methods) by name or "
                "description using FTS5 full-text search. Returns matching symbol "
                "signatures, file paths and line numbers."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query — can be a symbol name, keyword or natural language.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max number of results (default 20).",
                        "default": 20,
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="lens_context",
            description=(
                "Assemble an optimised context block for a given query, respecting the "
                "configured token budget. Auto-detects the task type (explain, bugfix, "
                "refactor, generate_test, navigate) or accepts an explicit task. "
                "Returns the context text plus usage metadata."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The coding question or task description.",
                    },
                    "task": {
                        "type": "string",
                        "description": (
                            "Task type: explain | bugfix | refactor | generate_test | navigate | auto. "
                            "Defaults to 'auto' (intent is classified automatically)."
                        ),
                        "default": "auto",
                        "enum": ["auto", "explain", "bugfix", "refactor", "generate_test", "navigate"],
                    },
                    "budget": {
                        "type": "integer",
                        "description": "Token budget override (default: project config, usually 8000).",
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="lens_status",
            description=(
                "Return index statistics (files, symbols, languages) and token economy "
                "summary (queries run, tokens saved). Useful for verifying the index is "
                "up-to-date and checking context efficiency."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
    ]


# ── Tool handlers ─────────────────────────────────────────────────────────────

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """Dispatch tool calls to the appropriate handler."""
    try:
        if name == "lens_search":
            return await _tool_search(arguments)
        elif name == "lens_context":
            return await _tool_context(arguments)
        elif name == "lens_status":
            return await _tool_status(arguments)
        else:
            raise ValueError(f"Unknown tool: {name!r}")
    except FileNotFoundError as exc:
        return [types.TextContent(type="text", text=f"[context-lens] {exc}")]
    except Exception as exc:
        return [types.TextContent(type="text", text=f"[context-lens] Error: {exc}")]


async def _tool_search(args: dict) -> list[types.TextContent]:
    query: str = args.get("query", "").strip()
    limit: int = int(args.get("limit", 20))

    if not query:
        return [types.TextContent(type="text", text="Error: query is required.")]

    store, cfg, root, log_path = _init_store()

    from .retrieval.search import search_symbols

    results = search_symbols(store, query, limit=limit)

    if not results:
        return [types.TextContent(
            type="text",
            text=f"No symbols found for query: {query!r}",
        )]

    lines = [f"Found {len(results)} symbol(s) for {query!r}:\n"]
    for row in results:
        sym = _format_symbol(row)
        lines.append(f"  {sym['signature']}")
        if sym["docstring"]:
            first_line = sym["docstring"].split("\n")[0][:120]
            lines.append(f"    # {first_line}")
        lines.append(f"    @ {sym['path']}:{sym['line']}")
        lines.append("")

    return [types.TextContent(type="text", text="\n".join(lines))]


async def _tool_context(args: dict) -> list[types.TextContent]:
    query: str = args.get("query", "").strip()
    task: str = args.get("task", "auto")
    budget_override: int | None = args.get("budget")

    if not query:
        return [types.TextContent(type="text", text="Error: query is required.")]

    store, cfg, root, lp = _init_store()

    from .retrieval.intent import classify_intent
    from .retrieval.search import search_symbols, find_related_paths
    from .context.builder import build_context

    # Intent detection
    if task == "auto" or not task:
        task, confidence = classify_intent(query)
    else:
        confidence = 1.0

    # Search for relevant symbols and paths
    relevant_symbols = search_symbols(store, query, limit=50)
    relevant_paths = find_related_paths(store, relevant_symbols)

    token_budget = budget_override or cfg.get("token_budget", 8000)
    buffer = cfg.get("budget_buffer", 0.12)

    ctx_text, meta = build_context(
        store=store,
        root=root,
        task=task,
        query=query,
        relevant_symbols=relevant_symbols,
        relevant_paths=relevant_paths,
        budget=token_budget,
        buffer_ratio=buffer,
    )

    # Log the retrieval event
    try:
        from .log import CtxLogger
        logger = CtxLogger(lp)
        logger.intent(query, task, confidence)
        logger.retrieval(task, relevant_paths, meta["tokens_used"], meta["budget"])
    except Exception:
        pass  # log failures are non-fatal

    # Append metadata summary at the end
    meta_lines = [
        "",
        "---",
        f"task={meta['task']}  tokens={meta['tokens_used']}/{meta['budget']}  "
        f"utilization={meta['utilization']:.0%}  "
        f"files={len(meta['paths_included'])}",
    ]

    return [types.TextContent(type="text", text=ctx_text + "\n".join(meta_lines))]


async def _tool_status(args: dict) -> list[types.TextContent]:
    store, cfg, root, lp = _init_store()

    from .config import db_path

    s = store.stats()
    dp = db_path(root)
    db_kb = dp.stat().st_size // 1024

    last_ts = (
        time.strftime("%Y-%m-%d %H:%M", time.localtime(s["last_indexed"]))
        if s.get("last_indexed")
        else "never"
    )

    lines = [
        f"Context Lens — {root.name}",
        f"  Index: {s['files']} files  {s['symbols']} symbols  {db_kb} KB",
        f"  Last indexed: {last_ts}",
        f"  Token budget: {cfg.get('token_budget', 8000)} tokens",
        "",
    ]

    # Languages breakdown
    if s.get("by_language"):
        lines.append("  Languages:")
        for lang, n in list(s["by_language"].items())[:8]:
            lines.append(f"    {lang:<16} {n:>5} file(s)")
        lines.append("")

    # Symbol kinds breakdown
    if s.get("by_kind"):
        lines.append("  Symbol kinds:")
        for kind, n in list(s["by_kind"].items())[:6]:
            lines.append(f"    {kind:<16} {n:>6}")
        lines.append("")

    # Token economy from log
    retrievals = []
    try:
        if lp.exists():
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
        total_saved = sum(r["budget"] - r["tokens_used"] for r in retrievals)
        avg_util = (
            1 - sum(r["tokens_used"] for r in retrievals)
            / sum(r["budget"] for r in retrievals)
        ) * 100
        lines.append(f"  Token economy ({len(retrievals)} queries):")
        lines.append(f"    Total tokens saved: ~{total_saved:,}")
        lines.append(f"    Avg utilization:    {avg_util:.0f}% under budget")
    else:
        lines.append("  No queries yet — run lens_context to see token economy.")

    return [types.TextContent(type="text", text="\n".join(lines))]


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    """Entry point for the `lens-mcp` console script."""
    async def _run():
        async with stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options(),
            )

    asyncio.run(_run())


if __name__ == "__main__":
    main()
