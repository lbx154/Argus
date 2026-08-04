"""Data-domain resolution end-to-end + the byte-identical-floor guarantee.

The highest-value regression check: with NO project data domain and NO
``research/CHECKLISTS.json``, the existing verticals render exactly as before.
"""

from __future__ import annotations

import json

from argus_skill.skills import stage_machine as sc
from argus_skill.skills import vertical_select as vs
from argus_skill.verticals import _data_domain as dd


def _write_store(root, *, vertical: str, stages: dict) -> None:
    path = root / "research" / "CHECKLISTS.json"
    path.write_text(
        json.dumps({"revision": 1, "vertical": vertical, "stages": stages}),
        encoding="utf-8",
    )


def test_undecided_legacy_project_keeps_research_seed(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    a = tmp_path / "proj_a"
    b = tmp_path / "proj_b"
    a.mkdir()
    b.mkdir()
    # Two undecided legacy projects keep the historical research seed.
    body_a = sc.format_full_pipeline_checklist(role="reviewer", project_root=a)
    body_b = sc.format_full_pipeline_checklist(role="reviewer", project_root=b)
    assert body_a == body_b
    assert "research.literature" in body_a
    assert "submission.readiness" in body_a


def test_data_domain_resolves_and_seeds_first_stage(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    dd.write_data_domain(
        tmp_path, "robotics_sim", stages=["scope", "simulate", "measure", "report"]
    )
    vs.persist_vertical(tmp_path, "robotics_sim")
    assert vs.resolve_vertical(tmp_path) == "robotics_sim"
    assert sc.current_stage(tmp_path) == "scope"  # seeded to the domain's first stage
    order, _items = sc._active_vertical_checklist_defs(tmp_path)
    assert list(order) == ["scope", "simulate", "measure", "report"]


def test_default_research_env_preserves_persisted_data_domain(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_VERTICAL", "research")
    dd.write_data_domain(
        tmp_path, "robotics_sim", stages=["scope", "simulate", "measure", "report"]
    )
    vs.persist_vertical(tmp_path, "robotics_sim")

    assert vs.resolve_vertical(tmp_path) == "robotics_sim"


# NOTE: the checklist-store DATA-domain INFERENCE (_data_domain_from_checklists)
# was removed — resolve_vertical is now FAIL-HARD and never guesses the vertical
# from checklist stages. The Manager decides + persists the vertical explicitly.
# The two tests that pinned that (now-deleted) inference were removed here.


def test_current_stage_uses_data_domain_under_default_research_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_VERTICAL", "research")
    dd.write_data_domain(
        tmp_path, "robotics_sim", stages=["scope", "simulate", "measure", "report"]
    )
    vs.persist_vertical(tmp_path, "robotics_sim")
    state_path = tmp_path / "research" / "PIPELINE_STATE.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["current_stage"] = "simulate"
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    assert sc.current_stage(tmp_path) == "simulate"
    body = sc.format_stage_checklist("simulate", role="reviewer", project_root=tmp_path)
    assert "research.literature" not in body


def test_manager_persisted_data_domain_wins_over_bootstrap_builtin_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_VERTICAL", "speedrun")
    dd.write_data_domain(
        tmp_path, "robotics_sim", stages=["scope", "simulate", "measure", "report"]
    )
    vs.persist_vertical(tmp_path, "robotics_sim")

    assert vs.resolve_vertical(tmp_path) == "robotics_sim"
    assert sc.current_stage(tmp_path) == "scope"


def test_store_override_shows_in_render(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    dd.write_data_domain(tmp_path, "robotics_sim", stages=["scope", "simulate"])
    vs.persist_vertical(tmp_path, "robotics_sim")
    _write_store(
        tmp_path,
        vertical="robotics_sim",
        stages={
            "simulate": [
                {
                    "id": "simulate.seeds",
                    "statement": "Run at least 3 seeds",
                    "evidence_hint": "runs/*/seed*",
                }
            ]
        },
    )
    body = sc.format_stage_checklist("simulate", role="reviewer", project_root=tmp_path)
    assert "simulate.seeds" in body and "Run at least 3 seeds" in body


def test_data_domain_gate_is_not_full_paper(tmp_path, monkeypatch):
    # R5-1: the gate / prompt call sites must thread project_root into load_vertical
    # so a Manager-authored data domain (completion_gate="none") is honored, not
    # silently resolved to research/full_paper -- which would wedge a metric mission
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


def test_store_custom_item_merges_with_seed_for_research_stage(tmp_path, monkeypatch):
    # Read compatibility: a historical custom item MERGES with the seed for that
    # stage (non-protected edits). Other stages keep their seed unchanged.
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    vs.persist_vertical(tmp_path, "research")
    _write_store(
        tmp_path,
        vertical="research",
        stages={
            "research": [
                {
                    "id": "research.custom",
                    "statement": "a custom research gate",
                    "evidence_hint": "x",
                }
            ]
        },
    )
    body = sc.format_stage_checklist("research", role="reviewer", project_root=tmp_path)
    assert "research.custom" in body
    # 'research.literature' is the seed; seeds are merged (not replaced) so it
    # is still present alongside the custom item.
    assert "research.literature" in body
    # A stage with no store entry still renders its seed.
    plan_body = sc.format_stage_checklist("plan", role="reviewer", project_root=tmp_path)
    assert "plan.experiment" in plan_body
