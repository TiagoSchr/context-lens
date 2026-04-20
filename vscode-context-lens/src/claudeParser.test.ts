/**
 * claudeParser.test.ts — unit tests for Claude Code transcript parsing.
 */
import {
  parseClaudeTranscript,
  parseClaudeSessionSummary,
  sanitizeProjectCwd,
} from './claudeParser';

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

console.log('\n=== claudeParser tests ===\n');

// ── Helpers ───────────────────────────────────────────────────────────────

function mkUser(text: string, ts: string): string {
  return JSON.stringify({
    type: 'user',
    timestamp: ts,
    message: { role: 'user', content: [{ type: 'text', text }] },
  });
}

function mkAssistant(text: string, ts: string, usage?: Record<string, number>, model?: string): string {
  return JSON.stringify({
    type: 'assistant',
    timestamp: ts,
    message: {
      role: 'assistant',
      model: model ?? 'claude-sonnet-4-5-20250929',
      content: [{ type: 'text', text }],
      ...(usage ? { usage } : {}),
    },
  });
}

function mkSummary(text: string): string {
  return JSON.stringify({ type: 'summary', summary: text });
}

function mkMeta(sessionId: string): string {
  return JSON.stringify({ sessionId, type: 'user', isMeta: true });
}

// ── Test 1: basic transcript with real usage ──────────────────────────────
console.log('Test 1: basic transcript with real usage');
{
  const lines = [
    mkMeta('session-abc'),
    mkUser('Fix the bug', '2026-04-16T10:00:00Z'),
    mkAssistant('Done!', '2026-04-16T10:00:05Z', {
      input_tokens: 500,
      output_tokens: 200,
      cache_read_input_tokens: 100,
      cache_creation_input_tokens: 50,
    }),
  ];
  const s = parseClaudeTranscript(lines);
  assertEq(s.tool, 'claude', 'tool is claude');
  assertEq(s.sessionId, 'session-abc', 'session id extracted');
  assertEq(s.messageCount, 2, '2 messages (user + assistant)');
  assert(s.tokensFromUsage, 'tokens come from usage');
  // input = estimateTokens("Fix the bug") + 500 (assistant turn usage.input_tokens)
  const estimatedUser = Math.ceil('Fix the bug'.length / 4);
  assertEq(s.inputTokens, estimatedUser + 500, 'inputTokens = user estimate + assistant usage.input');
  assertEq(s.outputTokens, 200, 'outputTokens from usage');
  assertEq(s.reasoningTokens, 150, 'cache read + cache creation = 150');
  assertEq(s.totalTokens, s.inputTokens + s.outputTokens + s.reasoningTokens, 'total = sum');
  assertEq(s.model, 'claude-sonnet-4-5-20250929', 'model extracted');
  assertEq(s.sessionName, 'Fix the bug', 'session name from first user message');
  assert(s.lastMessageTs > 0, 'lastMessageTs > 0');
}

// ── Test 2: summary overrides session name ────────────────────────────────
console.log('\nTest 2: summary text used for session name');
{
  const lines = [
    mkUser('Help me refactor the service layer', '2026-04-16T10:00:00Z'),
    mkSummary('Service layer refactoring session'),
    mkAssistant('All done', '2026-04-16T10:00:10Z'),
  ];
  const s = parseClaudeTranscript(lines);
  assertEq(s.sessionName, 'Service layer refactoring session', 'summary used as name');
  assertEq(s.messageCount, 2, '2 messages (user + assistant)');
}

// ── Test 3: empty lines and garbage are skipped ───────────────────────────
console.log('\nTest 3: empty/garbage lines are skipped');
{
  const lines = [
    '',
    '  ',
    'not json at all',
    mkUser('hello', '2026-04-16T10:00:00Z'),
    '{"broken json',
  ];
  const s = parseClaudeTranscript(lines);
  assertEq(s.messageCount, 1, '1 valid message');
}

// ── Test 4: tool_use counted ──────────────────────────────────────────────
console.log('\nTest 4: tool_use counted');
{
  const lines = [
    mkUser('Read the file', '2026-04-16T10:00:00Z'),
    JSON.stringify({
      type: 'assistant',
      timestamp: '2026-04-16T10:00:02Z',
      message: {
        role: 'assistant',
        content: [
          { type: 'tool_use', id: 'call1', name: 'read_file', input: { path: 'foo.ts' } },
          { type: 'tool_use', id: 'call2', name: 'grep', input: { q: 'bar' } },
          { type: 'text', text: 'Here are the results' },
        ],
        usage: { input_tokens: 100, output_tokens: 300 },
      },
    }),
  ];
  const s = parseClaudeTranscript(lines);
  assertEq(s.toolCallCount, 2, '2 tool calls detected');
}

// ── Test 5: meta/sidechain lines are skipped ──────────────────────────────
console.log('\nTest 5: meta and sidechain lines skipped');
{
  const lines = [
    JSON.stringify({ type: 'user', isMeta: true, message: { role: 'user', content: 'meta' } }),
    JSON.stringify({ type: 'assistant', isSidechain: true, message: { role: 'assistant', content: 'side' } }),
    mkUser('real message', '2026-04-16T10:00:00Z'),
  ];
  const s = parseClaudeTranscript(lines);
  assertEq(s.messageCount, 1, 'only the real message counted');
}

// ── Test 6: fallback to estimate when no usage ────────────────────────────
console.log('\nTest 6: heuristic estimate when no usage');
{
  const lines = [
    mkUser('hello world', '2026-04-16T10:00:00Z'),
    mkAssistant('hi there friend', '2026-04-16T10:00:02Z'),
  ];
  const s = parseClaudeTranscript(lines);
  assert(!s.tokensFromUsage, 'tokensFromUsage is false');
  assert(s.outputTokens > 0, 'output estimated by heuristic');
  assertEq(s.outputTokens, Math.ceil('hi there friend'.length / 4), 'output = ceil(len/4)');
}

// ── Test 7: parseClaudeSessionSummary ─────────────────────────────────────
console.log('\nTest 7: parseClaudeSessionSummary');
{
  const content = [
    JSON.stringify({ sessionId: 'sess-1', type: 'user', message: { role: 'user', content: 'First query' } }),
    mkAssistant('Answer', '2026-04-16T11:00:00Z', { input_tokens: 100, output_tokens: 50 }),
  ].join('\n');
  const sum = parseClaudeSessionSummary(content);
  assertEq(sum.sessionId, 'sess-1', 'session id');
  assertEq(sum.messageCount, 2, '2 messages');
  assert(sum.totalTokens > 0, 'totalTokens > 0');
  assert(sum.lastMessageTs > 0, 'timestamp present');
}

// ── Test 8: sanitizeProjectCwd ────────────────────────────────────────────
console.log('\nTest 8: sanitizeProjectCwd');
{
  const variants = sanitizeProjectCwd('C:\\Users\\Foo\\my project');
  assert(variants.length >= 2, 'at least 2 variants');
  assert(variants.includes('C--Users-Foo-my-project'), 'primary variant with dashes');
  assert(variants.some(v => !v.startsWith('-') && !v.endsWith('-')), 'collapsed variant without leading/trailing dash');
}

// ── Test 9: sanitizeProjectCwd with forward slashes ───────────────────────
console.log('\nTest 9: sanitizeProjectCwd with forward slashes');
{
  const variants = sanitizeProjectCwd('/home/user/projects/my-app');
  assert(variants.some(v => v.includes('home')), 'contains home');
  assert(variants.some(v => v.includes('my-app')), 'contains my-app');
}

console.log('\n=== All claudeParser tests passed! ===\n');
