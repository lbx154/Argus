"""Tests for data-domain promotion to source (``manager/domain_tidy``).

Promotion requires explicit operator approval; the render must compile to a valid
``stages`` module exposing the vertical contract.
"""
from __future__ import annotations

import json
import types

from argus_skill.manager import domain_tidy as dt
from argus_skill.manager import source_writeback
from argus_skill.skills import checklist_store as cs
from argus_skill.verticals import _data_domain as dd


def _seed_proven_domain(tmp_path):
    dd.write_data_domain(tmp_path, "robotics_sim", stages=["scope", "simulate", "measure", "report"])
    cs.apply_checklist_ops(tmp_path, [
        {"op": "add", "stage": "scope", "id": "scope.obj", "statement": "state the objective", "evidence_hint": "scope/OBJ.md"},
        {"op": "add", "stage": "simulate", "id": "simulate.seeds", "statement": "run >=3 seeds", "evidence_hint": "runs/"},
    ])
    state = tmp_path / "research" / "PIPELINE_STATE.json"
    state.write_text(json.dumps({"current_stage": "simulate", "stages": {"scope": {"status": "done"}}}))


def test_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_PROMOTE_DOMAINS", raising=False)
    _seed_proven_domain(tmp_path)
    assert dt.propose_promotions(tmp_path) == []        # gate OFF → nothing proposed


def test_proposes_only_when_proven(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_PROMOTE_DOMAINS", "1")
    dd.write_data_domain(tmp_path, "robotics_sim", stages=["scope", "simulate"])
    # No PIPELINE_STATE → not proven → no proposal.
    assert dt.propose_promotions(tmp_path) == []
    # Mark a stage done → proven.
    state = tmp_path / "research" / "PIPELINE_STATE.json"
    state.write_text(json.dumps({"current_stage": "simulate", "stages": {"scope": {"status": "done"}}}))
    names = [p.name for p in dt.propose_promotions(tmp_path)]
    assert names == ["robotics_sim"]


def test_promote_requires_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_PROMOTE_DOMAINS", "1")
    _seed_proven_domain(tmp_path)
    # Not approved → no write, returns None.
    assert dt.promote_data_domain(tmp_path, "robotics_sim", approved=False) is None
    # Headless sweep (no approve callback) never writes.
    assert dt.tidy_domains_after_mission(tmp_path, approve=None) == []


def test_rendered_stages_py_is_valid_and_exposes_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_PROMOTE_DOMAINS", "1")
    _seed_proven_domain(tmp_path)
    src = dt._render_stages_py("robotics_sim", tmp_path)
    src = src.replace("from ...skills.stage_checklists", "from argus_skill.skills.stage_checklists")
    mod = types.ModuleType("promoted_stages")
    exec(compile(src, "<stages>", "exec"), mod.__dict__)
    assert mod.STAGE_ORDER == ["scope", "simulate", "measure", "report"]
    assert mod.completion_gate == "none"
    assert [i.id for i in mod.CHECKLIST_ITEMS["scope"]] == ["scope.obj"]
    assert [i.id for i in mod.CHECKLIST_ITEMS["simulate"]] == ["simulate.seeds"]
    assert set(mod.STAGE_CHECKS) == set(mod.STAGE_ORDER)
    assert callable(mod.role_banner)


def test_approved_promotion_uses_shared_source_writeback(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_PROMOTE_DOMAINS", "1")
    _seed_proven_domain(tmp_path)
    verticals_root = tmp_path / "source" / "verticals"
    monkeypatch.setattr(dt, "_verticals_root", lambda: verticals_root)
    committed = []
    monkeypatch.setattr(
        source_writeback,
        "commit_to_source",
        lambda paths, _message: committed.extend(paths) or True,
    )

    stages_path = dt.promote_data_domain(
        tmp_path, "robotics_sim", approved=True
    )

    assert stages_path == verticals_root / "robotics_sim" / "stages.py"
    assert stages_path.is_file()
    assert (stages_path.parent / "__init__.py").is_file()
    assert committed == [stages_path.parent / "__init__.py", stages_path]
