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

    def intent(self, query: str, task: str, confidence: float) -> None:
        self._write("intent", {"query": query[:120], "task": task, "confidence": confidence})

    def retrieval(self, task: str, targets: list[str], tokens_used: int, budget: int) -> None:
        self._write("retrieval", {
            "task": task,
            "targets": targets[:20],
            "tokens_used": tokens_used,
            "budget": budget,
            "utilization": round(tokens_used / budget, 3) if budget else 0,
        })

    def index(self, path: str, symbols: int, skipped: bool = False) -> None:
        self._write("index", {"path": path, "symbols": symbols, "skipped": skipped})

    def error(self, message: str, **kwargs: Any) -> None:
        self._write("error", {"message": message, **kwargs})
