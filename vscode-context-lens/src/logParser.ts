/**
 * logParser.ts — parses .ctx/log.jsonl and .ctx/stats.json without any subprocess.
 * All reads are sync file reads for maximum performance.
 */

export interface TaskStat {
  count: number;
  avgUsed: number;
  avgRaw: number;
  avgPct: number;
}

export interface ToolStat {
  count: number;
  totalUsed: number;
  totalRaw: number;
  totalSaved: number;
  avgPct: number;
  lastTs: number;
}

export interface RecentQuery {
  ts: number;
  task: string;
  tokensUsed: number;
  tokensSaved: number;
  savingPct: number;
  query: string;
}

export interface LensStats {
  /** Whether optimization is active (from config.json `enabled` field) */
  enabled: boolean;
  /** Whether a .ctx index exists in the workspace */
  indexed: boolean;

  // Index info
  files: number;
  symbols: number;
  dbKb: number;
  lastIndexed: number; // unix timestamp
  tokenBudget: number;
  targetBudgets: Record<string, number>;
  projectTokensTotal: number;
  byLanguage: Record<string, number>;

  // Session info
  sessionId: number | null;
  sessionName: string;

  // Active AI tool detected by the MCP server (claude, copilot, codex, cursor, unknown)
  activeTool: string;

  // Economy (all-time)
  totalQueries: number;
  totalTokensUsed: number;
  totalTokensRaw: number;
  totalTokensSaved: number;
  avgSavingPct: number;

  // Economy (current session — by session_id when available, else since last index)
  sessionQueries: number;
  sessionTokensUsed: number;
  sessionTokensRaw: number;
  sessionTokensSaved: number;
  sessionSavingPct: number;

  // Per-task breakdown
  byTask: Record<string, TaskStat>;

  // Per-tool breakdown (claude, copilot, codex, etc.)
  byTool: Record<string, ToolStat>;

  // Current session broken down by tool.
  sessionByTool: Record<string, ToolStat>;

  // Last N queries
  lastQueries: RecentQuery[];

  // Timestamp of the most recent query (for "live" indicator)
  lastQueryTs: number;

  // Active tool session stats (derived from log.jsonl for the current session+tool)
  // Used by the sidebar when CopilotWatcher has no data (Codex, Claude, Cursor)
  activeToolSessionQueries: number;
  activeToolSessionTokensUsed: number;
  activeToolSessionTokensRaw: number;
  activeToolSessionTokensSaved: number;
  activeToolSessionSavingPct: number;
}

export function emptyStats(): LensStats {
  return {
    enabled: true,
    indexed: false,
    files: 0,
    symbols: 0,
    dbKb: 0,
    lastIndexed: 0,
    tokenBudget: 8000,
    targetBudgets: {},
    projectTokensTotal: 0,
    byLanguage: {},
    sessionId: null,
    sessionName: '',
    activeTool: 'unknown',
    totalQueries: 0,
    totalTokensUsed: 0,
    totalTokensRaw: 0,
    totalTokensSaved: 0,
    avgSavingPct: 0,
    sessionQueries: 0,
    sessionTokensUsed: 0,
    sessionTokensRaw: 0,
    sessionTokensSaved: 0,
    sessionSavingPct: 0,
    byTask: {},
    byTool: {},
    sessionByTool: {},
    lastQueries: [],
    lastQueryTs: 0,
    activeToolSessionQueries: 0,
    activeToolSessionTokensUsed: 0,
    activeToolSessionTokensRaw: 0,
    activeToolSessionTokensSaved: 0,
    activeToolSessionSavingPct: 0,
  };
}

/**
 * Parse raw log.jsonl text + config JSON + stats JSON into a LensStats object.
 * Pure function — no I/O here; caller provides file contents.
 */
export function parseLensData(
  logContent: string,
  configJson: Record<string, unknown>,
  statsJson: Record<string, unknown> | null,
  sessionJson?: Record<string, unknown> | null,
): LensStats {
  const result = emptyStats();

  // ── Config ────────────────────────────────────────────────────────────────
  result.enabled = configJson.enabled !== false;
  result.tokenBudget = (configJson.token_budget as number) ?? 8000;
  if (configJson.target_budgets && typeof configJson.target_budgets === 'object') {
    result.targetBudgets = configJson.target_budgets as Record<string, number>;
  }

  // ── Stats.json (written by lens index) ───────────────────────────────────
  if (statsJson) {
    result.indexed = true;
    result.files = (statsJson.files as number) ?? 0;
    result.symbols = (statsJson.symbols as number) ?? 0;
    result.dbKb = (statsJson.db_kb as number) ?? 0;
    result.lastIndexed = (statsJson.last_indexed as number) ?? 0;
    result.tokenBudget = (statsJson.token_budget as number) ?? result.tokenBudget;
    result.projectTokensTotal = (statsJson.project_tokens_total as number) ?? 0;
    result.byLanguage = (statsJson.by_language as Record<string, number>) ?? {};
  }

  // ── Session.json (written by MCP server) ─────────────────────────────────
  const activeSessionId = sessionJson ? (sessionJson.id as number) ?? null : null;
  result.sessionId = activeSessionId;
  result.sessionName = sessionJson ? (sessionJson.name as string) ?? '' : '';
  result.activeTool = sessionJson ? (sessionJson.tool as string) ?? 'unknown' : 'unknown';

  // ── Log.jsonl ─────────────────────────────────────────────────────────────
  const lines = logContent.split('\n').filter((l) => l.trim().length > 0);
  const retrievals: Array<Record<string, unknown>> = [];

  for (const line of lines) {
    try {
      const rec = JSON.parse(line) as Record<string, unknown>;
      if (rec.event === 'retrieval') {
        retrievals.push(rec);
      }
    } catch {
      // malformed line — skip
    }
  }

  if (retrievals.length === 0) {
    return result;
  }

  const projTokens = result.projectTokensTotal;

  function rawTokens(r: Record<string, unknown>): number {
    const raw = (r.tokens_raw as number) ?? 0;
    if (raw > 0) { return raw; }
    return projTokens || (r.budget as number) || result.tokenBudget;
  }

  // All-time aggregates
  result.totalQueries = retrievals.length;
  result.totalTokensUsed = retrievals.reduce((s, r) => s + ((r.tokens_used as number) ?? 0), 0);
  result.totalTokensRaw = retrievals.reduce((s, r) => s + rawTokens(r), 0);
  result.totalTokensSaved = Math.max(0, result.totalTokensRaw - result.totalTokensUsed);
  result.avgSavingPct =
    result.totalTokensRaw > 0
      ? (1 - result.totalTokensUsed / result.totalTokensRaw) * 100
      : 0;

  // Session aggregates — filter by session_id when available, else fall back to timestamp
  const session = activeSessionId !== null
    ? retrievals.filter((r) => (r.session_id as number) === activeSessionId)
    : retrievals.filter((r) => (r.ts as number) >= result.lastIndexed);
  result.sessionQueries = session.length;
  result.sessionTokensUsed = session.reduce((s, r) => s + ((r.tokens_used as number) ?? 0), 0);
  result.sessionTokensRaw = session.reduce((s, r) => s + rawTokens(r), 0);
  result.sessionTokensSaved = Math.max(0, result.sessionTokensRaw - result.sessionTokensUsed);
  result.sessionSavingPct =
    result.sessionTokensRaw > 0
      ? (1 - result.sessionTokensUsed / result.sessionTokensRaw) * 100
      : 0;

  // Per-task breakdown (dynamic — picks up any task name from logs)
  const taskNames = [...new Set(retrievals.map((r) => r.task as string).filter(Boolean))];
  for (const task of taskNames) {
    const recs = retrievals.filter((r) => r.task === task);
    if (recs.length === 0) { continue; }
    const avgUsed = recs.reduce((s, r) => s + ((r.tokens_used as number) ?? 0), 0) / recs.length;
    const avgRaw = recs.reduce((s, r) => s + rawTokens(r), 0) / recs.length;
    result.byTask[task] = {
      count: recs.length,
      avgUsed,
      avgRaw,
      avgPct: avgRaw > 0 ? (1 - avgUsed / avgRaw) * 100 : 0,
    };
  }

  // Per-tool breakdown — use explicit 'tool' field set by MCP server.
  // Old records without 'tool' field are tagged 'unknown'.
  function inferTool(r: Record<string, unknown>): string {
    if (r.tool && typeof r.tool === 'string') { return r.tool; }
    return 'unknown';
  }

  const toolNames = [...new Set(retrievals.map((r) => inferTool(r)))];
  for (const tool of toolNames) {
    const recs = retrievals.filter((r) => inferTool(r) === tool);
    if (recs.length === 0) { continue; }
    const totalUsed = recs.reduce((s, r) => s + ((r.tokens_used as number) ?? 0), 0);
    const totalRaw = recs.reduce((s, r) => s + rawTokens(r), 0);
    const totalSaved = Math.max(0, totalRaw - totalUsed);
    const lastTs = Math.max(...recs.map((r) => (r.ts as number) ?? 0));
    result.byTool[tool] = {
      count: recs.length,
      totalUsed,
      totalRaw,
      totalSaved,
      avgPct: totalRaw > 0 ? (1 - totalUsed / totalRaw) * 100 : 0,
      lastTs,
    };
  }

  // Current-session breakdown by tool.
  const sessionToolNames = [...new Set(session.map((r) => inferTool(r)))];
  for (const tool of sessionToolNames) {
    const recs = session.filter((r) => inferTool(r) === tool);
    if (recs.length === 0) { continue; }
    const totalUsed = recs.reduce((s, r) => s + ((r.tokens_used as number) ?? 0), 0);
    const totalRaw = recs.reduce((s, r) => s + rawTokens(r), 0);
    const totalSaved = Math.max(0, totalRaw - totalUsed);
    const sLastTs = Math.max(...recs.map((r) => (r.ts as number) ?? 0));
    result.sessionByTool[tool] = {
      count: recs.length,
      totalUsed,
      totalRaw,
      totalSaved,
      avgPct: totalRaw > 0 ? (1 - totalUsed / totalRaw) * 100 : 0,
      lastTs: sLastTs,
    };
  }

  // Last query timestamp
  if (retrievals.length > 0) {
    result.lastQueryTs = (retrievals[retrievals.length - 1].ts as number) ?? 0;
  }

  // Active tool — prefer session.json, then the most recent retrieval in the
  // current session, then the most recent retrieval overall.
  if (result.activeTool === 'unknown' && (session.length > 0 || retrievals.length > 0)) {
    const source = session.length > 0 ? session : retrievals;
    const lastTool = inferTool(source[source.length - 1]);
    if (lastTool !== 'unknown') {
      result.activeTool = lastTool;
    }
  }

  if (result.activeTool in result.targetBudgets) {
    result.tokenBudget = result.targetBudgets[result.activeTool];
  }

  // Active tool session stats — queries in the current session tagged with the active tool.
  // Provides the sidebar with session-level data for non-Copilot tools (Codex, Claude, Cursor).
  if (result.activeTool !== 'unknown') {
    const toolSession = result.sessionByTool[result.activeTool];
    if (toolSession) {
      result.activeToolSessionQueries = toolSession.count;
      result.activeToolSessionTokensUsed = toolSession.totalUsed;
      result.activeToolSessionTokensRaw = toolSession.totalRaw;
      result.activeToolSessionTokensSaved = toolSession.totalSaved;
      result.activeToolSessionSavingPct = toolSession.avgPct;
    }
  }

  // Last 6 queries (newest first)
  result.lastQueries = retrievals
    .slice(-6)
    .reverse()
    .map((r) => {
      const raw = rawTokens(r);
      const used = (r.tokens_used as number) ?? 0;
      return {
        ts: (r.ts as number) ?? 0,
        task: (r.task as string) ?? 'unknown',
        tokensUsed: used,
        tokensSaved: Math.max(0, raw - used),
        savingPct: raw > 0 ? (1 - used / raw) * 100 : 0,
        query: ((r.query as string) ?? '').slice(0, 80),
      };
    });

  return result;
}

/** Format a number like 12400 → "12.4k" */
export function fmtK(n: number): string {
  if (n >= 1_000_000) { return `${(n / 1_000_000).toFixed(1)}M`; }
  if (n >= 1000) { return `${(n / 1000).toFixed(1)}k`; }
  return `${n}`;
}

/** Format a unix timestamp as "MM/DD HH:MM" */
export function fmtTime(ts: number): string {
  if (!ts) { return 'never'; }
  const d = new Date(ts * 1000);
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  return `${dd}/${mm} ${hh}:${mi}`;
}
