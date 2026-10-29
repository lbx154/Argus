"""Scoped operator authority with durable consumption and bounded checkpoints."""

from __future__ import annotations

import json
import logging
import os
import re
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal, TypeAlias, cast

from .file_lock import exclusive_file_lock
from .operator_context_storage import (
    DELIVERY_STATE_KEY,
    MAX_SOURCE_BYTES,
    ContextDocument,
    bounded_mission,
    checkpoint_bytes,
    compact_document,
    preference_key,
    read_document,
    settled_revisions,
    write_checkpoint,
)
from .operator_context_storage import (
    NO_MISSION as _NO_MISSION,
)

LEDGER_FILENAME = "operator_context.jsonl"
PROJECTION_FILENAME = "operator_context.json"
LOCK_FILENAME = "operator_context.lock"
OWNERSHIP_FILENAME = "operator_context.owner"
log = logging.getLogger(__name__)

Scope = Literal["mission", "project", "global"]
Lifetime = Literal["standing", "bounded_increment", "once"]
Role = Literal["manager", "planner", "engineer", "reviewer"]
PreferenceKind = Literal["autonomy", "interaction", "workflow"]
IntakeKind = Literal[
    "ephemeral",
    "objective_amendment",
    "standing_directive",
    "preference",
    "credential_grant",
    "revocation",
]
AppliesToRoles: TypeAlias = tuple[str, ...] | Literal["all"]

_SCOPES = frozenset({"mission", "project", "global"})
_LIFETIMES = frozenset({"standing", "bounded_increment", "once"})
# "teammate" is retired but still named by records written before Agent Teams went.
_ROLES = frozenset({"manager", "planner", "engineer", "reviewer", "teammate"})
_PREFERENCE_KINDS = frozenset({"autonomy", "interaction", "workflow"})
_SCOPE_ORDER = {"global": 0, "project": 1, "mission": 2}


class OperatorContextUnavailable(RuntimeError):
    """Required current role policy could not be projected before a model call."""

JUDGMENT_INSTRUCTION = (
    "Before asking the operator, judge the objective together with OperatorContext. "
    "Ask only when no authorized role can decide: unavailable credentials, new "
    "spending, irreversible/outward action, or an operator-owned acceptance "
    "boundary, which includes picking among readings of their requirement that "
    "deliver different behavior, however reversible. Choose and disclose "
    "reversible technical, project-local, tooling, layout, and routing decisions "
    "yourself. A no-questions preference does not invent missing authority."
)


class StaleOperatorContextWrite(RuntimeError):
    """The ledger changed after a writer read its revision."""


class OperatorContextCapacityError(ValueError):
    """Live operator authority cannot be silently evicted to fit a storage cap."""


def operator_context_state_root(memory: Any) -> Path:
    value = getattr(memory, "project_root", None) or getattr(memory, "root", None)
    if value is None:
        raise ValueError("runtime memory must provide a project state root")
    return Path(value)


def operator_context_global_root(
    life_dir: Path | str,
    global_root: Path | str | None = None,
) -> Path | None:
    """Resolve the caller's user namespace without consulting ambient global state."""
    lexical = Path(life_dir).expanduser().absolute()
    project = lexical.resolve()
    inferred = lexical.parent.parent.resolve() if lexical.parent.name == "projects" else None
    if inferred is not None and project != inferred / "projects" / lexical.name:
        raise ValueError("operator context project state cannot alias another project or user")
    supplied = Path(global_root).expanduser().resolve() if global_root is not None else None
    if supplied is not None and inferred is not None and supplied != inferred:
        raise ValueError("operator context project and global root belong to different users")
    return supplied or inferred


def preference_state_root(
    life_dir: Path | str,
    *,
    scope: Scope,
    global_root: Path | str | None = None,
) -> Path:
    shared = operator_context_global_root(life_dir, global_root)
    return shared if scope == "global" and shared is not None else Path(life_dir)


@dataclass(frozen=True)
class DirectiveRecord:
    text: str
    scope: Scope
    applies_to_roles: AppliesToRoles
    lifetime: Lifetime
    source: str
    revision: int
    created_at: str
    mission_id: str = ""
    type: Literal["directive"] = "directive"


@dataclass(frozen=True)
class PreferenceRecord:
    kind: PreferenceKind
    value: str
    scope: Scope
    applies_to_roles: AppliesToRoles
    revision: int
    type: Literal["preference"] = "preference"


@dataclass(frozen=True)
class CapabilityRecord:
    kind: str
    available: bool
    route: str
    secret_ref: str
    scope: Scope
    revision: int
    type: Literal["capability"] = "capability"


@dataclass(frozen=True)
class RevokeRecord:
    target_revision: int
    reason: str
    revision: int
    type: Literal["revoke"] = "revoke"


OperatorRecord: TypeAlias = DirectiveRecord | PreferenceRecord | CapabilityRecord | RevokeRecord


@dataclass(frozen=True)
class OperatorContextProjection:
    revision: int
    directives: tuple[DirectiveRecord, ...]
    preferences: tuple[PreferenceRecord, ...]
    capabilities: tuple[CapabilityRecord, ...]
    global_revision: int = 0
    preference_sources: tuple[str, ...] = ()
    #: Shown bounded instructions recorded while no mission ran.
    idle_revisions: frozenset[int] = frozenset()


@dataclass(frozen=True)
class IntakeDecision:
    kind: IntakeKind
    scope: Scope = "project"
    applies_to_roles: AppliesToRoles = "all"
    preference_kind: PreferenceKind = "workflow"
    preference_value: str = ""
    target_revision: int = 0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _roles(value: object) -> AppliesToRoles:
    if value == "all":
        return "all"
    if not isinstance(value, (list, tuple)):
        raise ValueError("applies_to_roles must be 'all' or a role list")
    roles = tuple(dict.fromkeys(str(role).strip().lower() for role in value))
    if not roles or any(role not in _ROLES for role in roles):
        raise ValueError("applies_to_roles contains an unknown role")
    return roles


def _scope(value: object) -> Scope:
    normalized = str(value or "").strip().lower()
    if normalized not in _SCOPES:
        raise ValueError("scope must be mission, project, or global")
    return cast(Scope, normalized)


def _record_from_dict(payload: dict[str, Any]) -> OperatorRecord:
    if not isinstance(payload, dict):
        raise ValueError("operator context record must be an object")
    record_type = str(payload.get("type") or "").strip().lower()
    revision = int(payload.get("revision") or 0)
    if revision <= 0:
        raise ValueError("revision must be positive")
    if record_type == "directive":
        text = str(payload.get("text") or "").strip()
        lifetime = str(payload.get("lifetime") or "").strip().lower()
        if not text:
            raise ValueError("directive text must not be empty")
        if lifetime not in _LIFETIMES:
            raise ValueError("directive lifetime is invalid")
        return DirectiveRecord(
            text=text,
            scope=_scope(payload.get("scope")),
            applies_to_roles=_roles(payload.get("applies_to_roles")),
            lifetime=cast(Lifetime, lifetime),
            source=str(payload.get("source") or "operator").strip() or "operator",
            revision=revision,
            created_at=str(payload.get("created_at") or "").strip() or "unknown time",
            mission_id=str(payload.get("mission_id") or "").strip(),
        )
    if record_type == "preference":
        kind = str(payload.get("kind") or "").strip().lower()
        value = str(payload.get("value") or "").strip()
        if kind not in _PREFERENCE_KINDS:
            raise ValueError("preference kind is invalid")
        if not value:
            raise ValueError("preference value must not be empty")
        return PreferenceRecord(
            kind=cast(PreferenceKind, kind),
            value=value,
            scope=_scope(payload.get("scope")),
            applies_to_roles=_roles(payload.get("applies_to_roles")),
            revision=revision,
        )
    if record_type == "capability":
        kind = str(payload.get("kind") or "").strip()
        route = str(payload.get("route") or payload.get("handle") or "").strip()
        secret_ref = str(payload.get("secret_ref") or "").strip()
        if not kind or not route or not secret_ref:
            raise ValueError("capability kind, route, and secret_ref are required")
        return CapabilityRecord(
            kind=kind,
            available=bool(payload.get("available")),
            route=route,
            secret_ref=secret_ref,
            scope=_scope(payload.get("scope")),
            revision=revision,
        )
    if record_type == "revoke":
        target = int(payload.get("target_revision") or 0)
        if target <= 0:
            raise ValueError("revoke target_revision must be positive")
        return RevokeRecord(
            target_revision=target,
            reason=str(payload.get("reason") or "").strip(),
            revision=revision,
        )
    raise ValueError(f"unknown operator context record type: {record_type}")


def _legacy_records(root: Path) -> list[dict[str, Any]]:
    """Read the old steering files without importing their hash/id machinery."""
    from ..manager.directive import (
        _active_steering_records,
        _read_steering_records,
        load_active_manager_directive,
    )

    rows = _active_steering_records(_read_steering_records(root))
    if rows:
        return [
            {
                "text": str(row.get("text") or "").strip(),
                "source": str(row.get("source") or "legacy.steering"),
                "created_at": str(row.get("timestamp") or "").strip() or "unknown time",
            }
            for row in rows
            if str(row.get("text") or "").strip()
            and not str(row.get("source") or "").startswith("manager.supervision")
        ]
    active = load_active_manager_directive(root)
    if active is None or active.source.startswith("manager.supervision"):
        return []
    created_at = (
        datetime.fromtimestamp(max(0.0, float(active.set_at)), timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    return [
        {
            "text": active.text,
            "source": active.source or "legacy.active_manager_directive",
            "created_at": created_at,
        }
    ]


def _adapter_records(root: Path) -> list[OperatorRecord]:
    return [
        DirectiveRecord(
            text=row["text"],
            scope="project",
            applies_to_roles="all",
            lifetime="standing",
            source=row["source"],
            revision=index,
            created_at=row["created_at"],
        )
        for index, row in enumerate(_legacy_records(root), start=1)
    ]


def _read_cache(root: Path) -> dict[str, Any]:
    try:
        with (root / PROJECTION_FILENAME).open("rb") as handle:
            raw = handle.read(MAX_SOURCE_BYTES + 1)
        if len(raw) > MAX_SOURCE_BYTES:
            return {}
        payload = json.loads(raw)
    except (OSError, TypeError, json.JSONDecodeError, UnicodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _current_mission_id(root: Path) -> str:
    from ..life.memory import LifeMemory

    active = [item for item in LifeMemory.open(root).backlog.active() if item.status == "running"]
    if active:
        return max(active, key=lambda item: float(item.started_ts or 0.0)).id
    return ""


def _applies_to(applies: AppliesToRoles | str, role: str) -> bool:
    return applies == "all" or role in applies


def _standing_directive_key(
    record: DirectiveRecord,
) -> tuple[str, Scope, tuple[str, ...], bool] | None:
    """Identify identical standing instructions without merging independent work."""
    if record.lifetime != "standing":
        return None
    roles = ("all",) if record.applies_to_roles == "all" else tuple(sorted(record.applies_to_roles))
    return (
        record.text,
        record.scope,
        roles,
        record.source.endswith(".standing_sounding"),
    )


class OperatorContextStore:
    """One physical revision namespace; projection may read explicit shared preferences."""

    def __init__(
        self,
        life_dir: Path | str,
        *,
        global_root: Path | str | None = None,
        max_records: int = 256,
        max_bytes: int = 1_000_000,
    ) -> None:
        if not 1 <= max_records <= 4096 or not 1024 <= max_bytes <= 8_000_000:
            raise ValueError("operator context capacity is outside supported bounds")
        self.root = Path(life_dir)
        self.global_root = operator_context_global_root(life_dir, global_root)
        self.ledger_path = self.root / LEDGER_FILENAME
        self.lock_path = self.root / LOCK_FILENAME
        self.ownership_path = self.root / OWNERSHIP_FILENAME
        self.max_records = max_records
        self.max_bytes = max_bytes

    @contextmanager
    def _locked(self) -> Iterator[None]:
        operator_context_global_root(self.root, self.global_root)
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as handle:
            with exclusive_file_lock(handle, lock_name="operator context lock"):
                yield

    def _load(self) -> ContextDocument:
        cache = _read_cache(self.root)
        state = {
            key: cache[key]
            for key in (
                "consumed_once",
                "acknowledged_revisions",
                "bounded_missions",
                "preference_heads",
                "revoked",
            )
            if key in cache
        }
        doc = read_document(
            self.ledger_path,
            legacy_state=state,
            normalize_record=lambda row: asdict(_record_from_dict(row)),
        )
        if doc is None:
            if (
                self.ownership_path.exists()
                or cache.get("canonical_established") is True
                or int(cache.get("base_revision") or 0) > 0
            ):
                raise ValueError(
                    "established operator context source is missing; refusing legacy replay"
                )
            rows = [asdict(record) for record in _adapter_records(self.root)]
            doc = ContextDocument(rows, int(rows[-1]["revision"]) if rows else 0, state=state)
        doc.records = [asdict(_record_from_dict(row)) for row in doc.records]
        doc.absorb_records()
        if self.ledger_path.exists() and (doc.revision > 0 or DELIVERY_STATE_KEY in doc.state):
            self._ensure_ownership()
        if not doc.checkpointed and "consumed_once" not in cache:
            uncertain = [
                row["revision"]
                for row in doc.records
                if row["type"] == "directive" and row["lifetime"] == "once"
            ]
            if uncertain:
                # Old one-shot consumption lived only in a cache. If that
                # cache has vanished, there is no evidence authorizing replay.
                doc.state["consumed_once"] = uncertain
                doc.state["legacy_consumption_uncertain"] = True
        return doc

    def _ensure_ownership(self) -> None:
        try:
            descriptor = os.open(self.ownership_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
        except FileExistsError:
            return
        try:
            os.write(descriptor, b"operator-context-v2\n")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if os.name != "nt":
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)

    def records(self) -> list[OperatorRecord]:
        with self._locked():
            return [_record_from_dict(row) for row in self._load().records]

    @property
    def revision(self) -> int:
        with self._locked():
            return self._load().revision

    def _save(
        self,
        doc: ContextDocument,
        *,
        new_rows: list[dict[str, Any]] | None = None,
        checkpoint: bool = False,
        compact: bool = False,
    ) -> None:
        doc.absorb_records()
        needs_compaction = (
            compact
            or len(doc.records) > self.max_records
            or len(checkpoint_bytes(doc)) > self.max_bytes
        )
        if needs_compaction:
            compact_document(doc)
            checkpoint = True
        if len(doc.records) > self.max_records or len(checkpoint_bytes(doc)) > self.max_bytes:
            raise OperatorContextCapacityError(
                "active operator context exceeds its budget; revoke or consolidate obsolete instructions before adding more"
            )
        if checkpoint or doc.checkpointed or doc.needs_checkpoint or new_rows is None:
            write_checkpoint(self.ledger_path, doc)
        else:
            self._append_lines([_record_from_dict(row) for row in new_rows])
        self._ensure_ownership()

    def acknowledged_revision(self, role: Role) -> int:
        if role not in _ROLES:
            raise ValueError(f"unknown operator context role: {role}")
        with self._locked():
            return int((self._load().state.get("acknowledged_revisions") or {}).get(role, 0))

    def has_unhandled_input(self, role: Role) -> bool:
        """True while some revision above what ``role`` acknowledged still carries input for it.

        The one rule for "is there new input for this role": every gate asks it.
        A one-shot answer already settled on the task it answered carries none:
        that task took it in and no role is shown it again. Counting it kept a
        delivered bounded project "安排接下来要做的事" for a whole Planner call
        after the chat said done (k1-split-cents, 2026-10-24). Nor does a record
        meant for other roles: ``role`` is never shown it.
        """
        if role not in _ROLES:
            raise ValueError(f"unknown operator context role: {role}")
        with self._locked():
            doc = self._load()
            acknowledged = int((doc.state.get("acknowledged_revisions") or {}).get(role, 0))
            if doc.revision <= acknowledged:
                return False
            consumed = {int(value) for value in doc.state.get("consumed_once", [])}
            handled = {
                int(row["revision"])
                for row in doc.records
                if not _applies_to(row.get("applies_to_roles", "all"), role)
                or (
                    row.get("type") == "directive"
                    and row.get("lifetime") == "once"
                    and int(row["revision"]) in consumed
                )
            }
            # A revision not in the records (compacted away) counts as unhandled.
            return any(revision not in handled for revision in range(acknowledged + 1, doc.revision + 1))

    def acknowledge(
        self, role: Role, revision: int, *, settle: Iterable[int] = ()
    ) -> tuple[int, ...]:
        """Checkpoint the input ``role`` handled up to ``revision``.

        ``settle`` names idle bounded instructions the Planner declared handled
        in the decision being checkpointed. Only instructions that were idle,
        Planner-visible and at or below ``revision`` settle; the settled
        revisions are returned. Anything not declared stays visible.
        """
        if role not in _ROLES:
            raise ValueError(f"unknown operator context role: {role}")
        declared = {int(value) for value in settle}
        if declared and role != "planner":
            raise ValueError("only Planner settles idle bounded instructions")
        with self._locked():
            doc = self._load()
            if not 0 <= revision <= doc.revision:
                raise ValueError("acknowledged revision is outside the operator ledger")
            acknowledged = dict(doc.state.get("acknowledged_revisions") or {})
            acknowledged[role] = max(int(acknowledged.get(role, 0)), revision)
            doc.state["acknowledged_revisions"] = acknowledged
            consumed = {int(value) for value in doc.state.get("consumed_once", [])}
            consumed.update(
                int(row["revision"])
                for row in doc.records
                if row["type"] == "directive"
                and row["lifetime"] == "once"
                and int(row["revision"]) <= revision
                and _applies_to(row["applies_to_roles"], role)
            )
            doc.state["consumed_once"] = sorted(consumed)
            if not declared:
                self._save(doc, checkpoint=True)
                return ()
            bounded = dict(doc.state.get("bounded_missions") or {})
            settled = settled_revisions(doc.state)
            pending = declared - settled
            newly_settled = tuple(
                int(row["revision"])
                for row in doc.records
                if int(row["revision"]) in pending
                and int(row["revision"]) <= revision
                and row["type"] == "directive"
                and row["lifetime"] == "bounded_increment"
                and _applies_to(row["applies_to_roles"], role)
                and bounded_mission(row.get("mission_id", ""), int(row["revision"]), bounded)
                == _NO_MISSION
            )
            if newly_settled:
                doc.state["settled_idle_bounded"] = sorted(settled | set(newly_settled))
            self._save(doc, checkpoint=True)
            return newly_settled

    def append(
        self,
        record: OperatorRecord,
        *,
        expected_revision: int,
        mission_id: str = "",
    ) -> OperatorRecord:
        if isinstance(record, DirectiveRecord) and record.source.startswith("manager.supervision"):
            raise ValueError("Manager supervision is advisory, not operator authority")
        with self._locked():
            doc = self._load()
            if int(expected_revision) != doc.revision:
                raise StaleOperatorContextWrite(
                    f"stale operator context revision: expected {expected_revision}, current {doc.revision}"
                )
            written, appended = self._stage_record(doc, record, mission_id=mission_id)
            if not appended:
                return written
            new_rows = doc.records if not self.ledger_path.exists() else [asdict(written)]
            mutable_lifetime = isinstance(written, DirectiveRecord) and written.lifetime in {
                "once",
                "bounded_increment",
            }
            self._save(doc, new_rows=new_rows, checkpoint=mutable_lifetime)
            return written

    def replace_standing_directive(
        self, source: str, text: str, *, applies_to_roles: AppliesToRoles = "all",
    ) -> int:
        """Make ``text`` the one live standing directive from ``source``; "" withdraws it.

        Earlier live directives from ``source`` are revoked and the new one is
        appended in one checkpoint, so every role re-reads it and the Planner
        sees unhandled input. Unchanged text writes nothing. Returns the
        ledger revision after the write.
        """
        text = str(text or "").strip()
        with self._locked():
            doc = self._load()
            revoked = {int(row["target_revision"]) for row in doc.records if row["type"] == "revoke"}
            revoked.update(int(value) for value in doc.state.get("revoked", []))
            live = [
                row for row in doc.records
                if row["type"] == "directive" and row.get("source") == source
                and int(row["revision"]) not in revoked
            ]
            if (not text and not live) or (
                len(live) == 1 and live[0]["text"] == text and live[0]["lifetime"] == "standing"
            ):
                return doc.revision
            for row in live:
                self._stage_record(doc, RevokeRecord(
                    target_revision=int(row["revision"]), reason=f"replaced by {source}", revision=1,
                ))
            if text:
                self._stage_record(doc, DirectiveRecord(
                    text=text, scope="project", applies_to_roles=applies_to_roles,
                    lifetime="standing", source=source, revision=1, created_at=_utc_now(),
                ))
            self._save(doc, checkpoint=True)
            return doc.revision

    def _stage_record(
        self, doc: ContextDocument, record: OperatorRecord, *, mission_id: str = "",
    ) -> tuple[OperatorRecord, bool]:
        """Preserve ordinary append semantics while allowing one atomic checkpoint."""
        if isinstance(record, DirectiveRecord) and record.source.startswith("manager.supervision"):
            raise ValueError("Manager supervision is advisory, not operator authority")
        payload = asdict(record)
        payload["revision"] = doc.revision + 1
        if isinstance(record, DirectiveRecord) and record.lifetime == "bounded_increment":
            payload["mission_id"] = (
                str(mission_id).strip() or record.mission_id
                or _current_mission_id(self.root) or _NO_MISSION
            )
        written = _record_from_dict(payload)
        previous = _record_from_dict(doc.records[-1]) if doc.records else None
        if isinstance(written, DirectiveRecord) and isinstance(previous, DirectiveRecord):
            key = _standing_directive_key(written)
            if (
                key is not None and key == _standing_directive_key(previous)
                and written.source == previous.source and previous.revision == doc.revision
            ):
                return previous, False
        if isinstance(written, PreferenceRecord) and isinstance(previous, PreferenceRecord):
            if replace(written, revision=previous.revision) == previous and previous.revision == doc.revision:
                return previous, False
        if isinstance(written, RevokeRecord) and written.target_revision >= written.revision:
            raise ValueError("revocation must target an existing earlier revision")
        doc.records.append(asdict(written))
        doc.revision = written.revision
        return written, True

    def _append_lines(self, records: list[OperatorRecord]) -> None:
        payload = b"".join(
            (json.dumps(asdict(record), ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
            for record in records
        )
        descriptor = os.open(self.ledger_path, os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
        try:
            if os.write(descriptor, payload) != len(payload):
                raise OSError("short write while appending operator context")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _project_local(
        self, role: Role, *, mission_id: str, consume_once: bool, shared_only: bool = False
    ) -> OperatorContextProjection:
        with self._locked():
            doc = self._load()
            records = [_record_from_dict(row) for row in doc.records]
            revoked = {
                record.target_revision for record in records if isinstance(record, RevokeRecord)
            }
            revoked.update(int(value) for value in doc.state.get("revoked", []))
            consumed = {int(value) for value in doc.state.get("consumed_once", [])}
            bounded = dict(doc.state.get("bounded_missions") or {})
            settled = settled_revisions(doc.state)
            heads = doc.state["preference_heads"]
            current_mission = (
                str(mission_id).strip() or _current_mission_id(self.root) or _NO_MISSION
            )
            visible: list[OperatorRecord] = []
            idle_revisions: set[int] = set()
            newly_consumed: list[int] = []
            for record in records:
                if isinstance(record, RevokeRecord) or record.revision in revoked:
                    continue
                if shared_only and (
                    not isinstance(record, PreferenceRecord) or record.scope != "global"
                ):
                    continue
                if not _applies_to(getattr(record, "applies_to_roles", "all"), role):
                    continue
                if isinstance(record, PreferenceRecord) and record.revision != int(
                    heads[preference_key(asdict(record))]
                ):
                    continue
                if isinstance(record, DirectiveRecord):
                    if record.lifetime == "once" and record.revision in consumed:
                        continue
                    if record.lifetime == "bounded_increment":
                        bound_to = bounded_mission(record.mission_id, record.revision, bounded)
                        if bound_to != current_mission:
                            continue
                        if bound_to == _NO_MISSION:
                            # Recorded while nothing ran; it lasts until the
                            # Planner declares the increment that handles it.
                            if record.revision in settled:
                                continue
                            idle_revisions.add(record.revision)
                    if record.lifetime == "once" and consume_once:
                        newly_consumed.append(record.revision)
                visible.append(record)
            if (
                newly_consumed
                or doc.needs_checkpoint
                or doc.state.get("legacy_consumption_uncertain")
            ):
                doc.state["consumed_once"] = sorted(consumed | set(newly_consumed))
                doc.state.pop("legacy_consumption_uncertain", None)
                self._save(doc, checkpoint=True)
            directives: list[DirectiveRecord] = []
            seen: set[tuple[str, Scope, tuple[str, ...], bool]] = set()
            for directive in sorted(
                (record for record in visible if isinstance(record, DirectiveRecord)),
                key=lambda record: (_SCOPE_ORDER[record.scope], record.revision),
                reverse=True,
            ):
                key = _standing_directive_key(directive)
                if key is not None and key in seen:
                    continue
                if key is not None:
                    seen.add(key)
                directives.append(directive)
            preferences: dict[str, PreferenceRecord] = {}
            capabilities: dict[str, CapabilityRecord] = {}
            for record in sorted(
                visible,
                key=lambda record: (
                    _SCOPE_ORDER[getattr(record, "scope", "project")],
                    record.revision,
                ),
                reverse=True,
            ):
                if isinstance(record, PreferenceRecord):
                    preferences.setdefault(record.kind, record)
                elif isinstance(record, CapabilityRecord):
                    capabilities.setdefault(record.kind, record)
            return OperatorContextProjection(
                doc.revision,
                tuple(directives),
                tuple(preferences.values()),
                tuple(capabilities.values()),
                # Bounded records are never deduplicated, so all are shown.
                idle_revisions=frozenset(idle_revisions),
            )

    def project(
        self,
        role: Role,
        *,
        mission_id: str = "",
        consume_once: bool = True,
    ) -> OperatorContextProjection:
        if role not in _ROLES:
            raise ValueError(f"unknown operator context role: {role}")
        shared = None
        if self.global_root is not None and self.global_root.resolve() != self.root.resolve():
            shared = OperatorContextStore(self.global_root)._project_local(
                role, mission_id="", consume_once=False, shared_only=True
            )
        local = self._project_local(role, mission_id=mission_id, consume_once=consume_once)
        preferences = list(local.preferences)
        sources = ["project"] * len(preferences)
        seen = {record.kind for record in preferences}
        if shared is not None:
            for record in shared.preferences:
                if record.scope == "global" and record.kind not in seen:
                    preferences.append(record)
                    sources.append("global")
                    seen.add(record.kind)
        return replace(
            local,
            preferences=tuple(preferences),
            global_revision=shared.revision if shared is not None else 0,
            preference_sources=tuple(sources),
        )

    def settle_once(self, revision: int) -> None:
        with self._locked():
            doc = self._load()
            target = next((row for row in doc.records if row["revision"] == revision), None)
            if target is None and 0 < revision <= doc.base_revision:
                return  # this identity is already in the closed checkpoint prefix
            if target is None or target["type"] != "directive" or target["lifetime"] != "once":
                raise ValueError(
                    f"operator context revision {revision} is not a one-shot directive"
                )
            consumed = {int(value) for value in doc.state.get("consumed_once", [])}
            if revision in consumed:
                return
            doc.state["consumed_once"] = sorted(consumed | {revision})
            self._save(doc, checkpoint=True)

    def compact(self) -> dict[str, int]:
        with self._locked():
            doc = self._load()
            before = len(doc.records)
            self._save(doc, checkpoint=True, compact=True)
            return {
                "revision": doc.revision,
                "retained_records": len(doc.records),
                "removed_records": before - len(doc.records),
            }


def append_directive(
    life_dir: Path | str,
    text: str,
    *,
    expected_revision: int,
    scope: Scope = "project",
    applies_to_roles: AppliesToRoles = "all",
    lifetime: Lifetime = "standing",
    source: str = "operator",
    mission_id: str = "",
) -> DirectiveRecord:
    record = DirectiveRecord(
        text=str(text).strip(),
        scope=scope,
        applies_to_roles=applies_to_roles,
        lifetime=lifetime,
        source=source,
        revision=1,
        created_at=_utc_now(),
    )
    return cast(
        DirectiveRecord,
        OperatorContextStore(life_dir).append(
            record, expected_revision=expected_revision, mission_id=mission_id
        ),
    )


def append_preference(
    life_dir: Path | str,
    *,
    kind: PreferenceKind,
    value: str,
    expected_revision: int,
    scope: Scope = "project",
    applies_to_roles: AppliesToRoles = "all",
    global_root: Path | str | None = None,
) -> PreferenceRecord:
    record = PreferenceRecord(
        kind=kind,
        value=str(value).strip(),
        scope=scope,
        applies_to_roles=applies_to_roles,
        revision=1,
    )
    target = preference_state_root(life_dir, scope=scope, global_root=global_root)
    return cast(
        PreferenceRecord,
        OperatorContextStore(target).append(record, expected_revision=expected_revision),
    )


def append_capability(
    life_dir: Path | str,
    *,
    kind: str,
    available: bool,
    route: str,
    secret_ref: str,
    expected_revision: int,
    scope: Scope = "project",
) -> CapabilityRecord:
    record = CapabilityRecord(
        kind=str(kind).strip(),
        available=bool(available),
        route=str(route).strip(),
        secret_ref=str(secret_ref).strip(),
        scope=scope,
        revision=1,
    )
    return cast(
        CapabilityRecord,
        OperatorContextStore(life_dir).append(record, expected_revision=expected_revision),
    )


def append_revoke(
    life_dir: Path | str,
    target_revision: int,
    *,
    reason: str,
    expected_revision: int,
    scope: Scope = "project",
    global_root: Path | str | None = None,
) -> RevokeRecord:
    record = RevokeRecord(
        target_revision=int(target_revision),
        reason=str(reason).strip(),
        revision=1,
    )
    target = preference_state_root(life_dir, scope=scope, global_root=global_root)
    return cast(
        RevokeRecord,
        OperatorContextStore(target).append(record, expected_revision=expected_revision),
    )


_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?P<name>OPENAI_API_KEY|OPENAI_BASE_URL|ARGUS_SKILL_[A-Z_]+_(?:API_KEY|BASE_URL))"
    r"\s*=\s*(?P<quote>['\"]?)(?P<value>[^\s'\"]+)(?P=quote)",
)
_OPENAI_KEY = re.compile(r"(?<![A-Za-z0-9_])sk-[A-Za-z0-9_-]{20,}")


def import_deterministic_credential(
    life_dir: Path | str,
    text: str,
    *,
    global_root: Path | str | None = None,
) -> tuple[str, CapabilityRecord | None]:
    """Import explicit model-API assignments and return text safe to persist."""
    matches = list(_CREDENTIAL_ASSIGNMENT.finditer(str(text or "")))
    all_key_matches = [
        match
        for match in matches
        if match.group("name").endswith("API_KEY")
        and not match.group("value").startswith("[stored")
    ]
    key_matches = [
        match
        for match in all_key_matches
        if len(match.group("value")) >= 16 and not match.group("value").startswith("[")
    ]
    standalone_keys = list(_OPENAI_KEY.finditer(str(text or "")))
    if not all_key_matches and not standalone_keys:
        return str(text or ""), None
    safe_text = _CREDENTIAL_ASSIGNMENT.sub(
        lambda match: (
            f"{match.group('name')}=[stored in capability vault]"
            if match.group("name").endswith("API_KEY")
            and not match.group("value").startswith("[stored")
            else match.group(0)
        ),
        str(text or ""),
    )
    safe_text = _OPENAI_KEY.sub("[stored in capability vault]", safe_text)
    if not key_matches and not standalone_keys:
        return safe_text, None
    environment = dict(os.environ)
    if global_root is not None:
        environment["ARGUS_SKILL_HOME"] = str(global_root)
    for match in matches:
        if not match.group("name").endswith("API_KEY") or match in key_matches:
            environment[match.group("name")] = match.group("value")
    if standalone_keys and not key_matches:
        environment["OPENAI_API_KEY"] = standalone_keys[0].group(0)
    from ..tools.capability_vault import bootstrap_model_api_vault

    try:
        vault = bootstrap_model_api_vault(environment)
    except RuntimeError:
        return safe_text, None
    store = OperatorContextStore(life_dir)
    record = append_capability(
        life_dir,
        kind="model_api",
        available=True,
        route="text",
        secret_ref=f"{vault.name}:text",
        scope="project",
        expected_revision=store.revision,
    )
    return safe_text, record


def freeze_operator_intake(
    life_dir: Path | str,
    text: str,
    decision: IntakeDecision,
    *,
    source: str = "operator.inbox",
    mission_id: str = "",
    global_root: Path | str | None = None,
) -> dict[str, Any]:
    """Freeze the target of one inbox message before the running round takes it.

    The inbox carries guidance, never authority, so the plan has no effect and
    nothing is written; the source still owns delivery of the transient text.
    """
    # Validate the caller's namespace even though nothing is written.
    operator_context_global_root(life_dir, global_root)
    if decision.kind != "ephemeral":
        raise ValueError("the inbox freezes only ephemeral guidance")
    return {"version": 1, "target_root": str(Path(life_dir).expanduser().resolve()), "effect": None}


def persist_intake_decision(
    life_dir: Path | str,
    text: str,
    decision: IntakeDecision,
    *,
    source: str,
    mission_id: str = "",
    global_root: Path | str | None = None,
) -> OperatorRecord | None:
    """Persist one Manager intake decision before the message is routed onward."""
    normalized = str(text or "").strip()
    if decision.kind == "ephemeral":
        return None
    target = (
        preference_state_root(life_dir, scope=decision.scope, global_root=global_root)
        if decision.kind == "preference"
        or (decision.kind == "revocation" and decision.target_revision > 0)
        else Path(life_dir)
    )
    store = OperatorContextStore(target)
    expected = store.revision
    if decision.kind == "preference":
        return append_preference(
            life_dir,
            kind=decision.preference_kind,
            value=decision.preference_value.strip() or normalized,
            scope=decision.scope,
            applies_to_roles=decision.applies_to_roles,
            expected_revision=expected,
            global_root=global_root,
        )
    if decision.kind == "revocation" and decision.target_revision > 0:
        return append_revoke(
            life_dir,
            decision.target_revision,
            reason=normalized,
            expected_revision=expected,
            scope=decision.scope,
            global_root=global_root,
        )
    if decision.kind == "credential_grant":
        return append_directive(
            life_dir,
            "Credential-like content was not imported because its format was ambiguous.",
            scope="mission",
            applies_to_roles=("manager",),
            lifetime="once",
            source=f"{source}.ambiguous_credential",
            mission_id=mission_id,
            expected_revision=expected,
        )
    lifetime: Lifetime = "standing"
    scope = decision.scope
    if decision.kind == "objective_amendment":
        lifetime = "bounded_increment"
        scope = "mission"
    elif decision.kind == "revocation":
        normalized = f"Revocation request needs a target revision: {normalized}"
        lifetime = "once"
        scope = "mission"
    elif decision.kind == "standing_directive":
        normalized = decision.preference_value.strip() or normalized
    return append_directive(
        life_dir,
        normalized,
        scope=scope,
        applies_to_roles=decision.applies_to_roles,
        lifetime=lifetime,
        source=source,
        mission_id=mission_id,
        expected_revision=expected,
    )


def persist_once_answer(
    life_dir: Path | str,
    answer: str,
    *,
    source: str = "operator.answer",
    mission_id: str = "",
) -> DirectiveRecord:
    """Durably retain an explicit answer before its interpretation turn."""
    record_source = source
    if standing_sounding(answer):
        record_source += ".standing_sounding"
    store = OperatorContextStore(life_dir)
    return append_directive(
        life_dir,
        str(answer).strip(),
        scope="mission",
        applies_to_roles="all",
        lifetime="once",
        source=record_source,
        mission_id=mission_id,
        expected_revision=store.revision,
    )


def build_operator_context_block(
    role: Role,
    life_dir: Path | str | None,
    *,
    mission_id: str = "",
    live_turn: str = "",
    consume_once: bool = True,
    global_root: Path | str | None = None,
) -> tuple[str, int]:
    text, revision, _offered = _render_operator_context_block(
        role,
        life_dir,
        mission_id=mission_id,
        live_turn=live_turn,
        consume_once=consume_once,
        global_root=global_root,
        settleable=False,
    )
    return text, revision


def build_settleable_planner_context_block(
    life_dir: Path | str | None,
    *,
    live_turn: str = "",
    global_root: Path | str | None = None,
) -> tuple[str, int, frozenset[int]]:
    """The continuous Planner's OperatorContext.

    Idle bounded instructions are tagged with their revision, so the Planner can
    declare them handled in ``settle_instructions``; the third value names the
    revisions offered, the only ones the Host may then settle.
    """
    return _render_operator_context_block(
        "planner",
        life_dir,
        live_turn=live_turn,
        consume_once=False,
        global_root=global_root,
        settleable=True,
    )


def _render_operator_context_block(
    role: Role,
    life_dir: Path | str | None,
    *,
    mission_id: str = "",
    live_turn: str = "",
    consume_once: bool,
    global_root: Path | str | None,
    settleable: bool,
) -> tuple[str, int, frozenset[int]]:
    if life_dir is None:
        return "", 0, frozenset()
    projection = OperatorContextStore(life_dir, global_root=global_root).project(
        role, mission_id=mission_id, consume_once=consume_once
    )
    offered = projection.idle_revisions if settleable else frozenset()
    lines = [
        "## OperatorContext",
        "Safety and correctness policy outrank every preference. This context may "
        "tighten behavior but never grants sandbox or authorization permission.",
        "This projection replaces earlier OperatorContext blocks for this role and "
        "mission. Omission does not establish that an underlying record was revoked. "
        "The current task and explicit user instructions remain in force.",
    ]
    if live_turn.strip():
        lines.append(f"- live turn (highest precedence): {live_turn.strip()}")
    if projection.directives:
        lines.append("## Operator steering (standing)")
    if offered:
        lines.append(
            "Entries tagged `revision N` arrived while no work ran and belong to the "
            "next increment. When your decision carries one out, deliberately rules it "
            "out, or (for a subagent report) has taken it into account, list it in "
            "`settle_instructions`. It settles only once the Host applies that decision; "
            "otherwise it stays here."
        )
    for directive in projection.directives:
        flag = (
            "; standing-sounding answer: classify its durable scope"
            if role == "manager" and directive.source.endswith(".standing_sounding")
            else ""
        )
        # The Manager sees the revision so "取消"/"cancel that" can name it.
        revision = (
            f"; revision {directive.revision}"
            if role == "manager" or directive.revision in offered
            else ""
        )
        lines.append(f"- directive [{directive.scope}{revision}{flag}]: {directive.text}")
    if role in {"planner", "engineer"}:
        from ..manager.directive import load_active_manager_directive

        active = load_active_manager_directive(life_dir)
        if active is not None and active.source.startswith("manager.supervision"):
            lines.extend(
                (
                    "## Manager direction (project advisory)",
                    "This is Manager judgment within the operator's existing constraints, "
                    "not a new operator instruction or authorization. It cannot change "
                    "acceptance, spending, credentials, or outward-action permission.",
                    active.text,
                )
            )
    allowed_preferences = {
        "manager": {"interaction"},
        "planner": {"autonomy", "workflow"},
        "engineer": {"autonomy", "workflow"},
        "reviewer": {"interaction", "workflow"},
    }[role]
    for index, preference in enumerate(projection.preferences):
        if preference.kind in allowed_preferences:
            origin = (
                projection.preference_sources[index] if projection.preference_sources else "project"
            )
            label = str(preference.scope)
            if (
                origin == "project"
                and preference.scope == "global"
                and operator_context_global_root(life_dir, global_root) is not None
            ):
                label = "project; legacy global label"
            lines.append(
                f"- {preference.kind} preference [{label}]: {preference.value} "
                f"(revision {preference.revision} in {origin} preference ledger)"
            )
    if role == "engineer":
        for capability in projection.capabilities:
            lines.append(
                f"- capability {capability.kind}: "
                f"available={'yes' if capability.available else 'no'}, "
                f"handle={capability.route}"
            )
    from .autonomy import operator_available
    from .model_visible_text import LABELLED_FIXTURE_CARVE_OUT

    if not operator_available():
        lines.append(
            "- operator availability: no operator is available during this run, "
            "so the Manager decides in their place. Do not wait for one. Where "
            "a question of meaning would normally go to the operator, the team takes "
            "the most defensible interpretation of what a requirement means and "
            "records that reading, and the one it did not take, in its handoff's "
            "assumptions, never inside a machine-graded "
            "deliverable. An interpretation may settle ambiguity between "
            "requirements; it never drops or explains away an explicit "
            "requirement or reported symptom. Never assume facts, data, or "
            "results, and never substitute placeholder credentials or mocked "
            "services in what is delivered. "
            + LABELLED_FIXTURE_CARVE_OUT
            + " Never spend money, use real credentials, or take an irreversible "
            "or outward-facing action on an assumption: ask for it, naming its "
            "operator need, and the Manager decides it."
        )
    if role == "reviewer":
        lines.append(
            "- Reviewer boundary: acceptance preferences may clarify or tighten "
            "the bar; they never reduce correctness, evidence, or independent-review "
            "standards."
        )
    lines.extend(
        (
            "",
            JUDGMENT_INSTRUCTION,
            f"shared_preference_revision={projection.global_revision}",
            f"operator_context_revision={projection.revision}",
        )
    )
    return "\n".join(lines), projection.revision, offered


def append_operator_context(prompt: str, operator_context: str) -> str:
    """Place live operator facts after the stable role prompt.

    Besides preserving the provider-cacheable prefix, the tail gives current
    steering the strongest recency position without changing its contents.
    """
    context = str(operator_context or "").strip()
    if not context or context in str(prompt):
        # A caller that already carries this exact block keeps one copy.
        return str(prompt)
    return str(prompt) + "\n\n" + context


def operator_context_revision_from_text(text: str) -> int:
    match = re.search(r"(?m)^operator_context_revision=(\d+)$", str(text or ""))
    return int(match.group(1)) if match else 0


_STANDING_HINT = re.compile(
    r"\b(always|never|from now on|going forward|do not ask|don't ask|without asking)\b",
    re.IGNORECASE,
)


def standing_sounding(text: str) -> bool:
    return bool(_STANDING_HINT.search(str(text or "")))


__all__ = [
    "CapabilityRecord",
    "DirectiveRecord",
    "JUDGMENT_INSTRUCTION",
    "IntakeDecision",
    "LEDGER_FILENAME",
    "OWNERSHIP_FILENAME",
    "OperatorContextProjection",
    "OperatorContextCapacityError",
    "OperatorContextStore",
    "OperatorContextUnavailable",
    "PreferenceRecord",
    "PROJECTION_FILENAME",
    "RevokeRecord",
    "StaleOperatorContextWrite",
    "append_capability",
    "append_directive",
    "append_operator_context",
    "append_preference",
    "append_revoke",
    "build_operator_context_block",
    "build_settleable_planner_context_block",
    "freeze_operator_intake",
    "import_deterministic_credential",
    "operator_context_revision_from_text",
    "operator_context_state_root",
    "operator_context_global_root",
    "preference_state_root",
    "persist_intake_decision",
    "persist_once_answer",
    "standing_sounding",
]
