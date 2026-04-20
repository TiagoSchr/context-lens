/**
 * claudeParser.ts — pure parser for Claude Code transcript JSONL files.
 *
 * Claude Code (the `claude` CLI) stores each session at:
 *   ~/.claude/projects/<sanitized-cwd>/<sessionUuid>.jsonl
 *
 * Each line is a JSON object with `type` ∈ {"user", "assistant", "summary",
 * "system", ...}. Assistant messages carry a `message.usage` block with real
 * `input_tokens`, `output_tokens`, `cache_read_input_tokens`, and
 * `cache_creation_input_tokens` — so we can report accurate counts.
 */

import {
  ToolSessionSummary,
  ToolStats,
  emptyToolStats,
  estimateTokens,
  truncateSessionName,
} from './toolStats';

interface ClaudeLine {
  type?: string;
  uuid?: string;
  sessionId?: string;
  cwd?: string;
  timestamp?: string;
  summary?: string;
  leafUuid?: string;
  isMeta?: boolean;
  isSidechain?: boolean;
  message?: {
    id?: string;
    role?: string;
    model?: string;
    content?: unknown;
    usage?: {
      input_tokens?: number;
      output_tokens?: number;
      cache_read_input_tokens?: number;
      cache_creation_input_tokens?: number;
    };
  };
}

function extractText(content: unknown): string {
  if (typeof content === 'string') { return content; }
  if (!Array.isArray(content)) { return ''; }
  const parts: string[] = [];
  for (const block of content) {
    if (!block || typeof block !== 'object') { continue; }
    const b = block as Record<string, unknown>;
    if (b.type === 'text' && typeof b.text === 'string') { parts.push(b.text); }
    else if (b.type === 'tool_use' && b.input) {
      try { parts.push(JSON.stringify(b.input)); } catch { /* ignore */ }
    } else if (b.type === 'tool_result') {
      const c = b.content;
      if (typeof c === 'string') { parts.push(c); }
      else if (Array.isArray(c)) { parts.push(extractText(c)); }
    }
  }
  return parts.join('\n');
}

function countToolUses(content: unknown): number {
  if (!Array.isArray(content)) { return 0; }
  let n = 0;
  for (const block of content) {
    if (block && typeof block === 'object' && (block as Record<string, unknown>).type === 'tool_use') {
      n++;
    }
  }
  return n;
}

/**
 * Parse a Claude Code transcript and aggregate real token usage.
 * Uses `message.usage` when available (every assistant turn); falls back to
 * the char/4 heuristic for user messages (which carry no usage).
 */
export function parseClaudeTranscript(lines: string[]): ToolStats {
  const result = emptyToolStats('claude');
  let firstUserText = '';
  let summaryText = '';
  let usageSeen = false;

  for (const line of lines) {
    if (!line.trim()) { continue; }
    let rec: ClaudeLine;
    try { rec = JSON.parse(line) as ClaudeLine; } catch { continue; }

    if (rec.sessionId && !result.sessionId) { result.sessionId = rec.sessionId; }

    const ts = rec.timestamp ? new Date(rec.timestamp).getTime() / 1000 : 0;
    if (ts > result.lastMessageTs) { result.lastMessageTs = ts; }

    if (rec.type === 'summary' && typeof rec.summary === 'string') {
      summaryText = rec.summary;
      continue;
    }

    if (rec.isMeta || rec.isSidechain) { continue; }

    const msg = rec.message;
    if (!msg) { continue; }

    if (rec.type === 'user' && msg.role === 'user') {
      const text = extractText(msg.content);
      if (!firstUserText && text.trim()) { firstUserText = text; }
      // User messages have no usage — estimate
      result.inputTokens += estimateTokens(text);
      result.messageCount++;
      continue;
    }

    if (rec.type === 'assistant' && msg.role === 'assistant') {
      if (msg.model) { result.model = msg.model; }
      const u = msg.usage;
      if (u) {
        usageSeen = true;
        // Claude splits cache tokens from regular input. Count cache reads as
        // "reasoning/cached" context to keep the UI card meaningful.
        result.outputTokens += u.output_tokens ?? 0;
        result.reasoningTokens += (u.cache_read_input_tokens ?? 0) + (u.cache_creation_input_tokens ?? 0);
        // input_tokens here is the fresh input for the current assistant turn;
        // merge it into inputTokens (assistant turn receives recent user text
        // which was already counted, but Claude reports real per-turn numbers
        // when the content is re-sent — so trust usage over heuristic).
        result.inputTokens += u.input_tokens ?? 0;
      } else {
        result.outputTokens += estimateTokens(extractText(msg.content));
      }
      result.messageCount++;
      result.toolCallCount += countToolUses(msg.content);
      continue;
    }
  }

  result.tokensFromUsage = usageSeen;
  result.totalTokens = result.inputTokens + result.outputTokens + result.reasoningTokens;
  result.sessionName = truncateSessionName(summaryText || firstUserText);
  return result;
}

/** Quick scan of a transcript for the session-history list. */
export function parseClaudeSessionSummary(content: string): ToolSessionSummary {
  const summary: ToolSessionSummary = {
    sessionId: '',
    name: '',
    totalTokens: 0,
    messageCount: 0,
    lastMessageTs: 0,
    active: false,
  };

  let firstUserText = '';
  let summaryText = '';
  const lines = content.split('\n');

  for (const line of lines) {
    if (!line.trim()) { continue; }
    let rec: ClaudeLine;
    try { rec = JSON.parse(line) as ClaudeLine; } catch { continue; }

    if (rec.sessionId && !summary.sessionId) { summary.sessionId = rec.sessionId; }
    const ts = rec.timestamp ? new Date(rec.timestamp).getTime() / 1000 : 0;
    if (ts > summary.lastMessageTs) { summary.lastMessageTs = ts; }

    if (rec.type === 'summary' && typeof rec.summary === 'string') {
      summaryText = rec.summary;
      continue;
    }
    if (rec.isMeta || rec.isSidechain) { continue; }

    const msg = rec.message;
    if (!msg) { continue; }

    if (rec.type === 'user' && msg.role === 'user') {
      const text = extractText(msg.content);
      if (!firstUserText && text.trim()) { firstUserText = text; }
      summary.messageCount++;
      summary.totalTokens += estimateTokens(text);
      continue;
    }

    if (rec.type === 'assistant' && msg.role === 'assistant') {
      summary.messageCount++;
      const u = msg.usage;
      if (u) {
        summary.totalTokens += (u.input_tokens ?? 0)
          + (u.output_tokens ?? 0)
          + (u.cache_read_input_tokens ?? 0)
          + (u.cache_creation_input_tokens ?? 0);
      } else {
        summary.totalTokens += estimateTokens(extractText(msg.content));
      }
    }
  }

  summary.name = truncateSessionName(summaryText || firstUserText);
  return summary;
}

/**
 * Claude Code sanitizes a project cwd into a folder name by replacing every
 * non-alphanumeric character (including `:`, `\`, `/`, `.`, space) with `-`.
 * On Windows, `C:\Users\Foo\bar.baz` → `C--Users-Foo-bar-baz`.
 *
 * We return all plausible variants (with and without leading dash collapse)
 * so the watcher can try each against the real filesystem.
 */
export function sanitizeProjectCwd(cwd: string): string[] {
  const variants = new Set<string>();

  // Primary: replace any non-alphanumeric char with '-'
  variants.add(cwd.replace(/[^A-Za-z0-9]/g, '-'));

  // Alt: strip trailing/double dashes (Claude sometimes collapses these)
  const collapsed = cwd.replace(/[^A-Za-z0-9]/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '');
  variants.add(collapsed);

  // Alt: forward-slash form (if cwd arrives already normalized)
  const forward = cwd.replace(/\\/g, '/').replace(/[^A-Za-z0-9]/g, '-');
  variants.add(forward);

  return [...variants];
}
