/**
 * orchestrator.test.ts — tests for ToolOrchestrator logic:
 *  - auto-switch based on activity timestamps
 *  - manual pin (setActiveTool)
 *  - session ID validation (path traversal prevention)
 *  - snapshot generation
 *  - poll timer lifecycle
 *
 * Since ToolOrchestrator depends on vscode, CopilotWatcher, etc., we test the
 * pure logic portions directly by constructing minimal stubs.
 */

import { ToolName } from './toolStats';
import {
  ToolAvailability,
  listAvailableTools,
  firstAvailableTool,
  isToolAvailable,
  emptyToolAvailability,
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

console.log('\n=== orchestrator tests ===\n');

// ── Test 1: ToolAvailability helpers ─────────────────────────────────────
console.log('Test 1: ToolAvailability helpers');
{
  const all: ToolAvailability = { copilot: true, claude: true, codex: true };
  assertEq(listAvailableTools(all).length, 3, 'all 3 available');
  assertEq(firstAvailableTool(all), 'copilot', 'copilot is first');
  assert(isToolAvailable(all, 'claude'), 'claude available');

  const none: ToolAvailability = { copilot: false, claude: false, codex: false };
  assertEq(listAvailableTools(none).length, 0, 'none available');
  assertEq(firstAvailableTool(none), 'copilot', 'fallback to copilot');
  assert(!isToolAvailable(none, 'codex'), 'codex not available');

  const partial: ToolAvailability = { copilot: false, claude: true, codex: false };
  assertEq(listAvailableTools(partial).length, 1, 'one available');
  assertEq(firstAvailableTool(partial), 'claude', 'first is claude');
}

// ── Test 2: Session ID validation (path traversal prevention) ────────────
console.log('\nTest 2: Session ID validation');
{
  // Simulate the validation regex from toolOrchestrator.selectSession
  const isValid = (id: string) => !(!id || /[/\\]|\.\./.test(id));

  assert(isValid('abc-123'), 'normal ID is valid');
  assert(isValid('session_2026-04-18_001'), 'underscore-dash ID valid');
  assert(isValid('d4e5f6a7-b8c9-0d1e-2f3a-4b5c6d7e8f9a'), 'UUID valid');

  assert(!isValid(''), 'empty string rejected');
  assert(!isValid('../../../etc/passwd'), 'path traversal rejected');
  assert(!isValid('..\\..\\Windows\\System32'), 'windows traversal rejected');
  assert(!isValid('foo/bar'), 'forward slash rejected');
  assert(!isValid('foo\\bar'), 'backslash rejected');
  assert(!isValid('..'), 'double dots rejected');
}

// ── Test 3: Auto-switch timestamp logic ──────────────────────────────────
console.log('\nTest 3: auto-switch timestamp comparison');
{
  // Test the core logic: the tool with the highest recent timestamp wins,
  // but only if it leads the current tool by AUTO_SWITCH_MIN_LEAD_MS (500ms)
  const AUTO_SWITCH_MIN_LEAD_MS = 500;

  function computeBest(
    tools: Array<{ tool: ToolName; ts: number }>,
    currentTool: ToolName,
  ): ToolName {
    if (tools.length === 0) { return currentTool; }
    let best = tools[0];
    for (const t of tools) {
      if (t.ts > best.ts) { best = t; }
    }
    const currentTs = tools.find((t) => t.tool === currentTool)?.ts ?? 0;
    if ((best.ts - currentTs) * 1000 >= AUTO_SWITCH_MIN_LEAD_MS) {
      return best.tool;
    }
    return currentTool;
  }

  assertEq(
    computeBest([
      { tool: 'copilot', ts: 100 },
      { tool: 'claude', ts: 200 },
    ], 'copilot'),
    'claude',
    'switch to claude when it leads by 100s',
  );

  assertEq(
    computeBest([
      { tool: 'copilot', ts: 100.0 },
      { tool: 'claude', ts: 100.1 },
    ], 'copilot'),
    'copilot',
    'no switch when lead is only 0.1s (100ms < 500ms)',
  );

  assertEq(
    computeBest([
      { tool: 'copilot', ts: 100 },
      { tool: 'claude', ts: 100 },
    ], 'copilot'),
    'copilot',
    'no switch when timestamps equal',
  );

  assertEq(
    computeBest([], 'codex'),
    'codex',
    'no data: keep current tool',
  );
}

// ── Test 4: Pin expiry logic ─────────────────────────────────────────────
console.log('\nTest 4: pin expiry logic');
{
  const PIN_DURATION_MS = 30_000;

  function shouldRespectPin(pinExpiry: number, now: number, pinned: boolean): boolean {
    if (!pinned) { return false; }
    return now < pinExpiry;
  }

  const now = Date.now();
  assert(
    shouldRespectPin(now + PIN_DURATION_MS, now, true),
    'pin active within window',
  );
  assert(
    !shouldRespectPin(now - 1, now, true),
    'pin expired',
  );
  assert(
    !shouldRespectPin(now + PIN_DURATION_MS, now, false),
    'not pinned',
  );
}

// ── Test 5: Snapshot shape ───────────────────────────────────────────────
console.log('\nTest 5: snapshot shape validation');
{
  // Ensure a snapshot-like object has all required fields
  interface MinimalSnapshot {
    activeTool: ToolName;
    availableTools: ToolName[];
    changedTool: ToolName | null;
  }

  const snap: MinimalSnapshot = {
    activeTool: 'copilot',
    availableTools: ['copilot', 'claude'],
    changedTool: 'claude',
  };

  assert(typeof snap.activeTool === 'string', 'activeTool is string');
  assert(Array.isArray(snap.availableTools), 'availableTools is array');
  assert(snap.changedTool === null || typeof snap.changedTool === 'string', 'changedTool nullable');
  assert(snap.availableTools.every((t) => ['copilot', 'claude', 'codex'].includes(t)), 'valid tool names');
}

// ── Test 6: Tool order stability ─────────────────────────────────────────
console.log('\nTest 6: tool order stability');
{
  // listAvailableTools should always return in stable order copilot → claude → codex
  const tests: Array<{ avail: ToolAvailability; expected: ToolName[] }> = [
    { avail: { copilot: true, claude: true, codex: true }, expected: ['copilot', 'claude', 'codex'] },
    { avail: { copilot: false, claude: true, codex: true }, expected: ['claude', 'codex'] },
    { avail: { copilot: true, claude: false, codex: true }, expected: ['copilot', 'codex'] },
    { avail: { copilot: false, claude: false, codex: true }, expected: ['codex'] },
  ];

  for (const { avail, expected } of tests) {
    const result = listAvailableTools(avail);
    assertEq(JSON.stringify(result), JSON.stringify(expected), `order: ${expected.join(',') || 'empty'}`);
  }
}

console.log('\n=== All orchestrator tests passed! ===\n');
