/**
 * lensToolProvider.ts — registers Context Lens as VS Code Language Model Tools.
 *
 * Makes `lens_context` and `lens_search` available to ALL chat participants
 * (including Copilot agent mode, @workspace, and any third-party participants).
 *
 * The tools call the `lens` CLI as a subprocess, which reuses the existing
 * Python MCP engine — no duplicate logic needed.
 */
import * as vscode from 'vscode';
import * as cp from 'child_process';

// ── Input types ──────────────────────────────────────────────────────────────

interface LensContextInput {
  query: string;
  task?: string;
  budget?: number;
}

interface LensSearchInput {
  query: string;
}

// ── Public registration ──────────────────────────────────────────────────────

export function registerLensTools(
  getRoot: () => string | null,
): vscode.Disposable[] {
  return [
    vscode.lm.registerTool('lens_context', new LensContextTool(getRoot)),
    vscode.lm.registerTool('lens_search', new LensSearchTool(getRoot)),
  ];
}

// ── Tool implementations ─────────────────────────────────────────────────────

class LensContextTool implements vscode.LanguageModelTool<LensContextInput> {
  constructor(private readonly _getRoot: () => string | null) {}

  async invoke(
    options: vscode.LanguageModelToolInvocationOptions<LensContextInput>,
    token: vscode.CancellationToken,
  ): Promise<vscode.LanguageModelToolResult> {
    const root = this._getRoot();
    if (!root) {
      return textResult('No indexed project found. Run `lens index` in your project first.');
    }

    const { query, task, budget } = options.input;
    const args = ['context', query];
    if (task) { args.push('--task', task); }
    if (budget) { args.push('--budget', String(budget)); }

    const result = await runLens(root, args, token);
    return textResult(result ?? 'Failed to get context from Context Lens.');
  }
}

class LensSearchTool implements vscode.LanguageModelTool<LensSearchInput> {
  constructor(private readonly _getRoot: () => string | null) {}

  async invoke(
    options: vscode.LanguageModelToolInvocationOptions<LensSearchInput>,
    token: vscode.CancellationToken,
  ): Promise<vscode.LanguageModelToolResult> {
    const root = this._getRoot();
    if (!root) {
      return textResult('No indexed project found. Run `lens index` in your project first.');
    }

    const result = await runLens(root, ['search', options.input.query], token);
    return textResult(result ?? 'No results found.');
  }
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function textResult(text: string): vscode.LanguageModelToolResult {
  return new vscode.LanguageModelToolResult([
    new vscode.LanguageModelTextPart(text),
  ]);
}

const TIMEOUT_MS = 30_000;

function runLens(
  root: string,
  args: string[],
  token: vscode.CancellationToken,
): Promise<string | null> {
  return new Promise((resolve) => {
    const proc = cp.spawn('lens', args, {
      cwd: root,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
      shell: true,
    });

    let stdout = '';
    proc.stdout?.on('data', (d: Buffer) => { stdout += d.toString(); });
    proc.on('close', (code) => resolve(code === 0 && stdout ? stdout : null));
    proc.on('error', () => resolve(null));

    const cancelSub = token.onCancellationRequested(() => { proc.kill(); resolve(null); });
    const timer = setTimeout(() => { proc.kill(); resolve(null); }, TIMEOUT_MS);

    proc.on('exit', () => {
      clearTimeout(timer);
      cancelSub.dispose();
    });
  });
}
