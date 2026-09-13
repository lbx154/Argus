"""Factory ownership survives Windows newline conversion, not content edits."""

import hashlib
import json

import pytest

from argus_skill.skills import builtins


@pytest.mark.parametrize("installed_newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize("recorded_newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize("ownership", ["manifest", "legacy"])
def test_factory_copy_refreshes_across_line_endings(
    tmp_path, monkeypatch, installed_newline, recorded_newline, ownership,
):
    relative = "engineer/example.md"
    old = b"factory version one\nsecond line\n"
    new = "factory version two\nsecond line\n"
    destination = tmp_path / relative
    destination.parent.mkdir(parents=True)
    destination.write_bytes(old.replace(b"\n", installed_newline))
    recorded = hashlib.sha256(old.replace(b"\n", recorded_newline)).hexdigest()
    if ownership == "manifest":
        (tmp_path / builtins._BUILTIN_SEED_STATE).write_text(
            json.dumps({relative: recorded}), encoding="utf-8",
        )
    else:
        monkeypatch.setattr(builtins, "_LEGACY_BUILTIN_SEED_HASHES", {relative: recorded})
    monkeypatch.setattr(builtins, "iter_builtin_skill_texts", lambda: iter([(relative, new)]))

    changed = builtins.seed_builtin_skills(tmp_path)

    assert changed[relative] is True
    assert destination.read_bytes() == new.encode()
    state = json.loads((tmp_path / builtins._BUILTIN_SEED_STATE).read_text())
    assert state[relative] == hashlib.sha256(destination.read_bytes()).hexdigest()
    assert builtins.seed_builtin_skills(tmp_path)[relative] is False


@pytest.mark.parametrize("edit", [b"operator version\r\n", b"factory version \r\n", b"factory version"])
def test_newline_compatibility_does_not_claim_user_edits(tmp_path, monkeypatch, edit):
    relative = "engineer/example.md"
    destination = tmp_path / relative
    destination.parent.mkdir(parents=True)
    destination.write_bytes(edit)
    prior = hashlib.sha256(b"factory version\n").hexdigest()
    manifest = tmp_path / builtins._BUILTIN_SEED_STATE
    manifest.write_text(json.dumps({relative: prior}), encoding="utf-8")
    monkeypatch.setattr(
        builtins, "iter_builtin_skill_texts",
        lambda: iter([(relative, "new factory version\n")]),
    )

    assert builtins.seed_builtin_skills(tmp_path)[relative] is False
    assert destination.read_bytes() == edit
    assert json.loads(manifest.read_text())[relative] == prior


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


@pytest.mark.parametrize("installed_newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize("recorded_newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize("ownership", ["retired", "moved_manifest"])
def test_retired_factory_copy_is_removed_without_archiving_newline_only_change(
    tmp_path, monkeypatch, installed_newline, recorded_newline, ownership,
):
    relative = "engineer/retired.md"
    body = b"old factory body\nsecond line\n"
    destination = tmp_path / relative
    destination.parent.mkdir(parents=True)
    destination.write_bytes(body.replace(b"\n", installed_newline))
    digest = hashlib.sha256(body.replace(b"\n", recorded_newline)).hexdigest()
    monkeypatch.setattr(builtins, "_RETIRED_BUILTIN_SEED_HASHES", {})
    monkeypatch.setattr(builtins, "_moved_global_skill_names", lambda: set())
    if ownership == "retired":
        monkeypatch.setattr(builtins, "_RETIRED_BUILTIN_SEED_HASHES", {relative: digest})
    else:
        monkeypatch.setattr(builtins, "_moved_global_skill_names", lambda: {relative})
        (tmp_path / builtins._BUILTIN_SEED_STATE).write_text(
            json.dumps({relative: digest}), encoding="utf-8",
        )

    assert builtins.retire_orphaned_builtin_seeds(tmp_path) == [relative]
    assert not destination.exists()
    assert not (tmp_path / "_retired_builtin_skills").exists()


def test_retired_edited_crlf_copy_is_archived_byte_for_byte(tmp_path, monkeypatch):
    relative = "engineer/retired.md"
    factory = b"factory body\n"
    edited = b"factory body\r\noperator addition\r\n"
    destination = tmp_path / relative
    destination.parent.mkdir(parents=True)
    destination.write_bytes(edited)
    monkeypatch.setattr(
        builtins, "_RETIRED_BUILTIN_SEED_HASHES",
        {relative: hashlib.sha256(factory).hexdigest()},
    )
    monkeypatch.setattr(builtins, "_moved_global_skill_names", lambda: set())

    assert builtins.retire_orphaned_builtin_seeds(tmp_path) == [relative]
    assert not destination.exists()
    archive = tmp_path / "_retired_builtin_skills" / f"{relative}.retired"
    assert archive.read_bytes() == edited


def test_seeding_writes_lf_bytes_and_matching_manifest_from_crlf_source(tmp_path, monkeypatch):
    monkeypatch.setattr(
        builtins, "iter_builtin_skill_texts",
        lambda: iter([("example.md", "factory body\r\nsecond line\r\n")]),
    )

    assert builtins.seed_builtin_skills(tmp_path)["example.md"] is True

    installed = (tmp_path / "example.md").read_bytes()
    assert installed == b"factory body\nsecond line\n"
    manifest = (tmp_path / builtins._BUILTIN_SEED_STATE).read_bytes()
    assert b"\r\n" not in manifest
    assert json.loads(manifest)["example.md"] == hashlib.sha256(installed).hexdigest()
