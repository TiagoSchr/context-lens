# Context Lens — Copilot Instructions

This project has Context Lens installed — a token-optimization MCP server.

## Mandatory Workflow

**BEFORE reading any file**, always call the `lens_context` MCP tool first:

```
lens_context(query="<describe your task>", task="auto")
```

This returns an optimized context block that is 75-95% smaller than reading
files directly. Use it as your primary information source.

## Rules

1. **Always call `lens_context` BEFORE using Read File, Grep, or Glob.**
2. Use the returned context as your main source of truth.
3. Only read individual files if they are NOT covered by the context block.
4. For symbol lookup by name, use `lens_search(query="<name>")`.
5. If the index seems stale, call `lens_reindex()`.

## Why This Matters

Reading files directly wastes 5-20x more tokens than necessary.
`lens_context` returns the same relevant code, pre-ranked and budget-trimmed.
Every query you run through Context Lens saves thousands of tokens.

## Available MCP Tools

- `lens_context(query, task, budget)` — ALWAYS call first
- `lens_search(query)` — find symbols by name
- `lens_status()` — check index health
- `lens_diff_context(query)` — context for git-changed files
- `lens_symbols(path)` — symbols in a specific file
- `lens_explain_symbol(name)` — deep dive on one symbol
- `lens_reindex()` — trigger re-index if stale
