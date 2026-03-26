"""Bootstrap Context Lens integrations for Claude, VS Code and Codex."""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from ..config import config_path, merge_config, save_config

MANAGED_SENTINEL = "# context-lens: managed\n"
LEGACY_COPILOT_TASK_LABEL = "Context Lens: Copilot — gerar contexto"
LEGACY_COPILOT_TASK_COMMAND = 'lens context "${input:lensQuery}" -o .ctx/ctx.md && code .ctx/ctx.md'

CLAUDE_ALLOW = [
    "Bash(lens *)",
    "Bash(python -m ctx.cli *)",
    "Bash(python -m ctx.scripts.hooks *)",
    "Bash(python scripts/lens-context.py *)",
]

CLAUDE_PRE_HOOK = {
    "type": "command",
    "command": "python -m ctx.scripts.hooks pre-bash",
}

CLAUDE_POST_HOOK = {
    "type": "command",
    "command": "python -m ctx.scripts.hooks post-write",
}

CLAUDE_CTX_COMMAND = """Antes de executar, rode este bootstrap silencioso:

`python -m ctx.scripts.hooks ensure-index`

Depois execute:

`lens context "$ARGUMENTS"`

Mostre o contexto gerado e use-o como base para responder.
Se $ARGUMENTS estiver vazio, pergunte o que o usuario quer fazer.
"""

CLAUDE_SETUP_COMMAND = """Execute estes comandos em sequencia:

1. `python scripts/lens-setup.py --target claude`
2. Se o script local ainda nao existir, use `lens-setup --target claude`
3. `lens status`

Apos o ultimo comando, mostre o resumo do status e confirme que o MCP esta
configurado em .claude/mcp.json.
"""

CLAUDE_RULE = """
## Context Lens - Uso automatico

Antes de ler multiplos arquivos para responder uma pergunta, SEMPRE usar:
- `lens_context` (MCP) ou `lens context "<query>"` (CLI) para montar contexto
- `lens_search` (MCP) ou `lens search "<query>"` para localizar simbolos

Nunca ler 3+ arquivos diretamente se o indice .ctx/index.db existir.
O contexto retornado ja inclui apenas o relevante, economizando 75-98% de tokens.

Se .ctx/index.db nao existir no projeto atual: executar /setup-lens antes de tudo.
"""

VSCODE_INPUT = {
    "id": "lensQuery",
    "type": "promptString",
    "description": "Descreva o que voce quer fazer (ex: fix bug in extract_symbols)",
}

VSCODE_AUTO_INDEX_TASK = {
    "label": "Context Lens: auto-index on open",
    "type": "shell",
    "command": "lens index --quiet && lens show map > .ctx/ctx.md 2>/dev/null || true",
    "windows": {
        "command": "lens index --quiet; New-Item -ItemType Directory -Force .ctx | Out-Null; lens show map | Out-File -Encoding utf8 .ctx/ctx.md; exit 0",
    },
    "presentation": {"reveal": "silent", "panel": "shared", "close": True},
    "runOptions": {"runOn": "folderOpen"},
    "problemMatcher": [],
}

VSCODE_CONTEXT_TASK = {
    "label": "Context Lens: gerar contexto para Copilot",
    "type": "shell",
    "command": 'python scripts/lens-context.py "${input:lensQuery}" --target copilot --no-clip',
    "presentation": {"reveal": "silent", "panel": "shared", "close": True},
    "problemMatcher": [],
}

VSCODE_STATUS_TASK = {
    "label": "Context Lens: status",
    "type": "shell",
    "command": "lens status",
    "presentation": {"reveal": "always", "panel": "shared"},
    "problemMatcher": [],
}

VSCODE_EXTENSIONS = ["GitHub.copilot", "GitHub.copilot-chat"]

VSCODE_KEYBINDINGS = [
    {
        "key": "ctrl+shift+l",
        "command": "workbench.action.tasks.runTask",
        "args": "Context Lens: gerar contexto para Copilot",
    },
    {
        "key": "ctrl+shift+k",
        "command": "workbench.action.tasks.runTask",
        "args": "Context Lens: status",
    },
]

LOCAL_CONTEXT_WRAPPER = (
    MANAGED_SENTINEL
    + """#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCAL_SRC = ROOT / "src"
if LOCAL_SRC.exists():
    sys.path.insert(0, str(LOCAL_SRC))

from ctx.scripts.context import main


if __name__ == "__main__":
    raise SystemExit(main())
"""
)

LOCAL_CODEX_WRAPPER = (
    MANAGED_SENTINEL
    + """#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCAL_SRC = ROOT / "src"
if LOCAL_SRC.exists():
    sys.path.insert(0, str(LOCAL_SRC))

from ctx.scripts.codex import main


if __name__ == "__main__":
    raise SystemExit(main())
"""
)

LOCAL_SETUP_WRAPPER = (
    MANAGED_SENTINEL
    + """#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCAL_SRC = ROOT / "src"
if LOCAL_SRC.exists():
    sys.path.insert(0, str(LOCAL_SRC))

from ctx.scripts.setup import main


if __name__ == "__main__":
    raise SystemExit(main())
"""
)


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _append_unique(items: list, value) -> None:
    if value not in items:
        items.append(value)


def _upsert_by_label(items: list[dict], new_item: dict) -> None:
    label = new_item.get("label")
    index = next((i for i, item in enumerate(items) if item.get("label") == label), None)
    if index is None:
        items.append(new_item)
        return
    items[index] = new_item
    items[:] = [item for i, item in enumerate(items) if i == index or item.get("label") != label]


def _upsert_keybinding(items: list[dict], new_item: dict) -> None:
    key = new_item.get("key")
    index = next((i for i, item in enumerate(items) if item.get("key") == key), None)
    if index is None:
        items.append(new_item)
        return
    items[index] = new_item
    items[:] = [item for i, item in enumerate(items) if i == index or item.get("key") != key]


def _find_hook_bucket(buckets: list[dict], matcher: str) -> dict:
    for bucket in buckets:
        if bucket.get("matcher") == matcher:
            bucket.setdefault("hooks", [])
            return bucket
    bucket = {"matcher": matcher, "hooks": []}
    buckets.append(bucket)
    return bucket


def _replace_hook(hooks: list[dict], hook: dict) -> None:
    filtered = [item for item in hooks if item.get("type") != hook.get("type")]
    filtered.append(hook)
    hooks[:] = filtered


def _command_available(command: str) -> bool:
    return shutil.which(command) is not None


def _repo_has_context_lens(root: Path) -> bool:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return False
    return 'name = "context-lens"' in pyproject.read_text(encoding="utf-8", errors="ignore")


def _install_package_if_needed(root: Path) -> str:
    if os.environ.get("CONTEXT_LENS_SKIP_INSTALL") == "1":
        return "skipped-install-check"

    show = subprocess.run(
        [sys.executable, "-m", "pip", "show", "context-lens"],
        capture_output=True,
        cwd=str(root),
    )
    if show.returncode == 0:
        return "already-installed"

    if _repo_has_context_lens(root):
        cmd = [sys.executable, "-m", "pip", "install", "--quiet", "-e", ".[parse,mcp]"]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "--quiet", "context-lens[parse,mcp]"]

    result = subprocess.run(cmd, cwd=str(root))
    if result.returncode != 0:
        raise RuntimeError("Failed to install context-lens.")
    return "installed"


def _run_lens(args: list[str], cwd: Path, capture: bool = False) -> str:
    from .. import cli as cli_module

    old_cwd = Path.cwd()
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    try:
        os.chdir(cwd)
        if capture:
            with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                cli_module.main.main(args=args, prog_name="lens", standalone_mode=False)
            return stdout_buffer.getvalue()
        with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
            cli_module.main.main(args=args, prog_name="lens", standalone_mode=False)
        return ""
    finally:
        os.chdir(old_cwd)


def _ensure_config(root: Path) -> None:
    cfg_path = config_path(root)
    raw_cfg = _load_json(cfg_path, {}) if cfg_path.exists() else {}
    merged = merge_config(raw_cfg)
    if not cfg_path.exists() or raw_cfg != merged:
        save_config(root, merged)


def _ensure_index(root: Path) -> None:
    if not (root / ".ctx").exists():
        _run_lens(["init"], cwd=root)
    _ensure_config(root)
    _run_lens(["index", "--quiet"], cwd=root)


def _ensure_managed_text_file(root: Path, path: Path, content: str, notes: dict[str, list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rel = path.relative_to(root).as_posix()
    if not path.exists():
        path.write_text(content, encoding="utf-8")
        notes["updated"].append(rel)
        return

    current = path.read_text(encoding="utf-8", errors="ignore")
    if current.startswith(MANAGED_SENTINEL):
        if current != content:
            path.write_text(content, encoding="utf-8")
            notes["updated"].append(rel)
        else:
            notes["unchanged"].append(rel)
        return

    notes["preserved"].append(rel)


def _remove_legacy_copilot_task(tasks: list[dict]) -> None:
    tasks[:] = [
        task for task in tasks
        if not (
            task.get("label") == LEGACY_COPILOT_TASK_LABEL
            and task.get("command") == LEGACY_COPILOT_TASK_COMMAND
        )
    ]


def _ensure_local_scripts(root: Path, notes: dict[str, list[str]]) -> None:
    scripts_dir = root / "scripts"
    _ensure_managed_text_file(root, scripts_dir / "lens-context.py", LOCAL_CONTEXT_WRAPPER, notes)
    _ensure_managed_text_file(root, scripts_dir / "lens-codex.py", LOCAL_CODEX_WRAPPER, notes)
    _ensure_managed_text_file(root, scripts_dir / "lens-setup.py", LOCAL_SETUP_WRAPPER, notes)


def ensure_claude(root: Path) -> dict[str, list[str]]:
    notes = {"updated": [], "preserved": [], "unchanged": []}
    _ensure_local_scripts(root, notes)

    claude_dir = root / ".claude"
    commands_dir = claude_dir / "commands"
    commands_dir.mkdir(parents=True, exist_ok=True)

    mcp_path = claude_dir / "mcp.json"
    mcp_data = _load_json(mcp_path, {"mcpServers": {}})
    mcp_data.setdefault("mcpServers", {})
    mcp_data["mcpServers"]["context-lens"] = {"command": "lens-mcp", "args": []}
    _write_json(mcp_path, mcp_data)
    notes["updated"].append(".claude/mcp.json")

    settings_path = claude_dir / "settings.local.json"
    settings = _load_json(settings_path, {})
    permissions = settings.setdefault("permissions", {})
    allow = permissions.setdefault("allow", [])
    for item in CLAUDE_ALLOW:
        _append_unique(allow, item)

    hooks = settings.setdefault("hooks", {})
    pre_tool = hooks.setdefault("PreToolUse", [])
    post_tool = hooks.setdefault("PostToolUse", [])
    pre_bucket = _find_hook_bucket(pre_tool, "Bash")
    post_bucket = _find_hook_bucket(post_tool, "Write|Edit")
    _replace_hook(pre_bucket["hooks"], CLAUDE_PRE_HOOK)
    _replace_hook(post_bucket["hooks"], CLAUDE_POST_HOOK)
    _write_json(settings_path, settings)
    notes["updated"].append(".claude/settings.local.json")

    ctx_command = commands_dir / "ctx.md"
    ctx_command.write_text(CLAUDE_CTX_COMMAND, encoding="utf-8")
    notes["updated"].append(".claude/commands/ctx.md")

    setup_command = commands_dir / "setup-lens.md"
    setup_command.write_text(CLAUDE_SETUP_COMMAND, encoding="utf-8")
    notes["updated"].append(".claude/commands/setup-lens.md")

    claude_md = root / "CLAUDE.md"
    existing = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""
    if "## Context Lens - Uso automatico" not in existing:
        if existing and not existing.endswith("\n"):
            existing += "\n"
        claude_md.write_text(existing.rstrip() + CLAUDE_RULE + "\n", encoding="utf-8")
        notes["updated"].append("CLAUDE.md")
    else:
        notes["unchanged"].append("CLAUDE.md")
    return notes


def ensure_vscode(root: Path) -> dict[str, list[str]]:
    notes = {"updated": [], "preserved": [], "unchanged": []}
    _ensure_local_scripts(root, notes)

    vscode_dir = root / ".vscode"
    vscode_dir.mkdir(parents=True, exist_ok=True)

    tasks_path = vscode_dir / "tasks.json"
    tasks_data = _load_json(tasks_path, {"version": "2.0.0", "inputs": [], "tasks": []})
    tasks_data["version"] = "2.0.0"
    tasks_data.setdefault("inputs", [])
    tasks_data.setdefault("tasks", [])
    _remove_legacy_copilot_task(tasks_data["tasks"])
    if not any(item.get("id") == VSCODE_INPUT["id"] for item in tasks_data["inputs"]):
        tasks_data["inputs"].append(VSCODE_INPUT)
    _upsert_by_label(tasks_data["tasks"], VSCODE_AUTO_INDEX_TASK)
    _upsert_by_label(tasks_data["tasks"], VSCODE_CONTEXT_TASK)
    _upsert_by_label(tasks_data["tasks"], VSCODE_STATUS_TASK)
    _write_json(tasks_path, tasks_data)
    notes["updated"].append(".vscode/tasks.json")

    extensions_path = vscode_dir / "extensions.json"
    extensions_data = _load_json(extensions_path, {"recommendations": []})
    recs = extensions_data.setdefault("recommendations", [])
    for extension in VSCODE_EXTENSIONS:
        _append_unique(recs, extension)
    _write_json(extensions_path, extensions_data)
    notes["updated"].append(".vscode/extensions.json")

    keybindings_path = vscode_dir / "keybindings.json"
    keybindings = _load_json(keybindings_path, [])
    if not isinstance(keybindings, list):
        keybindings = []
    for binding in VSCODE_KEYBINDINGS:
        _upsert_keybinding(keybindings, binding)
    _write_json(keybindings_path, keybindings)
    notes["updated"].append(".vscode/keybindings.json")
    return notes


def ensure_codex(root: Path) -> dict[str, list[str]]:
    notes = {"updated": [], "preserved": [], "unchanged": []}
    _ensure_local_scripts(root, notes)
    return notes


def detect_tools(root: Path) -> list[str]:
    detected: list[str] = ["codex"]
    if (root / ".claude").exists() or _command_available("claude"):
        detected.append("claude")
    if "VSCODE_PID" in os.environ or _command_available("code") or (root / ".vscode").exists():
        detected.append("vscode")
    return detected


def _resolve_targets(requested: str, detected: list[str]) -> list[str]:
    _ = detected
    if requested == "all":
        return ["claude", "vscode", "codex"]
    return [requested]


def _merge_notes(target_notes: dict[str, list[str]], new_notes: dict[str, list[str]]) -> None:
    for key, values in new_notes.items():
        for value in values:
            if value not in target_notes[key]:
                target_notes[key].append(value)


def build_summary(
    root: Path,
    targets: list[str],
    detected: list[str],
    install_state: str,
    notes: dict[str, list[str]],
) -> str:
    status_output = _run_lens(["status"], cwd=root, capture=True).strip()
    lines = [
        "Context Lens setup complete.",
        f"Package: {install_state}",
        f"Detected: {', '.join(detected)}",
        f"Configured: {', '.join(targets)}",
    ]
    if notes["preserved"]:
        lines.append(f"Preserved user files: {', '.join(notes['preserved'])}")
    if notes["updated"]:
        lines.append(f"Updated managed files: {', '.join(notes['updated'])}")
    lines.extend([
        "",
        status_output,
        "",
        "Usage:",
        "  Claude Code: /setup-lens once, then /ctx <query>",
        "  Copilot: Ctrl+Shift+L updates .ctx/ctx.md",
        "  Codex: python scripts/lens-codex.py \"your query\"",
    ])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Configura Context Lens para o projeto atual.")
    parser.add_argument(
        "--target",
        choices=["all", "claude", "vscode", "codex"],
        default="all",
        help="Ferramenta alvo para configurar (padrao: all).",
    )
    args = parser.parse_args(argv)

    root = Path.cwd()
    detected = detect_tools(root)
    install_state = _install_package_if_needed(root)
    _ensure_index(root)

    configured: list[str] = []
    notes = {"updated": [], "preserved": [], "unchanged": []}
    for target in _resolve_targets(args.target, detected):
        if target == "claude":
            target_notes = ensure_claude(root)
        elif target == "vscode":
            target_notes = ensure_vscode(root)
        else:
            target_notes = ensure_codex(root)
        _merge_notes(notes, target_notes)
        configured.append(target)

    print(build_summary(root, configured, detected, install_state, notes))
    if "claude" in configured:
        print("MCP configured in .claude/mcp.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
