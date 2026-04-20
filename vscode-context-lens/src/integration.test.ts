/**
 * integration.test.ts — cross-parser integration tests.
 *
 * Validates that all three parsers (Copilot, Claude, Codex) produce
 * consistent ToolStats-compatible output and that the token breakdown
 * invariants hold:
 *   totalTokens = inputTokens + outputTokens + reasoningTokens
 *
 * Also tests the toolAvailability + toolStats helpers together.
 */
import { parseCopilotTranscript, CopilotStats } from './copilotParser';
import { parseClaudeTranscript } from './claudeParser';
import { parseCodexRollout } from './codexParser';
import { ToolStats, ToolName, emptyToolStats } from './toolStats';
import {
  emptyToolAvailability,
  listAvailableTools,
  firstAvailableTool,
  isToolAvailable,
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

const countChars = (text: string): number => text.length;

console.log('\n=== integration tests ===\n');

// ── Helper: build events ────────────────────────────────────────────
function copilotEvt(type: string, data: Record<string, unknown>, ts = '2026-01-01T00:00:00.000Z'): string {
  return JSON.stringify({ type, data, id: String(Math.random()), timestamp: ts, parentId: null });
}

function claudeEvt(data: Record<string, unknown>): string {
  return JSON.stringify(data);
}

function codexEvt(data: Record<string, unknown>): string {
  return JSON.stringify(data);
}

// ── Test 1: token invariant across all parsers ──────────────────────
console.log('Test 1: totalTokens = input + output + reasoning invariant');
{
  // Copilot
  const copilotLines = [
    copilotEvt('session.start', { sessionId: 'inv-copilot' }),
    copilotEvt('user.message', { content: 'Hello copilot' }, '2026-01-01T00:00:01.000Z'),
    copilotEvt('assistant.turn_start', { turnId: 't1' }),
    copilotEvt('assistant.message', {
      content: 'Hi there!',
      reasoningText: 'Think',
      toolRequests: [],
    }, '2026-01-01T00:00:02.000Z'),
  ];
  const copilot = parseCopilotTranscript(copilotLines, countChars);
  assertEq(copilot.totalTokens, copilot.inputTokens + copilot.outputTokens + copilot.reasoningTokens,
    'Copilot: totalTokens = input + output + reasoning');

  // Claude
  const claudeLines = [
    claudeEvt({
      type: 'human',
      message: { role: 'user', content: [{ type: 'text', text: 'Hello claude' }] },
      timestamp: '2026-01-01T00:00:01.000Z',
    }),
    claudeEvt({
      type: 'assistant',
      message: {
        role: 'assistant',
        content: [{ type: 'text', text: 'Hello!' }],
        model: 'claude-sonnet-4-20250514',
        usage: {
          input_tokens: 100,
          output_tokens: 50,
          cache_read_input_tokens: 20,
          cache_creation_input_tokens: 5,
        },
      },
      timestamp: '2026-01-01T00:00:02.000Z',
    }),
  ];
  const claude = parseClaudeTranscript(claudeLines);
  assertEq(claude.totalTokens, claude.inputTokens + claude.outputTokens + claude.reasoningTokens,
    'Claude: totalTokens = input + output + reasoning');
  assert(claude.tokensFromUsage, 'Claude uses real usage data');

  // Codex — token_count events
  const codexLines = [
    codexEvt({
      type: 'session_started',
      session_id: 'codex-sess-1',
      timestamp_ms: Date.parse('2026-01-01T00:00:00.000Z'),
    }),
    codexEvt({
      type: 'message_added',
      message: {
        role: 'user',
        content: [{ type: 'input_text', text: 'Hello codex' }],
      },
      timestamp_ms: Date.parse('2026-01-01T00:00:01.000Z'),
    }),
    codexEvt({
      type: 'message_added',
      message: {
        role: 'assistant',
        content: [{ type: 'output_text', text: 'Hi from codex' }],
      },
      timestamp_ms: Date.parse('2026-01-01T00:00:02.000Z'),
    }),
    codexEvt({
      type: 'token_count',
      token_count: {
        input_tokens: 80,
        output_tokens: 30,
        cached_input_tokens: 10,
        reasoning_output_tokens: 5,
      },
      timestamp_ms: Date.parse('2026-01-01T00:00:03.000Z'),
    }),
  ];
  const codex = parseCodexRollout(codexLines);
  assertEq(codex.totalTokens, codex.inputTokens + codex.outputTokens + codex.reasoningTokens,
    'Codex: totalTokens = input + output + reasoning');
}

// ── Test 2: all parsers return consistent structure ─────────────────
console.log('\nTest 2: consistent structure');
{
  // Copilot produces CopilotStats, but shares key fields
  const copilot = parseCopilotTranscript([
    copilotEvt('session.start', { sessionId: 'struct-1' }),
  ], countChars);
  const claude = parseClaudeTranscript([]);
  const codex = parseCodexRollout([]);

  // All should have zero tokens for empty input
  for (const [name, stats] of [['copilot', copilot], ['claude', claude], ['codex', codex]] as const) {
    assertEq(stats.totalTokens, 0, `${name}: empty parse has 0 totalTokens`);
    assertEq(stats.messageCount, 0, `${name}: empty parse has 0 messages`);
    assert(typeof stats.inputTokens === 'number', `${name}: inputTokens is number`);
    assert(typeof stats.outputTokens === 'number', `${name}: outputTokens is number`);
    assert(typeof stats.reasoningTokens === 'number', `${name}: reasoningTokens is number`);
  }
}

// ── Test 3: toolAvailability helpers ────────────────────────────────
console.log('\nTest 3: toolAvailability integration');
{
  const none = emptyToolAvailability();
  assertEq(listAvailableTools(none).length, 0, 'no tools available by default');
  assertEq(firstAvailableTool(none), 'copilot', 'fallback to copilot when nothing available');

  const copilotOnly = { copilot: true, claude: false, codex: false };
  assertEq(listAvailableTools(copilotOnly).length, 1, 'one tool available');
  assertEq(listAvailableTools(copilotOnly)[0], 'copilot', 'copilot is available');
  assert(isToolAvailable(copilotOnly, 'copilot'), 'copilot is available');
  assert(!isToolAvailable(copilotOnly, 'claude'), 'claude is not available');

  const all = { copilot: true, claude: true, codex: true };
  assertEq(listAvailableTools(all).length, 3, 'all 3 tools available');
  // Order should be copilot, claude, codex (matches TOOL_ORDER)
  const list = listAvailableTools(all);
  assertEq(list[0], 'copilot', 'first = copilot');
  assertEq(list[1], 'claude', 'second = claude');
  assertEq(list[2], 'codex', 'third = codex');
}

// ── Test 4: emptyToolStats defaults ─────────────────────────────────
console.log('\nTest 4: emptyToolStats');
{
  const tools: ToolName[] = ['copilot', 'claude', 'codex'];
  for (const tool of tools) {
    const empty = emptyToolStats(tool);
    assertEq(empty.tool, tool, `emptyToolStats(${tool}).tool = ${tool}`);
    assertEq(empty.totalTokens, 0, `emptyToolStats(${tool}).totalTokens = 0`);
    assertEq(empty.inputTokens, 0, `emptyToolStats(${tool}).inputTokens = 0`);
    assertEq(empty.outputTokens, 0, `emptyToolStats(${tool}).outputTokens = 0`);
    assertEq(empty.reasoningTokens, 0, `emptyToolStats(${tool}).reasoningTokens = 0`);
    assertEq(empty.messageCount, 0, `emptyToolStats(${tool}).messageCount = 0`);
    assertEq(empty.tokensFromUsage, false, `emptyToolStats(${tool}).tokensFromUsage = false`);
    assertEq(empty.allSessions.length, 0, `emptyToolStats(${tool}).allSessions empty`);
  }
}

// ── Test 5: Copilot tool call count matches tool requests ───────────
console.log('\nTest 5: Copilot toolCallCount accuracy');
{
  const lines = [
    copilotEvt('session.start', { sessionId: 'tc-count' }),
    copilotEvt('user.message', { content: 'Do stuff' }, '2026-01-01T00:00:01.000Z'),
    copilotEvt('assistant.turn_start', { turnId: 't1' }),
    copilotEvt('tool.execution_start', { toolName: 'a', arguments: '{}' }),
    copilotEvt('tool.execution_start', { toolName: 'b', arguments: '{}' }),
    copilotEvt('assistant.message', {
      content: 'Done',
      reasoningText: '',
      toolRequests: [
        { toolCallId: '1', name: 'a', arguments: '{}', type: 'function' },
        { toolCallId: '2', name: 'b', arguments: '{}', type: 'function' },
      ],
    }, '2026-01-01T00:00:02.000Z'),
    copilotEvt('assistant.turn_start', { turnId: 't2' }),
    copilotEvt('tool.execution_start', { toolName: 'c', arguments: '{}' }),
    copilotEvt('assistant.message', {
      content: 'Also done',
      reasoningText: '',
      toolRequests: [
        { toolCallId: '3', name: 'c', arguments: '{}', type: 'function' },
      ],
    }, '2026-01-01T00:00:03.000Z'),
  ];

  const stats = parseCopilotTranscript(lines, countChars);
  assertEq(stats.toolCallCount, 3, 'toolCallCount = 3 across two assistant messages');
  assertEq(stats.turnCount, 2, 'turnCount = 2');
  assertEq(stats.messageCount, 3, 'messageCount = 1 user + 2 assistant');
}

// ── Test 6: Copilot recentMessages ordering ─────────────────────────
console.log('\nTest 6: recentMessages ordering');
{
  const lines = [
    copilotEvt('session.start', { sessionId: 'recent-1' }),
    copilotEvt('user.message', { content: 'First' }, '2026-01-01T00:00:01.000Z'),
    copilotEvt('assistant.turn_start', { turnId: 't1' }),
    copilotEvt('assistant.message', { content: 'Reply', reasoningText: '', toolRequests: [] }, '2026-01-01T00:00:02.000Z'),
    copilotEvt('user.message', { content: 'Second' }, '2026-01-01T00:00:03.000Z'),
    copilotEvt('assistant.turn_start', { turnId: 't2' }),
    copilotEvt('assistant.message', { content: 'Reply2', reasoningText: '', toolRequests: [] }, '2026-01-01T00:00:04.000Z'),
  ];

  const stats = parseCopilotTranscript(lines, countChars);
  assertEq(stats.recentMessages.length, 4, '4 recent messages');
  assertEq(stats.recentMessages[0].role, 'assistant', 'newest message first');
  assertEq(stats.recentMessages[3].role, 'user', 'oldest message last');
}

console.log('\n=== All integration tests passed! ===\n');
