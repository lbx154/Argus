"""Liveness regressions for Manager/Operator handoffs.

Each test pins one confirmed defect: a question may park only its own task,
never the operator's control channel; a host "drop" ends one task; while the
operator cannot be asked the Manager decides in their place, so nothing parks
forever; and an answer is applied only to the question it was written for.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from argus.core.autonomy import read_autonomous_assumptions
from argus.core.knob_store import write_persisted_knob
from argus.core.operator_decision import build_operator_decision
from argus.daemon.state import read_continuous_state, write_continuous_config
from argus.life.memory import Backlog, BacklogItem, LifeMemory
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from argus.manager import front_door
from argus.webapi import manager_bridge, manager_pending_question, manager_state
from tests.hand_off import manager_runner, manager_settles, ticket

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
        def decide_vertical(self, text, **_kwargs):
            return SimpleNamespace(execution_task=text, session_title="")

        def commit_vertical_decision(self, text, decision, **_kwargs):
            return SimpleNamespace(execution_task=decision.execution_task)

    monkeypatch.setattr(front_door, "_ensure_manager_runner",
                        lambda chat_state, mem: manager_runner(_Manager(), hand=lambda text: ticket(text, lifetime="bounded")))


def _park(life: Path, title: str, question: str, options: tuple[str, ...] = ()) -> BacklogItem:
    item = BacklogItem.new(title=title, objective=f"work on {title}")
    item.status = "paused_operator"
    item.pending_question = question
    item.operator_decision = build_operator_decision(
        item_id=item.id, title=title, reason="blocked", question=question, options=options,
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

    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_a: manager_runner(
        hand=manager_settles(control={"kind": control, "quote": "stop that now"})))

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

    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_a: manager_runner(
        hand=manager_settles(control={"kind": "pause", "quote": "pause everything"})))

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
    (resumed,) = LifeMemory.open(life).backlog.history()
    assert "Remove the 3 outlier rows." in resumed.objective


@pytest.mark.parametrize(
    "done_reason",
    [
        "operator pause",
        "planner declared project done",
        "operator chose to stop the campaign",
    ],
)
def test_drop_leaves_a_stopped_campaign_stopped(
    tmp_path: Path, done_reason: str,
) -> None:
    sid = "s-liveness"
    life = _make_project(tmp_path, sid)
    write_continuous_config(
        life, enabled=False, objective="keep optimizing", done_reason=done_reason,
    )
    item = _provider_card_item(life)

    result = manager_pending_question.manager_resolve_operator_decision(
        sid, item.operator_decision["id"], "drop", global_root=tmp_path,
    )

    assert result["dropped"] is True
    # Dropping one task is not a resume: the resolve route starts no worker.
    assert result["resume_requested"] is False and "continuous" not in result
    assert "stays stopped" in result["reply"]
    campaign = read_continuous_state(life)
    assert campaign.enabled is False
    assert campaign.done_reason == done_reason
    assert campaign.objective == "keep optimizing"


def test_drop_continues_an_armed_campaign(tmp_path: Path) -> None:
    sid = "s-liveness"
    life = _make_project(tmp_path, sid)
    write_continuous_config(life, enabled=True, objective="keep optimizing")
    item = _provider_card_item(life)

    result = manager_pending_question.manager_resolve_operator_decision(
        sid, item.operator_decision["id"], "drop", global_root=tmp_path,
    )

    assert result["dropped"] is True and result["resume_requested"] is True
    assert result["reply"] == "Dropped this task."
    campaign = read_continuous_state(life)
    assert campaign.enabled is True
    assert campaign.objective == "keep optimizing"


@pytest.mark.parametrize("explicit_resume", [False, True])
def test_only_an_explicit_resume_rearms_a_paused_campaign(
    tmp_path: Path, monkeypatch, explicit_resume: bool,
) -> None:
    from argus.webapi import daemon_lifecycle

    sid = "s-liveness"
    life = _make_project(tmp_path, sid)
    write_continuous_config(
        life, enabled=False, objective="keep optimizing", done_reason="operator pause",
    )
    config = SimpleNamespace(
        continuous_open_ended=True, continuous_objective="", resume_continuous=False,
    )
    monkeypatch.setattr(daemon_lifecycle, "project_life_dir", lambda _sid, **_k: life)
    monkeypatch.setattr(
        daemon_lifecycle.daemon_worker,
        "read_daemon_status",
        lambda _d: SimpleNamespace(alive=False),
    )
    monkeypatch.setattr(daemon_lifecycle, "_worker_config_from_env", lambda _d, _r: config)

    def stop_here(*_a, **_k):
        raise RuntimeError("stop")

    monkeypatch.setattr(daemon_lifecycle, "_max_active_daemons", stop_here)
    with pytest.raises(RuntimeError):
        daemon_lifecycle.start_project_daemon(
            sid, global_root=tmp_path, resume_continuous=explicit_resume,
        )

    # Only the operator's own Resume re-arms a pause.
    assert read_continuous_state(life).enabled is explicit_resume
    assert config.resume_continuous is explicit_resume


def _cli_answer(
    tmp_path: Path, monkeypatch, life: Path, sid: str, answer: str = "drop",
) -> tuple[int, list[str]]:
    from argus.apps.cli import _core

    started: list[str] = []
    monkeypatch.setattr(
        "argus.webapi.daemon_lifecycle.start_project_daemon",
        lambda project_sid, **_kwargs: started.append(project_sid) or {"rc": 0},
    )
    bundle = SimpleNamespace(
        project=SimpleNamespace(root=life, fingerprint=sid), global_root=tmp_path,
    )
    monkeypatch.setattr(_core, "_resolve_project_bundle", lambda _args: bundle)
    code = _core._cmd_answer(argparse.Namespace(answer=answer, answer_item=""))
    return code, started


def test_cli_drop_after_a_pause_starts_nothing(tmp_path: Path, monkeypatch) -> None:
    sid = "s-liveness"
    life = _make_project(tmp_path, sid)
    item = _provider_card_item(life)
    write_continuous_config(
        life, enabled=False, objective="keep optimizing", done_reason="operator pause",
    )

    code, started = _cli_answer(tmp_path, monkeypatch, life, sid)

    assert code == 0
    assert started == []
    campaign = read_continuous_state(life)
    assert campaign.enabled is False and campaign.done_reason == "operator pause"
    rows = LifeMemory.open(life).backlog.history()
    assert [(row.id, row.status) for row in rows] == [(item.id, "aborted")]


# An ordinary answer follows the same rule: answering one card answers that
# question. It reaches its task, but a recorded stop stands until Resume.


def _campaign_left_by(life: Path, how: str) -> None:
    if how == "pause":
        write_continuous_config(
            life, enabled=False, objective="keep optimizing", done_reason="operator pause",
        )
    else:
        write_continuous_config(life, enabled=True, objective="keep optimizing")


def _resumed(life: Path, answered: BacklogItem) -> BacklogItem:
    (row,) = LifeMemory.open(life).backlog.history()
    assert row.id == answered.id and row.status == "pending"
    return row


def test_chat_answer_after_a_pause_reaches_its_task_and_restarts_nothing(
    tmp_path: Path, monkeypatch,
) -> None:
    """Plain chat the Manager reads as the answer to a waiting question takes the answer's own path."""
    sid = "s-liveness"
    life = _make_project(tmp_path, sid)
    _campaign_left_by(life, "pause")
    item = _park(life, "route", "Which route?")
    runner = manager_runner(reply="Delivered.", hand=manager_settles(
        "The operator answered the route question.",
        answer={"item": item.id, "decision": "Take the short route."}))
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_a: runner)

    result = manager_bridge.manager_message(sid, "take the short one", global_root=tmp_path)

    # The Manager's turn saw the waiting question it answered.
    assert item.id in runner.turns[0] and "Which route?" in runner.turns[0]
    assert result["kind"] == "pending_question" and result["resolved"] is True, result
    assert result["resume_requested"] is False
    assert "Take the short route." in _resumed(life, item).objective
    assert read_continuous_state(life).enabled is False


@pytest.mark.parametrize("answer_via", ["card", "text"])
def test_web_answer_after_a_pause_reaches_its_task_and_restarts_nothing(
    tmp_path: Path, answer_via: str,
) -> None:
    sid = "s-liveness"
    life = _make_project(tmp_path, sid)
    _campaign_left_by(life, "pause")
    item = _park(life, "route", "Which route?")

    def answer() -> dict[str, Any]:
        if answer_via == "card":
            return manager_pending_question.manager_resolve_operator_decision(
                sid, item.operator_decision["id"], "custom", "Take the short route.",
                global_root=tmp_path,
            )
        return manager_pending_question.manager_answer_pending_question(
            sid, item.id, "Take the short route.", global_root=tmp_path,
        )

    result = answer()

    assert result["resolved"] is True
    # The routes start a worker only on resume_requested.
    assert result["resume_requested"] is False and "continuous" not in result
    assert "stays stopped" in result["reply"]
    assert "Take the short route." in _resumed(life, item).objective
    campaign = read_continuous_state(life)
    assert campaign.enabled is False and campaign.done_reason == "operator pause"
    if answer_via == "card":
        replay = answer()
        assert replay["application_status"] == "already_applied"
        assert replay["resume_requested"] is False
        assert read_continuous_state(life).enabled is False


def _answered_events(life: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (life / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if '"life.operator_question.answered"' in line
    ]


def test_clicking_an_option_answers_with_its_words(tmp_path: Path) -> None:
    """One click on an offered option is the answer, exactly as the role wrote it."""
    sid = "s-liveness"
    life = _make_project(tmp_path, sid)
    item = _park(life, "api", "Which requirement wins?", ("Keep the v1 API", "Ship the v2 schema"))

    result = manager_pending_question.manager_resolve_operator_decision(
        sid, item.operator_decision["id"], "option-2", "", global_root=tmp_path,
    )

    assert result["resolved"] is True
    resumed = _resumed(life, item)
    assert "Ship the v2 schema" in resumed.objective
    assert resumed.operator_decision["selected_option"] == "option-2"
    (answered,) = _answered_events(life)
    assert (answered["option"], answered["manager_decision"], answered["decided_by"]) == (
        "Ship the v2 schema", "Ship the v2 schema", "operator",
    )


def test_a_written_answer_names_no_option(tmp_path: Path) -> None:
    sid = "s-liveness"
    life = _make_project(tmp_path, sid)
    item = _park(life, "api", "Which requirement wins?", ("Keep the v1 API", "Ship the v2 schema"))

    manager_pending_question.manager_resolve_operator_decision(
        sid, item.operator_decision["id"], "custom", "Keep v1 until June.", global_root=tmp_path,
    )

    (answered,) = _answered_events(life)
    assert answered["manager_decision"] == "Keep v1 until June." and "option" not in answered


@pytest.mark.parametrize(
    ("answer", "reply"),
    [
        ("走近路。", "收到，按你定的继续做。在你恢复之前，其余工作先停着。"),
        ("Take the short route.", "Got it, carrying on with your choice. Everything else stays stopped until you resume it."),
    ],
)
def test_an_answer_is_acknowledged_in_the_operator_s_language(
    tmp_path: Path, answer: str, reply: str,
) -> None:
    sid = "s-liveness"
    life = _make_project(tmp_path, sid)
    _campaign_left_by(life, "pause")
    item = _park(life, "route", "Which route?")

    result = manager_pending_question.manager_answer_pending_question(
        sid, item.id, answer, global_root=tmp_path,
    )

    assert result["reply"] == reply


def test_a_clicked_option_is_acknowledged_in_the_question_s_language(tmp_path: Path) -> None:
    """Trial s-7d356ee9: the option clicked under a Chinese question had no
    Chinese in it, and the reply was "Got it, carrying on with your choice."."""
    sid = "s-liveness"
    life = _make_project(tmp_path, sid)
    item = _park(life, "format", "请选择采用哪种格式？", (
        'A：format_date → "2026-10-22"', 'B：format_date → "22.10.2026"',
    ))

    result = manager_pending_question.manager_resolve_operator_decision(
        sid, item.operator_decision["id"], "option-2", "", global_root=tmp_path,
    )

    # One acknowledgement rule for every way an answer arrives: it names the option chosen.
    assert result["reply"] == '好的，按你的选择继续：B：format_date → "22.10.2026"。'


def test_web_answer_continues_an_armed_campaign(tmp_path: Path) -> None:
    sid = "s-liveness"
    life = _make_project(tmp_path, sid)
    _campaign_left_by(life, "armed")
    item = _park(life, "route", "Which route?")

    result = manager_pending_question.manager_answer_pending_question(
        sid, item.id, "Take the short route.", global_root=tmp_path,
    )

    assert result["resolved"] is True and result["resume_requested"] is True
    assert "stays stopped" not in result["reply"]
    assert read_continuous_state(life).enabled is True


@pytest.mark.parametrize("how", ["pause", "armed"])
def test_cli_answer_reaches_its_task_and_starts_no_worker(
    tmp_path: Path, monkeypatch, capsys, how: str,
) -> None:
    sid = "s-liveness"
    life = _make_project(tmp_path, sid)
    _campaign_left_by(life, how)
    item = _park(life, "route", "Which route?")

    code, started = _cli_answer(
        tmp_path, monkeypatch, life, sid, answer="Take the short route.",
    )

    assert code == 0
    assert "Take the short route." in _resumed(life, item).objective
    assert started == []
    assert ("still stopped" in capsys.readouterr().out) is (how == "pause")
    if how == "pause":
        campaign = read_continuous_state(life)
        assert campaign.enabled is False and campaign.done_reason == "operator pause"


def test_drop_does_not_overwrite_an_answer_that_resolved_the_card_first(
    tmp_path: Path,
) -> None:
    sid = "s-liveness"
    life = _make_project(tmp_path, sid)
    item = _provider_card_item(life)
    decision_id = item.operator_decision["id"]
    # The web drop validated this snapshot; a CLI free-text answer then
    # resolved the same card and resumed its task.
    stale_snapshot = next(
        row for row in LifeMemory.open(life).backlog.history() if row.id == item.id
    )
    _resumed_row, resumed = Backlog(life / "backlog.jsonl").resume_with_operator_reply(
        item.id, "Resume; the model name is fixed.",
        manager_decision="Resume; the model name is fixed.",
    )
    assert resumed

    result = manager_pending_question._drop_decision_item(
        SimpleNamespace(project_root=life, backlog=LifeMemory.open(life).backlog),
        stale_snapshot, decision_id=decision_id, note="",
    )

    assert result.get("application_status") == "stale"
    assert not result.get("dropped")
    rows = {row.id: row for row in LifeMemory.open(life).backlog.history()}
    answered = rows[item.id]
    assert answered.status == "pending"  # the answer's transaction, intact
    assert answered.operator_decision["status"] == "resolved"
    assert answered.operator_decision["selected_option"] == "custom"


# ---------------------------------------------------------------------------
# 3. While the operator cannot be asked, the Manager decides in their place.
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
    write_persisted_knob("ARGUS_SKILL_OPERATOR_AVAILABLE", "0")


@pytest.mark.parametrize("mode", ["no_operator", "forbid", "operator"])
def test_provider_configuration_failure_parks_for_whoever_decides(
    tmp_path: Path, monkeypatch, mode: str,
) -> None:
    from argus.life.supervisor._mission_execution_runtime import (
        MissionExecutionRuntimeMixin,
    )

    if mode != "operator":
        _nobody_to_ask(monkeypatch, tmp_path, mode)

    class _Harness(MissionExecutionRuntimeMixin):
        def __init__(self, memory: LifeMemory) -> None:
            self.memory = memory
            self.events: list[dict] = []

        def _emit(self, event) -> None:
            self.events.append(dict(event))

        def _operator_chinese(self) -> bool:
            return False

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
    assert results[2]["status"] == "parked"
    card = fresh().operator_decision
    assert card["decision_kind"] == "provider_configuration"
    assert card["status"] == "pending"


class _ProxyManager:
    """A Manager whose session answers every card it is asked to decide with ``fields``."""

    def __init__(self, **fields: str) -> None:
        self.fields = fields
        self.prompts: list[str] = []
        self.execution_workdir = Path(".")
        self._session = self

    def run_exec(self, *, prompt: str, options: Any, run_label: str, **_kwargs: Any) -> Any:
        from argus.core.models import RunnerResult
        from tests.hand_off import manager_settles, submit

        self.prompts.append(prompt)
        answer = {"item": re.search(r"- task (\S+), ", prompt)[1],
                  "decision": self.fields.get("NOTE") or self.fields.get("REASON", "Decided.")}
        if self.fields.get("OPTION", "custom") != "custom":
            answer["option"] = self.fields["OPTION"]
        submit(options, manager_settles(self.fields.get("REASON", "Decided."), answer=answer))
        return RunnerResult(exit_code=0, agent_messages=["Decided."])


def _supervise(life: Path, manager: _ProxyManager) -> LifeSupervisor:
    from argus.apps._runtime_construction import _pending_question_resolver_for

    supervisor = LifeSupervisor(
        memory=LifeMemory.open(life),
        runner=object(),
        sink=_Sink(),
        config=LifeSupervisorConfig(
            pending_question_resolver=_pending_question_resolver_for(life),
        ),
    )
    supervisor._bound_manager = lambda: manager
    return supervisor


@pytest.mark.parametrize("mode", ["no_operator", "forbid"])
def test_manager_drops_a_provider_card_in_the_operators_place(
    tmp_path: Path, monkeypatch, mode: str,
) -> None:
    from argus.core.transcript import read_turns

    life = _make_project(tmp_path)
    _nobody_to_ask(monkeypatch, life, mode)
    item = _provider_card_item(life)
    manager = _ProxyManager(
        DECISION_ID=item.operator_decision["id"],
        OPTION="drop",
        REASON="Nobody can fix the model name in this run.",
        EVIDENCE_REFS="events.jsonl",
    )
    supervisor = _supervise(life, manager)

    assert supervisor._decide_parked_questions() is True

    dropped = next(row for row in LifeMemory.open(life).backlog.history() if row.id == item.id)
    assert dropped.status == "aborted"
    assert dropped.operator_decision["selected_option"] == "drop"
    assert dropped.operator_decision["decided_by"] == "manager"
    # The choice is the Manager's, never written as the operator's.
    assert [turn["role"] for turn in read_turns(life)] == ["argus"]
    answered = [
        json.loads(line)
        for line in (life / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if '"life.operator_question.answered"' in line
    ]
    assert answered[-1]["decided_by"] == "manager" and answered[-1]["dropped"] is True
    row = read_autonomous_assumptions(life)[-1]
    assert row["source"] == "manager_decision"
    # Nothing is parked any more, so the Manager is not asked again.
    assert supervisor._decide_parked_questions() is False
    assert len(manager.prompts) == 1


def test_manager_answer_continues_an_agent_card_in_its_own_name(
    tmp_path: Path, monkeypatch,
) -> None:
    from argus.core.operator_context import OperatorContextStore

    life = _make_project(tmp_path)
    _nobody_to_ask(monkeypatch, life, "forbid")
    item = _park(life, "route", "Which route?")
    revision = OperatorContextStore(life).revision
    supervisor = _supervise(life, _ProxyManager(
        DECISION_ID=item.operator_decision["id"],
        OPTION="custom",
        NOTE="Take the shorter route.",
        REASON="It meets the deadline.",
    ))

    assert supervisor._decide_parked_questions() is True

    assert (
        "Manager decision in the operator's place:\nTake the shorter route."
        in _resumed(life, item).objective
    )
    # Nothing was recorded as the operator's own input.
    assert OperatorContextStore(life).revision == revision


def test_manager_answering_a_card_with_options_picks_one_of_them(
    tmp_path: Path, monkeypatch,
) -> None:
    life = _make_project(tmp_path)
    _nobody_to_ask(monkeypatch, life, "forbid")
    item = _park(life, "api", "Which requirement wins?", ("Keep the v1 API", "Ship the v2 schema"))

    # A choice the card does not offer is refused and decides nothing.
    outside = _ProxyManager(OPTION="option-9", REASON="r")
    assert _supervise(life, outside)._decide_parked_questions() is False
    assert "Ship the v2 schema" in outside.prompts[0]
    parked = next(row for row in LifeMemory.open(life).backlog.history() if row.id == item.id)
    assert parked.status == "parked"

    chooses = _ProxyManager(OPTION="option-2", REASON="The deadline needs v2.")
    assert _supervise(life, chooses)._decide_parked_questions() is True

    resumed = _resumed(life, item)
    assert resumed.operator_decision["selected_option"] == "option-2"
    assert resumed.operator_decision["decided_by"] == "manager"
    (answered,) = _answered_events(life)
    assert (answered["option"], answered["decided_by"]) == ("Ship the v2 schema", "manager")
    # The Manager's choice is recorded as an assumption, never as the operator's.
    assumption = read_autonomous_assumptions(life)[-1]
    assert assumption["source"] == "manager_decision"
    assert assumption["reading"].startswith("Ship the v2 schema")


def test_the_manager_cannot_answer_a_card_the_operator_can_be_asked(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    life = _make_project(tmp_path)
    item = _provider_card_item(life)
    manager = _ProxyManager(
        DECISION_ID=item.operator_decision["id"], OPTION="drop", REASON="r",
    )
    supervisor = _supervise(life, manager)

    assert supervisor._decide_parked_questions() is False
    assert manager.prompts == []
    result = manager_pending_question.manager_resolve_operator_decision(
        "s-liveness", item.operator_decision["id"], "drop",
        global_root=tmp_path, decided_by="manager",
    )
    assert "theirs" in result["error"]
    parked = next(row for row in LifeMemory.open(life).backlog.history() if row.id == item.id)
    assert parked.status == "parked"


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
def test_reviewed_framework_change_parks_for_the_manager(
    tmp_path: Path, monkeypatch, mode: str,
) -> None:
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

    assert result is not None and result["status"] == "parked"
    settled = next(row for row in memory.backlog.history() if row.id == item.id)
    assert settled.operator_decision["decision_kind"] == "framework_deployment"
    assert settled.operator_decision["status"] == "pending"
    # Nothing is published until someone decides: origin holds only the baseline.
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
