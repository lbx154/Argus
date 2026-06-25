"""Regression tests for the operator-audit fixes (map-and-prune pass).

Each test pins one confirmed discrepancy the audit found, in the spirit of the
fix: prefer UNBLOCKING the agent (optional schema channels) over adding hard
control, and keep the harness's bookkeeping faithful + bounded.
"""
from __future__ import annotations

import json
from pathlib import Path

import argus_skill.engineer.reviewer as reviewer_mod
import argus_skill.life.telemetry as telemetry
from argus_skill.core.models import CheckResult
from argus_skill.engineer.runner import failed_check_diagnostics
from argus_skill.meta import ledger
from argus_skill.meta.flow_controller import FlowDecision, record_decision
from argus_skill.meta.saturation import SaturationSignal
from argus_skill.planner.planner import PLANNER_SCHEMA_PATH


# --------------------------------------------------------------------------- #
# regime-jump JUDGE/ENFORCE — unblock the channel, no hard control
# --------------------------------------------------------------------------- #
def test_planner_schema_allows_optional_meta_decision():
    schema = json.loads(Path(PLANNER_SCHEMA_PATH).read_text())
    assert "meta_decision" in schema["properties"]  # the agent now has a channel
    assert "meta_decision" not in schema["required"]  # optional → agent's call


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
        "confidence": 0.7,
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
    assert "active_line" in cp["properties"]
    assert "env_facts" in cp["properties"]
    # optional — the reviewer only emits them when it is carrying a line
    assert "active_line" not in cp["required"]
    assert "env_facts" not in cp["required"]


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
