"""
Universal MCP installer for Context Lens.

Writes ``lens-mcp`` into the MCP configuration of every supported IDE/CLI:
  • Claude Desktop   (Global)
  • Cursor           (Global + Project)
  • VS Code Copilot  (Workspace settings)
  • Zed              (Global)
  • Continue.dev     (Global + Project)
  • JetBrains        (Global)
  • Claude Code      (Project)

Usage (CLI):
  lens install                   # project-local (auto-detect IDEs)
  lens install --global          # global for ALL supported IDEs
  lens install --ide cursor      # specific IDE, project-local
  lens install --global --ide claude  # specific IDE, global
"""
from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Callable

# ─────────────────────────────────────────────────────────── constants

MCP_SERVER_NAME = "context-lens"
MCP_ENTRY: dict = {"command": "lens-mcp", "args": []}

def _mcp_entry(tool: str | None = None) -> dict:
    """Return MCP server entry, optionally with a --tool flag for explicit detection."""
    if tool:
        return {"command": "lens-mcp", "args": ["--tool", tool]}
    return dict(MCP_ENTRY)

SYSTEM = platform.system()  # "Windows", "Darwin", "Linux"


# ─────────────────────────────────────────────────────────── helpers

def _home() -> Path:
    return Path.home()


def _load_json(path: Path, default=None):
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _lens_mcp_installed(path: Path, key_path: tuple[str, ...]) -> bool:
    """Return True if context-lens is already in *path* under nested *key_path*."""
    data = _load_json(path)
    obj = data
    for k in key_path:
        if not isinstance(obj, dict):
            return False
        obj = obj.get(k, {})
    return isinstance(obj, dict) and MCP_SERVER_NAME in obj


def _install_mcp_server(
    path: Path,
    key_path: tuple[str, ...],
    dry_run: bool = False,
    tool: str | None = None,
) -> str:
    """Upsert the MCP server entry at *path*[*key_path*[0]][…][*key_path*[-1]].

    Returns one of: "installed", "updated", "already_installed", "dry_run".
    If the entry exists but the --tool flag differs, updates it.
    """
    expected = _mcp_entry(tool)
    if _lens_mcp_installed(path, key_path):
        # Check if args need updating (e.g. missing --tool flag)
        data = _load_json(path)
        obj = data
        for k in key_path:
            obj = obj.get(k, {})
        current = obj.get(MCP_SERVER_NAME, {})
        if current.get("args") == expected.get("args"):
            return "already_installed"
        # Args differ — update
        if dry_run:
            return "dry_run"
        data2 = _load_json(path, default={})
        obj2 = data2
        for k in key_path:
            obj2 = obj2.setdefault(k, {})
        obj2[MCP_SERVER_NAME] = expected
        _save_json(path, data2)
        return "updated"
    if dry_run:
        return "dry_run"

    data = _load_json(path, default={})
    obj = data
    for k in key_path[:-1]:
        obj = obj.setdefault(k, {})
    obj.setdefault(key_path[-1], {})[MCP_SERVER_NAME] = expected
    _save_json(path, data)
    return "installed"


# ── VS Code / Cursor extension (.vsix) installer ────────────────────────────

class InstallResult:
    """Collects per-action outcomes for a single IDE."""

    def __init__(self, ide: str) -> None:
        self.ide = ide
        self.actions: list[tuple[str, str]] = []  # (description, status)

    def add(self, description: str, status: str) -> None:
        self.actions.append((description, status))

    @property
    def ok(self) -> bool:
        return any(s in ("installed", "dry_run") for _, s in self.actions)

    def __repr__(self) -> str:
        return f"InstallResult({self.ide}, {self.actions})"


_VSIX_NAME = "context-lens.vsix"
_EXTENSION_ID = "TiagoSchr.context-lens"

_log = logging.getLogger(__name__)


def _bundled_vsix_path() -> Path | None:
    """Return path to the .vsix bundled inside the Python package (src/ctx/data/)."""
    candidate = Path(__file__).parent / "data" / _VSIX_NAME
    if candidate.exists():
        return candidate
    return None


def _is_extension_installed(cli: str) -> bool:
    """Check if our extension is already installed in VS Code or Cursor."""
    try:
        result = subprocess.run(
            [cli, "--list-extensions"],
            capture_output=True, text=True, timeout=15,
        )
        return _EXTENSION_ID in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _install_extension(cli: str, ide_label: str, dry_run: bool = False) -> InstallResult:
    """Install the Context Lens VS Code extension via `code --install-extension` or `cursor --install-extension`.

    Provides @lens chat participant and Ctrl+Shift+L keybinding.
    """
    result = InstallResult(f"{ide_label} extension")

    # Check if CLI is available
    cli_path = shutil.which(cli)
    if not cli_path:
        result.add(f"{cli} CLI not found (install {ide_label} and add to PATH)", "unsupported_os")
        return result

    # Check if already installed
    if _is_extension_installed(cli):
        result.add(f"{_EXTENSION_ID} extension", "already_installed")
        return result

    if dry_run:
        result.add(f"{_EXTENSION_ID} extension", "dry_run")
        return result

    # Find .vsix
    vsix = _bundled_vsix_path()
    if not vsix:
        result.add("Bundled .vsix not found (run 'cd vscode-context-lens && npx @vscode/vsce package')", "error")
        return result

    # Install
    try:
        proc = subprocess.run(
            [cli, "--install-extension", str(vsix), "--force"],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode == 0:
            result.add(f"{_EXTENSION_ID} extension (@lens + Ctrl+Shift+L)", "installed")
        else:
            err = proc.stderr.strip() or proc.stdout.strip()
            _log.warning("Extension install failed: %s", err)
            result.add(f"Extension install failed: {err[:80]}", "error")
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        result.add(f"Extension install error: {exc}", "error")

    return result


# ── Instruction files (tell the AI to always use Context Lens) ───────────────

_COPILOT_INSTRUCTIONS = """\
# Context Lens — Copilot Instructions

This project has Context Lens installed — a token-optimization MCP server.

## Mandatory Workflow

**BEFORE reading any file**, always call the `lens_context` MCP tool first:

```
lens_context(query="<describe your task>", task="auto")
```

This returns an optimized context block that is 75-95% smaller than reading
files directly. Use it as your primary information source.

## Rules

1. **Always call `lens_context` BEFORE using Read File, Grep, or Glob.**
2. Use the returned context as your main source of truth.
3. Only read individual files if they are NOT covered by the context block.
4. For symbol lookup by name, use `lens_search(query="<name>")`.
5. If the index seems stale, call `lens_reindex()`.

## Why This Matters

Reading files directly wastes 5-20x more tokens than necessary.
`lens_context` returns the same relevant code, pre-ranked and budget-trimmed.
Every query you run through Context Lens saves thousands of tokens.

## Available MCP Tools

- `lens_context(query, task, budget)` — ALWAYS call first
- `lens_search(query)` — find symbols by name
- `lens_status()` — check index health
- `lens_diff_context(query)` — context for git-changed files
- `lens_symbols(path)` — symbols in a specific file
- `lens_explain_symbol(name)` — deep dive on one symbol
- `lens_reindex()` — trigger re-index if stale
"""

_CODEX_INSTRUCTIONS = """\
# Context Lens — MANDATORY

This project uses Context Lens (MCP server: `context-lens`) for token optimization.

**CRITICAL: You MUST call lens_context BEFORE any file read operation.**

    lens_context(query="<describe your task>", task="auto")

## Rules — non-negotiable

1. **FIRST action on every task**: call `lens_context` with your task description.
2. Use the returned context as your PRIMARY information source.
3. **ONLY** read files directly if they are NOT covered by the context block.
4. For symbol lookup: `lens_search(query="<name>")`.
5. If index seems stale: `lens_reindex()`.

Calling lens_context saves 75-95% tokens per query. Reading files directly
wastes 5-20x more tokens. The project owner requires this workflow.
"""

_CURSOR_RULES = """\
# Context Lens — MANDATORY WORKFLOW

This project has Context Lens installed (MCP server: `context-lens`).

## CRITICAL: Call lens_context BEFORE reading ANY file

Every task MUST start with:
```
lens_context(query="<your task description>", task="auto")
```

This returns optimized context (75-95% smaller than raw files).

## Non-negotiable rules

1. **FIRST tool call** on every task = `lens_context`. No exceptions.
2. Use the returned context as your PRIMARY source of truth.
3. ONLY read files that are NOT covered by the context block.
4. For symbol search: `lens_search(query="<name>")`.
5. If index is stale: `lens_reindex()`.

## Why

Reading files directly wastes 5-20x more tokens than necessary.
lens_context returns the same relevant code, pre-ranked and trimmed.
The project owner requires this for every interaction.
"""

_CLAUDE_MD = """\
# Context Lens — MANDATORY

## You MUST call lens_context before ANY file operation

This project uses Context Lens (MCP server: `context-lens`).
Before using Read, Glob, Grep, or ListDirectory, you MUST call:

```
lens_context(query="<your task>", task="auto")
```

## Rules — every task, no exceptions

1. **FIRST action**: call `lens_context` with your task description
2. Use the returned context as your PRIMARY source
3. ONLY use Read/Glob/Grep for files NOT covered by the context
4. For symbol lookup: `lens_search(query="<name>")`
5. If index is stale: `lens_reindex()`

## Why this is mandatory

- Saves 75-95% tokens per query (measured, not estimated)
- Reading files directly wastes 5-20x more tokens
- The project owner enforces this — skipping lens_context violates project rules

A PreToolUse hook will remind you if you forget.
"""

_INSTRUCTION_FILES: dict[str, tuple[str, str]] = {
    # IDE key → (relative path, content)
    "vscode":      (".github/copilot-instructions.md", _COPILOT_INSTRUCTIONS),
    "codex":       (".codex/instructions.md",          _CODEX_INSTRUCTIONS),
    "codex-cli":   ("AGENTS.md",                       _CODEX_INSTRUCTIONS),
    "cursor":      (".cursorrules",                    _CURSOR_RULES),
    "claude":      ("CLAUDE.md",                       _CLAUDE_MD),
}


def install_instruction_files(root: Path, dry_run: bool = False) -> InstallResult:
    """Write IDE instruction files that tell AI tools to always use Context Lens."""
    result = InstallResult("Instruction files")
    for ide_key, (rel_path, content) in _INSTRUCTION_FILES.items():
        path = root / rel_path
        if path.exists():
            existing = ""
            try:
                existing = path.read_text(encoding="utf-8")
            except OSError:
                pass
            if existing.strip() == content.strip():
                result.add(str(path), "already_installed")
                continue
            if "lens_context" in existing:
                # File has lens_context but content differs — update
                if dry_run:
                    result.add(str(path), "dry_run")
                    continue
                path.write_text(content, encoding="utf-8")
                result.add(str(path), "updated")
                continue
        if dry_run:
            result.add(str(path), "dry_run")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        result.add(str(path), "installed")
    return result


# ─────────────────────────────────────────────────────────── IDE installers


# ── Claude Desktop ───────────────────────────────────────────────────────────

def _claude_desktop_config_path() -> Path | None:
    if SYSTEM == "Windows":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            return Path(appdata) / "Claude" / "claude_desktop_config.json"
    elif SYSTEM == "Darwin":
        return _home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    else:  # Linux
        config = os.environ.get("XDG_CONFIG_HOME", str(_home() / ".config"))
        return Path(config) / "Claude" / "claude_desktop_config.json"
    return None


def install_claude_desktop(dry_run: bool = False) -> InstallResult:
    result = InstallResult("Claude Desktop")
    path = _claude_desktop_config_path()
    if path is None:
        result.add("claude_desktop_config.json", "unsupported_os")
        return result
    status = _install_mcp_server(path, ("mcpServers",), dry_run, tool="claude")
    result.add(str(path), status)
    return result


# ── Cursor global ────────────────────────────────────────────────────────────

def install_cursor_global(dry_run: bool = False) -> InstallResult:
    result = InstallResult("Cursor (global)")
    path = _home() / ".cursor" / "mcp.json"
    status = _install_mcp_server(path, ("mcpServers",), dry_run, tool="cursor")
    result.add(str(path), status)
    return result


def install_cursor_project(root: Path, dry_run: bool = False) -> InstallResult:
    result = InstallResult("Cursor (project)")
    path = root / ".cursor" / "mcp.json"
    status = _install_mcp_server(path, ("mcpServers",), dry_run, tool="cursor")
    result.add(str(path), status)
    return result


def install_cursor_extension(dry_run: bool = False) -> InstallResult:
    """Install the Context Lens extension in Cursor (provides keybinding + status bar)."""
    return _install_extension("cursor", "Cursor", dry_run)


# ── VS Code Copilot ──────────────────────────────────────────────────────────

def _vscode_user_settings_path() -> Path:
    if SYSTEM == "Windows":
        appdata = os.environ.get("APPDATA", "")
        base = Path(appdata) / "Code" / "User" if appdata else _home() / ".vscode" / "User"
    elif SYSTEM == "Darwin":
        base = _home() / "Library" / "Application Support" / "Code" / "User"
    else:
        config = os.environ.get("XDG_CONFIG_HOME", str(_home() / ".config"))
        base = Path(config) / "Code" / "User"
    return base / "settings.json"


def install_vscode_workspace(root: Path, dry_run: bool = False) -> InstallResult:
    """Write MCP server to .vscode/mcp.json (workspace-level)."""
    result = InstallResult("VS Code (workspace)")
    path = root / ".vscode" / "mcp.json"
    status = _install_mcp_server(path, ("servers",), dry_run, tool="copilot")
    result.add(str(path), status)
    return result


def install_vscode_extension(dry_run: bool = False) -> InstallResult:
    """Install the Context Lens VS Code extension (provides @lens + Ctrl+Shift+L)."""
    return _install_extension("code", "VS Code", dry_run)


def install_vscode_global(dry_run: bool = False) -> InstallResult:
    """Merge MCP server into global VS Code user settings.json under ``mcp.servers``."""
    result = InstallResult("VS Code (global)")
    path = _vscode_user_settings_path()
    if _lens_mcp_installed(path, ("mcp", "servers")):
        result.add(str(path), "already_installed")
        return result
    if dry_run:
        result.add(str(path), "dry_run")
        return result
    data = _load_json(path, default={})
    mcp = data.setdefault("mcp", {})
    mcp.setdefault("servers", {})[MCP_SERVER_NAME] = _mcp_entry("copilot")
    _save_json(path, data)
    result.add(str(path), "installed")
    return result


# ── Zed ──────────────────────────────────────────────────────────────────────

def _zed_settings_path() -> Path:
    if SYSTEM == "Windows":
        appdata = os.environ.get("APPDATA", "")
        return Path(appdata) / "Zed" / "settings.json" if appdata else _home() / ".config" / "zed" / "settings.json"
    elif SYSTEM == "Darwin":
        return _home() / "Library" / "Application Support" / "Zed" / "settings.json"
    else:
        config = os.environ.get("XDG_CONFIG_HOME", str(_home() / ".config"))
        return Path(config) / "zed" / "settings.json"


def install_zed(dry_run: bool = False) -> InstallResult:
    result = InstallResult("Zed")
    path = _zed_settings_path()
    if _lens_mcp_installed(path, ("context_servers",)):
        result.add(str(path), "already_installed")
        return result
    if dry_run:
        result.add(str(path), "dry_run")
        return result
    data = _load_json(path, default={})
    servers = data.setdefault("context_servers", {})
    servers[MCP_SERVER_NAME] = {
        "command": {"path": "lens-mcp", "args": []},
        "settings": {},
    }
    _save_json(path, data)
    result.add(str(path), "installed")
    return result


# ── Continue.dev ─────────────────────────────────────────────────────────────

def _continue_config_path() -> Path:
    if SYSTEM == "Windows":
        home_data = os.environ.get("USERPROFILE", str(_home()))
        return Path(home_data) / ".continue" / "config.json"
    return _home() / ".continue" / "config.json"


def install_continue_global(dry_run: bool = False) -> InstallResult:
    result = InstallResult("Continue.dev (global)")
    path = _continue_config_path()
    # Continue uses a list under "mcpServers"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            servers = data.get("mcpServers", [])
            if any(s.get("name") == MCP_SERVER_NAME for s in servers):
                result.add(str(path), "already_installed")
                return result
        except (json.JSONDecodeError, OSError):
            data = {}
    else:
        data = {}
    if dry_run:
        result.add(str(path), "dry_run")
        return result
    servers = data.setdefault("mcpServers", [])
    servers.append({"name": MCP_SERVER_NAME, "command": "lens-mcp", "args": ["--tool", "continue"]})
    _save_json(path, data)
    result.add(str(path), "installed")
    return result


def install_continue_project(root: Path, dry_run: bool = False) -> InstallResult:
    result = InstallResult("Continue.dev (project)")
    path = root / ".continue" / "config.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            servers = data.get("mcpServers", [])
            if any(s.get("name") == MCP_SERVER_NAME for s in servers):
                result.add(str(path), "already_installed")
                return result
        except (json.JSONDecodeError, OSError):
            data = {}
    else:
        data = {}
    if dry_run:
        result.add(str(path), "dry_run")
        return result
    servers = data.setdefault("mcpServers", [])
    servers.append({"name": MCP_SERVER_NAME, "command": "lens-mcp", "args": ["--tool", "continue"]})
    _save_json(path, data)
    result.add(str(path), "installed")
    return result


# ── JetBrains ────────────────────────────────────────────────────────────────

def _jetbrains_mcp_path() -> Path:
    if SYSTEM == "Windows":
        appdata = os.environ.get("APPDATA", "")
        return Path(appdata) / "JetBrains" / "mcp.json" if appdata else _home() / ".config" / "JetBrains" / "mcp.json"
    elif SYSTEM == "Darwin":
        return _home() / "Library" / "Application Support" / "JetBrains" / "mcp.json"
    else:
        config = os.environ.get("XDG_CONFIG_HOME", str(_home() / ".config"))
        return Path(config) / "JetBrains" / "mcp.json"


def install_jetbrains(dry_run: bool = False) -> InstallResult:
    result = InstallResult("JetBrains")
    path = _jetbrains_mcp_path()
    status = _install_mcp_server(path, ("mcpServers",), dry_run, tool="jetbrains")
    result.add(str(path), status)
    return result


# ── Codex (project) ────────────────────────────────────────────────────────────

def install_codex_project(root: Path, dry_run: bool = False) -> InstallResult:
    result = InstallResult("Codex (project)")
    path = root / ".codex" / "mcp.json"
    status = _install_mcp_server(path, ("mcpServers",), dry_run, tool="codex")
    result.add(str(path), status)
    return result


# ── Claude Code (project) ─────────────────────────────────────────────────────

def install_claude_code_project(root: Path, dry_run: bool = False) -> InstallResult:
    result = InstallResult("Claude Code (project)")
    path = root / ".claude" / "mcp.json"
    status = _install_mcp_server(path, ("mcpServers",), dry_run, tool="claude")
    result.add(str(path), status)

    # Install PreToolUse hooks for enforcement
    hooks_result = install_claude_code_hooks(root, dry_run)
    for desc, st in hooks_result.actions:
        result.add(desc, st)

    return result


# ── Claude Code hooks (structural enforcement) ──────────────────────────────

_CLAUDE_HOOKS_PRE_READ = {
    "matcher": "Read|Glob|Grep|ListDirectory",
    "hooks": [
        {
            "type": "command",
            "command": "python -m ctx.scripts.hooks pre-read",
        }
    ],
}

_CLAUDE_HOOKS_PRE_BASH = {
    "matcher": "Bash",
    "hooks": [
        {
            "type": "command",
            "command": "python -m ctx.scripts.hooks pre-bash",
        }
    ],
}

_CLAUDE_HOOKS_POST_WRITE = {
    "matcher": "Write|Edit",
    "hooks": [
        {
            "type": "command",
            "command": "python -m ctx.scripts.hooks post-write",
        }
    ],
}


def install_claude_code_hooks(root: Path, dry_run: bool = False) -> InstallResult:
    """Install PreToolUse/PostToolUse hooks in .claude/settings.local.json.

    Adds:
    - PreToolUse on Read|Glob|Grep|ListDirectory → reminds model to call lens_context
    - PreToolUse on Bash → incremental reindex
    - PostToolUse on Write|Edit → incremental reindex
    """
    result = InstallResult("Claude Code hooks")
    path = root / ".claude" / "settings.local.json"
    data = _load_json(path, default={})

    hooks = data.setdefault("hooks", {})
    pre_hooks: list = hooks.setdefault("PreToolUse", [])
    post_hooks: list = hooks.setdefault("PostToolUse", [])

    # Check if already installed by looking for our command
    def _has_hook(hook_list: list, command_fragment: str) -> bool:
        for entry in hook_list:
            for h in entry.get("hooks", []):
                if command_fragment in h.get("command", ""):
                    return True
        return False

    changed = False

    if not _has_hook(pre_hooks, "pre-read"):
        if not dry_run:
            pre_hooks.append(_CLAUDE_HOOKS_PRE_READ)
        result.add("PreToolUse hook (pre-read)", "dry_run" if dry_run else "installed")
        changed = True
    else:
        result.add("PreToolUse hook (pre-read)", "already_installed")

    if not _has_hook(pre_hooks, "pre-bash"):
        if not dry_run:
            pre_hooks.append(_CLAUDE_HOOKS_PRE_BASH)
        result.add("PreToolUse hook (pre-bash)", "dry_run" if dry_run else "installed")
        changed = True
    else:
        result.add("PreToolUse hook (pre-bash)", "already_installed")

    if not _has_hook(post_hooks, "post-write"):
        if not dry_run:
            post_hooks.append(_CLAUDE_HOOKS_POST_WRITE)
        result.add("PostToolUse hook (post-write)", "dry_run" if dry_run else "installed")
        changed = True
    else:
        result.add("PostToolUse hook (post-write)", "already_installed")

    # Add lens permissions if not present
    permissions = data.setdefault("permissions", {})
    allow_list: list = permissions.setdefault("allow", [])
    lens_perms = [
        "Bash(lens *)",
        "Bash(python -m ctx.scripts.hooks *)",
    ]
    for perm in lens_perms:
        if perm not in allow_list:
            if not dry_run:
                allow_list.append(perm)
            changed = True

    if changed and not dry_run:
        _save_json(path, data)

    return result


# ─────────────────────────────────────────────────────────── detection

_IDE_GLOBAL_DETECTORS: dict[str, Callable[[], bool]] = {
    "claude-desktop": lambda: bool(_claude_desktop_config_path() and _claude_desktop_config_path().parent.exists()),  # type: ignore[arg-type]
    "cursor":         lambda: (_home() / ".cursor").exists() or bool(shutil.which("cursor")),
    "vscode":         lambda: _vscode_user_settings_path().parent.exists() or bool(shutil.which("code")),
    "zed":            lambda: _zed_settings_path().parent.exists() or bool(shutil.which("zed")),
    "continue":       lambda: _continue_config_path().parent.exists(),
    "jetbrains":      lambda: _jetbrains_mcp_path().parent.exists(),
}

_IDE_PROJECT_DETECTORS: dict[str, Callable[[Path], bool]] = {
    "cursor":       lambda r: (r / ".cursor").exists() or (r / ".cursorrules").exists(),
    "vscode":       lambda r: (r / ".vscode").exists(),
    "continue":     lambda r: (r / ".continue").exists(),
    "claude-code":  lambda r: (r / ".claude").exists() or bool(shutil.which("claude")),
    "codex":        lambda r: (r / ".codex").exists() or bool(shutil.which("codex")),
}

ALL_GLOBAL_IDES = list(_IDE_GLOBAL_DETECTORS)
ALL_PROJECT_IDES = list(_IDE_PROJECT_DETECTORS)


def detect_global_ides() -> list[str]:
    """Return list of globally-detected IDEs."""
    return [ide for ide, fn in _IDE_GLOBAL_DETECTORS.items() if fn()]


def detect_project_ides(root: Path) -> list[str]:
    """Return list of project-local IDEs detected at *root*."""
    return [ide for ide, fn in _IDE_PROJECT_DETECTORS.items() if fn(root)]


# ─────────────────────────────────────────────────────────── main dispatcher

def install(
    root: Path | None = None,
    *,
    global_: bool = False,
    ide: str = "all",
    dry_run: bool = False,
) -> list[InstallResult]:
    """
    Run the installer and return a list of InstallResult objects.

    Parameters
    ----------
    root:    project root (used for project-local installs)
    global_: if True, install in global IDE configs
    ide:     "all" or a specific IDE name
    dry_run: if True, detect what would happen without writing files
    """
    results: list[InstallResult] = []

    if root is None:
        root = Path.cwd()

    if global_:
        targets = ALL_GLOBAL_IDES if ide == "all" else [ide]
        for t in targets:
            if t == "claude-desktop":
                results.append(install_claude_desktop(dry_run))
            elif t == "cursor":
                results.append(install_cursor_global(dry_run))
            elif t == "vscode":
                results.append(install_vscode_global(dry_run))
            elif t == "zed":
                results.append(install_zed(dry_run))
            elif t == "continue":
                results.append(install_continue_global(dry_run))
            elif t == "jetbrains":
                results.append(install_jetbrains(dry_run))
    else:
        targets = ALL_PROJECT_IDES if ide == "all" else [ide]
        for t in targets:
            if t == "cursor":
                results.append(install_cursor_project(root, dry_run))
            elif t == "vscode":
                results.append(install_vscode_workspace(root, dry_run))
            elif t == "continue":
                results.append(install_continue_project(root, dry_run))
            elif t == "claude-code":
                results.append(install_claude_code_project(root, dry_run))
            elif t == "codex":
                results.append(install_codex_project(root, dry_run))

    # Always install instruction files for project-local installs
    if not global_:
        results.append(install_instruction_files(root, dry_run))

    # Auto-install VS Code/Cursor extension (provides @lens + Ctrl+Shift+L)
    ext_targets = set()
    if ide == "all":
        # Check both global and project targets
        if global_:
            if "vscode" in ALL_GLOBAL_IDES:
                ext_targets.add("vscode")
            if "cursor" in ALL_GLOBAL_IDES:
                ext_targets.add("cursor")
        else:
            if "vscode" in ALL_PROJECT_IDES:
                ext_targets.add("vscode")
            if "cursor" in ALL_PROJECT_IDES:
                ext_targets.add("cursor")
    else:
        if ide in ("vscode", "cursor"):
            ext_targets.add(ide)

    for ext_ide in ext_targets:
        if ext_ide == "vscode":
            results.append(install_vscode_extension(dry_run))
        elif ext_ide == "cursor":
            results.append(install_cursor_extension(dry_run))

    return results


def format_results(results: list[InstallResult]) -> str:
    """Format installer results for terminal output."""
    lines: list[str] = []
    for r in results:
        for desc, status in r.actions:
            icon = {
                "installed": "  [+]",
                "updated": "  [~]",
                "already_installed": "  [=]",
                "dry_run": "  [?]",
                "unsupported_os": "  [-]",
                "error": "  [!]",
            }.get(status, "  [?]")
            # shorten long home paths
            try:
                short = Path(desc).relative_to(Path.home())
                display = "~/" + short.as_posix()
            except ValueError:
                display = desc
            lines.append(f"{icon}  {r.ide:<28} {display}")
    return "\n".join(lines)
