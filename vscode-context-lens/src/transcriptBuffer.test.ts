import {
  appendTranscriptChunk,
  finalizeTranscriptPartial,
  parseTranscriptSnapshot,
  resetTranscriptBuffer,
} from './transcriptBuffer';

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

console.log('\n=== transcriptBuffer tests ===\n');

// ── Test 1: complete line ─────────────────────────────────────────────────
console.log('Test 1: complete line');
const complete = appendTranscriptChunk('{"type":"user.message"}\n');
assertEq(complete.completeLines.length, 1, 'complete line emitted immediately');
assertEq(complete.completeLines[0], '{"type":"user.message"}', 'line content preserved');
assertEq(complete.partialLine, '', 'no partial line remains');

// ── Test 2: partial line + continuation ───────────────────────────────────
console.log('\nTest 2: partial line + continuation');
const partial = appendTranscriptChunk('{"type":"user');
assertEq(partial.completeLines.length, 0, 'partial chunk emits no lines');
assertEq(partial.partialLine, '{"type":"user', 'partial chunk retained');

const continued = appendTranscriptChunk('.message"}\n{"type":"assistant.message"}\n', partial.partialLine);
assertEq(continued.completeLines.length, 2, 'continuation emits both completed lines');
assertEq(continued.completeLines[0], '{"type":"user.message"}', 'first line reconstructed correctly');
assertEq(continued.completeLines[1], '{"type":"assistant.message"}', 'second line parsed correctly');
assertEq(continued.partialLine, '', 'continuation clears partial line');

// ── Test 3: file switch reset ─────────────────────────────────────────────
console.log('\nTest 3: file switch reset');
const stale = appendTranscriptChunk('{"type":"old');
assertEq(stale.completeLines.length, 0, 'stale buffer still partial');
assertEq(stale.partialLine, '{"type":"old', 'stale partial retained before reset');

const reset = resetTranscriptBuffer();
const afterReset = appendTranscriptChunk('{"type":"new.session"}\n', reset.partialLine);
assertEq(afterReset.completeLines.length, 1, 'new file starts clean after reset');
assertEq(afterReset.completeLines[0], '{"type":"new.session"}', 'new file line is unaffected by old partial');

// ── Test 4: parseable trailing line in snapshot ───────────────────────────
console.log('\nTest 4: parseable trailing line in snapshot');
const snapshot = parseTranscriptSnapshot('{"type":"session.start"}');
assertEq(snapshot.completeLines.length, 1, 'snapshot keeps a valid final line without newline');
assertEq(snapshot.partialLine, '', 'snapshot clears parseable trailing line');

// ── Test 5: finalize parseable partial ────────────────────────────────────
console.log('\nTest 5: finalize parseable partial');
const finalized = finalizeTranscriptPartial('{"type":"assistant.turn_end"}');
assertEq(finalized.completeLines.length, 1, 'finalize promotes valid JSON partial');
assertEq(finalized.partialLine, '', 'finalize clears promoted partial');

assert(reset.completeLines.length === 0, 'reset starts with no buffered lines');
assertEq(reset.partialLine, '', 'reset clears partial state');

console.log('\n=== All tests passed! ===\n');
