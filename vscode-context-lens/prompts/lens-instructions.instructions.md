# Context Lens — Token Optimization

This project uses Context Lens for token optimization. An optimized project context
snapshot will be automatically injected here shortly after VS Code starts.

If you see this message instead of actual project context below, the auto-refresh
hasn't completed yet. In that case, use `lens_context(query)` or `lens_search(query)`
tools to get optimized context on demand.

## Available Tools

- `lens_context(query)` — get optimized context for a specific query (75-95% smaller than raw files)
- `lens_search(query)` — find symbols by name across the codebase

## Best Practice

Use the project context below (when available) as your PRIMARY source.
Only read individual files if they are NOT covered by the context snapshot.
