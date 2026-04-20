"""Tests for installer instruction files and MCP instructions."""
from __future__ import annotations

import json
from pathlib import Path

from src.ctx.installer import (
    install_instruction_files,
    install_vscode_workspace,
    install,
    _COPILOT_INSTRUCTIONS,
    _CODEX_INSTRUCTIONS,
    _CURSOR_RULES,
    _INSTRUCTION_FILES,
)


def test_install_instruction_files_creates_all(tmp_path: Path):
    """All instruction files are created in a fresh project."""
    result = install_instruction_files(tmp_path)
    assert result.ok

    for ide_key, (rel_path, content) in _INSTRUCTION_FILES.items():
        path = tmp_path / rel_path
        assert path.exists(), f"{rel_path} not created"
        text = path.read_text(encoding="utf-8")
        assert "lens_context" in text, f"{rel_path} missing lens_context reference"


def test_install_instruction_files_idempotent(tmp_path: Path):
    """Second run detects files already installed."""
    install_instruction_files(tmp_path)
    result2 = install_instruction_files(tmp_path)
    for desc, status in result2.actions:
        assert status == "already_installed", f"{desc} should be already_installed, got {status}"


def test_install_instruction_files_preserves_existing(tmp_path: Path):
    """If a file exists without lens_context, it's not overwritten."""
    gh = tmp_path / ".github"
    gh.mkdir()
    original = "# My custom instructions\nDo something else.\n"
    (gh / "copilot-instructions.md").write_text(original, encoding="utf-8")

    result = install_instruction_files(tmp_path)
    text = (gh / "copilot-instructions.md").read_text(encoding="utf-8")
    assert text != original, "File without lens_context should be overwritten"
    assert "lens_context" in text


def test_install_instruction_files_updates_outdated(tmp_path: Path):
    """If a file has lens_context but differs from template, it gets updated."""
    gh = tmp_path / ".github"
    gh.mkdir()
    old_content = "# Old instructions\nUse lens_context always.\n"
    (gh / "copilot-instructions.md").write_text(old_content, encoding="utf-8")

    result = install_instruction_files(tmp_path)
    text = (gh / "copilot-instructions.md").read_text(encoding="utf-8")
    assert text.strip() == _COPILOT_INSTRUCTIONS.strip(), "Outdated file should be updated"
    statuses = {desc: st for desc, st in result.actions}
    copilot_path = str(tmp_path / ".github" / "copilot-instructions.md")
    assert statuses[copilot_path] == "updated"


def test_install_instruction_files_dry_run(tmp_path: Path):
    """Dry run does not create files."""
    result = install_instruction_files(tmp_path, dry_run=True)
    for desc, status in result.actions:
        assert status == "dry_run"
    for _, (rel_path, _) in _INSTRUCTION_FILES.items():
        assert not (tmp_path / rel_path).exists()


def test_install_includes_instruction_files(tmp_path: Path):
    """The main install() function also creates instruction files."""
    results = install(root=tmp_path, ide="vscode")
    # Should have at least VS Code MCP + instruction files
    all_actions = [(d, s) for r in results for d, s in r.actions]
    instruction_paths = [d for d, s in all_actions if "copilot-instructions" in d or ".cursorrules" in d or ".codex" in d]
    assert len(instruction_paths) > 0, "install() should create instruction files"


def test_copilot_instructions_content():
    """Copilot instructions contain the key directives."""
    assert "lens_context" in _COPILOT_INSTRUCTIONS
    assert "BEFORE" in _COPILOT_INSTRUCTIONS
    assert "lens_search" in _COPILOT_INSTRUCTIONS


def test_codex_instructions_content():
    """Codex instructions contain the key directives."""
    assert "lens_context" in _CODEX_INSTRUCTIONS
    assert "BEFORE" in _CODEX_INSTRUCTIONS


def test_cursor_rules_content():
    """Cursor rules contain the key directives."""
    assert "lens_context" in _CURSOR_RULES
    assert "MANDATORY" in _CURSOR_RULES


def test_mcp_server_has_instructions():
    """The MCP Server is created with instructions parameter."""
    from src.ctx.mcp import app, _MCP_INSTRUCTIONS
    assert app.instructions is not None
    assert "lens_context" in app.instructions
    assert "MANDATORY" in app.instructions
    assert app.instructions == _MCP_INSTRUCTIONS
