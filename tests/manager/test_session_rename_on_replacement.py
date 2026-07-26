"""A long-lived session should not keep the name of its first toy task.

Operator report (2026-07-26): a session was still labelled after a small
arithmetic question long after it had moved on to unrelated complex work, so the
resume picker showed a name that told you nothing about what the session had
become.

Naming on every task would churn the picker instead. The event worth renaming on
already exists: the Manager records when a new operator objective *supersedes*
the standing one — the same event it resets the pipeline for. Anything additive
(a clarification, an authorisation, an extra constraint) is explicitly not that.
"""

from __future__ import annotations

from pathlib import Path

from argus_skill.manager.front_door import (
    _maybe_name_session,
    objective_update_requires_stage_reset,
)


def _state(tmp_path: Path, sid: str = "s-1") -> dict:
    # Create the session through the real path helper: touch_session refuses to
    # write metadata for a session directory that does not exist, so a
    # hand-made fixture would silently test nothing.
    from argus_skill.core import paths as core_paths

    core_paths.session_state_root(sid, root=tmp_path).mkdir(parents=True, exist_ok=True)
    return {
        "session_id": sid,
        "global_root": tmp_path,
        "session_named": False,
    }


def _name(tmp_path: Path, sid: str = "s-1") -> str:
    from argus_skill.core.session import read_session_meta

    meta = read_session_meta(tmp_path, sid)
    return "" if meta is None else meta.display_name


# -- the behaviour that was reported ----------------------------------------


def test_an_ordinary_follow_up_task_does_not_rename_the_session(
    tmp_path: Path,
) -> None:
    """Renaming on every task would make the picker churn."""
    state = _state(tmp_path)
    _maybe_name_session(state, "evaluate 2 + 2")
    first = _name(tmp_path)

    _maybe_name_session(state, "now write a JSONL log analyser with tests")

    assert first
    assert _name(tmp_path) == first


def test_replacing_the_standing_objective_renames_the_session(
    tmp_path: Path,
) -> None:
    """The session is about something else now; the old label is simply wrong."""
    state = _state(tmp_path)
    _maybe_name_session(state, "evaluate 2 + 2")
    first = _name(tmp_path)

    _maybe_name_session(
        state,
        "build a Roman numeral converter with subtractive-case tests",
        replacing=True,
    )

    assert _name(tmp_path) != first
    assert "Roman numeral" in _name(tmp_path)


def test_a_rename_survives_an_already_persisted_name(tmp_path: Path) -> None:
    """The persisted name used to short-circuit before the rename could run."""
    state = _state(tmp_path)
    _maybe_name_session(state, "evaluate 2 + 2")
    # A fresh chat_state, as a reconnecting cockpit would have.
    reconnected = _state(tmp_path)

    _maybe_name_session(reconnected, "something entirely different", replacing=True)

    assert _name(tmp_path) == "something entirely different"


# -- and it fires on the right event ----------------------------------------


def test_an_additive_clarification_is_not_a_replacement() -> None:
    """Otherwise every clarification would rename the session."""
    assert not objective_update_requires_stage_reset(
        "optimise the attention kernel",
        "optimise the attention kernel on B200",
    )


def test_a_genuinely_different_objective_is_a_replacement() -> None:
    assert objective_update_requires_stage_reset(
        "optimise the attention kernel",
        "write a Roman numeral converter",
    )
