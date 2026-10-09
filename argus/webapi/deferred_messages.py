"""Durable retry of messages refused before provider admission.

Only an explicit local admission refusal is retried. A request interrupted
after execution started is left for inspection, never blindly replayed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

import portalocker

from ..core.operator_messages import publish_operator_message, uses_cjk

log = logging.getLogger(__name__)


def _write_record(path: Path, record: dict[str, Any]) -> None:
    """Keep the queue's disk format private and replace only complete records."""
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".message-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _read_record(path: Path) -> dict[str, Any]:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        return record if isinstance(record, dict) else {}
    except (OSError, ValueError):
        return {}


class DeferredMessages:
    def __init__(self, run: Callable[[dict, Callable[[], bool]], dict], *, retry_seconds=2.0,
                 cancelled: Callable[[dict], bool] = lambda request: False):
        self.run = run
        self.cancelled = cancelled
        self.retry_seconds = retry_seconds
        self.stop = threading.Event()
        self._lock = threading.Lock()
        self._workers: dict[Path, threading.Thread] = {}
        self._roots: set[Path] = set()

    def enqueue(self, life_dir: Path, request: dict[str, Any]) -> dict[str, Any]:
        directory = life_dir / "manager-deferred"
        directory.mkdir(parents=True, exist_ok=True)
        identity = hashlib.sha256(request["turn_id"].encode()).hexdigest()
        path = directory / f"{identity}.json"
        with self._lock:
            self._roots.add(life_dir.resolve())
            if not path.exists():
                waiting = [p for p in directory.glob("*.json")
                           if _read_record(p).get("status") in {"waiting", "running"}]
                if len(waiting) >= 32:
                    raise ValueError("Too many messages waiting for this project; retry after one finishes.")
                _write_record(path, {"status": "waiting", "created_at": time.time(),
                                         "request": request})
        chinese = uses_cjk(request["text"])
        reply = ("消息已保存，正在等待空闲的模型调用名额；Argus 会自动处理，无需重复发送。" if chinese else
                 "Your message is saved and waiting for model capacity. Argus will process it automatically; no resend is needed.")
        publish_operator_message(
            life_dir, text=reply, message_id=f"{request['turn_id']}-queued",
            event_fields={"queued": True, "user_action_required": False},
        )
        self.resume(life_dir)
        return {"kind": "queued", "queued": True, "success": True, "reply": reply,
                "message_id": f"{request['turn_id']}-operator"}

    def resume(self, life_dir: Path) -> None:
        life_dir = life_dir.resolve()
        if self.stop.is_set() or not (life_dir / "manager-deferred").is_dir():
            return
        with self._lock:
            self._roots.add(life_dir)
            worker = self._workers.get(life_dir)
            if worker is None or not worker.is_alive():
                worker = threading.Thread(target=self._drain, args=(life_dir,),
                                          name=f"argus-deferred-{life_dir.name}", daemon=True)
                self._workers[life_dir] = worker
                worker.start()

    def close(self) -> None:
        self.stop.set()
        with self._lock:
            workers = list(self._workers.values())
            roots = list(self._roots)
        deadline = time.monotonic() + 5
        for worker in workers:
            worker.join(timeout=max(0, deadline - time.monotonic()))
        # Persist a Stop which arrived while a waiter was sleeping. Otherwise
        # a subsequent server restart could lose the in-process control fence.
        for root in roots:
            for path in (root / "manager-deferred").glob("*.json"):
                record = _read_record(path)
                if record.get("status") == "waiting" and self.cancelled(record["request"]):
                    self._cancel(root, path, record)

    def _cancel(self, life_dir: Path, path: Path, record: dict) -> None:
        record["status"] = "cancelled"
        _write_record(path, record)
        request = record["request"]
        reply = "等待中的消息已取消。" if uses_cjk(request["text"]) else "The waiting message was cancelled."
        publish_operator_message(life_dir, text=reply,
                                 message_id=f"{request['turn_id']}-cancelled",
                                 event_fields={"queued": False, "user_action_required": False})

    def _attention(self, life_dir: Path, path: Path, record: dict) -> None:
        record["status"] = "needs_attention"
        _write_record(path, record)
        request = record["request"]
        reply = ("这条消息的处理被中断，原消息已保留。请先查看已有任务和回复，再决定是否重试。" if uses_cjk(request["text"]) else
                 "Processing this message was interrupted. The message is saved; check existing tasks and replies before retrying.")
        publish_operator_message(life_dir, text=reply,
                                 message_id=f"{request['turn_id']}-interrupted",
                                 event_fields={"success": False, "user_action_required": True})

    def _drain(self, life_dir: Path) -> None:
        directory = life_dir / "manager-deferred"
        drained = False
        try:
            with (directory / "worker.lock").open("a+b") as owner:
                try:
                    portalocker.lock(owner, portalocker.LOCK_EX | portalocker.LOCK_NB)
                except portalocker.exceptions.LockException:
                    return
                try:
                    while not self.stop.is_set() and life_dir.is_dir():
                        records = [(p, _read_record(p)) for p in directory.glob("*.json")]
                        pending = sorted(((p, r) for p, r in records
                                          if r.get("status") in {"waiting", "running"}),
                                         key=lambda pair: pair[1]["created_at"])
                        if not pending:
                            drained = True
                            return
                        path, record = pending[0]
                        if self.cancelled(record["request"]):
                            self._cancel(life_dir, path, record)
                            continue
                        if record["status"] == "running":
                            self._attention(life_dir, path, record)
                            continue
                        record["status"] = "running"
                        _write_record(path, record)
                        from .manager_followups import PRESHOWN_OPERATOR_IDS
                        token = PRESHOWN_OPERATOR_IDS.set(frozenset({f"{record['request']['turn_id']}-operator"}))
                        try:
                            result = self.run(record["request"], self.stop.is_set)
                        except Exception:
                            log.exception("deferred Manager message interrupted for %s", life_dir.name)
                            self._attention(life_dir, path, record)
                            continue
                        finally:
                            PRESHOWN_OPERATOR_IDS.reset(token)
                        if result.get("kind") == "provider_busy":
                            record["status"] = "waiting"
                            _write_record(path, record)
                            self.stop.wait(self.retry_seconds)
                        else:
                            record["status"] = "completed"
                            record["result_kind"] = result.get("kind")
                            _write_record(path, record)
                finally:
                    portalocker.unlock(owner)
        except OSError:
            log.warning("deferred messages unavailable for %s", life_dir.name, exc_info=True)
        finally:
            # Release the OS owner before a successor starts. Enqueue can race
            # this exit; after detaching the old thread, either side starts it.
            with self._lock:
                if self._workers.get(life_dir) is threading.current_thread():
                    self._workers.pop(life_dir, None)
            if drained and any(_read_record(p).get("status") == "waiting"
                               for p in directory.glob("*.json")):
                self.resume(life_dir)
