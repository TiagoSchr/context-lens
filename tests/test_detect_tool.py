"""Tests for _detect_tool priority chain and _detect_active_tool_by_transcript."""
from __future__ import annotations

import os
import time
import json
import types as _types
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ── _detect_tool tests ────────────────────────────────────────────────────────


class TestDetectToolPriority:
    """Verify priority ordering of _detect_tool()."""

    def _detect(self, monkeypatch, env: dict | None = None, cli_override: str | None = None,
                mcp_client: str | None = None, transcript_tool: str | None = None) -> str:
        """Helper: call _detect_tool with controlled env/state."""
        import src.ctx.mcp as mcp_mod
        from src.ctx.mcp import _detect_tool

        # Reset module-level state
        old_override = mcp_mod._cli_tool_override
        mcp_mod._cli_tool_override = cli_override

        # Clean environment
        for var in ("CONTEXT_LENS_CLIENT", "LENS_TARGET", "CODEX_INTERNAL_ORIGINATOR_OVERRIDE",
                     "CODEX_THREAD_ID", "CODEX_SANDBOX_ID", "CLAUDE_CODE_SSE_PORT",
                     "CLAUDE_CODE_ENTRY_POINT", "CURSOR_TRACE_DIR", "CURSOR_CHANNEL",
                     "VSCODE_GIT_IPC_HANDLE", "VSCODE_PID"):
            monkeypatch.delenv(var, raising=False)

        # Set requested env vars
        if env:
            for k, v in env.items():
                monkeypatch.setenv(k, v)

        # Mock MCP client capture
        with patch.object(mcp_mod, '_capture_mcp_client_name', return_value=mcp_client):
            # Mock transcript detection
            with patch.object(mcp_mod, '_detect_active_tool_by_transcript', return_value=transcript_tool):
                try:
                    return _detect_tool(0)
                finally:
                    mcp_mod._cli_tool_override = old_override

    def test_priority_1_cli_override(self, monkeypatch):
        """--tool flag always wins."""
        result = self._detect(monkeypatch, cli_override="codex",
                              mcp_client="vscode-copilot-chat",
                              env={"CLAUDE_CODE_SSE_PORT": "1234"})
        assert result == "codex"

    def test_priority_2_context_lens_client_env(self, monkeypatch):
        """CONTEXT_LENS_CLIENT env var beats MCP client and strong env vars."""
        result = self._detect(monkeypatch,
                              env={"CONTEXT_LENS_CLIENT": "claude",
                                   "CODEX_THREAD_ID": "t-123"},
                              mcp_client="vscode-copilot-chat")
        assert result == "claude"

    def test_priority_2_lens_target_env(self, monkeypatch):
        """LENS_TARGET env var is priority 2."""
        result = self._detect(monkeypatch,
                              env={"LENS_TARGET": "codex"})
        assert result == "codex"

    def test_priority_3_codex_thread_id(self, monkeypatch):
        """CODEX_THREAD_ID strong env var beats MCP client."""
        result = self._detect(monkeypatch,
                              env={"CODEX_THREAD_ID": "t-abc"},
                              mcp_client="vscode-copilot-chat")
        assert result == "codex"

    def test_priority_3_codex_sandbox_id(self, monkeypatch):
        result = self._detect(monkeypatch,
                              env={"CODEX_SANDBOX_ID": "s-123"})
        assert result == "codex"

    def test_priority_3_claude_code_sse_port(self, monkeypatch):
        """CLAUDE_CODE_SSE_PORT beats Copilot MCP client."""
        result = self._detect(monkeypatch,
                              env={"CLAUDE_CODE_SSE_PORT": "8080"},
                              mcp_client="vscode-copilot-chat")
        assert result == "claude"

    def test_priority_3_claude_entry_point(self, monkeypatch):
        result = self._detect(monkeypatch,
                              env={"CLAUDE_CODE_ENTRY_POINT": "cli"})
        assert result == "claude"

    def test_priority_4_mcp_client_non_copilot(self, monkeypatch):
        """MCP client_info.name is trusted when it's NOT copilot."""
        result = self._detect(monkeypatch, mcp_client="claude-code")
        assert result == "claude"

    def test_priority_4_mcp_copilot_not_trusted(self, monkeypatch):
        """When MCP says 'copilot', it falls through to transcript heuristic."""
        result = self._detect(monkeypatch,
                              mcp_client="vscode-copilot-chat",
                              transcript_tool="codex")
        assert result == "codex"

    def test_priority_5_transcript_heuristic(self, monkeypatch):
        """Transcript recency heuristic is used as fallback."""
        result = self._detect(monkeypatch, transcript_tool="claude")
        assert result == "claude"

    def test_priority_6_fallback_mcp_copilot(self, monkeypatch):
        """If no env, no strong signal, no transcript → MCP copilot is used."""
        result = self._detect(monkeypatch,
                              mcp_client="vscode-copilot-chat",
                              transcript_tool=None)
        assert result == "copilot"

    def test_priority_6_fallback_vscode_pid(self, monkeypatch):
        """VSCODE_PID makes detect_client_tool return copilot."""
        result = self._detect(monkeypatch,
                              env={"VSCODE_PID": "12345"},
                              transcript_tool=None)
        assert result == "copilot"

    def test_full_fallback_unknown(self, monkeypatch):
        """No env, no MCP, no transcript → unknown."""
        result = self._detect(monkeypatch,
                              mcp_client=None,
                              transcript_tool=None)
        assert result == "unknown"


# ── _detect_active_tool_by_transcript tests ───────────────────────────────────


class TestDetectActiveToolByTranscript:
    """Verify filesystem-based transcript recency detection."""

    @staticmethod
    def _isolate_home(monkeypatch, tmp_path):
        """Point Path.home() to tmp_path and neutralise APPDATA so the
        real filesystem is never scanned."""
        monkeypatch.setattr(Path, 'home', lambda: tmp_path)
        # Prevent the Copilot scan from finding real workspaceStorage via APPDATA.
        monkeypatch.delenv('APPDATA', raising=False)
        monkeypatch.delenv('XDG_CONFIG_HOME', raising=False)

    def test_returns_none_when_no_transcripts(self, tmp_path, monkeypatch):
        """Empty home directory → None."""
        import src.ctx.mcp as mcp_mod

        self._isolate_home(monkeypatch, tmp_path)
        mcp_mod._transcript_tool_cache = (0.0, None)

        result = mcp_mod._detect_active_tool_by_transcript()
        assert result is None

    def test_detects_recent_codex_rollout(self, tmp_path, monkeypatch):
        """A recent rollout file in today's date dir → codex."""
        import src.ctx.mcp as mcp_mod

        now = time.time()
        today = time.localtime(now)
        day_dir = tmp_path / ".codex" / "sessions" / f"{today.tm_year:04d}" / f"{today.tm_mon:02d}" / f"{today.tm_mday:02d}"
        day_dir.mkdir(parents=True)

        rollout = day_dir / "rollout-abc123.jsonl"
        rollout.write_text('{"type":"session_meta"}')
        os.utime(rollout, (now, now))

        self._isolate_home(monkeypatch, tmp_path)
        mcp_mod._transcript_tool_cache = (0.0, None)

        result = mcp_mod._detect_active_tool_by_transcript()
        assert result == "codex"

    def test_detects_recent_claude_transcript(self, tmp_path, monkeypatch):
        """A recent JSONL in ~/.claude/projects/proj/ → claude."""
        import src.ctx.mcp as mcp_mod

        now = time.time()
        proj_dir = tmp_path / ".claude" / "projects" / "my-project"
        proj_dir.mkdir(parents=True)

        transcript = proj_dir / "session-abc.jsonl"
        transcript.write_text('{"type":"user"}')
        os.utime(transcript, (now, now))

        self._isolate_home(monkeypatch, tmp_path)
        mcp_mod._transcript_tool_cache = (0.0, None)

        result = mcp_mod._detect_active_tool_by_transcript()
        assert result == "claude"

    def test_most_recent_wins(self, tmp_path, monkeypatch):
        """When both codex and claude have recent transcripts, newest wins."""
        import src.ctx.mcp as mcp_mod

        now = time.time()
        today = time.localtime(now)

        # Codex — 60s old
        codex_dir = tmp_path / ".codex" / "sessions" / f"{today.tm_year:04d}" / f"{today.tm_mon:02d}" / f"{today.tm_mday:02d}"
        codex_dir.mkdir(parents=True)
        rollout = codex_dir / "rollout-old.jsonl"
        rollout.write_text('{"type":"session_meta"}')
        os.utime(rollout, (now - 60, now - 60))

        # Claude — 5s old (fresher)
        claude_dir = tmp_path / ".claude" / "projects" / "proj"
        claude_dir.mkdir(parents=True)
        transcript = claude_dir / "sess.jsonl"
        transcript.write_text('{"type":"user"}')
        os.utime(transcript, (now - 5, now - 5))

        self._isolate_home(monkeypatch, tmp_path)
        mcp_mod._transcript_tool_cache = (0.0, None)

        result = mcp_mod._detect_active_tool_by_transcript()
        assert result == "claude"

    def test_old_transcripts_ignored(self, tmp_path, monkeypatch):
        """Transcripts older than 90s are ignored → None."""
        import src.ctx.mcp as mcp_mod

        now = time.time()
        today = time.localtime(now)

        codex_dir = tmp_path / ".codex" / "sessions" / f"{today.tm_year:04d}" / f"{today.tm_mon:02d}" / f"{today.tm_mday:02d}"
        codex_dir.mkdir(parents=True)
        rollout = codex_dir / "rollout-old.jsonl"
        rollout.write_text('{"type":"session_meta"}')
        os.utime(rollout, (now - 200, now - 200))  # 200s ago — too old

        self._isolate_home(monkeypatch, tmp_path)
        mcp_mod._transcript_tool_cache = (0.0, None)

        result = mcp_mod._detect_active_tool_by_transcript()
        assert result is None

    def test_cache_is_used_within_ttl(self, tmp_path, monkeypatch):
        """Cached result returned within TTL even if filesystem changes."""
        import src.ctx.mcp as mcp_mod

        # Pre-populate cache
        mcp_mod._transcript_tool_cache = (time.time(), "codex")

        # Even though no filesystem setup, cache should return codex
        monkeypatch.setattr(Path, 'home', lambda: tmp_path)
        result = mcp_mod._detect_active_tool_by_transcript()
        assert result == "codex"

    def test_cache_expires_after_ttl(self, tmp_path, monkeypatch):
        """Stale cache is refreshed."""
        import src.ctx.mcp as mcp_mod

        # Expired cache
        mcp_mod._transcript_tool_cache = (time.time() - 10, "codex")

        self._isolate_home(monkeypatch, tmp_path)
        # No files → should get None (cache expired)
        result = mcp_mod._detect_active_tool_by_transcript()
        assert result is None


# ── normalize_target_name edge cases ──────────────────────────────────────────


class TestNormalizeTargetName:
    def test_known_aliases(self):
        from src.ctx.config import normalize_target_name
        assert normalize_target_name("openai-codex") == "codex"
        assert normalize_target_name("claude-code") == "claude"
        assert normalize_target_name("vscode-copilot-chat") == "copilot"
        assert normalize_target_name("github-copilot") == "copilot"
        assert normalize_target_name("cursor-ai") == "cursor"

    def test_unknown_passthrough(self):
        from src.ctx.config import normalize_target_name
        assert normalize_target_name("my-custom-tool") == "my-custom-tool"

    def test_none_and_empty(self):
        from src.ctx.config import normalize_target_name
        assert normalize_target_name(None) is None
        assert normalize_target_name("") is None
        # Whitespace-only normalizes to empty string (falsy but not None)
        result = normalize_target_name("   ")
        assert not result  # falsy


class TestSessionJsonToolUpdate:
    def test_update_session_tool_backfills_missing_field(self, tmp_path):
        import src.ctx.mcp as mcp_mod

        old_session_id = mcp_mod._session_id
        try:
            mcp_mod._session_id = 42
            root = tmp_path / "proj"
            ctx = root / ".ctx"
            ctx.mkdir(parents=True)
            session_path = ctx / "session.json"
            session_path.write_text(json.dumps({
                "id": 42,
                "name": "proj #42",
                "started_at": 123.0,
            }), encoding="utf-8")

            mcp_mod._update_session_tool(root, "codex")

            updated = json.loads(session_path.read_text(encoding="utf-8"))
            assert updated["id"] == 42
            assert updated["name"] == "proj #42"
            assert updated["tool"] == "codex"
        finally:
            mcp_mod._session_id = old_session_id
