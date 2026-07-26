"""A busy workdir should tell the operator what to do about it.

Seen while testing on 2026-07-26: launching a second daemon in a directory that
already had one printed

    argus-skill: workdir /tmp/argus-night/wd-10 is already leased:
    {"life_dir": "...", "pid": 713014, "sid": "b1978edf2ccb", "workdir": "..."}

Everything the operator needed was in that line and none of it was actionable —
a raw lease record with a pid buried in it and no next step.
"""

from __future__ import annotations

import json
from pathlib import Path

from argus_skill.core.workspace_lease import _busy_message

_OWNER = {
    "life_dir": "/tmp/argus-night/home/projects/b1978edf2ccb",
    "pid": 713014,
    "sid": "b1978edf2ccb",
    "workdir": "/tmp/argus-night/wd-10",
}


def test_the_message_names_the_holder_and_the_ways_out() -> None:
    text = _busy_message(Path("/tmp/argus-night/wd-10"), json.dumps(_OWNER))

    assert "pid 713014" in text
    assert "kill 713014" in text, "an operator told a pid holds it must be told how to stop it"
    assert "--status" in text
    assert "different directory" in text
    assert _OWNER["life_dir"] in text
    # The session id is how the cockpit and CLI address this project; an
    # existing test rightly required it, and the first version of this message
    # dropped it.
    assert _OWNER["sid"] in text


def test_an_unparseable_lease_record_still_says_everything_it_knows() -> None:
    """Degrade to the old behaviour rather than swallowing the detail."""
    text = _busy_message(Path("/tmp/wd"), "not json at all")

    assert "already leased" in text
    assert "not json at all" in text


def test_an_empty_lease_record_still_reports_the_conflict() -> None:
    text = _busy_message(Path("/tmp/wd"), "")

    assert "/tmp/wd is already leased" in text
