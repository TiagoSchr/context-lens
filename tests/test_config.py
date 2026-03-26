"""Tests for config merging and target-specific budgets."""
from __future__ import annotations

import json
from pathlib import Path

from src.ctx.config import load_config, save_config


def test_load_config_applies_target_budget_from_env(tmp_path, monkeypatch):
    save_config(tmp_path, {"token_budget": 9000})
    monkeypatch.setenv("LENS_TARGET", "copilot")

    cfg = load_config(tmp_path)

    assert cfg["token_budget"] == 4000
    assert cfg["target_budgets"]["claude"] == 8000


def test_save_config_preserves_custom_target_budget(tmp_path):
    save_config(
        tmp_path,
        {
            "target_budgets": {
                "copilot": 3500,
            }
        },
    )

    raw = json.loads((tmp_path / ".ctx" / "config.json").read_text(encoding="utf-8"))

    assert raw["target_budgets"]["copilot"] == 3500
    assert raw["target_budgets"]["claude"] == 8000
