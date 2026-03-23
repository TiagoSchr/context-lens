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
