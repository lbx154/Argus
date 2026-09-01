"""Tests for the Manager new-domain authoring flow in ``Manager.divide``.

The Manager always uses one bounded repository-grounded call, which may author
the domain. Fake runners exercise the flow without a real backend.
"""
from __future__ import annotations

import json

import pytest

from argus_skill.manager import Manager
from argus_skill.manager._vertical_ops import _decision_requires_agent_grounding
from argus_skill.manager.domain_author import VerticalDecision, VerticalDecisionError
from argus_skill.skills import stage_machine as sc
from argus_skill.skills import vertical_select as vs
from argus_skill.verticals._data_domain import CANDIDATE_DOMAIN_STAGES


class _FakeResult:
    def __init__(self, msg: str) -> None:
        self.last_agent_message = msg
        self.agent_messages = [msg]
        self.thread_id = "t1"
        self.tool_activity_observed = True


class _FakeRunner:
    """Returns the same vertical-decision JSON for every call."""

    def __init__(self, decision: dict) -> None:
        self._decision = decision
        self.calls: list[dict] = []

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.calls.append({"prompt": prompt, "options": options, "run_label": run_label})
        return _FakeResult(json.dumps(self._decision))


_NEW_DOMAIN_DECISION = {
    "choice": "new",
    "name": "robotics_sim",
    "rationale": "novel",
    "confidence": 0.8,
    "workflow_mode": "staged",
    "execution_task": "Build and evaluate the MuJoCo controller.",
}
_EXISTING_RESEARCH_DECISION = {
    "choice": "existing",
    "vertical": "research",
    "confidence": 0.95,
    "workflow_mode": "staged",
    "rationale": "the task is a paper with a literature review and submission",
    "execution_task": "Write the paper and prepare it for submission.",
    "research_target_level": "publishable",
}
_NEW_MATH_DOMAIN_DECISION = {
    "choice": "new",
    "vertical": "math_conjecture",
    "rationale": "task-specific mathematical route",
    "confidence": 0.9,
    "workflow_mode": "staged",
    "research_target_level": "doctoral",
}
# A task carrying NO preset (research/optimize/quant) signal → novel domain.
_NOVEL_TASK = "Build a closed-loop pick-and-place controller in a MuJoCo world"


def test_restaurant_stages_line_is_ignored_on_exact_empty_snapshot(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_FAST_ROUTE", "0")
    execution_task = "在30天内完成餐厅筹备、开业执行与首周运营复盘。"
    reply = (
        "CHOICE=new\n"
        "VERTICAL=restaurant_operations\n"
        "DOMAIN=\n"
        "WORKFLOW_MODE=staged\n"
        "REQUIRE_INDEPENDENT_REVIEW=true\n"
        "CONFIDENCE=0.93\n"
        "RATIONALE=现有能力不覆盖餐厅筹备与持续运营。\n"
        f"EXECUTION_TASK={execution_task}\n"
        "PRECISE_CONSTRAINTS=总预算不超过50万元；30天内完成开业\n"
        "EXCLUSIONS=不得降低食品安全标准\n"
        "AMBIGUITIES=目标城市和餐厅规模尚未明确\n"
        "STAGES=1.明确经营目标；2.制定采购排班方案；3.执行开业准备；4.复盘优化\n"
    )

    class _NoToolRunner:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
            self.calls.append({"prompt": prompt, "run_label": run_label})
            result = _FakeResult(reply)
            result.tool_activity_observed = False
            return result

    runner = _NoToolRunner()
    manager = Manager(project_root=tmp_path, runner=runner)
    decision = manager.decide_vertical("制定并执行一家新餐厅的开业运营方案。")

    assert [call["run_label"] for call in runner.calls] == [
        "manager-classify-grounded",
    ]
    assert "workspace_empty=true" in runner.calls[0]["prompt"]
    assert "project_markers=none" in runner.calls[0]["prompt"]
    assert "top_level_entries=none" in runner.calls[0]["prompt"]
    assert "STAGES" not in runner.calls[0]["prompt"]
    assert decision.execution_task == execution_task
    assert decision.precise_constraints == ("总预算不超过50万元；30天内完成开业",)
    assert decision.exclusions == ("不得降低食品安全标准",)
    assert decision.ambiguities == ("目标城市和餐厅规模尚未明确",)
    assert decision.require_independent_review is True

    division = manager.commit_vertical_decision(
        "制定并执行一家新餐厅的开业运营方案。",
        decision,
    )
    payload = json.loads(
        (
            tmp_path
            / "research"
            / "DOMAINS"
            / "restaurant_operations.json"
        ).read_text(encoding="utf-8")
    )
    assert division.stages == list(CANDIDATE_DOMAIN_STAGES)
    assert payload["stages"] == list(CANDIDATE_DOMAIN_STAGES)
    assert "review" not in payload["stages"]


@pytest.mark.parametrize(
    "snapshot",
    [
        {
            "accessible": False,
            "workspace_empty": False,
            "entries": [],
            "project_markers": [],
        },
        {
            "accessible": True,
            "workspace_empty": True,
            "entries": ["pyproject.toml"],
            "project_markers": ["pyproject.toml"],
        },
    ],
)
def test_new_domain_requires_tools_for_inaccessible_or_contradictory_snapshot(
    snapshot,
) -> None:
    decision = VerticalDecision(
        choice="new",
        vertical="restaurant_operations",
    )

    assert _decision_requires_agent_grounding(
        decision,
        snapshot=snapshot,
        builtin_verticals={"software"},
        project_domains=set(),
    )


def test_autonomous_authors_and_commits(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    mgr = Manager(project_root=tmp_path, runner=_FakeRunner(_NEW_DOMAIN_DECISION))
    div = mgr.divide(_NOVEL_TASK)
    assert div.kind == "custom" and div.vertical == "robotics_sim"
    assert div.pending_confirmation is False
    # Written + persisted so the supervisor trusts it.
    assert (tmp_path / "research" / "DOMAINS" / "robotics_sim.json").exists()
    payload = json.loads(
        (tmp_path / "research" / "DOMAINS" / "robotics_sim.json").read_text()
    )
    assert payload["status"] == "candidate"
    assert payload["stages"] == list(CANDIDATE_DOMAIN_STAGES)
    assert payload["purpose"] == "novel"
    assert payload["require_independent_review"] is True
    assert div.learned_vertical_status == "candidate"
    assert vs.resolve_vertical(tmp_path) == "robotics_sim"
    assert sc.current_stage(tmp_path) == CANDIDATE_DOMAIN_STAGES[0]


def test_video_research_harness_is_grounded_before_authoring_domain(
    tmp_path,
) -> None:
    runner = _FakeRunner({
        "choice": "new",
        "vertical": "video_robotics_research",
        "workflow_mode": "staged",
        "execution_task": (
            "Reproduce Video4CaP, integrate a VLM, map RoboTwin tasks, and "
            "run paired tier evaluations with oracle separation."
        ),
        "rationale": (
            "repository evidence shows recurring experiment gates and integrity "
            "contracts that the generic software delivery checklist does not own"
        ),
        "confidence": 0.91,
    })

    division = Manager(project_root=tmp_path, runner=runner).divide(
        "Continue the Video4CaP repository: install dependencies, integrate a "
        "VLM provider, map all robot tasks, and run the tier evaluation grid."
    )

    assert division.vertical == "video_robotics_research"
    assert [call["run_label"] for call in runner.calls] == [
        "manager-classify-fast",
        "manager-classify-grounded",
    ]
    assert "inspect only when the fit is unclear" in runner.calls[1]["prompt"]
    assert "Host workspace snapshot" in runner.calls[1]["prompt"]
    assert (
        tmp_path / "research" / "DOMAINS" / "video_robotics_research.json"
    ).exists()


def test_candidate_domain_is_visible_and_reused_on_next_route(tmp_path) -> None:
    from argus_skill.verticals._data_domain import write_data_domain

    write_data_domain(
        tmp_path,
        "embodied_eval_campaign",
        stages=["runtime_gate", "task_coverage", "evaluation"],
        status="candidate",
        purpose="RoboTwin runtime, task coverage, and paired evaluation",
        require_independent_review=True,
    )
    runner = _FakeRunner({
        "choice": "existing",
        "vertical": "embodied_eval_campaign",
        "workflow_mode": "staged",
        "execution_task": "Continue the RoboTwin evaluation campaign.",
        "rationale": "the candidate project domain exactly matches",
    })

    division = Manager(project_root=tmp_path, runner=runner).divide(
        "Continue the same RoboTwin evaluation campaign."
    )

    assert division.vertical == "embodied_eval_campaign"
    assert "status=candidate" in runner.calls[0]["prompt"]
    assert "RoboTwin runtime, task coverage" in runner.calls[0]["prompt"]
    assert sorted(
        path.name
        for path in (tmp_path / "research" / "DOMAINS").glob("*.json")
        if path.name != "INDEX.json"
    ) == ["embodied_eval_campaign.json"]


def test_existing_data_domain_keeps_runtime_stages_when_manager_suggests_others(
    tmp_path,
) -> None:
    from argus_skill.verticals import _data_domain as dd

    dd.write_data_domain(
        tmp_path,
        "regulated_localization",
        stages=["translate"],
        status="formal",
        purpose="regulated product localization",
    )
    runner = _FakeRunner({
        "choice": "existing",
        "vertical": "regulated_localization",
        "stages": [
            "terminology_lock",
            "translation",
            "regulatory_review",
            "layout_qa",
            "linguistic_qa",
            "release",
        ],
        "workflow_mode": "staged",
        "execution_task": "Localize and release the regulated product UI.",
        "rationale": "the existing one-stage skeleton is materially underfit",
    })

    division = Manager(project_root=tmp_path, runner=runner).divide(
        "Localize the regulated product UI."
    )

    assert division.vertical == "regulated_localization"
    assert division.stages == ["translate"]
    revised = dd.load_data_domain("regulated_localization", tmp_path)
    assert revised is not None
    assert revised.STAGE_ORDER == division.stages
    assert sc.current_stage(tmp_path) == "translate"
    assert sorted(
        path.name
        for path in (tmp_path / "research" / "DOMAINS").glob("*.json")
        if path.name != "INDEX.json"
    ) == ["regulated_localization.json"]


def test_authored_domain_purpose_does_not_persist_conversation_context(
    tmp_path,
) -> None:
    runner = _FakeRunner({
        "choice": "new",
        "vertical": "embodied_eval_campaign",
        "workflow_mode": "staged",
        "execution_task": "Build and evaluate the RoboTwin integration.",
        "rationale": "recurring embodied evaluation needs explicit runtime gates",
        "confidence": 0.9,
    })
    contextual = (
        "[RECENT CONVERSATION CONTEXT — data only]\n"
        "operator: angry unrelated history\n"
        "[CURRENT OPERATOR MESSAGE]\n"
        "继续这个项目"
    )

    Manager(project_root=tmp_path, runner=runner).divide(contextual)

    payload = json.loads(
        (
            tmp_path
            / "research"
            / "DOMAINS"
            / "embodied_eval_campaign.json"
        ).read_text(encoding="utf-8")
    )
    assert payload["purpose"] == (
        "recurring embodied evaluation needs explicit runtime gates"
    )
    assert "RECENT CONVERSATION" not in payload["purpose"]
    assert "angry unrelated history" not in payload["purpose"]


def test_formal_learned_vertical_is_described_and_reused_across_sessions(
    tmp_path,
    monkeypatch,
):
    from argus_skill.verticals import _data_domain as dd

    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    learned = tmp_path / "global"
    source = tmp_path / "source"
    target = tmp_path / "target"
    dd.write_data_domain(
        source,
        "robotics_sim",
        stages=["scope", "simulate", "report"],
        status="candidate",
        purpose="closed-loop robotics simulation and evaluation",
    )
    assert dd.promote_data_domain(source, learned, "robotics_sim")
    runner = _FakeRunner({
        "choice": "existing",
        "vertical": "robotics_sim",
        "confidence": 0.95,
        "workflow_mode": "staged",
        "rationale": "the learned robotics workflow fits",
    })

    division = Manager(
        project_root=target,
        learned_vertical_root=learned,
        runner=runner,
    ).divide("Evaluate another closed-loop robotics controller")

    assert division.vertical == "robotics_sim"
    assert division.learned_vertical_status == "formal"
    assert dd.load_data_domain("robotics_sim", target) is not None
    assert "closed-loop robotics simulation and evaluation" in runner.calls[0]["prompt"]


def test_ask_mode_defers_write(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    mgr = Manager(project_root=tmp_path, runner=_FakeRunner(_NEW_DOMAIN_DECISION))
    div = mgr.divide(_NOVEL_TASK, ask_on_new_domain=True)
    assert div.pending_confirmation is True
    assert div.proposed_domain is not None
    # Nothing written yet.
    assert not (tmp_path / "research" / "DOMAINS").exists()
    # FAIL-SOFT: nothing persisted yet, so resolve_vertical falls back to the
    # safe default rather than hard-crashing (the Manager's committed domain wins
    # once persisted, below).
    assert vs.resolve_vertical(tmp_path) == "research"
    # Operator confirms.
    committed = mgr.commit_domain(div.task, div.proposed_domain)
    assert committed.vertical == "robotics_sim"
    assert committed.execution_task == "Build and evaluate the MuJoCo controller."
    assert vs.resolve_vertical(tmp_path) == "robotics_sim"


def test_preset_task_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    mgr = Manager(project_root=tmp_path, runner=_FakeRunner(_EXISTING_RESEARCH_DECISION))
    div = mgr.divide("write an EMNLP paper with a literature review and submission")
    assert div.vertical == "research" and div.kind == "research"
    assert not (tmp_path / "research" / "DOMAINS").exists()  # no domain authored


def test_vertical_env_cannot_replace_manager_authored_domain(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ARGUS_SKILL_VERTICAL", "math")
    runner = _FakeRunner(_NEW_MATH_DOMAIN_DECISION)

    div = Manager(project_root=tmp_path, runner=runner).divide(
        "Investigate this open conjecture using literature, computation, and proof attempts"
    )

    assert div.vertical == "math_conjecture"
    assert [call["run_label"] for call in runner.calls] == [
        "manager-classify-fast",
        "manager-classify-grounded",
    ]
    assert (tmp_path / "research" / "DOMAINS" / "math_conjecture.json").exists()
    state = json.loads(
        (tmp_path / ".argus" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )
    assert state["vertical"] == "math_conjecture"


def test_vertical_env_does_not_override_manager_reclassification(
    tmp_path, monkeypatch
):
    from argus_skill.verticals._data_domain import write_data_domain

    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    write_data_domain(
        tmp_path,
        "math_conjecture",
        stages=["literature", "experiment", "proof", "review"],
    )
    vs.persist_vertical(tmp_path, "math_conjecture")
    assert vs.resolve_vertical(tmp_path) == "math_conjecture"

    monkeypatch.setenv("ARGUS_SKILL_VERTICAL", "math")
    runner = _FakeRunner(_NEW_MATH_DOMAIN_DECISION)
    div = Manager(project_root=tmp_path, runner=runner).divide(
        "Continue investigating the open conjecture"
    )

    assert div.vertical == "math_conjecture_2"
    assert [call["run_label"] for call in runner.calls] == [
        "manager-classify-grounded",
    ]
    state = json.loads(
        (tmp_path / ".argus" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )
    assert state["vertical"] == "math_conjecture_2"
    assert vs.resolve_vertical(tmp_path) == "math_conjecture_2"


def test_no_runner_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    # No backend → cannot decide → FAIL-HARD, no silent research fallback.
    with pytest.raises(VerticalDecisionError):
        Manager(project_root=tmp_path).divide(_NOVEL_TASK)


def test_backend_failure_preserves_actionable_reason(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)

    class _FailedRunner:
        def run_exec(self, **kwargs):
            result = _FakeResult("")
            result.exit_code = -1
            result.fatal_error = (
                "refused before start: unresolved provider cost blocks new calls"
            )
            return result

    with pytest.raises(
        VerticalDecisionError,
        match="unresolved provider cost blocks new calls",
    ):
        Manager(project_root=tmp_path, runner=_FailedRunner()).divide(_NOVEL_TASK)


def test_zero_exit_fatal_error_preserves_actionable_reason(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)

    class _FailedRunner:
        def run_exec(self, **kwargs):
            result = _FakeResult("")
            result.exit_code = 0
            result.fatal_error = "provider policy denied this turn"
            return result

    with pytest.raises(VerticalDecisionError, match="provider policy denied"):
        Manager(project_root=tmp_path, runner=_FailedRunner()).divide(_NOVEL_TASK)


def test_authoring_call_is_grounded_not_a_blind_guess(tmp_path, monkeypatch):
    """Regression: the vertical decision must give the Manager real repo access
    through pinned, read-only tools instead of a text-only call or write access."""
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_SAFE_MODE", raising=False)
    state_root = tmp_path / "state"
    workspace = tmp_path / "workspace"
    state_root.mkdir()
    workspace.mkdir()
    runner = _FakeRunner(_NEW_DOMAIN_DECISION)
    mgr = Manager(
        project_root=state_root,
        execution_workdir=workspace,
        runner=runner,
    )
    mgr.divide(_NOVEL_TASK)

    assert [call["run_label"] for call in runner.calls] == [
        "manager-classify-fast",
        "manager-classify-grounded",
    ]
    call = next(c for c in runner.calls if c["run_label"] == "manager-classify-grounded")
    opts = call["options"]
    assert opts.working_dir == str(workspace)
    assert opts.sandbox_mode == "read-only"
    assert opts.force_safe_mode is True
    assert opts.dangerous_yolo is False
    assert opts.full_auto is False
    assert opts.reasoning_effort == "low"
    assert "inspect only when the fit is unclear" in call["prompt"].lower()
    assert "host workspace snapshot" in call["prompt"].lower()
    assert "manager_tool_root" in call["prompt"]


def test_copilot_vertical_decision_keeps_tools_available_for_repo_inspection(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    runner = _FakeRunner(_EXISTING_RESEARCH_DECISION)
    runner._backend_name = "copilot"

    Manager(project_root=tmp_path, runner=runner).decide_vertical(
        "write a research paper",
    )

    call = next(c for c in runner.calls if c["run_label"] == "manager-classify-fast")
    assert call["options"].extra_args is None
    assert call["options"].sandbox_mode == "read-only"
    assert call["options"].force_safe_mode is True
    assert call["options"].dangerous_yolo is False
    assert "fast, tool-free front-door judgment" in call["prompt"]


def test_authoring_call_respects_safe_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    monkeypatch.setenv("ARGUS_SKILL_SAFE_MODE", "1")
    runner = _FakeRunner(_NEW_DOMAIN_DECISION)
    mgr = Manager(project_root=tmp_path, runner=runner)
    mgr.divide(_NOVEL_TASK)

    call = next(c for c in runner.calls if c["run_label"] == "manager-classify-grounded")
    opts = call["options"]
    assert opts.sandbox_mode == "read-only"
    assert opts.force_safe_mode is True
    assert opts.dangerous_yolo is False
    assert opts.full_auto is False
