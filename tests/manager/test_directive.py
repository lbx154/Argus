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
