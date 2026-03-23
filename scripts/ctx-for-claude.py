#!/usr/bin/env python
"""
ctx-for-claude — gera contexto otimizado e copia para clipboard.

Uso:
    python scripts/ctx-for-claude.py "fix the bug in extract_symbols"
    python scripts/ctx-for-claude.py "explain how Budget works" --task explain
    python scripts/ctx-for-claude.py "write tests for Store" --file src/ctx/db/store.py
    python scripts/ctx-for-claude.py "refactor walker" --budget 12000

O contexto gerado e' copiado para o clipboard e tambem salvo em .ctx/last_context.md.
Cole no Claude Code com: Ctrl+V  ou  /context (se configurado como slash command)
"""
import sys
import os
import subprocess
import argparse
from pathlib import Path

# Garante que o pacote e' encontrado mesmo sem instalar
_root = Path(__file__).parent.parent
sys.path.insert(0, str(_root / "src"))


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


def main():
    parser = argparse.ArgumentParser(
        description="Gera contexto otimizado para Claude Code."
    )
    parser.add_argument("query", help="Descricao da tarefa")
    parser.add_argument("-t", "--task",
                        choices=["explain", "bugfix", "refactor", "generate_test", "navigate"],
                        default=None, help="Tipo de tarefa (auto-detectado se omitido)")
    parser.add_argument("-b", "--budget", type=int, default=None,
                        help="Orcamento de tokens (padrao: config do projeto)")
    parser.add_argument("-f", "--file", action="append", dest="files", default=[],
                        metavar="PATH", help="Incluir arquivo especifico (pode repetir)")
    parser.add_argument("--no-clip", action="store_true",
                        help="Nao copiar para clipboard")
    parser.add_argument("--show-meta", action="store_true",
                        help="Mostrar metadata de uso de tokens")
    parser.add_argument("-o", "--output", default=None,
                        help="Arquivo de saida (padrao: .ctx/last_context.md)")
    args = parser.parse_args()

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
        print("[erro] Nao foi encontrado projeto com .ctx/. Execute: ctx init && ctx index")
        sys.exit(1)

    dp = db_path(root)
    if not dp.exists():
        print(f"[erro] Indice nao encontrado em {dp}")
        print("Execute: ctx index")
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

    # Arquivos forcados
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

    # Salva em arquivo
    out_path = Path(args.output) if args.output else root / ".ctx" / "last_context.md"
    out_path.write_text(ctx_text, encoding="utf-8")

    # Copia para clipboard
    clipped = False
    if not args.no_clip:
        clipped = copy_to_clipboard(ctx_text)

    # Summary
    util = meta["utilization"]
    util_bar = "#" * int(util * 20) + "." * (20 - int(util * 20))
    print(f"\n[ctx] task={task} | tokens={meta['tokens_used']}/{meta['budget']} [{util_bar}] {util:.0%}")
    if meta["paths_included"]:
        print(f"[ctx] arquivos incluidos: {', '.join(meta['paths_included'][:5])}")
    print(f"[ctx] salvo em: {out_path}")
    if clipped:
        print("[ctx] copiado para o clipboard -- cole no Claude Code!")
    else:
        print(f"[ctx] (clipboard nao disponivel) -- use: cat {out_path}")

    if args.show_meta:
        import json
        print("\n--- metadata ---")
        print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
