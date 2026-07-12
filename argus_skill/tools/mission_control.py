"""Cross-process "abort the running mission" signal.

The Manager runs in the operator's REPL process; the mission it may need to
abort is executing in the *daemon's* separate OS process. There is no shared
memory between them, so the request is a small file dropped into the
session's shared ``life_dir`` — the same directory both processes already
agree on for ``events.jsonl`` / ``backlog.jsonl`` / ``continuous.json``.

The daemon's supervisor is single-lane (one mission in flight at a time; see
``life/supervisor/_core.py``), so there is never any ambiguity about *which*
mission an abort request targets: it is always "whatever round is running
right now". The running round's watchdog loop (the same one that already
polls for a daemon-shutdown interrupt — see
``engineer.runner.fatal_error_looks_like_daemon_stop_request``) polls for
this file too and consumes (deletes) it exactly once.

This module also exposes a tiny CLI so the Manager — which already has real
shell access on its SELF turn — can raise the request as an ordinary tool
call:

    python -m argus_skill.tools.mission_control abort \\
        --life-dir /root/.argus-skill/projects/s-540c1d6d \\
        --reason "operator asked to stop the running mission"
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

_ABORT_REQUEST_FILENAME = "mission_abort_request.json"


def _abort_request_path(life_dir: Path | str) -> Path:
    return Path(life_dir) / _ABORT_REQUEST_FILENAME


def request_mission_abort(
    life_dir: Path | str,
    *,
    reason: str,
    requested_by: str = "manager",
    target_item_id: str | None = None,
) -> Path:
    """Drop a one-shot abort request for whatever mission is currently
    running under ``life_dir``.

    Idempotent: calling this again before the previous request is consumed
    simply overwrites it (write-to-temp + ``os.replace``, matching the
    ``continuous.json`` convention in ``daemon.life_worker``). Returns the
    path written.
    """
    path = _abort_request_path(life_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "reason": str(reason or "").strip() or "operator requested abort",
        "requested_by": requested_by,
        "requested_at": time.time(),
    }
    if target_item_id:
        payload["target_item_id"] = str(target_item_id)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(path))
    except OSError:
        log.warning("failed to write mission abort request to %s", path)
    return path


def request_current_mission_abort(
    life_dir: Path | str,
    *,
    reason: str,
    requested_by: str = "manager",
) -> tuple[bool, str | None]:
    """Abort the currently running backlog item, never a future item.

    The explicit running-item check prevents an idle ``/abort`` from creating
    noise. The target id in the durable request is the authoritative race guard:
    the daemon consumes it only while that exact item remains running.
    """
    from ..life.memory import LifeMemory

    root = Path(life_dir)
    backlog = LifeMemory.open(root).backlog

    def _running_item_id() -> str | None:
        running = [item for item in backlog.all() if item.status == "running"]
        if not running:
            return None
        running.sort(key=lambda item: (item.started_ts or item.ts, item.id))
        return running[-1].id

    item_id = _running_item_id()
    if item_id is None:
        return False, None
    request_mission_abort(
        root,
        reason=reason,
        requested_by=requested_by,
        target_item_id=item_id,
    )
    return True, item_id


def pop_pending_mission_abort(life_dir: Path | str | None) -> str | None:
    """Consume (delete) a pending abort request, returning its reason if one
    is present.

    Returns ``None`` — never raises — when there is nothing pending, the
    file is malformed, or ``life_dir`` itself is falsy. This is polled from a
    tight (sub-second) watchdog loop on every running round, so it must stay
    cheap and fail silent.
    """
    if not life_dir:
        return None
    path = _abort_request_path(life_dir)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        path.unlink()
    except OSError:
        pass
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    target_item_id = str(data.get("target_item_id") or "").strip()
    if not target_item_id:
        return None
    try:
        from ..life.memory import LifeMemory

        target = next(
            (
                item
                for item in LifeMemory.open(Path(life_dir)).backlog.all()
                if item.id == target_item_id
            ),
            None,
        )
    except Exception:  # noqa: BLE001
        return None
    if target is None or target.status != "running":
        return None
    reason = str(data.get("reason") or "").strip()
    return reason or "operator requested abort"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m argus_skill.tools.mission_control")
    sub = parser.add_subparsers(dest="cmd", required=True)
    abort_p = sub.add_parser(
        "abort",
        help="ask the daemon to abort whatever mission is currently running",
    )
    abort_p.add_argument(
        "--life-dir", required=True, help="session life_dir shared with the daemon"
    )
    abort_p.add_argument(
        "--reason", default="", help="why the mission is being aborted"
    )
    abort_p.add_argument("--requested-by", default="manager")
    args = parser.parse_args(argv)

    if args.cmd == "abort":
        requested, item_id = request_current_mission_abort(
            args.life_dir,
            reason=args.reason,
            requested_by=args.requested_by,
        )
        if not requested:
            print("argus-skill: no mission is currently running; nothing was queued.")
            return 0
        print(
            f"argus-skill: mission abort requested for {item_id}. The daemon will "
            "stop the in-flight round on its next watchdog check and remain "
            "running to pick up the next backlog item."
        )
        return 0
    return 2


__all__ = [
    "request_mission_abort",
    "request_current_mission_abort",
    "pop_pending_mission_abort",
    "main",
]


if __name__ == "__main__":  # pragma: no cover — thin CLI wrapper
    raise SystemExit(main())
