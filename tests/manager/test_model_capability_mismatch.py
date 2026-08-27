from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from argus_skill.core.transcript import read_turns
from argus_skill.life import MemoryBundle
from argus_skill.life.memory import LifeMemory
from argus_skill.manager import Manager
from argus_skill.manager.classification_contract import (
    REPOSITORY_TOOL_CLAUSE,
    STRUCTURED_DECISION_CLAUSE,
    contract_failure_count,
)
from argus_skill.manager.domain_author import (
    ManagerClassificationContractError,
)
from argus_skill.manager.front_door import (
    ManagerHandoffError,
    ManagerModelCapabilityMismatchError,
    manager_bounded_handoff,
    prepare_manager_execution_task,
)

MODEL_ID = "opencode-go/mimo-v2.5"


class _Result:
    def __init__(
        self,
        message: str,
        *,
        tool_activity_observed: bool = True,
        fatal_error: str = "",
        exit_code: int = 0,
    ) -> None:
        self.last_agent_message = message
        self.agent_messages = [message]
        self.tool_activity_observed = tool_activity_observed
        self.fatal_error = fatal_error
        self.exit_code = exit_code


class _MutableRunner:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[str] = []

    def run_exec(self, *, run_label: str, **_kwargs):
        self.calls.append(run_label)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _malformed() -> _Result:
    return _Result("I think this is probably software work.")


def _valid() -> _Result:
    return _Result(json.dumps({
        "choice": "existing",
        "vertical": "software",
        "workflow_mode": "direct",
        "rationale": "repository repair",
    }))


def _manager(tmp_path, runner: _MutableRunner) -> Manager:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='contract-test'\n",
        encoding="utf-8",
    )
    return Manager(
        project_root=tmp_path,
        execution_workdir=tmp_path,
        runner=runner,
        memory_maintenance_enabled=False,
    )


def test_contract_failure_increments_and_success_resets(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_MODEL", MODEL_ID)
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_FAST_ROUTE", "0")
    runner = _MutableRunner(_malformed())
    manager = _manager(tmp_path, runner)

    with pytest.raises(ManagerClassificationContractError) as caught:
        manager.decide_vertical("Repair the parser.")

    assert caught.value.clause == STRUCTURED_DECISION_CLAUSE
    assert caught.value.consecutive_count == 1
    assert contract_failure_count(tmp_path, model_id=MODEL_ID) == 1

    runner.result = _valid()
    assert manager.decide_vertical("Repair the parser.").vertical == "software"
    assert contract_failure_count(tmp_path, model_id=MODEL_ID) == 0


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (TimeoutError("provider timed out"), "provider timed out"),
        (_Result("", fatal_error="HTTP 429 rate limited", exit_code=1), "429"),
        (_Result("", fatal_error="HTTP 503 unavailable", exit_code=1), "503"),
        (_Result("", fatal_error="401 authentication failed", exit_code=1), "401"),
    ],
)
def test_transient_and_auth_failures_do_not_increment_or_change_message(
    tmp_path, monkeypatch, result, expected,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_MODEL", MODEL_ID)
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_FAST_ROUTE", "0")
    runner = _MutableRunner(_malformed())
    manager = _manager(tmp_path, runner)
    with pytest.raises(ManagerClassificationContractError):
        manager.decide_vertical("Repair the parser.")
    assert contract_failure_count(tmp_path, model_id=MODEL_ID) == 1

    runner.result = result
    with pytest.raises(Exception) as caught:
        manager.decide_vertical("Repair the parser.")

    assert expected in str(caught.value)
    assert "role-capability mismatch" not in str(caught.value)
    assert contract_failure_count(tmp_path, model_id=MODEL_ID) == 1


def test_repository_tool_contract_has_its_own_clause(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_MODEL", MODEL_ID)
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_FAST_ROUTE", "0")
    no_tool = _Result(
        json.dumps({
            "choice": "new",
            "vertical": "custom_runtime",
            "stages": ["measure", "implement", "verify"],
            "workflow_mode": "staged",
            "execution_task": "Build the project-specific runtime.",
            "rationale": "new local capability",
            "confidence": 0.9,
        }),
        tool_activity_observed=False,
    )
    manager = _manager(tmp_path, _MutableRunner(no_tool))

    with pytest.raises(ManagerClassificationContractError) as caught:
        manager.decide_vertical("Build a project-specific runtime.")

    assert caught.value.clause == REPOSITORY_TOOL_CLAUSE
    assert caught.value.consecutive_count == 1


def test_threshold_changes_message_publishes_alert_and_stays_fail_closed(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_MODEL", MODEL_ID)
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_FAST_ROUTE", "0")
    memory = MemoryBundle.for_cwd(
        tmp_path / "workspace",
        global_root=tmp_path / "state",
        fingerprint="manager-mismatch",
    )
    memory.init()
    runner = _MutableRunner(_malformed())
    manager = Manager(
        project_root=memory.project_root,
        execution_workdir=tmp_path / "workspace",
        runner=runner,
        memory_maintenance_enabled=False,
    )
    (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)
    (tmp_path / "workspace" / "pyproject.toml").write_text(
        "[project]\nname='contract-test'\n",
        encoding="utf-8",
    )
    built = SimpleNamespace(manager=manager)
    state: dict[str, object] = {}

    for expected_count in (1, 2):
        with pytest.raises(ManagerHandoffError) as caught:
            prepare_manager_execution_task(
                memory,
                "Repair the parser.",
                state,
                ensure_runner=lambda *_args: built,
            )
        assert type(caught.value) is ManagerHandoffError
        assert str(caught.value).startswith(
            "Manager handoff failed: routing failed [parse]: model_reply"
        )
        assert "(attempts=2)" in str(caught.value)
        assert "role-capability mismatch" not in str(caught.value)
        assert contract_failure_count(memory.project_root, model_id=MODEL_ID) == expected_count

    persisted: list[str] = []
    with pytest.raises(ManagerModelCapabilityMismatchError) as caught:
        manager_bounded_handoff(
            memory,
            "Repair the parser.",
            state,
            lambda task, _division: persisted.append(task),
            ensure_runner=lambda *_args: built,
        )

    text = str(caught.value)
    assert MODEL_ID in text
    assert STRUCTURED_DECISION_CLAUSE in text
    assert "3 consecutive times" in text
    assert "role-capability mismatch, not a provider outage" in text
    assert "ARGUS_SKILL_MANAGER_MODEL" in text
    assert "Settings → Advanced settings" in text
    assert "manager_model" in text
    assert "There is no `/model` command" in text
    assert "retry" not in text.lower()
    assert persisted == []

    alerts = [
        json.loads(line)
        for line in (memory.project_root / "events.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if json.loads(line).get("manager_model_capability_mismatch") is True
    ]
    assert len(alerts) == 1
    assert alerts[0]["operator_alert"] is True
    assert alerts[0]["model_id"] == MODEL_ID
    assert alerts[0]["failed_clause"] == STRUCTURED_DECISION_CLAUSE
    assert alerts[0]["consecutive_count"] == 3
    assert any(turn["text"] == text for turn in read_turns(memory.project_root))

    with pytest.raises(ManagerModelCapabilityMismatchError) as after_threshold:
        manager_bounded_handoff(
            memory,
            "Repair the parser again.",
            state,
            lambda task, _division: persisted.append(task),
            ensure_runner=lambda *_args: built,
        )
    assert "4 consecutive times" in str(after_threshold.value)
    assert persisted == []


def test_cockpit_manager_model_alias_is_role_specific(tmp_path, monkeypatch) -> None:
    from argus_skill.webapi.mission_items import set_operator_config

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    result = set_operator_config("manager_model", "capable/manager-model")

    assert result["name"] == "ARGUS_SKILL_MANAGER_MODEL"
    stored = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert stored["ARGUS_SKILL_MANAGER_MODEL"] == "capable/manager-model"
    assert "ARGUS_SKILL_MODEL" not in stored


def test_manager_chat_surfaces_capability_message_without_dispatch(
    tmp_path, monkeypatch,
) -> None:
    from argus_skill.manager import config_intent, front_door
    from argus_skill.webapi import manager_bridge, manager_state

    sid = "manager-mismatch-inline"
    life_dir = tmp_path / "projects" / sid
    life_dir.mkdir(parents=True)
    (life_dir / "events.jsonl").write_text("", encoding="utf-8")
    (life_dir / "backlog.jsonl").write_text("", encoding="utf-8")
    manager_state._STATES.clear()
    message = "[not dispatched] Manager model role-capability mismatch: test"

    monkeypatch.setattr(
        config_intent,
        "_front_door_classify",
        lambda *_args, **_kwargs: (None, None, "complex"),
    )
    monkeypatch.setattr(front_door, "manager_triage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        manager_bridge,
        "_dispatch_team_mission",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ManagerModelCapabilityMismatchError(message)
        ),
    )

    result = manager_bridge.manager_message(
        sid,
        "Repair the parser.",
        global_root=tmp_path,
    )

    assert result == {"kind": "error", "reply": message}
    assert LifeMemory.open(life_dir).backlog.all() == []
