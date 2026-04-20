export interface TranscriptBufferResult {
  completeLines: string[];
  partialLine: string;
}

export function resetTranscriptBuffer(): TranscriptBufferResult {
  return {
    completeLines: [],
    partialLine: '',
  };
}

/**
 * Append raw transcript bytes and return only complete JSONL lines.
 * A trailing partial line is retained for the next chunk.
 */
export function appendTranscriptChunk(
  chunk: string,
  partialLine = '',
): TranscriptBufferResult {
  const normalized = `${partialLine}${chunk}`.replace(/\r\n/g, '\n');
  const parts = normalized.split('\n');
  const nextPartialLine = parts.pop() ?? '';

  return {
    completeLines: parts.filter((line) => line.trim().length > 0),
    partialLine: nextPartialLine,
  };
}

/**
 * If the trailing partial line is already valid JSON, treat it as complete.
 * This helps when the producer writes a JSON object before the final newline.
 */
export function finalizeTranscriptPartial(partialLine: string): TranscriptBufferResult {
  if (!partialLine.trim()) {
    return resetTranscriptBuffer();
  }

  try {
    JSON.parse(partialLine);
    return {
      completeLines: [partialLine],
      partialLine: '',
    };
  } catch {
    return {
      completeLines: [],
      partialLine,
    };
  }
}

/**
 * Parse a full transcript snapshot from disk while preserving an incomplete
 * trailing line if the file is currently being written.
 */
export function parseTranscriptSnapshot(content: string): TranscriptBufferResult {
  const buffered = appendTranscriptChunk(content);
  const finalized = finalizeTranscriptPartial(buffered.partialLine);

  return {
    completeLines: [...buffered.completeLines, ...finalized.completeLines],
    partialLine: finalized.partialLine,
  };
}
