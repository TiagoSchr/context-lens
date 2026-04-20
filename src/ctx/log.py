"""Structured JSONL logging for intent, retrieval and indexing events."""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Any


class CtxLogger:
    def __init__(self, log_file: Path):
        self._path = log_file
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, event: str, data: dict[str, Any]) -> None:
        record = {"ts": time.time(), "event": event, **data}
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def intent(self, query: str, task: str, confidence: float,
               session_id: int | None = None) -> None:
        data: dict[str, Any] = {"query": query[:120], "task": task, "confidence": confidence}
        if session_id is not None:
            data["session_id"] = session_id
        self._write("intent", data)

    def retrieval(self, task: str, targets: list[str], tokens_used: int, budget: int,
                  tokens_raw: int = 0, tool: str | None = None,
                  session_id: int | None = None, query: str = "") -> None:
        data: dict[str, Any] = {
            "task": task,
            "targets": targets[:20],
            "tokens_used": tokens_used,
            "budget": budget,
            "utilization": round(tokens_used / budget, 3) if budget else 0,
        }
        if tokens_raw > 0:
            data["tokens_raw"] = tokens_raw
            data["real_saving_pct"] = round(
                (tokens_raw - tokens_used) / tokens_raw * 100, 1
            )
        if tool:
            data["tool"] = tool
        if session_id is not None:
            data["session_id"] = session_id
        if query:
            data["query"] = query[:120]
        self._write("retrieval", data)

    def index(self, path: str, symbols: int, skipped: bool = False) -> None:
        self._write("index", {"path": path, "symbols": symbols, "skipped": skipped})

    def error(self, message: str, **kwargs: Any) -> None:
        self._write("error", {"message": message, **kwargs})
