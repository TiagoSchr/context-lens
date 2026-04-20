"""Structured error codes for Context Lens v2."""
from __future__ import annotations


class LensError(Exception):
    """Base error for all Context Lens failures."""

    code: str = "LENS_ERROR"

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code or self.__class__.code

    def to_dict(self) -> dict:
        return {"error": self.code, "message": str(self)}


class IndexNotFound(LensError):
    """No .ctx/index.db found in project."""
    code = "INDEX_NOT_FOUND"

    def __init__(self, path: str = ""):
        hint = f" at {path}" if path else ""
        super().__init__(
            f"No index found{hint}. Run `lens index` inside your project first.",
            self.code,
        )


class IndexCorrupted(LensError):
    """SQLite database is corrupted or unreadable."""
    code = "INDEX_CORRUPTED"

    def __init__(self, detail: str = ""):
        super().__init__(
            f"Index is corrupted{': ' + detail if detail else ''}. "
            "Run `lens index --force` to rebuild.",
            self.code,
        )


class BudgetExceeded(LensError):
    """Context exceeded the token budget."""
    code = "BUDGET_EXCEEDED"


class QueryTooShort(LensError):
    """Query is too short to produce useful results."""
    code = "QUERY_TOO_SHORT"

    def __init__(self, min_len: int = 3):
        super().__init__(
            f"Query must be at least {min_len} characters.",
            self.code,
        )


class SymbolNotFound(LensError):
    """Named symbol not found in the index."""
    code = "SYMBOL_NOT_FOUND"


class GitNotAvailable(LensError):
    """git CLI not found or not a git repository."""
    code = "GIT_NOT_AVAILABLE"


def format_error(exc: Exception) -> str:
    """Format any exception into a clean user-facing string."""
    if isinstance(exc, LensError):
        return f"[context-lens/{exc.code}] {exc}"
    return f"[context-lens] Error: {exc}"
