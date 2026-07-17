"""Manager-owned campaign revisions and durable operator authorizations.

A revision snapshot is immutable. ``HEAD.json`` is replaced last and is the sole
current-state commit point, so stale waiting contracts and authorizations cannot
be mistaken for current control-plane state after a restart.
"""
from __future__ import annotations

import hashlib
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

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]

CONTROL_DIRNAME = "campaign-control"
HEAD_FILENAME = "HEAD.json"
AUTHORIZATION_LOG = "operator-authorizations.jsonl"
CONTROL_VERSION = 1
_TREE_IGNORE_DIRS = frozenset({
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "node_modules",
    "venv",
})
_TREE_IGNORE_ROOTS = frozenset({
    ".argus_external_work",
    ".argus_subagents",
})
_ALLOWED_ACTIONS = frozenset({
    "validator_repair",
    "acceptance_retry",
    "provenance_repair",
    "artifact_refresh",
    "resume_blocked_work",
})


@dataclass(frozen=True)
class CampaignIdentity:
    campaign_id: str
    objective_sha256: str
    campaign_epoch: int


@dataclass(frozen=True)
class ControlHead:
    campaign_id: str
    objective_sha256: str
    campaign_epoch: int
    state_revision: int
    snapshot: str
    committed_at: float


@dataclass(frozen=True)
class Authorization:
    authorization_id: str
    campaign_id: str
    objective_sha256: str
    campaign_epoch: int
    state_revision: int
    blocker_fingerprint: str
    allowed_actions: tuple[str, ...]
    scope: str
    allowed_write_paths: tuple[str, ...]
    allowed_write_baseline: tuple[dict[str, str], ...]
    frozen_evidence: tuple[dict[str, str], ...]
    frozen_tree_sha256: str
    forbidden_mutations: tuple[str, ...]
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
    objective_sha256: str
    campaign_epoch: int
    state_revision: int
    action: str
    validator_id: str
    allowed_write_paths: tuple[str, ...]
    frozen_evidence: tuple[dict[str, str], ...]
    frozen_tree_sha256: str
    nonce: str
    mission_id: str
    status: str
    acceptance_retries_remaining: int
    claimed_at: float


def objective_sha256(objective: str) -> str:
    return hashlib.sha256(str(objective or "").encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
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
    """Validate exact writable files at each capability boundary."""
    validated: list[str] = []
    for value in paths:
        safe, path = _validated_project_path(project_root, value)
        if path.is_dir():
            raise ValueError("validator repair writable paths must identify files")
        validated.append(safe)
    return tuple(dict.fromkeys(validated))


def _hash_path(project_root: Path, relative: str) -> dict[str, str]:
    safe, path = _validated_project_path(project_root, relative)
    if path.is_dir():
        digest = _tree_sha256(path, excluded_paths=())
        return {"path": safe, "sha256": f"tree:{digest}"}
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        digest = "missing"
    except OSError as exc:
        raise ValueError(f"cannot freeze evidence path {safe}: {exc}") from exc
    else:
        digest = hashlib.sha256(content).hexdigest()
    return {"path": safe, "sha256": digest}


def _path_is_within(relative: str, roots: Iterable[str]) -> bool:
    path = Path(relative)
    return any(path == Path(root) or Path(root) in path.parents for root in roots)


def _tree_sha256(project_root: Path, *, excluded_paths: Iterable[str]) -> str:
    """Hash the project tree except explicitly writable and operational paths."""
    project_root = project_root.expanduser().resolve(strict=False)
    excluded = tuple(
        _validated_project_path(project_root, value)[0]
        for value in excluded_paths
    )
    digest = hashlib.sha256()
    if not project_root.exists():
        return digest.hexdigest()
    for root, dirs, files in os.walk(project_root, topdown=True, followlinks=False):
        root_path = Path(root)
        try:
            root_relative = root_path.relative_to(project_root)
        except ValueError as exc:
            raise ValueError("project tree escaped its root") from exc
        kept_dirs: list[str] = []
        for name in sorted(dirs):
            child = root_relative / name
            relative = child.as_posix()
            if name in _TREE_IGNORE_DIRS or relative in _TREE_IGNORE_ROOTS:
                continue
            if _path_is_within(relative, excluded):
                continue
            path = root_path / name
            if path.is_symlink():
                digest.update(b"L\0" + relative.encode("utf-8") + b"\0")
                digest.update(os.readlink(path).encode("utf-8") + b"\0")
                continue
            digest.update(b"D\0" + relative.encode("utf-8") + b"\0")
            kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in sorted(files):
            path = root_path / name
            relative = (root_relative / name).as_posix()
            if _path_is_within(relative, excluded):
                continue
            digest.update(b"F\0" + relative.encode("utf-8") + b"\0")
            if path.is_symlink():
                digest.update(b"LINK\0" + os.readlink(path).encode("utf-8") + b"\0")
                continue
            try:
                with path.open("rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        digest.update(chunk)
            except OSError as exc:
                raise ValueError(f"cannot freeze project path {relative}: {exc}") from exc
            digest.update(b"\0")
    return digest.hexdigest()


def _campaign_id(objective_hash: str, epoch: int) -> str:
    return f"campaign-{epoch}-{objective_hash[:16]}"


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
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def campaign_identity(
        self,
        *,
        objective: str = "",
        campaign_epoch: int | None = None,
    ) -> CampaignIdentity:
        continuous = _read_json(self.state_root / "continuous.json") or {}
        current_objective = str(
            objective or continuous.get("objective") or ""
        ).strip()
        epoch = (
            max(0, int(campaign_epoch))
            if campaign_epoch is not None
            else max(0, int(continuous.get("generation", 0) or 0))
        )
        digest = objective_sha256(current_objective)
        return CampaignIdentity(
            campaign_id=_campaign_id(digest, epoch),
            objective_sha256=digest,
            campaign_epoch=epoch,
        )

    def read_head(self) -> ControlHead | None:
        value = _read_json(self.head_path)
        if not value or value.get("version") != CONTROL_VERSION:
            return None
        try:
            return ControlHead(
                campaign_id=str(value["campaign_id"]),
                objective_sha256=str(value["objective_sha256"]),
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
            and current.objective_sha256 == identity.objective_sha256
            and current.campaign_epoch == identity.campaign_epoch
        )
        previous = self.read_snapshot(current) if same_campaign else None
        revision = (current.state_revision + 1) if same_campaign and current else 1
        snapshot: dict[str, Any] = {
            "version": CONTROL_VERSION,
            "campaign_id": identity.campaign_id,
            "objective_sha256": identity.objective_sha256,
            "campaign_epoch": identity.campaign_epoch,
            "state_revision": revision,
            "reason": str(reason or "manager control update")[:500],
            "committed_at": time.time(),
            "active_wait": None,
            "authorization_ids": [],
            "active_capability": None,
            "stage_projection": {},
            "terminal_evidence": [],
        }
        if previous is not None:
            for field_name in (
                "active_wait", "authorization_ids", "active_capability",
                "stage_projection", "terminal_evidence",
            ):
                snapshot[field_name] = previous.get(field_name, snapshot[field_name])
        snapshot.update(updates)
        relative = f"revisions/{identity.campaign_id}-{revision}.json"
        head = ControlHead(
            campaign_id=identity.campaign_id,
            objective_sha256=identity.objective_sha256,
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

    def activate_wait(
        self,
        *,
        identity: CampaignIdentity,
        wait_id: str,
        blocker_fingerprint: str,
        recheck_token: str,
        watched_paths: Iterable[str] = (),
    ) -> ControlHead:
        safe_watched_paths = [
            _safe_relative_path(path) for path in watched_paths
        ]
        head, _ = self.commit_revision(
            identity=identity,
            updates={
                "active_wait": {
                    "wait_id": str(wait_id),
                    "blocker_fingerprint": str(blocker_fingerprint),
                    "recheck_token": str(recheck_token),
                    "watched_paths": safe_watched_paths,
                },
            },
            reason="planner waiting contract activated",
        )
        return head

    def clear_wait_for_new_evidence(
        self,
        *,
        identity: CampaignIdentity,
        stage_projection: dict[str, Any] | None = None,
        terminal_evidence: Iterable[dict[str, Any]] | None = None,
        reason: str,
    ) -> ControlHead:
        updates: dict[str, Any] = {"active_wait": None}
        if stage_projection is not None:
            updates["stage_projection"] = dict(stage_projection)
        if terminal_evidence is not None:
            updates["terminal_evidence"] = [dict(row) for row in terminal_evidence]
        head, _ = self.commit_revision(
            identity=identity,
            updates=updates,
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
        evidence_paths: Iterable[str],
        forbidden_mutations: Iterable[str] = (),
        source_channel: str,
        source_message_id: str,
        expires_at: float = 0.0,
        validator_id: str = "",
        acceptance_retries: int = 0,
        metadata: dict[str, Any] | None = None,
        expected_state_revision: int | None = None,
        expected_wait_id: str = "",
    ) -> Authorization:
        actions = tuple(dict.fromkeys(
            str(value or "").strip().lower() for value in allowed_actions
            if str(value or "").strip().lower() in _ALLOWED_ACTIONS
        ))
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
        safe_forbidden = tuple(dict.fromkeys(
            _safe_relative_path(value) for value in forbidden_mutations
        ))
        if any(_path_is_within(path, safe_forbidden) for path in safe_allowed):
            raise ValueError("writable path overlaps a forbidden mutation path")
        frozen = tuple(
            _hash_path(self.project_root, value) for value in evidence_paths
        )
        allowed_baseline = tuple(
            _hash_path(self.project_root, value) for value in safe_allowed
        )
        frozen_tree_sha256 = _tree_sha256(
            self.project_root,
            excluded_paths=safe_allowed,
        )
        issued_at = time.time()
        if expires_at and float(expires_at) <= issued_at:
            raise ValueError("authorization expiry must be in the future")
        with self.locked():
            current = self.read_head()
            if current is not None and (
                current.campaign_id != identity.campaign_id
                or current.objective_sha256 != identity.objective_sha256
                or current.campaign_epoch != identity.campaign_epoch
            ):
                current = None
            if expected_state_revision is not None and (
                current is None
                or current.state_revision != int(expected_state_revision)
            ):
                raise ValueError("Manager HEAD changed before authorization issuance")
            if expected_wait_id:
                current_snapshot = self.read_snapshot(current)
                active_wait = (
                    current_snapshot.get("active_wait")
                    if current_snapshot is not None
                    else None
                )
                if (
                    not isinstance(active_wait, dict)
                    or active_wait.get("wait_id") != expected_wait_id
                    or active_wait.get("blocker_fingerprint") != blocker
                ):
                    raise ValueError("Manager wait changed before authorization issuance")
            next_revision = (current.state_revision + 1) if current else 1
            authorization = Authorization(
                authorization_id=f"auth-{uuid.uuid4().hex[:16]}",
                campaign_id=identity.campaign_id,
                objective_sha256=identity.objective_sha256,
                campaign_epoch=identity.campaign_epoch,
                state_revision=next_revision,
                blocker_fingerprint=blocker,
                allowed_actions=actions,
                scope=str(scope or "bounded").strip()[:200],
                allowed_write_paths=safe_allowed,
                allowed_write_baseline=allowed_baseline,
                frozen_evidence=frozen,
                frozen_tree_sha256=frozen_tree_sha256,
                forbidden_mutations=safe_forbidden,
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
                updates={
                    "active_wait": None,
                    "authorization_ids": [authorization.authorization_id],
                },
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
            row for row in self.authorization_events()
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
                    and row.get("event") in {
                        "claimed", "acceptance_started", "closed",
                    }
                ),
                None,
            )
            if not isinstance(latest, dict) or head is None or snapshot is None:
                return None
            identity = CampaignIdentity(
                campaign_id=str(latest.get("campaign_id") or ""),
                objective_sha256=str(latest.get("objective_sha256") or ""),
                campaign_epoch=int(latest.get("campaign_epoch") or 0),
            )
            if (
                head.campaign_id != identity.campaign_id
                or head.objective_sha256 != identity.objective_sha256
                or head.campaign_epoch != identity.campaign_epoch
            ):
                return None
            capability = {
                key: latest[key]
                for key in RepairCapability.__dataclass_fields__
                if key in latest
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
                if (
                    int(latest.get("state_revision") or -1)
                    != head.state_revision + 1
                    or str(latest.get("authorization_id") or "")
                    not in set(snapshot.get("authorization_ids") or [])
                ):
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
                return dict(raw) if active_matches else None

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
            "frozen_evidence": [
                {"path": str(value.get("path") or ""), "sha256": str(value.get("sha256") or "")}
                for value in (row.get("frozen_evidence") or [])
                if isinstance(value, dict)
            ],
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
            or issued.get("objective_sha256") != identity.objective_sha256
            or int(issued.get("campaign_epoch", -1)) != identity.campaign_epoch
        ):
            raise ValueError("authorization campaign mismatch")
        head = self.read_head()
        snapshot = self.read_snapshot(head)
        if (
            head is None
            or snapshot is None
            or head.campaign_id != identity.campaign_id
            or head.objective_sha256 != identity.objective_sha256
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
        expected_frozen = list(issued.get("frozen_evidence") or [])
        current_frozen = [
            _hash_path(self.project_root, row.get("path"))
            for row in expected_frozen if isinstance(row, dict)
        ]
        if current_frozen != expected_frozen:
            raise ValueError("frozen evidence changed")
        expected_allowed = list(issued.get("allowed_write_baseline") or [])
        _validated_write_paths(
            self.project_root,
            issued.get("allowed_write_paths") or [],
        )
        current_allowed = [
            _hash_path(self.project_root, row.get("path"))
            for row in expected_allowed if isinstance(row, dict)
        ]
        if current_allowed != expected_allowed:
            raise ValueError("authorized validator changed before capability claim")
        frozen_tree = _tree_sha256(
            self.project_root,
            excluded_paths=issued.get("allowed_write_paths") or [],
        )
        if frozen_tree != str(issued.get("frozen_tree_sha256") or ""):
            raise ValueError("frozen project tree changed")
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
                objective_sha256=identity.objective_sha256,
                campaign_epoch=identity.campaign_epoch,
                state_revision=head.state_revision + 1,
                action=normalized_action,
                validator_id=validator_id,
                allowed_write_paths=tuple(issued.get("allowed_write_paths") or []),
                frozen_evidence=tuple(issued.get("frozen_evidence") or []),
                frozen_tree_sha256=str(issued.get("frozen_tree_sha256") or ""),
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
                or head.objective_sha256 != identity.objective_sha256
                or head.campaign_epoch != identity.campaign_epoch
            ):
                raise ValueError("repair capability campaign mismatch")
            expected_frozen = list(raw.get("frozen_evidence") or [])
            _validated_write_paths(
                self.project_root,
                raw.get("allowed_write_paths") or [],
            )
            current_frozen = [
                _hash_path(self.project_root, row.get("path"))
                for row in expected_frozen if isinstance(row, dict)
            ]
            if current_frozen != expected_frozen:
                raise ValueError("frozen evidence changed")
            tree_digest = _tree_sha256(
                self.project_root,
                excluded_paths=raw.get("allowed_write_paths") or [],
            )
            if tree_digest != str(raw.get("frozen_tree_sha256") or ""):
                raise ValueError("write occurred outside authorized validator paths")
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
            return RepairCapability(**{
                key: updated[key]
                for key in RepairCapability.__dataclass_fields__
            })

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
                or head.objective_sha256 != identity.objective_sha256
                or head.campaign_epoch != identity.campaign_epoch
            ):
                raise ValueError("repair capability campaign mismatch")
            expected_frozen = list(raw.get("frozen_evidence") or [])
            _validated_write_paths(
                self.project_root,
                raw.get("allowed_write_paths") or [],
            )
            current_frozen = [
                _hash_path(self.project_root, row.get("path"))
                for row in expected_frozen if isinstance(row, dict)
            ]
            tree_digest = _tree_sha256(
                self.project_root,
                excluded_paths=raw.get("allowed_write_paths") or [],
            )
            guard_errors: list[str] = []
            if current_frozen != expected_frozen:
                guard_errors.append("frozen evidence changed")
            if tree_digest != str(raw.get("frozen_tree_sha256") or ""):
                guard_errors.append("write occurred outside authorized validator paths")
            final_accepted = bool(accepted and not guard_errors)
            closed = {
                **raw,
                "event": "closed",
                "status": "accepted" if final_accepted else "rejected",
                "accepted": final_accepted,
                "reason": str(reason or "")[:1000],
                "guard_errors": guard_errors,
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

    def consume_authorization(
        self,
        *,
        authorization_id: str,
        nonce: str,
        action: str,
        identity: CampaignIdentity,
        evidence_paths: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        normalized_action = str(action or "").strip().lower()
        with self.locked():
            events = [
                row for row in self.authorization_events()
                if row.get("authorization_id") == authorization_id
            ]
            if not events or events[-1].get("event") != "issued":
                raise ValueError("authorization is unavailable or already consumed")
            issued = events[-1]
            if not secrets.compare_digest(str(issued.get("nonce") or ""), str(nonce or "")):
                raise ValueError("authorization nonce mismatch")
            if (
                issued.get("campaign_id") != identity.campaign_id
                or issued.get("objective_sha256") != identity.objective_sha256
                or int(issued.get("campaign_epoch", -1)) != identity.campaign_epoch
            ):
                raise ValueError("authorization campaign mismatch")
            head = self.read_head()
            snapshot = self.read_snapshot(head)
            if (
                head is None
                or snapshot is None
                or head.campaign_id != identity.campaign_id
                or head.objective_sha256 != identity.objective_sha256
                or head.campaign_epoch != identity.campaign_epoch
                or head.state_revision != int(issued.get("state_revision", -1))
                or authorization_id not in set(snapshot.get("authorization_ids") or [])
            ):
                raise ValueError("authorization is stale relative to Manager HEAD")
            if normalized_action not in set(issued.get("allowed_actions") or []):
                raise ValueError("action is outside authorization scope")
            expires_at = float(issued.get("expires_at") or 0.0)
            if expires_at > 0 and time.time() >= expires_at:
                raise ValueError("authorization expired")
            expected_frozen = list(issued.get("frozen_evidence") or [])
            current_frozen = [
                _hash_path(self.project_root, row.get("path"))
                for row in expected_frozen if isinstance(row, dict)
            ]
            if current_frozen != expected_frozen:
                raise ValueError("frozen evidence changed")
            if evidence_paths is not None:
                requested = [_safe_relative_path(path) for path in evidence_paths]
                if requested != [row["path"] for row in expected_frozen]:
                    raise ValueError("evidence set differs from authorization")
            consumed = {
                **issued,
                "event": "consumed",
                "consumed_at": time.time(),
                "consumed_action": normalized_action,
                "consumed_state_revision": head.state_revision + 1,
            }
            _append_jsonl(self.authorization_path, consumed)
            consumed_head, _ = self._next_revision_unlocked(
                identity=identity,
                updates={"authorization_ids": []},
                reason=f"Manager authorization consumed: {normalized_action}",
            )
            if consumed_head.state_revision != consumed["consumed_state_revision"]:
                raise RuntimeError("authorization consumption revision mismatch")
            return consumed

    def is_wait_current(
        self,
        *,
        campaign_epoch: int,
        state_revision: int,
        wait_id: str,
    ) -> bool:
        head = self.read_head()
        snapshot = self.read_snapshot(head)
        active = snapshot.get("active_wait") if snapshot else None
        return bool(
            head is not None
            and head.campaign_epoch == int(campaign_epoch)
            and head.state_revision == int(state_revision)
            and isinstance(active, dict)
            and active.get("wait_id") == wait_id
        )
