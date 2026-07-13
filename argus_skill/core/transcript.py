"""Per-session operator↔Manager conversation transcript.

Append-only JSONL at ``<life_dir>/transcript.jsonl`` — Argus internal session
state (lives next to ``events.jsonl`` in the session artifact root, NOT task
output). Lets ``/resume`` label a session by its first message and replay the
conversation when you come back to it.

Every function is fail-soft: logging or reading a transcript must never break
the operator cockpit.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

_FNAME = "transcript.jsonl"


def _path(life_dir: Any) -> Path:
    return Path(life_dir) / _FNAME


def append_turn(life_dir: Any, role: str, text: str) -> None:
    """Append one conversation turn. ``role`` is ``"operator"`` or ``"argus"``."""
    try:
        body = str(text or "").strip()
        if not body:
            return
        p = _path(life_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": time.time(), "role": str(role or ""), "text": body}
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — never break the cockpit over transcript I/O
        pass


def read_turns(life_dir: Any, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Return the conversation turns (oldest first). ``limit`` keeps the last N."""
    try:
        p = _path(life_dir)
        if not p.exists():
            return []
        out: list[dict[str, Any]] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(rec, dict) and rec.get("text"):
                out.append(rec)
        return out[-limit:] if (limit and limit > 0) else out
    except Exception:  # noqa: BLE001
        return []


def first_operator_text(life_dir: Any) -> str:
    """Return the first operator turn for transcript inspection."""
    for turn in read_turns(life_dir):
        if turn.get("role") == "operator":
            return str(turn.get("text") or "")
    return ""


def has_transcript(life_dir: Any) -> bool:
    try:
        return _path(life_dir).exists()
    except Exception:  # noqa: BLE001
        return False
