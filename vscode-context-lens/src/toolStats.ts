/**
 * toolStats.ts — unified token-economy stats for Claude Code and Codex CLI.
 *
 * Copilot Chat keeps its own richer `CopilotStats` shape (see copilotParser.ts),
 * but the sidebar can consume this common interface for any future tool and
 * for Claude/Codex native transcripts.
 */

export type ToolName = 'copilot' | 'claude' | 'codex';

/** Summary of a single transcript/session (for history list). */
export interface ToolSessionSummary {
  sessionId: string;
  name: string;
  totalTokens: number;
  messageCount: number;
  lastMessageTs: number; // unix seconds
  active: boolean;
}

/** Canonical per-tool stats shape used by Claude/Codex watchers. */
export interface ToolStats {
  tool: ToolName;

  /** Active session identifier (transcript file basename without extension). */
  sessionId: string;
  /** Human-readable session title. */
  sessionName: string;

  /** Total user + assistant messages. */
  messageCount: number;
  /** Tool / function calls issued by the assistant. */
  toolCallCount: number;

  /** Input / user tokens (real when usage is present, else estimated). */
  inputTokens: number;
  /** Assistant output tokens. */
  outputTokens: number;
  /** Reasoning / thinking tokens (Claude cache reads, Codex reasoning summary). */
  reasoningTokens: number;
  /** inputTokens + outputTokens + reasoningTokens. */
  totalTokens: number;

  /** Whether usage values come from the provider (true) or heuristic length/4 (false). */
  tokensFromUsage: boolean;

  /** Model string (e.g. "claude-sonnet-4-5-20250929", "gpt-5-codex"). */
  model: string;

  /** Unix seconds of the most recent event in the transcript. */
  lastMessageTs: number;

  /** Up to 10 most recent sessions ordered newest first. */
  allSessions: ToolSessionSummary[];
}

export function emptyToolStats(tool: ToolName): ToolStats {
  return {
    tool,
    sessionId: '',
    sessionName: '',
    messageCount: 0,
    toolCallCount: 0,
    inputTokens: 0,
    outputTokens: 0,
    reasoningTokens: 0,
    totalTokens: 0,
    tokensFromUsage: false,
    model: '',
    lastMessageTs: 0,
    allSessions: [],
  };
}

/** ~4 chars per token heuristic (matches copilotParser). */
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
