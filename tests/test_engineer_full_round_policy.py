"""Which Engineer rounds re-send the full static task text.

The 48-hour billing forensics found 75% of Engineer rounds were FULL —
每轮 ~8.7k tokens — while a resumed provider session already holds the whole
task text from the round that created it. The full text is re-sent only when
the session cannot already hold it: a new or rotated session, a fresh-only
policy, a stage that changed since the last full prompt, or a first round
resuming a session with no sealed prior round for this mission.
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
    mission_path: Path | None = None,
    checkpoint: Path | None = None,
    prompt_block: str = "",
    mixin: object | None = None,
    events: list | None = None,
) -> str:
    from argus.engineer.round_prompt import RoundPromptMixin

    role_session = SimpleNamespace(
        policy=policy,
        action=action,
        rotation_reason=rotation_reason,
        prompt_block=lambda: prompt_block,
    )
    config = SimpleNamespace(
        max_rounds=8,
        context_packet_path=str(mission_path or ""),
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


def test_a_stage_change_forces_the_full_text_on_a_resumed_session(
    tmp_path: Path,
) -> None:
    """The stage picks the role banner and skills inside the static text, so a
    session whose last full prompt described another stage must be re-briefed."""
    from argus.engineer.round_prompt import RoundPromptMixin

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


# --- the compact prompt still names the durable files ------------------------
