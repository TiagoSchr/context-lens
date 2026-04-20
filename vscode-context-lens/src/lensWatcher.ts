/**
 * lensWatcher.ts — finds the .ctx/ directory, watches log.jsonl / config.json /
 * stats.json for changes, parses them, and fires onDidChange.
 *
 * Uses VS Code's FileSystemWatcher (no polling) for zero-overhead real-time updates.
 */
import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { parseLensData, LensStats, emptyStats } from './logParser';
import { leadingDebounce, Debounced } from './debounce';

export class LensWatcher implements vscode.Disposable {
  private readonly _onDidChange = new vscode.EventEmitter<LensStats>();
  readonly onDidChange: vscode.Event<LensStats> = this._onDidChange.event;

  private _stats: LensStats = emptyStats();
  private _root: string | null = null;
  private _fsWatcher: vscode.FileSystemWatcher | null = null;
  private _globalWatcher: vscode.FileSystemWatcher | null = null;
  private _workspaceSub: vscode.Disposable | null = null;
  private _editorSub: vscode.Disposable | null = null;
  private readonly _debouncedLoad: Debounced = leadingDebounce(() => this._load(), 300);

  // ── Lifecycle ────────────────────────────────────────────────────────────

  start(): void {
    // Primary: async findFiles — same mechanism as workspaceContains activation.
    // If the extension activated, this WILL find the file.
    this._detectRootAsync();

    // Also try sync strategies immediately so UI updates faster
    const syncRoot = this._findRoot();
    if (syncRoot && syncRoot !== this._root) {
      this._root = syncRoot;
      this._attachWatcher();
      this._load();
    }

    // Global watcher: detect .ctx/ creation from external terminals (e.g. user
    // runs `lens index` manually). Without this, the sidebar stays stuck on
    // "No index found" until the user switches editor tabs.
    this._attachGlobalWatcher();

    // Re-scan when the user opens a new folder
    this._workspaceSub = vscode.workspace.onDidChangeWorkspaceFolders(() => {
      this._detachWatcher();
      this._root = this._findRoot();
      if (this._root) {
        this._attachWatcher();
        this._load();
      } else {
        this._stats = emptyStats();
        this._onDidChange.fire(this._stats);
      }
    });

    // Re-scan when the active editor changes (user switches to a file in a different project)
    this._editorSub = vscode.window.onDidChangeActiveTextEditor(() => {
      const newRoot = this._findRoot();
      if (newRoot && newRoot !== this._root) {
        this._detachWatcher();
        this._root = newRoot;
        this._attachWatcher();
        this._load();
      } else if (!this._root) {
        // Sync didn't find it — try async
        this._detectRootAsync();
      }
    });
  }

  dispose(): void {
    this._detachWatcher();
    this._globalWatcher?.dispose();
    this._globalWatcher = null;
    this._workspaceSub?.dispose();
    this._editorSub?.dispose();
    this._onDidChange.dispose();
    this._debouncedLoad.cancel();
  }

  // ── Public API ───────────────────────────────────────────────────────────

  get stats(): LensStats { return this._stats; }
  get root(): string | null { return this._root; }

  /** Force an immediate reload from disk. */
  refresh(): void {
    this._load();
  }

  /**
   * Flip `enabled` in .ctx/config.json.
   * The Python MCP server reads this flag on every tool call.
   */
  toggle(): void {
    if (!this._root) {
      vscode.window.showWarningMessage(
        'Context Lens: No indexed project found in workspace. '
        + 'Run `lens index` inside your project first.',
      );
      return;
    }

    const configPath = path.join(this._root, '.ctx', 'config.json');
    let cfg: Record<string, unknown> = {};
    try {
      if (fs.existsSync(configPath)) {
        cfg = JSON.parse(fs.readFileSync(configPath, 'utf-8')) as Record<string, unknown>;
      }
    } catch {
      // file unreadable — start fresh
    }

    cfg.enabled = cfg.enabled === false ? true : false;

    try {
      fs.writeFileSync(configPath, JSON.stringify(cfg, null, 2), 'utf-8');
    } catch (e) {
      vscode.window.showErrorMessage(`Context Lens: Could not write config — ${e}`);
      return;
    }

    this._load();
    const nowEnabled = cfg.enabled !== false;
    const state = nowEnabled ? 'Enabled ✓' : 'Disabled ✗';
    vscode.window.showInformationMessage(`Context Lens: ${state}`);

    // When toggling ON, run a health check and auto-ensure instruction files
    if (nowEnabled) {
      this._ensureOptimizationReady();
    }
  }

  /**
   * Verify MCP config and instruction files exist for the active tools.
   * Auto-creates missing instruction files so the AI knows to call lens_context.
   */
  private _ensureOptimizationReady(): void {
    if (!this._root) { return; }
    const root = this._root;
    const warnings: string[] = [];
    const created: string[] = [];

    // Collect all roots where config/instruction files should exist:
    // 1. The project root (where .ctx/ lives)
    // 2. Each VS Code workspace folder (the IDE reads mcp.json and instructions from there)
    const roots = new Set<string>([root]);
    for (const folder of vscode.workspace.workspaceFolders ?? []) {
      roots.add(folder.uri.fsPath);
    }

    // ── 1. Check & auto-create MCP configs ───────────────────────────
    const mcpEntry = JSON.stringify({
      servers: {
        'context-lens': { type: 'stdio', command: 'lens-mcp', args: [] },
      },
    }, null, 2);

    let anyMcpFound = false;
    for (const r of roots) {
      const mcpPath = path.join(r, '.vscode', 'mcp.json');
      if (fs.existsSync(mcpPath)) {
        try {
          const content = fs.readFileSync(mcpPath, 'utf-8');
          if (content.includes('context-lens') || content.includes('lens-mcp')) {
            anyMcpFound = true;
            continue;
          }
        } catch { /* unreadable */ }
      }
      // Auto-create .vscode/mcp.json
      try {
        const dir = path.dirname(mcpPath);
        if (!fs.existsSync(dir)) { fs.mkdirSync(dir, { recursive: true }); }
        fs.writeFileSync(mcpPath, mcpEntry, 'utf-8');
        created.push(`.vscode/mcp.json (${path.basename(r)})`);
        anyMcpFound = true;
      } catch { /* non-fatal */ }
    }

    if (!anyMcpFound) {
      warnings.push('Could not create MCP config — run `lens install` manually.');
    }

    // ── 2. Auto-ensure instruction files ─────────────────────────────
    const instructionFiles: Array<{ name: string; relPath: string; content: string }> = [
      {
        name: '.github/copilot-instructions.md',
        relPath: '.github/copilot-instructions.md',
        content: this._copilotInstructionContent(),
      },
      {
        name: '.codex/instructions.md',
        relPath: '.codex/instructions.md',
        content: this._codexInstructionContent(),
      },
      {
        name: '.cursorrules',
        relPath: '.cursorrules',
        content: this._cursorRulesContent(),
      },
    ];

    for (const r of roots) {
      for (const inst of instructionFiles) {
        const fullPath = path.join(r, inst.relPath);
        if (fs.existsSync(fullPath)) {
          try {
            const existing = fs.readFileSync(fullPath, 'utf-8');
            if (existing.includes('lens_context')) { continue; }
          } catch { /* unreadable — skip */ }
        }
        try {
          const dir = path.dirname(fullPath);
          if (!fs.existsSync(dir)) { fs.mkdirSync(dir, { recursive: true }); }
          fs.writeFileSync(fullPath, inst.content, 'utf-8');
          created.push(`${inst.name} (${path.basename(r)})`);
        } catch {
          // non-fatal
        }
      }
    }

    // ── 3. Report results ────────────────────────────────────────────
    if (created.length > 0) {
      vscode.window.showInformationMessage(
        `Context Lens: Auto-created ${created.join(', ')} — reload window to activate MCP.`,
        'Reload Window',
      ).then((choice) => {
        if (choice === 'Reload Window') {
          vscode.commands.executeCommand('workbench.action.reloadWindow');
        }
      });
    }
    if (warnings.length > 0) {
      vscode.window.showWarningMessage(warnings[0]);
    }
  }

  /** Copilot instruction file content (.github/copilot-instructions.md). */
  private _copilotInstructionContent(): string {
    return [
      '# Context Lens — Copilot Instructions',
      '',
      'This project has Context Lens installed — a token-optimization MCP server.',
      '',
      '## Mandatory Workflow',
      '',
      '**BEFORE reading any file**, always call the `lens_context` MCP tool first:',
      '',
      '```',
      'lens_context(query="<describe your task>", task="auto")',
      '```',
      '',
      'This returns an optimized context block that is 75-95% smaller than reading',
      'files directly. Use it as your primary information source.',
      '',
      '## Rules',
      '',
      '1. **Always call `lens_context` BEFORE using Read File, Grep, or Glob.**',
      '2. Use the returned context as your main source of truth.',
      '3. Only read individual files if they are NOT covered by the context block.',
      '4. For symbol lookup by name, use `lens_search(query="<name>")`.',
      '5. If the index seems stale, call `lens_reindex()`.',
      '',
    ].join('\n');
  }

  /** Codex instruction file content (.codex/instructions.md). */
  private _codexInstructionContent(): string {
    return [
      '# Context Lens — Codex Instructions',
      '',
      'MANDATORY: Before reading any file, always call `lens_context` MCP tool first.',
      'It returns optimized context (75-95% smaller than raw files).',
      '',
      'Workflow:',
      '1. Call lens_context(query="<task description>") BEFORE any file read.',
      '2. Use the returned context as your primary source.',
      '3. Only read files not covered by the context block.',
      '4. For symbol search: lens_search(query="<name>").',
      '5. If index is stale: lens_reindex().',
      '',
    ].join('\n');
  }

  /** Cursor rules file content (.cursorrules). */
  private _cursorRulesContent(): string {
    return [
      '# Context Lens — Cursor Rules',
      '',
      'MANDATORY: Before reading any file, always call `lens_context` MCP tool first.',
      'It returns optimized context (75-95% smaller than raw files).',
      '',
      'Workflow:',
      '1. Call lens_context(query="<task description>") BEFORE any file read.',
      '2. Use the returned context as your primary source.',
      '3. Only read files not covered by the context block.',
      '4. For symbol search: lens_search(query="<name>").',
      '5. If index is stale: lens_reindex().',
      '',
    ].join('\n');
  }

  /** Open a VS Code terminal and run `lens index` in the project root. */
  runLensIndex(): void {
    // Re-detect root at click time (in case async detection finished after start)
    const cwd = this._root ?? this._findRoot() ?? vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    const terminal = vscode.window.createTerminal({ name: 'Context Lens', cwd });
    terminal.show();
    terminal.sendText('lens index');
    // After indexing completes, re-detect (stats.json will be written)
    // Watch picks this up automatically via FileSystemWatcher
  }

  /** Open .ctx/config.json in the editor. */
  openConfig(): void {
    if (!this._root) { return; }
    const configPath = path.join(this._root, '.ctx', 'config.json');
    vscode.commands.executeCommand('vscode.open', vscode.Uri.file(configPath));
  }

  // ── Private ──────────────────────────────────────────────────────────────

  /**
   * Uses VS Code's findFiles API — the same mechanism that powers workspaceContains.
   * Guaranteed to find .ctx/ anywhere in the workspace if the extension activated.
   */
  private _detectRootAsync(): void {
    // Search for stats.json first (written by `lens index` with our changes),
    // fall back to index.db (always present after indexing).
    const search = (glob: string) =>
      vscode.workspace.findFiles(glob, '**/node_modules/**', 1).then((uris) => {
        if (uris.length > 0) {
          // uri is e.g. .../context_compiler/.ctx/stats.json
          // parent of .ctx/ is the project root
          const newRoot = path.dirname(path.dirname(uris[0].fsPath));
          if (newRoot !== this._root) {
            this._detachWatcher();
            this._root = newRoot;
            this._attachWatcher();
            this._load();
          }
        }
      });

    Promise.resolve(search('**/.ctx/stats.json')).then(() => {
      if (!this._root) {
        return search('**/.ctx/index.db');
      }
      return undefined;
    }).then(undefined, () => { /* non-fatal */ });
  }

  private _findRoot(): string | null {
    // Strategy 1: active editor file — walk UP looking for .ctx/
    // BUT only if the file is inside a workspace folder (prevents picking up
    // external temp projects like pytest fixtures).
    const activeFile = vscode.window.activeTextEditor?.document.uri.fsPath;
    if (activeFile && this._isInsideWorkspace(activeFile)) {
      const found = this._walkUpForCtx(path.dirname(activeFile));
      if (found) { return found; }
    }

    // Strategy 2: each workspace folder root — direct child
    for (const folder of vscode.workspace.workspaceFolders ?? []) {
      if (fs.existsSync(path.join(folder.uri.fsPath, '.ctx'))) {
        return folder.uri.fsPath;
      }
    }

    // Strategy 3: each workspace folder — one level of subdirectories
    for (const folder of vscode.workspace.workspaceFolders ?? []) {
      try {
        const entries = fs.readdirSync(folder.uri.fsPath, { withFileTypes: true });
        for (const entry of entries) {
          if (!entry.isDirectory()) { continue; }
          const sub = path.join(folder.uri.fsPath, entry.name);
          if (fs.existsSync(path.join(sub, '.ctx'))) {
            return sub;
          }
        }
      } catch {
        // unreadable — skip
      }
    }

    return null;
  }

  /** Walk upward from `dir` until we find a directory that contains `.ctx/`. */
  private _walkUpForCtx(dir: string): string | null {
    let current = dir;
    for (let i = 0; i < 20; i++) {
      if (fs.existsSync(path.join(current, '.ctx'))) {
        return current;
      }
      const parent = path.dirname(current);
      if (parent === current) { break; } // filesystem root
      current = parent;
    }
    return null;
  }

  /** Return true if `filePath` is inside any open workspace folder. */
  private _isInsideWorkspace(filePath: string): boolean {
    const normalized = filePath.toLowerCase();
    for (const folder of vscode.workspace.workspaceFolders ?? []) {
      const folderPath = folder.uri.fsPath.toLowerCase();
      if (normalized.startsWith(folderPath + path.sep.toLowerCase())
        || normalized === folderPath) {
        return true;
      }
    }
    return false;
  }

  /**
   * Workspace-level watcher for .ctx/stats.json creation.
   * Detects new indexes created from external terminals or CLI — without this,
   * the sidebar stays stuck on "No index found" when the user runs `lens index`
   * outside the "Run lens index" button.
   */
  private _attachGlobalWatcher(): void {
    const pattern = '**/.ctx/stats.json';
    this._globalWatcher = vscode.workspace.createFileSystemWatcher(pattern);

    const onDetected = () => {
      // Only re-detect if we don't already have a root (or if stats changed)
      this._detectRootAsync();
      // Also try sync for faster response
      const syncRoot = this._findRoot();
      if (syncRoot && syncRoot !== this._root) {
        this._detachWatcher();
        this._root = syncRoot;
        this._attachWatcher();
        this._load();
      } else if (this._root) {
        this._scheduleLoad();
      }
    };

    this._globalWatcher.onDidCreate(onDetected);
    this._globalWatcher.onDidChange(onDetected);
  }

  private _attachWatcher(): void {
    if (!this._root) { return; }
    // Watch all three relevant files with a single glob
    const pattern = new vscode.RelativePattern(
      this._root,
      '.ctx/{log.jsonl,config.json,stats.json,session.json}',
    );
    this._fsWatcher = vscode.workspace.createFileSystemWatcher(pattern);
    this._fsWatcher.onDidChange(() => this._scheduleLoad());
    this._fsWatcher.onDidCreate(() => this._scheduleLoad());
    this._fsWatcher.onDidDelete(() => this._scheduleLoad());
  }

  private _detachWatcher(): void {
    this._fsWatcher?.dispose();
    this._fsWatcher = null;
  }

  /** Leading-edge debounced load — first event fires immediately. */
  private _scheduleLoad(): void { this._debouncedLoad(); }

  private _load(): void {
    if (!this._root) { return; }

    const ctxDir = path.join(this._root, '.ctx');
    const logPath = path.join(ctxDir, 'log.jsonl');
    const configPath = path.join(ctxDir, 'config.json');
    const statsPath = path.join(ctxDir, 'stats.json');
    const sessionPath = path.join(ctxDir, 'session.json');

    try {
      // Cap log.jsonl read to the last 512 KB to avoid blocking on large files
      let logContent = '';
      if (fs.existsSync(logPath)) {
        const stat = fs.statSync(logPath);
        const MAX_BYTES = 512 * 1024;
        if (stat.size > MAX_BYTES) {
          const fd = fs.openSync(logPath, 'r');
          const buf = Buffer.alloc(MAX_BYTES);
          try { fs.readSync(fd, buf, 0, MAX_BYTES, stat.size - MAX_BYTES); }
          finally { fs.closeSync(fd); }
          const raw = buf.toString('utf-8');
          // Skip the first (likely partial) line
          const idx = raw.indexOf('\n');
          logContent = idx >= 0 ? raw.slice(idx + 1) : raw;
        } else {
          logContent = fs.readFileSync(logPath, 'utf-8');
        }
      }

      const configJson = fs.existsSync(configPath)
        ? (JSON.parse(fs.readFileSync(configPath, 'utf-8')) as Record<string, unknown>)
        : {};

      const statsJson = fs.existsSync(statsPath)
        ? (JSON.parse(fs.readFileSync(statsPath, 'utf-8')) as Record<string, unknown>)
        : null;

      const sessionJson = fs.existsSync(sessionPath)
        ? (JSON.parse(fs.readFileSync(sessionPath, 'utf-8')) as Record<string, unknown>)
        : null;

      this._stats = parseLensData(logContent, configJson, statsJson, sessionJson);
      this._onDidChange.fire(this._stats);
    } catch (e) {
      // Parsing failure is non-fatal — keep last known stats
      console.error('Context Lens: failed to load stats', e);
    }
  }
}
