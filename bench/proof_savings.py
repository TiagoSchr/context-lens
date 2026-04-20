"""
Proof of real token savings — measures tokens WITH vs WITHOUT Context Lens.

This script:
  1. Reads ALL indexed source files (what an AI would get without optimization)
  2. Runs 3 realistic queries through the actual Context Lens pipeline
  3. Compares the token counts side-by-side

Run:  python bench/proof_savings.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from ctx.config import find_project_root, db_path, load_config
from ctx.db.schema import init_db
from ctx.db.store import Store
from ctx.retrieval.intent import classify_intent
from ctx.retrieval.search import search_symbols, find_related_paths
from ctx.context.builder import build_context
from ctx.context.budget import count_tokens

root = find_project_root()
conn = init_db(db_path(root))
store = Store(conn)
cfg = load_config(root)

# ── 1. Measure WITHOUT optimizer (read all files) ────────────────────────────
all_paths = store.list_indexed_paths(limit=9999)
total_raw_text = ''
for p in all_paths:
    full = root / p
    if full.exists():
        try:
            total_raw_text += full.read_text(encoding='utf-8', errors='replace')
        except Exception:
            pass

raw_tokens = count_tokens(total_raw_text)
raw_chars = len(total_raw_text)
raw_files = len(all_paths)

print("=" * 60)
print("  PROOF OF TOKEN SAVINGS — Context Lens")
print("=" * 60)
print()
print(f"WITHOUT Context Lens (reading all indexed files):")
print(f"  Files:      {raw_files}")
print(f"  Characters: {raw_chars:,}")
print(f"  Tokens:     {raw_tokens:,}")
print()

# ── 2. Measure WITH optimizer (3 realistic queries) ──────────────────────────
test_queries = [
    'explain how the MCP server handles tool calls',
    'fix the walker module to handle symlinks',
    'what does search_symbols do and where is it called',
]

print("WITH Context Lens (per query):")
print("-" * 60)

total_used = 0
for i, query in enumerate(test_queries, 1):
    task, conf = classify_intent(query)
    budget = cfg.get('token_budget', 8000)

    symbols = search_symbols(store, query, limit=50)
    paths = find_related_paths(store, symbols)

    ctx_text, meta = build_context(
        store=store, root=root, task=task, query=query,
        relevant_symbols=symbols, relevant_paths=paths,
        budget=budget, buffer_ratio=cfg.get('budget_buffer', 0.12),
    )

    ctx_tokens = count_tokens(ctx_text)
    total_used += ctx_tokens
    saving_pct = (1 - ctx_tokens / raw_tokens) * 100
    files_used = len(meta.get('paths_included', []))

    print(f"  Query {i}: \"{query}\"")
    print(f"    Task detected:  {task} (confidence {conf:.0%})")
    print(f"    Files selected: {files_used} of {raw_files}")
    print(f"    Tokens sent:    {ctx_tokens:,}  vs  {raw_tokens:,} (all files)")
    print(f"    Real saving:    {saving_pct:.1f}%")
    print()

# ── 3. Summary ───────────────────────────────────────────────────────────────
total_would_be = raw_tokens * len(test_queries)
total_saved = total_would_be - total_used

print("=" * 60)
print("  SUMMARY")
print("=" * 60)
print(f"  3 queries with Context Lens:    {total_used:,} tokens")
print(f"  3 queries without (all files):  {total_would_be:,} tokens")
print(f"  Tokens saved:                   {total_saved:,}")
print(f"  Average saving:                 {(1 - total_used / total_would_be) * 100:.1f}%")
print()

# ── 4. Show what the AI actually receives (first query excerpt) ──────────────
task, _ = classify_intent(test_queries[0])
symbols = search_symbols(store, test_queries[0], limit=50)
paths = find_related_paths(store, symbols)
ctx_text, meta = build_context(
    store=store, root=root, task=task, query=test_queries[0],
    relevant_symbols=symbols, relevant_paths=paths,
    budget=cfg.get('token_budget', 8000),
    buffer_ratio=cfg.get('budget_buffer', 0.12),
)

print("=" * 60)
print("  WHAT THE AI ACTUALLY RECEIVES (first 2000 chars)")
print("=" * 60)
print(ctx_text[:2000])
print(f"\n... ({len(ctx_text):,} chars total, {count_tokens(ctx_text):,} tokens)")
