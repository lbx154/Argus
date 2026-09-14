"""A busy workdir should tell the operator what to do about it.

Seen while testing on 2026-07-26: launching a second daemon in a directory that
already had one printed

    argus: workdir /tmp/argus-test/workdir is already leased:
    {"life_dir": "...", "pid": 4242, "sid": "s-holder", "workdir": "..."}

Everything the operator needed was in that line and none of it was actionable —
a raw lease record with a pid buried in it and no next step.
"""

from __future__ import annotations

import json
from pathlib import Path

from argus.core.workspace_lease import _busy_message

_OWNER = {
    "life_dir": "/tmp/argus-test/home/projects/s-holder",
    "pid": 4242,
    "sid": "s-holder",
    "workdir": "/tmp/argus-test/workdir",
}


def test_the_message_names_the_holder_and_the_ways_out() -> None:
    text = _busy_message(Path("/tmp/argus-test/workdir"), json.dumps(_OWNER))

    assert "pid 4242" in text
    assert "argus --daemon-stop --resume s-holder" in text
    assert "Stop-Process" not in text
    assert "kill 4242" not in text
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

    assert f"{Path('/tmp/wd')} is already leased" in text
