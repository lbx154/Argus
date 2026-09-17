from __future__ import annotations

import json

from argus.verticals.research.venue_research import (
    _build_prompt,
    needs_venue_research,
)


def _state(tmp_path, *, target_venue: str | None) -> None:
    research = tmp_path / "research"
    research.mkdir()
    payload = {"vertical": "research", "current_stage": "research"}
    if target_venue is not None:
        payload["target_venue"] = target_venue
    (research / "PIPELINE_STATE.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def test_missing_venue_never_triggers_automatic_discovery(tmp_path) -> None:
    _state(tmp_path, target_venue=None)

    assert needs_venue_research(tmp_path) is False


def test_explicit_unknown_venue_requests_profile_research(tmp_path) -> None:
    _state(tmp_path, target_venue="ExampleConf")

    assert needs_venue_research(tmp_path) is True


def test_explicit_venue_prompt_cannot_select_alternatives() -> None:
    prompt = _build_prompt("ExampleConf")

    assert "explicitly selected this publication venue: ExampleConf" in prompt
    assert "Do not search for or select alternatives" in prompt
    assert "CCF-A" not in prompt
