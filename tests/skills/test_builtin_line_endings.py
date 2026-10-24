"""Factory ownership follows what Argus wrote; any later change is the operator's."""

import json

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
