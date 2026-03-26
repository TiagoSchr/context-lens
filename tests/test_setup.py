"""Tests for bootstrap helpers that merge Claude and VS Code config safely."""
from __future__ import annotations

import json

from src.ctx.scripts.setup import MANAGED_SENTINEL, ensure_claude, ensure_codex, ensure_vscode


def test_ensure_vscode_merges_existing_tasks_keybindings_and_removes_legacy_task(tmp_path):
    vscode_dir = tmp_path / ".vscode"
    vscode_dir.mkdir()
    (vscode_dir / "tasks.json").write_text(
        json.dumps(
            {
                "version": "2.0.0",
                "inputs": [],
                "tasks": [
                    {
                        "label": "Custom Task",
                        "type": "shell",
                        "command": "echo custom",
                    },
                    {
                        "label": "Context Lens: auto-index on open",
                        "type": "shell",
                        "command": "lens index",
                    },
                    {
                        "label": "Context Lens: Copilot — gerar contexto",
                        "type": "shell",
                        "command": 'lens context "${input:lensQuery}" -o .ctx/ctx.md && code .ctx/ctx.md',
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (vscode_dir / "keybindings.json").write_text(
        json.dumps(
            [
                {
                    "key": "ctrl+alt+c",
                    "command": "custom.command",
                }
            ]
        ),
        encoding="utf-8",
    )

    notes = ensure_vscode(tmp_path)

    tasks = json.loads((vscode_dir / "tasks.json").read_text(encoding="utf-8"))
    labels = {task["label"] for task in tasks["tasks"]}
    assert "Custom Task" in labels
    assert "Context Lens: auto-index on open" in labels
    assert "Context Lens: gerar contexto para Copilot" in labels
    assert "Context Lens: Copilot — gerar contexto" not in labels

    auto_task = next(task for task in tasks["tasks"] if task["label"] == "Context Lens: auto-index on open")
    assert "--quiet" in auto_task["command"]
    assert any(item["id"] == "lensQuery" for item in tasks["inputs"])

    keybindings = json.loads((vscode_dir / "keybindings.json").read_text(encoding="utf-8"))
    keys = {item["key"] for item in keybindings}
    assert "ctrl+alt+c" in keys
    assert "ctrl+shift+l" in keys
    assert "ctrl+shift+k" in keys

    assert "scripts/lens-context.py" in notes["updated"]
    assert (tmp_path / "scripts" / "lens-codex.py").read_text(encoding="utf-8").startswith(MANAGED_SENTINEL)


def test_ensure_claude_merges_existing_mcp_and_replaces_hooks(tmp_path):
    claude_dir = tmp_path / ".claude"
    commands_dir = claude_dir / "commands"
    commands_dir.mkdir(parents=True)
    (claude_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "existing": {
                        "command": "something",
                        "args": [],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "CLAUDE.md").write_text("# Base\n", encoding="utf-8")

    ensure_claude(tmp_path)
    ensure_claude(tmp_path)

    mcp = json.loads((claude_dir / "mcp.json").read_text(encoding="utf-8"))
    assert "existing" in mcp["mcpServers"]
    assert "context-lens" in mcp["mcpServers"]

    settings = json.loads((claude_dir / "settings.local.json").read_text(encoding="utf-8"))
    assert "Bash(python -m ctx.scripts.hooks *)" in settings["permissions"]["allow"]
    assert settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "python -m ctx.scripts.hooks pre-bash"
    assert settings["hooks"]["PostToolUse"][0]["hooks"][0]["command"] == "python -m ctx.scripts.hooks post-write"

    claude_md = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert claude_md.count("## Context Lens - Uso automatico") == 1
    assert "python scripts/lens-setup.py --target claude" in (commands_dir / "setup-lens.md").read_text(encoding="utf-8")
    assert "python -m ctx.scripts.hooks ensure-index" in (commands_dir / "ctx.md").read_text(encoding="utf-8")


def test_ensure_codex_preserves_non_managed_wrapper(tmp_path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    custom_wrapper = scripts_dir / "lens-context.py"
    custom_wrapper.write_text("# custom wrapper\nprint('keep me')\n", encoding="utf-8")

    notes = ensure_codex(tmp_path)

    assert custom_wrapper.read_text(encoding="utf-8") == "# custom wrapper\nprint('keep me')\n"
    assert "scripts/lens-context.py" in notes["preserved"]
    assert (scripts_dir / "lens-codex.py").read_text(encoding="utf-8").startswith(MANAGED_SENTINEL)


def test_ensure_codex_updates_managed_wrapper(tmp_path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    managed_wrapper = scripts_dir / "lens-setup.py"
    managed_wrapper.write_text(MANAGED_SENTINEL + "print('old')\n", encoding="utf-8")

    notes = ensure_codex(tmp_path)

    updated_text = managed_wrapper.read_text(encoding="utf-8")
    assert "ctx.scripts.setup" in updated_text
    assert "scripts/lens-setup.py" in notes["updated"]
