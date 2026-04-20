"""
Token analytics engine for Context Lens v2.

Reads .ctx/log.jsonl and surfaces savings trends, hotspot files,
task distribution and budget utilisation over time.
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


# ── log reader ────────────────────────────────────────────────────────────────

def _load_retrievals(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    records = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
            if r.get("event") == "retrieval":
                records.append(r)
        except Exception:
            pass
    return records


# ── aggregation helpers ───────────────────────────────────────────────────────

def _bucket_key(ts: float, period: str) -> str:
    t = time.localtime(ts)
    if period == "day":
        return time.strftime("%Y-%m-%d", t)
    if period == "week":
        # ISO week
        return time.strftime("%Y-W%W", t)
    return time.strftime("%Y-%m", t)  # month


def compute_summary(log_path: Path, project_tokens: int | None = None) -> dict[str, Any]:
    """
    Full analytics summary dict:
      - total_queries, total_tokens_used, total_tokens_saved, avg_saving_pct
      - by_task: {task: {count, avg_used, avg_saved_pct}}
      - by_day / by_week / by_month: [{date, queries, saved}]
      - hotspot_files: [(path, access_count)]
      - budget_utilisation: avg fraction of budget used (0-1)
    """
    records = _load_retrievals(log_path)
    if not records:
        return {"total_queries": 0}

    def _raw(r: dict) -> int:
        raw = r.get("tokens_raw", 0)
        return raw if raw > 0 else (project_tokens or r.get("budget", r["tokens_used"]))

    total_used = sum(r["tokens_used"] for r in records)
    total_raw = sum(_raw(r) for r in records)
    total_saved = max(0, total_raw - total_used)
    avg_saving = (1 - total_used / total_raw) * 100 if total_raw else 0.0
    avg_util = sum(r.get("utilization", 0) for r in records) / len(records)

    # ── per-task ────────────────────────────────────────────────────────────
    task_map: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        task_map[r.get("task", "unknown")].append(r)

    by_task: dict[str, Any] = {}
    for task, recs in task_map.items():
        avg_u = sum(r["tokens_used"] for r in recs) / len(recs)
        avg_r = sum(_raw(r) for r in recs) / len(recs)
        by_task[task] = {
            "count": len(recs),
            "avg_used": round(avg_u),
            "avg_saved_pct": round((1 - avg_u / avg_r) * 100) if avg_r else 0,
        }

    # ── per-tool ────────────────────────────────────────────────────────────
    # Use explicit 'tool' field set by MCP server via env detection.
    # Old records without 'tool' field are tagged "unknown".
    def _infer_tool(r: dict) -> str:
        t = r.get("tool")
        if t:
            return t
        return "unknown"

    tool_map: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        tool_map[_infer_tool(r)].append(r)

    by_tool: dict[str, Any] = {}
    for tool, recs in tool_map.items():
        total_u = sum(r["tokens_used"] for r in recs)
        total_r = sum(_raw(r) for r in recs)
        total_s = max(0, total_r - total_u)
        by_tool[tool] = {
            "count": len(recs),
            "total_used": total_u,
            "total_raw": total_r,
            "total_saved": total_s,
            "avg_saved_pct": round((1 - total_u / total_r) * 100) if total_r else 0,
        }

    # ── time series ──────────────────────────────────────────────────────────
    def _series(period: str) -> list[dict]:
        buckets: dict[str, dict] = {}
        for r in records:
            k = _bucket_key(r["ts"], period)
            if k not in buckets:
                buckets[k] = {"date": k, "queries": 0, "saved": 0}
            buckets[k]["queries"] += 1
            buckets[k]["saved"] += max(0, _raw(r) - r["tokens_used"])
        return sorted(buckets.values(), key=lambda x: x["date"])

    # ── file hotspots ────────────────────────────────────────────────────────
    file_counts: Counter = Counter()
    for r in records:
        for p in r.get("paths", []):
            file_counts[p] += 1

    return {
        "total_queries": len(records),
        "total_tokens_used": total_used,
        "total_tokens_saved": total_saved,
        "avg_saving_pct": round(avg_saving, 1),
        "avg_budget_utilisation": round(avg_util, 3),
        "by_task": by_task,
        "by_tool": by_tool,
        "by_day": _series("day"),
        "by_week": _series("week"),
        "by_month": _series("month"),
        "hotspot_files": file_counts.most_common(10),
    }


def format_report(summary: dict, period: str = "week") -> str:
    """Render analytics summary as a Markdown-style text report."""
    if summary.get("total_queries", 0) == 0:
        return "No queries recorded yet. Run `lens context` or use a MCP tool."

    lines: list[str] = []
    lines.append("## Context Lens — Token Analytics")
    lines.append(
        f"Total queries: {summary['total_queries']}  |  "
        f"Saved: ~{summary['total_tokens_saved']:,} tokens  |  "
        f"Avg saving: {summary['avg_saving_pct']:.1f}%  |  "
        f"Budget utilisation: {summary['avg_budget_utilisation']:.0%}"
    )

    lines.append("\n### Savings by task")
    lines.append(f"{'Task':<18} {'Queries':>8} {'Avg used':>10} {'Avg saved':>10}")
    lines.append("-" * 50)
    for task, data in sorted(summary["by_task"].items(), key=lambda x: -x[1]["count"]):
        lines.append(
            f"{task:<18} {data['count']:>8} "
            f"{data['avg_used']:>9}t "
            f"{data['avg_saved_pct']:>9}%"
        )

    if summary.get("by_tool"):
        tool_labels = {
            "claude": "Claude Code",
            "copilot": "GitHub Copilot",
            "codex": "ChatGPT / Codex",
        }
        lines.append("\n### Savings by tool")
        lines.append(f"{'Tool':<18} {'Queries':>8} {'Used':>10} {'Saved':>10} {'Avg':>6}")
        lines.append("-" * 56)
        for tool, data in sorted(summary["by_tool"].items(), key=lambda x: -x[1]["total_saved"]):
            label = tool_labels.get(tool, tool.capitalize())
            lines.append(
                f"{label:<18} {data['count']:>8} "
                f"{data['total_used']:>9}t "
                f"{data['total_saved']:>9}t "
                f"{data['avg_saved_pct']:>5}%"
            )

    series = summary.get(f"by_{period}", [])
    if series:
        lines.append(f"\n### Savings by {period} (last 10)")
        lines.append(f"{'Date':<14} {'Queries':>8} {'Tokens saved':>14}")
        lines.append("-" * 38)
        for entry in series[-10:]:
            lines.append(
                f"{entry['date']:<14} {entry['queries']:>8} "
                f"{entry['saved']:>13,}"
            )

    if summary.get("hotspot_files"):
        lines.append("\n### Most accessed files")
        for path, count in summary["hotspot_files"]:
            lines.append(f"  {count:>4}x  {path}")

    return "\n".join(lines)
