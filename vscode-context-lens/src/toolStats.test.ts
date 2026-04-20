/**
 * toolStats.test.ts — unit tests for toolStats helpers.
 */
import { emptyToolStats, estimateTokens, truncateSessionName, ToolStats } from './toolStats';

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

console.log('\n=== toolStats tests ===\n');

// ── Test 1: emptyToolStats ────────────────────────────────────────────────
console.log('Test 1: emptyToolStats');
{
  const s = emptyToolStats('claude');
  assertEq(s.tool, 'claude', 'tool set');
  assertEq(s.totalTokens, 0, 'tokens zero');
  assertEq(s.messageCount, 0, 'messages zero');
  assertEq(s.sessionId, '', 'sessionId empty');
  assertEq(s.allSessions.length, 0, 'no sessions');
}

// ── Test 2: estimateTokens ────────────────────────────────────────────────
console.log('\nTest 2: estimateTokens');
{
  assertEq(estimateTokens(''), 0, 'empty string = 0');
  assertEq(estimateTokens('abcd'), 1, '4 chars = 1 token');
  assertEq(estimateTokens('abcde'), 2, '5 chars = 2 tokens (ceil)');
  assertEq(estimateTokens('a'), 1, '1 char = 1 token');
  assertEq(estimateTokens('ab'), 1, '2 chars = 1 token');
  assertEq(estimateTokens('abc'), 1, '3 chars = 1 token');
  assertEq(estimateTokens('a'.repeat(100)), 25, '100 chars = 25 tokens');
}

// ── Test 3: truncateSessionName ───────────────────────────────────────────
console.log('\nTest 3: truncateSessionName');
{
  assertEq(truncateSessionName(''), '', 'empty → empty');
  assertEq(truncateSessionName('short name'), 'short name', 'short unchanged');
  assertEq(truncateSessionName('line1\nline2\nline3'), 'line1', 'only first line');
  assertEq(truncateSessionName('  padded  '), 'padded', 'trimmed');
  const long = 'x'.repeat(100);
  const truncated = truncateSessionName(long, 60);
  assertEq(truncated.length, 61, '60 chars + ellipsis');
  assert(truncated.endsWith('\u2026'), 'ends with ellipsis');
}

// ── Test 4: emptyToolStats for each tool type ─────────────────────────────
console.log('\nTest 4: emptyToolStats for each tool');
{
  assertEq(emptyToolStats('copilot').tool, 'copilot', 'copilot');
  assertEq(emptyToolStats('codex').tool, 'codex', 'codex');
  assertEq(emptyToolStats('claude').tool, 'claude', 'claude');
}

console.log('\n=== All toolStats tests passed! ===\n');
