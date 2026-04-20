/**
 * codexParser.ts — pure parser for Codex CLI rollout JSONL files.
 *
 * Rollouts live at: ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl
 *
 * Event shape (one JSON object per line):
 *   { timestamp, type, payload }
 *
 * Relevant types:
 *   - `session_meta`                        — first line; `payload.cwd`, `payload.id`
 *   - `response_item.payload.type=message`  — user/assistant message (content blocks)
 *   - `response_item.payload.type=reasoning` — reasoning summary
 *   - `response_item.payload.type=function_call` — tool call
 *   - `event_msg.payload.type=token_count`  — cumulative real usage
 *   - `event_msg.payload.type=agent_reasoning` — reasoning text (alt)
 *   - `turn_context.payload.model`          — model id
 */

import {
  ToolSessionSummary,
  ToolStats,
  emptyToolStats,
  estimateTokens,
  truncateSessionName,
} from './toolStats';

interface CodexLine {
  timestamp?: string;
  type?: string;
  payload?: Record<string, unknown>;
}

interface TokenCountInfo {
  total_token_usage?: {
    input_tokens?: number;
    cached_input_tokens?: number;
    output_tokens?: number;
    reasoning_output_tokens?: number;
    total_tokens?: number;
  };
}

interface CodexSessionMeta {
  id?: string;
  cwd?: string;
  timestamp?: string;
}

interface ThreadNameUpdatedInfo {
  thread_id?: string;
  thread_name?: string;
}

function extractMessageText(content: unknown): string {
  if (!Array.isArray(content)) { return ''; }
  const parts: string[] = [];
  for (const block of content) {
    if (!block || typeof block !== 'object') { continue; }
    const b = block as Record<string, unknown>;
    if (typeof b.text === 'string') { parts.push(b.text); }
  }
  return parts.join('\n');
}

function extractUserIntent(text: string): string {
  // Codex may wrap the real prompt inside IDE context boilerplate. We still
  // want the user-authored tail after "## My request for Codex:".
  const trimmed = text.trim();
  if (!trimmed) { return ''; }
  if (trimmed.startsWith('<environment_context')) { return ''; }
  if (trimmed.startsWith('<turn_aborted>')) { return ''; }
  if (trimmed.startsWith('<permissions')) { return ''; }

  const marker = '## My request for Codex:';
  const idx = trimmed.indexOf(marker);
  if (idx >= 0) { return trimmed.slice(idx + marker.length).trim(); }

  if (trimmed.startsWith('# Context from my IDE setup')) {
    return '';
  }
  return trimmed;
}

/** Parse the first line (session_meta) to extract session id + cwd. */
export function parseCodexSessionMeta(line: string): CodexSessionMeta | null {
  if (!line.trim()) { return null; }
  try {
    const rec = JSON.parse(line) as CodexLine;
    if (rec.type !== 'session_meta' || !rec.payload) { return null; }
    const p = rec.payload as CodexSessionMeta;
    return { id: p.id, cwd: p.cwd, timestamp: p.timestamp };
  } catch { return null; }
}

export function parseCodexRollout(lines: string[]): ToolStats {
  const result = emptyToolStats('codex');
  let firstUserIntent = '';
  let threadName = '';
  let cumulativeTotal: TokenCountInfo['total_token_usage'] | undefined;

  for (const line of lines) {
    if (!line.trim()) { continue; }
    let rec: CodexLine;
    try { rec = JSON.parse(line) as CodexLine; } catch { continue; }

    const ts = rec.timestamp ? new Date(rec.timestamp).getTime() / 1000 : 0;
    if (ts > result.lastMessageTs) { result.lastMessageTs = ts; }

    const p = rec.payload ?? {};

    if (rec.type === 'session_meta') {
      const meta = p as CodexSessionMeta;
      if (meta.id) { result.sessionId = meta.id; }
      continue;
    }

    if (rec.type === 'turn_context') {
      const model = (p as Record<string, unknown>).model;
      if (typeof model === 'string' && model) { result.model = model; }
      continue;
    }

    if (rec.type === 'response_item') {
      const itemType = (p as Record<string, unknown>).type as string | undefined;
      if (itemType === 'message') {
        const role = (p as Record<string, unknown>).role as string | undefined;
        const text = extractMessageText((p as Record<string, unknown>).content);
        const userIntent = role === 'user' ? extractUserIntent(text) : '';
        if (role === 'user' && userIntent) {
          if (!firstUserIntent) { firstUserIntent = userIntent; }
          result.messageCount++;
        } else if (role === 'assistant') {
          result.messageCount++;
        }
      } else if (itemType === 'function_call') {
        result.toolCallCount++;
      }
      continue;
    }

    if (rec.type === 'event_msg') {
      const msgType = (p as Record<string, unknown>).type as string | undefined;
      if (msgType === 'token_count') {
        const info = (p as Record<string, unknown>).info as TokenCountInfo | null;
        if (info && info.total_token_usage) {
          cumulativeTotal = info.total_token_usage;
        }
      } else if (msgType === 'thread_name_updated') {
        const info = p as ThreadNameUpdatedInfo;
        if (typeof info.thread_name === 'string' && info.thread_name.trim()) {
          threadName = info.thread_name.trim();
        }
      } else if (msgType === 'user_message') {
        // Newer Codex format: actual user request is in event_msg.user_message
        const msg = (p as Record<string, unknown>).message;
        if (typeof msg === 'string') {
          const intent = extractUserIntent(msg);
          if (intent) {
            if (!firstUserIntent) { firstUserIntent = intent; }
            result.messageCount++;
          }
        }
      } else if (msgType === 'agent_message') {
        result.messageCount++;
      } else if (msgType === 'exec_command_end' || msgType === 'patch_apply_end') {
        result.toolCallCount++;
      }
      continue;
    }
  }

  if (cumulativeTotal) {
    const input = cumulativeTotal.input_tokens ?? 0;
    const cached = cumulativeTotal.cached_input_tokens ?? 0;
    const output = cumulativeTotal.output_tokens ?? 0;
    const reasoning = cumulativeTotal.reasoning_output_tokens ?? 0;
    // Report non-cached input as "input" and cached as part of "reasoning"
    // (surrogate for cached/context tokens in the UI card).
    result.inputTokens = Math.max(0, input - cached);
    result.reasoningTokens = cached + reasoning;
    result.outputTokens = output;
    result.tokensFromUsage = true;
    result.totalTokens = cumulativeTotal.total_tokens
      ?? (result.inputTokens + result.outputTokens + result.reasoningTokens);
  } else {
    result.totalTokens = result.inputTokens + result.outputTokens + result.reasoningTokens;
  }

  result.sessionName = truncateSessionName(threadName || firstUserIntent);
  return result;
}

/** Lightweight scan — used for history list (no per-line deep parse). */
export function parseCodexSessionSummary(content: string): ToolSessionSummary {
  const out: ToolSessionSummary = {
    sessionId: '',
    name: '',
    totalTokens: 0,
    messageCount: 0,
    lastMessageTs: 0,
    active: false,
  };
  let firstUserIntent = '';
  let threadName = '';
  let cumulativeTotal: TokenCountInfo['total_token_usage'] | undefined;
  const lines = content.split('\n');

  for (const line of lines) {
    if (!line.trim()) { continue; }
    let rec: CodexLine;
    try { rec = JSON.parse(line) as CodexLine; } catch { continue; }
    const ts = rec.timestamp ? new Date(rec.timestamp).getTime() / 1000 : 0;
    if (ts > out.lastMessageTs) { out.lastMessageTs = ts; }

    const p = rec.payload ?? {};
    if (rec.type === 'session_meta') {
      const meta = p as CodexSessionMeta;
      if (meta.id) { out.sessionId = meta.id; }
      continue;
    }
    if (rec.type === 'response_item') {
      const itemType = (p as Record<string, unknown>).type as string | undefined;
      if (itemType === 'message') {
        const role = (p as Record<string, unknown>).role as string | undefined;
        const text = extractMessageText((p as Record<string, unknown>).content);
        const userIntent = role === 'user' ? extractUserIntent(text) : '';
        if (role === 'user' && userIntent) {
          if (!firstUserIntent) { firstUserIntent = userIntent; }
          out.messageCount++;
        } else if (role === 'assistant') {
          out.messageCount++;
        }
      }
    } else if (rec.type === 'event_msg') {
      const msgType = (p as Record<string, unknown>).type as string | undefined;
      if (msgType === 'token_count') {
        const info = (p as Record<string, unknown>).info as TokenCountInfo | null;
        if (info && info.total_token_usage) { cumulativeTotal = info.total_token_usage; }
      } else if (msgType === 'thread_name_updated') {
        const info = p as ThreadNameUpdatedInfo;
        if (typeof info.thread_name === 'string' && info.thread_name.trim()) {
          threadName = info.thread_name.trim();
        }
      } else if (msgType === 'user_message') {
        const msg = (p as Record<string, unknown>).message;
        if (typeof msg === 'string') {
          const intent = extractUserIntent(msg);
          if (intent) {
            if (!firstUserIntent) { firstUserIntent = intent; }
            out.messageCount++;
          }
        }
      } else if (msgType === 'agent_message') {
        out.messageCount++;
      }
    }
  }

  if (cumulativeTotal) {
    out.totalTokens = cumulativeTotal.total_tokens
      ?? ((cumulativeTotal.input_tokens ?? 0)
         + (cumulativeTotal.output_tokens ?? 0)
         + (cumulativeTotal.reasoning_output_tokens ?? 0));
  } else {
    out.totalTokens = estimateTokens(firstUserIntent);
  }
  out.name = truncateSessionName(threadName || firstUserIntent);
  return out;
}
