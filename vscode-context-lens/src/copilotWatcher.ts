/**
 * copilotWatcher.ts — watches Copilot Chat transcript JSONL files for real-time
 * token usage estimation.
 *
 * Discovers the workspace hash directory first, then watches for transcript
 * files to appear or change under GitHub.copilot-chat/transcripts/.
 */
import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import {
  parseCopilotTranscript,
  parseSessionSummary,
  parseChatSessionCustomTitle,
  parseChatSessionModel,
  CopilotStats,
  CopilotSessionSummary,
  emptyCopilotStats,
} from './copilotParser';
import {
  appendTranscriptChunk,
  finalizeTranscriptPartial,
  parseTranscriptSnapshot,
  resetTranscriptBuffer,
} from './transcriptBuffer';
import { leadingDebounce, Debounced } from './debounce';

export class CopilotWatcher implements vscode.Disposable {
  private readonly _onDidChange = new vscode.EventEmitter<CopilotStats>();
  readonly onDidChange: vscode.Event<CopilotStats> = this._onDidChange.event;

  private _stats: CopilotStats = emptyCopilotStats();
  private _workspaceHashDir: string | null = null;
  private _transcriptDir: string | null = null;
  private _chatSessionsDir: string | null = null;
  private _currentFile: string | null = null;
  private _fsWatcher: vscode.FileSystemWatcher | null = null;
  private _chatSessionsWatcher: vscode.FileSystemWatcher | null = null;
  private _debouncedLoad: Debounced;
  private _debouncedParse: Debounced;
  private _lastByteOffset = 0;
  private _allLines: string[] = [];
  private _partialLine = '';
  private _lastPollMtimeMs = 0;
  /** Cache summaries by transcript path → avoids re-parsing unchanged files. */
  private readonly _summaryCache = new Map<string, { mtimeMs: number; summary: CopilotSessionSummary }>();
  private readonly _log = vscode.window.createOutputChannel('Context Lens: Copilot', { log: true });

  constructor(
    private readonly _storageUri: vscode.Uri | undefined,
    private readonly _globalStorageUri: vscode.Uri | undefined,
  ) {
    this._debouncedLoad = leadingDebounce(() => this._loadIncremental(), 300);
    this._debouncedParse = leadingDebounce(() => this._parse(), 300);
  }

  // ── Lifecycle ────────────────────────────────────────────────────────────

  start(): void {
    this._log.info('Starting CopilotWatcher...');
    this._log.info(`storageUri: ${this._storageUri?.fsPath ?? 'undefined'}`);
    this._log.info(`globalStorageUri: ${this._globalStorageUri?.fsPath ?? 'undefined'}`);

    this._workspaceHashDir = this._findWorkspaceHashDir();
    this._log.info(`Workspace hash dir: ${this._workspaceHashDir ?? 'NOT FOUND'}`);
    if (!this._workspaceHashDir) { return; }

    this._transcriptDir = this._findTranscriptDir(this._workspaceHashDir);
    this._log.info(`Transcript dir: ${this._transcriptDir ?? 'NOT FOUND'}`);
    this._chatSessionsDir = this._findChatSessionsDir(this._workspaceHashDir);
    this._log.info(`Chat sessions dir: ${this._chatSessionsDir ?? 'NOT FOUND'}`);

    this._currentFile = this._findLatestTranscript();
    this._log.info(`Current transcript: ${this._currentFile ?? 'none'}`);
    if (this._currentFile) {
      this._loadFull();
    }

    this._attachWatcher();
  }

  dispose(): void {
    this._detachWatcher();
    this._onDidChange.dispose();
    this._debouncedLoad.cancel();
    this._debouncedParse.cancel();
  }

  // ── Public API ───────────────────────────────────────────────────────────

  get stats(): CopilotStats { return this._stats; }

  /** Switch to a specific session by its ID (file basename without .jsonl). */
  switchToSession(sessionId: string): void {
    if (!this._transcriptDir) { return; }
    const filePath = path.join(this._transcriptDir, sessionId + '.jsonl');
    if (!fs.existsSync(filePath)) { return; }
    this._switchToTranscript(filePath);
    this._loadFull();
  }

  /** Force a full re-read from disk. */
  refresh(): void {
    if (this._workspaceHashDir) {
      this._transcriptDir = this._findTranscriptDir(this._workspaceHashDir);
      this._chatSessionsDir = this._findChatSessionsDir(this._workspaceHashDir);
    }
    this._switchToTranscript(this._findLatestTranscript());

    if (this._currentFile) {
      this._loadFull();
    } else {
      this._stats = emptyCopilotStats();
      this._onDidChange.fire(this._stats);
    }
  }

  /** Lightweight poll: check mtime, trigger incremental load only if changed. */
  pollCheck(): void {
    if (!this._currentFile) {
      const latest = this._findLatestTranscript();
      if (latest) { this._switchToTranscript(latest); this._debouncedLoad(); }
      return;
    }
    try {
      const mtime = fs.statSync(this._currentFile).mtimeMs;
      if (mtime > this._lastPollMtimeMs) {
        this._lastPollMtimeMs = mtime;
        this._debouncedLoad();
      }
    } catch { /* file gone — will be handled on next refresh */ }
  }

  // ── Path discovery ───────────────────────────────────────────────────────

  /**
   * Derive the workspaceStorage/{hash} directory using multiple strategies:
   *
   * 1. From storageUri — go up to the workspace hash dir
   * 2. From globalStorageUri — derive workspaceStorage root, scan hash dirs
   *
   * storageUri points to: workspaceStorage/{hash}/{extensionId}  (may not exist on disk)
   * globalStorageUri points to: globalStorage/{extensionId}
   */
  private _findWorkspaceHashDir(): string | null {
    // Strategy 1: storageUri → parent is hash dir
    if (this._storageUri) {
      const hashDir = path.dirname(this._storageUri.fsPath);
      this._log.info(`Strategy 1: checking hashDir=${hashDir}`);
      if (fs.existsSync(hashDir)) {
        return hashDir;
      }
    }

    // Strategy 2: globalStorageUri → derive workspaceStorage root, scan all hashes
    if (this._globalStorageUri) {
      try {
        // globalStorageUri → .../globalStorage/{extensionId}
        // We need          → .../workspaceStorage/
        const globalPath = this._globalStorageUri.fsPath;
        const userDir = path.dirname(path.dirname(globalPath)); // → .../Code/User
        const wsStorageRoot = path.join(userDir, 'workspaceStorage');
        this._log.info(`Strategy 2: scanning wsStorageRoot=${wsStorageRoot}`);

        if (fs.existsSync(wsStorageRoot)) {
          const hashDirs = fs.readdirSync(wsStorageRoot, { withFileTypes: true })
            .filter((d) => d.isDirectory())
            .map((d) => path.join(wsStorageRoot, d.name));

          const workspaceFolders = vscode.workspace.workspaceFolders?.map((f) => f.uri.fsPath.toLowerCase()) ?? [];

          for (const hashDir of hashDirs) {
            const wsJsonPath = path.join(hashDir, 'workspace.json');
            if (!fs.existsSync(wsJsonPath)) { continue; }

            try {
              const wsJson = JSON.parse(fs.readFileSync(wsJsonPath, 'utf-8')) as Record<string, unknown>;
              const folder = (wsJson.folder as string) ?? '';
              const decodedFolder = path.normalize(decodeURIComponent(folder.replace(/^file:\/\/\/?/, '')));

              const dfLower = decodedFolder.toLowerCase();
              // Append separator to prevent false-positive prefix match
              // (e.g. "C:\foo" falsely matching "C:\foobar").
              const dfWithSep = dfLower.endsWith(path.sep) ? dfLower : dfLower + path.sep;
              const matches = workspaceFolders.some((wf) => {
                const wfLower = wf.toLowerCase();
                if (wfLower === dfLower) { return true; }
                const wfWithSep = wfLower.endsWith(path.sep) ? wfLower : wfLower + path.sep;
                return wfWithSep.startsWith(dfWithSep);
              });

              if (!matches) { continue; }

              this._log.info(`Strategy 2: matched workspace hash dir=${hashDir}`);
              return hashDir;
            } catch {
              continue;
            }
          }
        }
      } catch (e) {
        this._log.warn(`Strategy 2 failed: ${e}`);
      }
    }

    this._log.warn('No workspace hash dir found with any strategy');
    return null;
  }

  /** Check if a workspace hash dir contains GitHub.copilot-chat/transcripts/. */
  private _findTranscriptDir(hashDir: string): string | null {
    const candidates = [
      path.join(hashDir, 'GitHub.copilot-chat', 'transcripts'),
      path.join(hashDir, 'github.copilot-chat', 'transcripts'),
    ];
    for (const dir of candidates) {
      if (fs.existsSync(dir)) {
        return dir;
      }
    }
    return null;
  }

  private _findChatSessionsDir(hashDir: string): string | null {
    const dir = path.join(hashDir, 'chatSessions');
    return fs.existsSync(dir) ? dir : null;
  }

  /** Find the most recently modified .jsonl file in the transcript directory. */
  private _findLatestTranscript(): string | null {
    if (!this._transcriptDir) { return null; }

    try {
      const files = fs.readdirSync(this._transcriptDir)
        .filter((f) => f.endsWith('.jsonl'));

      if (files.length === 0) { return null; }

      const withStats = files.map((f) => {
        const full = path.join(this._transcriptDir!, f);
        try {
          return { path: full, mtime: fs.statSync(full).mtimeMs };
        } catch {
          return { path: full, mtime: 0 };
        }
      });

      withStats.sort((a, b) => b.mtime - a.mtime);
      return withStats[0].path;
    } catch {
      return null;
    }
  }

  // ── File system watching ─────────────────────────────────────────────────

  private _attachWatcher(): void {
    if (!this._workspaceHashDir) { return; }

    const pattern = new vscode.RelativePattern(
      this._workspaceHashDir,
      '{GitHub.copilot-chat,github.copilot-chat}/transcripts/*.jsonl',
    );
    this._fsWatcher = vscode.workspace.createFileSystemWatcher(pattern);

    this._fsWatcher.onDidChange((uri) => this._handleTranscriptChange(uri.fsPath));
    this._fsWatcher.onDidCreate((uri) => this._handleTranscriptCreate(uri.fsPath));
    this._fsWatcher.onDidDelete((uri) => this._handleTranscriptDelete(uri.fsPath));

    const chatPattern = new vscode.RelativePattern(this._workspaceHashDir, 'chatSessions/*.jsonl');
    this._chatSessionsWatcher = vscode.workspace.createFileSystemWatcher(chatPattern);
    this._chatSessionsWatcher.onDidChange((uri) => this._handleChatSessionChange(uri.fsPath));
    this._chatSessionsWatcher.onDidCreate((uri) => this._handleChatSessionChange(uri.fsPath));
    this._chatSessionsWatcher.onDidDelete((uri) => this._handleChatSessionChange(uri.fsPath));
  }

  private _detachWatcher(): void {
    this._fsWatcher?.dispose();
    this._fsWatcher = null;
    this._chatSessionsWatcher?.dispose();
    this._chatSessionsWatcher = null;
  }

  /** Leading-edge debounced load — fires immediately, coalesces bursts. */
  private _scheduleLoad(): void { this._debouncedLoad(); }
  private _scheduleParseOnly(): void { this._debouncedParse(); }

  private _handleTranscriptCreate(filePath: string): void {
    this._refreshTranscriptDirFromFile(filePath);
    this._switchToTranscript(filePath);
    this._scheduleLoad();
  }

  private _handleTranscriptChange(filePath: string): void {
    this._refreshTranscriptDirFromFile(filePath);
    if (!this._currentFile) {
      this._switchToTranscript(filePath);
    } else if (filePath !== this._currentFile) {
      const latest = this._findLatestTranscript();
      if (latest === filePath) {
        this._switchToTranscript(filePath);
      }
    }
    this._scheduleLoad();
  }

  private _handleTranscriptDelete(filePath: string): void {
    if (this._currentFile === filePath) {
      this._switchToTranscript(null);
    }
    if (this._workspaceHashDir) {
      this._transcriptDir = this._findTranscriptDir(this._workspaceHashDir);
    }
    this._scheduleLoad();
  }

  private _handleChatSessionChange(filePath: string): void {
    this._refreshChatSessionsDirFromFile(filePath);
    this._scheduleParseOnly();
  }

  // ── File reading ─────────────────────────────────────────────────────────

  /** Full read from the start of the file. Used on init or session switch. */
  private _loadFull(): void {
    if (!this._currentFile) { return; }

    try {
      const content = fs.readFileSync(this._currentFile, 'utf-8');
      const snapshot = parseTranscriptSnapshot(content);
      this._lastByteOffset = Buffer.byteLength(content, 'utf-8');
      this._allLines = snapshot.completeLines;
      this._partialLine = snapshot.partialLine;
      this._parse();
    } catch {
      // File read failed — keep previous stats
    }
  }

  /** Incremental read — only parse new bytes appended since last read. */
  private _loadIncremental(): void {
    if (!this._transcriptDir && this._workspaceHashDir) {
      this._transcriptDir = this._findTranscriptDir(this._workspaceHashDir);
    }

    if (!this._currentFile) {
      const latest = this._findLatestTranscript();
      if (latest) {
        this._switchToTranscript(latest);
        this._loadFull();
      }
      return;
    }

    try {
      const stat = fs.statSync(this._currentFile);
      if (stat.size < this._lastByteOffset) {
        this._loadFull();
        return;
      }

      if (stat.size === this._lastByteOffset) {
        const latest = this._findLatestTranscript();
        if (latest && latest !== this._currentFile) {
          this._switchToTranscript(latest);
          this._loadFull();
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
        this._switchToTranscript(latest);
        this._loadFull();
      }
    }
  }

  // ── Parsing ──────────────────────────────────────────────────────────────

  private _parse(): void {
    this._stats = parseCopilotTranscript(this._allLines);
    if (!this._stats.sessionId && this._currentFile) {
      this._stats.sessionId = path.basename(this._currentFile, '.jsonl');
    }
    this._stats.sessionName = this._resolveSessionName(this._stats.sessionId, this._stats.sessionName);
    this._stats.model = this._resolveSessionModel(this._stats.sessionId);
    this._stats.allSessions = this._collectSessionSummaries();
    this._onDidChange.fire(this._stats);
  }

  /**
   * Scan all transcript files for session summaries.
   * The current session gets full stats; others get lightweight summaries.
   * Limited to last 10 sessions for performance.
   */
  private _collectSessionSummaries(): CopilotSessionSummary[] {
    if (!this._transcriptDir) { return []; }

    try {
      const files = fs.readdirSync(this._transcriptDir)
        .filter((f) => f.endsWith('.jsonl'));

      if (files.length === 0) { return []; }

      const withStats = files.map((f) => {
        const full = path.join(this._transcriptDir!, f);
        try {
          return { path: full, mtime: fs.statSync(full).mtimeMs };
        } catch {
          return { path: full, mtime: 0 };
        }
      });
      withStats.sort((a, b) => b.mtime - a.mtime);

      const summaries: CopilotSessionSummary[] = [];
      for (const entry of withStats.slice(0, 10)) {
        if (entry.path === this._currentFile) {
          summaries.push({
            sessionId: this._stats.sessionId,
            name: this._stats.sessionName,
            totalTokens: this._stats.totalTokens,
            messageCount: this._stats.messageCount,
            lastMessageTs: this._stats.lastMessageTs,
            active: true,
          });
          continue;
        }
        // mtime cache — skip re-parse if file hasn't changed.
        const cached = this._summaryCache.get(entry.path);
        if (cached && cached.mtimeMs === entry.mtime) {
          summaries.push({ ...cached.summary, active: false });
          continue;
        }
        try {
          const stat = fs.statSync(entry.path);
          const MAX_BYTES = 512 * 1024;
          let content: string;
          if (stat.size > MAX_BYTES) {
            content = fs.readFileSync(entry.path, { encoding: 'utf-8' }).slice(0, MAX_BYTES);
          } else {
            content = fs.readFileSync(entry.path, 'utf-8');
          }
          const summary = parseSessionSummary(content);
          if (!summary.sessionId) {
            summary.sessionId = path.basename(entry.path, '.jsonl');
          }
          summary.name = this._resolveSessionName(summary.sessionId, summary.name);
          summary.active = false;
          this._summaryCache.set(entry.path, { mtimeMs: entry.mtime, summary: { ...summary } });
          summaries.push(summary);
        } catch {
          // skip unreadable files
        }
      }

      // Evict stale cache entries (files no longer in the top-10 window).
      const keep = new Set(withStats.slice(0, 10).map((e) => e.path));
      for (const key of this._summaryCache.keys()) {
        if (!keep.has(key)) { this._summaryCache.delete(key); }
      }

      return summaries;
    } catch {
      return [];
    }
  }

  private _refreshTranscriptDirFromFile(filePath: string): void {
    const dir = path.dirname(filePath);
    if (dir !== this._transcriptDir) {
      this._transcriptDir = dir;
      this._log.info(`Transcript dir updated: ${dir}`);
    }
  }

  private _refreshChatSessionsDirFromFile(filePath: string): void {
    const dir = path.dirname(filePath);
    if (dir !== this._chatSessionsDir) {
      this._chatSessionsDir = dir;
      this._log.info(`Chat sessions dir updated: ${dir}`);
    }
  }

  private _switchToTranscript(filePath: string | null): void {
    if (filePath === this._currentFile) { return; }
    this._currentFile = filePath;
    this._resetReadState();
    if (filePath) {
      this._refreshTranscriptDirFromFile(filePath);
    }
    this._log.info(`Current transcript: ${filePath ?? 'none'}`);
  }

  private _resolveSessionName(sessionId: string, fallback: string): string {
    if (!sessionId) { return fallback; }

    if (!this._chatSessionsDir && this._workspaceHashDir) {
      this._chatSessionsDir = this._findChatSessionsDir(this._workspaceHashDir);
    }
    if (!this._chatSessionsDir) { return fallback; }

    const chatSessionPath = path.join(this._chatSessionsDir, `${sessionId}.jsonl`);
    if (!fs.existsSync(chatSessionPath)) { return fallback; }

    try {
      const content = this._readFileHead(chatSessionPath, 128 * 1024);
      return parseChatSessionCustomTitle(content) || fallback;
    } catch {
      return fallback;
    }
  }

  private _resolveSessionModel(sessionId: string): string {
    if (!sessionId) { return ''; }

    if (!this._chatSessionsDir && this._workspaceHashDir) {
      this._chatSessionsDir = this._findChatSessionsDir(this._workspaceHashDir);
    }
    if (!this._chatSessionsDir) { return ''; }

    const chatSessionPath = path.join(this._chatSessionsDir, `${sessionId}.jsonl`);
    if (!fs.existsSync(chatSessionPath)) { return ''; }

    try {
      const content = this._readFileHead(chatSessionPath, 128 * 1024);
      return parseChatSessionModel(content);
    } catch {
      return '';
    }
  }

  private _readFileHead(filePath: string, maxBytes: number): string {
    const stat = fs.statSync(filePath);
    const bytes = Math.min(stat.size, maxBytes);
    const buf = Buffer.alloc(bytes);
    const fd = fs.openSync(filePath, 'r');
    try {
      fs.readSync(fd, buf, 0, bytes, 0);
    } finally {
      fs.closeSync(fd);
    }
    return buf.toString('utf-8');
  }

  private _resetReadState(): void {
    const empty = resetTranscriptBuffer();
    this._lastByteOffset = 0;
    this._allLines = empty.completeLines;
    this._partialLine = empty.partialLine;
  }
}
