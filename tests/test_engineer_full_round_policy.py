"""Which Engineer rounds re-send the full static task text.

The 48-hour billing forensics found 75% of Engineer rounds were FULL —
每轮 ~8.7k tokens — while a resumed provider session already holds the whole
task text from the round that created it. The ``session`` policy (default)
re-sends the full text only when the session cannot already hold it: a new or
rotated session, a fresh-only policy, a stage that changed since the last full
prompt, or a first round resuming a session with no sealed prior round for
this mission. ``legacy`` restores the historical round-1-always-full behavior
for rollback.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace


def _mission_file(
    root: Path,
    *,
    stage: str = "research",
    sealed_rounds: int = 0,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    mission = root / "mission.json"
    mission.write_text(
        json.dumps({
            "kind": "mission_context",
            "mission_id": root.name,
            "stage": stage,
            "objective": "measure the effect",
            "execution_workdir": str(root),
        }),
        encoding="utf-8",
    )
    for index in range(1, sealed_rounds + 1):
        (root / f"round-{index:04d}.json").write_text(
            json.dumps({
                "kind": "round_reviewed_handoff",
                "round": index,
                "review": {"status": "continue", "next_action": "keep going"},
            }),
            encoding="utf-8",
        )
    return mission


def _assemble(
    *,
    round_index: int,
    workdir: Path,
    action: str = "resumed",
    policy: str = "rolling",
    rotation_reason: str = "",
    full_round_policy: str = "session",
    compact_continuation_prompts: bool = True,
    mission_path: Path | None = None,
    checkpoint: Path | None = None,
    prompt_block: str = "",
    mixin: object | None = None,
    events: list | None = None,
) -> str:
    from argus_skill.engineer.round_prompt import RoundPromptMixin

    role_session = SimpleNamespace(
        policy=policy,
        action=action,
        rotation_reason=rotation_reason,
        prompt_block=lambda: prompt_block,
    )
    config = SimpleNamespace(
        compact_continuation_prompts=compact_continuation_prompts,
        background_subagent_advisory=False,
        max_rounds=8,
        context_packet_path=str(mission_path or ""),
        engineer_full_round_policy=full_round_policy,
    )
    return (mixin or RoundPromptMixin())._assemble_round_prompt(
        round_index=round_index,
        supervised_config=config,
        engineer_prompt_builder=(
            lambda _next_action, include_static: (
                "FULL_STATIC_TEXT" if include_static else "COMPACT_DELTA_TEXT"
            )
        ),
        reviewer_next_action=None,
        checkpoint_path=checkpoint,
        workdir=workdir,
        role_session=role_session,
        on_event=(events.append if events is not None else None),
    )


def _mode(events: list) -> tuple[str, str]:
    payload = next(e for e in events if str(e.get("type", "")).endswith("round.start"))
    return payload["prompt_mode"], payload["prompt_mode_reason"]


# --- the decision itself -----------------------------------------------------


def test_first_round_on_a_resumed_session_with_prior_rounds_goes_compact(
    tmp_path: Path,
) -> None:
    """A resumed session already received the full text when it was created;
    a mission restart (round 1 again, same session, sealed prior rounds)
    must not re-pay the whole static prompt."""
    mission = _mission_file(tmp_path / "m", sealed_rounds=2)
    events: list = []

    prompt = _assemble(
        round_index=1,
        workdir=tmp_path,
        mission_path=mission,
        events=events,
    )

    assert "COMPACT_DELTA_TEXT" in prompt
    assert _mode(events) == ("compact", "resumed_session_with_prior_rounds")


def test_first_round_on_a_resumed_session_without_prior_rounds_stays_full(
    tmp_path: Path,
) -> None:
    """No sealed round for this mission means the resumed session may hold a
    DIFFERENT mission's text (same objective wording, new mission directory):
    send the full text rather than trust the coincidence."""
    mission = _mission_file(tmp_path / "m", sealed_rounds=0)
    events: list = []

    prompt = _assemble(
        round_index=1,
        workdir=tmp_path,
        mission_path=mission,
        events=events,
    )

    assert "FULL_STATIC_TEXT" in prompt
    assert _mode(events) == ("full", "first_round_without_prior_rounds")


def test_later_resumed_rounds_stay_compact(tmp_path: Path) -> None:
    mission = _mission_file(tmp_path / "m", sealed_rounds=1)
    events: list = []

    prompt = _assemble(
        round_index=3,
        workdir=tmp_path,
        mission_path=mission,
        events=events,
    )

    assert "COMPACT_DELTA_TEXT" in prompt
    assert _mode(events) == ("compact", "resumed_session")


def test_a_new_session_gets_the_full_text(tmp_path: Path) -> None:
    events: list = []

    prompt = _assemble(
        round_index=1,
        workdir=tmp_path,
        action="fresh",
        events=events,
    )

    assert "FULL_STATIC_TEXT" in prompt
    assert _mode(events) == ("full", "session_fresh")


def test_a_rotated_session_gets_the_full_text_and_names_the_reason(
    tmp_path: Path,
) -> None:
    events: list = []

    prompt = _assemble(
        round_index=4,
        workdir=tmp_path,
        action="rotated",
        rotation_reason="context_limit",
        events=events,
    )

    assert "FULL_STATIC_TEXT" in prompt
    assert _mode(events) == ("full", "session_rotated:context_limit")


def test_fresh_policy_sends_the_full_text_every_round(tmp_path: Path) -> None:
    events: list = []

    prompt = _assemble(
        round_index=5,
        workdir=tmp_path,
        policy="fresh",
        action="fresh",
        events=events,
    )

    assert "FULL_STATIC_TEXT" in prompt
    assert _mode(events) == ("full", "fresh_policy")


def test_disabled_compact_knob_sends_the_full_text(tmp_path: Path) -> None:
    events: list = []

    prompt = _assemble(
        round_index=3,
        workdir=tmp_path,
        compact_continuation_prompts=False,
        events=events,
    )

    assert "FULL_STATIC_TEXT" in prompt
    assert _mode(events) == ("full", "compact_prompts_disabled")


def test_a_stage_change_forces_the_full_text_on_a_resumed_session(
    tmp_path: Path,
) -> None:
    """The stage picks the role banner and skills inside the static text, so a
    session whose last full prompt described another stage must be re-briefed."""
    from argus_skill.engineer.round_prompt import RoundPromptMixin

    mission = _mission_file(tmp_path / "m", stage="research", sealed_rounds=1)
    mixin = RoundPromptMixin()
    first_events: list = []
    _assemble(
        round_index=1,
        workdir=tmp_path,
        action="fresh",
        mission_path=mission,
        mixin=mixin,
        events=first_events,
    )
    assert _mode(first_events)[0] == "full"

    _mission_file(tmp_path / "m", stage="experiment", sealed_rounds=1)
    events: list = []
    prompt = _assemble(
        round_index=3,
        workdir=tmp_path,
        mission_path=mission,
        mixin=mixin,
        events=events,
    )

    assert "FULL_STATIC_TEXT" in prompt
    assert _mode(events) == ("full", "stage_changed")


def test_an_unchanged_stage_does_not_force_the_full_text(tmp_path: Path) -> None:
    from argus_skill.engineer.round_prompt import RoundPromptMixin

    mission = _mission_file(tmp_path / "m", stage="research", sealed_rounds=1)
    mixin = RoundPromptMixin()
    _assemble(
        round_index=1,
        workdir=tmp_path,
        action="fresh",
        mission_path=mission,
        mixin=mixin,
    )

    events: list = []
    prompt = _assemble(
        round_index=2,
        workdir=tmp_path,
        mission_path=mission,
        mixin=mixin,
        events=events,
    )

    assert "COMPACT_DELTA_TEXT" in prompt
    assert _mode(events)[0] == "compact"


def test_legacy_policy_restores_round_one_full(tmp_path: Path) -> None:
    mission = _mission_file(tmp_path / "m", sealed_rounds=2)
    events: list = []

    prompt = _assemble(
        round_index=1,
        workdir=tmp_path,
        mission_path=mission,
        full_round_policy="legacy",
        events=events,
    )

    assert "FULL_STATIC_TEXT" in prompt
    assert _mode(events) == ("full", "first_round_legacy")


def test_legacy_policy_keeps_later_resumed_rounds_compact(tmp_path: Path) -> None:
    events: list = []

    prompt = _assemble(
        round_index=2,
        workdir=tmp_path,
        full_round_policy="legacy",
        events=events,
    )

    assert "COMPACT_DELTA_TEXT" in prompt
    assert _mode(events)[0] == "compact"


# --- the compact prompt still names the durable files ------------------------


def test_compact_round_names_the_mission_files_when_no_capsule_block(
    tmp_path: Path,
) -> None:
    """A compact continuation must point back at the files that carry the
    task's terms and current state, so the model can read them itself."""
    mission = _mission_file(tmp_path / "m", sealed_rounds=1)

    prompt = _assemble(
        round_index=2,
        workdir=tmp_path,
        mission_path=mission,
    )

    assert str(mission) in prompt
    assert str(mission.parent / "latest.json") in prompt


def test_compact_round_does_not_duplicate_an_existing_capsule_block(
    tmp_path: Path,
) -> None:
    mission = _mission_file(tmp_path / "m", sealed_rounds=1)
    capsule_block = (
        "## Role state references\n"
        f"Mission contract: `{mission}`"
    )

    prompt = _assemble(
        round_index=2,
        workdir=tmp_path,
        mission_path=mission,
        prompt_block=capsule_block,
    )

    assert prompt.count(str(mission)) == 1


def test_full_round_needs_no_extra_reference_block(tmp_path: Path) -> None:
    mission = _mission_file(tmp_path / "m", sealed_rounds=0)

    prompt = _assemble(
        round_index=1,
        workdir=tmp_path,
        action="fresh",
        mission_path=mission,
    )

    assert "latest.json" not in prompt


# --- configuration plumbing ---------------------------------------------------


def test_configured_policy_reads_the_env_knob(monkeypatch) -> None:
    from argus_skill.engineer.round_config import (
        configured_engineer_full_round_policy,
    )

    monkeypatch.delenv("ARGUS_SKILL_ENGINEER_FULL_ROUND_POLICY", raising=False)
    assert configured_engineer_full_round_policy() == "session"

    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_FULL_ROUND_POLICY", "legacy")
    assert configured_engineer_full_round_policy() == "legacy"

    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_FULL_ROUND_POLICY", "banana")
    assert configured_engineer_full_round_policy() == "session"


def test_supervised_config_carries_the_policy(monkeypatch) -> None:
    from argus_skill.engineer.round_config import SupervisedConfig

    monkeypatch.delenv("ARGUS_SKILL_ENGINEER_FULL_ROUND_POLICY", raising=False)
    assert SupervisedConfig().engineer_full_round_policy == "session"

    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_FULL_ROUND_POLICY", "legacy")
    assert SupervisedConfig().engineer_full_round_policy == "legacy"


def test_supervised_config_rejects_an_unknown_policy() -> None:
    import pytest

    from argus_skill.engineer.round_config import SupervisedConfig

    with pytest.raises(ValueError, match="session or legacy"):
        SupervisedConfig(engineer_full_round_policy="banana")


def test_knob_registry_documents_the_policy() -> None:
    from argus_skill.core.knobs import KNOBS

    knob = next(
        k for k in KNOBS if k.name == "ARGUS_SKILL_ENGINEER_FULL_ROUND_POLICY"
    )
    assert knob.default == "session"
    assert knob.group == "mission"
