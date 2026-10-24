"""Oversized artifacts are scanned in full every time and never replaced under a writer."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.core import secret_guard as guard


@pytest.fixture(autouse=True)
def small_streams(monkeypatch):
    monkeypatch.setattr(guard, "_MAX_ARTIFACT_BYTES", 16)
    monkeypatch.setattr(guard, "_STREAM_CHUNK_BYTES", 64)


def _payload(value: str) -> bytes:
    return (json.dumps({"observation": value, "padding": "x" * 100}) + "\n").encode()


def _scrub(root: Path, known=()):
    return guard.scrub_recent_text_artifacts(root, modified_since=0, known_values=known)


def test_new_known_credential_is_redacted_from_previously_clean_content(redact_secrets_on, tmp_path):
    secret = "newly-known-unit-test-credential"
    path = tmp_path / "result.jsonl"
    path.write_bytes(_payload(secret))
    assert not _scrub(tmp_path).changed

    report = _scrub(tmp_path, (secret,))
    assert report.changed and not report.errors
    assert secret.encode() not in path.read_bytes()


def test_a_change_during_the_rewrite_is_never_replaced(redact_secrets_on, tmp_path, monkeypatch):
    secret = "concurrently-written-test-credential"
    path = tmp_path / "result.jsonl"
    path.write_bytes(_payload(secret))
    original = guard._redact_text_with_count
    calls = []

    def writer_appends_during_rewrite(text, **kwargs):
        calls.append(text)
        if len(calls) == 2:  # the first rewrite segment: another writer appends
            with path.open("ab") as handle:
                handle.write(_payload("appended by another writer"))
        return original(text, **kwargs)

    monkeypatch.setattr(guard, "_redact_text_with_count", writer_appends_during_rewrite)
    report = _scrub(tmp_path, (secret,))
    assert any("ArtifactChangedDuringScrubError" in error for error in report.errors)
    assert b"appended by another writer" in path.read_bytes()

    monkeypatch.setattr(guard, "_redact_text_with_count", original)
    retry = _scrub(tmp_path, (secret,))
    assert retry.changed and not retry.errors
    assert secret.encode() not in path.read_bytes()
    assert b"appended by another writer" in path.read_bytes()
