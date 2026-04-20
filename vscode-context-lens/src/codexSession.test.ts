/**
 * codexSession.test.ts — unit tests for Codex session matching.
 */
import { matchesCodexSessionId } from './codexSession';

function assert(condition: boolean, msg: string): void {
  if (!condition) { throw new Error('FAIL: ' + msg); }
  console.log('  ✓ ' + msg);
}

console.log('\n=== codexSession tests ===\n');

console.log('Test 1: matches by logical session id');
{
  assert(
    matchesCodexSessionId('sess-123', {
      sessionId: 'sess-123',
      fileId: 'rollout-2026-04-17-abc',
    }),
    'logical session id matches',
  );
}

console.log('\nTest 2: matches by rollout filename fallback');
{
  assert(
    matchesCodexSessionId('rollout-2026-04-17-abc', {
      sessionId: 'sess-123',
      fileId: 'rollout-2026-04-17-abc',
    }),
    'rollout filename matches',
  );
}

console.log('\nTest 3: rejects unrelated ids');
{
  assert(
    !matchesCodexSessionId('sess-other', {
      sessionId: 'sess-123',
      fileId: 'rollout-2026-04-17-abc',
    }),
    'unrelated ids do not match',
  );
}

console.log('\n=== All codexSession tests passed! ===\n');
