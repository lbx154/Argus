"""V5 vertical-improvement smoke tests: stage-entry contracts, capability-consumption
trace, Novelty-Seeking Loop, original-research-required mode, anti-over-hedging.

Hermetic: no network, no external capability library required (gates fall back to the
in-source base); mode env vars set via monkeypatch.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.skills import capability_trace
from argus_skill.verticals.physics import manuscript, mode_config, stages
from argus_skill.verticals.physics.gates import novelty_seeking as nsl
from argus_skill.verticals.physics.gates import paper_type as pt


def _seed_stage(root: Path, stage: str) -> None:
    (root / ".argus").mkdir(parents=True, exist_ok=True)
    (root / ".argus" / "PIPELINE_STATE.json").write_text(
        json.dumps({"current_stage": stage, "vertical": "physics"}), encoding="utf-8")


# ---- 1. stage-entry contracts ---------------------------------------------- #

def test_stage_entry_contract_text_present() -> None:
    for stage in ("scope", "model", "execute", "review", "manuscript"):
        c = stages.stage_entry_contract(stage)
        assert "focus" in c.lower() and stage in c.lower()


def test_stage_entry_contract_injected_into_banner(tmp_path: Path) -> None:
    _seed_stage(tmp_path, "scope")
    banner = stages.role_banner("engineer", project_root=tmp_path)
    assert "## Scope focus" in banner
    assert "not a fixed matrix or paper count" in banner
    # a different stage yields that stage's contract
    _seed_stage(tmp_path, "execute")
    b2 = stages.role_banner("engineer", project_root=tmp_path)
    assert "## Execute focus" in b2 and "claim-bearing" in b2


# ---- 2. original-research-required mode banner ----------------------------- #

def test_mode_banner_only_in_original_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_stage(tmp_path, "review")
    # Tiered default: the banner is TIERED and does NOT carry the old hard
    # "RUN MODE — ORIGINAL RESEARCH REQUIRED" no-downgrade pin.
    auto = stages.role_banner("engineer", project_root=tmp_path)
    assert "RUN MODE — ORIGINAL RESEARCH REQUIRED" not in auto
    assert "## Physics strategy" in auto
    assert "scorecard" in auto
    # Opt-in original-research: now a STRETCH target (not a hard no-downgrade gate).
    monkeypatch.setenv("ARGUS_SKILL_PHYSICS_TARGET_PAPER_TYPE", "original_research_article")
    monkeypatch.setenv("ARGUS_SKILL_PHYSICS_ALLOW_DOWNGRADE", "false")
    assert mode_config.is_original_research_required()
    stretch = stages.role_banner("engineer", project_root=tmp_path)
    assert "operator requested original research" in stretch
    assert "## Physics strategy" in stretch


# ---- 3. capability consumption trace --------------------------------------- #

def test_capability_trace_records_gate_run(tmp_path: Path) -> None:
    from argus_skill.verticals.physics.gates import novelty
    novelty.run_gate(tmp_path)  # missing table -> NOV-000, but trace must be written
    tr = json.loads((tmp_path / capability_trace.TRACE_REL).read_text())
    assert "novelty" in tr["gates"]
    rec = tr["gates"]["novelty"]
    assert set(capability_trace.RECORD_FIELDS).issubset(rec.keys())
    assert rec["available_count"] >= 5  # base novelty caps at least
    assert "NOV-000" in rec["failure_ids_caused_by_missing_capability"]


def test_retired_novelty_table_gate_never_blocks_research(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus_skill.skills.research_gates import (
        read_gate_state,
        update_gate_state,
    )

    monkeypatch.setenv("ARGUS_SKILL_PHYSICS_TARGET_PAPER_TYPE", "original_research_article")
    monkeypatch.setenv("ARGUS_SKILL_PHYSICS_ALLOW_DOWNGRADE", "false")
    update_gate_state(
        tmp_path,
        nsl.GATE_ID,
        [{
            "failure_id": "NSL-OLD",
            "severity": "major",
            "stage": "review",
            "artifact": "NOVELTY_IDEA_POOL.csv",
            "field": "rows",
            "message": "stale table failure",
            "required_action": "fill the table",
            "blocks_progress": False,
        }],
    )
    assert nsl.verify_novelty_seeking(tmp_path) == []
    assert nsl.run_gate(tmp_path) == (True, [])
    assert read_gate_state(tmp_path, nsl.GATE_ID) is None
    assert nsl.REASONING_COLUMNS == ()
    assert nsl.SCORE_COLUMNS == ()


# ---- 5. original-research mode blocks a downgrade terminal ------------------ #

def _classifier(root: Path, paper_type: str) -> None:
    data = {f: "x" for f in pt.REQUIRED_FIELDS}
    data["paper_type"] = paper_type
    data["confidence"] = "high"
    (root / pt.ARTIFACT).write_text(json.dumps(data), encoding="utf-8")


def test_paper_type_pt006_blocks_downgrade_in_original_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_PHYSICS_TARGET_PAPER_TYPE", "original_research_article")
    monkeypatch.setenv("ARGUS_SKILL_PHYSICS_ALLOW_DOWNGRADE", "false")
    _classifier(tmp_path, "diagnostic benchmark")
    codes = [f["failure_id"] for f in pt.verify_paper_type(tmp_path)]
    assert "PT-006" in codes


def test_manuscript_hardgate_rejects_downgrade_terminal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_PHYSICS_TARGET_PAPER_TYPE", "original_research_article")
    monkeypatch.setenv("ARGUS_SKILL_PHYSICS_ALLOW_DOWNGRADE", "false")
    _classifier(tmp_path, "diagnostic benchmark")
    fails = manuscript.verify_all_deliverables(tmp_path)
    assert any("original-research-required mode" in f for f in fails)


def test_auto_mode_allows_diagnostic_benchmark(tmp_path: Path) -> None:
    _classifier(tmp_path, "diagnostic benchmark")
    assert "PT-006" not in [f["failure_id"] for f in pt.verify_paper_type(tmp_path)]
    assert not any("original-research-required mode" in f for f in manuscript.verify_all_deliverables(tmp_path))


# ---- 6. anti-over-hedging (issue 六) --------------------------------------- #

def test_overhedge_counts_repeated_disclaimer() -> None:
    text = (
        "We do not claim a new phase. This is not a universal scaling result. "
        "We do not discuss disorder. Disorder is not treated here. "
        "There is no disorder analysis. Disorder effects are not included. "
        "We do not address disorder at all."
    )
    counts = manuscript._overhedge_counts(text)
    assert counts.get("disorder", 0) > manuscript.MAX_DISCLAIMER_REPEATS_PER_FAMILY


def test_overhedge_lenient_on_few_disclaimers() -> None:
    text = "We do not discuss disorder. The results concern the clean limit and its edge spectrum."
    counts = manuscript._overhedge_counts(text)
    assert counts.get("disorder", 0) <= manuscript.MAX_DISCLAIMER_REPEATS_PER_FAMILY
