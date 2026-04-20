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


def pre_read() -> int:
    """Remind the AI model to call lens_context before reading files.

    Checks if lens_context was called recently (last 2 minutes).
    If not, prints a reminder that the model will see in its context.
    Always returns 0 (allow the read to proceed).
    """
    import json
    import time

    root = Path.cwd()
    log_file = root / ".ctx" / "log.jsonl"

    if not log_file.exists():
        return 0

    threshold = time.time() - 120  # 2 minutes

    try:
        with open(log_file, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 4096))
            tail = f.read().decode("utf-8", errors="replace")

        for line in reversed(tail.strip().split("\n")):
            try:
                entry = json.loads(line)
                if entry.get("event") == "retrieval" and entry.get("ts", 0) > threshold:
                    return 0  # Recent lens_context call found — stay silent
            except (json.JSONDecodeError, KeyError):
                continue
    except OSError:
        return 0

    # No recent lens_context call — print reminder for the model
    import sys
    print(
        "⚠️ Context Lens: You MUST call the lens_context MCP tool BEFORE "
        "reading files. It returns optimized context that saves 75-95% tokens. "
        "Call lens_context(query=\"<your task>\") first, then only Read files "
        "not covered by the context block.",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Silent helpers for Context Lens automation.")
    parser.add_argument("action", choices=["ensure-index", "pre-bash", "post-write", "pre-read"])
    args = parser.parse_args(argv)

    if args.action == "ensure-index":
        return ensure_index()
    if args.action == "pre-bash":
        return pre_bash()
    if args.action == "pre-read":
        return pre_read()
    return post_write()


if __name__ == "__main__":
    raise SystemExit(main())
