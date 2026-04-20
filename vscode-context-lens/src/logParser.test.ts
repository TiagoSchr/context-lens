import { parseLensData, emptyStats, fmtK, fmtTime } from './logParser';

function assert(condition: boolean, msg: string): void {
  if (!condition) { throw new Error('FAIL: ' + msg); }
  console.log('  \u2713 ' + msg);
}

function assertClose(a: number, b: number, tolerance: number, msg: string): void {
  if (Math.abs(a - b) > tolerance) { throw new Error(`FAIL: ${msg} (got ${a}, expected ~${b})`); }
  console.log('  \u2713 ' + msg);
}

function assertEq<T>(actual: T, expected: T, msg: string): void {
  if (actual !== expected) { throw new Error(`FAIL: ${msg} (got ${JSON.stringify(actual)}, expected ${JSON.stringify(expected)})`); }
  console.log('  \u2713 ' + msg);
}

console.log('\n=== logParser tests ===\n');

// ── Test 1: emptyStats ──────────────────────────────────────────────────────
console.log('Test 1: emptyStats');
const empty = emptyStats();
assert(empty.enabled === true, 'enabled is true');
assert(empty.indexed === false, 'indexed is false');
assertEq(empty.tokenBudget, 8000, 'budget is 8000');
assertEq(empty.totalQueries, 0, 'totalQueries is 0');
assertEq(empty.sessionQueries, 0, 'sessionQueries is 0');
assert(Object.keys(empty.byTask).length === 0, 'byTask is empty');
assertEq(empty.lastQueries.length, 0, 'lastQueries is empty');

// ── Test 2: parseLensData with no data ──────────────────────────────────────
console.log('\nTest 2: no data');
const noData = parseLensData('', {}, null);
assert(noData.enabled === true, 'enabled default true');
assert(noData.indexed === false, 'not indexed');
assertEq(noData.totalQueries, 0, '0 queries');
assertEq(noData.totalTokensSaved, 0, '0 saved');

// ── Test 3: config disabled ─────────────────────────────────────────────────
console.log('\nTest 3: config disabled');
const disabled = parseLensData('', { enabled: false, token_budget: 4000 }, null);
assert(disabled.enabled === false, 'disabled');
assertEq(disabled.tokenBudget, 4000, 'custom budget 4000');

// ── Test 4: stats.json only ─────────────────────────────────────────────────
console.log('\nTest 4: stats.json');
const withStats = parseLensData('', {}, {
  files: 50, symbols: 300, db_kb: 200,
  last_indexed: 1700000000, token_budget: 8000,
  project_tokens_total: 100000, by_language: { python: 40, javascript: 10 },
});
assert(withStats.indexed === true, 'indexed');
assertEq(withStats.files, 50, 'files=50');
assertEq(withStats.symbols, 300, 'symbols=300');
assertEq(withStats.projectTokensTotal, 100000, 'projectTokensTotal');
assertEq(withStats.dbKb, 200, 'dbKb=200');
assertEq(withStats.lastIndexed, 1700000000, 'lastIndexed');

// ── Test 5: retrieval log entries ───────────────────────────────────────────
console.log('\nTest 5: retrieval log');
const logLines = [
  JSON.stringify({ ts: 1700000100, event: 'retrieval', task: 'explain', tokens_used: 5000, tokens_raw: 100000, budget: 8000 }),
  JSON.stringify({ ts: 1700000200, event: 'retrieval', task: 'bugfix', tokens_used: 6000, tokens_raw: 100000, budget: 8000 }),
  JSON.stringify({ ts: 1700000300, event: 'retrieval', task: 'navigate', tokens_used: 1500, tokens_raw: 100000, budget: 8000 }),
  JSON.stringify({ ts: 1699999000, event: 'intent', task: 'explain', confidence: 0.9 }), // should be ignored
  JSON.stringify({ ts: 1700000400, event: 'retrieval', task: 'explain', tokens_used: 4000, tokens_raw: 100000, budget: 8000 }),
].join('\n');

const stats = parseLensData(logLines, { token_budget: 8000 }, {
  files: 50, symbols: 300, db_kb: 200,
  last_indexed: 1700000050, token_budget: 8000,
  project_tokens_total: 100000, by_language: {},
});
assertEq(stats.totalQueries, 4, '4 retrievals (intent ignored)');
assertEq(stats.totalTokensUsed, 16500, 'total used = 5000+6000+1500+4000');
assertEq(stats.totalTokensRaw, 400000, 'total raw = 4*100000');
assertEq(stats.totalTokensSaved, 383500, 'total saved');
assertClose(stats.avgSavingPct, 95.875, 0.1, 'avg saving ~96%');

// Session = since last_indexed (1700000050)
assertEq(stats.sessionQueries, 4, 'session: all 4 after last_indexed');

// By task
assert(stats.byTask['explain'] !== undefined, 'byTask has explain');
assertEq(stats.byTask['explain'].count, 2, 'explain count=2');
assertEq(stats.byTask['bugfix'].count, 1, 'bugfix count=1');
assertEq(stats.byTask['navigate'].count, 1, 'navigate count=1');

// Last queries (newest first, max 6)
assertEq(stats.lastQueries.length, 4, 'lastQueries length=4');
assertEq(stats.lastQueries[0].task, 'explain', 'newest = explain');
assertEq(stats.lastQueries[0].tokensUsed, 4000, 'newest used=4000');

// ── Test 6: fmtK ────────────────────────────────────────────────────────────
console.log('\nTest 6: fmtK');
assertEq(fmtK(0), '0', 'fmtK(0)');
assertEq(fmtK(500), '500', 'fmtK(500)');
assertEq(fmtK(1200), '1.2k', 'fmtK(1200)');
assertEq(fmtK(1500000), '1.5M', 'fmtK(1500000)');
assertEq(fmtK(999), '999', 'fmtK(999)');
assertEq(fmtK(1000), '1.0k', 'fmtK(1000)');

// ── Test 7: fmtTime ─────────────────────────────────────────────────────────
console.log('\nTest 7: fmtTime');
assertEq(fmtTime(0), 'never', 'fmtTime(0)');
assert(typeof fmtTime(1700000000) === 'string', 'returns string');
assert(fmtTime(1700000000).includes('/'), 'has date separator');
assert(fmtTime(1700000000).length >= 11, 'reasonable length');

// ── Test 8: malformed log lines skipped ─────────────────────────────────────
console.log('\nTest 8: malformed lines');
const badLog = 'not json\n{invalid\n' + JSON.stringify({ ts: 1, event: 'retrieval', task: 'explain', tokens_used: 100, tokens_raw: 1000, budget: 1000 });
const badResult = parseLensData(badLog, {}, { files: 1, symbols: 1, db_kb: 1, last_indexed: 0, token_budget: 1000, project_tokens_total: 1000, by_language: {} });
assertEq(badResult.totalQueries, 1, '1 valid retrieval');
assertEq(badResult.totalTokensUsed, 100, 'used=100');

// ── Test 9: missing tokens_raw falls back to projectTokensTotal ─────────────
console.log('\nTest 9: tokens_raw fallback');
const noRawLog = JSON.stringify({ ts: 1, event: 'retrieval', task: 'explain', tokens_used: 500, budget: 8000 });
const noRawResult = parseLensData(noRawLog, {}, { files: 1, symbols: 1, db_kb: 1, last_indexed: 0, token_budget: 8000, project_tokens_total: 50000, by_language: {} });
assertEq(noRawResult.totalTokensRaw, 50000, 'fallback to projectTokensTotal');
assertEq(noRawResult.totalTokensSaved, 49500, 'saved = 50000 - 500');

// ── Test 10: dynamic task names (not just hardcoded 5) ──────────────────────
console.log('\nTest 10: dynamic task names');
const customTaskLog = [
  JSON.stringify({ ts: 1, event: 'retrieval', task: 'custom_task', tokens_used: 200, tokens_raw: 5000, budget: 8000 }),
  JSON.stringify({ ts: 2, event: 'retrieval', task: 'another_one', tokens_used: 300, tokens_raw: 5000, budget: 8000 }),
].join('\n');
const customResult = parseLensData(customTaskLog, {}, { files: 1, symbols: 1, db_kb: 1, last_indexed: 0, token_budget: 8000, project_tokens_total: 5000, by_language: {} });
assert(customResult.byTask['custom_task'] !== undefined, 'custom_task exists in byTask');
assert(customResult.byTask['another_one'] !== undefined, 'another_one exists in byTask');
assertEq(customResult.byTask['custom_task'].count, 1, 'custom_task count=1');

// ── Test 11: session info from session.json ──────────────────────────────
console.log('\nTest 11: session info');
const sessResult = parseLensData('', {}, null, { id: 42, name: 'myproject #3', started_at: 1700000000 });
assertEq(sessResult.sessionId, 42, 'sessionId=42');
assertEq(sessResult.sessionName, 'myproject #3', 'sessionName');

// ── Test 12: session filtering by session_id ─────────────────────────────
console.log('\nTest 12: session filtering by session_id');
const sessLog = [
  `{"ts": 100, "event": "retrieval", "task": "explain", "tokens_used": 500, "budget": 8000, "tokens_raw": 10000, "session_id": 1}`,
  `{"ts": 200, "event": "retrieval", "task": "bugfix",  "tokens_used": 600, "budget": 8000, "tokens_raw": 10000, "session_id": 1}`,
  `{"ts": 300, "event": "retrieval", "task": "explain", "tokens_used": 700, "budget": 8000, "tokens_raw": 10000, "session_id": 2}`,
  `{"ts": 400, "event": "retrieval", "task": "explain", "tokens_used": 800, "budget": 8000, "tokens_raw": 10000, "session_id": 2}`,
  `{"ts": 500, "event": "retrieval", "task": "explain", "tokens_used": 900, "budget": 8000, "tokens_raw": 10000}`,
].join('\n');
// Filter for session_id=2
const s2 = parseLensData(sessLog, {}, { files: 1, symbols: 1, db_kb: 1, last_indexed: 0, token_budget: 8000, project_tokens_total: 10000, by_language: {} }, { id: 2, name: 'proj #2', started_at: 250 });
assertEq(s2.totalQueries, 5, 'total=5');
assertEq(s2.sessionQueries, 2, 'session=2 (only session_id=2)');
assertEq(s2.sessionTokensUsed, 700 + 800, 'session used=1500');
assertEq(s2.sessionName, 'proj #2', 'session name');

// ── Test 13: no session.json falls back to timestamp ─────────────────────
console.log('\nTest 13: no session fallback to timestamp');
const noSess = parseLensData(sessLog, {}, { files: 1, symbols: 1, db_kb: 1, last_indexed: 250, token_budget: 8000, project_tokens_total: 10000, by_language: {} });
assertEq(noSess.sessionId, null, 'no sessionId');
assertEq(noSess.sessionName, '', 'no sessionName');
assertEq(noSess.sessionQueries, 3, 'fallback: 3 queries after ts=250');

// ── Test 14: emptyStats has session fields ───────────────────────────────
console.log('\nTest 14: emptyStats session fields');
const es = emptyStats();
assertEq(es.sessionId, null, 'emptyStats sessionId');
assertEq(es.sessionName, '', 'emptyStats sessionName');
assertEq(es.lastQueryTs, 0, 'emptyStats lastQueryTs');

// ── Test 15: lastQueryTs ─────────────────────────────────────────────────
console.log('\nTest 15: lastQueryTs');
assertEq(s2.lastQueryTs, 500, 'lastQueryTs = ts of last retrieval');
assertEq(noSess.lastQueryTs, 500, 'lastQueryTs same without session');
const emptyLogResult = parseLensData('', {}, null);
assertEq(emptyLogResult.lastQueryTs, 0, 'no queries = 0');

// ── Test 16: session-by-tool stats + per-tool budget ─────────────────────
console.log('\nTest 16: session-by-tool stats');
const mixedToolLog = [
  JSON.stringify({ ts: 100, event: 'retrieval', task: 'explain', tokens_used: 500, tokens_raw: 10000, session_id: 7, tool: 'copilot' }),
  JSON.stringify({ ts: 200, event: 'retrieval', task: 'bugfix', tokens_used: 600, tokens_raw: 10000, session_id: 7, tool: 'codex' }),
  JSON.stringify({ ts: 300, event: 'retrieval', task: 'navigate', tokens_used: 700, tokens_raw: 10000, session_id: 7, tool: 'codex' }),
  JSON.stringify({ ts: 400, event: 'retrieval', task: 'explain', tokens_used: 800, tokens_raw: 10000, session_id: 8, tool: 'claude' }),
].join('\n');
const mixedStats = parseLensData(
  mixedToolLog,
  { token_budget: 8000, target_budgets: { copilot: 4000, codex: 6000, claude: 9000 } },
  { files: 1, symbols: 1, db_kb: 1, last_indexed: 0, token_budget: 8000, project_tokens_total: 10000, by_language: {} },
  { id: 7, name: 'proj #7', started_at: 50, tool: 'codex' },
);
assertEq(mixedStats.activeTool, 'codex', 'active tool comes from session.json tool');
assertEq(mixedStats.tokenBudget, 6000, 'budget switches to active tool target');
assertEq(mixedStats.sessionByTool['copilot'].count, 1, 'sessionByTool tracks copilot session queries');
assertEq(mixedStats.sessionByTool['codex'].count, 2, 'sessionByTool tracks codex session queries');
assertEq(mixedStats.activeToolSessionQueries, 2, 'active tool session queries from sessionByTool');
assertEq(mixedStats.activeToolSessionTokensSaved, 20000 - (600 + 700), 'active tool saved tokens');

// ── Test 17a: byTool lastTs ─────────────────────────────────────────────
console.log('\nTest 17a: byTool lastTs');
assertEq(mixedStats.byTool['copilot'].lastTs, 100, 'byTool copilot lastTs');
assertEq(mixedStats.byTool['codex'].lastTs, 300, 'byTool codex lastTs = latest of 200,300');
assertEq(mixedStats.byTool['claude'].lastTs, 400, 'byTool claude lastTs');
assertEq(mixedStats.sessionByTool['codex'].lastTs, 300, 'sessionByTool codex lastTs');

// ── Test 18: active tool fallback prefers current session ─────────────────
console.log('\nTest 18: active tool fallback prefers current session');
const inferredToolStats = parseLensData(
  mixedToolLog,
  { token_budget: 8000, target_budgets: { copilot: 4000, codex: 6000, claude: 9000 } },
  { files: 1, symbols: 1, db_kb: 1, last_indexed: 0, token_budget: 8000, project_tokens_total: 10000, by_language: {} },
  { id: 7, name: 'proj #7', started_at: 50 },
);
assertEq(inferredToolStats.activeTool, 'codex', 'falls back to latest tool in the active session');
assertEq(inferredToolStats.tokenBudget, 6000, 'fallback active tool still drives budget');

console.log('\n=== All tests passed! ===\n');
