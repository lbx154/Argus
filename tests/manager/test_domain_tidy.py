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
from argus_skill.skills.vertical_select import persist_vertical
from argus_skill.verticals import _data_domain as dd


def _seed_proven_domain(tmp_path):
    dd.write_data_domain(tmp_path, "robotics_sim", stages=["scope", "simulate", "measure", "report"])
    persist_vertical(tmp_path, "robotics_sim")
    cs.apply_checklist_ops(tmp_path, [
        {"op": "add", "stage": "scope", "id": "scope.obj", "statement": "state the objective", "evidence_hint": "scope/OBJ.md"},
        {"op": "add", "stage": "simulate", "id": "simulate.seeds", "statement": "run >=3 seeds", "evidence_hint": "runs/"},
    ])
    state = tmp_path / "research" / "PIPELINE_STATE.json"
    payload = json.loads(state.read_text())
    payload.update({"current_stage": "simulate", "stages": {"scope": {"status": "done"}}})
    state.write_text(json.dumps(payload))


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
    src = src.replace("from ...skills.stage_machine", "from argus_skill.skills.stage_machine")
    mod = types.ModuleType("promoted_stages")
    exec(compile(src, "<stages>", "exec"), mod.__dict__)
    assert mod.STAGE_ORDER == ["scope", "simulate", "measure", "report"]
    assert mod.completion_gate == "none"
    assert [i.id for i in mod.CHECKLIST_ITEMS["scope"]] == ["scope.obj"]
    assert [i.id for i in mod.CHECKLIST_ITEMS["simulate"]] == ["simulate.seeds"]
    assert set(mod.STAGE_CHECKS) == set(mod.STAGE_ORDER)
    assert callable(mod.role_banner)


def test_render_preserves_seed_plus_custom_items(tmp_path):
    """REGRESSION: _render_stages_py must snapshot seed items AND custom items.

    A data domain with seed checklist items in its CHECKLIST_ITEMS, plus a
    Planner-authored custom row for the same stage, should produce a promoted
    CHECKLIST_ITEMS that contains both IDs — seeds first, then custom.

    Previously _render_stages_py called load_checklist_store(), which only
    returns raw project rows.  When a custom row existed, store.get(stage) was
    truthy and the code returned *only* the custom row, silently dropping the
    domain's seed items.  The fix is to call store_items_for_stage() instead,
    which applies seed+override+tombstone semantics before returning.
    """
    # Hand-author a domain JSON that carries a real seed item for "scope".
    domains_dir = tmp_path / "research" / "DOMAINS"
    domains_dir.mkdir(parents=True, exist_ok=True)
    domain_payload = {
        "name": "robotics_sim",
        "stages": ["scope", "simulate", "measure", "report"],
        "checklist_stage_order": ["scope", "simulate", "measure", "report"],
        "completion_gate": "none",
        "role_banner": "",
        "created_by": "manager",
        "promoted": False,
        "checklist": {
            "scope": [
                {
                    "id": "scope.seed.vision",
                    "statement": "State the scope vision",
                    "evidence_hint": "scope/VISION.md",
                }
            ]
        },
    }
    (domains_dir / "robotics_sim.json").write_text(json.dumps(domain_payload, indent=2))

    # PIPELINE_STATE: vertical = robotics_sim (so seed_items_for resolves correctly)
    # and one stage done so the domain is "proven".
    state = tmp_path / "research" / "PIPELINE_STATE.json"
    state.write_text(
        json.dumps({
            "vertical": "robotics_sim",
            "current_stage": "simulate",
            "stages": {"scope": {"status": "done"}},
        })
    )

    # Planner adds a custom row for the same "scope" stage.
    cs.apply_checklist_ops(tmp_path, [
        {
            "op": "add",
            "stage": "scope",
            "id": "scope.custom.planner",
            "statement": "Custom planner item",
            "evidence_hint": "scope/CUSTOM.md",
        }
    ])

    src = dt._render_stages_py("robotics_sim", tmp_path)
    src = src.replace("from ...skills.stage_machine", "from argus_skill.skills.stage_machine")
    mod = types.ModuleType("promoted_stages_seed_test")
    exec(compile(src, "<stages>", "exec"), mod.__dict__)

    scope_ids = [i.id for i in mod.CHECKLIST_ITEMS["scope"]]
    assert "scope.seed.vision" in scope_ids, "seed item was dropped during promotion"
    assert "scope.custom.planner" in scope_ids, "custom item was dropped during promotion"
    assert scope_ids.index("scope.seed.vision") < scope_ids.index("scope.custom.planner"), (
        "seed items should precede custom items in the promoted snapshot"
    )


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
