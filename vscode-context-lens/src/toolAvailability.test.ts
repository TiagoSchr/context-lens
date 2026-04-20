/**
 * toolAvailability.test.ts — unit tests for shared tool-availability helpers.
 */
import {
  emptyToolAvailability,
  firstAvailableTool,
  isToolAvailable,
  listAvailableTools,
} from './toolAvailability';

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

console.log('\n=== toolAvailability tests ===\n');

console.log('Test 1: emptyToolAvailability');
{
  const availability = emptyToolAvailability();
  assertEq(availability.copilot, false, 'copilot disabled by default');
  assertEq(availability.claude, false, 'claude disabled by default');
  assertEq(availability.codex, false, 'codex disabled by default');
  assertEq(listAvailableTools(availability).length, 0, 'no available tools');
}

console.log('\nTest 2: listAvailableTools preserves tool order');
{
  const availability = {
    copilot: true,
    claude: false,
    codex: true,
  };
  assertEq(
    JSON.stringify(listAvailableTools(availability)),
    JSON.stringify(['copilot', 'codex']),
    'only enabled tools are listed in stable order',
  );
}

console.log('\nTest 3: firstAvailableTool');
{
  assertEq(firstAvailableTool({ copilot: false, claude: true, codex: true }), 'claude', 'first enabled tool');
  assertEq(firstAvailableTool({ copilot: false, claude: false, codex: false }), 'copilot', 'fallback tool');
}

console.log('\nTest 4: isToolAvailable');
{
  const availability = {
    copilot: true,
    claude: false,
    codex: true,
  };
  assert(isToolAvailable(availability, 'copilot'), 'copilot available');
  assert(!isToolAvailable(availability, 'claude'), 'claude unavailable');
  assert(isToolAvailable(availability, 'codex'), 'codex available');
}

console.log('\n=== All toolAvailability tests passed! ===\n');
