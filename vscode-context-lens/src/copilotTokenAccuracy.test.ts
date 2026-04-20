/**
 * copilotTokenAccuracy.test.ts — detailed tests to ensure Copilot token
 * counting is accurate and free from double-counting bugs.
 *
 * These tests specifically validate:
 *  - Tool arguments are NOT counted twice (the historical double-counting bug)
 *  - Input/output/reasoning token splits are correct
 *  - Session summaries agree with full parse results
 *  - Edge cases: empty messages, missing fields, multiple tool calls
 */
import {
  parseCopilotTranscript,
  parseSessionSummary,
  estimateTokens,
  emptyCopilotStats,
} from './copilotParser';

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

function assertClose(actual: number, expected: number, tolerance: number, msg: string): void {
  if (Math.abs(actual - expected) > tolerance) {
    throw new Error(`FAIL: ${msg} (got ${actual}, expected ~${expected} ±${tolerance})`);
  }
  console.log('  ✓ ' + msg);
}

// Use char-length counter for deterministic tests
const countChars = (text: string): number => text.length;

console.log('\n=== copilotTokenAccuracy tests ===\n');

// ── Helper: build transcript events ─────────────────────────────────
function evt(type: string, data: Record<string, unknown>, ts = '2026-01-01T00:00:00.000Z'): string {
  return JSON.stringify({ type, data, id: Math.random().toString(36).slice(2), timestamp: ts, parentId: null });
}

// ── Test 1: No double-counting of tool arguments ────────────────────
console.log('Test 1: tool arguments NOT double-counted');
{
  const toolArgs = '{"path":"src/main.ts","content":"hello world"}';
  const lines = [
    evt('session.start', { sessionId: 'dc-1' }),
    evt('user.message', { content: 'Read this file' }, '2026-01-01T00:00:01.000Z'),
    evt('assistant.turn_start', { turnId: 't1' }),
    // tool.execution_start has the same args that were in toolRequests
    evt('tool.execution_start', { toolName: 'read_file', arguments: toolArgs }, '2026-01-01T00:00:02.000Z'),
    evt('assistant.message', {
      content: 'Here is the file content',
      reasoningText: '',
      toolRequests: [{
        toolCallId: 'tc-1',
        name: 'read_file',
        arguments: toolArgs,
        type: 'function',
      }],
    }, '2026-01-01T00:00:03.000Z'),
  ];

  const stats = parseCopilotTranscript(lines, countChars);
  const userTokens = 'Read this file'.length;
  const toolInputTokens = toolArgs.length;
  const assistantContentTokens = 'Here is the file content'.length;

  assertEq(stats.inputTokens, userTokens + toolInputTokens,
    'inputTokens = user message + tool.execution_start args only');
  assertEq(stats.outputTokens, assistantContentTokens,
    'outputTokens = assistant content only (NOT including toolRequests.arguments)');
  assertEq(stats.totalTokens, userTokens + toolInputTokens + assistantContentTokens,
    'totalTokens = input + output, no double-counting');

  // Verify that tool args are counted exactly ONCE in the total
  const totalWithDoubleCounting = userTokens + toolInputTokens + assistantContentTokens + toolArgs.length;
  assert(stats.totalTokens < totalWithDoubleCounting,
    'totalTokens must be LESS than what double-counting would produce');
}

// ── Test 2: Multiple tool calls in single assistant message ─────────
console.log('\nTest 2: multiple tool calls, no double-counting');
{
  const args1 = '{"query":"token"}';
  const args2 = '{"path":"src/utils.ts"}';
  const args3 = '{"command":"npm test"}';

  const lines = [
    evt('session.start', { sessionId: 'dc-2' }),
    evt('user.message', { content: 'Help' }, '2026-01-01T00:00:01.000Z'),
    evt('assistant.turn_start', { turnId: 't1' }),
    evt('tool.execution_start', { toolName: 'grep_search', arguments: args1 }, '2026-01-01T00:00:02.000Z'),
    evt('tool.execution_start', { toolName: 'read_file', arguments: args2 }, '2026-01-01T00:00:03.000Z'),
    evt('tool.execution_start', { toolName: 'run_in_terminal', arguments: args3 }, '2026-01-01T00:00:04.000Z'),
    evt('assistant.message', {
      content: 'Done',
      reasoningText: 'Thinking hard',
      toolRequests: [
        { toolCallId: 'tc-1', name: 'grep_search', arguments: args1, type: 'function' },
        { toolCallId: 'tc-2', name: 'read_file', arguments: args2, type: 'function' },
        { toolCallId: 'tc-3', name: 'run_in_terminal', arguments: args3, type: 'function' },
      ],
    }, '2026-01-01T00:00:05.000Z'),
  ];

  const stats = parseCopilotTranscript(lines, countChars);
  const expectedInput = 'Help'.length + args1.length + args2.length + args3.length;
  const expectedOutput = 'Done'.length;
  const expectedReasoning = 'Thinking hard'.length;

  assertEq(stats.inputTokens, expectedInput, 'inputTokens = user + all 3 tool execution args');
  assertEq(stats.outputTokens, expectedOutput, 'outputTokens = only assistant content');
  assertEq(stats.reasoningTokens, expectedReasoning, 'reasoningTokens from thinking text');
  assertEq(stats.totalTokens, expectedInput + expectedOutput + expectedReasoning, 'totalTokens correct');
  assertEq(stats.toolCallCount, 3, '3 tool calls counted');
}

// ── Test 3: Assistant message with no tool calls ────────────────────
console.log('\nTest 3: simple conversation (no tools)');
{
  const lines = [
    evt('session.start', { sessionId: 'simple-1' }),
    evt('user.message', { content: 'What is 2+2?' }, '2026-01-01T00:00:01.000Z'),
    evt('assistant.turn_start', { turnId: 't1' }),
    evt('assistant.message', {
      content: 'The answer is 4.',
      reasoningText: '',
      toolRequests: [],
    }, '2026-01-01T00:00:02.000Z'),
  ];

  const stats = parseCopilotTranscript(lines, countChars);
  assertEq(stats.inputTokens, 'What is 2+2?'.length, 'inputTokens = user message only');
  assertEq(stats.outputTokens, 'The answer is 4.'.length, 'outputTokens = assistant content');
  assertEq(stats.reasoningTokens, 0, 'no reasoning tokens');
  assertEq(stats.totalTokens, stats.inputTokens + stats.outputTokens, 'total = input + output');
  assertEq(stats.toolCallCount, 0, 'no tool calls');
  assertEq(stats.messageCount, 2, '2 messages (user + assistant)');
  assertEq(stats.turnCount, 1, '1 turn');
}

// ── Test 4: Empty fields and missing data ───────────────────────────
console.log('\nTest 4: empty/missing fields');
{
  const lines = [
    evt('session.start', { sessionId: 'edge-1' }),
    evt('user.message', { content: '' }, '2026-01-01T00:00:01.000Z'),
    evt('assistant.message', {
      content: '',
      // no reasoningText field
      // no toolRequests field
    }, '2026-01-01T00:00:02.000Z'),
    // tool.execution_start with no arguments
    evt('tool.execution_start', { toolName: 'some_tool' }, '2026-01-01T00:00:03.000Z'),
  ];

  const stats = parseCopilotTranscript(lines, countChars);
  assertEq(stats.inputTokens, 0, 'no input tokens from empty content');
  assertEq(stats.outputTokens, 0, 'no output tokens from empty content');
  assertEq(stats.reasoningTokens, 0, 'no reasoning from missing field');
  assertEq(stats.totalTokens, 0, 'total is 0');
  assertEq(stats.messageCount, 2, '2 messages even with empty content');
}

// ── Test 5: Multi-turn conversation accumulation ────────────────────
console.log('\nTest 5: multi-turn accumulation');
{
  const lines = [
    evt('session.start', { sessionId: 'multi-1' }),
    evt('user.message', { content: 'Hello' }, '2026-01-01T00:00:01.000Z'),
    evt('assistant.turn_start', { turnId: 't1' }),
    evt('assistant.message', { content: 'Hi!', reasoningText: '', toolRequests: [] }, '2026-01-01T00:00:02.000Z'),
    evt('user.message', { content: 'Tell me more' }, '2026-01-01T00:00:03.000Z'),
    evt('assistant.turn_start', { turnId: 't2' }),
    evt('assistant.message', { content: 'Sure, here is info', reasoningText: 'Let me think', toolRequests: [] }, '2026-01-01T00:00:04.000Z'),
    evt('user.message', { content: 'Thanks' }, '2026-01-01T00:00:05.000Z'),
    evt('assistant.turn_start', { turnId: 't3' }),
    evt('assistant.message', { content: 'Welcome!', reasoningText: '', toolRequests: [] }, '2026-01-01T00:00:06.000Z'),
  ];

  const stats = parseCopilotTranscript(lines, countChars);
  const expectedInput = 'Hello'.length + 'Tell me more'.length + 'Thanks'.length;
  const expectedOutput = 'Hi!'.length + 'Sure, here is info'.length + 'Welcome!'.length;
  const expectedReasoning = 'Let me think'.length;

  assertEq(stats.inputTokens, expectedInput, 'input accumulates across turns');
  assertEq(stats.outputTokens, expectedOutput, 'output accumulates across turns');
  assertEq(stats.reasoningTokens, expectedReasoning, 'reasoning accumulates across turns');
  assertEq(stats.messageCount, 6, '6 total messages');
  assertEq(stats.turnCount, 3, '3 turns');
  assertEq(stats.sessionName, 'Hello', 'session name from first user message');
}

// ── Test 6: estimateTokens heuristic ────────────────────────────────
console.log('\nTest 6: estimateTokens heuristic');
{
  assertEq(estimateTokens(''), 0, 'empty string = 0 tokens');
  assertEq(estimateTokens('abcd'), 1, '4 chars = 1 token');
  assertEq(estimateTokens('abcde'), 2, '5 chars = 2 tokens (ceiling)');
  assertEq(estimateTokens('a'.repeat(100)), 25, '100 chars = 25 tokens');
  assertEq(estimateTokens('a'.repeat(401)), 101, '401 chars = 101 tokens (ceiling)');
}

// ── Test 7: parseSessionSummary vs full parse consistency ───────────
console.log('\nTest 7: summary consistency');
{
  const lines = [
    evt('session.start', { sessionId: 'sum-1' }),
    evt('user.message', { content: 'First question' }, '2026-01-01T00:00:01.000Z'),
    evt('assistant.turn_start', { turnId: 't1' }),
    evt('assistant.message', { content: 'First answer', reasoningText: 'Thinking...', toolRequests: [] }, '2026-01-01T00:00:02.000Z'),
  ];

  const full = parseCopilotTranscript(lines, countChars);
  const summary = parseSessionSummary(lines.join('\n'), countChars);

  assertEq(summary.sessionId, full.sessionId, 'summary.sessionId matches full parse');
  assertEq(summary.name, full.sessionName, 'summary.name matches full parse');
  assertEq(summary.messageCount, full.messageCount, 'summary.messageCount matches');
  assert(summary.lastMessageTs > 0, 'summary has a lastMessageTs');
}

// ── Test 8: tool.execution_start with object args (not string) ──────
console.log('\nTest 8: tool.execution_start with object arguments');
{
  const argsObj = { path: 'src/foo.ts', encoding: 'utf-8' };
  const argsStr = JSON.stringify(argsObj);

  const lines = [
    evt('session.start', { sessionId: 'obj-args-1' }),
    evt('user.message', { content: 'Read foo' }, '2026-01-01T00:00:01.000Z'),
    evt('assistant.turn_start', { turnId: 't1' }),
    evt('tool.execution_start', { toolName: 'read_file', arguments: argsObj }, '2026-01-01T00:00:02.000Z'),
    evt('assistant.message', {
      content: 'Content of foo',
      reasoningText: '',
      toolRequests: [{ toolCallId: 'tc-1', name: 'read_file', arguments: argsStr, type: 'function' }],
    }, '2026-01-01T00:00:03.000Z'),
  ];

  const stats = parseCopilotTranscript(lines, countChars);
  // object args → JSON.stringify
  assertEq(stats.inputTokens, 'Read foo'.length + argsStr.length,
    'object arguments are JSON.stringified for token counting');
  assertEq(stats.outputTokens, 'Content of foo'.length,
    'output is only assistant content');
}

// ── Test 9: malformed/empty lines are skipped ───────────────────────
console.log('\nTest 9: malformed lines skipped');
{
  const lines = [
    'not json at all',
    '',
    '   ',
    evt('session.start', { sessionId: 'mal-1' }),
    '{"incomplete":',
    evt('user.message', { content: 'Valid' }, '2026-01-01T00:00:01.000Z'),
  ];

  const stats = parseCopilotTranscript(lines, countChars);
  assertEq(stats.sessionId, 'mal-1', 'session parsed despite malformed lines');
  assertEq(stats.messageCount, 1, 'only valid messages counted');
  assertEq(stats.inputTokens, 'Valid'.length, 'tokens from valid message only');
}

// ── Test 10: empty transcript ───────────────────────────────────────
console.log('\nTest 10: empty transcript');
{
  const stats = parseCopilotTranscript([], countChars);
  assertEq(stats.totalTokens, 0, 'empty transcript = 0 total');
  assertEq(stats.messageCount, 0, 'no messages');
  assertEq(stats.sessionId, '', 'no session id');
  assertEq(stats.sessionName, '', 'no session name');

  const empty = emptyCopilotStats();
  assertEq(empty.totalTokens, 0, 'emptyCopilotStats has 0 tokens');
}

console.log('\n=== All copilotTokenAccuracy tests passed! ===\n');
