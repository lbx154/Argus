from __future__ import annotations

from pathlib import Path

from argus.core.usage import UsageSummary
from argus.webapi import mission_items, project_state


def _usage_summary() -> UsageSummary:
    return UsageSummary(
        call_count=0,
        known_cost_usd=0.0,
        cost_usd=None,
        pricing_status="empty",
        priced_calls=0,
        partial_calls=0,
        unpriced_calls=0,
        not_billed_calls=0,
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        reasoning_output_tokens=0,
        premium_requests=0.0,
    )


def test_project_state_caches_evict_old_project_and_root_keys(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(project_state, "_HOST_CACHE_MAX_ENTRIES", 2)
    monkeypatch.setattr(project_state, "_PROJECT_CACHE_MAX_ENTRIES", 2)
    monkeypatch.setattr(
        project_state,
        "metrics_snapshot",
        lambda *, root, cost_control=None: {"root": str(root)},
    )
    monkeypatch.setattr(
        project_state,
        "project_usage_summary",
        lambda _root: _usage_summary(),
    )
    for cache, lock in (
        (project_state._METRICS_CACHE, project_state._METRICS_CACHE_LOCK),
        (project_state._COST_CONTROL_CACHE, project_state._COST_CONTROL_CACHE_LOCK),
        (project_state._GLOBAL_USAGE_CACHE, project_state._GLOBAL_USAGE_CACHE_LOCK),
        (project_state._SPEND_CACHE, project_state._SPEND_CACHE_LOCK),
    ):
        with lock:
            cache.clear()

    roots = [tmp_path / f"root-{index}" for index in range(5)]
    projects = [tmp_path / f"project-{index}" for index in range(5)]
    for root in roots:
        project_state._cached_metrics_snapshot(root)
        project_state._store_cost_control_cache(str(root.resolve()), {"root": str(root)})
        project_state._store_global_usage_cache(str(root.resolve()), _usage_summary())
    for project in projects:
        project.mkdir()
        project_state.settled_spend(None, project)

    assert len(project_state._METRICS_CACHE) == 2
    assert len(project_state._COST_CONTROL_CACHE) == 2
    assert len(project_state._GLOBAL_USAGE_CACHE) == 2
    assert len(project_state._SPEND_CACHE) == 2
    assert set(project_state._METRICS_CACHE) == {
        str(root.resolve()) for root in roots[-2:]
    }
    assert set(project_state._SPEND_CACHE) == {
        str(project.resolve()) for project in projects[-2:]
    }


def test_journal_tail_cache_evicts_old_sessions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(mission_items, "_JOURNAL_TAIL_CACHE_MAX_ENTRIES", 2)
    with mission_items._JOURNAL_TAIL_CACHE_LOCK:
        mission_items._JOURNAL_TAIL_CACHE.clear()

    session_ids = [f"s-cache-{index}" for index in range(5)]
    for sid in session_ids:
        life_dir = tmp_path / "projects" / sid
        life_dir.mkdir(parents=True)
        (life_dir / mission_items.EVENT_FILE).write_text("", encoding="utf-8")
        assert mission_items.get_journal(sid, global_root=tmp_path) == []

    assert len(mission_items._JOURNAL_TAIL_CACHE) == 2
    assert {key[0] for key in mission_items._JOURNAL_TAIL_CACHE} == {
        str((tmp_path / "projects" / sid).resolve())
        for sid in session_ids[-2:]
    }
