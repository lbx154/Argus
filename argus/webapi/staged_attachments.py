"""Attachments uploaded from the composer before the message is sent.

The composer uploads a file as soon as it is chosen. Until a message carries
it, the file is *staged*: the operator may remove it from the draft (which
deletes it), and a staged file nobody sends is removed after a week. Sending
a message that references it unstages it for good; files that were never
staged (older uploads, other clients) are never touched here.
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

from ..core.atomic_replace import write_atomic
from ..core.file_lock import exclusive_file_lock
from . import attachments as store
from .artifacts import project_workspace

STAGED_FILENAME = "staged_attachments.json"
STAGED_SECONDS = 7 * 24 * 60 * 60


class StagedAttachmentError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _life_dir(root: Path | str, sid: str) -> Path | None:
    store._validate_storage_component(sid)
    life = Path(root) / "projects" / sid
    return life if life.is_dir() else None


@contextmanager
def _ledger(life: Path) -> Iterator[dict[str, float]]:
    """Read-modify-write the project's staged ledger under its own lock."""
    with (life / f"{STAGED_FILENAME}.lock").open("a") as handle:
        with exclusive_file_lock(handle, timeout_seconds=5, lock_name="staged attachments"):
            path = life / STAGED_FILENAME
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, ValueError):
                raw = {}
            entries = {
                str(key): float(value) for key, value in (raw if isinstance(raw, dict) else {}).items()
                if store._ATTACHMENT_ID_RE.fullmatch(str(key)) and isinstance(value, (int, float))
            }
            before = dict(entries)
            yield entries
            if entries == before:
                return
            if not entries:
                path.unlink(missing_ok=True)
                return
            write_atomic(path, json.dumps(entries, sort_keys=True) + "\n")


def stage(root: Path | str, sid: str, attachment_ids: Iterable[str], *, now: float | None = None) -> None:
    life = _life_dir(root, sid)
    ids = [value for value in attachment_ids if store._ATTACHMENT_ID_RE.fullmatch(value)]
    if life is None or not ids:
        return
    stamp = time.time() if now is None else now
    with _ledger(life) as entries:
        for attachment_id in ids:
            entries[attachment_id] = stamp


def unstage(root: Path | str, sid: str, attachment_ids: Iterable[str]) -> None:
    life = _life_dir(root, sid)
    ids = list(attachment_ids)
    if life is None or not ids or not (life / STAGED_FILENAME).exists():
        return
    with _ledger(life) as entries:
        for attachment_id in ids:
            entries.pop(attachment_id, None)


def _remove_attachment(root: Path | str, sid: str, attachment_id: str) -> None:
    from .attachment_uploads import _remove

    workspace = project_workspace(sid, global_root=root)
    if workspace is None:
        return
    try:
        _remove(workspace, sid, attachment_id)
    except FileNotFoundError:
        pass


def discard(root: Path | str, sid: str, attachment_id: str) -> dict[str, object]:
    """Delete a staged attachment the operator removed from the draft."""
    if not store._ATTACHMENT_ID_RE.fullmatch(attachment_id):
        raise StagedAttachmentError("invalid attachment_id")
    life = _life_dir(root, sid)
    if life is None:
        raise StagedAttachmentError("project not found", 404)
    with _ledger(life) as entries:
        if attachment_id not in entries:
            raise StagedAttachmentError("this attachment was already sent or is not a draft upload", 409)
        _remove_attachment(root, sid, attachment_id)
        entries.pop(attachment_id, None)
    return {"discarded": True, "attachment_id": attachment_id}


def sweep(roots: Iterable[Path | str], *, now: float | None = None) -> list[tuple[str, str]]:
    """Remove staged attachments no message picked up within a week."""
    removed: list[tuple[str, str]] = []
    cutoff = (time.time() if now is None else now) - STAGED_SECONDS
    for root in roots:
        for ledger in Path(root).glob(f"projects/*/{STAGED_FILENAME}"):
            sid = ledger.parent.name
            try:
                with _ledger(ledger.parent) as entries:
                    for attachment_id, staged_at in list(entries.items()):
                        if staged_at > cutoff:
                            continue
                        _remove_attachment(root, sid, attachment_id)
                        entries.pop(attachment_id, None)
                        removed.append((sid, attachment_id))
            except (OSError, ValueError, TimeoutError):
                continue
    return removed
