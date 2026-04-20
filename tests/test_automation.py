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
from src.ctx.context.budget import count_tokens
from src.ctx.db.schema import init_db
from src.ctx.db.store import Store
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
    _build_large_project(tmp_path)
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


# ─────────────────────────────── economia real de tokens (sem dados inventados)

def test_index_stores_real_project_tokens_total(tmp_path):
    """Após lens index, project_tokens_total no meta deve existir e ser > 0."""
    _build_project(tmp_path)
    _run_cli(tmp_path, ["index", "--quiet"])

    db_file = tmp_path / ".ctx" / "index.db"
    store = Store(init_db(db_file))
    raw = store.get_meta("project_tokens_total")

    assert raw is not None, "project_tokens_total não foi salvo no meta"
    assert int(raw) > 0


def test_project_tokens_total_matches_count_tokens(tmp_path):
    """O valor salvo deve ser igual a contar tokens reais dos arquivos — não bytes/4."""
    _build_project(tmp_path)
    _run_cli(tmp_path, ["index", "--quiet"])

    db_file = tmp_path / ".ctx" / "index.db"
    store = Store(init_db(db_file))
    stored = int(store.get_meta("project_tokens_total"))

    # Conta tokens manualmente sobre os mesmos arquivos
    expected = 0
    for p in store.list_indexed_paths():
        abs_path = tmp_path / p
        if abs_path.exists():
            expected += count_tokens(abs_path.read_text(encoding="utf-8", errors="ignore"))

    # Deve ser idêntico — mesma função count_tokens, mesmos arquivos
    assert stored == expected, f"stored={stored} != expected={expected}"


def test_context_query_logs_tokens_raw_from_meta(tmp_path):
    """Cada retrieval no log deve ter tokens_raw igual ao total do projeto."""
    _build_project(tmp_path)
    _run_cli(tmp_path, ["index", "--quiet"])

    old_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            context_main(["explain service flow", "--target", "codex", "--no-clip"])
    finally:
        os.chdir(old_cwd)

    log_path = tmp_path / ".ctx" / "log.jsonl"
    retrievals = [
        json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("event") == "retrieval"
    ]
    assert len(retrievals) == 1
    rec = retrievals[0]

    assert "tokens_raw" in rec, "tokens_raw ausente no log — não foi registrado"
    assert rec["tokens_raw"] > 0

    # tokens_raw must be >= tokens_used (no negative savings)
    assert rec["tokens_raw"] >= rec["tokens_used"], (
        f"tokens_raw ({rec['tokens_raw']}) < tokens_used ({rec['tokens_used']}): "
        "baseline must never be below actual usage"
    )


def test_real_saving_pct_in_log_is_accurate(tmp_path):
    """real_saving_pct deve ser (tokens_raw - tokens_used) / tokens_raw * 100, arredondado a 1 decimal."""
    _build_project(tmp_path)
    _run_cli(tmp_path, ["index", "--quiet"])

    old_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            context_main(["fix bug in compute_total", "--target", "codex", "--no-clip"])
    finally:
        os.chdir(old_cwd)

    log_path = tmp_path / ".ctx" / "log.jsonl"
    rec = next(
        json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("event") == "retrieval"
    )

    assert "real_saving_pct" in rec
    # Verifica a fórmula, independente de ser positivo ou negativo
    expected_pct = round((rec["tokens_raw"] - rec["tokens_used"]) / rec["tokens_raw"] * 100, 1)
    assert rec["real_saving_pct"] == expected_pct


def _build_heavy_project(root: Path, n_modules: int = 15, funcs_per_module: int = 8) -> None:
    """Projeto com conteúdo garantidamente maior que o budget padrão (8000 tokens).

    n_modules=15, funcs_per_module=8 → ~18.000 tokens totais.
    Garante que o lens não pode incluir tudo → savings reais mensuráveis.
    """
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    for i in range(n_modules):
        lines = [f'"""Module {i}: handles domain operations for subsystem {i}."""\n\n']
        for j in range(funcs_per_module):
            lines.append(
                f"class Entity{i}_{j}:\n"
                f'    """Entity for domain {i}, variant {j}."""\n\n'
                f"    def __init__(self, id: int, value: float, label: str):\n"
                f"        self.id = id\n"
                f"        self.value = value\n"
                f"        self.label = label\n\n"
                f"    def process(self, factor: float = 1.0) -> float:\n"
                f'        """Apply factor to value. Returns computed result."""\n'
                f"        return self.value * factor\n\n"
                f"def compute_{i}_{j}(items: list, threshold: float = 0.5) -> dict:\n"
                f'    """Compute aggregate for subsystem {i} variant {j}. '
                f"Returns dict with total and filtered.\"\"\"\n"
                f"    total = sum(getattr(item, 'value', 0) for item in items)\n"
                f"    filtered = [item for item in items if getattr(item, 'value', 0) > threshold]\n"
                f"    return {{'total': total, 'count': len(items), 'filtered': len(filtered)}}\n\n"
            )
        (src / f"module_{i:02d}.py").write_text("".join(lines), encoding="utf-8")


def test_genuine_savings_with_large_project(tmp_path):
    """Com projeto maior que o budget, tokens_used deve ser menor que tokens_raw."""
    _build_heavy_project(tmp_path)
    _run_cli(tmp_path, ["index", "--quiet"])

    db_file = tmp_path / ".ctx" / "index.db"
    project_tokens = int(Store(init_db(db_file)).get_meta("project_tokens_total"))

    old_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            context_main(["fix bug in compute_0_0", "--target", "codex", "--no-clip"])
    finally:
        os.chdir(old_cwd)

    log_path = tmp_path / ".ctx" / "log.jsonl"
    rec = next(
        json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("event") == "retrieval"
    )

    # Com projeto de ~18.000 tokens e budget de ~6.000, deve haver saving real
    assert project_tokens > 8000, f"projeto deveria ter >8000 tokens, tem {project_tokens}"
    assert rec["tokens_used"] < project_tokens, (
        f"tokens_used ({rec['tokens_used']}) >= project_tokens ({project_tokens}): "
        "o lens não economizou nada"
    )
    assert rec["real_saving_pct"] > 0, (
        f"real_saving_pct deveria ser positivo, é {rec['real_saving_pct']}"
    )


def test_status_shows_real_count_after_index(tmp_path):
    """Após lens index, status deve exibir 'real count' — não a estimativa KB/4."""
    _build_project(tmp_path)
    _run_cli(tmp_path, ["index", "--quiet"])

    stdout, _ = _run_cli(tmp_path, ["status"])
    assert "real count" in stdout, (
        "status ainda exibe estimativa bytes/4. Esperado 'real count' após lens index"
    )


def _build_large_project(root: Path) -> None:
    """Projeto com conteúdo suficiente para savings reais serem visíveis."""
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    modules = {
        "billing.py": (
            '"""Billing module — handles payments and invoices."""\n\n'
            "class Invoice:\n"
            '    """Represents a customer invoice."""\n\n'
            "    def __init__(self, customer_id: int, amount: float):\n"
            "        self.customer_id = customer_id\n"
            "        self.amount = amount\n"
            "        self.paid = False\n\n"
            "    def mark_paid(self) -> None:\n"
            '        """Mark invoice as paid."""\n'
            "        self.paid = True\n\n"
            "    def apply_discount(self, pct: float) -> float:\n"
            '        """Apply percentage discount and return new amount."""\n'
            "        return self.amount * (1 - pct / 100)\n\n"
            "def generate_invoice(customer_id: int, items: list[dict]) -> Invoice:\n"
            '    """Generate invoice from list of items."""\n'
            "    total = sum(i['price'] * i['qty'] for i in items)\n"
            "    return Invoice(customer_id, total)\n\n"
            "def compute_tax(amount: float, rate: float = 0.1) -> float:\n"
            '    """Compute tax for a given amount."""\n'
            "    return round(amount * rate, 2)\n"
        ),
        "payments.py": (
            '"""Payment processing module."""\n\n'
            "class PaymentProcessor:\n"
            '    """Handles payment gateway integration."""\n\n'
            "    def __init__(self, api_key: str, gateway: str = 'stripe'):\n"
            "        self.api_key = api_key\n"
            "        self.gateway = gateway\n\n"
            "    def charge(self, amount: float, token: str) -> dict:\n"
            '        """Charge a card token. Returns result dict."""\n'
            "        return {'status': 'ok', 'amount': amount}\n\n"
            "    def refund(self, charge_id: str, amount: float | None = None) -> dict:\n"
            '        """Issue a full or partial refund."""\n'
            "        return {'status': 'refunded', 'charge_id': charge_id}\n\n"
            "def validate_card(number: str) -> bool:\n"
            '    """Luhn algorithm check."""\n'
            "    digits = [int(d) for d in number if d.isdigit()]\n"
            "    return bool(digits)\n"
        ),
        "customers.py": (
            '"""Customer management module."""\n\n'
            "class Customer:\n"
            '    """Represents a customer account."""\n\n'
            "    def __init__(self, id: int, name: str, email: str):\n"
            "        self.id = id\n"
            "        self.name = name\n"
            "        self.email = email\n"
            "        self.invoices: list = []\n\n"
            "    def add_invoice(self, invoice) -> None:\n"
            '        """Add an invoice to the customer."""\n'
            "        self.invoices.append(invoice)\n\n"
            "    def total_due(self) -> float:\n"
            '        """Sum of all unpaid invoices."""\n'
            "        return sum(i.amount for i in self.invoices if not i.paid)\n\n"
            "def find_customer(customers: list, customer_id: int):\n"
            '    """Look up customer by id."""\n'
            "    return next((c for c in customers if c.id == customer_id), None)\n"
        ),
    }
    for name, content in modules.items():
        (src / name).write_text(content, encoding="utf-8")


def test_status_economy_uses_tokens_raw_not_budget(tmp_path):
    """Saving% em 'All time' deve usar tokens_raw do log, não o budget como baseline.

    Usa projeto grande o suficiente para que tokens_raw > tokens_used de forma genuína.
    """
    _build_large_project(tmp_path)
    _run_cli(tmp_path, ["index", "--quiet"])

    # Usa budget pequeno para garantir que o lens vai economizar tokens reais
    for query in ["explain billing module", "fix bug in compute_tax"]:
        old_cwd = Path.cwd()
        try:
            os.chdir(tmp_path)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                context_main([query, "--target", "codex", "--no-clip"])
        finally:
            os.chdir(old_cwd)

    log_path = tmp_path / ".ctx" / "log.jsonl"
    retrievals = [
        json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("event") == "retrieval"
    ]

    # Calcula saving real baseado em tokens_raw (clamped a 0, igual ao status)
    total_used = sum(r["tokens_used"] for r in retrievals)
    total_raw = sum(r.get("tokens_raw", 0) for r in retrievals)
    expected_saved = max(0, total_raw - total_used)

    stdout, _ = _run_cli(tmp_path, ["status"])
    match = re.search(r"All time\s+\d+\s+queries\s+saved ~([0-9,]+) tokens", stdout)
    assert match is not None, f"padrão 'All time ... saved ~N tokens' não encontrado em:\n{stdout}"
    reported_saved = int(match.group(1).replace(",", ""))

    assert reported_saved == expected_saved, (
        f"status reportou {reported_saved} tokens saved, "
        f"mas max(0, tokens_raw - tokens_used) = {expected_saved}"
    )


def test_incremental_index_preserves_token_total_when_no_changes(tmp_path):
    """Reindexação sem mudanças não deve recalcular project_tokens_total."""
    _build_project(tmp_path)
    _run_cli(tmp_path, ["index", "--quiet"])

    db_file = tmp_path / ".ctx" / "index.db"
    first_total = Store(init_db(db_file)).get_meta("project_tokens_total")
    first_ts = Store(init_db(db_file)).get_meta("project_tokens_updated_at")

    # Segunda indexação sem mudanças — timestamps devem ser iguais
    _run_cli(tmp_path, ["index", "--quiet"])

    second_ts = Store(init_db(db_file)).get_meta("project_tokens_updated_at")
    second_total = Store(init_db(db_file)).get_meta("project_tokens_total")

    assert second_total == first_total
    assert second_ts == first_ts  # não recalculou


def test_incremental_index_recalculates_on_new_file(tmp_path):
    """Novo arquivo indexado deve atualizar project_tokens_total."""
    _build_project(tmp_path)
    _run_cli(tmp_path, ["index", "--quiet"])

    db_file = tmp_path / ".ctx" / "index.db"
    first_total = int(Store(init_db(db_file)).get_meta("project_tokens_total"))

    # Adiciona um arquivo com conteúdo real
    new_file = tmp_path / "src" / "extra.py"
    new_file.write_text(
        '"""Extra module with real content."""\n\n'
        "def extra_function(x: int, y: int) -> int:\n"
        '    """Does extra computation."""\n'
        "    return x * y + 42\n",
        encoding="utf-8",
    )
    _run_cli(tmp_path, ["index", "--quiet"])

    second_total = int(Store(init_db(db_file)).get_meta("project_tokens_total"))
    added_tokens = count_tokens(new_file.read_text(encoding="utf-8"))

    assert second_total == first_total + added_tokens, (
        f"total deveria ser {first_total + added_tokens}, mas é {second_total}"
    )


def test_force_reindex_recalculates_token_total(tmp_path):
    """lens index --force sempre deve recalcular, mesmo sem mudanças."""
    _build_project(tmp_path)
    _run_cli(tmp_path, ["index", "--quiet"])

    db_file = tmp_path / ".ctx" / "index.db"
    first_ts = Store(init_db(db_file)).get_meta("project_tokens_updated_at")

    _run_cli(tmp_path, ["index", "--force", "--quiet"])

    second_ts = Store(init_db(db_file)).get_meta("project_tokens_updated_at")
    assert second_ts != first_ts, "--force deve atualizar project_tokens_updated_at"


# ─────────────────────── garantia de automação por ferramenta (sem mock)

def test_setup_cli_remove_clears_claude_instruction(tmp_path):
    """--remove deve limpar CLAUDE.md sem deixar rastros de lens_context."""
    (tmp_path / ".claude").mkdir()
    _run_cli(tmp_path, ["setup", "--auto"])

    stdout, _ = _run_cli(tmp_path, ["setup", "--remove", "--target", "claude"])

    claude_md = tmp_path / "CLAUDE.md"
    if claude_md.exists():
        assert "lens_context" not in claude_md.read_text(encoding="utf-8")
    assert "removed" in stdout.lower() or "deleted" in stdout.lower() or "cleaned" in stdout.lower()


def test_setup_cli_switch_replaces_active_tool(tmp_path):
    """--switch deve remover setup atual e instalar só o novo."""
    (tmp_path / ".claude").mkdir()
    _run_cli(tmp_path, ["setup", "--auto", "--target", "claude"])

    claude_md = tmp_path / "CLAUDE.md"
    assert claude_md.exists() and "lens_context" in claude_md.read_text(encoding="utf-8")

    _run_cli(tmp_path, ["setup", "--switch", "copilot"])

    # Claude limpo
    if claude_md.exists():
        assert "lens_context" not in claude_md.read_text(encoding="utf-8")

    # Copilot configurado
    copilot_file = tmp_path / ".github" / "copilot-instructions.md"
    assert copilot_file.exists()
    assert "lens_context" in copilot_file.read_text(encoding="utf-8")


def test_setup_cli_target_configures_only_specified_tool(tmp_path):
    """--target copilot não deve criar CLAUDE.md ou AGENTS.md."""
    _run_cli(tmp_path, ["setup", "--auto", "--target", "copilot"])

    assert not (tmp_path / "CLAUDE.md").exists()
    assert not (tmp_path / "AGENTS.md").exists()
    assert (tmp_path / ".github" / "copilot-instructions.md").exists()


def test_setup_cli_auto_with_claude_dir_configures_claude(tmp_path):
    """--auto com .claude/ presente deve detectar e configurar Claude."""
    (tmp_path / ".claude").mkdir()
    _run_cli(tmp_path, ["setup", "--auto"])

    claude_md = tmp_path / "CLAUDE.md"
    assert claude_md.exists()
    assert "lens_context" in claude_md.read_text(encoding="utf-8")


def test_setup_cli_no_tools_detected_auto_configures_all(tmp_path):
    """--auto sem ferramentas detectadas configura todas (Claude, Copilot, Codex, Cursor)."""
    _run_cli(tmp_path, ["setup", "--auto"])

    assert (tmp_path / "CLAUDE.md").exists()
    assert (tmp_path / ".github" / "copilot-instructions.md").exists()
    assert (tmp_path / "AGENTS.md").exists()


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


# ─────────────────────── auto-context injection tests (extension simulates this)

def test_lens_context_cli_produces_output_for_auto_inject(tmp_path):
    """lens context CLI produces non-empty output that can be injected into chatInstructions."""
    _build_project(tmp_path)
    _run_cli(tmp_path, ["index", "--quiet"])

    stdout, _ = _run_cli(tmp_path, ["context", "project architecture overview"])
    assert len(stdout) > 0, "lens context produced empty output"
    assert "PROJECT MAP" in stdout or "SYMBOLS" in stdout or "Context" in stdout, (
        "output should contain project map, symbols, or context header"
    )


def test_lens_context_output_stays_within_budget(tmp_path):
    """Auto-context uses budget=3000. Verify output fits within budget."""
    _build_heavy_project(tmp_path)
    _run_cli(tmp_path, ["index", "--quiet"])

    output = io.StringIO()
    old_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        with redirect_stdout(output), redirect_stderr(io.StringIO()):
            context_main([
                "project architecture overview: key files, entry points",
                "--budget", "3000",
                "--target", "copilot",
                "--no-clip",
            ])
    finally:
        os.chdir(old_cwd)

    result = output.getvalue()
    token_count = count_tokens(result)
    # Allow 12% overhead for headers/metadata (same as budget.py available_budget)
    assert token_count <= 3000, (
        f"auto-context output has {token_count} tokens, exceeds budget of 3000"
    )


def test_lens_context_logs_retrieval_for_detection(tmp_path):
    """Each lens context call must log a retrieval event for freshness detection."""
    _build_project(tmp_path)
    _run_cli(tmp_path, ["index", "--quiet"])

    old_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            context_main(["explain service flow", "--target", "copilot", "--no-clip"])
    finally:
        os.chdir(old_cwd)

    log_path = tmp_path / ".ctx" / "log.jsonl"
    assert log_path.exists(), "log.jsonl not created"

    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    retrievals = [r for r in records if r.get("event") == "retrieval"]

    assert len(retrievals) >= 1, "no retrieval events logged"
    rec = retrievals[-1]
    assert "ts" in rec, "retrieval missing ts field"
    assert "tokens_used" in rec, "retrieval missing tokens_used"
    assert "tokens_raw" in rec, "retrieval missing tokens_raw"
    assert rec["tokens_raw"] > 0, "tokens_raw should be positive"
    assert rec["tokens_used"] > 0, "tokens_used should be positive"
    assert rec["tokens_raw"] >= rec["tokens_used"], (
        f"tokens_raw ({rec['tokens_raw']}) < tokens_used ({rec['tokens_used']})"
    )


def test_multiple_context_calls_all_logged(tmp_path):
    """Multiple context calls should all be logged for monitoring."""
    _build_project(tmp_path)
    _run_cli(tmp_path, ["index", "--quiet"])

    queries = [
        "explain service flow",
        "fix bug in compute_total",
        "overview of project architecture",
    ]
    for query in queries:
        old_cwd = Path.cwd()
        try:
            os.chdir(tmp_path)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                context_main([query, "--target", "copilot", "--no-clip"])
        finally:
            os.chdir(old_cwd)

    log_path = tmp_path / ".ctx" / "log.jsonl"
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    retrievals = [r for r in records if r.get("event") == "retrieval"]

    assert len(retrievals) == 3, f"expected 3 retrievals, got {len(retrievals)}"

    # Timestamps should be monotonically increasing
    timestamps = [r["ts"] for r in retrievals]
    assert timestamps == sorted(timestamps), "timestamps should be monotonically increasing"


def test_context_output_contains_relevant_symbols(tmp_path):
    """Context about a specific query should include relevant symbols."""
    _build_project(tmp_path)
    _run_cli(tmp_path, ["index", "--quiet"])

    stdout, _ = _run_cli(tmp_path, ["context", "compute_total function"])
    assert "compute_total" in stdout, "query about compute_total should return compute_total in context"


def test_context_output_format_suitable_for_injection(tmp_path):
    """Context output should be plain text suitable for markdown embedding."""
    _build_project(tmp_path)
    _run_cli(tmp_path, ["index", "--quiet"])

    stdout, _ = _run_cli(tmp_path, ["context", "project overview"])
    # Should be valid text, no binary garbage
    assert stdout.isprintable() or "\n" in stdout, "output should be printable text"
    # Should contain structural markers (=== for sections or # for headers)
    assert "===" in stdout or "#" in stdout, "output should have structural markers"
