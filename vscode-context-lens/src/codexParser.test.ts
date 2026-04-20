/**
 * codexParser.test.ts — unit tests for Codex CLI rollout parsing.
 */
import {
  parseCodexRollout,
  parseCodexSessionSummary,
  parseCodexSessionMeta,
} from './codexParser';

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

console.log('\n=== codexParser tests ===\n');

// ── Helpers ───────────────────────────────────────────────────────────────

function mkSessionMeta(id: string, cwd: string, ts: string): string {
  return JSON.stringify({
    timestamp: ts,
    type: 'session_meta',
    payload: { id, cwd },
  });
}

function mkUserMessage(text: string, ts: string): string {
  return JSON.stringify({
    timestamp: ts,
    type: 'response_item',
    payload: {
      type: 'message',
      role: 'user',
      content: [{ type: 'output_text', text }],
    },
  });
}

function mkAssistantMessage(text: string, ts: string): string {
  return JSON.stringify({
    timestamp: ts,
    type: 'response_item',
    payload: {
      type: 'message',
      role: 'assistant',
      content: [{ type: 'output_text', text }],
    },
  });
}

function mkFunctionCall(name: string, ts: string): string {
  return JSON.stringify({
    timestamp: ts,
    type: 'response_item',
    payload: { type: 'function_call', name, arguments: '{}' },
  });
}

function mkTokenCount(ts: string, input: number, output: number, cached: number, reasoning: number): string {
  return JSON.stringify({
    timestamp: ts,
    type: 'event_msg',
    payload: {
      type: 'token_count',
      info: {
        total_token_usage: {
          input_tokens: input,
          cached_input_tokens: cached,
          output_tokens: output,
          reasoning_output_tokens: reasoning,
          total_tokens: input + output + reasoning,
        },
      },
    },
  });
}

function mkTurnContext(model: string, ts: string): string {
  return JSON.stringify({
    timestamp: ts,
    type: 'turn_context',
    payload: { model },
  });
}

function mkThreadNameUpdated(name: string, threadId: string, ts: string): string {
  return JSON.stringify({
    timestamp: ts,
    type: 'event_msg',
    payload: {
      type: 'thread_name_updated',
      thread_id: threadId,
      thread_name: name,
    },
  });
}

// ── Test 1: basic rollout with real token counts ──────────────────────────
console.log('Test 1: basic rollout with real token counts');
{
  const lines = [
    mkSessionMeta('sess-xyz', '/home/user/project', '2026-04-16T10:00:00Z'),
    mkTurnContext('o3-mini', '2026-04-16T10:00:01Z'),
    mkUserMessage('Fix the bug in main.py', '2026-04-16T10:00:02Z'),
    mkAssistantMessage('Done, here is the fix.', '2026-04-16T10:00:05Z'),
    mkFunctionCall('apply_diff', '2026-04-16T10:00:06Z'),
    mkTokenCount('2026-04-16T10:00:07Z', 1000, 500, 200, 100),
  ];
  const s = parseCodexRollout(lines);
  assertEq(s.tool, 'codex', 'tool is codex');
  assertEq(s.sessionId, 'sess-xyz', 'session id extracted');
  assertEq(s.model, 'o3-mini', 'model extracted');
  assertEq(s.messageCount, 2, '2 messages');
  assertEq(s.toolCallCount, 1, '1 tool call');
  assert(s.tokensFromUsage, 'tokens from real usage');
  assertEq(s.inputTokens, 800, 'input = 1000 - 200 cached');
  assertEq(s.outputTokens, 500, 'output');
  assertEq(s.reasoningTokens, 300, 'reasoning = 200 cached + 100 reasoning');
  assertEq(s.totalTokens, 1000 + 500 + 100, 'total from cumulative');
  assertEq(s.sessionName, 'Fix the bug in main.py', 'session name from user intent');
  assert(s.lastMessageTs > 0, 'lastMessageTs present');
}

// ── Test 2: environment_context is filtered for session name ──────────────
console.log('\nTest 2: environment_context filtered from session name');
{
  const lines = [
    mkSessionMeta('sess-2', '/tmp', '2026-04-16T10:00:00Z'),
    JSON.stringify({
      timestamp: '2026-04-16T10:00:01Z',
      type: 'response_item',
      payload: {
        type: 'message',
        role: 'user',
        content: [{ type: 'output_text', text: '<environment_context>lots of context here</environment_context>' }],
      },
    }),
    mkUserMessage('Do the real task', '2026-04-16T10:00:02Z'),
  ];
  const s = parseCodexRollout(lines);
  assertEq(s.sessionName, 'Do the real task', 'env context skipped, real msg used');
  assertEq(s.messageCount, 1, 'env context msg not counted');
}

// ── Test 3: "My request for Codex:" extraction ───────────────────────────
console.log('\nTest 3: extracts user intent after "My request for Codex:"');
{
  const lines = [
    mkSessionMeta('sess-3', '/tmp', '2026-04-16T10:00:00Z'),
    JSON.stringify({
      timestamp: '2026-04-16T10:00:01Z',
      type: 'response_item',
      payload: {
        type: 'message',
        role: 'user',
        content: [{
          type: 'output_text',
          text: '# Context from my IDE setup:\nstuff\n## My request for Codex:\nPlease fix the tests',
        }],
      },
    }),
  ];
  const s = parseCodexRollout(lines);
  assertEq(s.messageCount, 1, 'context-prefixed prompt still counts as a user message');
  assertEq(s.sessionName, 'Please fix the tests', 'session name extracted from wrapped IDE prompt');
}

// ── Test 4: no token_count → estimated ────────────────────────────────────
console.log('\nTest 4: no token_count events → estimated');
{
  const lines = [
    mkSessionMeta('sess-4', '/tmp', '2026-04-16T10:00:00Z'),
    mkUserMessage('Hello', '2026-04-16T10:00:01Z'),
    mkAssistantMessage('Hi', '2026-04-16T10:00:02Z'),
  ];
  const s = parseCodexRollout(lines);
  assert(!s.tokensFromUsage, 'no real usage');
  assertEq(s.totalTokens, 0, 'zero tokens (no estimate for codex messages without usage)');
}

// ── Test 5: thread_name_updated overrides raw prompt title ────────────────
console.log('\nTest 5: thread_name_updated overrides raw prompt title');
{
  const lines = [
    mkSessionMeta('sess-5', '/tmp', '2026-04-16T10:00:00Z'),
    mkUserMessage('do codex aqui, não esta pegando a sessão correta', '2026-04-16T10:00:01Z'),
    mkThreadNameUpdated('Corrige sessão e overview', 'sess-5', '2026-04-16T10:00:02Z'),
    mkAssistantMessage('Vou revisar a lógica.', '2026-04-16T10:00:03Z'),
  ];
  const s = parseCodexRollout(lines);
  assertEq(s.sessionName, 'Corrige sessão e overview', 'thread name preferred over raw prompt');
  assertEq(s.messageCount, 2, 'messages still counted normally');
}

// ── Test 6: empty/garbage lines ───────────────────────────────────────────
console.log('\nTest 6: empty and garbage lines handled');
{
  const lines = ['', '   ', 'not json', '{"partial'];
  const s = parseCodexRollout(lines);
  assertEq(s.messageCount, 0, 'no messages');
  assertEq(s.totalTokens, 0, 'zero tokens');
}

// ── Test 7: parseCodexSessionMeta ─────────────────────────────────────────
console.log('\nTest 7: parseCodexSessionMeta');
{
  const line = mkSessionMeta('id-abc', '/home/user/proj', '2026-04-16T10:00:00Z');
  const meta = parseCodexSessionMeta(line);
  assert(meta !== null, 'meta parsed');
  assertEq(meta!.id, 'id-abc', 'id');
  assertEq(meta!.cwd, '/home/user/proj', 'cwd');
}

// ── Test 8: parseCodexSessionMeta rejects non-meta lines ──────────────────
console.log('\nTest 8: parseCodexSessionMeta rejects non-meta');
{
  const line = mkUserMessage('hello', '2026-04-16T10:00:00Z');
  const meta = parseCodexSessionMeta(line);
  assertEq(meta, null, 'non session_meta returns null');
  assertEq(parseCodexSessionMeta(''), null, 'empty string returns null');
  assertEq(parseCodexSessionMeta('garbage'), null, 'garbage returns null');
}

// ── Test 9: parseCodexSessionSummary ──────────────────────────────────────
console.log('\nTest 9: parseCodexSessionSummary');
{
  const content = [
    mkSessionMeta('sess-s1', '/tmp', '2026-04-16T10:00:00Z'),
    mkUserMessage('Explain the code', '2026-04-16T10:00:01Z'),
    mkAssistantMessage('Here is the explanation', '2026-04-16T10:00:03Z'),
    mkTokenCount('2026-04-16T10:00:04Z', 800, 300, 100, 50),
  ].join('\n');
  const sum = parseCodexSessionSummary(content);
  assertEq(sum.sessionId, 'sess-s1', 'session id');
  assertEq(sum.messageCount, 2, '2 messages');
  assertEq(sum.totalTokens, 800 + 300 + 50, 'total from token_count (input+output+reasoning)');
  assert(sum.lastMessageTs > 0, 'timestamp present');
  assertEq(sum.name, 'Explain the code', 'name from user intent');
}

// ── Test 10: thread_name_updated also drives summary names ────────────────
console.log('\nTest 10: thread_name_updated also drives summary names');
{
  const content = [
    mkSessionMeta('sess-s1b', '/tmp', '2026-04-16T10:00:00Z'),
    mkUserMessage('raw prompt title', '2026-04-16T10:00:01Z'),
    mkThreadNameUpdated('Readable task title', 'sess-s1b', '2026-04-16T10:00:02Z'),
    mkTokenCount('2026-04-16T10:00:03Z', 300, 120, 20, 10),
  ].join('\n');
  const sum = parseCodexSessionSummary(content);
  assertEq(sum.name, 'Readable task title', 'summary prefers thread name');
  assertEq(sum.totalTokens, 300 + 120 + 10, 'summary total tokens still parsed');
}

// ── Test 11: multiple token_count events → last one wins (cumulative) ─────
console.log('\nTest 11: multiple token_count events - last wins');
{
  const lines = [
    mkSessionMeta('sess-cum', '/tmp', '2026-04-16T10:00:00Z'),
    mkUserMessage('First message', '2026-04-16T10:00:01Z'),
    mkTokenCount('2026-04-16T10:00:02Z', 100, 50, 10, 5),
    mkUserMessage('Second message', '2026-04-16T10:00:03Z'),
    mkTokenCount('2026-04-16T10:00:04Z', 300, 150, 30, 15),
  ];
  const s = parseCodexRollout(lines);
  // Last token_count should be used (cumulative)
  assertEq(s.inputTokens, 300 - 30, 'input from last token_count (minus cached)');
  assertEq(s.outputTokens, 150, 'output from last token_count');
  assertEq(s.reasoningTokens, 30 + 15, 'reasoning from last token_count');
}

// ── Test 12: large session_meta with base_instructions (real-world) ───────
console.log('\nTest 12: large session_meta with embedded base_instructions');
{
  // Real Codex rollouts embed base_instructions text (~10-15 KB) inside the
  // session_meta payload. The parser must handle this.
  const bigText = 'x'.repeat(12000);
  const largeMeta = JSON.stringify({
    timestamp: '2026-04-16T10:00:00Z',
    type: 'session_meta',
    payload: {
      id: 'big-session',
      cwd: '/home/user/project',
      originator: 'codex_vscode',
      base_instructions: { text: bigText },
    },
  });
  assert(largeMeta.length > 12000, 'meta line is large');
  const meta = parseCodexSessionMeta(largeMeta);
  assert(meta !== null, 'large meta parsed');
  assertEq(meta!.id, 'big-session', 'id extracted from large meta');
  assertEq(meta!.cwd, '/home/user/project', 'cwd extracted from large meta');

  // Also test as part of full rollout
  const lines = [
    largeMeta,
    mkUserMessage('Fix something', '2026-04-16T10:00:01Z'),
    mkTokenCount('2026-04-16T10:00:02Z', 500, 200, 50, 25),
  ];
  const s = parseCodexRollout(lines);
  assertEq(s.sessionId, 'big-session', 'session id from large meta');
  assert(s.tokensFromUsage, 'tokens from real usage');
  assertEq(s.messageCount, 1, '1 user message');
}

// ── Test 13: session summary also extracts wrapped IDE prompt ─────────────
console.log('\nTest 13: session summary extracts wrapped IDE prompt');
{
  const content = [
    mkSessionMeta('sess-s2', '/tmp', '2026-04-16T10:00:00Z'),
    JSON.stringify({
      timestamp: '2026-04-16T10:00:01Z',
      type: 'response_item',
      payload: {
        type: 'message',
        role: 'user',
        content: [{
          type: 'output_text',
          text: '# Context from my IDE setup:\nstuff\n## My request for Codex:\nShow the failing tests',
        }],
      },
    }),
    mkTokenCount('2026-04-16T10:00:02Z', 120, 40, 10, 5),
  ].join('\n');
  const sum = parseCodexSessionSummary(content);
  assertEq(sum.messageCount, 1, 'wrapped IDE prompt counted in summary');
  assertEq(sum.name, 'Show the failing tests', 'summary name extracted from wrapped IDE prompt');
}

// ── Test 14: event_msg user_message + agent_message handling ──────────────
console.log('\nTest 14: event_msg user_message + agent_message handling');
{
  const lines = [
    mkSessionMeta('sess-new', '/project', '2026-04-17T10:00:00Z'),
    // response_item user messages with env_context (should be filtered)
    mkUserMessage('<environment_context>\n  <cwd>/project</cwd>\n</environment_context>', '2026-04-17T10:00:01Z'),
    mkUserMessage('# Context from my IDE setup:\n## Active file: foo.ts\n## My request for Codex:\nfix the bug', '2026-04-17T10:00:02Z'),
    // event_msg user_message (newer Codex format)
    JSON.stringify({
      timestamp: '2026-04-17T10:00:03Z',
      type: 'event_msg',
      payload: { type: 'user_message', message: '# Context from my IDE setup:\nstuff\n## My request for Codex:\nadd error handling' },
    }),
    // event_msg agent_message
    JSON.stringify({
      timestamp: '2026-04-17T10:00:04Z',
      type: 'event_msg',
      payload: { type: 'agent_message', message: 'I will add try/catch blocks.' },
    }),
    // event_msg exec_command_end and patch_apply_end
    JSON.stringify({
      timestamp: '2026-04-17T10:00:05Z',
      type: 'event_msg',
      payload: { type: 'exec_command_end', exit_code: 0 },
    }),
    JSON.stringify({
      timestamp: '2026-04-17T10:00:06Z',
      type: 'event_msg',
      payload: { type: 'patch_apply_end', success: true },
    }),
    mkTokenCount('2026-04-17T10:00:07Z', 5000, 2000, 500, 100),
  ];
  const s = parseCodexRollout(lines);
  // env_context user (0 intent) + IDE-wrapped user (1 intent) + event_msg user_message (1 intent) + agent_message (1)
  assertEq(s.messageCount, 3, 'messageCount includes event_msg user+agent');
  // function_call (0) + exec_command_end (1) + patch_apply_end (1)
  assertEq(s.toolCallCount, 2, 'toolCallCount includes exec_command_end + patch_apply_end');
  assertEq(s.sessionName, 'fix the bug', 'first user intent from response_item');
}

// ── Test 15: event_msg user_message drives session name when no response_item intent ──
console.log('\nTest 15: event_msg user_message drives session name when no response_item intent');
{
  const lines = [
    mkSessionMeta('sess-evmsg', '/project2', '2026-04-17T11:00:00Z'),
    // Only env_context response_items (filtered out)
    mkUserMessage('<environment_context>\n  <cwd>/project2</cwd>\n</environment_context>', '2026-04-17T11:00:01Z'),
    // event_msg user_message has the actual request
    JSON.stringify({
      timestamp: '2026-04-17T11:00:02Z',
      type: 'event_msg',
      payload: { type: 'user_message', message: '# Context from my IDE setup:\nstuff\n## My request for Codex:\nconsegie ler os textos sessão' },
    }),
    mkAssistantMessage('OK, fixing it.', '2026-04-17T11:00:03Z'),
    mkTokenCount('2026-04-17T11:00:04Z', 1000, 400, 100, 50),
  ];
  const s = parseCodexRollout(lines);
  assertEq(s.sessionName, 'consegie ler os textos sessão', 'session name from event_msg user_message');
  assertEq(s.messageCount, 2, 'counts: event_msg user + assistant response_item (env_context filtered)');
}

// ── Test 16: turn_aborted and permissions filtered from intent ─────────────
console.log('\nTest 16: turn_aborted and permissions filtered from intent');
{
  const lines = [
    mkSessionMeta('sess-abort', '/proj', '2026-04-17T12:00:00Z'),
    mkUserMessage('<turn_aborted>\nThe user interrupted the previous turn', '2026-04-17T12:00:01Z'),
    mkUserMessage('<permissions instructions>\nFilesystem sandboxing...', '2026-04-17T12:00:02Z'),
    mkUserMessage('Real request here', '2026-04-17T12:00:03Z'),
    mkTokenCount('2026-04-17T12:00:04Z', 200, 100, 0, 0),
  ];
  const s = parseCodexRollout(lines);
  assertEq(s.sessionName, 'Real request here', 'turn_aborted and permissions skipped');
  assertEq(s.messageCount, 1, 'only real user message counted');
}

// ── Test 17: parseCodexSessionSummary handles event_msg user/agent ────────
console.log('\nTest 17: parseCodexSessionSummary handles event_msg user/agent');
{
  const content = [
    mkSessionMeta('sess-sum-ev', '/proj', '2026-04-17T13:00:00Z'),
    mkUserMessage('<environment_context>\n  <cwd>/proj</cwd>\n</environment_context>', '2026-04-17T13:00:01Z'),
    JSON.stringify({
      timestamp: '2026-04-17T13:00:02Z',
      type: 'event_msg',
      payload: { type: 'user_message', message: '# Context from my IDE setup:\nstuff\n## My request for Codex:\nrefactor the module' },
    }),
    JSON.stringify({
      timestamp: '2026-04-17T13:00:03Z',
      type: 'event_msg',
      payload: { type: 'agent_message', message: 'Done.' },
    }),
    mkTokenCount('2026-04-17T13:00:04Z', 3000, 1500, 200, 50),
  ].join('\n');
  const sum = parseCodexSessionSummary(content);
  assertEq(sum.messageCount, 2, 'summary counts event_msg user + agent');
  assertEq(sum.name, 'refactor the module', 'summary name from event_msg user_message');
}

console.log('\n=== All codexParser tests passed! ===\n');
