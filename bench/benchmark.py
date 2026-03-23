"""
Benchmark de performance do Context Compiler.
Mede: indexing time, context build time, token savings, memory footprint.
"""
import gc
import time
import tempfile
import tracemalloc
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ctx.config import DEFAULT_CONFIG
from ctx.db.schema import init_db
from ctx.db.store import Store
from ctx.indexer.hasher import hash_file
from ctx.indexer.walker import walk_project
from ctx.indexer.extractor import extract_symbols
from ctx.retrieval.search import search_symbols, find_related_paths
from ctx.retrieval.intent import classify_intent
from ctx.context.builder import build_context
from ctx.context.budget import count_tokens

# ─────────────────────────────────────────────── helpers

def hr(label=""):
    print(f"\n{'-'*60}")
    if label:
        print(f"  {label}")
        print(f"{'-'*60}")

def fmt_ms(s):
    return f"{s*1000:.1f}ms" if s < 1 else f"{s:.3f}s"

def fmt_kb(b):
    return f"{b/1024:.1f}KB" if b < 1_048_576 else f"{b/1_048_576:.2f}MB"

# ─────────────────────────────────────────────── setup: gerar projeto sintético

def make_synthetic_project(tmp: Path, n_files=50, funcs_per_file=8):
    """Gera N arquivos Python com funções e classes simuladas."""
    src = tmp / "src"
    src.mkdir()
    tests = tmp / "tests"
    tests.mkdir()

    for i in range(n_files):
        code = f'"""Module {i} — synthetic benchmark file."""\n\n'
        for j in range(funcs_per_file):
            code += (
                f'def func_{i}_{j}(x: int, y: str = "default") -> bool:\n'
                f'    """Do operation {j} in module {i}. Returns True on success."""\n'
                f'    result = x * {j + 1}\n'
                f'    return bool(result)\n\n'
            )
        if i % 5 == 0:
            code += (
                f'class Handler{i}:\n'
                f'    """Handler class for module {i}."""\n\n'
                f'    def process(self, data: list) -> dict:\n'
                f'        """Process input data and return results."""\n'
                f'        return {{}}\n\n'
            )
        (src / f"module_{i}.py").write_text(code, encoding="utf-8")

    for i in range(n_files // 5):
        test_code = (
            f'"""Tests for module {i}."""\n\n'
            f'def test_func_{i}_0():\n    assert func_{i}_0(1) == True\n\n'
            f'def test_func_{i}_1():\n    assert func_{i}_1(2) is not None\n'
        )
        (tests / f"test_module_{i}.py").write_text(test_code, encoding="utf-8")

    return tmp


# ─────────────────────────────────────────────── benchmark 1: indexing

def bench_indexing(project_root, label=""):
    cfg = DEFAULT_CONFIG
    extensions = cfg["index_extensions"]
    ignore_dirs = set(cfg["ignore_dirs"])

    db_path = project_root / ".ctx" / "index.db"
    db_path.parent.mkdir(exist_ok=True)

    tracemalloc.start()
    gc.collect()
    t0 = time.perf_counter()

    conn = init_db(db_path)
    store = Store(conn)

    files_indexed = 0
    total_symbols = 0
    for file_path in walk_project(project_root, extensions, ignore_dirs, 512):
        rel = file_path.relative_to(project_root).as_posix()
        h = hash_file(file_path)
        symbols, lang = extract_symbols(file_path)
        fid = store.upsert_file(rel, h, lang, file_path.stat().st_size)
        for s in symbols:
            s["file_id"] = fid
            s["path"] = rel
        if symbols:
            store.insert_symbols_batch(symbols)
        files_indexed += 1
        total_symbols += len(symbols)

    store.commit()
    elapsed = time.perf_counter() - t0
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    stats = store.stats()
    db_size = db_path.stat().st_size

    print(f"\n[Indexing {label}]")
    print(f"  Files indexed   : {files_indexed}")
    print(f"  Symbols total   : {total_symbols}")
    print(f"  Time            : {fmt_ms(elapsed)}")
    print(f"  Files/sec       : {files_indexed/elapsed:.0f}")
    print(f"  Symbols/sec     : {total_symbols/elapsed:.0f}")
    print(f"  Peak RAM (index): {fmt_kb(peak)}")
    print(f"  DB size on disk : {fmt_kb(db_size)}")

    return store, elapsed, total_symbols


# ─────────────────────────────────────────────── benchmark 2: incremental

def bench_incremental(project_root):
    cfg = DEFAULT_CONFIG
    extensions = cfg["index_extensions"]
    ignore_dirs = set(cfg["ignore_dirs"])
    db_path = project_root / ".ctx" / "index.db"

    conn = init_db(db_path)
    store = Store(conn)

    t0 = time.perf_counter()
    skipped = 0
    indexed = 0
    for file_path in walk_project(project_root, extensions, ignore_dirs, 512):
        rel = file_path.relative_to(project_root).as_posix()
        h = hash_file(file_path)
        if store.get_file_hash(rel) == h:
            skipped += 1
        else:
            symbols, lang = extract_symbols(file_path)
            fid = store.upsert_file(rel, h, lang, file_path.stat().st_size)
            for s in symbols:
                s["file_id"] = fid
                s["path"] = rel
            if symbols:
                store.insert_symbols_batch(symbols)
            indexed += 1
    store.commit()
    elapsed = time.perf_counter() - t0

    print(f"\n[Incremental Index — no changes]")
    print(f"  Skipped (cached): {skipped}")
    print(f"  Re-indexed      : {indexed}")
    print(f"  Time            : {fmt_ms(elapsed)}")
    print(f"  Speedup vs full : ~{(skipped+indexed)/max(elapsed,0.001):.0f} files/sec")


# ─────────────────────────────────────────────── benchmark 3: search

def bench_search(store):
    queries = [
        "func_0_1", "Handler", "process", "operation", "data", "result",
        "module", "xyznotfound", "test", "bool"
    ]
    times = []
    for q in queries:
        t0 = time.perf_counter()
        results = search_symbols(store, q, limit=20)
        elapsed = time.perf_counter() - t0
        times.append((q, elapsed, len(results)))

    print(f"\n[FTS5 Search — {len(queries)} queries]")
    for q, t, n in times:
        print(f"  {q:<20} {n:>3} results  {fmt_ms(t)}")
    avg = sum(t for _, t, _ in times) / len(times)
    print(f"  Average latency : {fmt_ms(avg)}")


# ─────────────────────────────────────────────── benchmark 4: context build

def bench_context_build(store, root, n_queries=10):
    queries = [
        ("explain how func_0_0 works", "explain"),
        ("fix bug in Handler0.process", "bugfix"),
        ("refactor module_0", "refactor"),
        ("write tests for func_1_0", "generate_test"),
        ("find where Handler10 is defined", "navigate"),
        ("explain the data processing pipeline", "explain"),
        ("why is func_2_3 returning None", "bugfix"),
        ("refactor func_3_1 to be cleaner", "refactor"),
        ("generate tests for Handler5", "generate_test"),
        ("list all Handler classes", "navigate"),
    ]

    times = []
    token_savings = []

    # Total raw tokens (simulating "paste everything")
    all_paths = store.list_indexed_paths()
    total_raw = 0
    for p in all_paths:
        full = root / p
        if full.exists():
            try:
                total_raw += count_tokens(full.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                pass

    print(f"\n[Context Build — {len(queries)} queries, budget=8000]")
    print(f"  Total raw project tokens : {total_raw:,}")

    for query, task in queries:
        t0 = time.perf_counter()
        syms = search_symbols(store, query, limit=40)
        paths = find_related_paths(store, syms)
        text, meta = build_context(
            store, root, task, query,
            relevant_symbols=syms,
            relevant_paths=paths,
            budget=8000,
        )
        elapsed = time.perf_counter() - t0
        saving = 1 - (meta["tokens_used"] / max(total_raw, 1))
        times.append(elapsed)
        token_savings.append((query[:35], task, meta["tokens_used"], saving))

    for q, task, tokens, saving in token_savings:
        print(f"  [{task:<14}] {tokens:>5} tokens  {saving:>5.0%} saved  — {q}")

    avg_t = sum(times) / len(times)
    avg_saving = sum(s for _, _, _, s in token_savings) / len(token_savings)
    avg_tokens = sum(t for _, _, t, _ in token_savings) / len(token_savings)
    print(f"\n  Average build time   : {fmt_ms(avg_t)}")
    print(f"  Average tokens used  : {avg_tokens:.0f} / 8000")
    print(f"  Average token saving : {avg_saving:.0%}")


# ─────────────────────────────────────────────── benchmark 5: intent speed

def bench_intent(n=1000):
    queries = [
        "explain how the auth module works",
        "fix the bug in login function",
        "refactor the parser module",
        "write unit tests for Budget class",
        "find where Store is defined",
    ] * (n // 5)

    t0 = time.perf_counter()
    for q in queries:
        classify_intent(q)
    elapsed = time.perf_counter() - t0

    print(f"\n[Intent Classification — {n} queries]")
    print(f"  Total time   : {fmt_ms(elapsed)}")
    print(f"  Per query    : {elapsed/n*1000:.3f}ms")
    print(f"  Queries/sec  : {n/elapsed:,.0f}")


# ─────────────────────────────────────────────── main

if __name__ == "__main__":
    print("=" * 60)
    print("  Context Compiler — Performance Benchmark")
    print("=" * 60)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        store = None

        hr("1. Generating synthetic project (50 files, 8 funcs each)...")
        make_synthetic_project(root, n_files=50, funcs_per_file=8)
        py_count = len(list(root.rglob("*.py")))
        total_lines = sum(len(f.read_text(encoding="utf-8").splitlines())
                          for f in root.rglob("*.py"))
        print(f"  Generated: {py_count} files, ~{total_lines:,} lines of code")

        hr("2. Full indexing benchmark")
        store, idx_time, total_syms = bench_indexing(root, "50 files")

        hr("3. Incremental index benchmark")
        bench_incremental(root)

        hr("4. FTS5 search latency")
        bench_search(store)

        hr("5. Context build + token savings")
        bench_context_build(store, root)

        hr("6. Intent classification speed")
        bench_intent(1000)

        if store:
            store.close()  # fecha conexao antes do cleanup no Windows

    print("\n" + "=" * 60)
    print("  Benchmark complete.")
    print("=" * 60)
