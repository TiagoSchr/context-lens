"""Fast file hashing for incremental indexing."""
from __future__ import annotations
import hashlib
from pathlib import Path


def hash_file(path: Path, block_size: int = 65536) -> str:
    """Return SHA-1 hex digest of a file. Fast enough for large repos."""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while chunk := f.read(block_size):
            h.update(chunk)
    return h.hexdigest()


def hash_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()
