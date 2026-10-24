from __future__ import annotations

from types import ModuleType, SimpleNamespace

import pytest

from argus.core.pipeline_state import read_pipeline_state, write_pipeline_state
from argus.core.vertical_contract import VerticalContractError, vertical_contract
from argus.manager import Manager
from argus.manager.domain_author import VerticalDecision, VerticalDecisionError
from argus.skills.stage_machine import (
    ChecklistItem,
    StageCompletionError,
    advance_stage,
    complete_final_stage,
    format_full_pipeline_checklist,
)
from argus.skills.vertical_select import (
    available_vertical_purposes,
    persist_vertical,
    vertical_completion_certificate_status,
)
from argus.verticals import _registry
from argus.verticals._base import load_vertical, load_vertical_contract


@pytest.fixture
def provider(monkeypatch):
    module = ModuleType("profile_lab.stages")
    module.ARGUS_VERTICAL_API_VERSION = 1
    module.VERTICAL_PURPOSE = "A scoped test capability"
    module.STAGE_ORDER = module.CHECKLIST_STAGE_ORDER = ("design", "verify", "deliver")
    module.CHECKLIST_ITEMS = {
        stage: (ChecklistItem(f"{stage}.evidence", f"{stage} evidence", f"{stage}.txt"),)
        for stage in module.STAGE_ORDER
    }
    module.completion_gate = "none"
    module.PROTECTED_ITEM_IDS = ("verify.evidence",)
    module.WORKFLOW_PROFILES = {
        "build": {"purpose": "design and verify only", "stages": ("design", "verify")},
        "verification": {"purpose": "existing design verification", "stages": ("verify",)},
        "full": {"purpose": "complete delivery", "stages": module.STAGE_ORDER},
    }

    def check(stage, root, *, state_root=None, workflow_profile="full"):
        assert state_root is not None
        assert workflow_profile in {*module.WORKFLOW_PROFILES, "custom"}
        return () if (root / f"{stage}.txt").is_file() else (f"missing {stage} evidence",)

    module.stage_completion_issues = check
    entry = SimpleNamespace(name="profile_lab", value="profile_lab.stages", load=lambda: module)
    monkeypatch.setattr(_registry, "entry_points", lambda group: [entry])
    _registry.refresh_vertical_plugins()
    yield module
    _registry.refresh_vertical_plugins()


@pytest.mark.parametrize("profile, expected", [
    (None, ("design", "verify", "deliver")),
    ("full", ("design", "verify", "deliver")),
    ("build", ("design", "verify")),
    ("verification", ("verify",)),
])
def test_profile_persists_and_drives_every_contract_view(provider, tmp_path, profile, expected):
    persist_vertical(tmp_path, "profile_lab", workflow_profile=profile)
    state = read_pipeline_state(tmp_path)
    contract = load_vertical_contract("profile_lab", tmp_path)
    assert contract.stage_order == expected
    assert state["current_stage"] == expected[0]
    assert Manager(project_root=tmp_path).plan_stages("profile_lab") == list(expected)
    module = load_vertical("profile_lab", tmp_path)
    assert module.STAGE_ORDER == expected
    assert module.PROTECTED_ITEM_IDS == provider.PROTECTED_ITEM_IDS
    assert vertical_contract("profile_lab", module).stage_order == expected
    rendered = format_full_pipeline_checklist(project_root=tmp_path)
    assert "verify.evidence" in rendered
    if profile and profile != "full":
        assert "deliver.evidence" not in rendered
    assert provider.STAGE_ORDER == ("design", "verify", "deliver")
    if profile:
        assert state["workflow_stages"] == list(expected)
        assert "ACTIVE WORKFLOW PROFILE" in contract.banner("engineer")
    else:
        assert "workflow_profile" not in state


def test_profile_snapshot_cannot_silently_change(provider, tmp_path):
    persist_vertical(tmp_path, "profile_lab", workflow_profile="build")
    provider.WORKFLOW_PROFILES["build"]["stages"] = ("verify",)
    with pytest.raises(VerticalContractError, match="changed since selection"):
        load_vertical_contract("profile_lab", tmp_path)
    with pytest.raises(ValueError, match="changed since selection"):
        persist_vertical(tmp_path, "profile_lab")
    persist_vertical(
        tmp_path, "profile_lab", workflow_profile="build", allow_workflow_profile_change=True,
    )
    assert load_vertical_contract("profile_lab", tmp_path).stage_order == ("verify",)


def test_selected_stages_cannot_be_skipped_or_completed_early(provider, tmp_path):
    persist_vertical(tmp_path, "profile_lab", workflow_profile="full")
    (tmp_path / "design.txt").write_text("accepted design")
    with pytest.raises(ValueError, match="cannot skip"):
        advance_stage(tmp_path, target_stage="deliver", reason="omit verification")
    state = read_pipeline_state(tmp_path)
    state["workflow_mode"] = "direct"
    write_pipeline_state(tmp_path, state)
    with pytest.raises(ValueError, match="not the final stage"):
        complete_final_stage(tmp_path, reason="early", allow_early_completion=True)


def test_completion_uses_evidence_root_and_certifies_selected_final_stage(provider, tmp_path):
    state_root, evidence = tmp_path / "state", tmp_path / "work"
    evidence.mkdir()
    persist_vertical(state_root, "profile_lab", workflow_profile="build")
    with pytest.raises(StageCompletionError, match="missing design"):
        advance_stage(state_root, target_stage="verify", reason="reviewed", evidence_root=evidence)
    (evidence / "design.txt").write_text("accepted design")
    advance_stage(state_root, target_stage="verify", reason="reviewed", evidence_root=evidence)
    with pytest.raises(StageCompletionError, match="missing verify"):
        complete_final_stage(state_root, reason="reviewed", evidence_root=evidence)
    (evidence / "verify.txt").write_text("accepted verification")
    complete_final_stage(state_root, reason="reviewed", evidence_root=evidence)
    assert vertical_completion_certificate_status(state_root, "profile_lab")["ok"]
    state = read_pipeline_state(state_root)
    assert set(state["stages"]) == {"design", "verify"}
    state["stages"]["design"]["status"] = "skipped"
    write_pipeline_state(state_root, state)
    assert not vertical_completion_certificate_status(state_root, "profile_lab")["ok"]
    assert not (evidence / ".argus" / "PIPELINE_STATE.json").exists()


def test_commit_rejects_unknown_profile_even_without_model(provider, tmp_path):
    with pytest.raises(VerticalContractError, match="no workflow profile"):
        Manager(project_root=tmp_path).commit_vertical_decision(
            "work", VerticalDecision(choice="existing", vertical="profile_lab", workflow_profile="bogus"),
        )
    assert not read_pipeline_state(tmp_path)


@pytest.mark.parametrize("mode", ["direct", "invalid"])
def test_profile_rejects_incompatible_execution_mode(provider, tmp_path, mode):
    with pytest.raises(ValueError, match="requires workflow_mode"):
        persist_vertical(tmp_path, "profile_lab", workflow_profile="build", workflow_mode=mode)
    assert not read_pipeline_state(tmp_path)


def test_new_commit_cannot_omit_profile(provider, tmp_path):
    with pytest.raises(VerticalDecisionError, match="requires a workflow_profile"):
        Manager(project_root=tmp_path).commit_vertical_decision(
            "work", VerticalDecision(choice="existing", vertical="profile_lab"),
        )
    assert not read_pipeline_state(tmp_path)


@pytest.mark.parametrize("invalid", [
    {"short": {"purpose": "x", "stages": ("design",)}},
    {"full": {"purpose": "x", "stages": ("verify", "design", "deliver")}},
    {"full": {"purpose": "", "stages": ("design", "verify", "deliver")}},
    {"full": {"purpose": "x", "stages": ("design", "verify", "deliver")}, "bad": {"purpose": "x", "stages": ()}},
    {"full": {"purpose": "x", "stages": ("design", "verify", "deliver")}, "bad": {"purpose": "x", "stages": ("absent",)}},
    {"full": {"purpose": "x", "stages": ("design", "verify", "deliver")}, "bad": {"purpose": "x", "stages": ("design", "design")}},
])
def test_invalid_provider_profiles_fail_contract_validation(provider, invalid):
    provider.WORKFLOW_PROFILES = invalid
    with pytest.raises(VerticalContractError, match="profile"):
        vertical_contract("profile_lab", provider)


@pytest.fixture
def composable_provider(provider):
    provider.WORKFLOW_STAGE_REQUIREMENTS = {
        "design": ("verify",),
        "verify": (),
        "deliver": ("design",),
    }
    return provider


@pytest.mark.parametrize("requested, expected", [
    (("design",), ("design", "verify")),
    (("verify",), ("verify",)),
    (("deliver",), ("design", "verify", "deliver")),
    (("verify", "design"), ("design", "verify")),
])
def test_custom_composition_closes_requirements_in_canonical_order(composable_provider, tmp_path, requested, expected):
    persist_vertical(
        tmp_path, "profile_lab", workflow_profile="custom", workflow_requested_stages=requested,
    )
    contract = load_vertical_contract("profile_lab", tmp_path)
    assert contract.stage_order == expected
    assert contract.workflow_requested_stages == tuple(
        stage for stage in composable_provider.STAGE_ORDER if stage in requested
    )
    assert "Requested:" in contract.workflow_summary()
    assert "Outside scope:" in contract.workflow_summary()
    assert "mandatory companions" in available_vertical_purposes()["profile_lab"]
    assert read_pipeline_state(tmp_path)["workflow_stages"] == list(expected)


@pytest.mark.parametrize("requested", [(), [], "design", ("unknown",), ("design", "design"), [None]])
def test_invalid_custom_requests_leave_no_state(composable_provider, tmp_path, requested):
    with pytest.raises(VerticalContractError, match="requested stages"):
        persist_vertical(
            tmp_path, "profile_lab", workflow_profile="custom", workflow_requested_stages=requested,
        )
    assert not read_pipeline_state(tmp_path)


def test_custom_requests_need_provider_opt_in_and_cannot_change_named_profiles(provider, tmp_path):
    with pytest.raises(VerticalContractError, match="does not support"):
        persist_vertical(tmp_path, "profile_lab", workflow_profile="custom", workflow_requested_stages=("design",))
    with pytest.raises(VerticalContractError, match="require workflow_profile"):
        persist_vertical(tmp_path, "profile_lab", workflow_profile="build", workflow_requested_stages=("verify",))


def test_custom_replacement_resets_only_at_operator_boundary(composable_provider, tmp_path):
    persist_vertical(tmp_path, "profile_lab", workflow_profile="custom", workflow_requested_stages=("verify",))
    decision = VerticalDecision(
        choice="existing", vertical="profile_lab", workflow_profile="custom",
        workflow_requested_stages=("design",),
    )
    manager = Manager(project_root=tmp_path)
    with pytest.raises(ValueError, match="active task"):
        manager.commit_vertical_decision("Now create it.", decision)
    manager.commit_vertical_decision("Now create it.", decision, force_stage_reset=True)
    assert read_pipeline_state(tmp_path)["current_stage"] == "design"
    assert read_pipeline_state(tmp_path)["workflow_stages"] == ["design", "verify"]


def test_scoped_contract_cannot_silently_drop_checklists_when_reselected(composable_provider):
    contract = vertical_contract("profile_lab", composable_provider).compose_workflow(("verify",))
    with pytest.raises(VerticalContractError, match="unscoped"):
        contract.for_profile("full")


@pytest.mark.parametrize("requirements", [
    {"design": ()},
    {"design": ("verify",), "verify": ("design",), "deliver": ()},
    {"design": ("design",), "verify": (), "deliver": ()},
    {"design": ("absent",), "verify": (), "deliver": ()},
    {"design": "verify", "verify": (), "deliver": ()},
    {"design": ("verify", "verify"), "verify": (), "deliver": ()},
])
def test_invalid_requirement_graphs_fail_visibly(provider, requirements):
    provider.WORKFLOW_STAGE_REQUIREMENTS = requirements
    with pytest.raises(VerticalContractError, match="requirements"):
        vertical_contract("profile_lab", provider)


def test_composition_takes_a_list_of_stages_not_an_ambiguous_string():
    from argus.manager.domain_author import RouteContext

    decide = RouteContext(known_verticals=("software",)).decide
    payload = {
        "to": "team", "summary": "a custom flow", "assumptions": [], "choice": "existing",
        "vertical": "software", "workflow_mode": "staged", "workflow_profile": "custom",
        "workflow_stages": ["rtl", "ppa"], "execution_task": "work",
    }
    assert decide(payload).workflow_requested_stages == ("rtl", "ppa")
    for invalid in ("rtl;ppa", None, 3, ["rtl", {}]):
        with pytest.raises(ValueError, match="workflow_stages"):
            decide({**payload, "workflow_stages": invalid})
