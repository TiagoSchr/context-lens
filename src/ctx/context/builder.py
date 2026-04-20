"""
Context assembler — budget-driven, never artificially truncates.

Strategy per task (v2: adaptive ranking):
  1. Level1 (all relevant signatures) — lightweight baseline, always first
  2. Rank files by relevance score (query density + symbol count + entry-point boost)
  3. For each ranked file:
       - High-score files get full source (level3) first
       - Mid-score files get skeleton (level2)
       - If budget < 20% remaining, force level1-only for remaining files
  4. Fill budget until exhausted — never cut off when budget allows.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any

from .budget import Budget
from .levels import build_level0, build_level1, build_level2, build_level3, build_file_index, _read_source
from .ranking import rank_paths
from ..retrieval.policy import POLICIES, TaskPolicy
from ..retrieval.search import expand_paths_cross_file
from ..memory.lite import format_context_block


def _is_test_path(path: str) -> bool:
    p = path.replace("\\", "/")
    return p.startswith("tests/") or "/test_" in p or p.startswith("test_")


def build_context(
    store: Any,
    root: Path,
    task: str,
    query: str,
    relevant_symbols: list | None = None,
    relevant_paths: list[str] | None = None,
    budget: int = 8000,
    buffer_ratio: float = 0.12,
) -> tuple[str, dict]:
    """
    Assemble context for a given task and query.
    Budget-driven: fills as much as the budget allows, never truncates arbitrarily.
    Returns (context_text, metadata).
    """
    b = Budget(budget, buffer_ratio)
    policy: TaskPolicy = POLICIES.get(task, POLICIES["explain"])
    sections: list[str] = []
    used_paths: list[str] = []

    # ── Normaliza paths por task ────────────────────────────────────────────
    if task == "generate_test" and relevant_paths:
        # Fonte primeiro, testes como referência
        src   = [p for p in relevant_paths if not _is_test_path(p)]
        tests = [p for p in relevant_paths if _is_test_path(p)]
        relevant_paths = src + tests

    if task in ("bugfix", "refactor") and relevant_paths:
        # Expande com imports e callers para cobrir bugs cross-file
        relevant_paths = expand_paths_cross_file(
            store, root, relevant_paths, relevant_symbols or [], max_expand=4
        )

    # ── Adaptive ranking: order paths by relevance score ────────────────────
    if relevant_paths:
        relevant_paths = rank_paths(
            relevant_paths, relevant_symbols or [], query
        )

    # ── Header ──────────────────────────────────────────────────────────────
    header = f"# Context — task={task} | query={query[:80]}\n"
    b.consume(header)
    sections.append(header)

    # ── Memory: project rules/notes ──────────────────────────────────────────
    mem_rows = store.memory_list()
    if mem_rows:
        mem_block = format_context_block(mem_rows)
        if b.consume(mem_block):
            sections.append(mem_block)

    # ── Level 0: project map ─────────────────────────────────────────────────
    if policy.use_level0:
        l0 = build_level0(store, root)
        if b.consume(l0):
            sections.append(l0)

    # ── File index: compact listing of ALL files ─────────────────────────────
    if policy.use_file_index:
        fi = build_file_index(store)
        if b.consume(fi):
            sections.append(fi)

    # ── Level 1: assinaturas — foca nos arquivos relevantes ──────────────────
    if policy.use_level1:
        prioritized = []
        seen_ids: set = set()

        # Símbolos dos arquivos relevantes primeiro (máximo relevante) — batch query
        if relevant_paths:
            batch_paths = relevant_paths[:policy.level2_files + 2]
            batch = store.get_symbols_for_files(batch_paths)
            for p_str in batch_paths:
                for sym in batch.get(p_str, []):
                    if sym["id"] not in seen_ids:
                        seen_ids.add(sym["id"])
                        prioritized.append(sym)

        # Complementa com resultados da busca FTS (sem duplicar)
        if relevant_symbols:
            for sym in relevant_symbols:
                if sym["id"] not in seen_ids:
                    seen_ids.add(sym["id"])
                    prioritized.append(sym)
                    if len(prioritized) >= policy.level1_limit:
                        break

        # Em projetos grandes: fallback apenas se não achou nada relevante
        if prioritized:
            l1 = build_level1(store, symbols=prioritized[:policy.level1_limit])
        elif relevant_symbols:
            l1 = build_level1(store, symbols=relevant_symbols[:policy.level1_limit])
        else:
            # Sem query relevante: lista geral limitada
            l1 = build_level1(store, limit=min(policy.level1_limit, 80))

        if b.consume(l1):
            sections.append(l1)

    # ── Level 2 + 3: budget-driven, sem limites artificiais ──────────────────
    # Para cada arquivo relevante: tenta source completa, cai para skeleton,
    # para quando o budget acabar. Nunca trunca se o budget comporta o arquivo.
    if (policy.use_level2 or policy.use_level3) and relevant_paths:
        file_syms_batch = store.get_symbols_for_files(relevant_paths)
        for p_str in relevant_paths:
            if b.is_full:
                break

            p = Path(p_str)
            if not p.exists():
                p = root / p_str
            if not p.exists():
                continue

            src_lines = _read_source(p)
            if src_lines is None:
                continue

            file_syms = file_syms_batch.get(p_str, [])

            if policy.use_level3:
                # Tenta arquivo completo primeiro (sem limite de linhas artificial)
                l3_full = build_level3(p, max_lines=len(src_lines), source_lines=src_lines)
                if b.fits(l3_full):
                    b.consume(l3_full)
                    sections.append(l3_full)
                    used_paths.append(p_str)
                    continue

                # Não coube inteiro: tenta com cap baseado no budget restante
                # ~4 chars por token → estima linhas que cabem
                chars_available = b.remaining * 4
                approx_lines = max(30, chars_available // 80)  # ~80 chars/linha média
                l3_partial = build_level3(p, max_lines=approx_lines, source_lines=src_lines)
                if b.fits(l3_partial):
                    b.consume(l3_partial)
                    sections.append(l3_partial)
                    used_paths.append(p_str)
                    continue

            if policy.use_level2 and file_syms:
                # Skeleton com body lines adaptativo ao budget
                body_lines = policy.level2_body_lines
                l2 = build_level2(p, file_syms, max_body_lines=body_lines, source_lines=src_lines)
                if b.fits(l2):
                    b.consume(l2)
                    sections.append(l2)
                    used_paths.append(p_str)

    context = "\n\n".join(sections)
    meta = {
        "task": task,
        "tokens_used": b.used,
        "budget": b.available,
        "utilization": b.utilization(),
        "paths_included": list(dict.fromkeys(used_paths)),
    }
    return context, meta
