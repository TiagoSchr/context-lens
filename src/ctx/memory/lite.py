"""
Memory Lite — optional lightweight memory module.

Stores only:
- maps: short key/value project facts
- refs: references (file paths, docs, URLs)
- hotspots: frequently accessed symbols/files
- notes: small session notes
- rules: project conventions
"""
from __future__ import annotations
from typing import Any

VALID_KINDS = {"map", "ref", "hotspot", "note", "rule"}


def format_context_block(rows: list) -> str:
    """Format memory rows (from store.memory_list()) as a context block."""
    if not rows:
        return ""
    lines = ["## Project Memory"]
    current_kind = None
    for r in rows:
        kind = r["kind"] if hasattr(r, "__getitem__") else r.get("kind", "")
        key = r["key"] if hasattr(r, "__getitem__") else r.get("key", "")
        value = r["value"] if hasattr(r, "__getitem__") else r.get("value", "")
        if kind != current_kind:
            current_kind = kind
            lines.append(f"\n[{kind}]")
        key_part = f"{key}: " if key else ""
        lines.append(f"  {key_part}{value}")
    return "\n".join(lines)


class MemoryLite:
    def __init__(self, store: Any):
        self._store = store

    def set(self, kind: str, key: str, value: str, ttl: int | None = None) -> None:
        if kind not in VALID_KINDS:
            raise ValueError(f"kind must be one of {VALID_KINDS}")
        self._store.memory_set(kind, key, value, ttl)
        self._store.commit()

    def get(self, kind: str, key: str | None = None) -> list[dict]:
        rows = self._store.memory_get(kind, key)
        return [dict(r) for r in rows]

    def list_all(self) -> list[dict]:
        rows = self._store.memory_list()
        return [dict(r) for r in rows]

    def delete(self, id_: int) -> None:
        self._store.memory_delete(id_)
        self._store.commit()

    def purge_expired(self) -> int:
        n = self._store.memory_purge_expired()
        self._store.commit()
        return n

    def format_for_context(self, kinds: list[str] | None = None) -> str:
        """Render memory as a compact context block."""
        rows = self.list_all()
        if kinds:
            rows = [r for r in rows if r["kind"] in kinds]
        if not rows:
            return ""
        lines = ["=== MEMORY LITE ==="]
        current_kind = None
        for r in rows:
            if r["kind"] != current_kind:
                current_kind = r["kind"]
                lines.append(f"\n[{current_kind}]")
            key_part = f"{r['key']}: " if r["key"] else ""
            lines.append(f"  {key_part}{r['value']}")
        return "\n".join(lines)
