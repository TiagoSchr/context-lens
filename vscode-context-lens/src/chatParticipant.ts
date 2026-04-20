/**
 * chatParticipant.ts — @lens chat participant for VS Code.
 *
 * Provides guaranteed token optimization for Copilot Chat:
 *   - @lens <query>      → fetch optimized context, answer with LLM
 *   - @lens /context <q>  → return raw optimized context block
 *   - @lens /search <q>   → search symbols by name
 *   - @lens /status        → show index health
 *
 * Unlike instruction-based approaches (copilot-instructions.md), the chat
 * participant ALWAYS calls Context Lens before answering — enforcement is
 * structural, not voluntary.
 *
 * The file index (project navigation map) is generated on-demand on the first
 * @lens request per session, then cached in memory for subsequent requests.
 */
import * as vscode from 'vscode';
import * as cp from 'child_process';

const TIMEOUT_MS = 30_000;
const FILE_INDEX_BUDGET = 4_000;

// ── Session-level cache for the file index ────────────────────────────────
let _cachedFileIndex: string | null = null;
let _fileIndexRoot: string | null = null;

export function registerChatParticipant(
  context: vscode.ExtensionContext,
  getRoot: () => string | null,
): vscode.Disposable {
  const participant = vscode.chat.createChatParticipant(
    'context-lens.lens',
    (request, chatContext, stream, token) =>
      handleRequest(request, chatContext, stream, token, getRoot),
  );

  participant.iconPath = vscode.Uri.joinPath(context.extensionUri, 'images', 'icon-activity.svg');

  return participant;
}

// ── Request handler ──────────────────────────────────────────────────────────

async function handleRequest(
  request: vscode.ChatRequest,
  _chatContext: vscode.ChatContext,
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
  getRoot: () => string | null,
): Promise<void> {
  const root = getRoot();

  // ── No indexed project → fall back to plain LLM (no context injection) ─
  if (!root) {
    return plainLlmFallback(request, stream, token);
  }

  // ── Slash commands ───────────────────────────────────────────────────────
  if (request.command === 'status') {
    return handleStatus(root, stream, token);
  }
  if (request.command === 'search') {
    return handleSearch(root, request.prompt, stream, token);
  }
  if (request.command === 'context') {
    return handleContextOnly(root, request.prompt, stream, token);
  }

  // ── Default: context + LLM answer ──────────────────────────────────────
  if (!request.prompt.trim()) {
    stream.markdown(
      'Context Lens is active — every message gets optimized codebase context automatically.\n\n'
      + 'Commands:\n'
      + '- `/context <query>` — get raw optimized context\n'
      + '- `/search <name>` — search symbols\n'
      + '- `/status` — check index health\n',
    );
    return;
  }

  stream.progress('Fetching optimized context…');

  const lensContext = await runLens(root, ['context', request.prompt], token);
  if (token.isCancellationRequested) { return; }

  // If lens fails, fall back to plain LLM — don't block the user
  if (!lensContext) {
    return plainLlmFallback(request, stream, token);
  }

  // Build prompt with the optimized context
  // Also include the file index (generated on-demand, cached per session)
  let fileIndex = '';
  if (!token.isCancellationRequested) {
    const index = await getFileIndex(root, token);
    if (index) {
      fileIndex = '\n\n--- FILE INDEX (all project files with symbols + line numbers) ---\n'
        + index
        + '\n--- END FILE INDEX ---\n';
    }
  }

  const messages = [
    vscode.LanguageModelChatMessage.User(
      'You are a helpful coding assistant. Use the following optimized codebase '
      + 'context to answer the user\'s question accurately. Reference specific '
      + 'files, functions, and line numbers when relevant.\n\n'
      + '--- CODEBASE CONTEXT (from Context Lens — token-optimized) ---\n'
      + lensContext
      + '\n--- END CONTEXT ---\n'
      + fileIndex
      + '\nUser\'s question: ' + request.prompt,
    ),
  ];

  const response = await request.model.sendRequest(messages, {}, token);
  for await (const fragment of response.text) {
    stream.markdown(fragment);
  }
}

// ── Fallback: forward to LLM without context injection ───────────────────

async function plainLlmFallback(
  request: vscode.ChatRequest,
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
): Promise<void> {
  if (!request.prompt.trim()) { return; }
  const messages = [
    vscode.LanguageModelChatMessage.User(request.prompt),
  ];
  const response = await request.model.sendRequest(messages, {}, token);
  for await (const fragment of response.text) {
    stream.markdown(fragment);
  }
}

// ── Command handlers ─────────────────────────────────────────────────────────

async function handleStatus(
  root: string,
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
): Promise<void> {
  stream.progress('Checking index health…');
  const result = await runLens(root, ['health'], token);
  stream.markdown(result || 'Could not fetch index status. Run `lens health` in terminal.');
}

async function handleSearch(
  root: string,
  query: string,
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
): Promise<void> {
  if (!query.trim()) {
    stream.markdown('Usage: `@lens /search <symbol name>`');
    return;
  }
  stream.progress('Searching symbols…');
  const result = await runLens(root, ['search', query], token);
  stream.markdown(result || 'No results found.');
}

async function handleContextOnly(
  root: string,
  query: string,
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
): Promise<void> {
  if (!query.trim()) {
    stream.markdown('Usage: `@lens /context <query>`');
    return;
  }
  stream.progress('Building optimized context…');
  const result = await runLens(root, ['context', query], token);
  if (result) {
    stream.markdown('```\n' + result + '\n```');
  } else {
    stream.markdown('Failed to build context. Check that `lens index` has been run.');
  }
}

// ── File index: on-demand generation with session cache ──────────────────────

async function getFileIndex(
  root: string,
  token: vscode.CancellationToken,
): Promise<string | null> {
  // Return cached version if same project root
  if (_cachedFileIndex && _fileIndexRoot === root) {
    return _cachedFileIndex;
  }

  // Generate file index via `lens auto-context` (lightweight: only level0 + file_index)
  const result = await runLens(
    root,
    ['auto-context', '--budget', String(FILE_INDEX_BUDGET)],
    token,
  );
  if (result) {
    // Extract just the FILE INDEX section
    const indexStart = result.indexOf('=== FILE INDEX');
    if (indexStart >= 0) {
      _cachedFileIndex = result.slice(indexStart).trim();
    } else {
      _cachedFileIndex = result.trim();
    }
    _fileIndexRoot = root;
    return _cachedFileIndex;
  }
  return null;
}

// ── CLI runner ───────────────────────────────────────────────────────────────

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
