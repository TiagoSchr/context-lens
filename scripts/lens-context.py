#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
lens-context — gera contexto otimizado para multiplos targets de IA.

Uso:
    python scripts/lens-context.py "fix the bug in extract_symbols"
    python scripts/lens-context.py "explain how Budget works" --task explain
    python scripts/lens-context.py "write tests for Store" --file src/ctx/db/store.py
    python scripts/lens-context.py "refactor walker" --budget 12000 --target chatgpt
    python scripts/lens-context.py "add feature X" --target copilot

Targets suportados:
    auto     — detecta automaticamente (VS Code ativo -> copilot, senao -> clipboard)
    claude   — copia para clipboard (comportamento original)
    copilot  — salva em .ctx/ctx.md e abre no VS Code
    chatgpt  — salva em .ctx/ctx.md, copia para clipboard e imprime link para chat.openai.com
    codex    — igual a chatgpt

O contexto gerado tambem e' sempre salvo em .ctx/last_context.md como backup.
"""
import sys
import os
import subprocess
import argparse
import urllib.parse
from pathlib import Path

# Garante que o pacote e' encontrado mesmo sem instalar
_root = Path(__file__).parent.parent
sys.path.insert(0, str(_root / "src"))


# ---------------------------------------------------------------------------
# Helpers de plataforma
# ---------------------------------------------------------------------------

def copy_to_clipboard(text: str) -> bool:
    """Copia texto para o clipboard. Funciona em Windows, Mac e Linux."""
    try:
        if sys.platform == "win32":
            subprocess.run(["clip"], input=text.encode("utf-16"), check=True)
            return True
        elif sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
            return True
        else:
            # Linux: tenta xclip ou xsel
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
    # VS Code define a variavel de ambiente TERM_PROGRAM ou VSCODE_PID
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
    """Gera link direto para chat.openai.com com a query como mensagem inicial."""
    # O ChatGPT nao aceita query pre-preenchida via URL publica de forma oficial,
    # mas o link base e' util para referencia rapida.
    base = "https://chat.openai.com/"
    # Inclui a query como fragment de referencia para o usuario
    fragment = urllib.parse.quote(query[:200])
    return f"{base}?model=gpt-4o#query:{fragment}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Gera contexto otimizado para multiplos assistentes de IA.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
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
    parser.add_argument(
        "--no-clip", action="store_true",
        help="Nao copiar para clipboard",
    )
    parser.add_argument(
        "--show-meta", action="store_true",
        help="Mostrar metadata de uso de tokens",
    )
    parser.add_argument(
        "-o", "--output", default=None,
        help="Arquivo de saida principal (padrao: depende do target)",
    )
    args = parser.parse_args()

    # Resolve target concreto
    target = resolve_target(args.target)
    print(f"[lens] target={target}", file=sys.stderr)

    # Importa modulos do ctx
    try:
        from ctx.config import find_project_root, load_config, db_path, log_path
        from ctx.db.schema import init_db
        from ctx.db.store import Store
        from ctx.retrieval.intent import classify_intent
        from ctx.retrieval.search import search_symbols, find_related_paths
        from ctx.context.builder import build_context
        from ctx.log import CtxLogger
    except ImportError as e:
        print(f"[erro] Nao foi possivel importar ctx: {e}")
        print("Execute: pip install -e '.[parse]' no diretorio do context_compiler")
        sys.exit(1)

    root = find_project_root(Path.cwd())
    if root is None:
        print("[erro] Nao foi encontrado projeto com .ctx/. Execute: lens init && lens index")
        sys.exit(1)

    dp = db_path(root)
    if not dp.exists():
        print(f"[erro] Indice nao encontrado em {dp}")
        print("Execute: lens index")
        sys.exit(1)

    cfg = load_config(root)
    conn = init_db(dp)
    store = Store(conn)
    logger = CtxLogger(log_path(root))

    # Intent
    if args.task:
        task = args.task
        confidence = 1.0
    else:
        task, confidence = classify_intent(args.query)
        print(f"[intent] {task} ({confidence:.0%})", file=sys.stderr)

    logger.intent(args.query, task, confidence)

    # Busca simbolos relevantes
    relevant_symbols = search_symbols(store, args.query, limit=50)
    relevant_paths = find_related_paths(store, relevant_symbols)

    # Arquivos forcados pelo usuario
    for f in args.files:
        p = Path(f).as_posix()
        if p not in relevant_paths:
            relevant_paths.insert(0, p)

    budget = args.budget or cfg["token_budget"]

    # Monta contexto
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
    logger.retrieval(task, relevant_paths, meta["tokens_used"], meta["budget"])

    # --- Determina caminhos de saida ---
    ctx_dir = root / ".ctx"
    ctx_dir.mkdir(parents=True, exist_ok=True)

    backup_path = ctx_dir / "last_context.md"

    if args.output:
        primary_path = Path(args.output)
    elif target == "copilot":
        primary_path = ctx_dir / "ctx.md"
    else:
        primary_path = ctx_dir / "last_context.md"

    # Salva arquivo principal
    primary_path.write_text(ctx_text, encoding="utf-8")

    # Salva backup (se diferente do principal)
    if primary_path.resolve() != backup_path.resolve():
        backup_path.write_text(ctx_text, encoding="utf-8")

    # --- Acoes especificas por target ---
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
        # claude ou qualquer outro: comportamento padrao (clipboard)
        if not args.no_clip:
            clipped = copy_to_clipboard(ctx_text)

    # --- Summary ---
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
            print("[ctx] ctx.md aberto no VS Code — o Copilot ja pode ver o contexto!")
        else:
            print(f"[ctx] (VS Code CLI nao encontrado) — abra manualmente: {primary_path}")
    elif target in ("chatgpt", "codex"):
        if clipped:
            print("[ctx] contexto copiado para o clipboard — cole no ChatGPT!")
        else:
            print(f"[ctx] (clipboard nao disponivel) — use: cat {primary_path}")
    else:
        if clipped:
            print("[ctx] copiado para o clipboard — cole no Claude Code!")
        else:
            print(f"[ctx] (clipboard nao disponivel) — use: cat {primary_path}")

    if args.show_meta:
        import json
        print("\n--- metadata ---")
        print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
