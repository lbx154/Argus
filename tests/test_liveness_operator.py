"""Liveness regressions for Manager/Operator handoffs.

Each test pins one confirmed defect: a question may park only its own task,
never the operator's control channel; a host "drop" ends one task; a decision
nobody can make does not park forever; and an answer is applied only to the
question it was written for.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from argus.core.autonomy import OPERATOR_WAIT_EXIT_FILENAME, read_operator_blocks
from argus.core.operator_decision import build_operator_decision
from argus.daemon.state import read_continuous_state, write_continuous_config
from argus.life.memory import Backlog, BacklogItem, LifeMemory
from argus.manager import config_intent, front_door
from argus.manager.directive import set_active_manager_directive
from argus.webapi import manager_bridge, manager_pending_question, manager_state

pytest.importorskip("fastapi")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_project(root: Path, sid: str = "s-liveness") -> Path:
    life = root / "projects" / sid
    life.mkdir(parents=True)
    (life / "events.jsonl").write_text(
        json.dumps({"type": "mission.started", "text": "hi", "ts": time.time()}) + "\n",
        encoding="utf-8",
    )
    (life / "backlog.jsonl").write_text("", encoding="utf-8")
    return life


@pytest.fixture(autouse=True)
def _offline_manager(monkeypatch) -> None:
    manager_state._STATES.clear()

    class _Manager:
        def classify_front_door(self, _text, *, lifetime_sink=None, **_kwargs):
            if lifetime_sink is not None:
                lifetime_sink("bounded")
            return None, None, "complex"

        def decide_vertical(self, text, **_kwargs):
            return SimpleNamespace(execution_task=text, session_title="")

        def commit_vertical_decision(self, text, decision, **_kwargs):
            return SimpleNamespace(execution_task=decision.execution_task)

    ensure = lambda chat_state, mem: SimpleNamespace(manager=_Manager())
    monkeypatch.setattr(front_door, "_ensure_manager_runner", ensure)
    monkeypatch.setattr(config_intent, "_ensure_manager_runner", ensure)


def _park(life: Path, title: str, question: str) -> BacklogItem:
    item = BacklogItem.new(title=title, objective=f"work on {title}")
    item.status = "paused_operator"
    item.pending_question = question
    item.operator_decision = build_operator_decision(
        item_id=item.id, title=title, reason="blocked", question=question,
    )
    LifeMemory.open(life).backlog.add(item)
    return item


def _questions(life: Path) -> dict[str, str]:
    return {
        row.id: row.pending_question
        for row in LifeMemory.open(life).backlog.all()
        if row.pending_question
    }


# ---------------------------------------------------------------------------
# 1. A pending question never blocks the operator's control channel.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("control", ["pause", "abort"])
def test_several_open_questions_do_not_swallow_control(
    tmp_path: Path, monkeypatch, control: str,
) -> None:
    sid = "s-liveness"
    life = _make_project(tmp_path, sid)
    memory = LifeMemory.open(life)
    running = memory.backlog.add(BacklogItem.new(title="looping", objective="work"))
    memory.backlog.mark_running(running.id)
    first = _park(life, "route", "Which route?")
    second = _park(life, "gpu", "Which GPU?")

    def interpret(*_args, **_kwargs):
        raise AssertionError("no single question for the Manager to interpret")

    monkeypatch.setattr(front_door, "manager_triage", interpret)
    monkeypatch.setattr(
        config_intent, "_front_door_classify",
        lambda *_args, **_kwargs: (None, control, "simple"),
    )

    result = manager_bridge.manager_message(sid, "stop that now", global_root=tmp_path)

    assert result["kind"] == "control"
    assert result["control"] == control
    assert result["item_id"] == running.id
    assert (life / "running_item_abort.json").exists()
    assert _questions(life) == {first.id: "Which route?", second.id: "Which GPU?"}


def test_several_open_questions_still_accept_new_work(tmp_path: Path, monkeypatch) -> None:
    sid = "s-liveness"
    life = _make_project(tmp_path, sid)
    first = _park(life, "route", "Which route?")
    second = _park(life, "gpu", "Which GPU?")
    monkeypatch.setattr(
        manager_bridge, "_dispatch_team_mission",
        lambda mem, body, *args, **kwargs: (
            mem.backlog.add(BacklogItem.new(title="new work", objective=body)),
            False,
            None,
        ),
    )

    result = manager_bridge.manager_message(
        sid, "Also benchmark the parser on the large corpus.", global_root=tmp_path,
    )

    assert result["kind"] == "task", result
    assert result["item"]["title"] == "new work"
    assert _questions(life) == {first.id: "Which route?", second.id: "Which GPU?"}


@pytest.mark.parametrize("failure", ["backend", "contract"])
def test_failed_interpretation_of_one_question_still_reaches_pause(
    tmp_path: Path, monkeypatch, failure: str,
) -> None:
    from argus.daemon.state import read_continuous_state, write_continuous_config

    sid = "s-liveness"
    life = _make_project(tmp_path, sid)
    blocked = _park(life, "route", "Which route?")
    write_continuous_config(life, enabled=True, objective="keep optimizing")

    def interpret(*_args, **_kwargs):
        if failure == "backend":
            raise RuntimeError("401 Missing bearer")
        return "IS_ANSWER=maybe\nRESOLVED=true\nDECISION=continue"

    monkeypatch.setattr(front_door, "manager_triage", interpret)
    monkeypatch.setattr(
        config_intent, "_front_door_classify",
        lambda *_args, **_kwargs: (None, "pause", "simple"),
    )

    result = manager_bridge.manager_message(sid, "pause everything", global_root=tmp_path)

    assert result["kind"] == "control" and result["control"] == "pause"
    assert read_continuous_state(life).enabled is False
    assert _questions(life) == {blocked.id: "Which route?"}
    assert not (life / "operator_context.jsonl").exists()


# ---------------------------------------------------------------------------
# 2. "Drop" on the host's provider-configuration card ends that task only.
# ---------------------------------------------------------------------------


def _provider_card_item(life: Path, *, decision_kind: str | None = "provider_configuration") -> BacklogItem:
    item = BacklogItem.new(title="prove the bound", objective="prove it")
    item.status = "paused_operator"
    item.provider_cooldown_signature = "model is not available"
    item.pending_question = "The model configuration failed 3 times. Fix it or drop it."
    card = build_operator_decision(
        item_id=item.id, title=item.title, reason="model unavailable",
        question=item.pending_question,
        options=[
            {"id": "resume", "label": "Resume after fixing the configuration",
             "description": "Retry this task once the model name has been corrected."},
            {"id": "drop", "label": "Drop this task",
             "description": "Stop pursuing this task under the current configuration."},
        ],
    )
    if decision_kind:
        card["decision_kind"] = decision_kind
    item.operator_decision = card
    LifeMemory.open(life).backlog.add(item)
    return item


@pytest.mark.parametrize("decision_kind", ["provider_configuration", None])
def test_drop_on_provider_card_ends_the_item_without_a_continuation(
    tmp_path: Path, decision_kind: str | None,
) -> None:
    sid = "s-liveness"
    life = _make_project(tmp_path, sid)
    item = _provider_card_item(life, decision_kind=decision_kind)
    decision_id = item.operator_decision["id"]

    result = manager_pending_question.manager_resolve_operator_decision(
        sid, decision_id, "drop", global_root=tmp_path,
    )

    assert result["resolved"] is True and result["dropped"] is True
    # The dropped item does not resume, but the rest of the campaign does, so
    # the resolve route still starts the worker.
    assert result["resume_requested"] is True
    rows = LifeMemory.open(life).backlog.history()
    assert [row.id for row in rows] == [item.id]  # no continuation was enqueued
    dropped = rows[0]
    assert dropped.status == "aborted"
    assert dropped.pending_question == ""
    assert dropped.operator_decision["status"] == "resolved"
    assert dropped.operator_decision["selected_option"] == "drop"
    assert dropped.operator_decision["resume_requested"] is False

    replay = manager_pending_question.manager_resolve_operator_decision(
        sid, decision_id, "drop", global_root=tmp_path,
    )
    assert replay["application_status"] == "already_applied"
    assert replay["resume_requested"] is False


def test_agent_option_named_drop_keeps_its_ordinary_meaning(tmp_path: Path) -> None:
    sid = "s-liveness"
    life = _make_project(tmp_path, sid)
    item = BacklogItem.new(title="clean data", objective="clean the dataset")
    item.status = "paused_operator"
    item.pending_question = "Keep or drop the outlier rows?"
    item.operator_decision = build_operator_decision(
        item_id=item.id, title=item.title, reason="ambiguous", question=item.pending_question,
        options=[
            {"id": "keep", "label": "Keep outliers", "description": "Keep all rows."},
            {"id": "drop", "label": "Drop outliers", "description": "Remove the 3 outlier rows."},
        ],
    )
    LifeMemory.open(life).backlog.add(item)

    result = manager_pending_question.manager_resolve_operator_decision(
        sid, item.operator_decision["id"], "drop", global_root=tmp_path,
    )

    assert result["resolved"] is True and "dropped" not in result
    rows = LifeMemory.open(life).backlog.history()
    continuation = next(row for row in rows if row.id != item.id)
    assert "Remove the 3 outlier rows." in continuation.objective


def test_drop_on_provider_card_rearms_a_disabled_campaign(tmp_path: Path) -> None:
    sid = "s-liveness"
    life = _make_project(tmp_path, sid)
    write_continuous_config(life, enabled=False, objective="keep optimizing")
    item = _provider_card_item(life)

    result = manager_pending_question.manager_resolve_operator_decision(
        sid, item.operator_decision["id"], "drop", global_root=tmp_path,
    )

    assert result["dropped"] is True and "projection_error" not in result
    # Dropping one task is not stopping the campaign.
    campaign = read_continuous_state(life)
    assert campaign.enabled is True
    assert campaign.objective == "keep optimizing"


def test_cli_drop_restarts_a_worker_that_ended_on_the_question(
    tmp_path: Path, monkeypatch,
) -> None:
    from argus.apps.cli import _core

    sid = "s-liveness"
    life = _make_project(tmp_path, sid)
    item = _provider_card_item(life)
    (life / OPERATOR_WAIT_EXIT_FILENAME).write_text("{}", encoding="utf-8")
    started: list[str] = []
    monkeypatch.setattr(
        "argus.webapi.daemon_lifecycle.start_project_daemon",
        lambda project_sid, **_kwargs: started.append(project_sid) or {"rc": 0},
    )
    bundle = SimpleNamespace(
        project=SimpleNamespace(root=life, fingerprint=sid), global_root=tmp_path,
    )
    monkeypatch.setattr(_core, "_resolve_project_bundle", lambda _args: bundle)

    code = _core._cmd_answer(argparse.Namespace(answer="drop", answer_item=""))

    assert code == 0
    assert started == [sid]
    assert not (life / OPERATOR_WAIT_EXIT_FILENAME).exists()
    rows = LifeMemory.open(life).backlog.history()
    assert [(row.id, row.status) for row in rows] == [(item.id, "aborted")]


def test_drop_does_not_overwrite_an_answer_that_resolved_the_card_first(
    tmp_path: Path,
) -> None:
    sid = "s-liveness"
    life = _make_project(tmp_path, sid)
    item = _provider_card_item(life)
    decision_id = item.operator_decision["id"]
    # The web drop validated this snapshot; a CLI free-text answer then
    # resolved the same card and enqueued its continuation.
    stale_snapshot = next(
        row for row in LifeMemory.open(life).backlog.history() if row.id == item.id
    )
    _blocked, continuation = Backlog(life / "backlog.jsonl").continue_with_operator_reply(
        item.id, "Resume; the model name is fixed.",
        manager_decision="Resume; the model name is fixed.",
    )
    assert continuation is not None

    result = manager_pending_question._drop_decision_item(
        SimpleNamespace(project_root=life, backlog=LifeMemory.open(life).backlog),
        stale_snapshot, decision_id=decision_id, note="",
    )

    assert result.get("application_status") == "stale"
    assert not result.get("dropped")
    rows = {row.id: row for row in LifeMemory.open(life).backlog.history()}
    answered = rows[item.id]
    assert answered.status == "failed"  # the continuation's transaction, intact
    assert answered.operator_decision["status"] == "resolved"
    assert answered.operator_decision["selected_option"] == "custom"
    assert answered.operator_decision["continuation_item_id"] == continuation.id
    assert rows[continuation.id].status == "pending"


# ---------------------------------------------------------------------------
# 3. Host questions nobody can answer end the item instead of parking it.
# ---------------------------------------------------------------------------


_MODEL_CONFIG_REASON = (
    'backend fatal error: error=Error: Model "gpt-5.6-sol" from --model flag is not available.'
)


def _cooldown_state(item: BacklogItem):
    from argus.life.supervisor._mission_execution_helpers import _MissionRunState

    state = _MissionRunState(item)
    state.stop_reason = _MODEL_CONFIG_REASON
    state.stop_kind = "provider_cooldown"
    state.outcome = SimpleNamespace(final_review_status="")
    state.usage_summary = SimpleNamespace(pricing_status="priced")
    return state


def _nobody_to_ask(monkeypatch, state_root: Path, mode: str) -> None:
    """No operator in the run, or one who asked not to be asked."""
    monkeypatch.setenv("ARGUS_AUTONOMY_RUN_ID", "liveness-run")
    if mode == "no_operator":
        monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
        return
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    set_active_manager_directive(
        state_root, "Work without asking the operator.",
        operator_question_policy="forbid",
    )


@pytest.mark.parametrize("mode", ["no_operator", "forbid"])
def test_provider_configuration_failure_without_operator_ends_the_item(
    tmp_path: Path, monkeypatch, mode: str,
) -> None:
    from argus.life.supervisor._mission_execution_runtime import (
        MissionExecutionRuntimeMixin,
    )

    _nobody_to_ask(monkeypatch, tmp_path, mode)

    class _Harness(MissionExecutionRuntimeMixin):
        def __init__(self, memory: LifeMemory) -> None:
            self.memory = memory
            self.events: list[dict] = []

        def _emit(self, event) -> None:
            self.events.append(dict(event))

        def _project_state_root(self) -> Path:
            return tmp_path

    memory = LifeMemory.open(tmp_path)
    memory.init()
    item = memory.backlog.add(BacklogItem.new(title="prove the bound", objective="prove it"))
    harness = _Harness(memory)

    def fresh() -> BacklogItem:
        return next(row for row in memory.backlog.history() if row.id == item.id)

    results = [
        harness._maybe_park_permanent_provider_failure(_cooldown_state(fresh()))
        for _ in range(3)
    ]

    assert results[:2] == [None, None]
    assert results[2]["status"] == "failed"
    ended = fresh()
    assert ended.status == "failed"
    assert ended.pending_question == "" and ended.operator_decision == {}
    assert "No operator is available" in ended.last_error
    blocks = read_operator_blocks(tmp_path)
    assert [row["item_id"] for row in blocks] == [item.id]
    assert not [
        event for event in harness.events
        if event["type"] == "life.operator_question.pending"
    ]


@dataclass
class _Outcome:
    success: bool = True
    status: str = "done"
    stop_reason: str = ""
    rounds: int = 1
    matched_skill_name: str = ""
    skill_distilled: bool = True
    had_follow_up: bool = False
    final_message: str = "done"
    operator_question: str = ""
    operator_options: list[dict[str, Any]] = field(default_factory=list)
    research_result: dict[str, Any] | None = None


class _MaintenanceRunner:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def execute(self, **kwargs) -> _Outcome:
        self.kwargs = kwargs
        if kwargs.get("maintenance_mission"):
            Path(kwargs["working_dir_override"], "reviewed-change.txt").write_text(
                "reviewed\n", encoding="utf-8",
            )
        outcome = _Outcome()
        outcome.final_review_status = "done"
        outcome.final_review_source = "reviewer"
        return outcome


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def handle_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)


@pytest.mark.parametrize("mode", ["no_operator", "forbid"])
def test_reviewed_framework_change_without_operator_is_not_parked(
    tmp_path: Path, monkeypatch, mode: str,
) -> None:
    from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig

    source = tmp_path / "framework"
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "checkout", "-qb", "main"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=source, check=True)
    (source / "baseline.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "baseline.txt"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=source, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=source, check=True)
    subprocess.run(["git", "push", "-qu", "origin", "main"], cwd=source, check=True)
    monkeypatch.setattr("argus.core.runtime_identity.source_root", lambda: source)

    memory = LifeMemory.open(tmp_path / "life")
    _nobody_to_ask(monkeypatch, memory.root, mode)
    project = tmp_path / "project"
    project.mkdir()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_MaintenanceRunner(),
        sink=_Sink(),
        config=LifeSupervisorConfig(project_worktree=project, artifact_root=project),
    )
    item = memory.backlog.add(BacklogItem.new(
        title="repair framework",
        objective="fix observed defect",
        tags=["framework_maintenance", "review:required", "scope:bounded"],
        execution_workdir="",
        context_refs=[{"ref": str(memory.root / "events.jsonl"), "why": "observed failure"}],
        acceptance_check=(
            "python -c \"from pathlib import Path; "
            "raise SystemExit(not Path('reviewed-change.txt').is_file())\""
        ),
        manager_decision={
            "routed": True, "vertical": "argus_maintenance", "workflow_mode": "direct",
        },
    ))

    result = supervisor.tick()

    assert result is not None and result["status"] == "failed"
    settled = next(row for row in memory.backlog.history() if row.id == item.id)
    assert settled.status == "failed"
    assert settled.pending_question == "" and settled.operator_decision == {}
    assert "no operator is available" in settled.last_error
    assert "Reviewed commit:" in settled.last_error
    blocks = read_operator_blocks(memory.root)
    assert [row["operator_need"] for row in blocks] == ["irreversible_or_external"]
    # Nothing was published: origin still holds only the baseline commit.
    published = subprocess.run(
        ["git", "rev-list", "--count", "main"], cwd=origin,
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert published == "1"


# ---------------------------------------------------------------------------
# 4. An answer applies only to the question it was written for.
# ---------------------------------------------------------------------------


def test_rebuilt_card_with_a_new_question_supersedes_the_old_id() -> None:
    first = build_operator_decision(
        item_id="item", title="t", reason="r", question="Use A?",
        options=[{"id": "option-1", "label": "Yes"}],
    )
    same = build_operator_decision(
        item_id="item", title="t2", reason="r2", question="Use A?",
        options=[{"id": "option-1", "label": "Yes"}], previous_decision=first,
    )
    changed = build_operator_decision(
        item_id="item", title="t", reason="r", question="Delete the cache?",
        options=[{"id": "option-1", "label": "Yes"}], previous_decision=same,
    )
    again = build_operator_decision(
        item_id="item", title="t", reason="r", question="Use B?",
        previous_decision=changed,
    )

    assert same["id"] == first["id"] == "decision-item"
    assert changed["id"] == "decision-item-q2"
    assert changed["superseded_decision_ids"] == ["decision-item"]
    assert again["id"] == "decision-item-q3"
    assert again["superseded_decision_ids"] == ["decision-item", "decision-item-q2"]


def test_stale_card_answer_is_rejected_not_applied_to_the_new_question(
    tmp_path: Path,
) -> None:
    sid = "s-liveness"
    life = _make_project(tmp_path, sid)
    backlog = LifeMemory.open(life).backlog
    item = BacklogItem.new(title="cleanup", objective="tidy the workspace")
    item.status = "paused_operator"
    item.pending_question = "Keep the scratch notes?"
    item.operator_decision = build_operator_decision(
        item_id=item.id, title=item.title, reason="r", question=item.pending_question,
        options=[{"id": "option-1", "label": "Yes, keep them"}],
    )
    backlog.add(item)
    stale_id = item.operator_decision["id"]
    # The question is re-asked differently before the operator's click lands;
    # "option-1" now means something else.
    newer = build_operator_decision(
        item_id=item.id, title=item.title, reason="r", question="Delete the cache?",
        options=[{"id": "option-1", "label": "Yes, delete it"}],
        previous_decision=item.operator_decision,
    )
    backlog.update(item.id, pending_question="Delete the cache?", operator_decision=newer)

    result = manager_pending_question.manager_resolve_operator_decision(
        sid, stale_id, "option-1", global_root=tmp_path,
    )

    assert result["application_status"] == "stale"
    assert "question changed" in result["error"]
    rows = LifeMemory.open(life).backlog.history()
    assert [row.id for row in rows] == [item.id]
    assert rows[0].pending_question == "Delete the cache?"
    assert rows[0].operator_decision["status"] == "pending"
