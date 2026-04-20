/**
 * crossPlatform.test.ts — tests for cross-platform path handling, encoding,
 * and edge cases that affect portability across Windows, macOS, and Linux.
 */

import * as path from 'path';
import { parseCopilotTranscript, emptyCopilotStats } from './copilotParser';
import { parseClaudeTranscript } from './claudeParser';
import { parseCodexRollout, parseCodexSessionMeta } from './codexParser';

function assert(condition: boolean, msg: string): void {
  if (!condition) { throw new Error('FAIL: ' + msg); }
  console.log('  ✓ ' + msg);
}

function assertEq<T>(actual: T, expected: T, msg: string): void {
  if (actual !== expected) {
    throw new Error(`FAIL: ${msg} (got ${JSON.stringify(actual)}, expected ${JSON.stringify(expected)})`);
  }
  console.log('  ✓ ' + msg);
}

function toLines(text: string): string[] {
  return text.split('\n').filter((l) => l.trim().length > 0);
}

console.log('\n=== crossPlatform tests ===\n');

// ── Test 1: Path normalization ───────────────────────────────────────────
console.log('Test 1: path normalization');
{
  // path.normalize should work correctly on all platforms
  const winPath = 'C:\\Users\\test\\.codex\\sessions';
  const unixPath = '/home/test/.codex/sessions';
  const mixedPath = 'C:/Users/test/.codex/sessions';

  const normWin = path.normalize(winPath);
  const normUnix = path.normalize(unixPath);
  const normMixed = path.normalize(mixedPath);

  assert(normWin.length > 0, 'windows path normalizes');
  assert(normUnix.length > 0, 'unix path normalizes');
  assert(normMixed.length > 0, 'mixed path normalizes');
  assert(!normMixed.includes('/') || !normMixed.includes('\\'), 'mixed path uses consistent separators');
}

// ── Test 2: file:// URI decoding ─────────────────────────────────────────
console.log('\nTest 2: file:// URI decoding');
{
  // The regex from copilotWatcher.ts should strip file:/// prefix correctly
  const strip = (uri: string) => decodeURIComponent(uri.replace(/^file:\/\/\/?/, ''));

  // Windows
  assertEq(strip('file:///C%3A/Users/test/project'), 'C:/Users/test/project', 'windows URI decoded');

  // Unix
  assertEq(strip('file:///home/test/project'), 'home/test/project', 'unix URI decoded');

  // Spaces in path
  assertEq(strip('file:///C%3A/My%20Projects/foo'), 'C:/My Projects/foo', 'spaces decoded');

  // Already decoded
  assertEq(strip('file:///C:/Users/test'), 'C:/Users/test', 'already decoded passes through');

  // path.normalize should fix separators after decoding
  const decoded = strip('file:///C%3A/Users/test/project');
  const normalized = path.normalize(decoded);
  assert(normalized.length > 0, 'normalized decoded path');
}

// ── Test 3: Codex session meta parsing ───────────────────────────────────
console.log('\nTest 3: Codex session meta with various cwd formats');
{
  // Windows cwd
  const winMeta = parseCodexSessionMeta(JSON.stringify({
    type: 'session_meta',
    payload: { id: 'test-1', cwd: 'C:\\Users\\test\\project' },
  }));
  assert(winMeta !== null, 'windows meta parsed');
  assertEq(winMeta!.id, 'test-1', 'win session id');
  assertEq(winMeta!.cwd, 'C:\\Users\\test\\project', 'win cwd preserved');

  // Unix cwd
  const unixMeta = parseCodexSessionMeta(JSON.stringify({
    type: 'session_meta',
    payload: { id: 'test-2', cwd: '/home/test/project' },
  }));
  assert(unixMeta !== null, 'unix meta parsed');
  assertEq(unixMeta!.cwd, '/home/test/project', 'unix cwd preserved');

  // CWD with spaces
  const spaceMeta = parseCodexSessionMeta(JSON.stringify({
    type: 'session_meta',
    payload: { id: 'test-3', cwd: 'C:\\My Projects\\cool app' },
  }));
  assert(spaceMeta !== null, 'spaced meta parsed');
  assertEq(spaceMeta!.cwd, 'C:\\My Projects\\cool app', 'spaces in cwd preserved');
}

// ── Test 4: Parser resilience to encoding issues ─────────────────────────
console.log('\nTest 4: parser resilience to encoding issues');
{
  // UTF-8 BOM at start of file
  const bom = '\uFEFF' + JSON.stringify({
    type: 'user.message',
    data: { content: 'hello' },
    id: '1', timestamp: '2026-01-01T00:00:00.000Z', parentId: null,
  });
  const bomResult = parseCopilotTranscript(toLines(bom));
  // Should not crash — BOM may cause first line parse to fail, which is ok
  assert(typeof bomResult.totalTokens === 'number', 'BOM does not crash copilot parser');

  // Null bytes in content
  const nullContent = JSON.stringify({
    type: 'user.message',
    data: { content: 'hello\x00world' },
    id: '1', timestamp: '2026-01-01T00:00:00.000Z', parentId: null,
  });
  const nullResult = parseCopilotTranscript(toLines(nullContent));
  assert(typeof nullResult.totalTokens === 'number', 'null bytes do not crash parser');

  // Empty lines mixed in
  const mixed = '\n\n' + JSON.stringify({
    type: 'user.message',
    data: { content: 'test' },
    id: '1', timestamp: '2026-01-01T00:00:00.000Z', parentId: null,
  }) + '\n\n\n';
  const mixedResult = parseCopilotTranscript(toLines(mixed));
  assertEq(mixedResult.messageCount, 1, 'empty lines skipped');
}

// ── Test 5: Claude parser with different path formats ────────────────────
console.log('\nTest 5: Claude parser resilience');
{
  // Empty content
  const emptyResult = parseClaudeTranscript([]);
  assertEq(emptyResult.totalTokens, 0, 'empty claude = 0 tokens');
  assertEq(emptyResult.messageCount, 0, 'empty claude = 0 messages');

  // Single valid message
  const single = JSON.stringify({
    type: 'user',
    message: { role: 'user', content: [{ type: 'text', text: 'hello world' }] },
  });
  const singleResult = parseClaudeTranscript(toLines(single));
  assertEq(singleResult.messageCount, 1, 'single message counted');

  // Garbage mixed with valid
  const garbageMixed = 'not json\n' + single + '\nalso not json';
  const garbageResult = parseClaudeTranscript(toLines(garbageMixed));
  assertEq(garbageResult.messageCount, 1, 'garbage lines skipped');
}

// ── Test 6: Codex parser resilience ──────────────────────────────────────
console.log('\nTest 6: Codex parser resilience');
{
  // Empty content
  const emptyResult = parseCodexRollout([]);
  assertEq(emptyResult.totalTokens, 0, 'empty codex = 0 tokens');

  // Only session_meta (no messages)
  const metaOnly = JSON.stringify({
    type: 'session_meta',
    payload: { id: 'test', cwd: '/tmp' },
  });
  const metaResult = parseCodexRollout(toLines(metaOnly));
  assertEq(metaResult.sessionId, 'test', 'session id from meta');
  assertEq(metaResult.messageCount, 0, 'no messages from meta-only');

  // Large session_meta with base_instructions
  const largeMeta = JSON.stringify({
    type: 'session_meta',
    payload: { id: 'large-test', cwd: '/home/test', base_instructions: 'x'.repeat(20000) },
  });
  const largeResult = parseCodexRollout(toLines(largeMeta));
  assertEq(largeResult.sessionId, 'large-test', 'large meta parsed correctly');
}

// ── Test 7: Process platform awareness ───────────────────────────────────
console.log('\nTest 7: process.platform awareness');
{
  assert(
    ['win32', 'darwin', 'linux'].includes(process.platform),
    `platform "${process.platform}" is supported`,
  );

  // The case-sensitivity logic from codexWatcher:
  const isCaseSensitive = process.platform === 'linux';
  assert(typeof isCaseSensitive === 'boolean', 'case sensitivity determined');

  if (process.platform === 'win32') {
    // On Windows, paths should be case-insensitive
    assert(!isCaseSensitive, 'Windows is case-insensitive');
  }
}

// ── Test 8: Copilot empty stats shape ────────────────────────────────────
console.log('\nTest 8: empty stats shapes are complete');
{
  const stats = emptyCopilotStats();

  // All fields must exist and be the right type
  assertEq(typeof stats.sessionId, 'string', 'sessionId is string');
  assertEq(typeof stats.sessionName, 'string', 'sessionName is string');
  assertEq(typeof stats.messageCount, 'number', 'messageCount is number');
  assertEq(typeof stats.toolCallCount, 'number', 'toolCallCount is number');
  assertEq(typeof stats.turnCount, 'number', 'turnCount is number');
  assertEq(typeof stats.inputTokens, 'number', 'inputTokens is number');
  assertEq(typeof stats.outputTokens, 'number', 'outputTokens is number');
  assertEq(typeof stats.reasoningTokens, 'number', 'reasoningTokens is number');
  assertEq(typeof stats.totalTokens, 'number', 'totalTokens is number');
  assertEq(typeof stats.lastMessageTs, 'number', 'lastMessageTs is number');
  assert(Array.isArray(stats.recentMessages), 'recentMessages is array');
  assert(Array.isArray(stats.allSessions), 'allSessions is array');
  assertEq(stats.totalTokens, 0, 'empty total = 0');
  assertEq(stats.messageCount, 0, 'empty messages = 0');
}

console.log('\n=== All crossPlatform tests passed! ===\n');
