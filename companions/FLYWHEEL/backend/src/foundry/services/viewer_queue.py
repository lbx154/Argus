"""Durable file queue for the independent research Viewer process."""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


class ViewerQueue:
    def __init__(
        self,
        root: Path,
        *,
        claim_timeout_seconds: float = 30 * 60,
        max_claim_attempts: int = 2,
    ) -> None:
        self.root = root.resolve()
        if claim_timeout_seconds < 0:
            raise ValueError("claim timeout must be non-negative")
        if max_claim_attempts < 1:
            raise ValueError("max claim attempts must be positive")
        self.claim_timeout_seconds = float(claim_timeout_seconds)
        self.max_claim_attempts = int(max_claim_attempts)

    def enqueue(self, request: Mapping[str, Any]) -> dict[str, Any]:
        request_id = str(request.get("request_id") or uuid.uuid4())
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", request_id):
            raise ValueError("invalid viewer request id")
        payload = {
            **dict(request),
            "request_id": request_id,
            "queued_at": datetime.now(UTC).isoformat(),
            "protocol_version": 1,
        }
        inbox = self.root / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        target = inbox / f"{request_id}.json"
        temporary = inbox / f".{request_id}.{os.getpid()}.tmp"
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            temporary.replace(target)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise
        return {
            "accepted": True,
            "state": "queued",
            "request_id": request_id,
            "queue_path": str(target),
            "detail": "Queued for an independent Viewer process; no score exists yet.",
        }

    def next_request(self) -> tuple[Path, dict[str, Any]] | None:
        self._recover_stale_claims()
        inbox = self.root / "inbox"
        processing = self.root / "processing"
        processing.mkdir(parents=True, exist_ok=True)
        # Snapshot metadata one entry at a time.  A competing worker may move
        # an inbox file between ``glob`` and ``stat``; treating that single
        # vanished entry as lost is correct, while discarding the entire
        # candidate list would make this worker spuriously report an empty
        # queue even when other requests are still available.
        stamped_candidates: list[tuple[int, str, Path]] = []
        try:
            for path in inbox.glob("*.json"):
                try:
                    stamped_candidates.append((path.stat().st_mtime_ns, path.name, path))
                except FileNotFoundError:
                    continue
        except OSError:
            pass
        candidates = [item[2] for item in sorted(stamped_candidates)]
        for path in candidates:
            claim_token = uuid.uuid4().hex
            claimed = processing / f"{path.stem}--{claim_token}.json"
            try:
                # Rename within the queue filesystem is the claim. Competing
                # workers use unique destinations, but only one can remove the
                # inbox source, so a request can only be returned once.
                os.replace(path, claimed)
            except FileNotFoundError:
                continue
            except OSError:
                continue
            try:
                value = json.loads(claimed.read_text(encoding="utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("viewer request must be a JSON object")
                attempts = self._claim_attempts(value) + 1
                value["_viewer_claim"] = {
                    "token": claim_token,
                    "claimed_at": datetime.now(UTC).isoformat(),
                    "pid": os.getpid(),
                    "hostname": socket.gethostname(),
                    "attempt": attempts,
                }
                self._atomic_json_write(claimed, value)
                os.utime(claimed, None)
                return claimed, value
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                self._fail_claim(claimed, f"invalid_claim: {exc}")
        return None

    def complete(self, request_path: Path, result: Mapping[str, Any]) -> Path:
        outbox = self.root / "outbox"
        processed = self.root / "processed"
        outbox.mkdir(parents=True, exist_ok=True)
        processed.mkdir(parents=True, exist_ok=True)
        request_path = request_path.resolve()
        if request_path.parent != (self.root / "processing").resolve():
            raise ValueError("viewer request is not an active processing claim")
        request_id = str(result.get("request_id") or request_path.stem.split("--", 1)[0])
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", request_id):
            raise ValueError("invalid viewer result request id")
        target = outbox / f"{request_id}.json"
        self._atomic_json_write(target, dict(result))
        request_path.replace(processed / request_path.name)
        return target

    def _recover_stale_claims(self) -> None:
        processing = self.root / "processing"
        if not processing.is_dir():
            return
        now = datetime.now(UTC).timestamp()
        for path in processing.glob("*.json"):
            recovered: Path | None = None
            try:
                if now - path.stat().st_mtime <= self.claim_timeout_seconds:
                    continue
                recovery = self.root / "recovery"
                recovery.mkdir(parents=True, exist_ok=True)
                recovered = recovery / f"{path.stem}--{uuid.uuid4().hex}.json"
                # Atomically take responsibility for stale-claim recovery.
                # If another worker won the race, this source no longer exists.
                os.replace(path, recovered)
                value = json.loads(recovered.read_text(encoding="utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("claim is not a JSON object")
                claim = value.get("_viewer_claim")
                if isinstance(claim, Mapping) and self._claim_owner_alive(claim):
                    # A slow evaluator is safer than duplicate evaluation.
                    os.replace(recovered, path)
                    os.utime(path, None)
                    continue
                attempts = self._claim_attempts(value)
                request_id = str(value.get("request_id") or "")
                if (
                    attempts >= self.max_claim_attempts
                    or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", request_id)
                ):
                    self._fail_claim(recovered, "stale_claim_attempts_exhausted")
                    continue
                value.pop("_viewer_claim", None)
                value["_viewer_claim_attempts"] = attempts
                inbox = self.root / "inbox"
                inbox.mkdir(parents=True, exist_ok=True)
                target = inbox / f"{request_id}.json"
                if target.exists():
                    self._fail_claim(recovered, "stale_claim_inbox_collision")
                    continue
                self._atomic_json_write(target, value)
                recovered.unlink()
                self._audit("stale_claim_requeued", recovered, {"request_id": request_id, "attempt": attempts})
            except FileNotFoundError:
                continue
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                failed_path = recovered if recovered is not None and recovered.exists() else path
                self._fail_claim(failed_path, f"stale_claim_unrecoverable: {exc}")

    @staticmethod
    def _claim_attempts(value: Mapping[str, Any]) -> int:
        claim = value.get("_viewer_claim")
        if isinstance(claim, Mapping):
            attempt = claim.get("attempt")
            if isinstance(attempt, int) and not isinstance(attempt, bool):
                return max(0, attempt)
        attempts = value.get("_viewer_claim_attempts")
        return max(0, attempts) if isinstance(attempts, int) and not isinstance(attempts, bool) else 0

    @staticmethod
    def _claim_owner_alive(claim: Mapping[str, Any]) -> bool:
        if claim.get("hostname") != socket.gethostname():
            return False
        pid = claim.get("pid")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return False
        if pid == os.getpid():
            return True
        if sys.platform == "win32":
            # On Windows, os.kill(pid, 0) is not the harmless POSIX existence
            # probe. Query a process handle without requesting termination or
            # mutation rights instead.
            import ctypes

            process_query_limited_information = 0x1000
            still_active = 259
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
            if not handle:
                # Access denied still means a process exists; err toward not
                # duplicating a potentially live independent evaluation.
                return ctypes.get_last_error() == 5
            try:
                exit_code = ctypes.c_ulong()
                return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == still_active
            finally:
                kernel32.CloseHandle(handle)
        try:
            os.kill(pid, 0)
        except (OSError, PermissionError):
            return False
        return True

    def _fail_claim(self, path: Path, reason: str) -> None:
        failed = self.root / "failed"
        failed.mkdir(parents=True, exist_ok=True)
        target = failed / path.name
        try:
            path.replace(target)
        except OSError:
            target = path
        self._audit("claim_failed", target, {"reason": reason[:500]})

    def _audit(self, event: str, path: Path, detail: Mapping[str, Any]) -> None:
        audit = self.root / "audit"
        audit.mkdir(parents=True, exist_ok=True)
        record = {
            "event": event,
            "at": datetime.now(UTC).isoformat(),
            "path": path.name,
            **dict(detail),
        }
        self._atomic_json_write(audit / f"{uuid.uuid4().hex}.json", record)

    @staticmethod
    def _atomic_json_write(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        temporary.write_text(json.dumps(dict(value), ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.replace(temporary, path)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise
