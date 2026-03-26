"""Codex wrapper that generates context and opens ChatGPT."""
from __future__ import annotations

import subprocess
import sys
import webbrowser


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    query = " ".join(args).strip()
    if not query:
        query = input("Query: ").strip()
    if not query:
        print("Query vazia.")
        return 1

    from .context import main as context_main

    result = context_main([query, "--target", "codex"])
    if result == 0:
        try:
            webbrowser.open("https://chat.openai.com/")
        except Exception:
            pass
    return result


if __name__ == "__main__":
    raise SystemExit(main())
