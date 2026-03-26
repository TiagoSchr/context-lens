"""End-to-end automation tests for wrappers, setup and token economy."""
from __future__ import annotations

import io
import json
import os
import re
import stat
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from src.ctx import cli as cli_module
from src.ctx.scripts.context import main as context_main
from src.ctx.scripts.setup import ensure_codex, main as setup_main


def _build_project(root: Path) -> None:
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "service.py").write_text(
        '"""Service module."""\n\n'
        "class Service:\n"
        "    def handle(self, value: int) -> int:\n"
        "        return value + 1\n\n"
        "def compute_total(items: list[int]) -> int:\n"
        '    """Compute total for billing."""\n'
        "    total = 0\n"
        "    for item in items:\n"
        "        total += item\n"
        "    return total\n",
        encoding="utf-8",
    )


def _run_cli(root: Path, args: list[str], env: dict[str, str] | None = None) -> tuple[str, str]:
    old_cwd = Path.cwd()
    old_env = os.environ.copy()
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    try:
        os.chdir(root)
        if env:
            os.environ.update(env)
        with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
            cli_module.main.main(args=args, prog_name="lens", standalone_mode=False)
    finally:
        os.chdir(old_cwd)
        os.environ.clear()
        os.environ.update(old_env)
    return stdout_buffer.getvalue(), stderr_buffer.getvalue()


def _create_lens_shim(bin_dir: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        (bin_dir / "lens.cmd").write_text(
            f'@echo off\r\n"{sys.executable}" -m ctx.cli %*\r\n',
            encoding="utf-8",
        )
    else:
        shim = bin_dir / "lens"
        shim.write_text(
            "#!/usr/bin/env sh\n"
            f'"{sys.executable}" -m ctx.cli "$@"\n',
            encoding="utf-8",
        )
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)


def _expected_available_budget(raw_budget: int) -> int:
    return int(raw_budget * 0.88)


def test_context_wrapper_auto_inits_in_subprocess(tmp_path):
    _build_project(tmp_path)
    ensure_codex(tmp_path)

    bin_dir = tmp_path / "bin"
    _create_lens_shim(bin_dir)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")

    wrapper = tmp_path / "scripts" / "lens-context.py"
    result = subprocess.run(
        [sys.executable, str(wrapper), "explain billing flow", "--target", "codex", "--no-clip"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 0
    assert (tmp_path / ".ctx" / "index.db").exists()
    assert (tmp_path / ".ctx" / "last_context.md").exists()
    assert "[ctx] task=explain" in combined


def test_setup_main_is_idempotent_and_creates_expected_artifacts(tmp_path, monkeypatch):
    _build_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("src.ctx.scripts.setup._install_package_if_needed", lambda root: "already-installed")

    first = setup_main(["--target", "claude"])
    second = setup_main(["--target", "claude"])

    assert first == 0
    assert second == 0
    assert (tmp_path / ".claude" / "settings.local.json").exists()
    assert (tmp_path / ".claude" / "commands" / "setup-lens.md").exists()
    assert (tmp_path / ".ctx" / "index.db").exists()


def test_target_budgets_are_applied_automatically(tmp_path):
    _build_project(tmp_path)
    stdout, stderr = _run_cli(tmp_path, ["index", "--quiet"])
    assert stdout == ""

    target_expectations = {
        "claude": _expected_available_budget(8000),
        "copilot": _expected_available_budget(4000),
        "codex": _expected_available_budget(6000),
    }
    target_outputs = {}

    for target, expected_budget in target_expectations.items():
        output = io.StringIO()
        error = io.StringIO()
        env = {"LENS_TARGET": target}
        old_env = os.environ.copy()
        old_cwd = Path.cwd()
        try:
            os.chdir(tmp_path)
            os.environ.update(env)
            with redirect_stdout(output), redirect_stderr(error):
                context_main(["explain service flow", "--target", target, "--no-clip"])
        finally:
            os.chdir(old_cwd)
            os.environ.clear()
            os.environ.update(old_env)
        combined = output.getvalue() + error.getvalue()
        target_outputs[target] = combined
        assert f"/{expected_budget}" in combined

    assert (tmp_path / ".ctx" / "ctx.md").exists()
    assert (tmp_path / ".ctx" / "last_context.md").exists()
    assert "[lens] target=claude" in target_outputs["claude"]
    assert "[lens] target=copilot" in target_outputs["copilot"]
    assert "[lens] target=codex" in target_outputs["codex"]


def test_token_economy_grows_and_status_reports_it(tmp_path):
    _build_project(tmp_path)
    _run_cli(tmp_path, ["index", "--quiet"])

    queries = [
        "explain service flow",
        "fix bug in compute_total",
        "write tests for Service",
    ]
    for query in queries:
        out = io.StringIO()
        err = io.StringIO()
        old_cwd = Path.cwd()
        try:
            os.chdir(tmp_path)
            with redirect_stdout(out), redirect_stderr(err):
                context_main([query, "--target", "codex", "--no-clip"])
        finally:
            os.chdir(old_cwd)

    log_path = tmp_path / ".ctx" / "log.jsonl"
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    retrievals = [record for record in records if record["event"] == "retrieval"]
    assert len(retrievals) >= 3
    assert all(record["budget"] > record["tokens_used"] for record in retrievals)

    status_stdout, _ = _run_cli(tmp_path, ["status"])
    assert "All time" in status_stdout
    assert "This session" in status_stdout
    match = re.search(r"All time\s+\d+\s+queries\s+saved ~([0-9,]+) tokens", status_stdout)
    assert match is not None
    assert int(match.group(1).replace(",", "")) > 0


def test_incremental_reindex_only_reports_changed_files(tmp_path):
    _build_project(tmp_path)
    extra = tmp_path / "src" / "extra.py"
    extra.write_text("def helper():\n    return 1\n", encoding="utf-8")

    first_stdout, _ = _run_cli(tmp_path, ["index"])
    assert "Indexed" in first_stdout

    service_file = tmp_path / "src" / "service.py"
    service_file.write_text(service_file.read_text(encoding="utf-8") + "\n\ndef newly_added():\n    return 42\n", encoding="utf-8")

    second_stdout, _ = _run_cli(tmp_path, ["index"])
    assert "Indexed" in second_stdout
    assert "  Indexed" in second_stdout
    assert "  Unchanged" in second_stdout
    assert "  Indexed                 1 file(s)" in second_stdout
    assert "  Unchanged               1 file(s)" in second_stdout
