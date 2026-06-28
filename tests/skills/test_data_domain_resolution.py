"""Data-domain resolution end-to-end + the byte-identical-floor guarantee.

The highest-value regression check: with NO project data domain and NO
``research/CHECKLISTS.json``, the existing verticals render exactly as before.
"""
from __future__ import annotations

from argus_skill.skills import checklist_store as cs
from argus_skill.skills import stage_checklists as sc
from argus_skill.skills import vertical_select as vs
from argus_skill.verticals import _data_domain as dd


def test_byte_identical_floor_when_no_project_data(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    a = tmp_path / "proj_a"
    b = tmp_path / "proj_b"
    a.mkdir()
    b.mkdir()
    # Two fresh projects with no DOMAINS/ and no CHECKLISTS.json render identically
    # to each other AND each contains the historical research floor items.
    body_a = sc.format_full_pipeline_checklist(role="reviewer", project_root=a)
    body_b = sc.format_full_pipeline_checklist(role="reviewer", project_root=b)
    assert body_a == body_b
    assert "research.literature" in body_a
    assert "submission.assurance" in body_a


def test_data_domain_resolves_and_seeds_first_stage(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    dd.write_data_domain(tmp_path, "robotics_sim", stages=["scope", "simulate", "measure", "report"])
    vs.persist_vertical(tmp_path, "robotics_sim")
    assert vs.resolve_vertical(tmp_path) == "robotics_sim"
    assert sc.current_stage(tmp_path) == "scope"           # seeded to the domain's first stage
    order, _items = sc._active_vertical_checklist_defs(tmp_path)
    assert list(order) == ["scope", "simulate", "measure", "report"]


def test_store_override_shows_in_render(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    dd.write_data_domain(tmp_path, "robotics_sim", stages=["scope", "simulate"])
    vs.persist_vertical(tmp_path, "robotics_sim")
    cs.apply_checklist_ops(tmp_path, [
        {"op": "add", "stage": "simulate", "id": "simulate.seeds",
         "statement": "Run at least 3 seeds", "evidence_hint": "runs/*/seed*"},
    ])
    body = sc.format_stage_checklist("simulate", role="reviewer", project_root=tmp_path)
    assert "simulate.seeds" in body and "Run at least 3 seeds" in body


def test_data_domain_gate_is_not_full_emnlp(tmp_path, monkeypatch):
    # R5-1: the gate / prompt call sites must thread project_root into load_vertical
    # so a Manager-authored data domain (completion_gate="none") is honored, not
    # silently resolved to research/full_emnlp -- which would wedge a metric mission
    # forever (the EMNLP gate can never certify). The full-pipeline title is one such
    # site: a data domain must render as itself, not the EMNLP final-submission gate.
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    dd.write_data_domain(tmp_path, "robotics_sim", stages=["scope", "measure"])
    vs.persist_vertical(tmp_path, "robotics_sim")
    body = sc.format_full_pipeline_checklist(role="reviewer", project_root=tmp_path)
    assert "robotics_sim" in body and "final submission gate" not in body


def test_data_domain_can_advance_past_first_stage(tmp_path, monkeypatch):
    # R6-1: a data domain has a full stage ORDER but an EMPTY CHECKLIST_ITEMS dict
    # (the Planner authors items into research/CHECKLISTS.json separately). Stage
    # existence must be validated against the order, not items -- else every
    # transition ValueErrors and the mission is pinned to stage 1 forever.
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    dd.write_data_domain(tmp_path, "robotics_sim", stages=["scope", "build", "report"])
    vs.persist_vertical(tmp_path, "robotics_sim")
    assert sc.current_stage(tmp_path) == "scope"
    sc.advance_stage(tmp_path, target_stage="build", reason="r6-1 regression")
    assert sc.current_stage(tmp_path) == "build"  # advanced, not stuck on scope


def test_store_override_replaces_seed_for_research_stage(tmp_path, monkeypatch):
    # Even for a paper vertical, an authored store entry REPLACES the seed for that
    # stage (non-protected edits), while other stages keep the seed.
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    cs.apply_checklist_ops(tmp_path, [
        {"op": "add", "stage": "research", "id": "research.custom",
         "statement": "a custom research gate", "evidence_hint": "x"},
    ])
    body = sc.format_stage_checklist("research", role="reviewer", project_root=tmp_path)
    assert "research.custom" in body
    # 'research.literature' is the seed; the store entry replaced the whole stage,
    # so it is no longer present (the Planner now owns this stage's checklist).
    assert "research.literature" not in body
    # A stage with no store entry still renders its seed.
    plan_body = sc.format_stage_checklist("plan", role="reviewer", project_root=tmp_path)
    assert "plan.experiment" in plan_body
