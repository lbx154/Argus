"""Factory ownership follows what Argus wrote; any later change is the operator's."""

import json
import os

import pytest

from argus.core.file_facts import file_facts
from argus.skills import builtins


def _seed(monkeypatch, tmp_path, relative, text):
    monkeypatch.setattr(builtins, "iter_builtin_skill_texts", lambda: iter([(relative, text)]))
    return builtins.seed_builtin_skills(tmp_path)


def test_a_copy_untouched_since_seeding_is_refreshed(tmp_path, monkeypatch):
    relative = "engineer/example.md"
    destination = tmp_path / relative

    assert _seed(monkeypatch, tmp_path, relative, "factory version one\n")[relative] is True
    changed = _seed(monkeypatch, tmp_path, relative, "factory version two\n")

    assert changed[relative] is True
    assert destination.read_bytes() == b"factory version two\n"
    state = json.loads((tmp_path / builtins._BUILTIN_SEED_STATE).read_text())
    assert state[relative] == file_facts(destination)
    assert _seed(monkeypatch, tmp_path, relative, "factory version two\n")[relative] is False


@pytest.mark.parametrize("edit", [b"operator version\r\n", b"factory version one\r\n", b"factory version one"])
def test_a_copy_changed_after_seeding_is_the_operators(tmp_path, monkeypatch, edit):
    relative = "engineer/example.md"
    destination = tmp_path / relative
    _seed(monkeypatch, tmp_path, relative, "factory version one\n")
    destination.write_bytes(edit)
    stat = destination.stat()
    os.utime(destination, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    assert _seed(monkeypatch, tmp_path, relative, "factory version two\n")[relative] is False
    assert destination.read_bytes() == edit


def test_a_copy_seeded_before_file_facts_were_kept_is_not_claimed(tmp_path, monkeypatch):
    relative = "engineer/example.md"
    destination = tmp_path / relative
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"older factory version\n")
    (tmp_path / builtins._BUILTIN_SEED_STATE).write_text(
        json.dumps({relative: "a" * 64}), encoding="utf-8",
    )

    assert _seed(monkeypatch, tmp_path, relative, "factory version two\n")[relative] is False
    assert destination.read_bytes() == b"older factory version\n"


def test_matching_crlf_factory_copy_becomes_owned_without_claiming_independent_skill(
    tmp_path, monkeypatch,
):
    copied = tmp_path / "copied.md"
    copied.write_bytes(b"factory body\r\n")
    independent = tmp_path / "independent.md"
    independent.write_bytes(b"independent user body\r\n")
    unrelated = tmp_path / "user-only.md"
    unrelated.write_bytes(b"user only\r\n")
    monkeypatch.setattr(
        builtins, "iter_builtin_skill_texts",
        lambda: iter([
            ("copied.md", "factory body\n"),
            ("independent.md", "factory body\n"),
        ]),
    )

    changed = builtins.seed_builtin_skills(tmp_path)

    assert changed == {"copied.md": True, "independent.md": False}
    assert copied.read_bytes() == b"factory body\n"
    assert independent.read_bytes() == b"independent user body\r\n"
    assert unrelated.read_bytes() == b"user only\r\n"
    assert set(json.loads((tmp_path / builtins._BUILTIN_SEED_STATE).read_text())) == {"copied.md"}


@pytest.mark.parametrize("ownership", ["retired", "moved"])
def test_a_retired_copy_untouched_since_seeding_is_removed_without_archiving(
    tmp_path, monkeypatch, ownership,
):
    relative = "engineer/retired.md"
    destination = tmp_path / relative
    _seed(monkeypatch, tmp_path, relative, "old factory body\nsecond line\n")
    monkeypatch.setattr(builtins, "_RETIRED_BUILTIN_SEEDS", frozenset())
    monkeypatch.setattr(builtins, "_moved_global_skill_names", lambda: set())
    if ownership == "retired":
        monkeypatch.setattr(builtins, "_RETIRED_BUILTIN_SEEDS", frozenset({relative}))
    else:
        monkeypatch.setattr(builtins, "_moved_global_skill_names", lambda: {relative})

    assert builtins.retire_orphaned_builtin_seeds(tmp_path) == [relative]
    assert not destination.exists()
    assert not (tmp_path / "_retired_builtin_skills").exists()


def test_a_retired_copy_argus_cannot_vouch_for_is_archived_byte_for_byte(tmp_path, monkeypatch):
    relative = "engineer/retired.md"
    edited = b"factory body\r\noperator addition\r\n"
    destination = tmp_path / relative
    destination.parent.mkdir(parents=True)
    destination.write_bytes(edited)
    monkeypatch.setattr(builtins, "_RETIRED_BUILTIN_SEEDS", frozenset({relative}))
    monkeypatch.setattr(builtins, "_moved_global_skill_names", lambda: set())

    assert builtins.retire_orphaned_builtin_seeds(tmp_path) == [relative]
    assert not destination.exists()
    archive = tmp_path / "_retired_builtin_skills" / f"{relative}.retired"
    assert archive.read_bytes() == edited


def test_seeding_writes_lf_bytes_and_records_their_file_facts(tmp_path, monkeypatch):
    assert _seed(monkeypatch, tmp_path, "example.md", "factory body\r\nsecond line\r\n")["example.md"] is True

    installed = tmp_path / "example.md"
    assert installed.read_bytes() == b"factory body\nsecond line\n"
    manifest = (tmp_path / builtins._BUILTIN_SEED_STATE).read_bytes()
    assert b"\r\n" not in manifest
    assert json.loads(manifest)["example.md"] == file_facts(installed)
