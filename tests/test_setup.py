"""Tests for bootstrap helpers that merge Claude and VS Code config safely."""
from __future__ import annotations

import json

from src.ctx.scripts.setup import (
    MANAGED_SENTINEL,
    VSCODE_AUTO_INDEX_TASK,
    VSCODE_KEYBINDINGS,
    ensure_claude,
    ensure_codex,
    ensure_vscode,
    remove_claude,
    remove_codex,
    remove_vscode,
    main as setup_main,
)


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


# ─────────────────────────────────────── MCP e tasks: garantia de auto-uso

def test_mcp_json_has_lens_mcp_command_after_ensure_claude(tmp_path):
    """Garante que o Claude Code encontrará o MCP server correto."""
    (tmp_path / ".claude").mkdir()
    ensure_claude(tmp_path)

    mcp = json.loads((tmp_path / ".claude" / "mcp.json").read_text(encoding="utf-8"))
    server = mcp["mcpServers"]["context-lens"]
    assert server["command"] == "lens-mcp"
    assert isinstance(server["args"], list)


def test_vscode_task_has_run_on_folder_open_after_ensure_vscode(tmp_path):
    """Garante que o VS Code vai indexar automaticamente ao abrir o projeto."""
    ensure_vscode(tmp_path)

    tasks_data = json.loads((tmp_path / ".vscode" / "tasks.json").read_text(encoding="utf-8"))
    auto_task = next(
        (t for t in tasks_data["tasks"] if t["label"] == VSCODE_AUTO_INDEX_TASK["label"]),
        None,
    )
    assert auto_task is not None
    assert auto_task.get("runOptions", {}).get("runOn") == "folderOpen"


def test_vscode_task_presentation_is_silent(tmp_path):
    """Auto-index não deve abrir terminal na cara do usuário."""
    ensure_vscode(tmp_path)

    tasks_data = json.loads((tmp_path / ".vscode" / "tasks.json").read_text(encoding="utf-8"))
    auto_task = next(
        t for t in tasks_data["tasks"] if t["label"] == VSCODE_AUTO_INDEX_TASK["label"]
    )
    assert auto_task["presentation"]["reveal"] == "silent"


def test_claude_md_contains_lens_rule_after_ensure_claude(tmp_path):
    """CLAUDE.md deve instruir o Claude a usar lens_context automaticamente."""
    ensure_claude(tmp_path)
    claude_md = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "lens_context" in claude_md
    assert "lens_search" in claude_md


# ─────────────────────────────────────── remove_claude

def test_remove_claude_removes_mcp_entry_but_keeps_others(tmp_path):
    (tmp_path / ".claude").mkdir()
    mcp_path = tmp_path / ".claude" / "mcp.json"
    mcp_path.write_text(json.dumps({
        "mcpServers": {
            "other-tool": {"command": "other", "args": []},
            "context-lens": {"command": "lens-mcp", "args": []},
        }
    }), encoding="utf-8")

    remove_claude(tmp_path)

    mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    assert "context-lens" not in mcp["mcpServers"]
    assert "other-tool" in mcp["mcpServers"]  # não toca no que não é do lens


def test_remove_claude_cleans_lens_rule_from_claude_md(tmp_path):
    (tmp_path / ".claude").mkdir()
    ensure_claude(tmp_path)
    assert "## Context Lens - Uso automatico" in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")

    remove_claude(tmp_path)

    remaining = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "## Context Lens - Uso automatico" not in remaining


def test_remove_claude_removes_hooks_from_settings(tmp_path):
    (tmp_path / ".claude").mkdir()
    ensure_claude(tmp_path)
    settings_path = tmp_path / ".claude" / "settings.local.json"
    before = json.loads(settings_path.read_text(encoding="utf-8"))
    assert any(
        "ctx.scripts.hooks" in h.get("command", "")
        for bucket in before.get("hooks", {}).get("PreToolUse", [])
        for h in bucket.get("hooks", [])
    )

    remove_claude(tmp_path)

    after = json.loads(settings_path.read_text(encoding="utf-8"))
    for bucket in after.get("hooks", {}).get("PreToolUse", []):
        for h in bucket.get("hooks", []):
            assert "ctx.scripts.hooks" not in h.get("command", "")


def test_remove_claude_deletes_managed_slash_commands(tmp_path):
    (tmp_path / ".claude").mkdir()
    ensure_claude(tmp_path)
    assert (tmp_path / ".claude" / "commands" / "ctx.md").exists()
    assert (tmp_path / ".claude" / "commands" / "setup-lens.md").exists()

    remove_claude(tmp_path)

    assert not (tmp_path / ".claude" / "commands" / "ctx.md").exists()
    assert not (tmp_path / ".claude" / "commands" / "setup-lens.md").exists()


def test_remove_claude_is_idempotent(tmp_path):
    (tmp_path / ".claude").mkdir()
    ensure_claude(tmp_path)
    remove_claude(tmp_path)
    # segunda chamada não deve lançar exceção
    result = remove_claude(tmp_path)
    assert isinstance(result, dict)


# ─────────────────────────────────────── remove_vscode

def test_remove_vscode_removes_lens_tasks_but_keeps_custom(tmp_path):
    vscode_dir = tmp_path / ".vscode"
    vscode_dir.mkdir()
    tasks_path = vscode_dir / "tasks.json"
    tasks_path.write_text(json.dumps({
        "version": "2.0.0",
        "inputs": [],
        "tasks": [{"label": "My Build", "type": "shell", "command": "make"}],
    }), encoding="utf-8")

    ensure_vscode(tmp_path)
    remove_vscode(tmp_path)

    tasks_data = json.loads(tasks_path.read_text(encoding="utf-8"))
    labels = {t["label"] for t in tasks_data["tasks"]}
    assert "My Build" in labels
    assert VSCODE_AUTO_INDEX_TASK["label"] not in labels


def test_remove_vscode_removes_lens_keybindings_but_keeps_custom(tmp_path):
    vscode_dir = tmp_path / ".vscode"
    vscode_dir.mkdir()
    kb_path = vscode_dir / "keybindings.json"
    kb_path.write_text(json.dumps([
        {"key": "ctrl+alt+x", "command": "custom.cmd"}
    ]), encoding="utf-8")

    ensure_vscode(tmp_path)
    remove_vscode(tmp_path)

    keybindings = json.loads(kb_path.read_text(encoding="utf-8"))
    keys = {kb["key"] for kb in keybindings}
    assert "ctrl+alt+x" in keys
    for lens_kb in VSCODE_KEYBINDINGS:
        assert lens_kb["key"] not in keys


# ─────────────────────────────────────── remove_codex

def test_remove_codex_deletes_managed_scripts(tmp_path):
    ensure_codex(tmp_path)
    scripts_dir = tmp_path / "scripts"
    assert (scripts_dir / "lens-codex.py").exists()

    remove_codex(tmp_path)

    assert not (scripts_dir / "lens-codex.py").exists()
    assert not (scripts_dir / "lens-context.py").exists()
    assert not (scripts_dir / "lens-setup.py").exists()


def test_remove_codex_preserves_non_managed_scripts(tmp_path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    custom = scripts_dir / "lens-context.py"
    custom.write_text("# custom, not managed\nprint('keep')\n", encoding="utf-8")

    ensure_codex(tmp_path)
    remove_codex(tmp_path)

    assert custom.exists()
    assert custom.read_text(encoding="utf-8").startswith("# custom")


# ─────────────────────────────────────── setup_main --remove

def test_setup_main_remove_flag_removes_claude(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("src.ctx.scripts.setup._install_package_if_needed", lambda root: "already-installed")
    (tmp_path / ".claude").mkdir()
    ensure_claude(tmp_path)

    result = setup_main(["--target", "claude", "--remove"])

    assert result == 0
    mcp = json.loads((tmp_path / ".claude" / "mcp.json").read_text(encoding="utf-8"))
    assert "context-lens" not in mcp.get("mcpServers", {})


def test_setup_main_remove_all_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("src.ctx.scripts.setup._install_package_if_needed", lambda root: "already-installed")

    # Remove sem nada instalado: não deve falhar
    result = setup_main(["--remove"])
    assert result == 0
