/**
 * debounce.test.ts — unit tests for leadingDebounce.
 */
import { leadingDebounce } from './debounce';

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

console.log('\n=== debounce tests ===\n');

// ── Test 1: first call fires immediately (leading edge) ──────────────────
console.log('Test 1: leading-edge fires immediately');
{
  let count = 0;
  const d = leadingDebounce(() => { count++; }, 500);
  d();
  assertEq(count, 1, 'first call fires synchronously');
}

// ── Test 2: second call within window is deferred ─────────────────────────
console.log('\nTest 2: second call within window is deferred');
{
  let count = 0;
  const d = leadingDebounce(() => { count++; }, 50);
  d();
  assertEq(count, 1, 'first fires immediately');
  d(); // within 50ms
  assertEq(count, 1, 'second is deferred (not yet fired)');
}

// ── Test 3: cancel prevents the trailing call ─────────────────────────────
console.log('\nTest 3: cancel prevents trailing call');
{
  let count = 0;
  const d = leadingDebounce(() => { count++; }, 50);
  d();
  d();
  d.cancel();
  assertEq(count, 1, 'trailing call was cancelled');
}

// ── Test 4: flush fires trailing call synchronously ───────────────────────
console.log('\nTest 4: flush fires trailing call');
{
  let count = 0;
  const d = leadingDebounce(() => { count++; }, 5000);
  d();
  assertEq(count, 1, 'leading fires');
  d();
  d.flush();
  assertEq(count, 2, 'flush forces trailing call synchronously');
}

// ── Test 5: flush with no pending call is a no-op ─────────────────────────
console.log('\nTest 5: flush with nothing pending is no-op');
{
  let count = 0;
  const d = leadingDebounce(() => { count++; }, 5000);
  d();
  d.flush(); // nothing pending
  assertEq(count, 1, 'flush does not fire extra call');
}

console.log('\n=== All debounce tests passed! ===\n');
