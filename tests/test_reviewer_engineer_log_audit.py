"""Reviewer engineer execution-log audit (process-correctness review).

Operator directive (2026-06-26): the reviewer runs in the project work-tree and
only receives the engineer's final summary, so it can verify that the OUTCOME
traces to the checklist but NOT that the PROCESS that produced it was honest
(no hardcoded answer, no skipped step, no cheat method, no method contradiction).
The supervisor threads the absolute path to this mission's execution log to
``reviewer.evaluate`` as a one-line fallback pointer after the Engineer's own
account, avoiding routine re-derivation.

Pinned here:
  * non-empty engineer_log_path -> prompt contains the audit section + the path
  * empty engineer_log_path     -> prompt is byte-for-byte the legacy prompt
                                   (back-compat: memory backend / tests / no life_dir)
  * the path threads structurally: SupervisedConfig.engineer_log_path is what
    the supervised loop hands reviewer.evaluate(...)
  * the engineer-process-audit reviewer skill exists in the reviewer pool
"""
from __future__ import annotations

from pathlib import Path

from argus.core.models import ReviewDecision, RunnerResult
from argus.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
)
from argus.reviewer import Reviewer, ReviewerConfig
from argus.roles.task_contract import EXPERIMENT_EVIDENCE_CONTRACT
from tests.hand_off import hand_off, submit

_LOG_PATH = "/abs/global/projects/deadbeef/events.jsonl"


def _build(
    path: str,
    *,
    call_id: str = "",
    scope: str = "",
    main_error: str | None = None,
    objective: str = "implement and verify the kernel",
) -> str:
    r = Reviewer(runner=None, skill_store=None)
    return r._build_prompt(
        objective=objective,
        operator_messages=[],
        planner_review_instruction="",
        round_index=4,
        session_id=None,
        main_summary="HANDOFF: did X. artifact at out.json",
        main_error=main_error,
        prior_checkpoint={},
        engineer_log_path=path,
        engineer_call_id=call_id,
        scope=scope,
    )


def test_engineer_account_is_labeled_and_capped_at_6000_chars() -> None:
    reviewer = Reviewer(runner=None, skill_store=None)
    prompt = reviewer._build_prompt(
        objective="implement and verify the kernel",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="HEAD:" + "m" * 6990 + ":TAIL",
        main_error=None,
    )

    account = prompt.split("## Engineer's account of this round\n", 1)[1].split(
        "\n\nOperator messages:\n", 1
    )[0]
    assert account.startswith("HEAD:") and account.endswith(":TAIL")
    assert "…[middle omitted 1100 characters]…" in account and len(account) <= 6000


def test_an_audit_objective_reads_the_record_rule_once_from_the_evidence_contract() -> None:
    # Words like "audit" or "ledger" used to add a section repeating it.
    p = _build(
        "",
        objective="Review an append-only audit ledger and exact COMMAND_LOG.",
    )

    assert p.count(EXPERIMENT_EVIDENCE_CONTRACT) == 1
    assert "a reconstructed or overwritten record is never the contemporaneous one" in p
    assert "## Integrity of the historical record" not in p


def test_empty_path_is_byte_for_byte_legacy_prompt() -> None:
    # Back-compat: passing "" must produce exactly the same prompt as omitting
    # the new kwarg entirely (the memory backend / tests / no-life_dir case).
    r = Reviewer(runner=None, skill_store=None)
    common = dict(
        objective="implement and verify the kernel",
        operator_messages=[],
        planner_review_instruction="",
        round_index=4,
        session_id=None,
        main_summary="HANDOFF: did X. artifact at out.json",
        main_error=None,
        prior_checkpoint={},
    )
    assert r._build_prompt(engineer_log_path="", **common) == r._build_prompt(**common)


def test_final_submission_uses_on_demand_process_audit() -> None:
    p = _build(
        _LOG_PATH,
        scope="final_submission",
    )
    assert "The event log is at" in p
    assert "if the Engineer's account seems wrong" in p
    assert "use_attach" not in p


class _CapturingReviewer:
    """Captures the engineer_log_path the supervised loop hands evaluate()."""

    def __init__(self) -> None:
        self.seen_log_path: str | None = None
        self.seen_call_id: str | None = None
        self.seen_round_max: int | None = None

    def evaluate(self, **kwargs) -> ReviewDecision:
        self.seen_log_path = kwargs.get("engineer_log_path")
        self.seen_call_id = kwargs.get("engineer_call_id")
        self.seen_round_max = kwargs.get("round_max")
        return ReviewDecision(
            status="done",
            reason="ok",
            next_action="",
        )


class _UncorrelatedEngineerRunner:
    def run_exec(self, **kwargs):
        submit(kwargs["options"], hand_off("reviewer", "implemented without a backend audit log"))
        return RunnerResult(
            exit_code=0,
            agent_messages=["implemented without a backend audit log"],
            thread_id="t2",
        )


def test_gateway_synthesized_call_id_uses_legacy_unscoped_audit(
    tmp_path: Path,
) -> None:
    log_path = str(tmp_path / "global" / "projects" / "deadbeef" / "events.jsonl")
    reviewer = _CapturingReviewer()
    engine = SupervisedEngineer(
        engineer_runner=_UncorrelatedEngineerRunner(),
        reviewer=reviewer,
        engineer_config=EngineerConfig(model="gpt-5.5"),
        reviewer_config=ReviewerConfig(model="gpt-5.5"),
    )
    config = SupervisedConfig(
        max_rounds=1,
        engineer_log_path=log_path,
    )

    engine.run(
        objective="implement the increment",
        engineer_prompt_builder=lambda _next_action, _include_static=True: "do it",
        supervised_config=config,
        workdir=tmp_path,
        on_event=lambda _e: None,
    )

    assert reviewer.seen_call_id == ""


# --------------------------------------------------------------------------- #
# The reviewer skill exists in the reviewer pool
# --------------------------------------------------------------------------- #
def test_engineer_process_audit_skill_exists_in_reviewer_pool() -> None:
    from argus.skills.builtins import iter_builtin_skill_texts

    names = {fn for fn, _ in iter_builtin_skill_texts()}
    assert "reviewer/engineer-process-audit.md" in names

    from argus.skills.missions import ReviewerMission

    # must NOT be hard-excluded, or the matcher could never surface it
    assert "engineer-process-audit.md" not in ReviewerMission.default_exclude
