"""Manager-owned campaign revisions and durable operator authorizations.

A revision snapshot is immutable. ``HEAD.json`` is replaced last and is the sole
current-state commit point, so stale review evidence and authorizations cannot
be mistaken for current control-plane state after a restart.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

import portalocker

CONTROL_DIRNAME = "campaign-control"
HEAD_FILENAME = "HEAD.json"
AUTHORIZATION_LOG = "operator-authorizations.jsonl"
CONTROL_VERSION = 1
_ALLOWED_ACTIONS = frozenset(
    {
        "validator_repair",
        "acceptance_retry",
        "provenance_repair",
        "artifact_refresh",
        "resume_blocked_work",
    }
)


@dataclass(frozen=True)
class CampaignIdentity:
    campaign_id: str
    objective: str
    campaign_epoch: int


@dataclass(frozen=True)
class ControlHead:
    campaign_id: str
    objective: str
    campaign_epoch: int
    state_revision: int
    snapshot: str
    committed_at: float


@dataclass(frozen=True)
class Authorization:
    authorization_id: str
    campaign_id: str
    objective: str
    campaign_epoch: int
    state_revision: int
    blocker_fingerprint: str
    allowed_actions: tuple[str, ...]
    scope: str
    allowed_write_paths: tuple[str, ...]
    nonce: str
    source_channel: str
    source_message_id: str
    issued_at: float
    expires_at: float
    event: str = "issued"
    validator_id: str = ""
    acceptance_retries: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RepairCapability:
    capability_id: str
    authorization_id: str
    campaign_id: str
    objective: str
    campaign_epoch: int
    state_revision: int
    action: str
    validator_id: str
    allowed_write_paths: tuple[str, ...]
    nonce: str
    mission_id: str
    status: str
    acceptance_retries_remaining: int
    claimed_at: float


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _safe_relative_path(raw: object) -> str:
    value = str(raw or "").strip().replace("\\", "/")
    if "\x00" in value:
        raise ValueError("path contains a null byte")
    parts = Path(value).parts
    if not value or value.startswith("/") or ".." in parts:
        raise ValueError(f"path must be project-relative: {value!r}")
    normalized = Path(*parts).as_posix()
    if normalized in {"", "."}:
        raise ValueError("path must identify a project child")
    return normalized


def _validated_project_path(project_root: Path, relative: object) -> tuple[str, Path]:
    """Resolve one project child without accepting symlink traversal."""
    safe = _safe_relative_path(relative)
    root = project_root.expanduser().resolve(strict=False)
    candidate = root
    for part in Path(safe).parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError(f"project path must not traverse a symlink: {safe}")
    try:
        candidate.resolve(strict=False).relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValueError(f"project path escaped its root: {safe}") from exc
    return safe, candidate


def _validated_write_paths(
    project_root: Path,
    paths: Iterable[object],
) -> tuple[str, ...]:
    """Validate the exact writable files an authorization names."""
    validated: list[str] = []
    for value in paths:
        safe, path = _validated_project_path(project_root, value)
        if path.is_dir():
            raise ValueError("validator repair writable paths must identify files")
        validated.append(safe)
    return tuple(dict.fromkeys(validated))


class CampaignControlStore:
    """Serialize Manager control mutations and commit immutable revisions."""

    def __init__(self, state_root: Path | str, *, project_root: Path | str | None = None):
        self.state_root = Path(state_root)
        self.project_root = Path(project_root) if project_root is not None else self.state_root
        self.control_root = self.state_root / CONTROL_DIRNAME
        self.revisions_root = self.control_root / "revisions"
        self.head_path = self.control_root / HEAD_FILENAME
        self.authorization_path = self.state_root / AUTHORIZATION_LOG
        self.lock_path = self.state_root / ".manager-control.lock"

    @contextmanager
    def locked(self):
        self.state_root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as handle:
            # Deliberately blocking: control-state mutations fail closed and
            # historically waited for the current immutable revision commit.
            portalocker.lock(handle, portalocker.LOCK_EX)
            try:
                yield
            finally:
                portalocker.unlock(handle)

    def campaign_identity(
        self,
        *,
        objective: str = "",
        campaign_epoch: int | None = None,
    ) -> CampaignIdentity:
        continuous = _read_json(self.state_root / "continuous.json") or {}
        current_objective = str(objective or continuous.get("objective") or "").strip()
        epoch = (
            max(0, int(campaign_epoch))
            if campaign_epoch is not None
            else max(0, int(continuous.get("generation", 0) or 0))
        )
        return CampaignIdentity(
            campaign_id=f"campaign-{epoch}",
            objective=current_objective,
            campaign_epoch=epoch,
        )

    def read_head(self) -> ControlHead | None:
        value = _read_json(self.head_path)
        if not value or value.get("version") != CONTROL_VERSION:
            return None
        try:
            return ControlHead(
                campaign_id=str(value["campaign_id"]),
                objective=str(value["objective"]),
                campaign_epoch=int(value["campaign_epoch"]),
                state_revision=int(value["state_revision"]),
                snapshot=str(value["snapshot"]),
                committed_at=float(value["committed_at"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def read_snapshot(self, head: ControlHead | None = None) -> dict[str, Any] | None:
        current = head or self.read_head()
        if current is None:
            return None
        value = _read_json(self.control_root / current.snapshot)
        if not value or value.get("version") != CONTROL_VERSION:
            return None
        if (
            value.get("campaign_id") != current.campaign_id
            or int(value.get("state_revision", -1)) != current.state_revision
        ):
            return None
        return value

    def _next_revision_unlocked(
        self,
        *,
        identity: CampaignIdentity,
        updates: dict[str, Any],
        reason: str,
    ) -> tuple[ControlHead, dict[str, Any]]:
        current = self.read_head()
        same_campaign = bool(
            current is not None
            and current.campaign_id == identity.campaign_id
            and current.objective == identity.objective
            and current.campaign_epoch == identity.campaign_epoch
        )
        previous = self.read_snapshot(current) if same_campaign else None
        revision = (current.state_revision + 1) if same_campaign else 1
        snapshot: dict[str, Any] = {
            "version": CONTROL_VERSION,
            "campaign_id": identity.campaign_id,
            "objective": identity.objective,
            "campaign_epoch": identity.campaign_epoch,
            "state_revision": revision,
            "reason": str(reason or "manager control update")[:500],
            "committed_at": time.time(),
            "authorization_ids": [],
            "active_capability": None,
            "stage_projection": {},
            "terminal_evidence": [],
        }
        if previous is not None:
            for field_name in (
                "authorization_ids",
                "active_capability",
                "stage_projection",
                "terminal_evidence",
            ):
                snapshot[field_name] = previous.get(field_name, snapshot[field_name])
        snapshot.update(updates)
        relative = f"revisions/{identity.campaign_id}-{revision}-{uuid.uuid4().hex[:8]}.json"
        head = ControlHead(
            campaign_id=identity.campaign_id,
            objective=identity.objective,
            campaign_epoch=identity.campaign_epoch,
            state_revision=revision,
            snapshot=relative,
            committed_at=float(snapshot["committed_at"]),
        )
        _atomic_write_json(self.control_root / relative, snapshot)
        _atomic_write_json(
            self.head_path,
            {"version": CONTROL_VERSION, **asdict(head)},
        )
        return head, snapshot

    def commit_revision(
        self,
        *,
        identity: CampaignIdentity,
        updates: dict[str, Any],
        reason: str,
    ) -> tuple[ControlHead, dict[str, Any]]:
        with self.locked():
            return self._next_revision_unlocked(
                identity=identity,
                updates=updates,
                reason=reason,
            )

    def record_review_evidence_if_current(
        self,
        *,
        identity: CampaignIdentity,
        expected_head: ControlHead | None,
        stage_projection: dict[str, Any],
        terminal_evidence: Iterable[dict[str, Any]],
        reason: str,
    ) -> ControlHead | None:
        """Publish one verdict only if its campaign and control revision remain current.

        The caller holds the Manager pipeline lock to serialize objective changes.
        """
        with self.locked():
            if self.campaign_identity() != identity or self.read_head() != expected_head:
                return None
            head, _ = self._next_revision_unlocked(
                identity=identity,
                updates={
                    "stage_projection": dict(stage_projection),
                    "terminal_evidence": [dict(row) for row in terminal_evidence],
                },
                reason=reason,
            )
            return head

    def issue_authorization(
        self,
        *,
        identity: CampaignIdentity,
        blocker_fingerprint: str,
        allowed_actions: Iterable[str],
        scope: str,
        allowed_write_paths: Iterable[str] = (),
        source_channel: str,
        source_message_id: str,
        expires_at: float = 0.0,
        validator_id: str = "",
        acceptance_retries: int = 0,
        metadata: dict[str, Any] | None = None,
        expected_state_revision: int | None = None,
    ) -> Authorization:
        actions = tuple(
            dict.fromkeys(
                str(value or "").strip().lower()
                for value in allowed_actions
                if str(value or "").strip().lower() in _ALLOWED_ACTIONS
            )
        )
        if not actions:
            raise ValueError("authorization requires at least one allowed action")
        blocker = str(blocker_fingerprint or "").strip()
        if not blocker:
            raise ValueError("authorization requires a blocker fingerprint")
        safe_allowed = _validated_write_paths(
            self.project_root,
            allowed_write_paths,
        )
        if "validator_repair" in actions:
            if not str(validator_id or "").strip():
                raise ValueError("validator repair requires Reviewer validator_id")
            if not safe_allowed:
                raise ValueError("validator repair requires explicit writable paths")
            if max(0, min(1, int(acceptance_retries or 0))) != 1:
                raise ValueError("validator repair requires exactly one acceptance retry")
        issued_at = time.time()
        if expires_at and float(expires_at) <= issued_at:
            raise ValueError("authorization expiry must be in the future")
        with self.locked():
            current = self.read_head()
            if current is not None and (
                current.campaign_id != identity.campaign_id
                or current.objective != identity.objective
                or current.campaign_epoch != identity.campaign_epoch
            ):
                current = None
            if expected_state_revision is not None and (
                current is None or current.state_revision != int(expected_state_revision)
            ):
                raise ValueError("Manager HEAD changed before authorization issuance")
            next_revision = (current.state_revision + 1) if current else 1
            authorization = Authorization(
                authorization_id=f"auth-{uuid.uuid4().hex[:16]}",
                campaign_id=identity.campaign_id,
                objective=identity.objective,
                campaign_epoch=identity.campaign_epoch,
                state_revision=next_revision,
                blocker_fingerprint=blocker,
                allowed_actions=actions,
                scope=str(scope or "bounded").strip()[:200],
                allowed_write_paths=safe_allowed,
                nonce=secrets.token_urlsafe(24),
                source_channel=str(source_channel or "unknown").strip()[:100],
                source_message_id=str(source_message_id or "").strip()[:200],
                issued_at=issued_at,
                expires_at=max(0.0, float(expires_at or 0.0)),
                validator_id=str(validator_id or "").strip()[:200],
                acceptance_retries=max(0, min(1, int(acceptance_retries or 0))),
                metadata=dict(metadata or {}),
            )
            _append_jsonl(
                self.authorization_path,
                {"version": CONTROL_VERSION, **asdict(authorization)},
            )
            head, _ = self._next_revision_unlocked(
                identity=identity,
                updates={"authorization_ids": [authorization.authorization_id]},
                reason="Manager issued operator authorization",
            )
            if head.state_revision != authorization.state_revision:
                raise RuntimeError("authorization revision commit mismatch")
            return authorization

    def authorization_events(self) -> list[dict[str, Any]]:
        try:
            lines = self.authorization_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        rows: list[dict[str, Any]] = []
        for line in lines:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
        return rows

    def get_authorization(self, authorization_id: str) -> dict[str, Any] | None:
        events = [
            row
            for row in self.authorization_events()
            if row.get("authorization_id") == authorization_id
        ]
        return events[-1] if events else None

    def current_authorizations(self) -> list[dict[str, Any]]:
        head = self.read_head()
        snapshot = self.read_snapshot(head)
        if head is None or snapshot is None:
            return []
        rows: list[dict[str, Any]] = []
        for authorization_id in snapshot.get("authorization_ids") or []:
            row = self.get_authorization(str(authorization_id))
            if row is not None and row.get("event") == "issued":
                rows.append(row)
        return rows

    def current_repair_capability(self, *, mission_id: str) -> dict[str, Any] | None:
        with self.locked():
            head = self.read_head()
            snapshot = self.read_snapshot(head)
            raw = snapshot.get("active_capability") if snapshot else None
            latest = next(
                (
                    row
                    for row in reversed(self.authorization_events())
                    if row.get("mission_id") == str(mission_id or "")
                    and row.get("event")
                    in {
                        "claimed",
                        "acceptance_started",
                        "closed",
                    }
                ),
                None,
            )
            if not isinstance(latest, dict) or head is None or snapshot is None:
                return None
            identity = CampaignIdentity(
                campaign_id=str(latest.get("campaign_id") or ""),
                objective=str(latest.get("objective") or ""),
                campaign_epoch=int(latest.get("campaign_epoch") or 0),
            )
            if (
                head.campaign_id != identity.campaign_id
                or head.objective != identity.objective
                or head.campaign_epoch != identity.campaign_epoch
            ):
                return None
            capability = {
                key: latest[key] for key in RepairCapability.__dataclass_fields__ if key in latest
            }
            if len(capability) != len(RepairCapability.__dataclass_fields__):
                return None
            capability_id = str(capability["capability_id"])
            active_matches = bool(
                isinstance(raw, dict)
                and raw.get("capability_id") == capability_id
                and raw.get("mission_id") == str(mission_id or "")
            )
            if isinstance(raw, dict) and not active_matches:
                return None

            if latest.get("event") == "closed":
                if active_matches:
                    self._next_revision_unlocked(
                        identity=identity,
                        updates={"active_capability": None},
                        reason="recovered durable validator repair settlement",
                    )
                return dict(latest)

            if latest.get("event") == "claimed" and not active_matches:
                if int(latest.get("state_revision") or -1) != head.state_revision + 1 or str(
                    latest.get("authorization_id") or ""
                ) not in set(snapshot.get("authorization_ids") or []):
                    return None
                self._next_revision_unlocked(
                    identity=identity,
                    updates={
                        "authorization_ids": [],
                        "active_capability": capability,
                    },
                    reason="recovered durable validator repair claim",
                )
                return capability

            if latest.get("event") == "claimed":
                return dict(raw)

            if latest.get("event") != "acceptance_started":
                return None
            if not active_matches:
                return None
            if raw.get("status") == "claimed":
                if int(latest.get("state_revision") or -1) != head.state_revision + 1:
                    return None
                head, _ = self._next_revision_unlocked(
                    identity=identity,
                    updates={"active_capability": capability},
                    reason="recovered consumed validator acceptance retry",
                )
            elif raw.get("status") != "acceptance_started":
                return None

            closed = {
                **capability,
                "event": "closed",
                "status": "rejected",
                "accepted": False,
                "reason": (
                    "validator acceptance was interrupted before durable "
                    "settlement; the one-shot retry was not replayed"
                ),
                "guard_errors": ["acceptance outcome unavailable after restart"],
                "closed_at": time.time(),
                "state_revision": head.state_revision + 1,
            }
            _append_jsonl(self.authorization_path, closed)
            self._next_revision_unlocked(
                identity=identity,
                updates={"active_capability": None},
                reason="rejected interrupted validator acceptance retry",
            )
            return closed

    @staticmethod
    def public_authorization(row: dict[str, Any]) -> dict[str, Any]:
        """Return the non-secret subset suitable for Planner context/events."""
        return {
            "authorization_id": str(row.get("authorization_id") or ""),
            "allowed_actions": list(row.get("allowed_actions") or []),
            "scope": str(row.get("scope") or ""),
            "validator_id": str(row.get("validator_id") or ""),
            "allowed_write_paths": list(row.get("allowed_write_paths") or []),
            "issued_at": float(row.get("issued_at") or 0.0),
            "expires_at": float(row.get("expires_at") or 0.0),
        }

    def _validate_issued_unlocked(
        self,
        *,
        authorization_id: str,
        nonce: str,
        action: str,
        identity: CampaignIdentity,
    ) -> tuple[dict[str, Any], ControlHead, dict[str, Any]]:
        issued = self.get_authorization(authorization_id)
        if issued is None or issued.get("event") != "issued":
            raise ValueError("authorization is unavailable or already consumed")
        if not secrets.compare_digest(str(issued.get("nonce") or ""), str(nonce or "")):
            raise ValueError("authorization nonce mismatch")
        if (
            issued.get("campaign_id") != identity.campaign_id
            or issued.get("objective") != identity.objective
            or int(issued.get("campaign_epoch", -1)) != identity.campaign_epoch
        ):
            raise ValueError("authorization campaign mismatch")
        head = self.read_head()
        snapshot = self.read_snapshot(head)
        if (
            head is None
            or snapshot is None
            or head.campaign_id != identity.campaign_id
            or head.objective != identity.objective
            or head.campaign_epoch != identity.campaign_epoch
            or head.state_revision != int(issued.get("state_revision", -1))
            or authorization_id not in set(snapshot.get("authorization_ids") or [])
        ):
            raise ValueError("authorization is stale relative to Manager HEAD")
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in set(issued.get("allowed_actions") or []):
            raise ValueError("action is outside authorization scope")
        expires_at = float(issued.get("expires_at") or 0.0)
        if expires_at > 0 and time.time() >= expires_at:
            raise ValueError("authorization expired")
        return issued, head, snapshot

    def claim_repair_capability(
        self,
        *,
        authorization_id: str,
        nonce: str,
        action: str,
        identity: CampaignIdentity,
        mission_id: str,
    ) -> RepairCapability:
        normalized_action = str(action or "").strip().lower()
        if normalized_action != "validator_repair":
            raise ValueError("only validator_repair supports a repair capability")
        with self.locked():
            issued, head, snapshot = self._validate_issued_unlocked(
                authorization_id=authorization_id,
                nonce=nonce,
                action=normalized_action,
                identity=identity,
            )
            terminal = list(snapshot.get("terminal_evidence") or [])
            diagnosis = terminal[-1] if terminal and isinstance(terminal[-1], dict) else {}
            if diagnosis.get("failure_source") != "validator_defect":
                raise ValueError("Reviewer did not diagnose a validator defect")
            validator_id = str(issued.get("validator_id") or "")
            if not validator_id or diagnosis.get("validator_id") != validator_id:
                raise ValueError("Reviewer validator diagnosis does not match authorization")
            capability = RepairCapability(
                capability_id=f"repair-{uuid.uuid4().hex[:16]}",
                authorization_id=authorization_id,
                campaign_id=identity.campaign_id,
                objective=identity.objective,
                campaign_epoch=identity.campaign_epoch,
                state_revision=head.state_revision + 1,
                action=normalized_action,
                validator_id=validator_id,
                allowed_write_paths=tuple(issued.get("allowed_write_paths") or []),
                nonce=str(issued.get("nonce") or ""),
                mission_id=str(mission_id or ""),
                status="claimed",
                acceptance_retries_remaining=int(issued.get("acceptance_retries") or 0),
                claimed_at=time.time(),
            )
            event = {**issued, **asdict(capability), "event": "claimed"}
            _append_jsonl(self.authorization_path, event)
            claimed_head, _ = self._next_revision_unlocked(
                identity=identity,
                updates={
                    "authorization_ids": [],
                    "active_capability": asdict(capability),
                },
                reason="validator repair capability claimed",
            )
            if claimed_head.state_revision != capability.state_revision:
                raise RuntimeError("repair capability revision mismatch")
            return capability

    def begin_acceptance_retry(
        self,
        *,
        capability_id: str,
        nonce: str,
        identity: CampaignIdentity,
    ) -> RepairCapability:
        with self.locked():
            head = self.read_head()
            snapshot = self.read_snapshot(head)
            raw = snapshot.get("active_capability") if snapshot else None
            if not isinstance(raw, dict) or raw.get("capability_id") != capability_id:
                raise ValueError("repair capability is not current")
            if raw.get("status") != "claimed":
                raise ValueError("acceptance retry already started")
            if not secrets.compare_digest(str(raw.get("nonce") or ""), str(nonce or "")):
                raise ValueError("repair capability nonce mismatch")
            if (
                head is None
                or head.campaign_id != identity.campaign_id
                or head.objective != identity.objective
                or head.campaign_epoch != identity.campaign_epoch
            ):
                raise ValueError("repair capability campaign mismatch")
            remaining = int(raw.get("acceptance_retries_remaining") or 0)
            if remaining != 1:
                raise ValueError("acceptance retry budget is unavailable")
            updated = {
                **raw,
                "status": "acceptance_started",
                "acceptance_retries_remaining": 0,
                "acceptance_started_at": time.time(),
                "state_revision": head.state_revision + 1,
            }
            _append_jsonl(
                self.authorization_path,
                {**updated, "event": "acceptance_started"},
            )
            started_head, _ = self._next_revision_unlocked(
                identity=identity,
                updates={"active_capability": updated},
                reason="one validator acceptance retry started",
            )
            if started_head.state_revision != updated["state_revision"]:
                raise RuntimeError("acceptance retry revision mismatch")
            return RepairCapability(
                **{key: updated[key] for key in RepairCapability.__dataclass_fields__}
            )

    def close_repair_capability(
        self,
        *,
        capability_id: str,
        nonce: str,
        identity: CampaignIdentity,
        accepted: bool,
        reason: str,
    ) -> dict[str, Any]:
        with self.locked():
            head = self.read_head()
            snapshot = self.read_snapshot(head)
            raw = snapshot.get("active_capability") if snapshot else None
            if not isinstance(raw, dict) or raw.get("capability_id") != capability_id:
                raise ValueError("repair capability is not current")
            if raw.get("status") != "acceptance_started":
                raise ValueError("repair capability acceptance was not started")
            if not secrets.compare_digest(str(raw.get("nonce") or ""), str(nonce or "")):
                raise ValueError("repair capability nonce mismatch")
            if (
                head is None
                or head.campaign_id != identity.campaign_id
                or head.objective != identity.objective
                or head.campaign_epoch != identity.campaign_epoch
            ):
                raise ValueError("repair capability campaign mismatch")
            closed = {
                **raw,
                "event": "closed",
                "status": "accepted" if accepted else "rejected",
                "accepted": bool(accepted),
                "reason": str(reason or "")[:1000],
                "closed_at": time.time(),
                "state_revision": head.state_revision + 1,
            }
            _append_jsonl(self.authorization_path, closed)
            closed_head, _ = self._next_revision_unlocked(
                identity=identity,
                updates={"active_capability": None},
                reason=f"validator repair capability {closed['status']}",
            )
            if closed_head.state_revision != closed["state_revision"]:
                raise RuntimeError("repair capability close revision mismatch")
            return closed
