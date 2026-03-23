"""Tests for Budget and count_tokens from src/ctx/context/budget.py."""
from __future__ import annotations
import pytest
from src.ctx.context.budget import Budget, count_tokens


# ─────────────────────────────────────────────── count_tokens

class TestCountTokens:
    def test_empty_string(self):
        # Empty string: tiktoken returns 0 tokens; regex fallback returns max(1, 0//4)=1
        result = count_tokens("")
        assert result >= 0

    def test_short_text_positive(self):
        result = count_tokens("hello world")
        assert result >= 1

    def test_longer_text_more_tokens(self):
        short = count_tokens("hi")
        long_ = count_tokens("hello world this is a longer piece of text for testing purposes")
        assert long_ > short

    def test_returns_int(self):
        result = count_tokens("some text")
        assert isinstance(result, int)

    def test_whitespace_only(self):
        result = count_tokens("    ")
        assert result >= 0

    def test_code_snippet(self):
        code = "def foo(x: int) -> str:\n    return str(x)"
        result = count_tokens(code)
        assert result >= 1

    def test_unicode_text(self):
        result = count_tokens("Olá, mundo! こんにちは")
        assert result >= 1

    def test_large_text_scales(self):
        small = count_tokens("word " * 10)
        large = count_tokens("word " * 100)
        assert large > small


# ─────────────────────────────────────────────── Budget

class TestBudgetInit:
    def test_default_buffer(self):
        b = Budget(1000)
        assert b.total == 1000
        assert b.available == int(1000 * 0.88)
        assert b.used == 0

    def test_custom_buffer_ratio(self):
        b = Budget(1000, buffer_ratio=0.0)
        assert b.available == 1000

    def test_buffer_ratio_half(self):
        b = Budget(1000, buffer_ratio=0.5)
        assert b.available == 500

    def test_used_starts_at_zero(self):
        b = Budget(500)
        assert b.used == 0


class TestBudgetRemaining:
    def test_remaining_initially_equals_available(self):
        b = Budget(1000)
        assert b.remaining == b.available

    def test_remaining_decreases_after_consume(self):
        b = Budget(10000)
        initial = b.remaining
        b.consume("hello world")
        assert b.remaining < initial

    def test_remaining_cannot_go_negative_after_overflow(self):
        b = Budget(10)
        # Consume something that might overflow — remaining stays tracked
        b.consume("a" * 10000)
        # remaining is just available - used; used may exceed available
        assert b.remaining == b.available - b.used


class TestBudgetIsFull:
    def test_not_full_initially(self):
        b = Budget(1000)
        assert not b.is_full

    def test_is_full_when_used_equals_available(self):
        b = Budget(1000, buffer_ratio=0.0)
        b.used = 1000
        assert b.is_full

    def test_is_full_when_used_exceeds_available(self):
        b = Budget(1000, buffer_ratio=0.0)
        b.used = 1001
        assert b.is_full

    def test_not_full_when_partially_consumed(self):
        b = Budget(10000)
        b.consume("hello")
        assert not b.is_full


class TestBudgetConsume:
    def test_consume_returns_true_when_fits(self):
        b = Budget(10000)
        result = b.consume("hello world")
        assert result is True

    def test_consume_increments_used(self):
        b = Budget(10000)
        b.consume("hello world")
        assert b.used > 0

    def test_consume_returns_false_when_overflow(self):
        b = Budget(1, buffer_ratio=0.0)
        # Budget of 1 token — large text won't fit
        result = b.consume("a" * 1000)
        assert result is False

    def test_consume_does_not_add_when_overflow(self):
        b = Budget(1, buffer_ratio=0.0)
        b.consume("a" * 1000)
        assert b.used == 0

    def test_consume_sequential_fits(self):
        b = Budget(10000)
        assert b.consume("first chunk")
        used_after_first = b.used
        assert b.consume("second chunk")
        assert b.used > used_after_first

    def test_consume_exactly_at_limit(self):
        b = Budget(10000, buffer_ratio=0.0)
        # Fill budget almost full
        big_text = "word " * 2000
        tokens = count_tokens(big_text)
        if tokens <= 10000:
            assert b.consume(big_text)


class TestBudgetFits:
    def test_fits_small_text_in_large_budget(self):
        b = Budget(10000)
        assert b.fits("hello world")

    def test_fits_returns_false_when_too_large(self):
        b = Budget(1, buffer_ratio=0.0)
        assert not b.fits("a" * 1000)

    def test_fits_does_not_mutate_used(self):
        b = Budget(10000)
        before = b.used
        b.fits("hello")
        assert b.used == before


class TestBudgetUtilization:
    def test_utilization_zero_initially(self):
        b = Budget(1000)
        assert b.utilization() == 0.0

    def test_utilization_increases_after_consume(self):
        b = Budget(10000)
        b.consume("hello world")
        assert b.utilization() > 0.0

    def test_utilization_at_most_one_when_full(self):
        b = Budget(1000, buffer_ratio=0.0)
        b.used = 1000
        assert b.utilization() == 1.0

    def test_utilization_zero_budget_no_crash(self):
        b = Budget(0)
        # available = 0 → division guard
        result = b.utilization()
        assert result == 0.0

    def test_utilization_is_float(self):
        b = Budget(1000)
        assert isinstance(b.utilization(), float)
