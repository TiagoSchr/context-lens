/**
 * sidebarProvider.ts — WebviewViewProvider that renders the full Context Lens
 * dashboard inside the Activity Bar sidebar.
 *
 * All UI is generated in TypeScript as a template literal so there are no
 * external HTML files to manage. The webview uses VS Code's CSS custom
 * properties for automatic light/dark theme support.
 */
import * as vscode from 'vscode';
import * as crypto from 'crypto';
import { LensWatcher } from './lensWatcher';
import { CopilotWatcher } from './copilotWatcher';
import { fmtK, fmtTime } from './logParser';
import { ToolOrchestrator } from './toolOrchestrator';
import { ToolName } from './toolStats';

export class LensSidebarProvider implements vscode.WebviewViewProvider, vscode.Disposable {
  private _view: vscode.WebviewView | undefined;
  private readonly _sub: vscode.Disposable;
  private readonly _orchestratorSub: vscode.Disposable | undefined;

  constructor(
    private readonly _ctx: vscode.ExtensionContext,
    private readonly _watcher: LensWatcher,
    private readonly _copilotWatcher?: CopilotWatcher,
    private readonly _orchestrator?: ToolOrchestrator,
  ) {
    // Push fresh stats to the webview whenever .ctx data changes
    this._sub = _watcher.onDidChange((stats) => {
      if (this._view) {
        this._view.webview.postMessage({ type: 'update', data: stats });
      }
    });

    // Push unified per-tool stats when any watcher fires
    if (_orchestrator) {
      this._orchestratorSub = _orchestrator.onDidChange((snap) => {
        if (this._view) {
          this._view.webview.postMessage({ type: 'toolsUpdate', data: snap });
        }
      });
    }
  }

  dispose(): void {
    this._sub.dispose();
    this._orchestratorSub?.dispose();
  }

  resolveWebviewView(
    webviewView: vscode.WebviewView,
    _context: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken,
  ): void {
    this._view = webviewView;

    webviewView.webview.options = {
      enableScripts: true,
    };

    webviewView.webview.html = this._buildHtml(webviewView.webview);

    // Handle messages from webview → extension
    webviewView.webview.onDidReceiveMessage((msg: { type: string; tool?: ToolName; sessionId?: string }) => {
      switch (msg.type) {
        case 'toggle':     this._watcher.toggle(); break;
        case 'refresh':    this._watcher.refresh(); break;
        case 'runIndex':   this._watcher.runLensIndex(); break;
        case 'openConfig': this._watcher.openConfig(); break;
        case 'selectTool':
          if (msg.tool && this._orchestrator) { this._orchestrator.setActiveTool(msg.tool); }
          break;
        case 'selectSession':
          if (msg.tool && msg.sessionId && this._orchestrator) {
            this._orchestrator.selectSession(msg.tool, msg.sessionId);
          }
          break;
      }
    });

    // Send initial data immediately
    webviewView.webview.postMessage({ type: 'update', data: this._watcher.stats });
    if (this._orchestrator) {
      webviewView.webview.postMessage({ type: 'toolsUpdate', data: this._orchestrator.snapshot(null) });
    } else if (this._copilotWatcher) {
      // Legacy path: no orchestrator → still send copilotUpdate for backwards compat
      webviewView.webview.postMessage({ type: 'copilotUpdate', data: this._copilotWatcher.stats });
    }
  }

  // ── HTML generation ───────────────────────────────────────────────────────

  private _buildHtml(webview: vscode.Webview): string {
    // Content Security Policy nonce — prevents XSS
    const nonce = crypto.randomBytes(16).toString('hex');

    return /* html */`<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Content-Security-Policy"
      content="default-src 'none'; script-src 'nonce-${nonce}'; style-src 'unsafe-inline';">
<title>Context Lens</title>
<style>
  :root {
    --spacing: 6px;
    --radius: 5px;
    --green:  #4caf82;
    --yellow: #e5c07b;
    --red:    #e06c75;
    --blue:   #61afef;
    --dim:    var(--vscode-descriptionForeground);
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: var(--vscode-font-family);
    font-size: var(--vscode-font-size);
    color: var(--vscode-foreground);
    background: var(--vscode-sideBar-background);
    padding: var(--spacing);
    user-select: none;
    overflow-x: hidden;
  }

  /* ── Header ── */
  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
    gap: 6px;
  }
  .header-title {
    font-weight: 600;
    font-size: 1.05em;
    letter-spacing: 0.02em;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .header-title svg { flex-shrink: 0; }

  /* ── Toggle switch ── */
  .toggle-wrap {
    display: flex;
    align-items: center;
    gap: 6px;
    cursor: pointer;
  }
  .toggle-label {
    font-size: 0.82em;
    color: var(--dim);
    white-space: nowrap;
  }
  .toggle {
    position: relative;
    width: 34px;
    height: 18px;
    flex-shrink: 0;
  }
  .toggle input { opacity: 0; width: 0; height: 0; }
  .slider {
    position: absolute; inset: 0;
    background: var(--vscode-input-border);
    border-radius: 18px;
    transition: 0.2s;
    cursor: pointer;
  }
  .slider::before {
    content: '';
    position: absolute;
    width: 12px; height: 12px;
    left: 3px; bottom: 3px;
    background: var(--vscode-foreground);
    border-radius: 50%;
    transition: 0.2s;
  }
  input:checked + .slider { background: var(--green); }
  input:checked + .slider::before { transform: translateX(16px); }

  /* ── No-index banner ── */
  .no-index {
    background: var(--vscode-inputValidation-warningBackground);
    border: 1px solid var(--vscode-inputValidation-warningBorder);
    border-radius: var(--radius);
    padding: 10px 12px;
    font-size: 0.85em;
    line-height: 1.5;
    margin-bottom: 6px;
  }
  .no-index code {
    font-family: var(--vscode-editor-font-family, monospace);
    background: var(--vscode-textCodeBlock-background);
    padding: 1px 4px;
    border-radius: 3px;
  }

  /* ── Stat cards ── */
  .cards {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px;
    margin-bottom: 8px;
  }
  .card {
    background: var(--vscode-editor-background);
    border: 1px solid var(--vscode-widget-border, #333);
    border-radius: 6px;
    padding: 8px 10px;
    transition: border-color 0.15s;
  }
  .card:hover {
    border-color: color-mix(in srgb, var(--vscode-foreground) 25%, transparent);
  }
  .card-label {
    font-size: 0.7em;
    color: var(--dim);
    letter-spacing: 0.05em;
    text-transform: uppercase;
    margin-bottom: 4px;
    font-weight: 500;
  }
  .card-value {
    font-size: 1.35em;
    font-weight: 700;
    line-height: 1.1;
    letter-spacing: -0.01em;
  }
  .card-sub {
    font-size: 0.68em;
    color: var(--dim);
    margin-top: 3px;
  }
  .card-value.green { color: var(--green); }
  .card-value.blue  { color: var(--blue); }
  .card-value.yellow{ color: var(--yellow); }

  /* ── Collapsible sections ── */
  .sec-hdr {
    font-size: 0.7em;
    font-weight: 600;
    color: var(--dim);
    letter-spacing: 0.06em;
    text-transform: uppercase;
    margin: 8px 0 4px;
    cursor: pointer;
    user-select: none;
    transition: color 0.15s;
  }
  .sec-hdr:hover { color: var(--vscode-foreground); }
  .sec-hdr::before { content: '\\25BE '; }
  .sec-hdr.shut::before { content: '\\25B8 '; }
  .sec-body {
    overflow: hidden;
    transition: max-height 0.2s ease;
  }
  .sec-body.shut { max-height: 0 !important; }



  /* ── Action buttons ── */
  .actions {
    display: flex;
    gap: 6px;
    margin-top: 8px;
    margin-bottom: 4px;
  }
  .btn {
    flex: 1;
    min-width: 0;
    padding: 6px 8px;
    font-size: 0.75em;
    font-weight: 500;
    font-family: inherit;
    background: var(--vscode-button-secondaryBackground, #3a3d41);
    color: var(--vscode-button-secondaryForeground, #ccc);
    border: 1px solid var(--vscode-widget-border, #444);
    border-radius: 6px;
    cursor: pointer;
    text-align: center;
    transition: background 0.15s, border-color 0.15s, transform 0.1s;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    line-height: 1.2;
  }
  .btn:hover {
    background: var(--vscode-button-secondaryHoverBackground, #494d51);
    border-color: color-mix(in srgb, var(--vscode-foreground) 30%, transparent);
  }
  .btn:active {
    transform: scale(0.97);
  }
  .btn.pri {
    background: var(--vscode-button-background);
    color: var(--vscode-button-foreground);
    border-color: transparent;
    font-weight: 600;
  }
  .btn.pri:hover {
    background: var(--vscode-button-hoverBackground);
  }

  /* ── Disabled overlay ── */
  .disabled-banner {
    background: var(--vscode-inputValidation-warningBackground);
    border: 1px solid var(--vscode-inputValidation-warningBorder);
    border-radius: var(--radius);
    padding: 8px 12px;
    font-size: 0.82em;
    margin-bottom: 6px;
    text-align: center;
  }

  .dimmed { opacity: 0.4; pointer-events: none; }

  /* ── Tool tabs (CHAT / CLAUDE / CODEX) ── */
  .tabs {
    display: flex;
    gap: 0;
    margin-bottom: 8px;
    border-bottom: 1px solid var(--vscode-widget-border, #333);
  }
  .tab {
    flex: 1;
    padding: 7px 6px 6px;
    font-size: 0.72em;
    font-weight: 600;
    text-align: center;
    cursor: pointer;
    color: var(--dim);
    border-bottom: 2px solid transparent;
    transition: color 0.15s, border-color 0.15s, background 0.15s;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    letter-spacing: 0.04em;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 5px;
  }
  .tab:hover {
    color: var(--vscode-foreground);
    background: color-mix(in srgb, var(--vscode-foreground) 5%, transparent);
  }
  .tab.active {
    color: var(--vscode-foreground);
    border-bottom-color: var(--green);
  }
  .tab .tab-badge {
    font-size: 0.82em;
    font-weight: 500;
    color: var(--dim);
    background: color-mix(in srgb, var(--vscode-foreground) 8%, transparent);
    padding: 1px 5px;
    border-radius: 8px;
    line-height: 1.3;
  }
  .tab.active .tab-badge {
    color: var(--green);
    background: color-mix(in srgb, var(--green) 12%, transparent);
  }
  .tab .tab-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--green);
    display: none;
  }
  .tab.live .tab-dot { display: inline-block; animation: pulse 1.2s infinite; }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.35; }
  }

  /* ── Copilot session section ── */
  .copilot-section {
    margin-top: 8px;
    border-top: 1px solid var(--vscode-widget-border, #333);
    padding-top: 8px;
  }
  .copilot-header {
    font-size: 0.72em;
    font-weight: 600;
    color: var(--dim);
    letter-spacing: 0.05em;
    text-transform: uppercase;
    margin-bottom: 6px;
    display: flex;
    align-items: center;
    gap: 5px;
    cursor: pointer;
    user-select: none;
  }
  .copilot-header::before { content: '\\25BE '; }
  .copilot-header.shut::before { content: '\\25B8 '; }
  .copilot-body.shut { max-height: 0 !important; overflow: hidden; }
  .copilot-big {
    font-size: 1.3em;
    font-weight: 700;
    color: var(--blue);
    letter-spacing: -0.01em;
  }
  .copilot-sub {
    font-size: 0.65em;
    color: var(--dim);
    margin-top: 1px;
  }
  .copilot-breakdown {
    display: flex;
    gap: 4px;
    margin-top: 6px;
    background: var(--vscode-editor-background);
    border: 1px solid var(--vscode-widget-border, #333);
    border-radius: 6px;
    padding: 6px 4px;
  }
  .copilot-stat {
    flex: 1;
    text-align: center;
    position: relative;
  }
  .copilot-stat + .copilot-stat::before {
    content: '';
    position: absolute;
    left: 0;
    top: 15%;
    height: 70%;
    width: 1px;
    background: var(--vscode-widget-border, #333);
  }
  .copilot-stat-val {
    font-size: 0.92em;
    font-weight: 700;
    line-height: 1.2;
  }
  .copilot-stat-lbl {
    font-size: 0.62em;
    color: var(--dim);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-top: 2px;
  }
  .copilot-meta {
    font-size: 0.65em;
    color: var(--dim);
    margin-top: 5px;
  }
  .copilot-est {
    font-size: 0.58em;
    color: var(--dim);
    font-style: italic;
    margin-top: 4px;
    opacity: 0.7;
  }
  .copilot-body .sec { margin-top: 6px; }
  .copilot-body .sec-hdr { margin: 4px 0 3px; }

  /* ── Per-tool economy cards ── */
  .tool-card {
    background: var(--vscode-editor-background);
    border: 1px solid var(--vscode-widget-border, #333);
    border-radius: 6px;
    padding: 8px 10px;
    margin-bottom: 4px;
    display: flex;
    align-items: center;
    gap: 8px;
    transition: border-color 0.15s;
  }
  .tool-card:hover {
    border-color: color-mix(in srgb, var(--vscode-foreground) 25%, transparent);
  }
  .tool-icon {
    font-size: 1.1em;
    flex-shrink: 0;
    width: 18px;
    text-align: center;
  }
  .tool-info {
    flex: 1;
    min-width: 0;
  }
  .tool-name {
    font-size: 0.78em;
    font-weight: 600;
    text-transform: capitalize;
  }
  .tool-detail {
    font-size: 0.65em;
    color: var(--dim);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    margin-top: 1px;
  }
  .tool-stats {
    text-align: right;
    flex-shrink: 0;
  }
  .tool-saved {
    font-size: 0.88em;
    font-weight: 700;
    color: var(--green);
  }
  .tool-pct {
    font-size: 0.65em;
    color: var(--dim);
    margin-top: 1px;
  }

  /* ── Session rows ── */
  .session-row {
    transition: background 0.12s;
    border-radius: 3px;
    padding: 3px 4px !important;
    margin: 0 -4px;
  }
  .session-row:hover {
    background: color-mix(in srgb, var(--vscode-foreground) 6%, transparent);
  }
</style>
</head>
<body>

<!-- ── Tool tabs (CHAT / CLAUDE CODE / CODEX) ────────────────── -->
<div class="tabs" id="tool-tabs">
  <div class="tab active" data-tool="copilot" id="tab-copilot">
    <span class="tab-dot"></span>
    <span>CHAT</span>
    <span class="tab-badge" id="tab-badge-copilot">—</span>
  </div>
  <div class="tab" data-tool="claude" id="tab-claude">
    <span class="tab-dot"></span>
    <span>CLAUDE</span>
    <span class="tab-badge" id="tab-badge-claude">—</span>
  </div>
  <div class="tab" data-tool="codex" id="tab-codex">
    <span class="tab-dot"></span>
    <span>CODEX</span>
    <span class="tab-badge" id="tab-badge-codex">—</span>
  </div>
</div>

<!-- ── Header ────────────────────────────────────────────────────── -->
<div class="header">
  <div class="header-title">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="11" cy="11" r="8"/>
      <line x1="21" y1="21" x2="16.65" y2="16.65"/>
      <line x1="8" y1="11" x2="14" y2="11"/>
      <line x1="11" y1="8" x2="11" y2="14"/>
    </svg>
    Context Lens
  </div>

  <div id="last-query-ts" style="font-size:0.62em;color:var(--dim);white-space:nowrap;flex-shrink:0"></div>
  <div class="toggle-wrap" id="toggle-wrap" title="Toggle optimization on/off">
    <span class="toggle-label" id="toggle-label">ON</span>
    <label class="toggle" id="toggle-label-el">
      <input type="checkbox" id="enabled-toggle" checked>
      <span class="slider"></span>
    </label>
  </div>
</div>

<!-- ── No index banner ───────────────────────────────────────────── -->
<div class="no-index" id="no-index-banner" style="display:none">
  No index found. Run: <code>lens index</code>
  <div style="margin-top:4px">
    <button class="btn pri" id="btn-idx-b">&#9654; Run lens index</button>
  </div>
</div>

<!-- ── Disabled banner ───────────────────────────────────────────── -->
<div class="disabled-banner" id="disabled-banner" style="display:none">
  Optimization is <strong>disabled</strong>. AI queries use full context.
</div>

<!-- ── Main content ──────────────────────────────────────────────── -->
<div id="main-content">
  <div class="cards">
    <div class="card">
      <div class="card-label">Tokens Saved</div>
      <div class="card-value green" id="total-saved">&mdash;</div>
      <div class="card-sub" id="total-saved-sub">all time</div>
    </div>
    <div class="card">
      <div class="card-label">Avg Saving</div>
      <div class="card-value blue" id="avg-pct">&mdash;</div>
      <div class="card-sub" id="total-queries">0 queries</div>
    </div>
    <div class="card">
      <div class="card-label">This Session</div>
      <div class="card-value green" id="sess-saved">&mdash;</div>
      <div class="card-sub" id="sess-queries">0 queries</div>
      <div class="card-sub" id="sess-name" style="font-style:italic;margin-top:1px"></div>
    </div>
    <div class="card">
      <div class="card-label">Budget</div>
      <div class="card-value yellow" id="budget">&mdash;</div>
      <div class="card-sub">tokens / query</div>
    </div>
  </div>

  <!-- ── Per-tool economy (always visible) ──────────────────────── -->
  <div id="tool-breakdown" style="margin-bottom:6px"></div>

  <!-- ── Actions (compact) ──────────────────────────────────────── -->
  <div class="actions">
    <button class="btn pri" id="btn-re">&#x27F3; Re-index</button>
    <button class="btn" id="btn-rf">&#x21BA; Refresh</button>
    <button class="btn" id="btn-cfg">&#9881; Config</button>
  </div>
</div>

<!-- ── Copilot Session (outside main-content — always visible) ──── -->
<div class="copilot-section" id="copilot-section" style="display:none">
  <div class="copilot-header" id="copilot-hdr">
    &#x1F4AC; Copilot Chat Session
    <span id="copilot-live" style="font-size:0.9em;color:var(--dim);margin-left:auto"></span>
  </div>
  <div class="copilot-body" id="copilot-body">
    <div id="copilot-session-name" style="font-size:0.75em;font-weight:600;color:var(--vscode-foreground);margin-bottom:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title=""></div>
    <div style="display:flex;align-items:baseline;gap:6px;margin-bottom:3px">
      <div class="copilot-big" id="copilot-total">0</div>
      <div class="copilot-sub" id="copilot-sub-label">tokens ~estimated</div>
    </div>
    <div class="copilot-breakdown">
      <div class="copilot-stat">
        <div class="copilot-stat-val" id="copilot-input" style="color:var(--blue)">0</div>
        <div class="copilot-stat-lbl" id="copilot-lbl-input">Input</div>
      </div>
      <div class="copilot-stat">
        <div class="copilot-stat-val" id="copilot-output" style="color:var(--green)">0</div>
        <div class="copilot-stat-lbl" id="copilot-lbl-output">Output</div>
      </div>
      <div class="copilot-stat">
        <div class="copilot-stat-val" id="copilot-reasoning" style="color:var(--yellow)">0</div>
        <div class="copilot-stat-lbl" id="copilot-lbl-reasoning">Thinking</div>
      </div>
    </div>
    <div class="copilot-meta" id="copilot-meta"></div>

    <!-- Session history -->
    <div class="sec" style="margin-top:4px">
      <div class="sec-hdr shut" id="copilot-hist-hdr" data-sec="copilot-hist">Sessions</div>
      <div class="sec-body shut" id="sec-copilot-hist">
        <div id="copilot-sessions" style="font-size:0.68em;color:var(--dim)">No other sessions</div>
      </div>
    </div>

    <div class="copilot-est" id="copilot-est">~ estimates based on transcript content (system prompt &amp; re-sent history not included)</div>
  </div>
</div>

<script nonce="${nonce}">
  const vscode = acquireVsCodeApi();

  // ── Message handlers ────────────────────────────────────────────
  window.addEventListener('message', (event) => {
    const { type, data } = event.data;
    if (type === 'update') { render(data); }
    if (type === 'copilotUpdate') { renderCopilot(data); }
    if (type === 'toolsUpdate') { handleToolsUpdate(data); }
  });

  // ── Tool tabs ───────────────────────────────────────────────────
  var _lastLensStats = null;
  var _lastToolsSnapshot = null;
  var _userSelectedTool = null;
  var _userSelectedExpiry = 0;

  function getAvailableToolsFromSnapshot(snap) {
    if (snap && Array.isArray(snap.availableTools) && snap.availableTools.length > 0) {
      return snap.availableTools.slice();
    }
    return ['copilot', 'claude', 'codex'].filter(function(tool) {
      var stat = snap && snap[tool];
      return !!stat && (((stat.totalTokens || 0) > 0) || ((stat.messageCount || 0) > 0));
    });
  }

  function getAvailableTools() {
    return getAvailableToolsFromSnapshot(_lastToolsSnapshot);
  }

  function syncToolTabs(snap) {
    var available = getAvailableToolsFromSnapshot(snap);
    var tabsWrap = document.getElementById('tool-tabs');
    if (tabsWrap) {
      tabsWrap.style.display = available.length > 0 ? '' : 'none';
    }

    var allTools = ['copilot', 'claude', 'codex'];
    for (var i = 0; i < allTools.length; i++) {
      var tool = allTools[i];
      var tab = document.getElementById('tab-' + tool);
      if (!tab) { continue; }
      tab.style.display = available.indexOf(tool) >= 0 ? '' : 'none';
    }

    if (_userSelectedTool && available.indexOf(_userSelectedTool) < 0) {
      _userSelectedTool = null;
      _userSelectedExpiry = 0;
    }
  }

  function handleToolsUpdate(snap) {
    _lastToolsSnapshot = snap;

    // Update badges on every tab
    updateTabBadge('copilot', snap.copilot);
    updateTabBadge('claude',  snap.claude);
    updateTabBadge('codex',   snap.codex);

    syncToolTabs(snap);
    var availableTools = getAvailableToolsFromSnapshot(snap);

    // Auto-switch highlight — respect user's manual tab click
    if (_userSelectedTool && Date.now() < _userSelectedExpiry && availableTools.indexOf(_userSelectedTool) >= 0) {
      snap.activeTool = _userSelectedTool;
    } else {
      _userSelectedTool = null;
    }
    if (availableTools.length > 0 && availableTools.indexOf(snap.activeTool) < 0) {
      snap.activeTool = availableTools[0];
    }
    setActiveTab(snap.activeTool);

    // Cache Copilot data so renderCopilot / renderToolSession can reach it
    if (snap.copilot) { _lastCopilotData = snap.copilot; }

    // Mark which tabs are "live" (recent activity within ~2 min)
    markLiveTabs(snap);

    // Render the body for the active tool
    renderActiveTool();
    if (_lastLensStats) {
      renderEconomyCards(_lastLensStats);
      renderTools(_lastLensStats.byTool);
    }
  }

  function updateTabBadge(tool, s) {
    var el = document.getElementById('tab-badge-' + tool);
    if (!el) { return; }
    if (!s || !s.totalTokens) { el.textContent = '—'; return; }
    el.textContent = fmtK(s.totalTokens);
  }

  function setActiveTab(tool) {
    var tabs = document.querySelectorAll('.tab');
    for (var i = 0; i < tabs.length; i++) {
      var t = tabs[i];
      if (t.getAttribute('data-tool') === tool) { t.classList.add('active'); }
      else { t.classList.remove('active'); }
    }
  }

  function markLiveTabs(snap) {
    var nowSec = Date.now() / 1000;
    var tools = ['copilot', 'claude', 'codex'];
    for (var i = 0; i < tools.length; i++) {
      var t = tools[i];
      var s = snap[t];
      var ts = s && (s.lastMessageTs || 0);
      var tab = document.getElementById('tab-' + t);
      if (!tab) { continue; }
      if (ts && (nowSec - ts) < 120) { tab.classList.add('live'); }
      else { tab.classList.remove('live'); }
    }
  }

  function getPreferredActiveTool(s) {
    var available = getAvailableTools();
    if (_lastToolsSnapshot && _lastToolsSnapshot.activeTool && available.indexOf(_lastToolsSnapshot.activeTool) >= 0) {
      return _lastToolsSnapshot.activeTool;
    }
    if (s && s.activeTool && available.indexOf(s.activeTool) >= 0) {
      return s.activeTool;
    }
    return available[0] || 'unknown';
  }

  function getToolSnapshot(tool) {
    if (!_lastToolsSnapshot) { return null; }
    return _lastToolsSnapshot[tool] || null;
  }

  function getToolBudget(s, tool) {
    var budgets = (s && s.targetBudgets) || {};
    var toolBudget = budgets ? budgets[tool] : null;
    return typeof toolBudget === 'number' && toolBudget > 0
      ? toolBudget
      : ((s && s.tokenBudget) || 0);
  }

  function getToolSessionName(s, tool) {
    var toolSnap = getToolSnapshot(tool);
    if (toolSnap && toolSnap.sessionName) {
      return toolSnap.sessionName;
    }
    if (tool === 'copilot' && _lastCopilotData && _lastCopilotData.sessionName) {
      return _lastCopilotData.sessionName;
    }
    if (s && s.activeTool === tool && s.sessionName) {
      return s.sessionName;
    }
    return '';
  }

  function emptyEconomy() {
    return { queries: 0, tokensUsed: 0, tokensRaw: 0, tokensSaved: 0, savingPct: 0 };
  }

  function getToolSessionEconomy(s, tool) {
    if (s && s.sessionByTool && s.sessionByTool[tool]) {
      var stat = s.sessionByTool[tool];
      return {
        queries: stat.count || 0,
        tokensUsed: stat.totalUsed || 0,
        tokensRaw: stat.totalRaw || 0,
        tokensSaved: stat.totalSaved || 0,
        savingPct: stat.avgPct || 0,
      };
    }
    if (s && tool === s.activeTool) {
      return {
        queries: s.activeToolSessionQueries || 0,
        tokensUsed: s.activeToolSessionTokensUsed || 0,
        tokensRaw: s.activeToolSessionTokensRaw || 0,
        tokensSaved: s.activeToolSessionTokensSaved || 0,
        savingPct: s.activeToolSessionSavingPct || 0,
      };
    }
    return emptyEconomy();
  }

  function hasToolTranscriptActivity(tool) {
    var toolSnap = getToolSnapshot(tool);
    if (!toolSnap) { return false; }
    return (toolSnap.totalTokens || 0) > 0 || (toolSnap.messageCount || 0) > 0;
  }

  function renderEconomyCards(s) {
    var selectedTool = getPreferredActiveTool(s);
    var sessionEconomy = getToolSessionEconomy(s, selectedTool);
    var sessionName = getToolSessionName(s, selectedTool);
    var toolSnap = getToolSnapshot(selectedTool);
    var hasTranscript = hasToolTranscriptActivity(selectedTool);

    document.getElementById('total-saved').textContent = fmtK(s.totalTokensSaved);
    document.getElementById('total-saved-sub').textContent =
      s.totalTokensRaw > 0 ? 'of ' + fmtK(s.totalTokensRaw) + ' raw' : 'all time';
    document.getElementById('avg-pct').textContent =
      s.totalQueries > 0 ? s.avgSavingPct.toFixed(0) + '%' : '\u2014';
    document.getElementById('total-queries').textContent =
      s.totalQueries + ' quer' + (s.totalQueries === 1 ? 'y' : 'ies');

    // "This Session" card — show lens savings if available, otherwise transcript total
    if (sessionEconomy.queries > 0) {
      // Has lens queries: show lens savings
      document.getElementById('sess-saved').textContent = fmtK(sessionEconomy.tokensSaved);
      document.getElementById('sess-queries').textContent =
        sessionEconomy.queries + ' quer' + (sessionEconomy.queries === 1 ? 'y' : 'ies');
    } else if (hasTranscript && toolSnap && toolSnap.totalTokens > 0) {
      // No lens queries but has transcript: show transcript session total
      document.getElementById('sess-saved').textContent = fmtK(toolSnap.totalTokens);
      document.getElementById('sess-queries').textContent = 'transcript tokens';
    } else {
      document.getElementById('sess-saved').textContent = '0';
      document.getElementById('sess-queries').textContent = '0 queries';
    }

    document.getElementById('sess-name').textContent = sessionName;
    document.getElementById('sess-name').title = sessionName;
    document.getElementById('budget').textContent = fmtK(getToolBudget(s, selectedTool));
  }

  function renderActiveTool() {
    if (!_lastToolsSnapshot) { return; }
    var availableTools = getAvailableTools();
    if (availableTools.length === 0) {
      var section = document.getElementById('copilot-section');
      if (section) { section.style.display = 'none'; }
      return;
    }
    var activeTool = _lastToolsSnapshot.activeTool || availableTools[0];
    if (availableTools.indexOf(activeTool) < 0) {
      activeTool = availableTools[0];
    }
    _lastActiveTool = activeTool;

    if (activeTool === 'copilot') {
      // Reset labels to Copilot defaults (in case they were overwritten)
      resetSessionLabelsToCopilot();
      renderCopilot(_lastToolsSnapshot.copilot);
    } else {
      // Render Claude / Codex using the generic tool-stats card
      renderToolStatsCard(_lastToolsSnapshot[activeTool], activeTool);
    }
  }

  function resetSessionLabelsToCopilot() {
    // Restore header to Copilot default (renderToolStatsCard overwrites it)
    var hdrEl = document.getElementById('copilot-hdr');
    if (hdrEl) {
      var wasShut = hdrEl.classList.contains('shut');
      hdrEl.innerHTML = '\uD83D\uDCAC Copilot Chat Session'
        + '<span id="copilot-live" style="font-size:0.9em;color:var(--dim);margin-left:auto"></span>';
      if (wasShut) { hdrEl.classList.add('shut'); }
    }
    var labels = [
      ['copilot-sub-label', 'tokens ~estimated'],
      ['copilot-lbl-input', 'Input'],
      ['copilot-lbl-output', 'Output'],
      ['copilot-lbl-reasoning', 'Thinking'],
    ];
    for (var i = 0; i < labels.length; i++) {
      var el = document.getElementById(labels[i][0]);
      if (el) { el.textContent = labels[i][1]; }
    }
    var inp = document.getElementById('copilot-input');
    if (inp) { inp.style.color = 'var(--blue)'; }
    var est = document.getElementById('copilot-est');
    if (est) {
      est.textContent = '~ estimates based on transcript content (system prompt & re-sent history not included)';
    }
    var hh = document.getElementById('copilot-hist-hdr');
    var sh = document.getElementById('sec-copilot-hist');
    if (hh) { hh.style.display = ''; }
    if (sh) { sh.style.display = ''; }
  }

  /** Render card for Claude / Codex ToolStats. */
  function renderToolStatsCard(s, tool) {
    var section = document.getElementById('copilot-section');
    if (!section) { return; }
    section.style.display = '';

    var toolMeta = TOOL_META[tool] || TOOL_META['unknown'];
    var hdrEl = document.getElementById('copilot-hdr');
    if (hdrEl) {
      var wasShut = hdrEl.classList.contains('shut');
      hdrEl.innerHTML = toolMeta.icon + ' ' + escHtml(toolMeta.label) + ' Session'
        + '<span id="copilot-live" style="font-size:0.9em;color:var(--dim);margin-left:auto"></span>';
      if (wasShut) { hdrEl.classList.add('shut'); }
    }

    var nameEl = document.getElementById('copilot-session-name');
    if (s && s.sessionName) {
      nameEl.textContent = '\u25B6 ' + s.sessionName;
      nameEl.title = s.sessionName;
      nameEl.style.display = '';
    } else {
      nameEl.style.display = 'none';
    }

    if (!s || s.totalTokens === 0) {
      document.getElementById('copilot-total').textContent = '0';
      document.getElementById('copilot-sub-label').textContent =
        s && s.tokensFromUsage ? 'tokens (real usage)' : 'tokens ~estimated';
      document.getElementById('copilot-input').textContent = '0';
      document.getElementById('copilot-output').textContent = '0';
      document.getElementById('copilot-reasoning').textContent = '0';
      document.getElementById('copilot-meta').textContent =
        'No activity yet in this workspace';
      document.getElementById('copilot-est').textContent = tool === 'claude'
        ? '~ real token counts from Claude Code usage reports'
        : tool === 'codex'
          ? '~ cumulative token_count events from Codex rollout'
          : '';
      // Still render sessions even when no token data yet
      var hh0 = document.getElementById('copilot-hist-hdr');
      var sh0 = document.getElementById('sec-copilot-hist');
      if (hh0) { hh0.style.display = ''; }
      if (sh0) { sh0.style.display = ''; }
      renderCopilotSessions(s ? (s.allSessions || []) : []);
      return;
    }

    document.getElementById('copilot-total').textContent = fmtK(s.totalTokens);
    document.getElementById('copilot-sub-label').textContent =
      s.tokensFromUsage ? 'tokens (real usage)' : 'tokens ~estimated';

    document.getElementById('copilot-input').textContent = fmtK(s.inputTokens);
    document.getElementById('copilot-input').style.color = toolMeta.color || 'var(--blue)';
    document.getElementById('copilot-lbl-input').textContent = 'Input';

    document.getElementById('copilot-output').textContent = fmtK(s.outputTokens);
    document.getElementById('copilot-output').style.color = 'var(--green)';
    document.getElementById('copilot-lbl-output').textContent = 'Output';

    document.getElementById('copilot-reasoning').textContent = fmtK(s.reasoningTokens);
    document.getElementById('copilot-reasoning').style.color = 'var(--yellow)';
    document.getElementById('copilot-lbl-reasoning').textContent =
      tool === 'claude' ? 'Cache' : 'Reasoning';

    _lastCopilotTs = s.lastMessageTs || null;
    var liveEl = document.getElementById('copilot-live');
    if (liveEl) {
      liveEl.textContent = s.lastMessageTs ? '\u26A1 ' + timeAgo(s.lastMessageTs) : '';
    }

    var meta = s.messageCount + ' msg' + (s.messageCount !== 1 ? 's' : '');
    if (s.toolCallCount > 0) {
      meta += ' \u00B7 ' + s.toolCallCount + ' tool call' + (s.toolCallCount !== 1 ? 's' : '');
    }
    if (s.model) { meta += ' \u00B7 ' + escHtml(s.model); }
    document.getElementById('copilot-meta').textContent = meta;

    document.getElementById('copilot-est').textContent = tool === 'claude'
      ? '~ real token counts from Claude Code usage reports'
      : '~ cumulative token_count events from Codex rollout';

    // Ensure session history section is visible for all tools
    var hh1 = document.getElementById('copilot-hist-hdr');
    var sh1 = document.getElementById('sec-copilot-hist');
    if (hh1) { hh1.style.display = ''; }
    if (sh1) { sh1.style.display = ''; }

    // Render session history for this tool
    renderCopilotSessions(s.allSessions || []);
  }

  // ── Tab click handlers ─────────────────────────────────────────
  (function wireTabs() {
    var tabs = document.querySelectorAll('.tab');
    for (var i = 0; i < tabs.length; i++) {
      tabs[i].addEventListener('click', function(e) {
        var tool = this.getAttribute('data-tool');
        if (tool) {
          _userSelectedTool = tool;
          _userSelectedExpiry = Date.now() + 30000;
          vscode.postMessage({ type: 'selectTool', tool: tool });
        }
      });
    }
  })();

  // ── Collapsible sections ────────────────────────────────────────
  document.querySelectorAll('.sec-hdr').forEach(function(hdr) {
    hdr.addEventListener('click', function() {
      var id = 'sec-' + hdr.getAttribute('data-sec');
      document.getElementById(id).classList.toggle('shut');
      hdr.classList.toggle('shut');
    });
  });

  // Copilot section collapse toggle
  document.getElementById('copilot-hdr').addEventListener('click', function() {
    document.getElementById('copilot-body').classList.toggle('shut');
    this.classList.toggle('shut');
  });

  // ── Wire up buttons via addEventListener (CSP blocks inline onclick) ─
  // Use the checkbox change event instead of the wrapper div click.
  // A div-level click listener double-fires because <label> wrapping a checkbox
  // produces a synthetic click that bubbles back to the div.
  document.getElementById('enabled-toggle').addEventListener('change', function(e) {
    e.stopPropagation();
    vscode.postMessage({ type: 'toggle' });
  });
  document.getElementById('btn-idx-b').addEventListener('click', function() {
    vscode.postMessage({ type: 'runIndex' });
  });
  document.getElementById('btn-re').addEventListener('click', function() {
    vscode.postMessage({ type: 'runIndex' });
  });
  document.getElementById('btn-rf').addEventListener('click', function() {
    vscode.postMessage({ type: 'refresh' });
  });
  document.getElementById('btn-cfg').addEventListener('click', function() {
    vscode.postMessage({ type: 'openConfig' });
  });

  // ── Render ──────────────────────────────────────────────────────
  function render(s) {
    _lastLensStats = s;

    // Last query live indicator
    var lqEl = document.getElementById('last-query-ts');
    _lastTs = s.lastQueryTs || null;
    if (s.lastQueryTs) {
      lqEl.textContent = '\u26A1 ' + timeAgo(s.lastQueryTs);
      lqEl.title = 'Last query: ' + fmtTime(s.lastQueryTs);
    } else {
      lqEl.textContent = '';
    }

    // Toggle switch
    var chk = document.getElementById('enabled-toggle');
    chk.checked = s.enabled !== false;
    document.getElementById('toggle-label').textContent = s.enabled !== false ? 'ON' : 'OFF';

    // No-index banner
    document.getElementById('no-index-banner').style.display = s.indexed ? 'none' : '';
    document.getElementById('main-content').style.display = s.indexed ? '' : 'none';

    if (!s.indexed) { return; }

    // Disabled banner
    document.getElementById('disabled-banner').style.display = s.enabled ? 'none' : '';

    // Dim main content when disabled
    var mc = document.getElementById('main-content');
    if (!s.enabled) { mc.classList.add('dimmed'); } else { mc.classList.remove('dimmed'); }

    // Cards
    renderEconomyCards(s);

    // Update Copilot/AI session header based on active tool
    var activeTool = s.activeTool || 'unknown';
    var toolMeta = TOOL_META[activeTool] || TOOL_META['unknown'];

    // If the orchestrator is driving the UI, skip the legacy tool-session logic.
    if (_lastToolsSnapshot) {
      renderActiveTool();
    } else {
      var hdrEl = document.getElementById('copilot-hdr');
    if (hdrEl) {
      var wasShut = hdrEl.classList.contains('shut');
      hdrEl.innerHTML = toolMeta.icon + ' ' + escHtml(toolMeta.label) + ' Session'
        + '<span id="copilot-live" style="font-size:0.9em;color:var(--dim);margin-left:auto"></span>';
      if (wasShut) { hdrEl.classList.add('shut'); }
    }

    // ── Session section: tool-aware rendering ────────────────────
    var prevTool = _lastActiveTool;
    _lastActiveTool = activeTool;

    if (activeTool === 'copilot') {
      // Copilot: section is managed by renderCopilot(). But if we're switching
      // FROM a non-Copilot tool, we must reset the labels and re-trigger Copilot
      // rendering with the last known data (or hide section if no data).
      if (prevTool !== 'copilot' && prevTool !== 'unknown') {
        // Reset labels to Copilot defaults
        document.getElementById('copilot-sub-label').textContent = 'tokens ~estimated';
        document.getElementById('copilot-lbl-input').textContent = 'Input';
        document.getElementById('copilot-lbl-output').textContent = 'Output';
        document.getElementById('copilot-lbl-reasoning').textContent = 'Thinking';
        document.getElementById('copilot-input').style.color = 'var(--blue)';
        document.getElementById('copilot-est').textContent =
          '~ estimates based on transcript content (system prompt & re-sent history not included)';
        document.getElementById('copilot-hist-hdr').style.display = '';
        document.getElementById('sec-copilot-hist').style.display = '';
        // If CopilotWatcher has no data yet, hide the section until it fires
        if (!_lastCopilotData || _lastCopilotData.totalTokens === 0) {
          document.getElementById('copilot-section').style.display = 'none';
        } else {
          renderCopilot(_lastCopilotData);
        }
      }
    } else {
      // Non-Copilot tool: show session section with log.jsonl-derived stats
      renderToolSession(s, activeTool, toolMeta);
    }
    } // end: else branch of legacy copilot/tool-switch block (orchestrator not active)

    // Tool breakdown (per-tab economy)
    renderTools(s.byTool);
  }


  var TOOL_META = {
    'claude':  { icon: '\uD83E\uDDE0', label: 'Claude Code',     color: 'var(--yellow)' },
    'copilot': { icon: '\uD83E\uDD16', label: 'GitHub Copilot',  color: 'var(--blue)' },
    'codex':   { icon: '\u26A1',         label: 'ChatGPT / Codex', color: 'var(--green)' },
    'cursor':  { icon: '\uD83D\uDDB1', label: 'Cursor',          color: 'var(--blue)' },
    'unknown': { icon: '\u2753',         label: 'Unknown',         color: 'var(--dim)' },
  };

  function getToolTranscriptStats(tool) {
    if (tool === 'copilot') {
      if (_lastToolsSnapshot && _lastToolsSnapshot.copilot) { return _lastToolsSnapshot.copilot; }
      return _lastCopilotData;
    }
    if (_lastToolsSnapshot && _lastToolsSnapshot[tool]) {
      return _lastToolsSnapshot[tool];
    }
    return null;
  }

  function hasToolTranscriptStats(tool) {
    var stats = getToolTranscriptStats(tool);
    if (!stats) { return false; }
    return (stats.totalTokens || 0) > 0 || (stats.messageCount || 0) > 0;
  }

  function getToolTranscriptTotal(tool) {
    var stats = getToolTranscriptStats(tool);
    return stats ? (stats.totalTokens || 0) : 0;
  }


  function renderTools(byTool) {
    var container = document.getElementById('tool-breakdown');
    if (!byTool) { byTool = {}; }
    var activeTool = getPreferredActiveTool(_lastLensStats || {});
    var stat = byTool[activeTool];
    var meta = TOOL_META[activeTool] || TOOL_META['unknown'];

    // If tool has lens economy data, show savings card
    if (stat) {
      var pct = Math.max(0, Math.min(100, stat.avgPct));
      var detail = stat.count + ' quer' + (stat.count === 1 ? 'y' : 'ies') + ' \u00B7 used ' + fmtK(stat.totalUsed);
      var statMain = '-' + fmtK(stat.totalSaved);
      var statSub = pct.toFixed(0) + '% saved';
      var barWidth = pct.toFixed(1);
      var barStyle = 'height:100%;width:' + barWidth + '%;background:' + meta.color + ';border-radius:2px';
      var agoHtml = stat.lastTs ? '<span style="font-size:0.7em;color:var(--dim);margin-left:auto;white-space:nowrap">' + timeAgo(stat.lastTs) + '</span>' : '';
      var toolSnap0 = getToolSnapshot(activeTool);
      var modelLabel = (toolSnap0 && toolSnap0.model) ? ' \u00B7 ' + escHtml(toolSnap0.model) : '';

      container.innerHTML =
        '<div class="tool-card" style="border-color:' + meta.color + ';">'
        + '<div class="tool-icon">' + meta.icon + '</div>'
        + '<div class="tool-info">'
        + '<div class="tool-name" style="color:' + meta.color + ';display:flex;align-items:center;gap:4px">' + escHtml(meta.label) + modelLabel + agoHtml + '</div>'
        + '<div class="tool-detail">' + detail + '</div>'
        + '<div style="height:3px;background:var(--vscode-input-border);border-radius:2px;margin-top:2px;overflow:hidden">'
        + '<div style="' + barStyle + '"></div>'
        + '</div>'
        + '</div>'
        + '<div class="tool-stats">'
        + '<div class="tool-saved" style="color:var(--green)">' + statMain + '</div>'
        + '<div class="tool-pct">' + statSub + '</div>'
        + '</div>'
        + '</div>';
      return;
    }

    // No lens data — show "not optimizing" card if tool has transcript activity
    var toolSnap = getToolSnapshot(activeTool);
    if (toolSnap && ((toolSnap.totalTokens || 0) > 0 || (toolSnap.messageCount || 0) > 0)) {
      var tDetail = (toolSnap.messageCount || 0) + ' msg' + ((toolSnap.messageCount || 0) !== 1 ? 's' : '');
      if (toolSnap.toolCallCount > 0) {
        tDetail += ' \u00B7 ' + toolSnap.toolCallCount + ' tool call' + (toolSnap.toolCallCount !== 1 ? 's' : '');
      }
      if (toolSnap.model) {
        tDetail += ' \u00B7 ' + escHtml(toolSnap.model);
      }
      container.innerHTML =
        '<div class="tool-card" style="border-color:var(--vscode-inputValidation-warningBorder, #cca700);">'
        + '<div class="tool-icon">' + meta.icon + '</div>'
        + '<div class="tool-info">'
        + '<div class="tool-name" style="color:' + meta.color + '">' + escHtml(meta.label) + '</div>'
        + '<div class="tool-detail" style="color:var(--vscode-inputValidation-warningBorder, #cca700)">'
        + '\u26A0 Not using Context Lens \u2014 tokens not optimized'
        + '</div>'
        + '<div class="tool-detail">' + tDetail + ' \u00B7 ' + fmtK(toolSnap.totalTokens || 0) + ' tokens raw</div>'
        + '</div>'
        + '</div>';
      return;
    }

    container.innerHTML = '';
  }


  // ── Helpers ─────────────────────────────────────────────────────
  function fmtK(n) {
    if (n >= 1e6) { return (n / 1e6).toFixed(1) + 'M'; }
    if (n >= 1000) { return (n / 1000).toFixed(1) + 'k'; }
    return String(n);
  }

  function fmtTime(ts) {
    if (!ts) { return 'never'; }
    var d = new Date(ts * 1000);
    var mm = String(d.getMonth() + 1).padStart(2, '0');
    var dd = String(d.getDate()).padStart(2, '0');
    var hh = String(d.getHours()).padStart(2, '0');
    var mi = String(d.getMinutes()).padStart(2, '0');
    return dd + '/' + mm + ' ' + hh + ':' + mi;
  }

  function escHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function timeAgo(ts) {
    var now = Date.now() / 1000;
    var diff = Math.max(0, Math.floor(now - ts));
    if (diff < 5) { return 'just now'; }
    if (diff < 60) { return diff + 's ago'; }
    if (diff < 3600) { return Math.floor(diff / 60) + 'min ago'; }
    if (diff < 86400) { return Math.floor(diff / 3600) + 'h ago'; }
    return Math.floor(diff / 86400) + 'd ago';
  }

  function escAttr(s) { return escHtml(s || ''); }

  // Auto-refresh "⚡ Xmin ago" indicator every 60 seconds
  var _lastTs = null;
  var _lastCopilotTs = null;
  var _lastActiveTool = 'unknown';
  var _lastCopilotData = null;  // cached last Copilot transcript stats
  setInterval(function() {
    if (_lastTs) {
      var el = document.getElementById('last-query-ts');
      if (el) { el.textContent = '\u26A1 ' + timeAgo(_lastTs); }
    }
    if (_lastCopilotTs) {
      var cel = document.getElementById('copilot-live');
      if (cel) { cel.textContent = '\u26A1 ' + timeAgo(_lastCopilotTs); }
    }
  }, 60000);

  // ── Tool session rendering (non-Copilot tools) ─────────────────
  function renderToolSession(s, activeTool, toolMeta) {
    var section = document.getElementById('copilot-section');
    section.style.display = '';

    // Session name
    var nameEl = document.getElementById('copilot-session-name');
    if (s.sessionName) {
      nameEl.textContent = '\u25B6 ' + s.sessionName;
      nameEl.title = s.sessionName;
      nameEl.style.display = '';
    } else {
      nameEl.style.display = 'none';
    }

    var queries = s.activeToolSessionQueries || 0;
    var tokensUsed = s.activeToolSessionTokensUsed || 0;
    var tokensSaved = s.activeToolSessionTokensSaved || 0;
    var savingPct = s.activeToolSessionSavingPct || 0;

    // Total tokens used this session (for the big number)
    document.getElementById('copilot-total').textContent = queries > 0 ? fmtK(tokensUsed) : '0';
    document.getElementById('copilot-sub-label').textContent = 'tokens used this session';

    // Replace input/output/thinking with queries/used/saved
    document.getElementById('copilot-input').textContent = String(queries);
    document.getElementById('copilot-input').style.color = toolMeta.color || 'var(--blue)';
    document.getElementById('copilot-lbl-input').textContent = 'Queries';

    document.getElementById('copilot-output').textContent = queries > 0 ? fmtK(tokensSaved) : '\u2014';
    document.getElementById('copilot-output').style.color = 'var(--green)';
    document.getElementById('copilot-lbl-output').textContent = 'Saved';

    document.getElementById('copilot-reasoning').textContent =
      savingPct > 0 ? savingPct.toFixed(0) + '%' : '\u2014';
    document.getElementById('copilot-reasoning').style.color = 'var(--yellow)';
    document.getElementById('copilot-lbl-reasoning').textContent = 'Saving';

    // Live indicator
    _lastCopilotTs = s.lastQueryTs || null;
    var liveEl = document.getElementById('copilot-live');
    if (s.lastQueryTs) {
      liveEl.textContent = '\u26A1 ' + timeAgo(s.lastQueryTs);
    } else {
      liveEl.textContent = '';
    }

    // Meta line
    var meta;
    if (queries === 0) {
      meta = 'No queries yet \u00B7 waiting for first lens_context call';
    } else {
      meta = queries + ' quer' + (queries !== 1 ? 'ies' : 'y');
      meta += ' \u00B7 ' + fmtK(tokensUsed) + ' tokens used';
      if (tokensSaved > 0) {
        meta += ' \u00B7 ' + fmtK(tokensSaved) + ' saved';
      }
    }
    document.getElementById('copilot-meta').textContent = meta;

    // Hide session history (only relevant for Copilot)
    document.getElementById('copilot-hist-hdr').style.display = 'none';
    document.getElementById('sec-copilot-hist').style.display = 'none';

    // Update disclaimer
    document.getElementById('copilot-est').textContent =
      '~ based on Context Lens retrieval logs for this session';
  }

  // ── Copilot session rendering ───────────────────────────────────
  function renderCopilot(c) {
    // Always cache the latest Copilot data (used when switching back to Copilot)
    if (c) { _lastCopilotData = c; }

    // Only render Copilot transcript data when the active tool is copilot
    // (or unknown — backwards compat). For other tools, renderToolSession handles it.
    if (_lastActiveTool !== 'copilot' && _lastActiveTool !== 'unknown') {
      return;
    }

    var section = document.getElementById('copilot-section');
    if (!c || c.totalTokens === 0) {
      section.style.display = 'none';
      return;
    }
    section.style.display = '';

    // Restore Copilot-specific labels (in case renderToolSession changed them)
    document.getElementById('copilot-sub-label').textContent = 'tokens ~estimated';
    document.getElementById('copilot-lbl-input').textContent = 'Input';
    document.getElementById('copilot-lbl-output').textContent = 'Output';
    document.getElementById('copilot-lbl-reasoning').textContent = 'Thinking';
    document.getElementById('copilot-input').style.color = 'var(--blue)';
    document.getElementById('copilot-est').textContent =
      '~ estimates based on transcript content (system prompt & re-sent history not included)';
    document.getElementById('copilot-hist-hdr').style.display = '';
    document.getElementById('sec-copilot-hist').style.display = '';

    // Session name
    var nameEl = document.getElementById('copilot-session-name');
    if (c.sessionName) {
      nameEl.textContent = '\u25B6 ' + c.sessionName;
      nameEl.title = c.sessionName;
      nameEl.style.display = '';
    } else {
      nameEl.style.display = 'none';
    }

    document.getElementById('copilot-total').textContent = fmtK(c.totalTokens);
    document.getElementById('copilot-input').textContent = fmtK(c.inputTokens);
    document.getElementById('copilot-output').textContent = fmtK(c.outputTokens);
    document.getElementById('copilot-reasoning').textContent = fmtK(c.reasoningTokens);

    // Live indicator
    _lastCopilotTs = c.lastMessageTs || null;
    var liveEl = document.getElementById('copilot-live');
    if (c.lastMessageTs) {
      liveEl.textContent = '\u26A1 ' + timeAgo(c.lastMessageTs);
    } else {
      liveEl.textContent = '';
    }

    // Meta line
    var meta = c.messageCount + ' msg' + (c.messageCount !== 1 ? 's' : '');
    meta += ' \u00B7 ' + c.turnCount + ' turn' + (c.turnCount !== 1 ? 's' : '');
    if (c.toolCallCount > 0) {
      meta += ' \u00B7 ' + c.toolCallCount + ' tool call' + (c.toolCallCount !== 1 ? 's' : '');
    }
    document.getElementById('copilot-meta').textContent = meta;

    // Session history
    renderCopilotSessions(c.allSessions || []);
  }

  function renderCopilotSessions(sessions) {
    var container = document.getElementById('copilot-sessions');
    if (!sessions || sessions.length === 0) {
      container.innerHTML = '<div style="color:var(--dim)">No sessions found</div>';
      return;
    }
    var activeTool = (_lastToolsSnapshot && _lastToolsSnapshot.activeTool) || 'copilot';
    var rows = sessions.map(function(s) {
      var name = escHtml(s.name || 'Untitled');
      var tokens = fmtK(s.totalTokens);
      var ago = s.lastMessageTs ? timeAgo(s.lastMessageTs) : '';
      var activeTag = s.active
        ? '<span style="color:var(--green);font-weight:700"> \u25CF</span>'
        : '';
      var sid = escAttr(s.sessionId || '');
      return '<div class="session-row" data-session-id="' + sid + '" data-tool="' + activeTool + '" '
        + 'style="display:flex;align-items:center;gap:6px;cursor:pointer">'
        + '<div style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:0.95em" title="' + escAttr(s.name) + '">'
        + activeTag + ' ' + name
        + '</div>'
        + '<div style="flex-shrink:0;color:var(--blue);font-weight:600;font-size:0.9em">' + tokens + '</div>'
        + '<div style="flex-shrink:0;color:var(--dim);font-size:0.85em;width:52px;text-align:right">' + ago + '</div>'
        + '</div>';
    }).join('');
    container.innerHTML = rows;

    // Wire click handlers on session rows
    var rowEls = container.querySelectorAll('.session-row');
    for (var i = 0; i < rowEls.length; i++) {
      rowEls[i].addEventListener('click', function() {
        var sid = this.getAttribute('data-session-id');
        var tool = this.getAttribute('data-tool');
        if (sid && tool) {
          vscode.postMessage({ type: 'selectSession', tool: tool, sessionId: sid });
        }
      });
    }
  }
</script>
</body>
</html>`;
  }
}
