# Context Lens

> 🇧🇷 [Leia em Português](README.pt-BR.md)

> Index once, setup once, forget about it.
> GitHub Copilot receives optimized context automatically via `@lens` — no copy-paste, no terminal.

**Real savings: ~97.7% tokens** per query — measured against the actual project index.

![Token Savings](docs/token_savings.png)

> **v2.0 — Primary target: GitHub Copilot** — fully tested with `@lens` chat participant, `Ctrl+Shift+L` keybinding, real-time VS Code extension dashboard. Other integrations (Claude Code, Cursor, Codex) use the MCP server and are in **alpha** — the core engine works but end-to-end UX in those tools hasn't been exhaustively tested yet.

---

## How it works

AI assistants like Claude Code and Copilot have context limits (tokens). The bigger the project, the more irrelevant code fills the window, and responses become generic.

`lens` solves this in three steps:

**0. Setup — once per project**
`lens setup` detects your AI assistant and configures everything:
- Claude Code gets an MCP server that queries the index automatically.
- Copilot gets a VS Code task that injects context before each session.
- Codex gets an `AGENTS.md` with instructions to use the index.

After setup: no manual steps.

**1. Indexing — once per project**
Reads all files and extracts only the symbols: functions, classes, parameters, docstrings, line numbers. Saves to a local SQLite database in `.ctx/index.db`.

**2. At query time**
Searches the FTS5 index for relevant symbols in ~0.2ms (no disk reads), assembles focused context within the token budget. The assistant receives only the right snippet — automatically.

```
Without lens:  reads all 123 indexed files                                    →  264,967 tokens
With lens:     "fix walker module to handle symlinks"  (5 files selected)  →    6,859 tokens  (97.4% less)
```

> Numbers from `bench/proof_savings.py` run against the context-lens project itself.

The index lives in `.ctx/` inside each project and is ignored by git.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  AI Assistant (Claude / Copilot / Cursor / Codex)           │
│  ↕ MCP stdio                                                │
├─────────────────────────────────────────────────────────────┤
│  MCP Server (lens-mcp)                                       │
│  8 tools: search, context, status, symbols, explain_symbol, │
│           diff_context, reindex, memory                      │
│  4 resources: project/map, project/stats, symbols/{path},   │
│               memory                                         │
├─────────────────────────────────────────────────────────────┤
│  Core Engine                                                 │
│  ┌──────────┐ ┌───────────┐ ┌──────────┐ ┌──────────────┐  │
│  │ Indexer   │ │ Retrieval │ │ Context  │ │ Session/Log  │  │
│  │ walker    │ │ FTS5      │ │ builder  │ │ JSONL logger │  │
│  │ extractor │ │ intent    │ │ budget   │ │ SQLite v4    │  │
│  │ hasher    │ │ policy    │ │ levels   │ │ sessions     │  │
│  │ parser    │ │ search    │ │ ranking  │ │ memory_lite  │  │
│  └──────────┘ └───────────┘ └──────────┘ └──────────────┘  │
├─────────────────────────────────────────────────────────────┤
│  SQLite + FTS5 (.ctx/index.db)                              │
│  Tables: files, symbols, symbols_fts, project_map,          │
│          project_meta, memory_lite, sessions                 │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  VS Code Extension (context-lens)                            │
│  Activity Bar sidebar · real-time dashboard                  │
│  FileSystemWatcher on .ctx/ (log, config, stats, session)   │
│  Toggle ON/OFF · re-index · session tracking                │
└─────────────────────────────────────────────────────────────┘
```

---

## Installation

**Prerequisite:** Python 3.10+.

```bash
# With tree-sitter (recommended — precise parsing)
pip install "context-lens-v2[parse]"

# With MCP server (for Claude Code, Cursor, Continue.dev)
pip install "context-lens-v2[parse,mcp]"

# Everything (parsing + MCP + tiktoken + file watch)
pip install "context-lens-v2[all,mcp]"
```

Verify:

```bash
lens --version
```

> **Windows:** if `lens` is not recognized after install, add the scripts directory to PATH:
> ```powershell
> [Environment]::SetEnvironmentVariable("PATH",
>   [Environment]::GetEnvironmentVariable("PATH","User") + ";$env:LOCALAPPDATA\Packages\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\LocalCache\local-packages\Python313\Scripts",
>   "User")
> ```
> Close and reopen the terminal.

**From source:**

```bash
git clone https://github.com/TiagoSchr/context-lens
cd context-lens
pip install -e ".[parse,mcp]"
```

**Uninstall:**

```bash
pip uninstall context-lens-v2
rm -rf .ctx/    # remove the project index (optional)
```

---

## Quick start

Three commands to get started on any project:

```bash
pip install "context-lens-v2[parse,mcp]"
lens index && lens setup --auto
lens status
# Done. Open your AI assistant.
```

---

## VS Code Extension

The Context Lens extension provides a **real-time dashboard** in the VS Code Activity Bar sidebar.

![Context Lens Dashboard](docs/screenshot.png)

> **To add this screenshot:** save the sidebar image as `docs/screenshot.png` in the repo root.

### Features

- **Token economy cards** — total saved, average %, session savings
- **Task breakdown** — savings per task type (explain, bugfix, refactor...)
- **Tool breakdown** — economy per AI tool (Copilot, Claude, Cursor)
- **Recent queries** — last 4 queries with task badges and savings
- **Session tracking** — current MCP session name, query count, "⚡ 3min ago" live indicator
- **Toggle ON/OFF** — disable/enable optimization with one click
- **Re-index button** — trigger `lens index` from the sidebar
- **Auto-refresh** — FileSystemWatcher on `.ctx/` files, zero polling

### Install

`lens install` **automatically installs the extension** for VS Code and Cursor (if they are in PATH):

```bash
lens install   # detects IDEs, installs extension + MCP config
```

Or install manually:

```bash
cd vscode-context-lens
npm install
npm run compile
npx @vscode/vsce package --no-dependencies
code --install-extension context-lens-1.0.0.vsix
```

The sidebar appears automatically when `.ctx/index.db` exists in the workspace.

---

## MCP Server

The `lens-mcp` server exposes 8 tools and 4 resources via stdio MCP transport:

### Tools

| Tool | Description |
|------|-------------|
| `lens_search` | FTS5 symbol search by name or keyword |
| `lens_context` | ⭐ Primary tool — assembles optimized context for a query |
| `lens_status` | Index stats + token economy summary |
| `lens_symbols` | All symbols in a specific file |
| `lens_explain_symbol` | Deep dive: full source + callers + docstring |
| `lens_diff_context` | Context focused on git-changed files |
| `lens_reindex` | Trigger incremental reindex |
| `lens_memory` | CRUD memory entries (rules, notes, hotspots) |

### Resources

| URI | Description |
|-----|-------------|
| `lens://project/map` | Project structure (level0) |
| `lens://project/stats` | Index statistics (JSON) |
| `lens://symbols/{path}` | Symbols for a specific file |
| `lens://memory` | All memory entries |

### Features

- **Auto-detection** — detects Copilot, Cursor, Claude Code, Codex via env vars
- **Per-tool budgets** — configurable in `.ctx/config.json` per detected tool
- **Session tracking** — each MCP server lifetime = one session in SQLite
- **Graceful shutdown** — sessions are closed and `session.json` cleaned up on exit
- **Enabled flag** — respects the VS Code extension toggle for all data tools
- **Context caching** — identical queries within 60s return cached results
- **Realistic economy** — savings computed against included files, not entire project

The server uses ~5MB RAM, responds in ~1ms, communicates via stdio (no HTTP, no open port).

---

## Using with GitHub Copilot (`@lens`)

> `Ctrl+Shift+L` is exclusive to **GitHub Copilot in VS Code**. It opens the chat with `@lens` already typed — you just write your question.

After `lens install`, the full flow is:

```
[focus in editor]  →  Ctrl+Shift+L  →  Copilot Chat opens with "@lens "  →  type your question  →  Enter
```

**Step by step:**

1. With focus in the editor (not in another chat), press `Ctrl+Shift+L`
2. Copilot Chat opens with `@lens ` already typed
3. Continue typing your question: `@lens fix bug in checkout`
4. Press Enter — `@lens` fetches the relevant context from the index and injects it automatically

> **Important:** without `@lens` at the start, Copilot answers normally without optimized context.
> `@lens` is what triggers the system — always start with it.

> **Token savings:** every `@lens` query sends **~97% fewer tokens** to the AI compared to Copilot reading the files directly. The index selects only the 3–5 relevant files out of 123+ and injects just those — not the entire project. ([see benchmark](bench/proof_savings.py))

**Example queries:**

```
@lens fix bug in the calculate_total method
@lens how does the authentication system work
@lens write tests for the Cart class
@lens where is validate_coupon defined
```

**The shortcut won't work if:**
- Focus is inside another chat (e.g. Claude, Codex) — click the editor first
- The extension is not installed — run `lens install`
- There is no index in the project — run `lens index`

**Other tools (alpha):**

| Tool | How it works |
|------|-------------|
| Claude Code | MCP server auto-injects context |
| Cursor | MCP server auto-injects context |
| OpenAI Codex | `AGENTS.md` directs Codex automatically |

**Manual CLI** (explicit control):

```bash
lens context "fix bug in checkout returning wrong total"
lens context "how does the authentication system work" -t explain
lens context "write tests for Cart class" -t generate_test
lens context "where is calculate_discount defined" -t navigate
```

---

## Setup per project

`lens` works **per project**, like `git`. For each new project:

```bash
cd my-new-project/
lens index          # creates .ctx/ here and indexes
lens setup --auto   # configures all detected integrations silently
lens status         # confirms it's active and shows economy
```

`lens setup --auto` detects your tool (`.claude/`, `.vscode/`, etc.) and creates the right files (`CLAUDE.md`, `.claude/mcp.json`, `.vscode/tasks.json`, `.github/copilot-instructions.md`, `AGENTS.md`) with instructions for the assistant to use `lens_context` automatically.

```bash
lens setup --auto   # recommended — detects and configures silently
lens setup          # interactive — asks before each integration
```

---

## AI assistant integrations

Detailed guides per assistant:

- [Claude Code](docs/claude-code.md) — Automatic MCP, slash commands
- [GitHub Copilot](docs/copilot.md) — Automatic task + instructions
- [ChatGPT / OpenAI Codex](docs/chatgpt-codex.md) — Automatic AGENTS.md or clipboard
- [Cursor](docs/cursor.md) — Native MCP
- [Continue.dev](docs/continue-dev.md) — Native MCP, open source

### Compatibility matrix

| Assistant | Status | Automatic mode | Configuration |
|-----------|--------|---------------|---------------|
| **GitHub Copilot** | ✅ **Tested** | `@lens` chat participant + `Ctrl+Shift+L` | `lens install` |
| Claude Code IDE/CLI | ⚠️ Alpha | MCP server | `lens install` |
| Cursor | ⚠️ Alpha | MCP server | `lens install` |
| OpenAI Codex CLI | ⚠️ Alpha | AGENTS.md + MCP | `lens install` |
| Continue.dev (VS Code) | ⚠️ Alpha | MCP server | `lens install` |
| ChatGPT web | ⚠️ Alpha | Script + clipboard | `lc "query"` |

> **Alpha** means the MCP server and config files are generated correctly, but the end-to-end experience in those tools hasn't been thoroughly tested. Contributions and feedback welcome.

---

## Token economy by task type

| Task | When to use | Typical savings |
|------|-------------|-----------------|
| `navigate` | "where is X defined?" | **60–85%** |
| `generate_test` | "write tests for X" | **50–75%** |
| `explain` | "how does X work?" | **40–65%** |
| `refactor` | "refactor X" | **45–70%** |
| `bugfix` | "fix bug in X" | **25–55%** |

Task is auto-detected from the query. Use `-t` to override:

```bash
lens context "fix bug in checkout" -t bugfix --file src/cart.py
```

`--file` forces inclusion of a specific file — useful when the bug crosses multiple files.

> **Note on savings metrics:** v2.0 computes savings against the **raw tokens of included files** (what the AI would read if it opened those files without optimization). This is a realistic baseline — not the entire project total.

---

## All commands

```bash
lens index                           # incremental indexing
lens index --force                   # re-index everything from scratch
lens index --verbose                 # show each file
lens status                          # health + token economy
lens watch                           # monitor changes and re-index (background)
lens stats                           # files, symbols, languages
lens search "query"                  # search symbols
lens context "query"                 # assemble context (task auto-detected)
lens context "query" -t bugfix       # explicit task
lens context "query" --file x.py     # force file inclusion
lens context "query" --budget 12000  # custom budget
lens context "query" -o out.md       # save to file
lens show map                        # project map
lens show symbol:name                # symbol details
lens show file:src/module.py         # file symbols
lens log                             # query and token history
lens log --last 10                   # last 10 queries
lens memory list                     # list project memory
lens memory set rule key "value"     # add rule (appears in every generated context)
lens memory set hotspot file "src/core.py"  # mark file as critical
lens setup                           # configure AI assistant integrations
lens setup --auto                    # silent setup
lens config                          # current configuration
```

---

## Project structure created

```
your-project/
  .ctx/
    config.json     ← budget, extensions, ignored dirs
    index.db        ← SQLite database with symbols + FTS5
    log.jsonl       ← query and token history
    stats.json      ← index stats for VS Code extension
    session.json    ← current MCP session (auto-managed)
```

Everything in `.ctx/` is local and never goes to git.

---

## Configuration (`.ctx/config.json`)

```json
{
  "token_budget": 8000,
  "target_budgets": {
    "claude": 8000,
    "copilot": 4000,
    "codex": 6000
  },
  "budget_buffer": 0.12,
  "index_extensions": [".py", ".js", ".ts", ".tsx", ".go", ".rs"],
  "ignore_dirs": [".git", "node_modules", ".venv", "dist"],
  "max_file_size_kb": 512,
  "enabled": true
}
```

- `target_budgets` — per-tool budget overrides (auto-detected from env vars)
- `budget_buffer` — 12% safety margin to avoid budget overrun
- `enabled` — toggle via VS Code extension or manually (respected by all MCP data tools)

---

## Supported languages

| Language | Parser | Extracts |
|----------|--------|----------|
| Python | tree-sitter | functions, classes, decorators, docstrings |
| JavaScript | tree-sitter | functions, classes, methods, arrow functions |
| TypeScript / TSX | tree-sitter | same as JS + interfaces |
| Go, Rust, Java, C, C++ | regex | functions, structs, classes |
| Ruby, PHP, C#, Swift, Kotlin | regex | functions, classes |

---

## Performance

| Operation | Speed |
|-----------|-------|
| Full indexing | ~320 files/sec |
| Incremental re-index (no changes) | ~5,500 files/sec |
| FTS5 search | ~0.2ms |
| Context assembly | ~1–5ms |
| RAM during use | ~3–5MB |
| Scale | tested with 640 files / 7,000 symbols |

---

## SQLite schema (v4)

| Table | Purpose |
|-------|---------|
| `files` | Tracked files with hash-based change detection |
| `symbols` | Functions, classes, methods — with params, docstring, line range |
| `symbols_fts` | FTS5 virtual table for full-text search |
| `project_map` | Level0 project structure data |
| `project_meta` | Key-value metadata (token counts, timestamps) |
| `memory_lite` | Project rules, notes, hotspots (with optional TTL) |
| `sessions` | MCP server session tracking (start/end timestamps) |

---

## Changelog

### v2.0 — April 2025

#### Major release: VS Code extension + honest metrics + session tracking

**VS Code Extension — real-time dashboard**
- Activity Bar sidebar with token economy cards, task/tool breakdown, recent queries
- Session tracking — shows current MCP session name, query count, live "⚡ 3min ago" indicator
- Toggle ON/OFF — disable/enable optimization with one click (writes to `.ctx/config.json`)
- Re-index and refresh buttons
- FileSystemWatcher on `.ctx/` — zero polling, instant updates
- CSP nonce for security, `escHtml` everywhere to prevent XSS

**MCP Server v2 — 8 tools + 4 resources**
- `lens_diff_context` — context focused on git-changed files (now with retrieval logging)
- `lens_explain_symbol` — full source + callers deep dive
- `lens_memory` — CRUD memory entries via MCP
- `lens_reindex` — now writes `stats.json` + updates `project_tokens_total` (mirrors CLI)
- Auto-detection of Copilot, Cursor, Claude Code, Codex via environment variables
- Per-tool budget overrides (`target_budgets` in config)
- Context caching (60s TTL) for identical queries
- Graceful shutdown — sessions closed, `session.json` cleaned up on exit

**Honest token economy**
- `tokens_raw` baseline changed from "entire project" to "raw tokens of included files"
- Savings percentages now reflect realistic AI tool behavior
- `enabled` flag respected by all data-retrieval tools (5/8), not just 2

**Session system (SQLite v4)**
- Each MCP server lifetime = one session with start/end timestamps
- Session ID propagated to all log entries for session-level analytics
- `session.json` written for the VS Code extension, auto-cleaned on shutdown
- Concurrent MCP servers no longer corrupt each other's sessions

**Adaptive context ranking**
- Files ranked by relevance score (query term density + symbol count + entry-point boost)
- Budget-driven level selection: full source → skeleton → signatures (never truncates artificially)
- Cross-file expansion for bugfix/refactor tasks (imports + callers)
- Batch symbol queries — single `WHERE path IN (...)` instead of N+1 round-trips

**363 Python tests + 59 TypeScript assertions**

---

### v0.2 — March 2025

- `lens setup` — automatic multi-tool configuration
- `lens status` — projected savings before first query
- Project memory injected into generated context
- Fixed: memory duplicates, FTS5 silent failure, tree-sitter version
- N+1 queries eliminated in context assembler

### v0.1 — Initial release

- Incremental SHA-1 indexing
- FTS5 search with stop words and technical identifier prioritization
- Budget-driven context assembly by level (L0 map, L1 signatures, L2 skeleton, L3 source)
- Task-specific policies (navigate, explain, bugfix, refactor, generate_test)
- MCP server for Claude Code, Continue.dev and Cursor
- Slash commands, VS Code tasks, Memory Lite

---

## License

MIT — see [LICENSE](LICENSE).
