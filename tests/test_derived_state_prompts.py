"""Roles read the same state now that derived files are no longer written.

``tests/fixtures/derived_state_prompts.json`` holds the prompt texts the
runtime produced for ``tests/state_snapshot_project.py`` while it still wrote
``mission-view.json`` and ``manager-supervision/latest.json`` and read the
handoff ``latest.json`` pointer. Every text must be unchanged except where a
prompt named a file that is no longer written: it now names the record that
file was derived from. Those replacements are listed here; nothing else may
differ. Prompts still name ``handoffs/<id>/latest.json``, which stays as a
stable reference to the newest round record.
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.state_snapshot_project import build

RECORDED = Path(__file__).parent / "fixtures" / "derived_state_prompts.json"

# (prompt, text before, text now): the only differences allowed.
HANDOFFS = "<root>/handoffs/a1b2c3d4e5f6/"
SUPERVISION = "manager-supervision/00000000000000000000000000000001.json"
REFERENCE_CHANGES = [
    # The Manager cites the event log the Mission View is computed from.
    ("manager_observation", '{"path": "mission-view.json"}', '{"path": "events.jsonl"}'),
    ("manager_observation_before_rounds", '{"path": "mission-view.json"}', '{"path": "events.jsonl"}'),
    # Before the first round handoffs/<id>/latest.json was a copy of the
    # mission contract, which the Manager is already pointed at.
    ("manager_observation_before_rounds", '{"path": "handoffs/a1b2c3d4e5f6/latest.json"}, ', ""),
    # manager-supervision/latest.json was a copy of the newest record, which
    # the continuity handoff already listed; the copy is no longer listed.
    ("continuity_capsule", '"manager-supervision/latest.json": {"status": "observed"}', ""),
    ("continuity_capsule", f'"{SUPERVISION}": {{"status": "observed"}}, ', f'"{SUPERVISION}": {{"status": "observed"}}'),
    ("continuity_evidence", '{"path": "manager-supervision/latest.json", "status": "observed"}, ', ""),
    # The Manager is told plainly when no daemon runs (PR #391), so it does not
    # report the team as working.
    *(
        (name, '"health": "stopped"}', '"health": "stopped", "working": "no: no daemon is running for this project, '
         'so no task is being executed; a task listed as running is stopped until a daemon starts"}')
        for name in ("manager_observation", "manager_observation_before_rounds")
    ),
]


def test_prompts_read_the_same_state_as_before(tmp_path, monkeypatch) -> None:
    expected = json.loads(RECORDED.read_text(encoding="utf-8"))
    for name, before, now in REFERENCE_CHANGES:
        assert before in expected[name], (name, before)
        expected[name] = expected[name].replace(before, now)
    # The briefs, the Manager's task evidence and the supervision receipt are unchanged.

    assert build(tmp_path / "project", monkeypatch) == expected
