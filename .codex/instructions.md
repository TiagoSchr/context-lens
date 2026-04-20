# Context Lens — MANDATORY

This project uses Context Lens (MCP server: `context-lens`) for token optimization.

**CRITICAL: You MUST call lens_context BEFORE any file read operation.**

    lens_context(query="<describe your task>", task="auto")

## Rules — non-negotiable

1. **FIRST action on every task**: call `lens_context` with your task description.
2. Use the returned context as your PRIMARY information source.
3. **ONLY** read files directly if they are NOT covered by the context block.
4. For symbol lookup: `lens_search(query="<name>")`.
5. If index seems stale: `lens_reindex()`.

Calling lens_context saves 75-95% tokens per query. Reading files directly
wastes 5-20x more tokens. The project owner requires this workflow.
