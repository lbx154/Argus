"""Roles read the same state now that derived files are no longer written.

``tests/fixtures/derived_state_prompts.json`` holds the prompt texts the
runtime produced for ``tests/state_snapshot_project.py`` while it still wrote
``mission-view.json``, the handoff ``latest.json`` pointers and
``manager-supervision/latest.json``. Every text must be unchanged except where
a prompt named a derived file: it now names the record that file was derived
from. Those replacements are listed here, and nothing else may differ.
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.state_snapshot_project import build

RECORDED = Path(__file__).parent / "fixtures" / "derived_state_prompts.json"

# (prompt, text before, text now): the only differences allowed.
REFERENCE_CHANGES = [
    # The Manager cites the event log the Mission View is computed from.
    ("manager_observation", '{"path": "mission-view.json"}', '{"path": "events.jsonl"}'),
]


def test_prompts_read_the_same_state_as_before(tmp_path, monkeypatch) -> None:
    expected = json.loads(RECORDED.read_text(encoding="utf-8"))
    for name, before, now in REFERENCE_CHANGES:
        assert before in expected[name], (name, before)
        expected[name] = expected[name].replace(before, now)

    assert build(tmp_path / "project", monkeypatch) == expected
