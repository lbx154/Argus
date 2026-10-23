from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.manager.directive import (
    ACTIVE_MANAGER_DIRECTIVE_FILENAME,
    STEERING_HEADER,
    STEERING_LEDGER_FILENAME,
    active_manager_directive_message,
    append_steering_directive,
    clear_active_manager_directive,
    load_active_manager_directive,
    set_active_manager_directive,
)


def _set_objective(root: Path, objective: str) -> None:
    (root / "continuous.json").write_text(
        json.dumps({"enabled": True, "objective": objective}),
        encoding="utf-8",
    )



def _steer(chat_state, life_dir, emitter):
    """Apply the steer a Manager filed, its directive and lifetime given as the test's chat-state fields."""
    from argus.webapi.manager_dispatch import _handle_steer_control

    directive = chat_state.pop("_frontdoor_steering_directive")
    lifetime = chat_state.pop("_frontdoor_lifetime", "bounded")
    return _handle_steer_control(life_dir, emitter, chat_state=chat_state, body=directive,
                                 directive=directive, lifetime=lifetime)

def test_directives_accumulate_until_explicitly_retracted(tmp_path: Path) -> None:
    _set_objective(tmp_path, "prove the theorem")

    first = set_active_manager_directive(tmp_path, "stop row-by-row work")
    assert load_active_manager_directive(tmp_path) == first
    assert STEERING_HEADER in active_manager_directive_message(tmp_path)
    assert "stop row-by-row work" in active_manager_directive_message(tmp_path)

    second = set_active_manager_directive(tmp_path, "use a structural batch")
    assert second.revision != first.revision
    assert load_active_manager_directive(tmp_path) == second
    standing = active_manager_directive_message(tmp_path)
    assert standing.index("use a structural batch") < standing.index(
        "stop row-by-row work"
    )
    records = [
        json.loads(line)
        for line in (tmp_path / STEERING_LEDGER_FILENAME).read_text().splitlines()
    ]
    assert [row["text"] for row in records if row["kind"] == "directive"] == [
        "stop row-by-row work",
        "use a structural batch",
    ]
    assert all("timestamp" in row for row in records)

    assert clear_active_manager_directive(tmp_path) is True
    assert clear_active_manager_directive(tmp_path) is False
    assert active_manager_directive_message(tmp_path) == ""


def test_only_a_directive_handed_to_the_planner_moves_its_revision(tmp_path: Path) -> None:
    _set_objective(tmp_path, "prove the theorem")
    source = "manager.supervision:1"

    team = set_active_manager_directive(tmp_path, "rerun the fixture", source=source, for_planner=False)
    assert team.planner_revision == ""
    planned = set_active_manager_directive(tmp_path, "split the proof", source=source)
    assert planned.planner_revision == planned.revision
    later = set_active_manager_directive(tmp_path, "keep one review", source=source, for_planner=False)
    assert load_active_manager_directive(tmp_path).planner_revision == planned.revision != later.revision


def test_directive_is_scoped_to_the_objective(tmp_path: Path) -> None:
    _set_objective(tmp_path, "first objective")
    set_active_manager_directive(tmp_path, "first-objective policy")

    _set_objective(tmp_path, "replacement objective")

    assert load_active_manager_directive(tmp_path) is None
    assert (tmp_path / ACTIVE_MANAGER_DIRECTIVE_FILENAME).exists()
    standing = active_manager_directive_message(tmp_path)
    assert "first-objective policy" in standing
    assert "OBJECTIVE.md changed on" in standing


def test_inbox_retract_retires_matching_directive_only(tmp_path: Path) -> None:
    _set_objective(tmp_path, "prove the theorem")
    append_steering_directive(tmp_path, "keep the proof structural")
    append_steering_directive(tmp_path, "run the focused verifier")

    append_steering_directive(tmp_path, "retract: focused verifier")

    standing = active_manager_directive_message(tmp_path)
    assert "keep the proof structural" in standing
    assert "run the focused verifier" not in standing
    records = [
        json.loads(line)
        for line in (tmp_path / STEERING_LEDGER_FILENAME).read_text().splitlines()
    ]
    assert records[-1]["kind"] == "retraction"
    assert records[-1]["retired_ids"]


def test_standing_render_is_newest_first_and_budget_capped(tmp_path: Path) -> None:
    _set_objective(tmp_path, "prove the theorem")
    for index in range(12):
        append_steering_directive(
            tmp_path,
            f"directive-{index:02d} " + ("x" * 450),
        )

    standing = active_manager_directive_message(tmp_path)

    assert len(standing) <= 4000
    assert "directive-11" in standing
    assert "directive-00" not in standing
    assert standing.index("directive-11") < standing.index("directive-10")


def test_empty_directive_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        set_active_manager_directive(tmp_path, "  ")


def test_malformed_directive_is_ignored(tmp_path: Path) -> None:
    (tmp_path / ACTIVE_MANAGER_DIRECTIVE_FILENAME).write_text(
        "{not json",
        encoding="utf-8",
    )

    assert load_active_manager_directive(tmp_path) is None


def test_old_directive_fields_are_ignored(tmp_path: Path) -> None:
    record = set_active_manager_directive(tmp_path, "continue the current route")
    payload = json.loads(
        (tmp_path / ACTIVE_MANAGER_DIRECTIVE_FILENAME).read_text(encoding="utf-8")
    )
    payload.pop("authorized_objective")
    payload["operator_question_policy"] = "forbid"  # retired field
    (tmp_path / ACTIVE_MANAGER_DIRECTIVE_FILENAME).write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    loaded = load_active_manager_directive(tmp_path)

    assert loaded is not None
    assert loaded.text == record.text
    assert not hasattr(loaded, "operator_question_policy")


def test_standing_steer_authorizes_final_continuous_objective(tmp_path: Path) -> None:
    from argus.daemon.state import (
        read_continuous_state,
        write_continuous_config,
    )
    from argus.life.memory import BacklogItem, LifeMemory
    from argus.webapi.manager_dispatch import (
        _handle_steer_control,
        _TurnEmitter,
    )

    write_continuous_config(
        tmp_path,
        enabled=True,
        objective="old bounded objective",
        open_ended=False,
    )
    set_active_manager_directive(tmp_path, "existing direction")
    memory = LifeMemory.open(tmp_path)
    active = memory.backlog.add(
        BacklogItem.new(
            title="active mission",
            objective="final standing objective",
        )
    )
    memory.backlog.mark_running(active.id)
    emitter = _TurnEmitter(tmp_path, "turn-1", lambda *_args: None)

    result = _handle_steer_control(
        tmp_path,
        emitter,
        chat_state={},
        body="continue autonomously",
        directive="continue autonomously",
        lifetime="standing",
    )

    continuous = read_continuous_state(tmp_path)
    directive = load_active_manager_directive(tmp_path)
    assert result["control"] == "steer"
    assert continuous.objective == "final standing objective"
    assert continuous.open_ended is True
    assert directive is not None
    assert directive.objective == "old bounded objective"
    assert directive.authorized_objective == continuous.objective
    payload = json.loads(
        (tmp_path / ACTIVE_MANAGER_DIRECTIVE_FILENAME).read_text(encoding="utf-8")
    )
    assert payload["authorized_objective"] == "final standing objective"
    assert "authorized_objective_sha256" not in payload
    write_continuous_config(
        tmp_path,
        enabled=True,
        objective="unrelated replacement objective",
        open_ended=True,
    )
    assert load_active_manager_directive(tmp_path) is None


def test_failed_standing_cas_keeps_directive_and_refreshes_chat_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from argus.daemon import state as daemon_state
    from argus.webapi.manager_dispatch import (
        _TurnEmitter,
    )

    daemon_state.write_continuous_config(
        tmp_path,
        enabled=True,
        objective="already standing",
        open_ended=True,
    )
    monkeypatch.setattr(
        daemon_state,
        "compare_and_swap_continuous_config",
        lambda *_args, **_kwargs: False,
    )

    chat_state = {
        "_frontdoor_steering_directive": "replace the active direction",
        "_frontdoor_lifetime": "standing",
        "config": {"continuous": False},
        "continuous_objective": "stale cache",
    }
    result = _steer(
        chat_state,
        tmp_path,
        _TurnEmitter(tmp_path, "turn-2", lambda *_args: None),
    )

    assert result["control"] == "steer"
    assert result["continuous"] is False
    assert "The direction is noted" in result["reply"]
    directive = load_active_manager_directive(tmp_path)
    assert directive is not None
    assert chat_state["config"]["continuous"] is True
    assert chat_state["continuous_objective"] == "already standing"
    assert (tmp_path / "inbox.jsonl").exists()


def test_concurrent_replacement_steer_is_stale_and_not_queued(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from argus.daemon import state as daemon_state
    from argus.life.memory import BacklogItem, LifeMemory
    from argus.manager import directive as directive_module
    from argus.webapi.manager_dispatch import (
        _TurnEmitter,
    )

    daemon_state.write_continuous_config(
        tmp_path,
        enabled=True,
        objective="objective A",
        open_ended=False,
    )
    memory = LifeMemory.open(tmp_path)
    active = memory.backlog.add(BacklogItem.new(
        title="active mission",
        objective="authorized standing objective",
    ))
    memory.backlog.mark_running(active.id)
    original_set = directive_module.set_active_manager_directive

    def replace_objective_before_directive(*args, **kwargs):
        daemon_state.write_continuous_config(
            tmp_path,
            enabled=True,
            objective="objective B",
            open_ended=True,
        )
        return original_set(*args, **kwargs)

    monkeypatch.setattr(
        directive_module,
        "set_active_manager_directive",
        replace_objective_before_directive,
    )
    chat_state = {
        "_frontdoor_steering_directive": "steer objective A",
        "_frontdoor_lifetime": "standing",
        "config": {"continuous": False},
        "continuous_objective": "stale cache",
    }

    result = _steer(
        chat_state,
        tmp_path,
        _TurnEmitter(tmp_path, "turn-race", lambda *_args: None),
    )

    payload = json.loads(
        (tmp_path / ACTIVE_MANAGER_DIRECTIVE_FILENAME).read_text(encoding="utf-8")
    )
    assert result["continuous"] is False
    assert payload["objective"] == "objective A"
    assert payload["authorized_objective"] == "authorized standing objective"
    assert load_active_manager_directive(tmp_path) is None
    assert (tmp_path / "inbox.jsonl").exists()
    assert "steer objective A" in active_manager_directive_message(tmp_path)
    assert chat_state["config"]["continuous"] is True
    assert chat_state["continuous_objective"] == "objective B"


def test_directive_write_failure_happens_before_standing_cas(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from argus.daemon import state as daemon_state
    from argus.manager import directive as directive_module
    from argus.webapi.manager_dispatch import (
        _TurnEmitter,
    )

    daemon_state.write_continuous_config(
        tmp_path,
        enabled=True,
        objective="bounded objective",
        open_ended=False,
    )
    before = daemon_state.read_continuous_state(tmp_path)
    cas_calls = 0

    def fail_write(*_args, **_kwargs):
        raise OSError("directive write failed")

    def unexpected_cas(*_args, **_kwargs):
        nonlocal cas_calls
        cas_calls += 1
        return True

    monkeypatch.setattr(
        directive_module,
        "set_active_manager_directive",
        fail_write,
    )
    monkeypatch.setattr(
        daemon_state,
        "compare_and_swap_continuous_config",
        unexpected_cas,
    )
    with pytest.raises(OSError, match="directive write failed"):
        _steer(
            {
                "_frontdoor_steering_directive": "promote safely",
                "_frontdoor_lifetime": "standing",
            },
            tmp_path,
            _TurnEmitter(tmp_path, "turn-3", lambda *_args: None),
        )

    after = daemon_state.read_continuous_state(tmp_path)
    assert cas_calls == 0
    assert after == before
    assert after.generation == before.generation
    assert load_active_manager_directive(tmp_path) is None
