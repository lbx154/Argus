"""Real-file byte limits and resumable JSONL record boundaries."""
from __future__ import annotations

import json

import pytest

from argus_skill.core import jsonl_reader


class CountedReader:
    def __init__(self, stream):
        self.stream = stream
        self.bytes_read = 0
        self.limits = []

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def read(self, size=-1):
        assert size >= 0, "unbounded read"
        self.limits.append(size)
        value = self.stream.read(size)
        self.bytes_read += len(value)
        return value

    def readline(self, size=-1):
        assert size >= 0, "unbounded readline"
        self.limits.append(size)
        value = self.stream.readline(size)
        self.bytes_read += len(value)
        return value


@pytest.mark.parametrize("record_limit,byte_limit", [(128, 64), (1024 * 1024, 64 * 1024)])
def test_each_batch_has_a_hard_read_bound_and_oversized_row_resumes(tmp_path, record_limit, byte_limit):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'x' * (record_limit * 3 + 42) + b'\n{"id":"good"}\n')
    original = path.read_bytes()
    offset, discarding, skipped = 0, False, 0
    rows = []
    for _ in range(80):
        # Reopen between batches, as a persisted history index does on restart.
        with path.open("rb") as stream:
            counted = CountedReader(stream)
            batch = jsonl_reader.read_jsonl_batch(
                counted, offset, byte_limit=byte_limit, max_record_bytes=record_limit, discarding=discarding,
            )
        assert counted.bytes_read == batch.bytes_read
        assert batch.bytes_read <= byte_limit + record_limit + 1
        assert max(counted.limits, default=0) <= max(byte_limit, record_limit + 1)
        offset, discarding = batch.offset, batch.discarding
        skipped += batch.oversized_rows
        rows.extend(batch.rows)
        if not batch.more:
            break
    assert rows == [{"id": "good"}]
    assert skipped == 1
    assert offset == len(original)
    assert path.read_bytes() == original


def test_unterminated_huge_line_does_not_keep_an_unbounded_buffer(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'x' * 4096)
    offset, discarding = 0, False
    for _ in range(80):
        with path.open("rb") as stream:
            batch = jsonl_reader.read_jsonl_batch(stream, offset, byte_limit=64,
                                                 max_record_bytes=128, discarding=discarding)
        offset, discarding = batch.offset, batch.discarding
        if batch.waiting_for_line:
            break
    assert offset == 4096 and discarding
    with path.open("ab") as stream:
        stream.write(b'\n{"id":"after"}\n')
    with path.open("rb") as stream:
        batch = jsonl_reader.read_jsonl_batch(stream, offset, byte_limit=64,
                                             max_record_bytes=128, discarding=discarding)
    assert batch.rows == [{"id": "after"}]
    assert not batch.discarding and not batch.waiting_for_line


def test_partial_record_straddling_replay_window_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(jsonl_reader, "MAX_JSONL_RECORD_BYTES", 512)
    path = tmp_path / "events.jsonl"
    first = b'{"id":"old"}\n'
    partial = b'{"id":"' + b'x' * 300
    path.write_bytes(first + partial)
    with path.open("rb") as stream:
        counted = CountedReader(stream)
        replay = jsonl_reader.read_jsonl_replay(counted, limit=0, max_bytes=64)
    assert replay.offset == len(first)
    assert not replay.discarding
    assert counted.bytes_read <= 64 + 512
    with path.open("ab") as stream:
        stream.write(b'"}\n')
    with path.open("rb") as stream:
        batch = jsonl_reader.read_jsonl_batch(stream, replay.offset, byte_limit=64)
    assert batch.rows == [{"id": "x" * 300}]


def test_bad_and_nonfinite_records_do_not_block_a_later_valid_row(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'not json\n{"n":NaN}\n{"n":1e400}\n' + json.dumps({"id": "good"}).encode() + b'\n')
    with path.open("rb") as stream:
        batch = jsonl_reader.read_jsonl_batch(stream, 0, byte_limit=1024)
    assert batch.rows == [{"id": "good"}]
    assert batch.skipped_rows == 3


def test_sealed_partial_line_is_not_joined_to_the_next_generation(tmp_path):
    path = tmp_path / "events.jsonl.1"
    path.write_bytes(b'{"id":')
    with path.open("rb") as stream:
        batch = jsonl_reader.read_jsonl_batch(stream, 0, byte_limit=32, sealed=True)
    assert batch.rows == []
    assert batch.offset == path.stat().st_size
    assert not batch.waiting_for_line and not batch.discarding
