"""Project-level configuration and .ctx directory management."""
from __future__ import annotations
import json
from pathlib import Path

CTX_DIR = ".ctx"
DB_FILE = "index.db"
LOG_FILE = "log.jsonl"
CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "token_budget": 8000,
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
    if path.exists():
        with open(path) as f:
            user_cfg = json.load(f)
        return {**DEFAULT_CONFIG, **user_cfg}
    return dict(DEFAULT_CONFIG)


def save_config(root: Path, cfg: dict) -> None:
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
