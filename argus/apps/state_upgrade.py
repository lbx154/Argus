"""``argus upgrade-state``: bring project state written by 0.1.x releases up to date, once.

Supported: state written by any tagged release from v0.1.1 (2026-09-04) on.
The runtime reads only the current formats; the three older formats those
releases wrote are converted here, each only where it is found:

* a project directory named ``sha1(label)[:12]`` (every release up to 0.1.9)
  is renamed to the escaped label it has now;
* an operator inbox kept as ``inbox[.<stage>].jsonl`` with ``.offset`` files
  (v0.1.1 to v0.1.6) is imported into the SQLite queue;
* ``failure_experiences.jsonl`` kept as an append log without a snapshot
  header (v0.1.1 to v0.1.6) is rewritten as a snapshot.

Formats only written before v0.1.1 are not read by anything any more.

A project whose daemon is running is skipped and reported; stop it and run the
command again. Every step is safe to repeat.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from ..core import paths as core_paths

if TYPE_CHECKING:
    from ..life.failure_experience_storage import ExperienceRepository, ExperienceSnapshot

_SHA1_NAME = re.compile(r"^[0-9a-f]{12}$")
FAILURE_EXPERIENCES = "failure_experiences.jsonl"
# The per-record bound the append log was written under.
_MAX_RECORD_BYTES = 128 * 1024


@dataclass(frozen=True)
class UpgradeStep:
    project: str
    change: str
    done: bool
    note: str = ""


def upgrade_state(
    global_root: Path,
    *,
    workdirs: Iterable[str | Path] = (),
    dry_run: bool = False,
) -> list[UpgradeStep]:
    """Upgrade every project under ``global_root``; return what was (or would be) done."""
    states = core_paths.session_states_root(global_root)
    if not states.is_dir():
        return []
    steps: list[UpgradeStep] = []
    explicit = [Path(path).expanduser() for path in workdirs]
    for directory in sorted(path for path in states.iterdir() if path.is_dir()):
        if directory.name.startswith("."):
            continue
        if _is_live(directory):
            if _needs_upgrade(directory):
                steps.append(UpgradeStep(directory.name, "skipped", False, "its daemon is running"))
            continue
        if _SHA1_NAME.fullmatch(directory.name):
            step = _rename_sha1_directory(directory, explicit, dry_run=dry_run)
            steps.append(step)
            if not step.done or dry_run:
                continue
            directory = states / step.note
        steps.extend(_upgrade_project(directory, dry_run=dry_run))
    return steps


def _is_live(directory: Path) -> bool:
    from ..daemon.state import read_daemon_status

    return read_daemon_status(directory).alive


def _needs_upgrade(directory: Path) -> bool:
    return bool(
        _SHA1_NAME.fullmatch(directory.name)
        or _legacy_inbox_files(directory)
        or _failure_experiences_are_an_append_log(directory / FAILURE_EXPERIENCES)
    )


def _upgrade_project(directory: Path, *, dry_run: bool) -> list[UpgradeStep]:
    steps: list[UpgradeStep] = []
    if _legacy_inbox_files(directory):
        steps.append(_upgrade_inbox(directory, dry_run=dry_run))
    if _failure_experiences_are_an_append_log(directory / FAILURE_EXPERIENCES):
        steps.append(_upgrade_failure_experiences(directory, dry_run=dry_run))
    return steps


# -- sha1 project directory names -------------------------------------------


def _rename_sha1_directory(
    directory: Path, explicit: list[Path], *, dry_run: bool,
) -> UpgradeStep:
    """Rename ``sha1(label)[:12]`` to the label's escaped name.

    sha1 cannot be reversed, so the label is found by fingerprinting every
    workdir the project recorded (its daemon status and session metadata) and
    every ``--workdir`` given, and keeping the one whose sha1 is this name.
    """
    from ..core.project import project_fingerprint

    for candidate in [*explicit, *_recorded_workdirs(directory)]:
        if not candidate.is_dir():
            continue
        identity = project_fingerprint(candidate)
        digest = hashlib.sha1(identity.label.encode("utf-8"), usedforsecurity=False).hexdigest()
        if digest[:12] != directory.name:
            continue
        target = directory.parent / identity.fingerprint
        if target.exists():
            return UpgradeStep(
                directory.name, "rename", False,
                f"{target.name} already exists; the older copy was left untouched",
            )
        if not dry_run:
            os.rename(directory, target)
        return UpgradeStep(directory.name, "rename", True, target.name)
    return UpgradeStep(
        directory.name, "rename", False,
        "its project directory is unknown; run again with --workdir <project directory>",
    )


def _recorded_workdirs(directory: Path) -> list[Path]:
    found: list[Path] = []
    for name, keys in (
        ("daemon.status.json", ("project_workdir",)),
        ("session.json", ("workdir", "cwd")),
    ):
        try:
            payload = json.loads((directory / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict):
            found.extend(Path(str(payload[key])) for key in keys if payload.get(key))
    return found


# -- operator inbox ----------------------------------------------------------


def _legacy_inbox_files(directory: Path) -> list[Path]:
    return [path for path in directory.glob("inbox*.jsonl") if path.is_file() and not path.is_symlink()]


def _upgrade_inbox(directory: Path, *, dry_run: bool) -> UpgradeStep:
    from ._inbox_protocol import InboxError, migrate_legacy_inbox

    if dry_run:
        return UpgradeStep(directory.name, "inbox", False, "would import the JSONL inbox")
    try:
        # The daemon is stopped (checked by the caller), so no old writer remains.
        migrate_legacy_inbox(directory, writers_stopped=True)
    except InboxError as exc:
        return UpgradeStep(directory.name, "inbox", False, str(exc))
    return UpgradeStep(directory.name, "inbox", True)


# -- failure experiences -----------------------------------------------------


def _failure_experiences_are_an_append_log(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            first = handle.readline(_MAX_RECORD_BYTES + 1)
    except OSError:
        return False
    if not first.strip():
        return False
    try:
        header = json.loads(first)
    except (ValueError, UnicodeError):
        # A damaged snapshot header is the runtime's to report, not ours to rewrite.
        return b'"record_type": "snapshot"' not in first
    return not (isinstance(header, dict) and header.get("record_type") == "snapshot")


def _upgrade_failure_experiences(directory: Path, *, dry_run: bool) -> UpgradeStep:
    from ..life.failure_experience_storage import ExperienceRepository

    if dry_run:
        return UpgradeStep(directory.name, "failure experiences", False, "would rewrite the append log")
    repository = ExperienceRepository(directory / FAILURE_EXPERIENCES)
    with repository.locked():
        if not _failure_experiences_are_an_append_log(repository.path):
            return UpgradeStep(directory.name, "failure experiences", True, "already a snapshot")
        snapshot = _read_append_log(repository)
        repository.save(snapshot, now=time.time())
    return UpgradeStep(directory.name, "failure experiences", True, f"{len(snapshot.current)} kept")


def _read_append_log(repository: ExperienceRepository) -> ExperienceSnapshot:
    """The append log's latest revision of each experience, with its annotations."""
    from ..life.failure_experience import FailureAnnotation, FailureExperience
    from ..life.failure_experience_storage import ExperienceSnapshot

    snapshot = ExperienceSnapshot()
    with repository.path.open("rb") as handle:
        while True:
            offset = handle.tell()
            raw = handle.readline(_MAX_RECORD_BYTES + 1)
            if not raw:
                break
            if len(raw) > _MAX_RECORD_BYTES:
                while raw and not raw.endswith(b"\n"):
                    raw = handle.readline(_MAX_RECORD_BYTES + 1)
                continue
            try:
                row = json.loads(raw)
                if not isinstance(row, dict):
                    continue
                if row.get("record_type") == "annotation":
                    previous = snapshot.current.get(str(row.get("experience_id") or ""))
                    payload = row.get("annotation")
                    if previous is not None and isinstance(payload, dict):
                        annotation = FailureAnnotation(
                            id=str(payload.get("id") or f"legacy-{offset}"),
                            created_at=float(payload.get("created_at") or previous.created_at),
                            text=str(payload.get("text") or "")[:4000],
                            relation=str(payload.get("relation") or "")[:200],
                            evidence_refs=[
                                str(ref)[:800] for ref in (payload.get("evidence_refs") or [])[:24]
                            ],
                        )
                        snapshot.current[previous.id] = replace(
                            previous, annotations=(previous.annotations + [annotation])[-24:]
                        )
                    continue
                if row.get("record_type") not in {None, "experience"}:
                    continue
                # Missing old identities take their line's place in the stream.
                row.setdefault("id", f"legacy-{offset}")
                item = FailureExperience.from_jsonable(row)
                repository.validate(item)
                previous = snapshot.current.get(item.id)
                if previous is None and item.created_at <= snapshot.admission_floor:
                    continue
                if previous is None or item.revision >= previous.revision:
                    snapshot.current[item.id] = item
            except (TypeError, ValueError, UnicodeError, AttributeError):
                continue
            # Keep the state bounded while reading a long log.
            if len(snapshot.current) > repository.max_active * 2:
                repository.trim(snapshot, now=0)
    return snapshot


__all__ = ["UpgradeStep", "upgrade_state"]
