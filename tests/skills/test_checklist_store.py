"""Read-only compatibility tests for historical project checklist stores."""

from __future__ import annotations

import json

import pytest

from argus_skill.skills import checklist_store as cs
from argus_skill.skills.vertical_select import persist_vertical


@pytest.fixture(autouse=True)
def _research_vertical(tmp_path) -> None:
    persist_vertical(tmp_path, "research")


def _write_store(
    root,
    *,
    vertical: str,
    stages: dict,
    disabled: dict | None = None,
    revision: int = 1,
) -> None:
    path = root / "research" / "CHECKLISTS.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "revision": revision,
        "vertical": vertical,
        "stages": stages,
    }
    if disabled is not None:
        payload["disabled"] = disabled
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_absent_stage_falls_back_but_historical_custom_rows_are_merged(tmp_path) -> None:
    assert cs.store_items_for_stage(tmp_path, "scope") is None

    _write_store(
        tmp_path,
        vertical="research",
        stages={
            "scope": [
                {
                    "id": "scope.custom",
                    "statement": "State the project scope.",
                    "evidence_hint": "scope/README.md",
                }
            ]
        },
    )

    ids = {item.id for item in cs.store_items_for_stage(tmp_path, "scope")}
    assert "scope.custom" in ids
    assert cs.store_items_for_stage(tmp_path, "simulate") is None


def test_malformed_historical_rows_are_dropped(tmp_path) -> None:
    _write_store(
        tmp_path,
        vertical="research",
        stages={
            "scope": [
                {"id": "good", "statement": "ok", "evidence_hint": ""},
                {"id": "", "statement": "no id"},
                {"statement": "no id key"},
                "not a dict",
            ]
        },
    )

    assert [item.id for item in cs.store_items_for_stage(tmp_path, "scope")] == ["good"]


def test_protected_floor_is_reinjected_when_historical_store_is_empty(tmp_path) -> None:
    _write_store(tmp_path, vertical="research", stages={"run": []})

    ids = {item.id for item in cs.store_items_for_stage(tmp_path, "run")}
    assert "run.score_variance" in ids


def test_protected_floor_is_restored_to_canonical_text(tmp_path) -> None:
    canonical = {item.id: item.statement for item in cs.seed_items_for(tmp_path, "run")}
    _write_store(
        tmp_path,
        vertical="research",
        stages={
            "run": [
                {
                    "id": "run.score_variance",
                    "statement": "N/A trivially satisfied",
                    "evidence_hint": "",
                }
            ]
        },
    )

    by_id = {item.id: item.statement for item in cs.store_items_for_stage(tmp_path, "run")}
    assert by_id["run.score_variance"] == canonical["run.score_variance"]


def test_legacy_tombstones_hide_unprotected_seed_but_not_protected_floor(tmp_path) -> None:
    persist_vertical(tmp_path, "math")
    _write_store(
        tmp_path,
        vertical="math",
        stages={"review": []},
        disabled={
            "review": ["review.argument-correct", "review.goal-achieved"],
        },
    )

    ids = {item.id for item in cs.store_items_for_stage(tmp_path, "review")}
    assert "review.argument-correct" not in ids
    assert "review.goal-achieved" in ids
    assert "review.statement-fidelity" in ids


def test_legacy_store_without_disabled_field_keeps_seeds_and_custom_rows(tmp_path) -> None:
    persist_vertical(tmp_path, "math")
    _write_store(
        tmp_path,
        vertical="math",
        stages={
            "review": [
                {
                    "id": "review.current-certificate-replay",
                    "statement": "No certificate replay.",
                    "evidence_hint": "review/CERTIFICATE.json",
                }
            ]
        },
        disabled=None,
    )

    ids = {item.id for item in cs.store_items_for_stage(tmp_path, "review")}
    assert "review.current-certificate-replay" in ids
    assert "review.argument-correct" in ids
    assert "review.outcome-honest" in ids


def test_store_for_previous_vertical_is_ignored(tmp_path) -> None:
    _write_store(
        tmp_path,
        vertical="research",
        stages={
            "research": [
                {
                    "id": "research.only",
                    "statement": "paper-only gate",
                    "evidence_hint": "research/PAPER.md",
                }
            ]
        },
    )
    assert cs.store_items_for_stage(tmp_path, "research") is not None

    persist_vertical(tmp_path, "math")
    assert cs.store_items_for_stage(tmp_path, "research") is None
