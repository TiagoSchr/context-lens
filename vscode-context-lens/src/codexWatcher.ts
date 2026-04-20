/**
 * codexWatcher.ts — watches ~/.codex/sessions/ (YYYY/MM/DD)/rollout-*.jsonl
 * rollouts whose `session_meta.payload.cwd` matches the current workspace.
 *
 * Because Codex scatters rollouts across YYYY/MM/DD date folders, we scan the
 * last 7 days on startup and keep an index { path, mtime, cwd, id } in memory.
 * The VS Code FileSystemWatcher is attached to the date directory of "today"
 * and refreshed lazily whenever the calendar day changes.
 */

import * as vscode from 'vscode';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import {
  parseCodexRollout,
  parseCodexSessionSummary,
  parseCodexSessionMeta,
} from './codexParser';
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
import { matchesCodexSessionId } from './codexSession';

interface RolloutEntry {
  path: string;
  mtime: number;
  cwd: string;
  id: string;
}

export class CodexWatcher implements vscode.Disposable {
  private readonly _onDidChange = new vscode.EventEmitter<ToolStats>();
  readonly onDidChange: vscode.Event<ToolStats> = this._onDidChange.event;

  private _stats: ToolStats = emptyToolStats('codex');
  private _workspaceCwds: string[] = [];
  private _currentFile: string | null = null;
  private _fsWatchers: vscode.FileSystemWatcher[] = [];
  private _wsSub: vscode.Disposable | null = null;
  private _lastByteOffset = 0;
  private _allLines: string[] = [];
  private _partialLine = '';
  private _lastPollMtimeMs = 0;
  private readonly _summaryCache = new Map<string, { mtimeMs: number; summary: ToolSessionSummary }>();
  private readonly _log = vscode.window.createOutputChannel('Context Lens: Codex', { log: true });
  private _debouncedLoad: Debounced;
  private _rolloutRoot: string;

  constructor() {
    this._rolloutRoot = path.join(os.homedir(), '.codex', 'sessions');
    this._debouncedLoad = leadingDebounce(() => this._loadIncremental(), 300);
  }

  start(): void {
    this._log.info(`Starting CodexWatcher (root=${this._rolloutRoot})`);
    this._refreshWorkspaceCwds();
    if (!fs.existsSync(this._rolloutRoot)) {
      this._log.info('No ~/.codex/sessions directory — skipping');
      return;
    }
    this._currentFile = this._findLatestMatchingRollout();
    this._log.info(`Current rollout: ${this._currentFile ?? 'none'}`);
    if (this._currentFile) { this._loadFull(); }
    this._attachWatchers();
    this._wsSub = vscode.workspace.onDidChangeWorkspaceFolders(() => this.refresh());
  }

  dispose(): void {
    for (const w of this._fsWatchers) { w.dispose(); }
    this._fsWatchers = [];
    this._wsSub?.dispose();
    this._onDidChange.dispose();
    this._debouncedLoad.cancel();
    this._log.dispose();
  }

  get stats(): ToolStats { return this._stats; }

  /** Switch to a specific session by its ID (rollout file basename without .jsonl). */
  switchToSession(sessionId: string): void {
    const candidates = this._listRecentRollouts(14, 60);
    const match = candidates.find((e) => matchesCodexSessionId(sessionId, {
      sessionId: e.id,
      fileId: path.basename(e.path, '.jsonl'),
    }));
    if (!match) { return; }
    this._currentFile = match.path;
    this._resetReadState();
    this._loadFull();
  }

  refresh(): void {
    for (const w of this._fsWatchers) { w.dispose(); }
    this._fsWatchers = [];
    this._refreshWorkspaceCwds();
    this._currentFile = this._findLatestMatchingRollout();
    this._resetReadState();
    if (this._currentFile) { this._loadFull(); }
    else { this._stats = emptyToolStats('codex'); this._onDidChange.fire(this._stats); }
    this._attachWatchers();
  }

  /** Lightweight poll: check mtime, trigger incremental load only if changed. */
  pollCheck(): void {
    if (!this._currentFile) {
      const latest = this._findLatestMatchingRollout();
      if (latest) { this._currentFile = latest; this._resetReadState(); this._debouncedLoad(); }
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

  private _refreshWorkspaceCwds(): void {
    // On case-insensitive filesystems (Windows, macOS default) normalize to lowercase.
    // On case-sensitive filesystems (Linux, macOS APFS strict) keep original case.
    this._workspaceCwds = (vscode.workspace.workspaceFolders ?? [])
      .map((f) => {
        const n = path.normalize(f.uri.fsPath);
        return process.platform === 'linux' ? n : n.toLowerCase();
      });
  }

  private _cwdMatches(rolloutCwd: string): boolean {
    if (!rolloutCwd) { return false; }
    const n = path.normalize(rolloutCwd);
    const norm = process.platform === 'linux' ? n : n.toLowerCase();
    return this._workspaceCwds.some((w) => w === norm);
  }

  /**
   * Scan up to the last 7 date directories for rollouts whose session_meta
   * cwd matches the current workspace, return the newest by mtime.
   */
  private _findLatestMatchingRollout(): string | null {
    if (!fs.existsSync(this._rolloutRoot)) { return null; }
    const candidates = this._listRecentRollouts(7 /* days */, 80 /* max */);
    let newest: RolloutEntry | null = null;
    for (const entry of candidates) {
      if (!this._cwdMatches(entry.cwd)) { continue; }
      if (!newest || entry.mtime > newest.mtime) { newest = entry; }
    }
    return newest?.path ?? null;
  }

  private _listRecentRollouts(days: number, max: number): RolloutEntry[] {
    const out: RolloutEntry[] = [];
    if (!fs.existsSync(this._rolloutRoot)) { return out; }

    // Walk the YYYY/MM/DD tree newest-first.
    const years = this._safeReadDir(this._rolloutRoot).sort().reverse();
    let scanned = 0;
    for (const y of years) {
      const yDir = path.join(this._rolloutRoot, y);
      if (!fs.statSync(yDir).isDirectory()) { continue; }
      const months = this._safeReadDir(yDir).sort().reverse();
      for (const m of months) {
        const mDir = path.join(yDir, m);
        if (!fs.statSync(mDir).isDirectory()) { continue; }
        const dayDirs = this._safeReadDir(mDir).sort().reverse();
        for (const d of dayDirs) {
          const dDir = path.join(mDir, d);
          if (!fs.statSync(dDir).isDirectory()) { continue; }
          scanned++;
          if (scanned > days) { return out.slice(0, max); }
          for (const f of this._safeReadDir(dDir)) {
            if (!f.startsWith('rollout-') || !f.endsWith('.jsonl')) { continue; }
            const full = path.join(dDir, f);
            try {
              const stat = fs.statSync(full);
              const meta = this._readSessionMeta(full);
              out.push({
                path: full,
                mtime: stat.mtimeMs,
                cwd: meta?.cwd ?? '',
                id: meta?.id ?? path.basename(full, '.jsonl'),
              });
              if (out.length >= max) { return out; }
            } catch { /* ignore */ }
          }
        }
      }
    }
    return out;
  }

  private _safeReadDir(dir: string): string[] {
    try { return fs.readdirSync(dir); } catch { return []; }
  }

  private _readSessionMeta(filePath: string): { id?: string; cwd?: string } | null {
    // Session meta is always the first JSONL line, but Codex embeds
    // base_instructions in the payload so it can be very large (~15-20 KB).
    // Read in growing chunks until we find the first newline.
    try {
      const fd = fs.openSync(filePath, 'r');
      try {
        let totalRead = 0;
        const chunks: Buffer[] = [];
        const CHUNK = 16 * 1024;
        const MAX_READ = 128 * 1024;
        while (totalRead < MAX_READ) {
          const buf = Buffer.alloc(CHUNK);
          const n = fs.readSync(fd, buf, 0, CHUNK, totalRead);
          if (n === 0) { break; }
          chunks.push(buf.slice(0, n));
          totalRead += n;
          const text = Buffer.concat(chunks).toString('utf-8');
          const nlIdx = text.indexOf('\n');
          if (nlIdx >= 0) {
            return parseCodexSessionMeta(text.slice(0, nlIdx));
          }
        }
        // No newline found — try whatever we have
        const text = Buffer.concat(chunks).toString('utf-8');
        return parseCodexSessionMeta(text);
      } finally { fs.closeSync(fd); }
    } catch { return null; }
  }

  // ── File system watching ──────────────────────────────────────────

  private _attachWatchers(): void {
    if (!fs.existsSync(this._rolloutRoot)) { return; }
    // Watch all rollout jsonl files recursively under ~/.codex/sessions.
    // The '**' glob inside RelativePattern is supported by VS Code.
    const pattern = new vscode.RelativePattern(this._rolloutRoot, '**/rollout-*.jsonl');
    const w = vscode.workspace.createFileSystemWatcher(pattern);
    w.onDidChange((uri) => this._onRolloutEvent(uri.fsPath, 'change'));
    w.onDidCreate((uri) => this._onRolloutEvent(uri.fsPath, 'create'));
    w.onDidDelete((uri) => this._onRolloutEvent(uri.fsPath, 'delete'));
    this._fsWatchers.push(w);
  }

  private _onRolloutEvent(filePath: string, kind: 'change' | 'create' | 'delete'): void {
    if (kind === 'delete') {
      this._summaryCache.delete(filePath);
      if (filePath === this._currentFile) {
        this._currentFile = this._findLatestMatchingRollout();
        this._resetReadState();
      }
      this._debouncedLoad();
      return;
    }

    // A new rollout appeared or changed — check if it belongs to this workspace.
    // We read session_meta lazily to avoid parsing every unrelated rollout.
    if (filePath !== this._currentFile) {
      const meta = this._readSessionMeta(filePath);
      if (!meta || !this._cwdMatches(meta.cwd ?? '')) { return; }
      // Switch to this rollout — it's the most recent activity for this workspace.
      this._currentFile = filePath;
      this._resetReadState();
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
      this._log.warn(`Failed to load rollout: ${e}`);
    }
  }

  private _loadIncremental(): void {
    if (!this._currentFile) {
      const latest = this._findLatestMatchingRollout();
      if (latest) { this._currentFile = latest; this._resetReadState(); this._loadFull(); }
      return;
    }
    try {
      const stat = fs.statSync(this._currentFile);
      if (stat.size < this._lastByteOffset) { this._loadFull(); return; }
      if (stat.size === this._lastByteOffset) { return; }
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
      const latest = this._findLatestMatchingRollout();
      if (latest && latest !== this._currentFile) {
        this._currentFile = latest; this._resetReadState(); this._loadFull();
      }
    }
  }

  // ── Parsing ───────────────────────────────────────────────────────

  private _parse(): void {
    this._stats = parseCodexRollout(this._allLines);
    if (!this._stats.sessionId && this._currentFile) {
      this._stats.sessionId = path.basename(this._currentFile, '.jsonl');
    }
    this._stats.allSessions = this._collectSessionSummaries();
    this._onDidChange.fire(this._stats);
  }

  private _collectSessionSummaries(): ToolSessionSummary[] {
    const entries = this._listRecentRollouts(14, 60)
      .filter((e) => this._cwdMatches(e.cwd))
      .sort((a, b) => b.mtime - a.mtime)
      .slice(0, 10);

    const out: ToolSessionSummary[] = [];
    const MAX_BYTES = 512 * 1024;

    for (const entry of entries) {
      if (entry.path === this._currentFile) {
        out.push({
          sessionId: this._stats.sessionId || entry.id,
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
          // For long rollouts we need the FIRST line (session_meta) plus some
          // content; read head + tail to capture both.
          const fd = fs.openSync(entry.path, 'r');
          try {
            const headBuf = Buffer.alloc(32 * 1024);
            fs.readSync(fd, headBuf, 0, headBuf.length, 0);
            const tailBuf = Buffer.alloc(MAX_BYTES);
            fs.readSync(fd, tailBuf, 0, tailBuf.length, stat.size - tailBuf.length);
            content = headBuf.toString('utf-8') + '\n' + tailBuf.toString('utf-8');
          } finally { fs.closeSync(fd); }
        } else {
          content = fs.readFileSync(entry.path, 'utf-8');
        }
        const summary = parseCodexSessionSummary(content);
        if (!summary.sessionId) { summary.sessionId = entry.id; }
        this._summaryCache.set(entry.path, { mtimeMs: entry.mtime, summary });
        out.push({ ...summary, active: false });
      } catch { /* skip */ }
    }
    return out;
  }

  private _resetReadState(): void {
    const empty = resetTranscriptBuffer();
    this._lastByteOffset = 0;
    this._allLines = empty.completeLines;
    this._partialLine = empty.partialLine;
  }
}
