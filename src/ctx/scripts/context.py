"""Context generator for Claude, Copilot and Codex targets."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path


def _run_lens_silent(args: list[str], cwd: Path) -> None:
    """Chama lens via Python API (sem subprocess) com fallback para CLI."""
    import io
    import os
    from contextlib import redirect_stderr, redirect_stdout
    buf = io.StringIO()
    old_cwd = os.getcwd()
    try:
        try:
            from ctx import cli as _cli  # absolute (package instalado)
        except ImportError:
            from .. import cli as _cli  # relative (editable install)
        os.chdir(str(cwd))
        with redirect_stdout(buf), redirect_stderr(buf):
            _cli.main.main(args=args, prog_name="lens", standalone_mode=False)
    except Exception:
        os.chdir(str(cwd))
        subprocess.run(["lens"] + args, capture_output=True, cwd=str(cwd))
    finally:
        try:
            os.chdir(old_cwd)
        except Exception:
            pass


def ensure_index_ready() -> None:
    """Auto-init silencioso para uso zero-config em projetos novos."""
    db_file = Path.cwd() / ".ctx" / "index.db"
    if db_file.exists():
        return
    root = Path.cwd()
    if not (root / ".ctx").exists():
        _run_lens_silent(["init"], root)
    _run_lens_silent(["index", "--quiet"], root)


def copy_to_clipboard(text: str) -> bool:
    """Copia texto para o clipboard. Funciona em Windows, Mac e Linux."""
    try:
        if sys.platform == "win32":
            subprocess.run(["clip"], input=text.encode("utf-16"), check=True)
            return True
        if sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
            return True
        for cmd in (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
            try:
                subprocess.run(cmd, input=text.encode("utf-8"), check=True)
                return True
            except FileNotFoundError:
                continue
    except Exception:
        pass
    return False


def open_in_vscode(file_path: Path) -> bool:
    """Abre arquivo no VS Code via CLI."""
    try:
        subprocess.run(["code", str(file_path)], check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def is_vscode_active() -> bool:
    """Detecta se o processo esta sendo executado de dentro do VS Code."""
    return (
        os.environ.get("TERM_PROGRAM", "").lower() == "vscode"
        or "VSCODE_PID" in os.environ
        or "VSCODE_IPC_HOOK_CLI" in os.environ
    )


def resolve_target(target: str) -> str:
    """Resolve 'auto' para o target concreto adequado ao ambiente."""
    if target != "auto":
        return target
    if is_vscode_active():
        return "copilot"
    return "claude"


def chatgpt_link(query: str) -> str:
    """Gera link util para abrir o ChatGPT."""
    base = "https://chat.openai.com/"
    fragment = urllib.parse.quote(query[:200])
    return f"{base}?model=gpt-4o#query:{fragment}"


def main(argv: list[str] | None = None) -> int:
    ensure_index_ready()

    parser = argparse.ArgumentParser(
        description="Gera contexto otimizado para multiplos assistentes de IA.",
    )
    parser.add_argument("query", help="Descricao da tarefa")
    parser.add_argument(
        "--target", "-T",
        choices=["auto", "claude", "copilot", "chatgpt", "codex"],
        default="auto",
        help="Assistente de destino (padrao: auto)",
    )
    parser.add_argument(
        "-t", "--task",
        choices=["explain", "bugfix", "refactor", "generate_test", "navigate"],
        default=None,
        help="Tipo de tarefa (auto-detectado se omitido)",
    )
    parser.add_argument(
        "-b", "--budget", type=int, default=None,
        help="Orcamento de tokens (padrao: config do projeto)",
    )
    parser.add_argument(
        "-f", "--file", action="append", dest="files", default=[],
        metavar="PATH",
        help="Incluir arquivo especifico (pode repetir)",
    )
    parser.add_argument("--no-clip", action="store_true", help="Nao copiar para clipboard")
    parser.add_argument("--show-meta", action="store_true", help="Mostrar metadata de uso de tokens")
    parser.add_argument(
        "-o", "--output", default=None,
        help="Arquivo de saida principal (padrao: depende do target)",
    )
    args = parser.parse_args(argv)

    target = resolve_target(args.target)
    budget_target = "codex" if target in ("chatgpt", "codex") else target
    os.environ.setdefault("LENS_TARGET", budget_target)
    print(f"[lens] target={target}", file=sys.stderr)

    try:
        from ..config import find_project_root, load_config, db_path, log_path, normalize_target_name
        from ..db.schema import init_db
        from ..db.store import Store
        from ..retrieval.intent import classify_intent
        from ..retrieval.search import search_symbols, find_related_paths
        from ..context.builder import build_context
        from ..context.budget import compute_tokens_raw
        from ..log import CtxLogger
    except ImportError as exc:
        print(f"[erro] Nao foi possivel importar ctx: {exc}")
        print("Execute: pip install -e '.[parse]' no diretorio do context_compiler")
        return 1

    root = find_project_root(Path.cwd())
    if root is None:
        print("[erro] Nao foi encontrado projeto com .ctx/. Execute: lens init && lens index")
        return 1

    dp = db_path(root)
    if not dp.exists():
        print(f"[erro] Indice nao encontrado em {dp}")
        print("Execute: lens index")
        return 1

    cfg = load_config(root)
    conn = init_db(dp)
    store = Store(conn)
    logger = CtxLogger(log_path(root))

    if args.task:
        task = args.task
        confidence = 1.0
    else:
        task, confidence = classify_intent(args.query)
        print(f"[intent] {task} ({confidence:.0%})", file=sys.stderr)

    logger.intent(args.query, task, confidence)

    relevant_symbols = search_symbols(store, args.query, limit=50)
    relevant_paths = find_related_paths(store, relevant_symbols)
    for file_name in args.files:
        path_str = Path(file_name).as_posix()
        if path_str not in relevant_paths:
            relevant_paths.insert(0, path_str)

    budget = args.budget or cfg["token_budget"]
    ctx_text, meta = build_context(
        store=store,
        root=root,
        task=task,
        query=args.query,
        relevant_symbols=relevant_symbols,
        relevant_paths=relevant_paths,
        budget=budget,
        buffer_ratio=cfg["budget_buffer"],
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
        tool=normalize_target_name(budget_target) or budget_target,
        query=args.query,
    )

    ctx_dir = root / ".ctx"
    ctx_dir.mkdir(parents=True, exist_ok=True)
    backup_path = ctx_dir / "last_context.md"

    if args.output:
        primary_path = Path(args.output)
    elif target == "copilot":
        primary_path = ctx_dir / "ctx.md"
    else:
        primary_path = backup_path

    primary_path.write_text(ctx_text, encoding="utf-8")
    if primary_path.resolve() != backup_path.resolve():
        backup_path.write_text(ctx_text, encoding="utf-8")

    clipped = False
    vscode_opened = False
    if target == "copilot":
        vscode_opened = open_in_vscode(primary_path)
    elif target in ("chatgpt", "codex"):
        if not args.no_clip:
            clipped = copy_to_clipboard(ctx_text)
        link = chatgpt_link(args.query)
        print(f"\n[lens] Link ChatGPT: {link}")
        print("[lens] Abra o link, cole o contexto do clipboard e inicie a conversa.")
    else:
        if not args.no_clip:
            clipped = copy_to_clipboard(ctx_text)

    util = meta["utilization"]
    util_bar = "#" * int(util * 20) + "." * (20 - int(util * 20))
    print(f"\n[ctx] task={task} | tokens={meta['tokens_used']}/{meta['budget']} [{util_bar}] {util:.0%}")
    if meta["paths_included"]:
        print(f"[ctx] arquivos incluidos: {', '.join(meta['paths_included'][:5])}")
    print(f"[ctx] salvo em: {primary_path}")
    if backup_path.resolve() != primary_path.resolve():
        print(f"[ctx] backup em: {backup_path}")

    if target == "copilot":
        if vscode_opened:
            print("[ctx] ctx.md aberto no VS Code - o Copilot ja pode ver o contexto!")
        else:
            print(f"[ctx] (VS Code CLI nao encontrado) - abra manualmente: {primary_path}")
    elif target in ("chatgpt", "codex"):
        if clipped:
            print("[ctx] contexto copiado para o clipboard - cole no ChatGPT!")
        else:
            print(f"[ctx] (clipboard nao disponivel) - use: cat {primary_path}")
    else:
        if clipped:
            print("[ctx] copiado para o clipboard - cole no Claude Code!")
        else:
            print(f"[ctx] (clipboard nao disponivel) - use: cat {primary_path}")

    if args.show_meta:
        print("\n--- metadata ---")
        print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
