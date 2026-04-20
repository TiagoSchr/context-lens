/**
 * statusBarItem.ts — Shows token savings in the VS Code status bar (bottom right).
 * Click to toggle optimization on/off.
 */
import * as vscode from 'vscode';
import { LensWatcher } from './lensWatcher';
import { LensStats, fmtK } from './logParser';

export class LensStatusBar implements vscode.Disposable {
  private readonly _item: vscode.StatusBarItem;
  private readonly _sub: vscode.Disposable;

  constructor(_ctx: vscode.ExtensionContext, watcher: LensWatcher) {
    this._item = vscode.window.createStatusBarItem(
      'contextLens.statusBar',
      vscode.StatusBarAlignment.Right,
      90,
    );
    this._item.command = 'contextLens.openChat';
    this._item.name = 'Context Lens';

    this._sub = watcher.onDidChange((stats) => this._render(stats));
    this._render(watcher.stats);

    const cfg = vscode.workspace.getConfiguration('contextLens');
    if (cfg.get('showStatusBar', true)) {
      this._item.show();
    }

    // React to user changing the setting
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration('contextLens.showStatusBar')) {
        const show = vscode.workspace.getConfiguration('contextLens').get('showStatusBar', true);
        show ? this._item.show() : this._item.hide();
      }
    });
  }

  private _render(stats: LensStats): void {
    if (!stats.indexed) {
      this._item.text = '$(circuit-board) Lens: not indexed';
      this._item.tooltip = 'Context Lens — run `lens index` to start\nClick to open @lens chat';
      this._item.backgroundColor = undefined;
      return;
    }

    if (!stats.enabled) {
      this._item.text = '$(circle-slash) Lens: OFF';
      this._item.tooltip = new vscode.MarkdownString(
        '**Context Lens** is **disabled**.\nClick to open @lens chat.',
      );
      this._item.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
      return;
    }

    this._item.backgroundColor = undefined;

    if (stats.totalQueries === 0) {
      this._item.text = '$(circuit-board) Lens: ready';
      this._item.tooltip = new vscode.MarkdownString(
        `**Context Lens** is active.\n\n`
        + `Index: **${stats.files}** files · **${stats.symbols}** symbols\n\n`
        + `Budget: **${fmtK(stats.tokenBudget)}** tokens\n\n`
        + `No queries run yet. Click to open @lens chat.\n\n`
        + `Shortcut: **Ctrl+Shift+L**`,
      );
      return;
    }

    const saved = fmtK(stats.totalTokensSaved);
    const pct = stats.avgSavingPct.toFixed(0);
    this._item.text = `$(circuit-board) Lens: ${saved} saved (${pct}%)`;

    const sessLine = stats.sessionQueries > 0
      ? `Session: **${fmtK(stats.sessionTokensSaved)}** saved over **${stats.sessionQueries}** queries\n\n`
      : '';

    this._item.tooltip = new vscode.MarkdownString(
      `**Context Lens** — token economy\n\n`
      + `${sessLine}`
      + `All-time: **${fmtK(stats.totalTokensSaved)}** tokens saved (**${pct}%**) `
      + `over **${stats.totalQueries}** queries\n\n`
      + `Click to open @lens chat · **Ctrl+Shift+L**`,
    );
    this._item.tooltip.isTrusted = true;
  }

  dispose(): void {
    this._item.dispose();
    this._sub.dispose();
  }
}
