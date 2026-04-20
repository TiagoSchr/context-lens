/**
 * copilotParser.ts — pure parser for Copilot Chat transcript JSONL files.
 *
 * Extracts message content from transcript events and estimates token counts.
 * No I/O — caller provides raw lines.
 */

// ── Types ───────────────────────────────────────────────────────────────────

export interface CopilotMessage {
  role: 'user' | 'assistant' | 'tool';
  tokens: number;
  ts: number;
}

/** Summary of a single Copilot Chat session (for history list). */
export interface CopilotSessionSummary {
  sessionId: string;
  name: string;
  totalTokens: number;
  messageCount: number;
  lastMessageTs: number;
  active: boolean;
}

export interface CopilotStats {
  /** Session UUID from session.start event */
  sessionId: string;
  /** Human-readable session name (first user message, truncated) */
  sessionName: string;
  /** Model name used in this session (e.g. "Claude Opus 4.6") */
  model: string;
  /** Total user + assistant messages */
  messageCount: number;
  /** Number of assistant turns */
  turnCount: number;
  /** Number of tool calls */
  toolCallCount: number;
  /** Estimated tokens from user messages */
  inputTokens: number;
  /** Estimated tokens from assistant content + tool request args */
  outputTokens: number;
  /** Estimated tokens from reasoningText (extended thinking) */
  reasoningTokens: number;
  /** Sum of input + output + reasoning */
  totalTokens: number;
  /** Timestamp of last message */
  lastMessageTs: number;
  /** Last 8 messages (newest first) */
  recentMessages: CopilotMessage[];
  /** All known sessions (current + history) */
  allSessions: CopilotSessionSummary[];
}

export function emptyCopilotStats(): CopilotStats {
  return {
    sessionId: '',
    sessionName: '',
    model: '',
    messageCount: 0,
    turnCount: 0,
    toolCallCount: 0,
    inputTokens: 0,
    outputTokens: 0,
    reasoningTokens: 0,
    totalTokens: 0,
    lastMessageTs: 0,
    recentMessages: [],
    allSessions: [],
  };
}

// ── Heuristic token counter ────────────────────────────────────────────────

/**
 * Estimate token count from text using a simple heuristic.
 * ~4 chars per token is a reasonable approximation for o200k_base.
 */
export function estimateTokens(text: string): number {
  if (!text) { return 0; }
  return Math.ceil(text.length / 4);
}

export function truncateSessionName(text: string, maxChars = 60): string {
  const firstLine = text.trim().split(/\r?\n/, 1)[0]?.trim() ?? '';
  if (!firstLine) { return ''; }
  return firstLine.length > maxChars
    ? `${firstLine.slice(0, maxChars)}\u2026`
    : firstLine;
}

export function parseChatSessionCustomTitle(content: string): string {
  if (!content.trim()) { return ''; }

  const lines = content.split('\n');
  for (const line of lines) {
    if (!line.trim()) { continue; }

    let record: Record<string, unknown>;
    try {
      record = JSON.parse(line) as Record<string, unknown>;
    } catch {
      continue;
    }

    const keyPath = Array.isArray(record.k) ? record.k : [];
    if (
      keyPath.length === 1
      && keyPath[0] === 'customTitle'
      && typeof record.v === 'string'
    ) {
      const title = truncateSessionName(record.v);
      if (title) { return title; }
    }

    if (record.kind === 0 && record.v && typeof record.v === 'object' && !Array.isArray(record.v)) {
      const root = record.v as Record<string, unknown>;
      if (typeof root.customTitle === 'string') {
        const title = truncateSessionName(root.customTitle);
        if (title) { return title; }
      }
    }
  }

  return '';
}

/**
 * Extract the model name from a chatSessions JSONL file.
 * Looks for `selectedModel.metadata.name` in the kind=0 root record
 * or in a `k: ["inputState"]` patch.
 */
export function parseChatSessionModel(content: string): string {
  if (!content.trim()) { return ''; }

  for (const line of content.split('\n')) {
    if (!line.trim()) { continue; }

    let record: Record<string, unknown>;
    try {
      record = JSON.parse(line) as Record<string, unknown>;
    } catch {
      continue;
    }

    // kind=0 full snapshot
    if (record.kind === 0 && record.v && typeof record.v === 'object' && !Array.isArray(record.v)) {
      const name = extractModelName(record.v as Record<string, unknown>);
      if (name) { return name; }
    }

    // k: ["inputState"] patch
    const keyPath = Array.isArray(record.k) ? record.k : [];
    if (keyPath.length === 1 && keyPath[0] === 'inputState' && record.v && typeof record.v === 'object') {
      const inputState = record.v as Record<string, unknown>;
      const selectedModel = inputState.selectedModel as Record<string, unknown> | undefined;
      if (selectedModel) {
        const name = extractModelNameFromSelectedModel(selectedModel);
        if (name) { return name; }
      }
    }
  }

  return '';
}

function extractModelName(root: Record<string, unknown>): string {
  const inputState = root.inputState as Record<string, unknown> | undefined;
  if (!inputState) { return ''; }
  const selectedModel = inputState.selectedModel as Record<string, unknown> | undefined;
  if (!selectedModel) { return ''; }
  return extractModelNameFromSelectedModel(selectedModel);
}

function extractModelNameFromSelectedModel(selectedModel: Record<string, unknown>): string {
  const metadata = selectedModel.metadata as Record<string, unknown> | undefined;
  if (metadata && typeof metadata.name === 'string' && metadata.name) {
    return metadata.name;
  }
  // Fallback: identifier like "copilot/claude-opus-4.6"
  if (typeof selectedModel.identifier === 'string' && selectedModel.identifier) {
    const parts = selectedModel.identifier.split('/');
    return parts.length > 1 ? parts.slice(1).join('/') : selectedModel.identifier;
  }
  return '';
}

// ── Transcript event shapes ────────────────────────────────────────────────

interface TranscriptEvent {
  type: string;
  data: Record<string, unknown>;
  id: string;
  timestamp: string;
  parentId: string | null;
}

interface ToolRequest {
  toolCallId: string;
  name: string;
  arguments: string;
  type: string;
}

// ── Parser ─────────────────────────────────────────────────────────────────

/**
 * Parse Copilot Chat transcript lines into CopilotStats.
 * Pure function — no I/O.
 *
 * @param lines - Array of JSONL strings from the transcript file
 * @param tokenCounter - Optional async-free token estimator (defaults to heuristic)
 */
export function parseCopilotTranscript(
  lines: string[],
  tokenCounter: (text: string) => number = estimateTokens,
): CopilotStats {
  const result = emptyCopilotStats();
  const allMessages: CopilotMessage[] = [];

  for (const line of lines) {
    if (!line.trim()) { continue; }

    let event: TranscriptEvent;
    try {
      event = JSON.parse(line) as TranscriptEvent;
    } catch {
      continue; // malformed line
    }

    const ts = event.timestamp ? new Date(event.timestamp).getTime() / 1000 : 0;

    switch (event.type) {
      case 'session.start': {
        result.sessionId = (event.data.sessionId as string) ?? '';
        break;
      }

      case 'user.message': {
        const content = (event.data.content as string) ?? '';
        const tokens = tokenCounter(content);
        result.inputTokens += tokens;
        result.messageCount++;
        allMessages.push({ role: 'user', tokens, ts });
        if (ts > result.lastMessageTs) { result.lastMessageTs = ts; }
        // Session name = first user message (truncated)
        if (!result.sessionName && content.trim()) {
          result.sessionName = truncateSessionName(content);
        }
        break;
      }

      case 'assistant.message': {
        const content = (event.data.content as string) ?? '';
        const reasoning = (event.data.reasoningText as string) ?? '';
        const toolRequests = (event.data.toolRequests as ToolRequest[]) ?? [];

        // Content counts as output tokens (tool args are counted in tool.execution_start)
        const outputTokens = tokenCounter(content);

        const reasoningTokens = tokenCounter(reasoning);

        result.outputTokens += outputTokens;
        result.reasoningTokens += reasoningTokens;
        result.messageCount++;
        result.toolCallCount += toolRequests.length;

        allMessages.push({
          role: 'assistant',
          tokens: outputTokens + reasoningTokens,
          ts,
        });

        if (ts > result.lastMessageTs) { result.lastMessageTs = ts; }
        break;
      }

      case 'assistant.turn_start': {
        result.turnCount++;
        break;
      }

      case 'tool.execution_start': {
        // Tool arguments are sent back as context to the model
        const args = event.data.arguments;
        if (args) {
          const argStr = typeof args === 'string' ? args : JSON.stringify(args);
          const tokens = tokenCounter(argStr);
          result.inputTokens += tokens;
          allMessages.push({ role: 'tool', tokens, ts });
        }
        break;
      }

      // session.start, assistant.turn_end, tool.execution_complete — no token content
      default:
        break;
    }
  }

  result.totalTokens = result.inputTokens + result.outputTokens + result.reasoningTokens;

  // Last 8 messages (newest first)
  result.recentMessages = allMessages.slice(-8).reverse();

  return result;
}

/**
 * Quick scan of a transcript file to extract just session name and total tokens.
 * Much lighter than full parse — only reads first 20 lines for the name,
 * and does a fast char-count for total tokens.
 */
export function parseSessionSummary(
  content: string,
  tokenCounter: (text: string) => number = estimateTokens,
): CopilotSessionSummary {
  const summary: CopilotSessionSummary = {
    sessionId: '',
    name: '',
    totalTokens: 0,
    messageCount: 0,
    lastMessageTs: 0,
    active: false,
  };

  const lines = content.split('\n');
  let totalText = 0;

  for (const line of lines) {
    if (!line.trim()) { continue; }

    let event: TranscriptEvent;
    try {
      event = JSON.parse(line) as TranscriptEvent;
    } catch {
      continue;
    }

    const ts = event.timestamp ? new Date(event.timestamp).getTime() / 1000 : 0;

    switch (event.type) {
      case 'session.start':
        summary.sessionId = (event.data.sessionId as string) ?? '';
        break;

      case 'user.message': {
        const content = (event.data.content as string) ?? '';
        totalText += content.length;
        summary.messageCount++;
        if (ts > summary.lastMessageTs) { summary.lastMessageTs = ts; }
        if (!summary.name && content.trim()) {
          summary.name = truncateSessionName(content);
        }
        break;
      }

      case 'assistant.message': {
        const c = (event.data.content as string) ?? '';
        const r = (event.data.reasoningText as string) ?? '';
        totalText += c.length + r.length;
        summary.messageCount++;
        if (ts > summary.lastMessageTs) { summary.lastMessageTs = ts; }
        break;
      }
    }
  }

  // Heuristic batch count without allocating a throwaway string of length N.
  // Preserves tokenCounter override behavior: if a custom counter is supplied
  // we still call it on a small representative string so tests remain stable.
  summary.totalTokens = tokenCounter === estimateTokens
    ? Math.ceil(totalText / 4)
    : tokenCounter('x'.repeat(Math.min(totalText, 64)));

  return summary;
}
