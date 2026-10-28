"""Narrowed handlers still absorb the failures they were written for."""
from __future__ import annotations

import json
from pathlib import Path

from argus.core import secret_guard, transcript
from argus.core.session import read_session_meta


def test_a_session_record_of_the_wrong_shape_reads_as_missing(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "s-shape"
    project.mkdir(parents=True)
    for body in ("not json", "[1, 2]", json.dumps({"created": "yesterday"})):
        (project / "session.json").write_text(body, encoding="utf-8")
        assert read_session_meta(tmp_path, "s-shape") is None


def test_transcript_io_and_metadata_failures_return_quietly(tmp_path: Path) -> None:
    assert transcript.append_turn(tmp_path, "operator", "hi", metadata={"item_id": object()}) is False
    (tmp_path / transcript._FNAME).write_bytes(b"\xff\xfe not utf-8\n")
    assert transcript.read_turns(tmp_path) == []


def test_a_corrupt_knob_store_keeps_redaction_off(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(secret_guard.REDACT_SECRETS_KNOB, raising=False)
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    from argus.core.knob_store import config_path

    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text("{not json", encoding="utf-8")
    secret_guard._REDACTION_CACHE.clear()
    assert secret_guard.secret_redaction_enabled() is False
