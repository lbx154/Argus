"""Regression tests for the operator-audit fixes (map-and-prune pass).

Each test pins one confirmed discrepancy the audit found, in the spirit of the
fix: prefer UNBLOCKING the agent (optional schema channels) over adding hard
control, and keep the harness's bookkeeping faithful + bounded.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

import argus_skill.reviewer as reviewer_mod
import argus_skill.life.telemetry as telemetry
from argus_skill.core.models import CheckResult
from argus_skill.engineer.runner import failed_check_diagnostics
from argus_skill.regime_jump import ledger
from argus_skill.regime_jump.flow_controller import FlowDecision, record_decision
from argus_skill.regime_jump.saturation import SaturationSignal
from argus_skill.planner.planner import PLANNER_SCHEMA_PATH


# --------------------------------------------------------------------------- #
# regime-jump JUDGE/ENFORCE — unblock the channel, no hard control
# --------------------------------------------------------------------------- #
def test_planner_schema_allows_optional_meta_decision():
    schema = json.loads(Path(PLANNER_SCHEMA_PATH).read_text())
    md = schema["properties"]["meta_decision"]
    assert md  # the agent now has a sanctioned channel
    # required-but-NULLABLE: the model must consider the field, but may emit null
    # when it has nothing to declare — so it's never forced to invent a regime
    # call (agent's judgment preserved via nullability, not via omission).
    assert "null" in md["type"]


def test_meta_decision_from_structured_obj_populates_forbidden_ledger(tmp_path):
    # The planner returns meta_decision as a structured field; the never-cleared
    # forbidden ledger must populate from it (no prose-scraping, no hard reject).
    flow = FlowDecision(
        mode="jump",
        signal=SaturationSignal(frozen_rounds=15, is_saturated=True),
        forbidden_axes=set(),
    )
    meta_obj = {
        "mode": "jump",
        "strategy_type": "optimizer",
        "forbidden": ["residual-temperature row-group tweaks"],
    }
    dec = record_decision(tmp_path, "", flow, now=1.0, meta_obj=meta_obj)
    assert dec.present and dec.valid and dec.strategy_type == "optimizer"
    led = ledger.load_ledger(tmp_path)
    assert any("residual-temperature" in f for f in led.forbidden)
    # and the decision row records the real strategy_type, not 'unknown'
    rows = (tmp_path / "research" / "META_LEDGER.jsonl").read_text().strip().splitlines()
    assert json.loads(rows[-1])["strategy_type"] == "optimizer"


# --------------------------------------------------------------------------- #
# reviewer cumulative-memory — unblock the schema fields
# --------------------------------------------------------------------------- #
def test_reviewer_schema_allows_optional_active_line_and_env_facts():
    schema_path = Path(reviewer_mod.__file__).with_name("reviewer_schema.json")
    schema = json.loads(schema_path.read_text())
    cp = schema["properties"]["checkpoint"]
    assert "active_line" in cp["properties"]  # the cumulative-line channel exists
    assert "env_facts" in cp["properties"]
    # active_line is required-but-NULLABLE: the reviewer must consider it but may
    # emit null when not carrying a line (never forced to fabricate one).
    assert "null" in cp["properties"]["active_line"]["type"]


# --------------------------------------------------------------------------- #
# faithful bookkeeping — bounds actually hold
# --------------------------------------------------------------------------- #
def test_failed_check_diagnostics_respects_total_char_budget():
    checks = [
        CheckResult(command=f"cmd{i}", exit_code=1, passed=False, output_tail="X" * 5000)
        for i in range(12)
    ]
    out = failed_check_diagnostics(checks, max_chars=2600)
    # the actual error payload (the X's) is bounded ACROSS all failing checks,
    # not 400-per-check unbounded
    assert out.count("X") <= 2600
    # but every failing command is still named so nothing is hidden
    for i in range(12):
        assert f"cmd{i}" in out
    assert "omitted to stay within" in out  # honest truncation marker


def test_telemetry_jsonl_rotates_past_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(telemetry, "TELEMETRY_ROLL_BYTES", 500)
    rec = telemetry.TelemetryRecorder(tmp_path)
    for i in range(80):
        rec.record({"i": i, "pad": "y" * 40})
    live = tmp_path / "telemetry.jsonl"
    rolled = tmp_path / "telemetry.jsonl.1"
    assert rolled.exists()  # rotation happened
    # the live file is bounded (cap + at most one extra record), not unbounded
    assert live.stat().st_size < 500 + 200


# --------------------------------------------------------------------------- #
# prune pass — fail-loud (not fail-open) vertical resolution
# --------------------------------------------------------------------------- #
def test_load_vertical_unknown_name_falls_back_quietly():
    from argus_skill.verticals import _base
    mod = _base.load_vertical("totally_made_up_xyz")
    assert mod.__name__.endswith("research.stages")  # unknown → safe fallback


def test_load_vertical_named_but_broken_fails_loud(monkeypatch):
    # A REAL vertical whose stages.py exists but fails to import must NOT silently
    # degrade a metric mission into the paper pipeline — it raises loudly.
    from argus_skill.verticals import _base
    real_import = importlib.import_module

    def fake_import(modname, *a, **k):
        if modname == "argus_skill.verticals.nanochat.stages":
            raise ImportError("simulated broken vertical")
        return real_import(modname, *a, **k)

    monkeypatch.setattr(_base.importlib, "import_module", fake_import)
    monkeypatch.setattr(_base.os.path, "isfile", lambda p: True)  # pretend it exists
    with pytest.raises(RuntimeError, match="paper pipeline"):
        _base.load_vertical("nanochat")


def test_dead_author_prompts_removed():
    from argus_skill.skills.skill_prompts import Prompts
    # Skill memory is reviewer-proposed + Manager-gated now; the in-process
    # distill/revise prompts are gone. Matcher + parser stay.
    assert hasattr(Prompts, "skill_match") and hasattr(Prompts, "parse_skill_output")
    assert not hasattr(Prompts, "distill")
    assert not hasattr(Prompts, "revise")
    assert not hasattr(Prompts, "execute")
    assert not hasattr(Prompts, "repair")
    assert not hasattr(Prompts, "refine_from_feedback")
