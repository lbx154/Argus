"""Outside refusals and failed publishes must not re-buy work without end.

A teammate stopped by the provider went back to the queue every two minutes for
as long as the provider refused, each time as a freshly spawned teammate; and a
team summary whose publish failed was rewritten by the Manager on every tick.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from argus.core.autonomy import AUTOMATIC_RETRY_WINDOW_SECONDS
from argus.team import completion, registry, task_board


def _refused(root: Path, *, now: float, kind: str = "provider_cooldown") -> float | None:
    task = task_board.claim_top(root, "worker", now=now)
    assert task is not None
    return task_board.release_paused(
        root, task["task_id"], reason=f"stop_kind={kind}", retry_after=now + 120.0,
        kind=kind, now=now,
    )


def test_the_same_refusal_waits_longer_each_time_then_asks_the_operator(tmp_path: Path) -> None:
    task_board.form(tmp_path, [{"task_id": "t::a", "objective": "x"}])
    now, waits = 1_000.0, []
    while True:
        retry_at = _refused(tmp_path, now=now)
        if retry_at is None:
            break
        waits.append(retry_at - now)
        now = retry_at

    assert waits[:3] == [120.0, 240.0, 480.0] and max(waits) == 3600.0
    assert len(waits) < 10, "a few retries in three hours, not one every two minutes"
    task = {row["task_id"]: row for row in task_board.snapshot(tmp_path)}["t::a"]
    assert task["state"] == "blocked" and "three hours" in task["pending_question"]
    assert now - 1_000.0 >= AUTOMATIC_RETRY_WINDOW_SECONDS
    assert task_board.claim_top(tmp_path, "worker", now=now + 86_400.0) is None

    task_board.resume(tmp_path, "t::a", answer="Retry it.")
    assert _refused(tmp_path, now=now + 90_000.0) == now + 90_000.0 + 120.0


def test_a_different_refusal_starts_its_own_window(tmp_path: Path) -> None:
    task_board.form(tmp_path, [{"task_id": "t::a", "objective": "x"}])
    first = _refused(tmp_path, now=1_000.0)
    second = _refused(tmp_path, now=first, kind="budget_exhausted")
    assert second == first + 120.0


def test_a_failed_publish_does_not_ask_the_manager_again(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "team"
    conversation = tmp_path / "conversation"
    marker = registry.write_marker(tmp_path, team_id="t1", team_root=root, cwd=tmp_path, now=1.0)
    task_board.form(root, [{"task_id": "t::a", "title": "audit", "objective": "x"}])
    task_board.complete(root, "t::a")
    prompts: list[str] = []
    publish = completion.publish_operator_message
    monkeypatch.setattr(completion, "publish_operator_message", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    marker = marker if isinstance(marker, dict) else registry.list_markers(tmp_path)[0]

    def summarize(prompt: str) -> str:
        prompts.append(prompt)
        return "The audit is done."

    clock = [1_000.0]
    monkeypatch.setattr(completion, "time", SimpleNamespace(time=lambda: clock[0]))
    raised = 0
    # Twelve hours of Curator ticks, five seconds apart.
    for _ in range(12 * 720):
        try:
            completion.publish_if_complete(
                root, marker=marker, conversation_root=conversation, summarize=summarize,
            )
        except OSError:
            raised += 1
        clock[0] += 5.0
    # One minute doubling to an hour: a handful of tries, not one per tick.
    assert raised <= 18
    monkeypatch.setattr(completion, "publish_operator_message", publish)
    clock[0] += 3600.0
    assert completion.publish_if_complete(
        root, marker=marker, conversation_root=conversation, summarize=summarize,
    ) is True
    assert len(prompts) == 1


def test_a_disk_that_cannot_keep_the_summary_does_not_ask_the_manager_again(
    tmp_path: Path, monkeypatch,
) -> None:
    root = tmp_path / "team"
    marker = registry.write_marker(tmp_path, team_id="t1", team_root=root, cwd=tmp_path, now=1.0)
    task_board.form(root, [{"task_id": "t::a", "title": "audit", "objective": "x"}])
    task_board.complete(root, "t::a")
    marker = marker if isinstance(marker, dict) else registry.list_markers(tmp_path)[0]
    clock = [1_000.0]
    monkeypatch.setattr(completion, "time", SimpleNamespace(time=lambda: clock[0]))
    monkeypatch.setattr(completion._store, "atomic_write_json",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no space left on device")))
    prompts: list[str] = []

    for _ in range(720):
        try:
            completion.publish_if_complete(
                root, marker=marker, conversation_root=tmp_path / "conversation",
                summarize=lambda prompt: prompts.append(prompt) or "The audit is done.",
            )
        except OSError:
            pass
        clock[0] += 5.0

    assert len(prompts) == 1
