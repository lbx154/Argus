"""Per-recipient mailbox — generalises apps/_inbox.py (append + offset)
to one inbox per team member so teammates can message each other directly,
not only report back to the lead.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _box(root: Path, member: str) -> Path:
    return Path(root) / "mailbox" / member / "inbox.jsonl"


def _offset_path(root: Path, member: str) -> Path:
    return Path(root) / "mailbox" / member / "inbox.offset"


def _read_offset(p: Path) -> int:
    try:
        return max(0, int(p.read_text(encoding="utf-8").strip() or "0"))
    except (OSError, ValueError):
        return 0


def send(root: Path, *, to: str, frm: str, text: str, now: float) -> None:
    """Append a message to ``to``'s inbox (single-writer per recipient)."""
    box = _box(root, to)
    box.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": now, "from": frm, "text": text}
    with box.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _read(root: Path, member: str, *, advance: bool) -> list[dict[str, Any]]:
    box = _box(root, member)
    if not box.exists():
        return []
    offset = _read_offset(_offset_path(root, member))
    out: list[dict[str, Any]] = []
    try:
        with box.open("rb") as fh:
            fh.seek(offset)
            while True:
                raw = fh.readline()
                if not raw:
                    break
                new_offset = fh.tell()
                try:
                    obj = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    if advance:
                        _offset_path(root, member).write_text(str(new_offset), encoding="utf-8")
                    continue
                if isinstance(obj, dict) and isinstance(obj.get("text"), str):
                    out.append(obj)
                if advance:
                    _offset_path(root, member).write_text(str(new_offset), encoding="utf-8")
    except OSError:
        return []
    return out


def drain(root: Path, member: str) -> list[dict[str, Any]]:
    """Return unread messages for ``member`` and advance the read offset."""
    return _read(root, member, advance=True)


def count_pending(root: Path, member: str) -> int:
    """Count unread messages without advancing the offset."""
    return len(_read(root, member, advance=False))


def broadcast(root: Path, members: list[str], *, frm: str, text: str, now: float) -> None:
    """Send one copy of ``text`` to each member (Agent-Teams fan-out style)."""
    for m in members:
        send(root, to=m, frm=frm, text=text, now=now)
