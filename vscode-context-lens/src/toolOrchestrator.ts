/**
 * toolOrchestrator.ts — unifies Copilot, Claude, and Codex watchers.
 *
 * Responsibilities:
 *   1. Maintain the latest `ToolStats`-compatible snapshot for each tool.
 *   2. Detect which tool is "currently active" based on the most recent
 *      activity timestamp across all three watchers (with a small hysteresis
 *      to avoid flicker when two tools are running in parallel).
 *   3. Emit `onDidChange` whenever any tool's stats update, carrying the
 *      merged payload the sidebar needs.
 */

import * as vscode from 'vscode';
import { CopilotWatcher } from './copilotWatcher';
import { CopilotStats, emptyCopilotStats } from './copilotParser';
import { ClaudeWatcher } from './claudeWatcher';
import { CodexWatcher } from './codexWatcher';
import { ToolStats, ToolName, emptyToolStats } from './toolStats';
import {
  ToolAvailability,
  firstAvailableTool,
  isToolAvailable,
  listAvailableTools,
} from './toolAvailability';

export interface ToolsSnapshot {
  /** Which tool is currently active (most recent activity). */
  activeTool: ToolName;
  /** Which tools are currently detected/enabled in this environment. */
  availableTools: ToolName[];
  /** The snapshot fires for ANY watcher change; tells UI which one changed. */
  changedTool: ToolName | null;
  copilot: CopilotStats;
  claude: ToolStats;
  codex: ToolStats;
}

/**
 * Minimum milliseconds by which a *new* activity must exceed the *current*
 * activeTool's last timestamp to trigger an auto-switch. Prevents flicker
 * when two tools write transcripts in rapid alternation.
 */
const AUTO_SWITCH_MIN_LEAD_MS = 500;

/**
 * How long (ms) a user's manual tab-click "pins" the active tool,
 * preventing auto-switch from overriding the selection.
 */
const PIN_DURATION_MS = 30_000;

/**
 * Interval (ms) for lightweight mtime-based poll fallback.
 * FileSystemWatcher can miss events for files outside the workspace;
 * this ensures updates arrive within a few seconds regardless.
 */
const POLL_INTERVAL_MS = 3_000;

export class ToolOrchestrator implements vscode.Disposable {
  private readonly _onDidChange = new vscode.EventEmitter<ToolsSnapshot>();
  readonly onDidChange: vscode.Event<ToolsSnapshot> = this._onDidChange.event;

  private _copilotStats: CopilotStats = emptyCopilotStats();
  private _claudeStats: ToolStats = emptyToolStats('claude');
  private _codexStats: ToolStats = emptyToolStats('codex');
  private _activeTool: ToolName = 'copilot';
  private _userPinned = false;
  private _pinExpiry = 0;
  private readonly _subs: vscode.Disposable[] = [];
  private _pollTimer: ReturnType<typeof setInterval> | null = null;

  constructor(
    private readonly _copilot: CopilotWatcher | undefined,
    private readonly _claude: ClaudeWatcher | undefined,
    private readonly _codex: CodexWatcher | undefined,
    private readonly _availability: ToolAvailability,
  ) {
    if (_copilot) {
      this._copilotStats = _copilot.stats;
      this._subs.push(_copilot.onDidChange((s) => { this._copilotStats = s; this._emit('copilot'); }));
    }
    if (_claude) {
      this._claudeStats = _claude.stats;
      this._subs.push(_claude.onDidChange((s) => { this._claudeStats = s; this._emit('claude'); }));
    }
    if (_codex) {
      this._codexStats = _codex.stats;
      this._subs.push(_codex.onDidChange((s) => { this._codexStats = s; this._emit('codex'); }));
    }

    // Pick an initial active tool from whichever has the most recent data.
    this._activeTool = this._computeActive(null);

    // Lightweight poll fallback — FileSystemWatcher can miss events for files
    // outside the workspace (e.g. ~/.codex/sessions, ~/.claude/projects).
    // Each watcher's pollCheck() only does fs.statSync and triggers incremental
    // load only when the file's mtime has actually changed.
    this._pollTimer = setInterval(() => this._poll(), POLL_INTERVAL_MS);
  }

  dispose(): void {
    if (this._pollTimer) { clearInterval(this._pollTimer); this._pollTimer = null; }
    for (const s of this._subs) { s.dispose(); }
    this._onDidChange.dispose();
  }

  /** Manually set the active tool (user clicked a tab). Pins for PIN_DURATION_MS. */
  setActiveTool(tool: ToolName): void {
    if (!isToolAvailable(this._availability, tool)) { return; }
    this._userPinned = true;
    this._pinExpiry = Date.now() + PIN_DURATION_MS;
    if (tool === this._activeTool) { return; }
    this._activeTool = tool;
    this._onDidChange.fire(this.snapshot(null));
  }

  /** Switch to a specific session within a tool's watcher. */
  selectSession(tool: ToolName, sessionId: string): void {
    // Validate sessionId: reject anything that could be used for path traversal
    if (!sessionId || /[/\\]|\.\./.test(sessionId)) { return; }
    this.setActiveTool(tool);
    if (tool === 'copilot' && this._copilot) { this._copilot.switchToSession(sessionId); }
    else if (tool === 'claude' && this._claude) { this._claude.switchToSession(sessionId); }
    else if (tool === 'codex' && this._codex) { this._codex.switchToSession(sessionId); }
  }

  snapshot(changedTool: ToolName | null): ToolsSnapshot {
    return {
      activeTool: this._activeTool,
      availableTools: listAvailableTools(this._availability),
      changedTool,
      copilot: this._copilotStats,
      claude: this._claudeStats,
      codex: this._codexStats,
    };
  }

  /** Latest snapshot (for external polling without subscribing to events). */
  get latestSnapshot(): ToolsSnapshot { return this.snapshot(null); }

  // ── Internals ─────────────────────────────────────────────────────

  private _lastTsFor(tool: ToolName): number {
    if (tool === 'copilot') { return this._copilotStats.lastMessageTs ?? 0; }
    if (tool === 'claude') { return this._claudeStats.lastMessageTs ?? 0; }
    return this._codexStats.lastMessageTs ?? 0;
  }

  private _hasAnyData(tool: ToolName): boolean {
    if (tool === 'copilot') { return (this._copilotStats.totalTokens ?? 0) > 0 || this._copilotStats.messageCount > 0; }
    if (tool === 'claude')  { return (this._claudeStats.totalTokens ?? 0) > 0 || this._claudeStats.messageCount > 0; }
    return (this._codexStats.totalTokens ?? 0) > 0 || this._codexStats.messageCount > 0;
  }

  private _computeActive(changedTool: ToolName | null): ToolName {
    const availableTools = listAvailableTools(this._availability);
    if (availableTools.length === 0) { return 'copilot'; }

    // Respect user's manual tab selection during the pin window
    if (this._userPinned) {
      if (Date.now() < this._pinExpiry && isToolAvailable(this._availability, this._activeTool)) {
        return this._activeTool;
      }
      this._userPinned = false;
    }

    const withData = availableTools.filter((t) => this._hasAnyData(t));
    if (withData.length === 0) {
      return isToolAvailable(this._availability, this._activeTool)
        ? this._activeTool
        : firstAvailableTool(this._availability);
    }

    // Pick tool with highest lastMessageTs.
    let best: ToolName = withData[0];
    let bestTs = this._lastTsFor(best);
    for (const t of withData) {
      const ts = this._lastTsFor(t);
      if (ts > bestTs) { best = t; bestTs = ts; }
    }

    // Apply hysteresis: only switch away from the current active tool if the
    // best candidate is at least AUTO_SWITCH_MIN_LEAD_MS newer.
    if (best === this._activeTool) { return best; }
    if (!isToolAvailable(this._availability, this._activeTool)) { return best; }
    const currentTs = this._lastTsFor(this._activeTool);
    // Timestamps are seconds → compare in ms.
    if ((bestTs - currentTs) * 1000 >= AUTO_SWITCH_MIN_LEAD_MS) {
      return best;
    }
    // If the active tool has no data yet, allow the switch regardless.
    if (!this._hasAnyData(this._activeTool)) { return best; }
    // Allow immediate switch if the change itself came from 'best'.
    if (changedTool === best && currentTs === 0) { return best; }
    return this._activeTool;
  }

  private _emit(changedTool: ToolName): void {
    this._activeTool = this._computeActive(changedTool);
    this._onDidChange.fire(this.snapshot(changedTool));
  }

  /** Lightweight poll — calls each watcher's pollCheck() which only does
   *  fs.statSync and triggers incremental reads when mtime changes. */
  private _poll(): void {
    this._copilot?.pollCheck();
    this._claude?.pollCheck();
    this._codex?.pollCheck();
  }
}
