import {
  parseChatSessionCustomTitle,
  parseChatSessionModel,
  parseCopilotTranscript,
  parseSessionSummary,
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

console.log('\n=== copilotParser tests ===\n');

const countChars = (text: string): number => text.length;

// ── Test 1: transcript parsing ────────────────────────────────────────────
console.log('Test 1: transcript parsing');
const transcriptLines = [
  JSON.stringify({
    type: 'session.start',
    data: { sessionId: 'sess-123' },
    id: '1',
    timestamp: '2026-04-16T00:00:00.000Z',
    parentId: null,
  }),
  JSON.stringify({
    type: 'user.message',
    data: { content: 'Dúvida sobre tokens\nsegunda linha' },
    id: '2',
    timestamp: '2026-04-16T00:00:01.000Z',
    parentId: '1',
  }),
  JSON.stringify({
    type: 'assistant.turn_start',
    data: { turnId: 'turn-1' },
    id: '3',
    timestamp: '2026-04-16T00:00:02.000Z',
    parentId: '2',
  }),
  JSON.stringify({
    type: 'tool.execution_start',
    data: {
      toolName: 'read_file',
      arguments: { path: 'src/app.ts' },
    },
    id: '4',
    timestamp: '2026-04-16T00:00:03.000Z',
    parentId: '3',
  }),
  JSON.stringify({
    type: 'assistant.message',
    data: {
      content: 'Resposta pronta',
      reasoningText: 'Pensando',
      toolRequests: [
        {
          toolCallId: 'tool-1',
          name: 'grep_search',
          arguments: '{"query":"token"}',
          type: 'function',
        },
      ],
    },
    id: '5',
    timestamp: '2026-04-16T00:00:04.000Z',
    parentId: '4',
  }),
];

const stats = parseCopilotTranscript(transcriptLines, countChars);
const userContent = 'Dúvida sobre tokens\nsegunda linha';
const toolInput = JSON.stringify({ path: 'src/app.ts' });
const assistantContent = 'Resposta pronta';
const reasoning = 'Pensando';
const toolRequestArgs = '{"query":"token"}';

assertEq(stats.sessionId, 'sess-123', 'sessionId extracted');
assertEq(stats.sessionName, 'Dúvida sobre tokens', 'sessionName comes from first user line');
assertEq(stats.messageCount, 2, 'messageCount counts user + assistant messages');
assertEq(stats.turnCount, 1, 'turnCount counts assistant.turn_start');
assertEq(stats.toolCallCount, 1, 'toolCallCount counts assistant tool requests');
assertEq(stats.inputTokens, userContent.length + toolInput.length, 'inputTokens include user + tool execution args');
assertEq(stats.outputTokens, assistantContent.length, 'outputTokens include only assistant content (tool args counted in tool.execution_start)');
assertEq(stats.reasoningTokens, reasoning.length, 'reasoningTokens counted separately');
assertEq(stats.totalTokens, stats.inputTokens + stats.outputTokens + stats.reasoningTokens, 'totalTokens is the full sum');
assertEq(stats.recentMessages.length, 3, 'recentMessages include user, tool, assistant');
assertEq(stats.recentMessages[0].role, 'assistant', 'recentMessages are newest first');
assertEq(stats.lastMessageTs, Date.parse('2026-04-16T00:00:04.000Z') / 1000, 'lastMessageTs uses newest message');

// ── Test 2: session summary for history ───────────────────────────────────
console.log('\nTest 2: session summary');
const summary = parseSessionSummary(transcriptLines.join('\n'), countChars);
assertEq(summary.sessionId, 'sess-123', 'summary sessionId extracted');
assertEq(summary.name, 'Dúvida sobre tokens', 'summary name extracted from first user message');
assertEq(summary.messageCount, 2, 'summary messageCount counts user + assistant messages');
assertEq(
  summary.totalTokens,
  userContent.length + assistantContent.length + reasoning.length,
  'summary totalTokens uses transcript content for history display',
);
assert(summary.lastMessageTs > 0, 'summary lastMessageTs populated');
assertEq(summary.active, false, 'summary active defaults to false');

// ── Test 3: chatSessions custom title override ────────────────────────────
console.log('\nTest 3: chatSessions custom title');
const chatSessionContent = [
  JSON.stringify({
    kind: 0,
    v: {
      sessionId: 'sess-123',
      inputState: { inputText: '' },
    },
  }),
  JSON.stringify({
    kind: 1,
    k: ['customTitle'],
    v: 'Dúvida sobre aumento de tokens em tempo real',
  }),
].join('\n');
assertEq(
  parseChatSessionCustomTitle(chatSessionContent),
  'Dúvida sobre aumento de tokens em tempo real',
  'customTitle is preferred when present in chatSessions metadata',
);

// ── parseChatSessionModel ─────────────────────────────────────────

{
  const withModel = JSON.stringify({
    kind: 0,
    v: {
      version: 3,
      sessionId: 'test-session',
      inputState: {
        selectedModel: {
          identifier: 'copilot/claude-opus-4.6',
          metadata: {
            name: 'Claude Opus 4.6',
            family: 'claude-opus-4.6',
            vendor: 'copilot',
          },
        },
      },
    },
  });
  assertEq(
    parseChatSessionModel(withModel),
    'Claude Opus 4.6',
    'parseChatSessionModel extracts metadata.name from kind=0 snapshot',
  );
}

{
  const withIdentifierOnly = JSON.stringify({
    kind: 0,
    v: {
      version: 3,
      sessionId: 'test-session',
      inputState: {
        selectedModel: {
          identifier: 'copilot/gpt-4o',
        },
      },
    },
  });
  assertEq(
    parseChatSessionModel(withIdentifierOnly),
    'gpt-4o',
    'parseChatSessionModel falls back to identifier when metadata.name is absent',
  );
}

assertEq(
  parseChatSessionModel(''),
  '',
  'parseChatSessionModel returns empty string for empty input',
);

assertEq(
  parseChatSessionModel('{}'),
  '',
  'parseChatSessionModel returns empty string when no model data',
);

// CopilotStats.model field exists
{
  const lines = [
    JSON.stringify({ type: 'session.start', data: { sessionId: 'x' }, id: '1', timestamp: '2026-01-01T00:00:00Z', parentId: null }),
    JSON.stringify({ type: 'user.message', data: { content: 'hello' }, id: '2', timestamp: '2026-01-01T00:00:01Z', parentId: '1' }),
  ];
  const stats = parseCopilotTranscript(lines);
  assertEq(stats.model, '', 'CopilotStats.model defaults to empty string');
}

console.log('\n=== All tests passed! ===\n');
