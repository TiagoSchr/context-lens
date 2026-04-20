/**
 * claudeWatcher.ts — watches ~/.claude/projects/<cwd>/*.jsonl for the current
 * workspace and emits real-time token stats.
 *
 * Mirrors the structure of copilotWatcher.ts: incremental reads, mtime cache,
 * leading-edge debounce, and a session-history list of the N newest sessions.
 */

import * as vscode from 'vscode';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import {
  parseClaudeTranscript,
  parseClaudeSessionSummary,
  sanitizeProjectCwd,
} from './claudeParser';
import {
  ToolStats,
  ToolSessionSummary,
  emptyToolStats,
} from './toolStats';
import {
  appendTranscriptChunk,
  finalizeTranscriptPartial,
  parseTranscriptSnapshot,
  resetTranscriptBuffer,
} from './transcriptBuffer';
import { leadingDebounce, Debounced } from './debounce';

export class ClaudeWatcher implements vscode.Disposable {
  private readonly _onDidChange = new vscode.EventEmitter<ToolStats>();
  readonly onDidChange: vscode.Event<ToolStats> = this._onDidChange.event;

  private _stats: ToolStats = emptyToolStats('claude');
  private _projectDir: string | null = null;
  private _currentFile: string | null = null;
  private _fsWatcher: vscode.FileSystemWatcher | null = null;
  private _wsSub: vscode.Disposable | null = null;
  private _lastByteOffset = 0;
  private _allLines: string[] = [];
  private _partialLine = '';
  private _lastPollMtimeMs = 0;
  private readonly _summaryCache = new Map<string, { mtimeMs: number; summary: ToolSessionSummary }>();
  private readonly _log = vscode.window.createOutputChannel('Context Lens: Claude', { log: true });
  private _debouncedLoad: Debounced;

  constructor() {
    this._debouncedLoad = leadingDebounce(() => this._loadIncremental(), 300);
  }

  start(): void {
    this._log.info('Starting ClaudeWatcher...');
    this._projectDir = this._findProjectDir();
    this._log.info(`Project dir: ${this._projectDir ?? 'NOT FOUND'}`);
    if (this._projectDir) {
      this._currentFile = this._findLatestTranscript();
      this._log.info(`Current transcript: ${this._currentFile ?? 'none'}`);
      if (this._currentFile) { this._loadFull(); }
      this._attachWatcher();
    }
    this._wsSub = vscode.workspace.onDidChangeWorkspaceFolders(() => this.refresh());
  }

  dispose(): void {
    this._fsWatcher?.dispose();
    this._wsSub?.dispose();
    this._onDidChange.dispose();
    this._debouncedLoad.cancel();
    this._log.dispose();
  }

  get stats(): ToolStats { return this._stats; }

  /** Switch to a specific session by its ID (file basename without .jsonl). */
  switchToSession(sessionId: string): void {
    if (!this._projectDir) { return; }
    const filePath = path.join(this._projectDir, sessionId + '.jsonl');
    if (!fs.existsSync(filePath)) { return; }
    this._currentFile = filePath;
    this._resetReadState();
    this._loadFull();
  }

  refresh(): void {
    this._fsWatcher?.dispose();
    this._fsWatcher = null;
    this._projectDir = this._findProjectDir();
    this._currentFile = this._projectDir ? this._findLatestTranscript() : null;
    this._resetReadState();
    if (this._currentFile) { this._loadFull(); }
    else { this._stats = emptyToolStats('claude'); this._onDidChange.fire(this._stats); }
    this._attachWatcher();
  }

  /** Lightweight poll: check mtime, trigger incremental load only if changed. */
  pollCheck(): void {
    if (!this._currentFile) {
      if (this._projectDir) {
        const latest = this._findLatestTranscript();
        if (latest) { this._currentFile = latest; this._resetReadState(); this._debouncedLoad(); }
      }
      return;
    }
    try {
      const mtime = fs.statSync(this._currentFile).mtimeMs;
      if (mtime > this._lastPollMtimeMs) {
        this._lastPollMtimeMs = mtime;
        this._debouncedLoad();
      }
    } catch { /* file gone */ }
  }

  // ── Path discovery ────────────────────────────────────────────────

  private _findProjectDir(): string | null {
    const claudeRoot = path.join(os.homedir(), '.claude', 'projects');
    if (!fs.existsSync(claudeRoot)) { return null; }

    const folders = vscode.workspace.workspaceFolders ?? [];
    if (folders.length === 0) { return null; }

    // Try each workspace folder with each sanitization variant.
    for (const folder of folders) {
      const cwd = folder.uri.fsPath;
      for (const variant of sanitizeProjectCwd(cwd)) {
        const candidate = path.join(claudeRoot, variant);
        if (fs.existsSync(candidate)) { return candidate; }
      }
    }

    // Fallback: scan all project folders and pick any whose name contains
    // the workspace folder's basename (defensive — sanitization may differ
    // across Claude Code versions).
    try {
      const entries = fs.readdirSync(claudeRoot, { withFileTypes: true })
        .filter((d) => d.isDirectory())
        .map((d) => d.name);
      for (const folder of folders) {
        const basename = path.basename(folder.uri.fsPath).replace(/[^A-Za-z0-9]/g, '-');
        const hit = entries.find((name) => name.endsWith(`-${basename}`) || name === basename);
        if (hit) { return path.join(claudeRoot, hit); }
      }
    } catch { /* ignore */ }

    return null;
  }

  private _findLatestTranscript(): string | null {
    if (!this._projectDir) { return null; }
    try {
      const files = fs.readdirSync(this._projectDir).filter((f) => f.endsWith('.jsonl'));
      if (files.length === 0) { return null; }
      const withStats = files.map((f) => {
        const full = path.join(this._projectDir!, f);
        try { return { path: full, mtime: fs.statSync(full).mtimeMs }; }
        catch { return { path: full, mtime: 0 }; }
      });
      withStats.sort((a, b) => b.mtime - a.mtime);
      return withStats[0].path;
    } catch { return null; }
  }

  // ── File system watching ──────────────────────────────────────────

  private _attachWatcher(): void {
    if (!this._projectDir) { return; }
    const pattern = new vscode.RelativePattern(this._projectDir, '*.jsonl');
    this._fsWatcher = vscode.workspace.createFileSystemWatcher(pattern);
    this._fsWatcher.onDidChange((uri) => this._onTranscriptEvent(uri.fsPath, 'change'));
    this._fsWatcher.onDidCreate((uri) => this._onTranscriptEvent(uri.fsPath, 'create'));
    this._fsWatcher.onDidDelete((uri) => this._onTranscriptEvent(uri.fsPath, 'delete'));
  }

  private _onTranscriptEvent(filePath: string, kind: 'change' | 'create' | 'delete'): void {
    if (kind === 'delete') {
      this._summaryCache.delete(filePath);
      if (filePath === this._currentFile) {
        this._currentFile = this._findLatestTranscript();
        this._resetReadState();
      }
    } else if (kind === 'create') {
      // New session — switch to it (latest by mtime)
      this._currentFile = filePath;
      this._resetReadState();
    } else if (kind === 'change') {
      if (!this._currentFile) {
        this._currentFile = filePath;
        this._resetReadState();
      } else if (filePath !== this._currentFile) {
        // A different session received a write — switch if it's now the newest
        const latest = this._findLatestTranscript();
        if (latest === filePath) {
          this._currentFile = filePath;
          this._resetReadState();
        }
      }
    }
    this._debouncedLoad();
  }

  // ── File reading ──────────────────────────────────────────────────

  private _loadFull(): void {
    if (!this._currentFile) { return; }
    try {
      const content = fs.readFileSync(this._currentFile, 'utf-8');
      const snapshot = parseTranscriptSnapshot(content);
      this._lastByteOffset = Buffer.byteLength(content, 'utf-8');
      this._allLines = snapshot.completeLines;
      this._partialLine = snapshot.partialLine;
      this._parse();
    } catch (e) {
      this._log.warn(`Failed to load transcript: ${e}`);
    }
  }

  private _loadIncremental(): void {
    if (!this._currentFile) {
      const latest = this._findLatestTranscript();
      if (latest) { this._currentFile = latest; this._resetReadState(); this._loadFull(); }
      return;
    }
    try {
      const stat = fs.statSync(this._currentFile);
      if (stat.size < this._lastByteOffset) { this._loadFull(); return; }
      if (stat.size === this._lastByteOffset) {
        const latest = this._findLatestTranscript();
        if (latest && latest !== this._currentFile) {
          this._currentFile = latest; this._resetReadState(); this._loadFull();
        }
        return;
      }
      const newBytes = stat.size - this._lastByteOffset;
      const buf = Buffer.alloc(newBytes);
      const fd = fs.openSync(this._currentFile, 'r');
      try { fs.readSync(fd, buf, 0, newBytes, this._lastByteOffset); }
      finally { fs.closeSync(fd); }
      this._lastByteOffset = stat.size;

      const chunk = appendTranscriptChunk(buf.toString('utf-8'), this._partialLine);
      const finalized = finalizeTranscriptPartial(chunk.partialLine);
      const newLines = [...chunk.completeLines, ...finalized.completeLines];
      this._partialLine = finalized.partialLine;
      if (newLines.length > 0) {
        this._allLines.push(...newLines);
        this._parse();
      }
    } catch {
      const latest = this._findLatestTranscript();
      if (latest && latest !== this._currentFile) {
        this._currentFile = latest; this._resetReadState(); this._loadFull();
      }
    }
  }

  // ── Parsing ───────────────────────────────────────────────────────

  private _parse(): void {
    this._stats = parseClaudeTranscript(this._allLines);
    if (!this._stats.sessionId && this._currentFile) {
      this._stats.sessionId = path.basename(this._currentFile, '.jsonl');
    }
    this._stats.allSessions = this._collectSessionSummaries();
    this._onDidChange.fire(this._stats);
  }

  /** Scan project dir and return up to 10 newest sessions. mtime-cached. */
  private _collectSessionSummaries(): ToolSessionSummary[] {
    if (!this._projectDir) { return []; }
    try {
      const files = fs.readdirSync(this._projectDir).filter((f) => f.endsWith('.jsonl'));
      if (files.length === 0) { return []; }

      const withStats = files.map((f) => {
        const full = path.join(this._projectDir!, f);
        try { return { path: full, mtime: fs.statSync(full).mtimeMs }; }
        catch { return { path: full, mtime: 0 }; }
      });
      withStats.sort((a, b) => b.mtime - a.mtime);

      const out: ToolSessionSummary[] = [];
      const MAX_BYTES = 512 * 1024;
      for (const entry of withStats.slice(0, 10)) {
        if (entry.path === this._currentFile) {
          out.push({
            sessionId: this._stats.sessionId,
            name: this._stats.sessionName,
            totalTokens: this._stats.totalTokens,
            messageCount: this._stats.messageCount,
            lastMessageTs: this._stats.lastMessageTs,
            active: true,
          });
          continue;
        }
        const cached = this._summaryCache.get(entry.path);
        if (cached && cached.mtimeMs === entry.mtime) {
          out.push({ ...cached.summary, active: false });
          continue;
        }
        try {
          const stat = fs.statSync(entry.path);
          let content: string;
          if (stat.size > MAX_BYTES) {
            const fd = fs.openSync(entry.path, 'r');
            const buf = Buffer.alloc(MAX_BYTES);
            try { fs.readSync(fd, buf, 0, MAX_BYTES, stat.size - MAX_BYTES); }
            finally { fs.closeSync(fd); }
            const raw = buf.toString('utf-8');
            const idx = raw.indexOf('\n');
            content = idx >= 0 ? raw.slice(idx + 1) : raw;
          } else {
            content = fs.readFileSync(entry.path, 'utf-8');
          }
          const summary = parseClaudeSessionSummary(content);
          if (!summary.sessionId) { summary.sessionId = path.basename(entry.path, '.jsonl'); }
          this._summaryCache.set(entry.path, { mtimeMs: entry.mtime, summary });
          out.push({ ...summary, active: false });
        } catch { /* skip unreadable */ }
      }
      return out;
    } catch { return []; }
  }

  private _resetReadState(): void {
    const empty = resetTranscriptBuffer();
    this._lastByteOffset = 0;
    this._allLines = empty.completeLines;
    this._partialLine = empty.partialLine;
  }
}
