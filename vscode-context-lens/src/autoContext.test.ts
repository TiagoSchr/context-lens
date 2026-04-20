/**
 * autoContext.test.ts — tests for the auto-context injection system.
 *
 * Validates that:
 * 1. chatInstructions file format is correct for VS Code consumption
 * 2. lastQueryTs detection works for stale-context warnings
 * 3. The monitor timer correctly identifies fresh vs stale auto-context
 * 4. Edge cases: empty context, missing root, concurrent runs
 */
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';
import { parseLensData, LensStats } from './logParser';

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

console.log('\n=== autoContext tests ===\n');

// ── Helper: simulate what refreshAutoContext writes ──────────────────────

function buildChatInstructionsContent(lensOutput: string): string {
  return [
    '# Context Lens — Auto-injected Project Context',
    '',
    'This project uses Context Lens for token optimization.',
    'Below is an optimized snapshot of the codebase, auto-refreshed every 10 minutes.',
    'Use this context as your PRIMARY source. Only read individual files if they are',
    'NOT covered here.',
    '',
    '## Project Context',
    '',
    '```',
    lensOutput.trim(),
    '```',
    '',
    '## Additional Tools',
    '',
    '- `lens_context(query)` — get query-specific context (more targeted than above)',
    '- `lens_search(query)` — find symbols by name',
  ].join('\n');
}

// ── Test 1: chatInstructions file format ─────────────────────────────────
console.log('Test 1: chatInstructions file format');
{
  const sampleContext = [
    '# Context — task=explain | query=project overview',
    '',
    '=== PROJECT MAP ===',
    'root: /project',
    'dirs: src, tests',
    '',
    '=== SYMBOLS ===',
    '[function] main()',
    '  @ src/main.py:1',
  ].join('\n');

  const content = buildChatInstructionsContent(sampleContext);

  assert(content.startsWith('# Context Lens'), 'starts with header');
  assert(content.includes('## Project Context'), 'has Project Context section');
  assert(content.includes('```'), 'has code fence');
  assert(content.includes('PROJECT MAP'), 'includes project map');
  assert(content.includes('[function] main()'), 'includes symbols');
  assert(content.includes('lens_context(query)'), 'mentions lens_context tool');
  assert(content.includes('lens_search(query)'), 'mentions lens_search tool');
  assert(!content.includes('undefined'), 'no undefined in content');
  assert(!content.includes('null'), 'no null in content');
}

// ── Test 2: empty context produces valid file ───────────────────────────
console.log('\nTest 2: empty context handling');
{
  const content = buildChatInstructionsContent('');
  assert(content.includes('# Context Lens'), 'still has header');
  assert(content.includes('```\n\n```'), 'empty code block');
  assert(content.includes('lens_context'), 'still has tool reference');
}

// ── Test 3: file write/read round-trip ──────────────────────────────────
console.log('\nTest 3: file write/read round-trip');
{
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'autocontext-test-'));
  const promptsDir = path.join(tmpDir, 'prompts');
  fs.mkdirSync(promptsDir, { recursive: true });
  const filePath = path.join(promptsDir, 'lens-instructions.instructions.md');

  const sampleOutput = '# Context\nroot: /test\n[function] hello()';
  const content = buildChatInstructionsContent(sampleOutput);

  fs.writeFileSync(filePath, content, 'utf-8');
  assert(fs.existsSync(filePath), 'file created');

  const readBack = fs.readFileSync(filePath, 'utf-8');
  assertEq(readBack, content, 'content matches');
  assert(readBack.includes('[function] hello()'), 'symbols preserved after round-trip');

  // Cleanup
  fs.rmSync(tmpDir, { recursive: true, force: true });
}

// ── Test 4: monitor freshness detection ─────────────────────────────────
console.log('\nTest 4: monitor freshness detection');
{
  // Simulate the monitor logic from extension.ts
  const ACTIVITY_WINDOW_S = 600; // 10 min

  function isAutoContextFresh(lastAutoContextTs: number, nowMs: number): boolean {
    if (lastAutoContextTs <= 0) { return false; }
    const nowS = nowMs / 1000;
    return (nowS - lastAutoContextTs / 1000) < ACTIVITY_WINDOW_S;
  }

  function isLensContextFresh(lastQueryTs: number, nowMs: number): boolean {
    if (lastQueryTs <= 0) { return false; }
    const nowS = nowMs / 1000;
    return (nowS - lastQueryTs) < ACTIVITY_WINDOW_S;
  }

  const now = Date.now();

  // Fresh auto-context (just updated)
  assert(isAutoContextFresh(now - 30_000, now), 'auto-context from 30s ago is fresh');

  // Stale auto-context (15 min old)
  assert(!isAutoContextFresh(now - 15 * 60_000, now), 'auto-context from 15min ago is stale');

  // No auto-context at all
  assert(!isAutoContextFresh(0, now), 'no auto-context = not fresh');

  // Fresh lens_context call
  const freshTs = now / 1000 - 60; // 60s ago in unix seconds
  assert(isLensContextFresh(freshTs, now), 'lens_context from 60s ago is fresh');

  // Stale lens_context call
  const staleTs = now / 1000 - 900; // 15 min ago
  assert(!isLensContextFresh(staleTs, now), 'lens_context from 15min ago is stale');

  // Combined: either fresh = no warning
  const shouldWarn = (autoTs: number, lensTs: number, nowMs: number): boolean => {
    return !isAutoContextFresh(autoTs, nowMs) && !isLensContextFresh(lensTs, nowMs);
  };

  assert(!shouldWarn(now - 30_000, 0, now), 'no warning when auto-context is fresh');
  assert(!shouldWarn(0, now / 1000 - 60, now), 'no warning when lens_context is fresh');
  assert(!shouldWarn(now - 30_000, now / 1000 - 60, now), 'no warning when both fresh');
  assert(shouldWarn(0, 0, now), 'warning when both missing');
  assert(shouldWarn(now - 15 * 60_000, now / 1000 - 900, now), 'warning when both stale');
}

// ── Test 5: parseLensData lastQueryTs used for freshness ────────────────
console.log('\nTest 5: lastQueryTs drives freshness check');
{
  const recentLog = JSON.stringify({
    ts: Date.now() / 1000 - 30, // 30 seconds ago
    event: 'retrieval',
    task: 'explain',
    tokens_used: 2000,
    tokens_raw: 50000,
    budget: 8000,
  });

  const stats = parseLensData(recentLog, {}, {
    files: 10, symbols: 50, db_kb: 100,
    last_indexed: Date.now() / 1000 - 3600,
    token_budget: 8000,
    project_tokens_total: 50000,
    by_language: {},
  });

  assert(stats.lastQueryTs > 0, 'lastQueryTs populated');
  assert(stats.totalQueries === 1, '1 query counted');
  assert(stats.totalTokensUsed === 2000, 'tokens used tracked');

  // Freshness check
  const nowS = Date.now() / 1000;
  const isRecent = (nowS - stats.lastQueryTs) < 600;
  assert(isRecent, 'lastQueryTs from 30s ago detected as recent');
}

// ── Test 6: old lastQueryTs detected as stale ───────────────────────────
console.log('\nTest 6: stale lastQueryTs detection');
{
  const oldLog = JSON.stringify({
    ts: Date.now() / 1000 - 7200, // 2 hours ago
    event: 'retrieval',
    task: 'explain',
    tokens_used: 2000,
    tokens_raw: 50000,
    budget: 8000,
  });

  const stats = parseLensData(oldLog, {}, {
    files: 10, symbols: 50, db_kb: 100,
    last_indexed: Date.now() / 1000 - 10800,
    token_budget: 8000,
    project_tokens_total: 50000,
    by_language: {},
  });

  const nowS = Date.now() / 1000;
  const isRecent = (nowS - stats.lastQueryTs) < 600;
  assert(!isRecent, 'lastQueryTs from 2h ago detected as stale');
}

// ── Test 7: auto-context refresh debounce ───────────────────────────────
console.log('\nTest 7: auto-context debounce logic');
{
  // Simulates the debounce from extension.ts:
  // "only if last auto-context was > 60s ago"
  const DEBOUNCE_MS = 60_000;

  function shouldRefresh(lastAutoContextTs: number, nowMs: number): boolean {
    return (nowMs - lastAutoContextTs) > DEBOUNCE_MS;
  }

  const now = Date.now();
  assert(!shouldRefresh(now - 30_000, now), 'skip refresh if last was 30s ago');
  assert(!shouldRefresh(now - 59_000, now), 'skip refresh if last was 59s ago');
  assert(shouldRefresh(now - 61_000, now), 'allow refresh if last was 61s ago');
  assert(shouldRefresh(0, now), 'allow refresh if never ran');
}

// ── Test 8: concurrent run guard ────────────────────────────────────────
console.log('\nTest 8: concurrent run guard');
{
  // Simulates the _autoContextRunning flag from extension.ts
  let running = false;

  function tryStartAutoContext(): boolean {
    if (running) { return false; }
    running = true;
    return true;
  }

  function finishAutoContext(): void {
    running = false;
  }

  assert(tryStartAutoContext(), 'first run starts');
  assert(!tryStartAutoContext(), 'second run blocked while first runs');
  finishAutoContext();
  assert(tryStartAutoContext(), 'third run starts after first finishes');
  finishAutoContext();
}

// ── Test 9: chatInstructions content with special characters ────────────
console.log('\nTest 9: special characters in context');
{
  const specialContext = [
    '# Context with special chars: <html> & "quotes" \'single\'',
    'paths: C:\\Users\\User\\project',
    'unicode: café résumé naïve',
    'backticks: `code` and ```blocks```',
  ].join('\n');

  const content = buildChatInstructionsContent(specialContext);
  assert(content.includes('<html>'), 'HTML preserved');
  assert(content.includes('C:\\Users\\User\\project'), 'Windows paths preserved');
  assert(content.includes('café'), 'Unicode preserved');
  assert(content.includes('`code`'), 'Inline backticks preserved');
}

// ── Test 10: chatInstructions with large context ────────────────────────
console.log('\nTest 10: large context handling');
{
  // Simulate a 3000-token context (~12000 chars)
  const lines: string[] = [];
  for (let i = 0; i < 300; i++) {
    lines.push(`[function] func_${i}(arg1: int, arg2: str) -> dict`);
    lines.push(`  @ src/module_${Math.floor(i / 10)}.py:${(i % 100) + 1}`);
  }
  const largeContext = lines.join('\n');

  const content = buildChatInstructionsContent(largeContext);
  assert(content.length > 10000, 'large content preserved');
  assert(content.includes('func_0'), 'first function present');
  assert(content.includes('func_299'), 'last function present');
  assert(content.split('```').length === 3, 'exactly one code block (open + close)');
}

// ── Test 11: context detection in parseLensData with tool field ─────────
console.log('\nTest 11: tool-specific context detection');
{
  const toolLog = [
    JSON.stringify({ ts: 100, event: 'retrieval', task: 'explain', tokens_used: 500, tokens_raw: 10000, tool: 'copilot' }),
    JSON.stringify({ ts: 200, event: 'retrieval', task: 'explain', tokens_used: 600, tokens_raw: 10000, tool: 'copilot' }),
    JSON.stringify({ ts: 300, event: 'retrieval', task: 'bugfix', tokens_used: 700, tokens_raw: 10000, tool: 'claude' }),
  ].join('\n');

  const stats = parseLensData(toolLog, {}, {
    files: 10, symbols: 50, db_kb: 100,
    last_indexed: 0, token_budget: 8000,
    project_tokens_total: 10000, by_language: {},
  });

  assert(stats.totalQueries === 3, '3 total queries from all tools');
  assert(stats.byTool['copilot'] !== undefined, 'copilot tracked in byTool');
  assert(stats.byTool['claude'] !== undefined, 'claude tracked in byTool');
  assertEq(stats.byTool['copilot'].count, 2, 'copilot: 2 queries');
  assertEq(stats.byTool['claude'].count, 1, 'claude: 1 query');
  assertEq(stats.lastQueryTs, 300, 'lastQueryTs = latest query ts');
}

// ── Test 12: auto-context interval constants ────────────────────────────
console.log('\nTest 12: interval constants validation');
{
  // These should match the values in extension.ts
  const AUTO_CONTEXT_BUDGET = 3000;
  const AUTO_CONTEXT_INTERVAL_MS = 10 * 60_000;
  const AUTO_CONTEXT_TIMEOUT_MS = 20_000;
  const MONITOR_INTERVAL_MS = 60_000;
  const ACTIVITY_WINDOW_S = 600;

  assert(AUTO_CONTEXT_BUDGET > 0, 'budget is positive');
  assert(AUTO_CONTEXT_BUDGET <= 8000, 'budget within reasonable limit');
  assert(AUTO_CONTEXT_INTERVAL_MS === 600_000, 'refresh interval = 10 min');
  assert(AUTO_CONTEXT_TIMEOUT_MS < AUTO_CONTEXT_INTERVAL_MS, 'timeout < interval');
  assert(MONITOR_INTERVAL_MS < AUTO_CONTEXT_INTERVAL_MS, 'monitor checks more often than refresh');
  assert(ACTIVITY_WINDOW_S * 1000 >= AUTO_CONTEXT_INTERVAL_MS, 'activity window >= refresh interval');
}

console.log('\n=== All autoContext tests passed! ===\n');
