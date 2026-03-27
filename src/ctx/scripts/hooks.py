"""Silent helpers used by Claude hooks and slash commands."""
from __future__ import annotations

import argparse
import io
import os
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


def _run_lens(args: list[str], cwd: Path) -> None:
    from .. import cli as cli_module

    old_cwd = Path.cwd()
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    try:
        os.chdir(cwd)
        with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
            cli_module.main.main(args=args, prog_name="lens", standalone_mode=False)
    finally:
        os.chdir(old_cwd)


def ensure_index() -> int:
    root = Path.cwd()
    db_file = root / ".ctx" / "index.db"
    try:
        if not db_file.exists():
            _run_lens(["index", "--quiet"], cwd=root)
    except Exception:
        pass
    return 0


def pre_bash() -> int:
    root = Path.cwd()
    db_file = root / ".ctx" / "index.db"
    try:
        if db_file.exists():
            _run_lens(["index", "--incremental", "--quiet"], cwd=root)
    except Exception:
        pass
    return 0


def post_write() -> int:
    root = Path.cwd()
    try:
        _run_lens(["index", "--incremental", "--quiet"], cwd=root)
    except Exception:
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Silent helpers for Context Lens automation.")
    parser.add_argument("action", choices=["ensure-index", "pre-bash", "post-write"])
    args = parser.parse_args(argv)

    if args.action == "ensure-index":
        return ensure_index()
    if args.action == "pre-bash":
        return pre_bash()
    return post_write()


if __name__ == "__main__":
    raise SystemExit(main())
