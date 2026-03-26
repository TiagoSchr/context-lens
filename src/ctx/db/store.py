"""Read and write operations against the SQLite store."""
from __future__ import annotations
import sqlite3
import time
from typing import Any


class Store:
    """Thread-safe (single-writer) wrapper around the SQLite connection."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    # ------------------------------------------------------------------ files
    def get_file_hash(self, path: str) -> str | None:
        row = self._conn.execute(
            "SELECT hash FROM files WHERE path = ?", (path,)
        ).fetchone()
        return row["hash"] if row else None

    def upsert_file(self, path: str, hash_: str, language: str | None, size: int) -> int:
        row = self._conn.execute(
            """INSERT INTO files(path, hash, language, size_bytes, indexed_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET
                 hash=excluded.hash,
                 language=excluded.language,
                 size_bytes=excluded.size_bytes,
                 indexed_at=excluded.indexed_at
               RETURNING id""",
            (path, hash_, language, size, time.time()),
        ).fetchone()
        file_id: int = row["id"]
        self._conn.execute("DELETE FROM symbols WHERE file_id = ?", (file_id,))
        return file_id

    def get_file_id(self, path: str) -> int:
        row = self._conn.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()
        return row["id"] if row else -1

    def delete_file(self, path: str) -> None:
        self._conn.execute("DELETE FROM files WHERE path = ?", (path,))

    def list_indexed_paths(self, limit: int | None = None) -> list[str]:
        sql = "SELECT path FROM files ORDER BY path"
        if limit is not None:
            sql += f" LIMIT {limit}"
        rows = self._conn.execute(sql).fetchall()
        return [r["path"] for r in rows]

    # --------------------------------------------------------------- symbols
    def insert_symbols_batch(self, symbols: list[dict[str, Any]]) -> None:
        self._conn.executemany(
            """INSERT INTO symbols
               (file_id, name, kind, params, return_type, docstring, start_line, end_line, language, path)
               VALUES (:file_id, :name, :kind, :params, :return_type, :docstring,
                       :start_line, :end_line, :language, :path)""",
            symbols,
        )

    def search_symbols_fts(self, query: str, limit: int = 30) -> list[sqlite3.Row]:
        return self._conn.execute(
            """SELECT s.* FROM symbols s
               JOIN symbols_fts fts ON s.id = fts.rowid
               WHERE symbols_fts MATCH ?
               ORDER BY rank LIMIT ?""",
            (query, limit),
        ).fetchall()

    def get_symbols_for_file(self, path: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM symbols WHERE path = ? ORDER BY start_line", (path,)
        ).fetchall()

    def get_symbols_for_files(self, paths: list[str]) -> dict[str, list[sqlite3.Row]]:
        """Batch version: returns {path: [rows]} in a single SQL query."""
        if not paths:
            return {}
        placeholders = ",".join("?" * len(paths))
        rows = self._conn.execute(
            f"SELECT * FROM symbols WHERE path IN ({placeholders}) ORDER BY path, start_line",
            paths,
        ).fetchall()
        result: dict[str, list] = {p: [] for p in paths}
        for row in rows:
            result[row["path"]].append(row)
        return result

    def get_all_symbols(self, limit: int = 500) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM symbols ORDER BY path, start_line LIMIT ?", (limit,)
        ).fetchall()

    def get_symbols_by_name(self, name: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM symbols WHERE name = ?", (name,)
        ).fetchall()

    def get_symbols_by_kind(self, kind: str, limit: int = 100) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM symbols WHERE kind = ? ORDER BY path, start_line LIMIT ?",
            (kind, limit),
        ).fetchall()

    # ----------------------------------------------------------- project map
    def set_project_map(self, key: str, value: str) -> None:
        self._conn.execute(
            """INSERT INTO project_map(key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, value, time.time()),
        )

    def get_project_map(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM project_map WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def get_all_project_map(self) -> dict[str, str]:
        rows = self._conn.execute("SELECT key, value FROM project_map").fetchall()
        return {r["key"]: r["value"] for r in rows}

    # --------------------------------------------------------------- memory
    def memory_set(self, kind: str, key: str, value: str, ttl: int | None = None) -> None:
        expires = time.time() + ttl if ttl else None
        self._conn.execute(
            """INSERT INTO memory_lite(kind, key, value, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(kind, key) DO UPDATE SET
                 value=excluded.value,
                 created_at=excluded.created_at,
                 expires_at=excluded.expires_at""",
            (kind, key, value, time.time(), expires),
        )

    def memory_get(self, kind: str, key: str | None = None) -> list[sqlite3.Row]:
        now = time.time()
        if key:
            return self._conn.execute(
                "SELECT * FROM memory_lite WHERE kind=? AND key=? AND (expires_at IS NULL OR expires_at > ?)",
                (kind, key, now),
            ).fetchall()
        return self._conn.execute(
            "SELECT * FROM memory_lite WHERE kind=? AND (expires_at IS NULL OR expires_at > ?)",
            (kind, now),
        ).fetchall()

    def memory_list(self) -> list[sqlite3.Row]:
        now = time.time()
        return self._conn.execute(
            "SELECT * FROM memory_lite WHERE expires_at IS NULL OR expires_at > ? ORDER BY kind, key",
            (now,),
        ).fetchall()

    def memory_delete(self, id_: int) -> None:
        self._conn.execute("DELETE FROM memory_lite WHERE id = ?", (id_,))

    def memory_purge_expired(self) -> int:
        cur = self._conn.execute("DELETE FROM memory_lite WHERE expires_at <= ?", (time.time(),))
        return cur.rowcount

    # ---------------------------------------------------------------- stats
    def stats(self) -> dict[str, Any]:
        files = self._conn.execute("SELECT COUNT(*) as n FROM files").fetchone()["n"]
        syms = self._conn.execute("SELECT COUNT(*) as n FROM symbols").fetchone()["n"]
        kinds = self._conn.execute(
            "SELECT kind, COUNT(*) as n FROM symbols GROUP BY kind ORDER BY n DESC"
        ).fetchall()
        langs = self._conn.execute(
            "SELECT language, COUNT(*) as n FROM files GROUP BY language ORDER BY n DESC"
        ).fetchall()
        last_indexed = self._conn.execute(
            "SELECT MAX(indexed_at) as t FROM files"
        ).fetchone()["t"]
        total_bytes = self._conn.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) as n FROM files"
        ).fetchone()["n"]
        return {
            "files": files,
            "symbols": syms,
            "by_kind": {r["kind"]: r["n"] for r in kinds},
            "by_language": {r["language"]: r["n"] for r in langs if r["language"]},
            "last_indexed": last_indexed,
            "total_bytes": total_bytes,
        }

    # ---------------------------------------------------------------- commit
    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
