"""Real-file byte limits and resumable JSONL record boundaries."""
from __future__ import annotations

from argus.core import jsonl_reader


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


def test_sealed_partial_line_is_not_joined_to_the_next_generation(tmp_path):
    path = tmp_path / "events.jsonl.1"
    path.write_bytes(b'{"id":')
    with path.open("rb") as stream:
        batch = jsonl_reader.read_jsonl_batch(stream, 0, byte_limit=32, sealed=True)
    assert batch.rows == []
    assert batch.offset == path.stat().st_size
    assert not batch.waiting_for_line and not batch.discarding
