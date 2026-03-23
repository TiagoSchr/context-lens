"""SQLite schema creation and migrations."""
from __future__ import annotations
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

DDL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

-- Tracked files with change detection
CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY,
    path        TEXT    UNIQUE NOT NULL,
    hash        TEXT    NOT NULL,
    language    TEXT,
    size_bytes  INTEGER DEFAULT 0,
    indexed_at  REAL    NOT NULL
);

-- Canonical symbol index (level1)
CREATE TABLE IF NOT EXISTS symbols (
    id          INTEGER PRIMARY KEY,
    file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    name        TEXT    NOT NULL,
    kind        TEXT    NOT NULL,   -- function|class|method|variable|interface|struct
    params      TEXT,               -- raw params string e.g. "(x: int, y=0)"
    return_type TEXT,
    docstring   TEXT,
    start_line  INTEGER,
    end_line    INTEGER,
    language    TEXT    NOT NULL,
    path        TEXT    NOT NULL    -- denormalized for fast reads
);

CREATE INDEX IF NOT EXISTS idx_symbols_name      ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_kind      ON symbols(kind);
CREATE INDEX IF NOT EXISTS idx_symbols_path_line ON symbols(path, start_line);
CREATE INDEX IF NOT EXISTS idx_symbols_file_line ON symbols(file_id, start_line);

-- FTS5 index for full-text search on level1 fields
CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
    name, kind, docstring, path,
    content = 'symbols',
    content_rowid = 'id',
    tokenize = 'ascii'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS symbols_ai AFTER INSERT ON symbols BEGIN
    INSERT INTO symbols_fts(rowid, name, kind, docstring, path)
    VALUES (new.id, new.name, new.kind, COALESCE(new.docstring,''), new.path);
END;

CREATE TRIGGER IF NOT EXISTS symbols_ad AFTER DELETE ON symbols BEGIN
    INSERT INTO symbols_fts(symbols_fts, rowid, name, kind, docstring, path)
    VALUES ('delete', old.id, old.name, old.kind, COALESCE(old.docstring,''), old.path);
END;

CREATE TRIGGER IF NOT EXISTS symbols_au AFTER UPDATE ON symbols BEGIN
    INSERT INTO symbols_fts(symbols_fts, rowid, name, kind, docstring, path)
    VALUES ('delete', old.id, old.name, old.kind, COALESCE(old.docstring,''), old.path);
    INSERT INTO symbols_fts(rowid, name, kind, docstring, path)
    VALUES (new.id, new.name, new.kind, COALESCE(new.docstring,''), new.path);
END;

-- Project map (level0 data)
CREATE TABLE IF NOT EXISTS project_map (
    id          INTEGER PRIMARY KEY,
    key         TEXT    UNIQUE NOT NULL,
    value       TEXT    NOT NULL,
    updated_at  REAL    NOT NULL
);

-- Optional lightweight memory
CREATE TABLE IF NOT EXISTS memory_lite (
    id          INTEGER PRIMARY KEY,
    kind        TEXT    NOT NULL,   -- map|ref|hotspot|note|rule
    key         TEXT,
    value       TEXT    NOT NULL,
    created_at  REAL    NOT NULL,
    expires_at  REAL    DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_kind ON memory_lite(kind);
CREATE INDEX IF NOT EXISTS idx_memory_key  ON memory_lite(key);
"""


def init_db(db_file: Path) -> sqlite3.Connection:
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_file), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    # Set or verify schema version
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
        conn.commit()
    return conn
