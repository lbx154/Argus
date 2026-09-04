from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.core.models import ReviewDecision
from argus_skill.core.pipeline_state import read_pipeline_state
from argus_skill.life.terminal_state import build_terminal_idle_signature
from argus_skill.reviewer._core import (
    ReviewerConfig,
    _parallel_final_review_passes,
    _persist_research_review,
)
from argus_skill.skills.stage_machine import (
    current_stage,
    migrate_legacy_research_stage,
    rollback_stage,
)
from argus_skill.skills.vertical_select import persist_vertical
from argus_skill.verticals.research.prompt_policy import (
    active_context_paths,
    render_role_prompt_fragment,
)
from argus_skill.verticals.research.stages import (
    CANONICAL_STAGE_ORDER,
    STAGE_CHECKLISTS,
    stage_completion_issues,
)


def _write_state(root: Path, stage: str, *, stages: dict | None = None) -> Path:
    path = root / ".argus" / "PIPELINE_STATE.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "vertical": "research",
            "current_stage": stage,
            "stages": stages or {},
        }),
        encoding="utf-8",
    )
    return path


def test_research_has_exactly_four_forward_stages() -> None:
    assert CANONICAL_STAGE_ORDER == (
        "idea",
        "experiment",
        "paper",
        "review",
    )
    assert tuple(STAGE_CHECKLISTS) == CANONICAL_STAGE_ORDER


@pytest.mark.parametrize(
    ("legacy", "mapped"),
    [
        ("research", "idea"),
        ("plan", "experiment"),
        ("benchmark", "experiment"),
        ("build", "experiment"),
        ("run", "experiment"),
        ("analysis", "experiment"),
        ("draft", "paper"),
        ("submission", "review"),
    ],
)
def test_pipeline_reads_are_pure_and_explicit_migration_maps_old_stages(
    tmp_path: Path,
    legacy: str,
    mapped: str,
) -> None:
    path = _write_state(
        tmp_path,
        legacy,
        stages={legacy: {"status": "done"}},
    )
    before = path.read_text(encoding="utf-8")

    assert current_stage(tmp_path) == mapped
    assert read_pipeline_state(tmp_path)["current_stage"] == legacy
    assert path.read_text(encoding="utf-8") == before

    assert migrate_legacy_research_stage(tmp_path)
    migrated = read_pipeline_state(tmp_path)
    assert migrated["current_stage"] == mapped
    assert migrated["stages"][mapped]["status"] == "in_progress"


def test_experiment_completion_requires_a_handoff(tmp_path: Path) -> None:
    assert any(
        "HANDOFF.md is missing or empty" in issue
        for issue in stage_completion_issues("experiment", tmp_path)
    )

    (tmp_path / "HANDOFF.md").write_text(
        "# HANDOFF — EXPERIMENT\n\nImplementation, evaluator, and results are ready.",
        encoding="utf-8",
    )
    assert stage_completion_issues("experiment", tmp_path) == ()


def test_review_uses_review_not_handoff_or_history(tmp_path: Path) -> None:
    (tmp_path / "HANDOFF.md").write_text("OLD HANDOFF", encoding="utf-8")
    review = tmp_path / "paper" / "REVIEW.md"
    review.parent.mkdir()
    review.write_text("CURRENT REVIEW", encoding="utf-8")
    old = tmp_path / "research" / "old-report.md"
    old.parent.mkdir()
    old.write_text("OLD REPORT", encoding="utf-8")

    prompt = render_role_prompt_fragment(
        role="reviewer",
        operation="review",
        stage="review",
        scope="final_submission",
        project_root=tmp_path,
    )

    assert active_context_paths("review") == ("paper/REVIEW.md",)
    assert "CURRENT REVIEW" in prompt
    assert "OLD HANDOFF" not in prompt
    assert "OLD REPORT" not in prompt
    assert "executed code" in prompt
    assert "raw rows" in prompt


def test_research_rollback_is_rejected(tmp_path: Path) -> None:
    _write_state(tmp_path, "paper")

    with pytest.raises(ValueError, match="forward-only"):
        rollback_stage(tmp_path, target_stage="experiment", reason="repair")


def test_terminal_signature_reads_review_from_the_workdir(tmp_path: Path) -> None:
    state = tmp_path / "state"
    workdir = tmp_path / "workdir"
    persist_vertical(state, "research")
    state_review = state / "paper" / "REVIEW.md"
    state_review.parent.mkdir()
    state_review.write_text("state review", encoding="utf-8")
    review = workdir / "paper" / "REVIEW.md"
    review.parent.mkdir(parents=True)
    review.write_text("workdir review one", encoding="utf-8")

    def signature() -> str:
        return build_terminal_idle_signature(
            objective="finish",
            stage="review",
            backlog=(),
            artifact_root=state,
            project_root=workdir,
            state_root=state,
            completion_contract=None,
        )

    first = signature()
    state_review.write_text("state review changed", encoding="utf-8")
    assert signature() == first
    review.write_text("workdir review two", encoding="utf-8")
    assert signature() != first


def test_review_persistence_uses_the_explicit_artifact_root(tmp_path: Path) -> None:
    state = tmp_path / "state"
    workdir = tmp_path / "workdir"
    maintenance = tmp_path / "maintenance"
    maintenance.mkdir()
    persist_vertical(state, "research")
    path = state / ".argus" / "PIPELINE_STATE.json"
    payload = read_pipeline_state(state)
    payload["current_stage"] = "review"
    path.write_text(json.dumps(payload), encoding="utf-8")

    _persist_research_review(
        ReviewDecision(
            status="continue",
            reason=(
                "Scientific: fail because the strongest published baseline is missing. "
                "Visual: pass. Language: pass."
            ),
            next_action="Run the baseline through the real evaluator.",
        ),
        ReviewerConfig(
            active_vertical="research",
            working_dir=str(maintenance),
            artifact_root=str(workdir),
            vertical_state_root=str(state),
        ),
    )

    review = workdir / "paper" / "REVIEW.md"
    assert review.is_file()
    assert "## Scientific, visual, and language assessment" in review.read_text(
        encoding="utf-8"
    )
    assert "## Strongest accept case" in review.read_text(encoding="utf-8")
    assert not (maintenance / "paper" / "REVIEW.md").exists()


def test_initial_final_review_runs_three_read_only_passes_in_parallel(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    persist_vertical(state, "research")
    path = state / ".argus" / "PIPELINE_STATE.json"
    payload = read_pipeline_state(state)
    payload["current_stage"] = "review"
    path.write_text(json.dumps(payload), encoding="utf-8")
    stale = workdir / "paper" / "REVIEW.md"
    stale.parent.mkdir()
    stale.write_text("stale prior review", encoding="utf-8")

    class Runner:
        def __init__(self, shared=None) -> None:
            self.shared = shared or SimpleNamespace(
                barrier=threading.Barrier(3),
                active=0,
                max_active=0,
                options=[],
                lock=threading.Lock(),
            )

        def fork(self):
            return Runner(self.shared)

        def run_exec(self, **kwargs):
            with self.shared.lock:
                self.shared.active += 1
                self.shared.max_active = max(
                    self.shared.max_active,
                    self.shared.active,
                )
                self.shared.options.append(kwargs["options"])
            self.shared.barrier.wait(timeout=2)
            with self.shared.lock:
                self.shared.active -= 1
            return SimpleNamespace(
                exit_code=0,
                fatal_error=None,
                agent_messages=[kwargs["run_label"]],
            )

    runner = Runner()
    decision = _parallel_final_review_passes(
        runner,
        ReviewerConfig(
            active_vertical="research",
            working_dir=str(workdir),
            artifact_root=str(workdir),
            vertical_state_root=str(state),
        ),
    )

    assert decision is not None
    assert decision.status == "continue"
    assert all(label in decision.reason for label in ("Scientific:", "Visual:", "Language:"))
    assert runner.shared.max_active == 3
    assert all(
        option.sandbox_mode == "read-only"
        for option in runner.shared.options
    )
    assert all(option.force_safe_mode for option in runner.shared.options)


def test_failed_parallel_review_preserves_the_provider_stop_kind(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    persist_vertical(state, "research")
    path = state / ".argus" / "PIPELINE_STATE.json"
    payload = read_pipeline_state(state)
    payload["current_stage"] = "review"
    path.write_text(json.dumps(payload), encoding="utf-8")

    class Runner:
        def fork(self):
            return Runner()

        def run_exec(self, **kwargs):
            failed = kwargs["run_label"] == "reviewer-visual"
            return SimpleNamespace(
                exit_code=1 if failed else 0,
                fatal_error="provider cooling down" if failed else None,
                stop_kind="provider_cooldown" if failed else None,
                agent_messages=[] if failed else ["pass"],
                input_tokens=2,
                cached_input_tokens=0,
                output_tokens=1,
                reasoning_output_tokens=0,
                premium_requests=0.0,
            )

    decision = _parallel_final_review_passes(
        Runner(),
        ReviewerConfig(
            active_vertical="research",
            working_dir=str(workdir),
            artifact_root=str(workdir),
            vertical_state_root=str(state),
        ),
    )

    assert decision is not None
    assert decision.status == "blocked"
    assert decision.backend_stop_kind == "provider_cooldown"
    assert decision.backend_fatal_error == "provider cooling down"
    assert decision.input_tokens == 6


def test_parallel_review_provider_exception_becomes_a_blocked_decision(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    persist_vertical(state, "research")
    path = state / ".argus" / "PIPELINE_STATE.json"
    payload = read_pipeline_state(state)
    payload["current_stage"] = "review"
    path.write_text(json.dumps(payload), encoding="utf-8")

    class ProviderError(RuntimeError):
        stop_kind = "provider_cooldown"

    class Runner:
        def fork(self):
            return Runner()

        def run_exec(self, **kwargs):
            if kwargs["run_label"] == "reviewer-visual":
                raise ProviderError("retry later")
            return SimpleNamespace(
                exit_code=0,
                fatal_error=None,
                stop_kind=None,
                agent_messages=["pass"],
                input_tokens=2,
                cached_input_tokens=0,
                output_tokens=1,
                reasoning_output_tokens=0,
                premium_requests=0.0,
            )

    decision = _parallel_final_review_passes(
        Runner(),
        ReviewerConfig(
            active_vertical="research",
            working_dir=str(workdir),
            artifact_root=str(workdir),
            vertical_state_root=str(state),
        ),
    )

    assert decision is not None
    assert decision.status == "blocked"
    assert decision.backend_stop_kind == "provider_cooldown"
    assert "ProviderError: retry later" in decision.reason
    assert decision.input_tokens == 4
    assert decision.output_tokens == 2


def test_parallel_review_supports_the_public_memory_backend(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    persist_vertical(state, "research")
    path = state / ".argus" / "PIPELINE_STATE.json"
    payload = read_pipeline_state(state)
    payload["current_stage"] = "review"
    path.write_text(json.dumps(payload), encoding="utf-8")

    backend = MemoryBackend()
    for label in ("scientific", "visual", "language"):
        backend.queue(
            f"reviewer-{label}",
            CannedResponse(message=f"{label} pass", input_tokens=1),
        )

    decision = _parallel_final_review_passes(
        backend,
        ReviewerConfig(
            active_vertical="research",
            working_dir=str(workdir),
            artifact_root=str(workdir),
            vertical_state_root=str(state),
        ),
    )

    assert decision is not None
    assert decision.status == "continue"
    assert decision.input_tokens == 3
    assert {entry[0] for entry in backend.history} == {
        "reviewer-scientific",
        "reviewer-visual",
        "reviewer-language",
    }


def test_parallel_review_prefers_returned_control_stop_over_generic_exception(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    persist_vertical(state, "research")
    path = state / ".argus" / "PIPELINE_STATE.json"
    payload = read_pipeline_state(state)
    payload["current_stage"] = "review"
    path.write_text(json.dumps(payload), encoding="utf-8")

    class Runner:
        def fork(self):
            return Runner()

        def run_exec(self, **kwargs):
            label = kwargs["run_label"]
            if label == "reviewer-visual":
                raise RuntimeError("transport failed")
            aborted = label == "reviewer-scientific"
            return SimpleNamespace(
                exit_code=1 if aborted else 0,
                fatal_error="operator stopped review" if aborted else None,
                stop_kind="operator_abort" if aborted else None,
                agent_messages=[] if aborted else ["pass"],
                input_tokens=2,
                cached_input_tokens=0,
                output_tokens=1,
                reasoning_output_tokens=0,
                premium_requests=0.0,
            )

    decision = _parallel_final_review_passes(
        Runner(),
        ReviewerConfig(
            active_vertical="research",
            working_dir=str(workdir),
            artifact_root=str(workdir),
            vertical_state_root=str(state),
        ),
    )

    assert decision is not None
    assert decision.status == "blocked"
    assert decision.backend_stop_kind == "operator_abort"
    assert decision.backend_fatal_error == "operator stopped review"
    assert decision.input_tokens == 4
    assert decision.output_tokens == 2


def test_review_write_failure_is_visible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    persist_vertical(state, "research")
    path = state / ".argus" / "PIPELINE_STATE.json"
    payload = read_pipeline_state(state)
    payload["current_stage"] = "review"
    path.write_text(json.dumps(payload), encoding="utf-8")

    def fail_write(_path: Path, _text: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(
        "argus_skill.manager.source_writeback.atomic_write",
        fail_write,
    )

    with pytest.raises(OSError, match="disk full"):
        _persist_research_review(
            ReviewDecision(status="done", reason="accepted", next_action=""),
            ReviewerConfig(
                active_vertical="research",
                working_dir=str(workdir),
                artifact_root=str(workdir),
                vertical_state_root=str(state),
            ),
        )
