"""A held transport reader must not block log rotation on Windows or POSIX."""
from pathlib import Path

from argus_skill.core.jsonl_reader import open_jsonl_generation


def test_read_handle_follows_its_generation_during_rotation(tmp_path: Path) -> None:
    active = tmp_path / "events.jsonl"
    retained = tmp_path / "events.jsonl.1"
    previous = b'{"generation": 1}\n'
    current = b'{"generation": 2}\n'
    active.write_bytes(previous)

    with open_jsonl_generation(active) as held:
        active.rename(retained)
        active.write_bytes(current)
        assert held.read() == previous
        with open_jsonl_generation(active) as fresh:
            assert fresh.read() == current

    assert retained.read_bytes() == previous
    assert active.read_bytes() == current
