"""Token budget management with optional tiktoken support."""
from __future__ import annotations

_tiktoken_enc = None
_tiktoken_available: bool | None = None


def _get_encoder():
    global _tiktoken_enc, _tiktoken_available
    if _tiktoken_available is None:
        try:
            import tiktoken
            _tiktoken_enc = tiktoken.get_encoding("cl100k_base")
            _tiktoken_available = True
        except ImportError:
            _tiktoken_available = False
    return _tiktoken_enc


def count_tokens(text: str) -> int:
    """Count tokens. Uses tiktoken if available, else estimates at ~4 chars/token."""
    enc = _get_encoder()
    if enc is not None:
        return len(enc.encode(text))
    return max(1, len(text) // 4)


def compute_tokens_raw(
    root: "Path",
    included_paths: list[str],
    tokens_used: int,
    budget: int,
) -> int:
    """Compute realistic baseline for economy metrics.

    The baseline is the raw token count of the files actually included in
    the context — this represents what the model would have consumed by
    reading those same files directly (e.g. via ``read_file``).

    The result is guaranteed ``>= tokens_used`` so that savings are never
    negative.
    """
    from pathlib import Path as _Path

    raw = 0
    for p_str in included_paths:
        fp = _Path(p_str)
        if not fp.exists():
            fp = root / p_str
        if fp.exists():
            try:
                raw += count_tokens(fp.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    return max(raw, tokens_used, budget)


class Budget:
    """Tracks token usage against a fixed budget with a safety buffer."""

    def __init__(self, total: int, buffer_ratio: float = 0.12):
        self.total = total
        self.available = int(total * (1.0 - buffer_ratio))
        self.used = 0

    @property
    def remaining(self) -> int:
        return self.available - self.used

    @property
    def is_full(self) -> bool:
        return self.used >= self.available

    def consume(self, text: str) -> bool:
        """Try to consume tokens for text. Returns True if fits, False if overflows."""
        tokens = count_tokens(text)
        if self.used + tokens > self.available:
            return False
        self.used += tokens
        return True

    def fits(self, text: str) -> bool:
        return count_tokens(text) <= self.remaining

    def utilization(self) -> float:
        return self.used / self.available if self.available else 0.0
