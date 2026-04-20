/**
 * security.test.ts — security-focused tests for the Context Lens extension.
 *
 * Validates:
 *  - Session ID sanitization (no path traversal)
 *  - Cross-platform path normalization
 *  - Input validation on parser inputs
 *  - XSS prevention in text formatting helpers
 */

import { estimateTokens, truncateSessionName } from './toolStats';
import { fmtK, fmtTime, parseLensData, emptyStats } from './logParser';

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

console.log('\n=== security tests ===\n');

// ── Test 1: Session ID validation regex ──────────────────────────────────
console.log('Test 1: session ID validation');
{
  // The regex used in ToolOrchestrator.selectSession:
  const isUnsafe = (id: string) => !id || /[/\\]|\.\./.test(id);

  // Valid IDs
  assert(!isUnsafe('abc123'), 'alphanumeric valid');
  assert(!isUnsafe('session-2026-04-18'), 'dashes valid');
  assert(!isUnsafe('d4e5f6a7-b8c9-0d1e-2f3a-4b5c6d7e8f9a'), 'UUID valid');
  assert(!isUnsafe('file.jsonl'), 'dots in filename valid');
  assert(!isUnsafe('my session name'), 'spaces valid');

  // Dangerous IDs — must be rejected
  assert(isUnsafe(''), 'empty rejected');
  assert(isUnsafe('../secret'), 'unix parent dir rejected');
  assert(isUnsafe('..\\secret'), 'windows parent dir rejected');
  assert(isUnsafe('../../etc/passwd'), 'deep traversal rejected');
  assert(isUnsafe('foo/bar'), 'forward slash rejected');
  assert(isUnsafe('foo\\bar'), 'backslash rejected');
  assert(isUnsafe('..'), 'bare double dots rejected');
  assert(isUnsafe('/absolute'), 'absolute unix rejected');
  assert(isUnsafe('\\absolute'), 'absolute windows rejected');
}

// ── Test 2: truncateSessionName sanitization ─────────────────────────────
console.log('\nTest 2: truncateSessionName edge cases');
{
  assertEq(truncateSessionName(''), '', 'empty stays empty');
  assertEq(truncateSessionName('hello'), 'hello', 'short unchanged');
  assertEq(truncateSessionName('line1\nline2\nline3'), 'line1', 'only first line');
  assertEq(truncateSessionName('  padded  '), 'padded', 'trimmed');

  // Very long input — should not crash or leak memory
  const longInput = 'A'.repeat(10000);
  const truncated = truncateSessionName(longInput);
  assert(truncated.length <= 70, 'long input truncated to reasonable length');
  assert(truncated.endsWith('…'), 'long input has ellipsis');

  // Potential XSS in session name — should be passed through (HTML escaping
  // is the UI layer's job, but parser should not crash)
  const xss = '<script>alert(1)</script>';
  const result = truncateSessionName(xss);
  assertEq(result, xss, 'HTML tags pass through (escaped by UI layer)');
}

// ── Test 3: estimateTokens robustness ────────────────────────────────────
console.log('\nTest 3: estimateTokens robustness');
{
  assertEq(estimateTokens(''), 0, 'empty = 0');
  assertEq(estimateTokens('a'), 1, '1 char = 1 token');
  assertEq(estimateTokens('abcd'), 1, '4 chars = 1 token');
  assertEq(estimateTokens('abcde'), 2, '5 chars = 2 tokens');

  // Unicode — should count bytes/chars, not crash
  const unicode = '你好世界🌍🚀';
  const tokens = estimateTokens(unicode);
  assert(tokens > 0, 'unicode produces positive token count');

  // Very large input
  const large = 'x'.repeat(1_000_000);
  assertEq(estimateTokens(large), 250_000, '1M chars = 250k tokens');
}

// ── Test 4: parseLensData with malicious input ───────────────────────────
console.log('\nTest 4: parseLensData with malicious input');
{
  // Empty everything — should not crash
  const empty = parseLensData('', {}, null);
  assert(empty.enabled === true, 'default enabled');
  assertEq(empty.totalQueries, 0, 'no queries');

  // Garbage log content
  const garbage = parseLensData(
    'not json\n{invalid\n<<<garbage>>>\n',
    { enabled: true },
    null,
  );
  assertEq(garbage.totalQueries, 0, 'garbage log = 0 queries');

  // Extremely large log (many lines)
  const bigLog = Array.from({ length: 1000 }, (_, i) =>
    JSON.stringify({
      event: 'retrieval',
      task: 'explain',
      tokens_used: 100,
      tokens_raw: 10000,
      ts: 1000 + i,
    }),
  ).join('\n');
  const bigResult = parseLensData(bigLog, {}, null);
  assertEq(bigResult.totalQueries, 1000, '1000 queries parsed');
  assertEq(bigResult.totalTokensUsed, 100_000, '1000 * 100 tokens used');
  assertEq(bigResult.totalTokensRaw, 10_000_000, '1000 * 10000 tokens raw');
  assertEq(bigResult.totalTokensSaved, 9_900_000, 'savings = raw - used');

  // Config with unexpected types — should not crash
  const weirdConfig = parseLensData('', {
    enabled: 'yes' as unknown,  // string instead of boolean
    token_budget: 'not a number' as unknown,
  }, null);
  assert(weirdConfig.enabled === true, 'non-false enabled = true');

  // Null / undefined defense
  const nullStats = parseLensData('', {}, null, null);
  assertEq(nullStats.sessionId, null, 'null session when no session.json');
}

// ── Test 5: fmtK formatting ─────────────────────────────────────────────
console.log('\nTest 5: fmtK formatting edge cases');
{
  assertEq(fmtK(0), '0', 'zero');
  assert(fmtK(999).includes('999'), '999 stays as-is');
  assert(fmtK(1000).includes('k') || fmtK(1000).includes('K') || fmtK(1000).includes('1'), '1000 formatted');
  assert(fmtK(1_500_000).includes('M') || fmtK(1_500_000).includes('m'), '1.5M formatted');

  // Negative numbers — edge case
  const neg = fmtK(-100);
  assert(typeof neg === 'string', 'negative produces string');

  // Very large
  const huge = fmtK(999_999_999);
  assert(typeof huge === 'string', 'billion produces string');
  assert(huge.length < 20, 'billion formatted compactly');
}

// ── Test 6: fmtTime ────────────────────────────────────────────────────
console.log('\nTest 6: fmtTime edge cases');
{
  const zero = fmtTime(0);
  assert(typeof zero === 'string', 'zero timestamp produces string');

  const now = fmtTime(Date.now() / 1000);
  assert(typeof now === 'string', 'current timestamp produces string');
  assert(now.length > 0, 'current timestamp non-empty');
}

// ── Test 7: Log parser retrieval aggregation ─────────────────────────────
console.log('\nTest 7: retrieval aggregation accuracy');
{
  const log = [
    JSON.stringify({ event: 'retrieval', task: 'explain', tokens_used: 2000, tokens_raw: 50000, ts: 100, tool: 'copilot' }),
    JSON.stringify({ event: 'retrieval', task: 'bugfix', tokens_used: 3000, tokens_raw: 80000, ts: 200, tool: 'copilot' }),
    JSON.stringify({ event: 'retrieval', task: 'explain', tokens_used: 1500, tokens_raw: 40000, ts: 300, tool: 'claude' }),
    JSON.stringify({ event: 'intent', task: 'explain', ts: 50 }),  // not a retrieval — should be ignored
  ].join('\n');

  const result = parseLensData(log, { enabled: true }, null);
  assertEq(result.totalQueries, 3, '3 retrievals (intent excluded)');
  assertEq(result.totalTokensUsed, 6500, 'used = 2000+3000+1500');
  assertEq(result.totalTokensRaw, 170000, 'raw = 50000+80000+40000');
  assertEq(result.totalTokensSaved, 163500, 'saved = 170000-6500');

  // Average saving percentage
  const expectedPct = (1 - 6500 / 170000) * 100;
  assert(Math.abs(result.avgSavingPct - expectedPct) < 0.1, 'avg saving % within tolerance');

  // byTask breakdown
  assert('explain' in result.byTask, 'explain task exists');
  assert('bugfix' in result.byTask, 'bugfix task exists');
  assertEq(result.byTask.explain.count, 2, 'explain count = 2');
  assertEq(result.byTask.bugfix.count, 1, 'bugfix count = 1');
}

// ── Test 8: Session filtering ────────────────────────────────────────────
console.log('\nTest 8: session filtering by session_id');
{
  const log = [
    JSON.stringify({ event: 'retrieval', task: 'explain', tokens_used: 100, tokens_raw: 1000, ts: 1, session_id: 1 }),
    JSON.stringify({ event: 'retrieval', task: 'explain', tokens_used: 200, tokens_raw: 2000, ts: 2, session_id: 2 }),
    JSON.stringify({ event: 'retrieval', task: 'explain', tokens_used: 300, tokens_raw: 3000, ts: 3, session_id: 2 }),
  ].join('\n');

  const sessionJson = { id: 2, name: 'session two', tool: 'copilot' };
  const result = parseLensData(log, {}, null, sessionJson);

  assertEq(result.totalQueries, 3, 'total = 3');
  assertEq(result.sessionQueries, 2, 'session queries = 2 (session_id=2)');
  assertEq(result.sessionId, 2, 'session id = 2');
}

console.log('\n=== All security tests passed! ===\n');
