"""Project-level configuration and .ctx directory management."""
from __future__ import annotations
import json
import os
from pathlib import Path

CTX_DIR = ".ctx"
DB_FILE = "index.db"
LOG_FILE = "log.jsonl"
CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "token_budget": 8000,
    "target_budgets": {
        "claude": 8000,
        "copilot": 4000,
        "codex": 6000,
    },
    "budget_buffer": 0.12,          # reserve 12% as buffer
    "default_task": "explain",
    "index_extensions": [
        ".py", ".js", ".ts", ".jsx", ".tsx",
        ".go", ".rs", ".java", ".c", ".cpp", ".h",
        ".rb", ".php", ".cs", ".swift", ".kt",
    ],
    "ignore_dirs": [
        ".git", ".ctx", "__pycache__", "node_modules",
        ".venv", "venv", "env", "dist", "build",
        ".tox", ".mypy_cache", ".pytest_cache",
    ],
    "max_file_size_kb": 512,
    "fts_min_length": 2,
}


def normalize_target_name(target: str | None) -> str | None:
    """Normalize tool targets used by scripts and env vars."""
    if not target:
        return None
    normalized = target.strip().lower()
    aliases = {
        "chatgpt": "codex",
        "openai": "codex",
        "openai-codex": "codex",
        "claude-code": "claude",
        "github-copilot": "copilot",
    }
    return aliases.get(normalized, normalized)


def merge_config(user_cfg: dict | None = None) -> dict:
    """Merge user config with defaults, including nested target budgets."""
    merged = dict(DEFAULT_CONFIG)
    merged["target_budgets"] = dict(DEFAULT_CONFIG["target_budgets"])
    if not user_cfg:
        return merged

    for key, value in user_cfg.items():
        if key == "target_budgets" and isinstance(value, dict):
            merged["target_budgets"] = {
                **DEFAULT_CONFIG["target_budgets"],
                **value,
            }
        else:
            merged[key] = value
    return merged


def find_project_root(start: Path | None = None) -> Path | None:
    """Walk up from start until we find a .ctx dir or a known project marker."""
    here = Path(start or Path.cwd()).resolve()
    markers = {".ctx", ".git", "pyproject.toml", "package.json", "Cargo.toml", "go.mod"}
    for candidate in [here, *here.parents]:
        for m in markers:
            if (candidate / m).exists():
                return candidate
    return here  # fallback: current directory


def ctx_dir(root: Path) -> Path:
    return root / CTX_DIR


def db_path(root: Path) -> Path:
    return ctx_dir(root) / DB_FILE


def log_path(root: Path) -> Path:
    return ctx_dir(root) / LOG_FILE


def config_path(root: Path) -> Path:
    return ctx_dir(root) / CONFIG_FILE


def load_config(root: Path) -> dict:
    path = config_path(root)
    user_cfg = None
    if path.exists():
        with open(path) as f:
            user_cfg = json.load(f)

    cfg = merge_config(user_cfg)
    env_target = normalize_target_name(os.environ.get("LENS_TARGET"))
    if env_target and env_target in cfg.get("target_budgets", {}):
        cfg["token_budget"] = cfg["target_budgets"][env_target]
    return cfg


def save_config(root: Path, cfg: dict) -> None:
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = merge_config(cfg)
    with open(path, "w") as f:
        json.dump(merged, f, indent=2)
