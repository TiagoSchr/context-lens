"""
Context Lens CLI — entry point.

Commands:
  lens init       Initialize .ctx/ in current project
  lens index      Index project files
  lens status     Full status: index health + token economy
  lens search     Search symbols by query
  lens context    Build and print context for a query
  lens show       Show project map or symbol info
  lens stats      Index statistics
  lens log        Query history and events
  lens watch      Auto-reindex on file changes
  lens memory     Manage memory entries
  lens config     Show or edit configuration
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

import click

# Força UTF-8 no stdout/stderr em Windows (cp1252 não suporta símbolos unicode)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from .config import (
    find_project_root, ctx_dir, db_path, log_path, load_config, save_config, DEFAULT_CONFIG,
    config_path, merge_config, detect_client_tool,
)
from .db.schema import init_db
from .db.store import Store
from .indexer.hasher import hash_file
from .indexer.walker import walk_project
from .indexer.extractor import extract_symbols
from .context.builder import build_context
from .context.budget import compute_tokens_raw
from .context.levels import build_level0, build_level1
from .retrieval.intent import classify_intent
from .retrieval.search import search_symbols, find_related_paths
from .retrieval.policy import POLICIES
from .memory.lite import MemoryLite
from .log import CtxLogger


# ─────────────────────────────────────────────────────────── shared helpers

def _require_index(root: Path) -> tuple[Store, dict, CtxLogger]:
    cfg = load_config(root)
    dp = db_path(root)
    if not dp.exists():
        click.echo("No index found. Run `lens index` first.", err=True)
        sys.exit(1)
    conn = init_db(dp)
    store = Store(conn)
    logger = CtxLogger(log_path(root))
    return store, cfg, logger


def _iter_index_paths(
    root: Path,
    target: Path,
    extensions: list[str],
    ignore_dirs: set[str],
    max_kb: int,
):
    """Yield files to index from a project root, subdirectory or single file."""
    if target.is_file():
        try:
            size_ok = target.stat().st_size <= max_kb * 1024
        except OSError:
            return
        if target.suffix in set(extensions) and size_ok:
            yield target
        return

    yield from walk_project(target, extensions, ignore_dirs, max_kb)


# ─────────────────────────────────────────────────────────── CLI group

@click.group()
@click.version_option(package_name="context-lens", prog_name="lens")
def main():
    """Context Lens — lightweight context assembler for coding assistants."""


# ─────────────────────────────────────────────────────────── init

@main.command()
@click.option("--budget", default=None, type=int, help="Token budget (default: 8000)")
def init(budget):
    """Initialize .ctx/ directory in the current project."""
    root = Path.cwd()
    d = ctx_dir(root)
    cfg_file = config_path(root)

    if d.exists():
        click.echo(f"Already initialized at {d}")
    else:
        d.mkdir(parents=True)
        click.echo(f"Created {d}")

    if cfg_file.exists():
        existing_cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        cfg = merge_config(existing_cfg)
        if budget:
            cfg["token_budget"] = budget
        if cfg != existing_cfg:
            save_config(root, cfg)
            click.echo(f"Config merged into {cfg_file}")
        else:
            click.echo(f"Config preserved at {cfg_file}")
    else:
        cfg = dict(DEFAULT_CONFIG)
        if budget:
            cfg["token_budget"] = budget
        save_config(root, cfg)
        click.echo(f"Config written to {cfg_file}")

    # Initialize DB
    conn = init_db(db_path(root))
    conn.close()
    click.echo("Index database ready.")
    click.echo("\nNext step: run `lens index` to index your project.")
    click.echo("Optional:   run `lens setup` to configure auto-context for your AI tools.")


# ─────────────────────────────────────────────────────────── setup

# Templates de instrução por ferramenta
_AI_TOOLS = {
    "claude": {
        "name": "Claude Code",
        "detect": [".claude"],
        "file": "CLAUDE.md",
        "content": """\
# Context Lens — Instruções obrigatórias

Antes de usar Read, Glob, Grep ou qualquer ferramenta de leitura de arquivo,
SEMPRE chame lens_context primeiro — mesmo para um único arquivo.

**Fluxo obrigatório:**
1. Receber tarefa do usuário
2. Chamar `lens_context "<query>"` via MCP → obtém contexto otimizado
3. Usar o contexto retornado como base principal
4. Usar Read/Glob apenas para arquivos específicos não cobertos pelo contexto

Ler arquivos sem chamar lens_context primeiro desperdiça 75-98% dos tokens
disponíveis nesta sessão e reduz a qualidade das respostas seguintes.

Para buscar símbolos pelo nome: usar `lens_search` via MCP.
""",
    },
    "cursor": {
        "name": "Cursor",
        "detect": [".cursor", ".cursorrules"],
        "file": ".cursor/rules/lens.mdc",
        "content": """\
---
description: Context Lens — always use lens_context before exploring files
alwaysApply: true
---
Before exploring code files, always call the `lens_context` MCP tool with your query.
Use the returned context as the primary source. Only read specific files not covered
by the lens context. This reduces token usage by ~80% per query.
""",
    },
    "copilot": {
        "name": "GitHub Copilot",
        "detect": [".github"],
        "file": ".github/copilot-instructions.md",
        "content": """\
# Context Lens

Before exploring files, use the `lens_context` MCP tool with your query.
Use the returned optimized context as the primary source of truth for the codebase.
Only read specific files not already covered by lens context.
This reduces token usage significantly per query.
""",
    },
    "codex": {
        "name": "OpenAI Codex / ChatGPT",
        "detect": ["AGENTS.md"],
        "file": "AGENTS.md",
        "content": """\
# Context Lens

Before exploring files, call the `lens_context` tool with your query.
Use the returned context as primary source. Only read specific files
not covered by lens context. This reduces token usage per query.
""",
    },
}


def _detect_tools(root: Path) -> list[str]:
    """Detecta quais ferramentas de AI estão presentes no projeto."""
    found = []
    for key, tool in _AI_TOOLS.items():
        for marker in tool["detect"]:
            if (root / marker).exists():
                found.append(key)
                break
    return found


def _remove_tool_setup(root: Path, key: str) -> list[str]:
    """Remove a seção Context Lens do arquivo da ferramenta.
    Retorna lista de mensagens sobre arquivos modificados."""
    msgs = []
    tool = _AI_TOOLS.get(key)
    if not tool:
        return msgs
    dest = root / tool["file"]
    if not dest.exists():
        return msgs
    content = dest.read_text(encoding="utf-8", errors="ignore")
    if "lens_context" not in content:
        return msgs
    lens_block = "\n" + tool["content"]
    if content.strip() == tool["content"].strip():
        dest.unlink()
        msgs.append(f"deleted {tool['file']}")
    elif lens_block in content:
        new_content = content.replace(lens_block, "").rstrip() + "\n"
        dest.write_text(new_content, encoding="utf-8")
        msgs.append(f"cleaned {tool['file']}")
    else:
        msgs.append(f"skipped {tool['file']} (custom content, cannot auto-remove)")
    return msgs


def _do_create_targets(root: Path, targets: list[str]) -> None:
    """Cria/atualiza arquivos de instrução para os targets especificados."""
    created, skipped = [], []
    for key in targets:
        tool = _AI_TOOLS[key]
        dest = root / tool["file"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            if "lens_context" in dest.read_text(encoding="utf-8", errors="ignore"):
                skipped.append(tool["file"])
                continue
            with open(dest, "a", encoding="utf-8") as f:
                f.write("\n" + tool["content"])
            click.echo(f"  Updated  {tool['file']}")
        else:
            dest.write_text(tool["content"], encoding="utf-8")
            click.echo(f"  Created  {tool['file']}")
        created.append(tool["file"])
    if skipped:
        click.echo(f"  Skipped  {', '.join(skipped)}  (lens_context already present)")
    if created:
        click.echo("\n  Done. The AI will now call lens_context automatically.")
        click.echo("  Run `lens index` first if you haven't already.\n")
    else:
        click.echo("\n  All tools already configured.\n")


@main.command()
@click.option("--auto", is_flag=True, help="Configura automaticamente sem perguntar.")
@click.option("--manual", is_flag=True, help="Pula configuração automática.")
@click.option("--remove", "do_remove", is_flag=True,
              help="Remove Context Lens setup for specified target.")
@click.option("--switch", default=None,
              type=click.Choice(["claude", "cursor", "copilot", "codex"]),
              help="Remove configuração atual e instala para a ferramenta especificada.")
@click.option("--target", default=None,
              type=click.Choice(["all", "claude", "cursor", "copilot", "codex"]),
              help="Tool to configure or remove (default: all detected).")
def setup(auto, manual, do_remove, switch, target):
    """Configure automatic context injection for AI coding tools.

    Creates instruction files (CLAUDE.md, .cursorrules, etc.) that tell
    the AI assistant to always use lens_context before exploring files,
    reducing token usage by ~80% per query.
    """
    root = Path.cwd()
    all_keys = list(_AI_TOOLS.keys())

    if manual:
        click.echo("Skipping auto-context setup. Use lens context \"query\" manually.")
        return

    # ── --remove ────────────────────────────────────────────────────────────
    if do_remove:
        keys = all_keys if (target is None or target == "all") else [target]
        msgs = []
        for key in keys:
            msgs.extend(_remove_tool_setup(root, key))
        if msgs:
            for m in msgs:
                click.echo(f"  {m}")
            click.echo("\n  Setup removed. Run `lens setup` to reconfigure.")
        else:
            click.echo("  Nothing to remove — no Context Lens setup found.")
        return

    # ── --switch ─────────────────────────────────────────────────────────────
    if switch:
        for key in all_keys:
            _remove_tool_setup(root, key)
        click.echo()
        _do_create_targets(root, [switch])
        click.echo(f"  Switched to {_AI_TOOLS[switch]['name']}.\n")
        return

    # ── normal setup ─────────────────────────────────────────────────────────
    click.echo("\n  Context Lens — AI Tool Setup")
    click.echo("  " + "-" * 34)
    click.echo("  When automatic mode is ON, the AI tool will call lens_context")
    click.echo("  before every task, reducing tokens sent by ~80%.\n")

    detected = _detect_tools(root)
    if detected:
        names = ", ".join(_AI_TOOLS[k]["name"] for k in detected)
        click.echo(f"  Detected tools: {names}")
    else:
        click.echo("  No AI tools detected. Will configure based on your choice.")

    if not auto:
        choice = click.prompt(
            "\n  Configure automatic context injection?",
            type=click.Choice(["yes", "no"], case_sensitive=False),
            default="yes",
        )
        if choice.lower() == "no":
            click.echo("\n  Skipped. Use lens context \"query\" manually when needed.")
            return

    # Resolve targets — --target flag takes priority over detection
    if target and target != "all":
        targets = [target]
    elif target == "all":
        targets = all_keys
    elif detected and not auto:
        use_detected = click.confirm(
            f"\n  Configure only detected tools ({', '.join(_AI_TOOLS[k]['name'] for k in detected)})?",
            default=True,
        )
        targets = detected if use_detected else all_keys
    elif detected:
        targets = detected
    else:
        if not auto:
            click.echo("\n  Select tools to configure:")
            for i, key in enumerate(all_keys, 1):
                click.echo(f"    {i}. {_AI_TOOLS[key]['name']}  ({_AI_TOOLS[key]['file']})")
            raw = click.prompt("  Enter numbers (e.g. 1,2) or 'all'", default="1")
            if raw.strip().lower() == "all":
                targets = all_keys
            else:
                idxs = [int(x.strip()) - 1 for x in raw.split(",") if x.strip().isdigit()]
                targets = [all_keys[i] for i in idxs if 0 <= i < len(all_keys)]
        else:
            targets = all_keys

    if not targets:
        click.echo("  Nothing selected.")
        return

    click.echo()
    _do_create_targets(root, targets)


# ─────────────────────────────────────────────────────────── index

@main.command()
@click.option("--force", is_flag=True, help="Re-index all files, ignoring hash cache")
@click.option("--incremental", is_flag=True, help="Explicit no-op; incremental indexing is already the default")
@click.option("--quiet", is_flag=True, help="Suppress non-error output")
@click.option("--verbose", "-v", is_flag=True)
@click.argument("path", default=".", type=click.Path(exists=True, path_type=Path))
def index(force, incremental, quiet, verbose, path):
    """Index project files into the context database.

    Auto-initializes .ctx/ if not present — no need to run ctx init first.
    """
    _ = incremental
    target_path = path.resolve()
    root = find_project_root(target_path) or target_path
    cfg = load_config(root)
    dp = db_path(root)
    # Auto-init: cria .ctx/ se não existir, sem exigir ctx init manual
    d = ctx_dir(root)
    first_run = not d.exists()
    d.mkdir(parents=True, exist_ok=True)
    if first_run:
        save_config(root, dict(DEFAULT_CONFIG))
        if not quiet:
            click.echo(f"Initialized .ctx/ at {root}")

    conn = init_db(dp)
    store = Store(conn)
    logger = CtxLogger(log_path(root))

    extensions = cfg["index_extensions"]
    ignore_dirs = set(cfg["ignore_dirs"])
    max_kb = cfg["max_file_size_kb"]

    if not quiet:
        click.echo(f"Indexing {root} ...")
    t0 = time.time()

    files_checked = 0
    files_indexed = 0
    files_skipped = 0
    total_symbols = 0

    for file_path in _iter_index_paths(root, target_path, extensions, ignore_dirs, max_kb):
        rel = file_path.relative_to(root).as_posix()  # forward slashes em todos os OS
        files_checked += 1

        try:
            current_hash = hash_file(file_path)
        except OSError as e:
            logger.error(f"hash failed: {rel}", error=str(e))
            continue

        stored_hash = store.get_file_hash(rel)
        if not force and stored_hash == current_hash:
            files_skipped += 1
            logger.index(rel, 0, skipped=True)
            continue

        # Extract symbols
        symbols, lang = extract_symbols(file_path)

        file_id = store.upsert_file(rel, current_hash, lang, file_path.stat().st_size)
        if symbols:
            for s in symbols:
                s["file_id"] = file_id
                s["path"] = rel  # normalizar para caminho relativo ao root
            store.insert_symbols_batch(symbols)

        total_symbols += len(symbols)
        files_indexed += 1
        logger.index(rel, len(symbols))

        if verbose and not quiet:
            click.echo(f"  {rel} ({lang or '?'}) — {len(symbols)} symbols")

    store.commit()

    # Calcula tokens reais do projeto (uma vez por indexação com mudanças)
    try:
        from .context.budget import count_tokens as _count_tokens
        _existing_total = store.get_meta("project_tokens_total")
        if force or _existing_total is None or files_indexed > 0:
            _total_tokens = 0
            for _indexed_path in store.list_indexed_paths():
                try:
                    _abs = root / _indexed_path
                    if _abs.exists():
                        _total_tokens += _count_tokens(
                            _abs.read_text(encoding="utf-8", errors="ignore")
                        )
                except Exception:
                    pass
            if _total_tokens > 0:
                store.set_meta("project_tokens_total", str(_total_tokens))
                store.set_meta("project_tokens_updated_at", str(time.time()))
    except Exception:
        pass  # nunca interrompe a indexação por causa do token counting

    elapsed = time.time() - t0

    # Write stats.json for the VS Code extension to read without a subprocess
    try:
        s = store.stats()
        dp = db_path(root)
        _proj_str = store.get_meta("project_tokens_total")
        _stats_data = {
            "files": s["files"],
            "symbols": s["symbols"],
            "db_kb": dp.stat().st_size // 1024 if dp.exists() else 0,
            "last_indexed": s.get("last_indexed") or 0,
            "token_budget": cfg.get("token_budget", 8000),
            "project_tokens_total": int(_proj_str) if _proj_str else 0,
            "by_language": s.get("by_language", {}),
            "total_bytes": s.get("total_bytes", 0),
        }
        _stats_path = ctx_dir(root) / "stats.json"
        with open(_stats_path, "w", encoding="utf-8") as _f:
            json.dump(_stats_data, _f, indent=2)
    except Exception:
        pass  # never interrupt indexing due to stats write failure

    rate = files_checked / elapsed if elapsed > 0 else 0
    if not quiet:
        click.echo(f"\n{'='*44}")
        click.echo("  Indexing complete")
        click.echo(f"{'='*44}")
        click.echo(f"  {'Indexed':<18} {files_indexed:>6} file(s)")
        click.echo(f"  {'Unchanged':<18} {files_skipped:>6} file(s)  (cache hit)")
        click.echo(f"  {'Symbols found':<18} {total_symbols:>6}")
        click.echo(f"  {'Speed':<18} {rate:>5.0f} files/sec")
        click.echo(f"  {'Time':<18} {elapsed:>5.1f}s")
        click.echo(f"{'='*44}")
        click.echo("  Run `lens status` to see token economy.")


# ─────────────────────────────────────────────────────────── search

@main.command()
@click.argument("query")
@click.option("--limit", "-n", default=20, help="Max results")
@click.option("--kind", "-k", default=None, help="Filter by kind: function|class|method|...")
def search(query, limit, kind):
    """Search symbols by name or docstring."""
    root = find_project_root() or Path.cwd()
    store, cfg, _ = _require_index(root)

    if kind:
        results = store.get_symbols_by_kind(kind, limit=limit)
    else:
        results = search_symbols(store, query, limit=limit)

    if not results:
        click.echo("No results.")
        return

    click.echo(f"Found {len(results)} symbol(s):\n")
    for row in results:
        sig = f"[{row['kind']}] {row['name']}"
        if row["params"]:
            sig += row["params"]
        if row["return_type"]:
            sig += f" -> {row['return_type']}"
        click.echo(f"  {sig}")
        if row["docstring"]:
            first = row["docstring"].split("\n")[0][:100]
            click.echo(f"    # {first}")
        click.echo(f"    @ {row['path']}:{row['start_line']}")


# ─────────────────────────────────────────────────────────── context

@main.command("context")
@click.argument("query")
@click.option("--task", "-t", default=None,
              type=click.Choice(list(POLICIES.keys())),
              help="Task type (auto-detected if not set)")
@click.option("--budget", "-b", default=None, type=int, help="Token budget override")
@click.option("--file", "-f", "extra_files", multiple=True,
              help="Force include these files (level3)")
@click.option("--show-meta", is_flag=True, help="Print metadata after context")
@click.option("--output", "-o", default=None, type=click.Path(),
              help="Write context to file instead of stdout")
def context_cmd(query, task, budget, extra_files, show_meta, output):
    """Build and print context for a query."""
    root = find_project_root() or Path.cwd()
    store, cfg, logger = _require_index(root)

    # Intent detection
    if task is None:
        task, confidence = classify_intent(query)
        logger.intent(query, task, confidence)
        click.echo(f"[intent: {task} ({confidence:.0%})]", err=True)
    else:
        confidence = 1.0
        logger.intent(query, task, confidence)

    # Search for relevant symbols and paths
    relevant_symbols = search_symbols(store, query, limit=50)
    relevant_paths = find_related_paths(store, relevant_symbols)

    # Add explicitly requested files (normaliza separadores)
    for ef in extra_files:
        ef_norm = Path(ef).as_posix()
        if ef_norm not in relevant_paths:
            relevant_paths.insert(0, ef_norm)

    detected_tool = detect_client_tool()
    token_budget = budget or cfg.get("target_budgets", {}).get(detected_tool, cfg["token_budget"])
    buffer = cfg["budget_buffer"]

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

    _tokens_raw = compute_tokens_raw(
        root,
        meta.get("paths_included", []),
        meta["tokens_used"],
        meta["budget"],
    )
    logger.retrieval(
        task,
        relevant_paths,
        meta["tokens_used"],
        meta["budget"],
        tokens_raw=_tokens_raw,
        tool=detected_tool or "unknown",
        query=query,
    )

    if output:
        Path(output).write_text(ctx_text, encoding="utf-8")
        click.echo(f"Context written to {output} ({meta['tokens_used']} tokens)", err=True)
    else:
        click.echo(ctx_text)

    if show_meta:
        click.echo("\n--- metadata ---", err=True)
        click.echo(json.dumps(meta, indent=2), err=True)


# ─────────────────────────────────────────────────── auto-context

AUTO_CONTEXT_DEFAULT_BUDGET = 6_000

@main.command("auto-context")
@click.option("--budget", "-b", default=AUTO_CONTEXT_DEFAULT_BUDGET, type=int,
              help=f"Token budget (default: {AUTO_CONTEXT_DEFAULT_BUDGET})")
@click.option("--output", "-o", default=None, type=click.Path(),
              help="Write context to file instead of stdout")
def auto_context_cmd(budget, output):
    """Build comprehensive project context for automatic injection.

    Uses the 'auto_overview' policy: project map + complete file index +
    300 top symbols + skeletons of key files.  Designed to be injected
    into chatInstructions so every AI interaction starts with full project
    awareness.
    """
    root = find_project_root() or Path.cwd()
    store, cfg, logger = _require_index(root)

    query = "full project overview: all files, key functions, classes, entry points"
    task = "auto_overview"

    # Broad search for maximum symbol coverage
    relevant_symbols = search_symbols(store, query, limit=100)
    relevant_paths = find_related_paths(store, relevant_symbols)

    # Add entry point files that may not appear in search
    for candidate in ("src/ctx/cli.py", "src/ctx/mcp.py", "src/ctx/__init__.py"):
        if candidate not in relevant_paths:
            relevant_paths.append(candidate)

    detected_tool = detect_client_tool()
    buffer = cfg["budget_buffer"]

    ctx_text, meta = build_context(
        store=store,
        root=root,
        task=task,
        query=query,
        relevant_symbols=relevant_symbols,
        relevant_paths=relevant_paths,
        budget=budget,
        buffer_ratio=buffer,
    )

    _tokens_raw = compute_tokens_raw(
        root,
        meta.get("paths_included", []),
        meta["tokens_used"],
        meta["budget"],
    )
    logger.retrieval(
        task,
        relevant_paths,
        meta["tokens_used"],
        meta["budget"],
        tokens_raw=_tokens_raw,
        tool=detected_tool or "unknown",
        query=query,
    )

    if output:
        Path(output).write_text(ctx_text, encoding="utf-8")
        click.echo(f"Auto-context written to {output} ({meta['tokens_used']} tokens)", err=True)
    else:
        click.echo(ctx_text)


# ─────────────────────────────────────────────────────────── show

@main.command()
@click.argument("target", default="map",
                metavar="[map|symbols|file:<path>|symbol:<name>]")
def show(target):
    """Show project map, symbol list, or details for a file/symbol."""
    root = find_project_root() or Path.cwd()
    store, cfg, _ = _require_index(root)

    if target == "map":
        click.echo(build_level0(store, root))

    elif target == "symbols":
        click.echo(build_level1(store))

    elif target.startswith("file:"):
        path_str = Path(target[5:]).as_posix()  # normaliza separadores
        symbols = store.get_symbols_for_file(path_str)
        if not symbols:
            click.echo(f"No symbols found for {path_str}")
        else:
            click.echo(f"Symbols in {path_str}:")
            for row in symbols:
                sig = f"  [{row['kind']}] {row['name']}"
                if row["params"]:
                    sig += row["params"]
                click.echo(sig + f"  (line {row['start_line']})")

    elif target.startswith("symbol:"):
        name = target[7:]
        rows = store.get_symbols_by_name(name)
        if not rows:
            click.echo(f"Symbol '{name}' not found.")
        else:
            for row in rows:
                click.echo(f"[{row['kind']}] {row['name']}{row['params'] or ''}")
                if row["return_type"]:
                    click.echo(f"  -> {row['return_type']}")
                if row["docstring"]:
                    click.echo(f"  doc: {row['docstring'][:200]}")
                click.echo(f"  @ {row['path']}:{row['start_line']}")
    else:
        click.echo(f"Unknown target: {target}. Use map|symbols|file:<path>|symbol:<name>")


# ─────────────────────────────────────────────────────────── stats

@main.command()
def stats():
    """Show index statistics (files, symbols, languages)."""
    root = find_project_root() or Path.cwd()
    store, cfg, _ = _require_index(root)
    s = store.stats()

    click.echo(f"\n{'='*40}")
    click.echo("  Index Statistics")
    click.echo(f"{'='*40}")
    click.echo(f"  {'Files indexed':<20} {s['files']:>6}")
    click.echo(f"  {'Symbols total':<20} {s['symbols']:>6}")

    if s["by_language"]:
        click.echo(f"\n  {'Language':<16} {'Files':>6}  {'% of total':>10}")
        click.echo(f"  {'-'*36}")
        for lang, n in s["by_language"].items():
            pct = n / s["files"] * 100 if s["files"] else 0
            click.echo(f"  {lang:<16} {n:>6}  {pct:>9.0f}%")

    if s["by_kind"]:
        click.echo(f"\n  {'Symbol kind':<16} {'Count':>6}  {'% of total':>10}")
        click.echo(f"  {'-'*36}")
        for kind, n in s["by_kind"].items():
            pct = n / s["symbols"] * 100 if s["symbols"] else 0
            click.echo(f"  {kind:<16} {n:>6}  {pct:>9.0f}%")

    click.echo(f"\n  {'Token budget':<20} {cfg['token_budget']:>6} tokens")
    click.echo(f"  {'Safety buffer':<20} {cfg['budget_buffer']:>5.0%}")


# ─────────────────────────────────────────────────────────── log

@main.command("log")
@click.option("--last", "-n", default=10, help="Ultimos N eventos")
@click.option("--event", "-e", default=None,
              type=click.Choice(["intent", "retrieval", "index", "error"]),
              help="Filtrar por tipo de evento")
def log_cmd(last, event):
    """Mostra historico de uso e estatisticas do log."""
    import json as _json
    root = find_project_root() or Path.cwd()
    lp = log_path(root)
    if not lp.exists():
        click.echo("Nenhum log encontrado. Execute lens index e lens context primeiro.")
        return

    lines = lp.read_text(encoding="utf-8").splitlines()
    records = []
    for line in lines:
        try:
            records.append(_json.loads(line))
        except Exception:
            pass

    if event:
        records = [r for r in records if r.get("event") == event]

    # Estatisticas gerais
    from collections import Counter
    counts = Counter(r["event"] for r in records)
    retrievals = [r for r in records if r["event"] == "retrieval"]
    intents    = [r for r in records if r["event"] == "intent"]

    click.echo(f"\n  Log: {lp}  ({len(lines)} entries)")
    click.echo("  Events: " + "  ".join(f"{k}={v}" for k, v in counts.items()))

    if retrievals:
        avg_tok  = sum(r["tokens_used"] for r in retrievals) / len(retrievals)
        avg_util = sum(r["utilization"] for r in retrievals) / len(retrievals)
        max_tok  = max(r["tokens_used"] for r in retrievals)
        min_tok  = min(r["tokens_used"] for r in retrievals)
        click.echo(f"\n  Token usage across {len(retrievals)} queries:")
        click.echo(f"  {'Avg':<8} {avg_tok:>5.0f}t   Utilization {avg_util:.0%}")
        click.echo(f"  {'Min':<8} {min_tok:>5}t")
        click.echo(f"  {'Max':<8} {max_tok:>5}t")

        task_counts = Counter(r["task"] for r in retrievals)
        click.echo(f"\n  {'Task':<16} {'Count':>6} {'Avg tokens':>11} {'Avg saved':>10}")
        click.echo(f"  {'-'*46}")
        for task, n in sorted(task_counts.items(), key=lambda x: -x[1]):
            recs = [r for r in retrievals if r["task"] == task]
            avg_t = sum(r["tokens_used"] for r in recs) / len(recs)
            avg_b = sum(r["budget"] for r in recs) / len(recs)
            saved = (1 - avg_t / avg_b) * 100 if avg_b else 0
            click.echo(f"  {task:<16} {n:>6} {avg_t:>10.0f}t {saved:>9.0f}%")

    if intents:
        avg_conf = sum(r["confidence"] for r in intents) / len(intents)
        click.echo(f"\n  Intent detection: {len(intents)} queries, avg confidence {avg_conf:.0%}")

    # Ultimos N eventos
    recent = records[-last:]
    click.echo(f"\n  Last {min(last, len(recent))} events:")
    click.echo(f"  {'Time':<10} {'Event':<10} {'Details'}")
    click.echo(f"  {'-'*60}")
    for r in recent:
        import time as _t
        ts = _t.strftime("%d/%m %H:%M", _t.localtime(r["ts"]))
        ev = r["event"]
        if ev == "retrieval":
            saved = (1 - r["tokens_used"]/r["budget"])*100 if r["budget"] else 0
            detail = f"task={r['task']:<13} {r['tokens_used']:>5}t  saved {saved:.0f}%"
        elif ev == "intent":
            detail = f"task={r['task']:<13} conf={r['confidence']:.0%}  {r['query'][:35]!r}"
        elif ev == "index":
            detail = r['path'][:45] + (" (skip)" if r.get("skipped") else "")
        else:
            detail = r.get("message", "")[:55]
        click.echo(f"  {ts:<10} {ev:<10} {detail}")


# ─────────────────────────────────────────────────────────── status

@main.command()
def status():
    """Project status: index health, token savings per task, recent queries."""
    import json as _json

    root = find_project_root() or Path.cwd()
    dp   = db_path(root)

    def sep(title=""):
        if title:
            click.echo(f"\n  -- {title} " + "-" * (30 - len(title)))
        else:
            click.echo("  " + "-" * 34)

    click.echo(f"\n  Context Lens  /  {root.name}")
    sep()

    if not dp.exists():
        click.echo("  Index not found — run: lens index\n")
        return

    store, cfg, _ = _require_index(root)
    s     = store.stats()
    db_kb = dp.stat().st_size // 1024

    import time as _t
    last_ts = _t.strftime("%d/%m %H:%M", _t.localtime(s["last_indexed"])) if s.get("last_indexed") else "never"
    langs   = ", ".join(f"{lang}({n})" for lang, n in list(s["by_language"].items())[:4])

    click.echo(f"  {s['files']} files  {s['symbols']} symbols  {db_kb} KB  |  indexed {last_ts}  |  {langs}")

    # carrega log
    lp = log_path(root)
    retrievals = []
    if lp.exists():
        for ln in lp.read_text(encoding="utf-8").splitlines():
            try:
                r = _json.loads(ln)
                if r.get("event") == "retrieval":
                    retrievals.append(r)
            except Exception:
                pass

    if not retrievals:
        sep("Projected savings  (no queries yet)")
        budget = cfg.get("token_budget", 8000)
        _raw_str = store.get_meta("project_tokens_total")
        if _raw_str:
            raw_tokens = int(_raw_str)
            click.echo(f"  Raw project  ~{raw_tokens:,} tokens  (real count,  {s['files']} files)")
        else:
            total_bytes = s.get("total_bytes", 0)
            raw_tokens = max(1, total_bytes // 4)
            click.echo(f"  Raw project  ~{raw_tokens:,} tokens  ({total_bytes // 1024} KB  /  {s['files']} files)")
        lens_tokens = min(budget, raw_tokens)
        pct = (raw_tokens - lens_tokens) / raw_tokens * 100 if raw_tokens else 0
        click.echo(f"  Lens budget  {budget:,} tokens")
        click.echo(f"  Est. saving  ~{pct:.0f}%  (~{raw_tokens - lens_tokens:,} tokens por query)")
        click.echo("\n  Use MCP (lens_context) ou: lens context \"sua pergunta\"\n")
        return

    # sessão = queries desde a última indexação
    last_idx_ts = s.get("last_indexed") or 0
    session = [r for r in retrievals if r["ts"] >= last_idx_ts]

    _proj_str = store.get_meta("project_tokens_total")
    _proj_tokens = int(_proj_str) if _proj_str else None

    def _raw(r: dict) -> int:
        """Tokens brutos reais: do log se disponível, senão usa total do projeto."""
        raw = r.get("tokens_raw", 0)
        return raw if raw > 0 else (_proj_tokens or r["budget"])

    total_used  = sum(r["tokens_used"] for r in retrievals)
    total_raw   = sum(_raw(r) for r in retrievals)
    total_saved = max(0, total_raw - total_used)
    avg_all     = max(0.0, (1 - total_used / total_raw) * 100) if total_raw else 0

    sess_used   = sum(r["tokens_used"] for r in session)
    sess_raw    = sum(_raw(r) for r in session)
    sess_saved  = max(0, sess_raw - sess_used)
    avg_sess    = max(0.0, (1 - sess_used / sess_raw) * 100) if sess_raw else 0

    sep("Economy")
    click.echo(f"  This session  {len(session):>3} queries   saved ~{sess_saved:,} tokens  ({avg_sess:.0f}%)")
    click.echo(f"  All time      {len(retrievals):>3} queries   saved ~{total_saved:,} tokens  ({avg_all:.0f}%)")

    sep("By task  (all time)")
    click.echo(f"  {'Task':<16} {'n':>3}  {'Avg used':>9}  {'Saved':>6}  {'':>10}")
    for task in ["navigate", "explain", "generate_test", "refactor", "bugfix"]:
        recs = [r for r in retrievals if r["task"] == task]
        if not recs:
            continue
        avg_u   = sum(r["tokens_used"] for r in recs) / len(recs)
        avg_raw = sum(_raw(r) for r in recs) / len(recs)
        pct     = (1 - avg_u / avg_raw) * 100 if avg_raw else 0
        bar     = "#" * int(pct / 10) + "." * (10 - int(pct / 10))
        click.echo(f"  {task:<16} {len(recs):>3}  {avg_u:>7.0f}t   {pct:>4.0f}%  {bar}")

    sep("Last queries")
    for r in retrievals[-4:]:
        ts    = _t.strftime("%d/%m %H:%M", _t.localtime(r["ts"]))
        r_raw = _raw(r)
        saved = (1 - r["tokens_used"] / r_raw) * 100 if r_raw else 0
        click.echo(f"  {ts}  {r['task']:<14} {r['tokens_used']:>5}t  saved {saved:.0f}%")

    click.echo("")


# ─────────────────────────────────────────────────────────── watch

@main.command()
@click.option("--interval", "-i", default=30, type=int,
              help="Seconds between checks (default: 30)")
@click.option("--verbose", "-v", is_flag=True)
def watch(interval, verbose):
    """Watch for file changes and auto-reindex. Runs until Ctrl+C."""
    root = find_project_root() or Path.cwd()
    cfg = load_config(root)
    dp = db_path(root)
    ctx_dir(root).mkdir(parents=True, exist_ok=True)

    conn = init_db(dp)
    store = Store(conn)
    logger = CtxLogger(log_path(root))

    extensions = cfg["index_extensions"]
    ignore_dirs = set(cfg["ignore_dirs"])
    max_kb = cfg["max_file_size_kb"]

    click.echo(f"Watching {root}  (interval: {interval}s)  Ctrl+C to stop")

    def _reindex():
        changed = 0
        for file_path in walk_project(root, extensions, ignore_dirs, max_kb):
            rel = file_path.relative_to(root).as_posix()
            try:
                current_hash = hash_file(file_path)
            except OSError:
                continue
            if store.get_file_hash(rel) == current_hash:
                continue
            symbols, lang = extract_symbols(file_path)
            file_id = store.upsert_file(rel, current_hash, lang, file_path.stat().st_size)
            if symbols:
                for s in symbols:
                    s["file_id"] = file_id
                    s["path"] = rel
                store.insert_symbols_batch(symbols)
            changed += 1
            if verbose:
                click.echo(f"  re-indexed: {rel}")
            logger.index(rel, len(symbols))
        if changed:
            store.commit()
            click.echo(f"[{time.strftime('%H:%M:%S')}] {changed} file(s) re-indexed")
        elif verbose:
            click.echo(f"[{time.strftime('%H:%M:%S')}] no changes")

    try:
        while True:
            _reindex()
            time.sleep(interval)
    except KeyboardInterrupt:
        click.echo("\nWatch stopped.")


# ─────────────────────────────────────────────────────────── memory

@main.group()
def memory():
    """Manage memory_lite entries."""


@memory.command("set")
@click.argument("kind", type=click.Choice(["map", "ref", "hotspot", "note", "rule"]))
@click.argument("key")
@click.argument("value")
@click.option("--ttl", default=None, type=int, help="Time-to-live in seconds")
def memory_set(kind, key, value, ttl):
    """Add or update a memory entry."""
    root = find_project_root() or Path.cwd()
    store, _, _ = _require_index(root)
    mem = MemoryLite(store)
    mem.set(kind, key, value, ttl)
    click.echo(f"Saved [{kind}] {key}: {value}")


@memory.command("list")
def memory_list():
    """List all memory entries."""
    root = find_project_root() or Path.cwd()
    store, _, _ = _require_index(root)
    mem = MemoryLite(store)
    rows = mem.list_all()
    if not rows:
        click.echo("No memory entries.")
        return
    for r in rows:
        ttl_info = ""
        import time as _t
        if r["expires_at"]:
            remaining = int(r["expires_at"] - _t.time())
            ttl_info = f" [expires in {remaining}s]"
        key_part = f"{r['key']}: " if r["key"] else ""
        click.echo(f"  [{r['id']}] ({r['kind']}) {key_part}{r['value']}{ttl_info}")


@memory.command("delete")
@click.argument("id", type=int)
def memory_delete(id):
    """Delete a memory entry by ID."""
    root = find_project_root() or Path.cwd()
    store, _, _ = _require_index(root)
    mem = MemoryLite(store)
    mem.delete(id)
    click.echo(f"Deleted memory entry {id}")


@memory.command("show")
def memory_show():
    """Show memory as formatted context block."""
    root = find_project_root() or Path.cwd()
    store, _, _ = _require_index(root)
    mem = MemoryLite(store)
    text = mem.format_for_context()
    click.echo(text if text else "(empty)")


# ─────────────────────────────────────────────────────────── config

@main.command("config")
@click.argument("key", default=None, required=False)
@click.argument("value", default=None, required=False)
def config_cmd(key, value):
    """Show or set config values. `ctx config` shows all."""
    root = find_project_root() or Path.cwd()
    cfg = load_config(root)

    if key is None:
        click.echo(json.dumps(cfg, indent=2))
        return

    if value is None:
        if key in cfg:
            click.echo(f"{key} = {cfg[key]}")
        else:
            click.echo(f"Unknown key: {key}")
        return

    # Try to parse value as JSON (for lists, ints, etc.)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = value

    cfg[key] = parsed
    save_config(root, cfg)
    click.echo(f"Set {key} = {parsed}")


# ─────────────────────────────────────────────────────────── analytics

@main.command("analytics")
@click.option("--period", default="week",
              type=click.Choice(["day", "week", "month", "all"]),
              help="Time period to analyse (default: week)")
@click.option("--export", default=None,
              type=click.Choice(["json", "csv"]),
              help="Export data to stdout in JSON or CSV format")
def analytics_cmd(period, export):
    """Show token savings analytics for this project.

    Reads the .ctx/log.jsonl file and computes how many tokens were saved,
    which task types are most efficient, and which files are queried most.
    """
    from .analytics import compute_summary, format_report

    root = find_project_root() or Path.cwd()
    lp = log_path(root)
    if not lp.exists():
        click.echo("No query log found. Run `lens context` a few times first.", err=True)
        return

    _raw_str = None
    dp = db_path(root)
    if dp.exists():
        conn = init_db(dp)
        _raw_str = Store(conn).get_meta("project_tokens_total")

    project_tokens = int(_raw_str) if _raw_str else 0
    summary = compute_summary(lp, project_tokens)

    if export == "json":
        click.echo(json.dumps(summary, indent=2))
        return
    if export == "csv":
        import csv
        import io
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["date", "queries", "tokens_used", "tokens_saved", "saving_pct"])
        for row in summary.get("by_day", []):
            writer.writerow([row["date"], row["queries"], row["tokens_used"],
                             row["tokens_saved"], f"{row['saving_pct']:.1f}"])
        click.echo(buf.getvalue())
        return

    click.echo(format_report(summary, period=period))


# ─────────────────────────────────────────────────────────── health

@main.command("health")
def health_cmd():
    """Check project health: index freshness, symbol density, budget advice.

    Surfaces actionable recommendations to keep Context Lens working well.
    """
    from .health import check_health, format_health_report

    root = find_project_root() or Path.cwd()
    dp = db_path(root)
    if not dp.exists():
        click.echo("Index not found. Run `lens index` first.", err=True)
        return

    conn = init_db(dp)
    store = Store(conn)
    cfg = load_config(root)
    report = check_health(store, root, cfg)
    click.echo(format_health_report(report))


# ─────────────────────────────────────────────────────────── install

_ALL_GLOBAL_IDES = ["claude-desktop", "cursor", "vscode", "zed", "continue", "jetbrains"]
_ALL_PROJECT_IDES = ["cursor", "vscode", "continue", "claude-code"]
_ALL_IDES = sorted(set(_ALL_GLOBAL_IDES + _ALL_PROJECT_IDES))


@main.command("install")
@click.option("--global", "global_", is_flag=True,
              help="Install in global IDE configs (home directory).")
@click.option("--ide", default="all",
              type=click.Choice(_ALL_IDES + ["all"]),
              help="IDE to configure (default: all detected).")
@click.option("--dry-run", is_flag=True,
              help="Show what would be done without writing any files.")
@click.option("--list-ides", is_flag=True,
              help="List detected IDEs and exit.")
def install_cmd(global_, ide, dry_run, list_ides):
    """Install lens-mcp in IDE/CLI MCP configs — zero manual editing.

    \b
    Examples:
      lens install                        # project-local, auto-detect IDEs
      lens install --global               # global install for all detected IDEs
      lens install --global --ide cursor  # global Cursor only
      lens install --dry-run              # preview without writing

    Supported IDEs (global):  claude-desktop, cursor, vscode, zed, continue, jetbrains
    Supported IDEs (project): cursor, vscode, continue, claude-code

    After installing, restart your IDE to load the new MCP server.
    Verify with: lens status
    """
    from .installer import (
        install, format_results,
        detect_global_ides, detect_project_ides,
    )

    root = find_project_root() or Path.cwd()

    if list_ides:
        g = detect_global_ides()
        p = detect_project_ides(root)
        click.echo("Detected IDEs (global):  " + (", ".join(g) if g else "(none)"))
        click.echo("Detected IDEs (project): " + (", ".join(p) if p else "(none)"))
        return

    scope = "global" if global_ else f"project ({root.name})"
    click.echo(f"\n  Context Lens — MCP Install  [{scope}]")
    click.echo("  " + "─" * 40)
    if dry_run:
        click.echo("  DRY RUN — no files will be written\n")

    results = install(root=root, global_=global_, ide=ide, dry_run=dry_run)

    if not results:
        click.echo("  No IDEs matched. Try --list-ides to see what's detected.")
        return

    click.echo(format_results(results))

    installed = sum(1 for r in results for _, s in r.actions if s == "installed")
    would_install = sum(1 for r in results for _, s in r.actions if s == "dry_run")
    already = sum(1 for r in results for _, s in r.actions if s == "already_installed")
    click.echo()
    if dry_run:
        click.echo(f"  Would install in {would_install} config(s), {already} already configured.")
    else:
        if installed:
            click.echo(f"  Installed in {installed} config(s). Restart your IDE to apply.")
        if already:
            click.echo(f"  {already} config(s) already had context-lens — nothing changed.")
        if not installed and not already:
            click.echo("  Nothing installed (check --list-ides to debug detection).")
        if installed and not global_:
            click.echo("\n  Tip: run `lens install --global` to also configure global IDE settings.")
    click.echo()
