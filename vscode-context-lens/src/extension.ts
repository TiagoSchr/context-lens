/**
 * extension.ts — VS Code extension entry point.
 *
 * Wires together:
 *  - LensWatcher        — file-system watcher for .ctx/{log.jsonl,...}
 *  - CopilotWatcher     — Copilot Chat transcript JSONL watcher
 *  - ClaudeWatcher      — Claude Code transcript JSONL watcher
 *  - CodexWatcher       — Codex CLI rollout JSONL watcher
 *  - ToolOrchestrator   — unified per-tool snapshot + auto-switch
 *  - LensStatusBar      — bottom status bar
 *  - LensSidebarProvider — Activity Bar webview dashboard
 */
import * as vscode from 'vscode';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { LensWatcher } from './lensWatcher';
import { LensStatusBar } from './statusBarItem';
import { LensSidebarProvider } from './sidebarProvider';
import { CopilotWatcher } from './copilotWatcher';
import { ClaudeWatcher } from './claudeWatcher';
import { CodexWatcher } from './codexWatcher';
import { ToolOrchestrator } from './toolOrchestrator';
import { ToolName } from './toolStats';
import { ToolAvailability } from './toolAvailability';
import { registerChatParticipant } from './chatParticipant';
import { registerLensTools } from './lensToolProvider';

function detectToolFromActiveTab(tab: vscode.Tab | undefined): ToolName | undefined {
  if (!tab) { return undefined; }

  const input = tab.input;
  if (input instanceof vscode.TabInputWebview) {
    const viewType = input.viewType.toLowerCase();
    if (viewType === 'claudevscodepanel') { return 'claude'; }
    if (viewType === 'chatgpt.sidebarview' || viewType === 'chatgpt.sidebarsecondaryview') {
      return 'codex';
    }
  }

  if (input instanceof vscode.TabInputCustom) {
    const viewType = input.viewType.toLowerCase();
    if (viewType === 'chatgpt.conversationeditor') { return 'codex'; }
  }

  return undefined;
}

type TabGroupsCompat = {
  activeTabGroup?: { activeTab?: vscode.Tab };
  onDidChangeTabs?: (listener: () => void) => vscode.Disposable;
  onDidChangeTabGroups?: (listener: () => void) => vscode.Disposable;
};

function hasInstalledExtension(...ids: string[]): boolean {
  const wanted = new Set(ids.map((id) => id.toLowerCase()));
  return vscode.extensions.all.some((ext) => wanted.has(ext.id.toLowerCase()));
}

function workspaceHasMarker(...relativePaths: string[]): boolean {
  return (vscode.workspace.workspaceFolders ?? []).some((folder) =>
    relativePaths.some((relativePath) => fs.existsSync(path.join(folder.uri.fsPath, relativePath))));
}

function detectToolAvailability(
  showCopilotTokens: boolean,
  enableClaude: boolean,
  enableCodex: boolean,
): ToolAvailability {
  return {
    copilot: showCopilotTokens && hasInstalledExtension('github.copilot', 'github.copilot-chat'),
    claude: enableClaude
      && (fs.existsSync(path.join(os.homedir(), '.claude')) || workspaceHasMarker('.claude', 'CLAUDE.md')),
    codex: enableCodex
      && (fs.existsSync(path.join(os.homedir(), '.codex')) || workspaceHasMarker('.codex', 'AGENTS.md')),
  };
}

export function activate(context: vscode.ExtensionContext): void {
  const watcher = new LensWatcher();
  const cfg = vscode.workspace.getConfiguration('contextLens');
  const showCopilotTokens = cfg.get<boolean>('showCopilotTokens', true);
  const enableClaude = cfg.get<boolean>('enableClaudeTracking', true);
  const enableCodex = cfg.get<boolean>('enableCodexTracking', true);
  const availability = detectToolAvailability(showCopilotTokens, enableClaude, enableCodex);

  const copilotWatcher = availability.copilot
    ? new CopilotWatcher(context.storageUri, context.globalStorageUri)
    : undefined;
  const claudeWatcher = availability.claude ? new ClaudeWatcher() : undefined;
  const codexWatcher = availability.codex ? new CodexWatcher() : undefined;

  const orchestrator = new ToolOrchestrator(copilotWatcher, claudeWatcher, codexWatcher, availability);

  const statusBar = new LensStatusBar(context, watcher);
  const sidebar = new LensSidebarProvider(context, watcher, copilotWatcher, orchestrator);
  const tabGroups = (vscode.window as typeof vscode.window & { tabGroups?: TabGroupsCompat }).tabGroups;
  const syncFocusedToolFromTab = () => {
    const hinted = detectToolFromActiveTab(tabGroups?.activeTabGroup?.activeTab);
    if (hinted) { orchestrator.setActiveTool(hinted); }
  };

  context.subscriptions.push(
    watcher,
    statusBar,
    sidebar,
    orchestrator,

    vscode.window.registerWebviewViewProvider('contextLens.sidebar', sidebar, {
      webviewOptions: { retainContextWhenHidden: true },
    }),

    vscode.commands.registerCommand('contextLens.toggle', () => watcher.toggle()),
    vscode.commands.registerCommand('contextLens.refresh', () => {
      watcher.refresh();
      copilotWatcher?.refresh();
      claudeWatcher?.refresh();
      codexWatcher?.refresh();
    }),
    vscode.commands.registerCommand('contextLens.runIndex', () => watcher.runLensIndex()),
    vscode.commands.registerCommand('contextLens.openConfig', () => watcher.openConfig()),
    vscode.commands.registerCommand('contextLens.openChat', () => {
      vscode.commands.executeCommand('workbench.action.chat.open', {
        query: '@lens ',
        isPartialQuery: true,
      });
    }),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (
        e.affectsConfiguration('contextLens.showCopilotTokens')
        || e.affectsConfiguration('contextLens.enableClaudeTracking')
        || e.affectsConfiguration('contextLens.enableCodexTracking')
      ) {
        vscode.window.showInformationMessage(
          'Context Lens: reload the window to apply AI tool tracking changes.',
        );
      }
    }),
  );

  if (tabGroups?.onDidChangeTabs) {
    context.subscriptions.push(tabGroups.onDidChangeTabs(syncFocusedToolFromTab));
  }
  if (tabGroups?.onDidChangeTabGroups) {
    context.subscriptions.push(tabGroups.onDidChangeTabGroups(syncFocusedToolFromTab));
  }

  // ── Chat Participant & Language Model Tools ────────────────────────────
  // @lens participant: guaranteed context injection for Copilot Chat
  // LM tools: lens_context/lens_search available to ALL chat agents
  const getRoot = () => watcher.root;
  context.subscriptions.push(registerChatParticipant(context, getRoot));
  context.subscriptions.push(...registerLensTools(getRoot));

  if (copilotWatcher) { context.subscriptions.push(copilotWatcher); }
  if (claudeWatcher) { context.subscriptions.push(claudeWatcher); }
  if (codexWatcher) { context.subscriptions.push(codexWatcher); }

  watcher.start();
  copilotWatcher?.start();
  claudeWatcher?.start();
  codexWatcher?.start();
  syncFocusedToolFromTab();

  // ── First-use notification: teach @lens shortcut ────────────────────────
  const SEEN_KEY = 'contextLens.seenWelcome';
  if (!context.globalState.get<boolean>(SEEN_KEY)) {
    const showWelcome = () => {
      if (!watcher.root) { return; }
      vscode.window.showInformationMessage(
        'Context Lens: Use @lens in chat for guaranteed optimized context. '
        + 'Shortcut: Ctrl+Shift+L',
        'Open @lens Chat',
        'Got it',
      ).then((choice) => {
        if (choice === 'Open @lens Chat') {
          vscode.commands.executeCommand('contextLens.openChat');
        }
        context.globalState.update(SEEN_KEY, true);
      });
    };
    // Show after root is found (10s delay to not spam on startup)
    const welcomeTimer = setTimeout(showWelcome, 10_000);
    context.subscriptions.push({ dispose: () => clearTimeout(welcomeTimer) });
  }
}

export function deactivate(): void {
  // VS Code calls dispose() on all context.subscriptions automatically
}
