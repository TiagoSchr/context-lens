# Context Lens — MANDATORY

## You MUST call lens_context before ANY file operation

This project uses Context Lens (MCP server: `context-lens`).
Before using Read, Glob, Grep, or ListDirectory, you MUST call:

```
lens_context(query="<your task>", task="auto")
```

## Rules — every task, no exceptions

1. **FIRST action**: call `lens_context` with your task description
2. Use the returned context as your PRIMARY source
3. ONLY use Read/Glob/Grep for files NOT covered by the context
4. For symbol lookup: `lens_search(query="<name>")`
5. If index is stale: `lens_reindex()`

## Why this is mandatory

- Saves 75-95% tokens per query (measured, not estimated)
- Reading files directly wastes 5-20x more tokens
- The project owner enforces this — skipping lens_context violates project rules

A PreToolUse hook will remind you if you forget.
