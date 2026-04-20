"""
Project health checks for Context Lens v2.

Surfaces stale index, uncovered files, config issues, and
integration status so users know when to re-run `lens index`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class HealthReport:
    is_healthy: bool = True
    warnings: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        self.is_healthy = False

    def suggest(self, msg: str) -> None:
        self.suggestions.append(msg)

    def note(self, msg: str) -> None:
        self.info.append(msg)


def check_health(store: Any, root: Path, cfg: dict) -> HealthReport:
    """Run all health checks and return a HealthReport."""
    report = HealthReport()

    # ── Index freshness ───────────────────────────────────────────────────────
    s = store.stats()
    last_indexed = s.get("last_indexed")
    if last_indexed:
        age_hours = (time.time() - last_indexed) / 3600
        if age_hours > 48:
            report.warn(
                f"Index is {age_hours:.0f}h old. "
                "Run `lens index` to pick up recent changes."
            )
        elif age_hours > 12:
            report.suggest(
                f"Index is {age_hours:.0f}h old — consider running `lens index`."
            )
        else:
            report.note(
                f"Index is {age_hours:.1f}h old (OK)."
            )
    else:
        report.warn("Index has never been built. Run `lens index`.")
        return report

    # ── Symbol density ────────────────────────────────────────────────────────
    if s["files"] > 0:
        density = s["symbols"] / s["files"]
        if density < 1.0:
            report.suggest(
                f"Low symbol density ({density:.1f} symbols/file). "
                "Consider `pip install context-lens-v2[parse]` for tree-sitter parsing."
            )
        else:
            report.note(f"Symbol density: {density:.1f} symbols/file (healthy).")

    # ── Budget check ─────────────────────────────────────────────────────────
    budget = cfg.get("token_budget", 8000)
    raw_str = store.get_meta("project_tokens_total")
    if raw_str:
        raw_tokens = int(raw_str)
        if budget >= raw_tokens:
            report.warn(
                f"Token budget ({budget:,}) ≥ project size ({raw_tokens:,}) — "
                "lens may return the entire project on every query (no savings)."
            )
            report.suggest(
                f"Lower the budget: `lens config token_budget {raw_tokens // 4}`"
            )
        else:
            pct = (1 - budget / raw_tokens) * 100
            report.note(
                f"Budget {budget:,} / project {raw_tokens:,} tokens → "
                f"~{pct:.0f}% savings per query."
            )

    # ── Integration files ────────────────────────────────────────────────────
    # Check both legacy and current paths for each tool
    integrations = {
        "Claude Code": [root / "CLAUDE.md"],
        "Cursor": [root / ".cursorrules", root / ".cursor" / "rules" / "lens.mdc"],
        "Copilot": [root / ".github" / "copilot-instructions.md"],
        "Codex / Agents": [root / ".codex" / "instructions.md", root / "AGENTS.md"],
        "Continue.dev": [root / ".continue" / "config.json"],
        "Zed": [root / ".zed" / "settings.json"],
        "MCP (VS Code)": [root / ".vscode" / "mcp.json"],
        "MCP (Cursor)": [root / ".cursor" / "mcp.json"],
        "MCP (Claude Code)": [root / ".claude" / "mcp.json"],
    }
    found_integrations = []
    for name, paths in integrations.items():
        for p in paths:
            if p.exists():
                try:
                    content = p.read_text(encoding="utf-8", errors="ignore")
                    if "lens_context" in content or "lens-mcp" in content or "context-lens" in content:
                        found_integrations.append(name)
                        break
                except OSError:
                    pass

    if found_integrations:
        report.note(f"Active integrations: {', '.join(found_integrations)}.")
    else:
        report.suggest(
            "No AI tool integrations found. "
            "Run `lens install` to configure MCP and instruction files."
        )

    # ── .ctx/log.jsonl ────────────────────────────────────────────────────────
    lp = root / ".ctx" / "log.jsonl"
    if not lp.exists():
        report.suggest("No query log found — run some `lens context` queries to start tracking savings.")

    # ── watchdog ────────────────────────────────────────────────────────────
    try:
        import watchdog  # noqa: F401
        report.note("watchdog installed — `lens watch` uses filesystem events (instant).")
    except ImportError:
        report.suggest(
            "`pip install watchdog` for instant file-change detection in `lens watch`."
        )

    return report


def format_health_report(report: HealthReport) -> str:
    lines: list[str] = []
    status = "HEALTHY" if report.is_healthy else "ISSUES FOUND"
    lines.append(f"## Context Lens Health — {status}")

    if report.warnings:
        lines.append("\n### Warnings")
        for w in report.warnings:
            lines.append(f"  ⚠  {w}")

    if report.suggestions:
        lines.append("\n### Suggestions")
        for s in report.suggestions:
            lines.append(f"  →  {s}")

    if report.info:
        lines.append("\n### Info")
        for i in report.info:
            lines.append(f"  ✓  {i}")

    return "\n".join(lines)
