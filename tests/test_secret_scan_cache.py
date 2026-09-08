"""Repeated research aggregates may reuse only a complete scan of identical bytes."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from argus_skill.core import secret_guard as guard


@pytest.fixture(autouse=True)
def small_streams(monkeypatch):
    monkeypatch.setattr(guard, "_MAX_ARTIFACT_BYTES", 16)
    monkeypatch.setattr(guard, "_STREAM_CHUNK_BYTES", 64)


def _payload(value: str) -> bytes:
    return (json.dumps({"observation": value, "padding": "x" * 100}) + "\n").encode()


def _scrub(root: Path, cache: guard.SecretScanCache, known=()):
    return guard.scrub_recent_text_artifacts(
        root, modified_since=0, known_values=known, cache=cache,
    )


def test_rewritten_identical_bytes_reuse_but_same_size_changed_bytes_do_not(
    tmp_path, monkeypatch,
):
    secret = "unit-test-credential-12345"
    clean = _payload("x" * len(secret))
    dirty = _payload(secret)
    assert len(clean) == len(dirty)
    path = tmp_path / "refinement.jsonl"
    path.write_bytes(clean)
    cache = guard.SecretScanCache()
    scan_calls = []
    original = guard.redact_secrets_text_with_count

    def count_scans(text, **kwargs):
        scan_calls.append(len(text))
        return original(text, **kwargs)

    monkeypatch.setattr(guard, "redact_secrets_text_with_count", count_scans)
    assert not _scrub(tmp_path, cache, (secret,)).changed
    count_after_scan = len(scan_calls)
    assert count_after_scan > 0

    # An aggregate rebuild rewrites the same contents and changes metadata.
    path.write_bytes(clean)
    assert not _scrub(tmp_path, cache, (secret,)).changed
    assert len(scan_calls) == count_after_scan
    assert cache.hits == 1

    # Reusing the original mtime and byte count must not reuse different data.
    before = path.stat()
    path.write_bytes(dirty)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    report = _scrub(tmp_path, cache, (secret,))
    assert report.changed and not report.errors
    assert secret.encode() not in path.read_bytes()
    assert len(scan_calls) > count_after_scan
    assert cache.hits == 1


def test_new_known_credential_invalidates_previously_clean_content(tmp_path):
    secret = "newly-known-unit-test-credential"
    path = tmp_path / "result.jsonl"
    path.write_bytes(_payload(secret))
    cache = guard.SecretScanCache()
    assert not _scrub(tmp_path, cache).changed

    report = _scrub(tmp_path, cache, (secret,))
    assert report.changed and not report.errors
    assert secret.encode() not in path.read_bytes()
    assert cache.hits == 0


def test_known_only_source_scan_cannot_certify_pattern_scanned_output(tmp_path):
    payload = (json.dumps({"api_key": "synthetic-test-key", "padding": "x" * 100}) + "\n").encode()
    source = tmp_path / "schema.py"
    source.write_bytes(payload)
    cache = guard.SecretScanCache()
    assert not _scrub(tmp_path, cache).changed

    output = tmp_path / "response.jsonl"
    output.write_bytes(payload)
    report = _scrub(tmp_path, cache)
    assert report.redacted_paths == ("response.jsonl",)
    assert source.read_bytes() == payload
    assert b"synthetic-test-key" not in output.read_bytes()


def test_concurrent_change_during_digest_check_is_not_a_cache_hit(tmp_path, monkeypatch):
    secret = "concurrently-added-test-credential"
    path = tmp_path / "result.jsonl"
    path.write_bytes(_payload("x" * len(secret)))
    cache = guard.SecretScanCache()
    assert not _scrub(tmp_path, cache, (secret,)).changed
    original = guard._stream_file_digest

    def change_after_hash(target):
        digest = original(target)
        target.write_bytes(_payload(secret))
        return digest

    monkeypatch.setattr(guard, "_stream_file_digest", change_after_hash)
    report = _scrub(tmp_path, cache, (secret,))
    assert any("ArtifactChangedDuringScrubError" in error for error in report.errors)
    assert cache.hits == 0

    monkeypatch.setattr(guard, "_stream_file_digest", original)
    retry = _scrub(tmp_path, cache, (secret,))
    assert retry.changed and not retry.errors
    assert secret.encode() not in path.read_bytes()


def test_failed_scan_never_creates_reusable_evidence(tmp_path, monkeypatch):
    secret = "failed-scan-test-credential"
    path = tmp_path / "result.jsonl"
    path.write_bytes(_payload(secret))
    cache = guard.SecretScanCache()
    original = guard.redact_secrets_text_with_count

    def interrupted(*_args, **_kwargs):
        raise OSError("temporary scan failure")

    monkeypatch.setattr(guard, "redact_secrets_text_with_count", interrupted)
    assert _scrub(tmp_path, cache, (secret,)).errors
    monkeypatch.setattr(guard, "redact_secrets_text_with_count", original)
    report = _scrub(tmp_path, cache, (secret,))
    assert report.changed and not report.errors
    assert cache.hits == 0
    assert secret.encode() not in path.read_bytes()
