"""Typed, append-only operator context for one project life directory."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, TypeAlias, cast

from .file_lock import exclusive_file_lock

LEDGER_FILENAME = "operator_context.jsonl"
PROJECTION_FILENAME = "operator_context.json"
LOCK_FILENAME = "operator_context.lock"

Scope = Literal["mission", "project", "global"]
Lifetime = Literal["standing", "bounded_increment", "once"]
Role = Literal["manager", "planner", "engineer", "reviewer", "teammate"]
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
_ROLES = frozenset({"manager", "planner", "engineer", "reviewer", "teammate"})
_PREFERENCE_KINDS = frozenset({"autonomy", "interaction", "workflow"})
_SCOPE_ORDER = {"global": 0, "project": 1, "mission": 2}
_NO_MISSION = "__no_mission__"

JUDGMENT_INSTRUCTION = (
    "Before asking the operator, judge the objective together with OperatorContext. "
    "Ask only when no authorized role can decide: unavailable credentials, new "
    "spending, irreversible/outward action, or changing an operator-owned acceptance "
    "boundary. Choose and disclose reversible technical, project-local, tooling, "
    "layout, and routing decisions yourself. A no-questions preference does not "
    "invent missing authority."
)


class StaleOperatorContextWrite(RuntimeError):
    """The ledger changed after a writer read its revision."""


@dataclass(frozen=True)
class DirectiveRecord:
    text: str
    scope: Scope
    applies_to_roles: AppliesToRoles
    lifetime: Lifetime
    source: str
    revision: int
    created_at: str
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


OperatorRecord: TypeAlias = (
    DirectiveRecord | PreferenceRecord | CapabilityRecord | RevokeRecord
)


@dataclass(frozen=True)
class OperatorContextProjection:
    revision: int
    directives: tuple[DirectiveRecord, ...]
    preferences: tuple[PreferenceRecord, ...]
    capabilities: tuple[CapabilityRecord, ...]


@dataclass(frozen=True)
class IntakeDecision:
    kind: IntakeKind
    scope: Scope = "project"
    applies_to_roles: AppliesToRoles = "all"
    preference_kind: PreferenceKind = "workflow"
    preference_value: str = ""
    target_revision: int = 0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


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
            created_at=str(payload.get("created_at") or "").strip() or _utc_now(),
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


def _read_native(path: Path) -> list[OperatorRecord]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    records: list[OperatorRecord] = []
    expected = 1
    for line_number, line in enumerate(lines, start=1):
        try:
            payload = json.loads(line)
            record = _record_from_dict(payload)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"invalid operator context row {path}:{line_number}: {exc}"
            ) from exc
        if record.revision != expected:
            raise ValueError(
                f"operator context revision gap at {path}:{line_number}: "
                f"expected {expected}, got {record.revision}"
            )
        records.append(record)
        expected += 1
    return records


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
                "created_at": str(row.get("timestamp") or "").strip() or _utc_now(),
            }
            for row in rows
            if str(row.get("text") or "").strip()
        ]
    active = load_active_manager_directive(root)
    if active is None:
        return []
    return [{
        "text": active.text,
        "source": active.source or "legacy.active_manager_directive",
        "created_at": _utc_now(),
    }]


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
        payload = json.loads((root / PROJECTION_FILENAME).read_text(encoding="utf-8"))
    except (OSError, TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_cache(root: Path, payload: dict[str, Any]) -> None:
    path = root / PROJECTION_FILENAME
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _current_mission_id(root: Path) -> str:
    from ..life.memory import LifeMemory

    active = [
        item
        for item in LifeMemory.open(root).backlog.active()
        if item.status == "running"
    ]
    if active:
        return max(active, key=lambda item: float(item.started_ts or 0.0)).id
    return ""


class OperatorContextStore:
    def __init__(self, life_dir: Path | str) -> None:
        self.root = Path(life_dir)
        self.ledger_path = self.root / LEDGER_FILENAME
        self.lock_path = self.root / LOCK_FILENAME

    def records(self) -> list[OperatorRecord]:
        native = _read_native(self.ledger_path)
        return native if self.ledger_path.exists() else _adapter_records(self.root)

    @property
    def revision(self) -> int:
        records = self.records()
        return records[-1].revision if records else 0

    def append(
        self,
        record: DirectiveRecord | PreferenceRecord | CapabilityRecord | RevokeRecord,
        *,
        expected_revision: int,
        mission_id: str = "",
    ) -> OperatorRecord:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as lock:
            with exclusive_file_lock(lock, lock_name="operator context lock"):
                records = _read_native(self.ledger_path)
                if not self.ledger_path.exists():
                    records = _adapter_records(self.root)
                    if records:
                        self._append_lines(records)
                current = records[-1].revision if records else 0
                if int(expected_revision) != current:
                    raise StaleOperatorContextWrite(
                        f"stale operator context revision: expected "
                        f"{expected_revision}, current {current}"
                    )
                payload = asdict(record)
                payload["revision"] = current + 1
                written = _record_from_dict(payload)
                self._append_lines([written])
                records.append(written)
                cache = _read_cache(self.root)
                bounded = dict(cache.get("bounded_missions") or {})
                if isinstance(written, DirectiveRecord) and written.lifetime == "bounded_increment":
                    bounded[str(written.revision)] = (
                        str(mission_id).strip() or _current_mission_id(self.root)
                    ) or _NO_MISSION
                self._refresh_cache(records, cache=cache, bounded_missions=bounded)
                return written

    def _append_lines(self, records: list[OperatorRecord]) -> None:
        payload = b"".join(
            (json.dumps(asdict(record), ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
            for record in records
        )
        descriptor = os.open(
            self.ledger_path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        try:
            written = os.write(descriptor, payload)
            if written != len(payload):
                raise OSError("short write while appending operator context")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _refresh_cache(
        self,
        records: list[OperatorRecord],
        *,
        cache: dict[str, Any],
        bounded_missions: dict[str, str] | None = None,
    ) -> None:
        revoked = {
            record.target_revision
            for record in records
            if isinstance(record, RevokeRecord)
        }
        active = [
            asdict(record)
            for record in records
            if not isinstance(record, RevokeRecord) and record.revision not in revoked
        ]
        _write_cache(self.root, {
            "version": 1,
            "revision": records[-1].revision if records else 0,
            "records": active,
            "consumed_once": list(cache.get("consumed_once") or []),
            "bounded_missions": bounded_missions
            if bounded_missions is not None
            else dict(cache.get("bounded_missions") or {}),
        })

    def project(
        self,
        role: Role,
        *,
        mission_id: str = "",
        consume_once: bool = True,
    ) -> OperatorContextProjection:
        if role not in _ROLES:
            raise ValueError(f"unknown operator context role: {role}")
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as lock:
            with exclusive_file_lock(lock, lock_name="operator context lock"):
                records = self.records()
                cache = _read_cache(self.root)
                revoked = {
                    record.target_revision
                    for record in records
                    if isinstance(record, RevokeRecord)
                }
                consumed = {int(value) for value in cache.get("consumed_once") or []}
                bounded = {
                    str(key): str(value)
                    for key, value in dict(cache.get("bounded_missions") or {}).items()
                }
                current_mission = str(mission_id).strip() or _current_mission_id(self.root)
                current_mission = current_mission or _NO_MISSION
                visible: list[OperatorRecord] = []
                newly_consumed: list[int] = []
                for record in records:
                    if isinstance(record, RevokeRecord) or record.revision in revoked:
                        continue
                    applies = getattr(record, "applies_to_roles", "all")
                    if applies != "all" and role not in applies:
                        continue
                    if isinstance(record, DirectiveRecord):
                        if record.lifetime == "once" and record.revision in consumed:
                            continue
                        if record.lifetime == "bounded_increment":
                            bound_to = bounded.get(str(record.revision), "")
                            if bound_to and bound_to != current_mission:
                                continue
                        if record.lifetime == "once" and consume_once:
                            newly_consumed.append(record.revision)
                    visible.append(record)
                if newly_consumed:
                    cache["consumed_once"] = sorted(consumed | set(newly_consumed))
                if newly_consumed or int(cache.get("revision") or -1) != (
                    records[-1].revision if records else 0
                ):
                    self._refresh_cache(records, cache=cache, bounded_missions=bounded)
                directives = sorted(
                    (record for record in visible if isinstance(record, DirectiveRecord)),
                    key=lambda record: (_SCOPE_ORDER[record.scope], record.revision),
                    reverse=True,
                )
                preferences_by_kind: dict[str, PreferenceRecord] = {}
                for record in sorted(
                    (record for record in visible if isinstance(record, PreferenceRecord)),
                    key=lambda record: (_SCOPE_ORDER[record.scope], record.revision),
                    reverse=True,
                ):
                    preferences_by_kind.setdefault(record.kind, record)
                capabilities_by_kind: dict[str, CapabilityRecord] = {}
                for record in sorted(
                    (record for record in visible if isinstance(record, CapabilityRecord)),
                    key=lambda record: (_SCOPE_ORDER[record.scope], record.revision),
                    reverse=True,
                ):
                    capabilities_by_kind.setdefault(record.kind, record)
                return OperatorContextProjection(
                    revision=records[-1].revision if records else 0,
                    directives=tuple(directives),
                    preferences=tuple(preferences_by_kind.values()),
                    capabilities=tuple(capabilities_by_kind.values()),
                )

    def settle_once(self, revision: int) -> None:
        """Consume one exact one-shot directive after its work is committed."""
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as lock:
            with exclusive_file_lock(lock, lock_name="operator context lock"):
                records = self.records()
                target = next(
                    (record for record in records if record.revision == revision),
                    None,
                )
                if not (
                    isinstance(target, DirectiveRecord)
                    and target.lifetime == "once"
                ):
                    raise ValueError(
                        f"operator context revision {revision} is not a one-shot directive"
                    )
                cache = _read_cache(self.root)
                consumed = {int(value) for value in cache.get("consumed_once") or []}
                if revision in consumed:
                    return
                cache["consumed_once"] = sorted(consumed | {revision})
                self._refresh_cache(records, cache=cache)


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
    return cast(DirectiveRecord, OperatorContextStore(life_dir).append(
        record, expected_revision=expected_revision, mission_id=mission_id
    ))


def append_preference(
    life_dir: Path | str,
    *,
    kind: PreferenceKind,
    value: str,
    expected_revision: int,
    scope: Scope = "project",
    applies_to_roles: AppliesToRoles = "all",
) -> PreferenceRecord:
    record = PreferenceRecord(
        kind=kind,
        value=str(value).strip(),
        scope=scope,
        applies_to_roles=applies_to_roles,
        revision=1,
    )
    return cast(PreferenceRecord, OperatorContextStore(life_dir).append(
        record, expected_revision=expected_revision
    ))


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
    return cast(CapabilityRecord, OperatorContextStore(life_dir).append(
        record, expected_revision=expected_revision
    ))


def append_revoke(
    life_dir: Path | str,
    target_revision: int,
    *,
    reason: str,
    expected_revision: int,
) -> RevokeRecord:
    record = RevokeRecord(
        target_revision=int(target_revision),
        reason=str(reason).strip(),
        revision=1,
    )
    return cast(RevokeRecord, OperatorContextStore(life_dir).append(
        record, expected_revision=expected_revision
    ))


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
        if len(match.group("value")) >= 16
        and not match.group("value").startswith("[")
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


def persist_intake_decision(
    life_dir: Path | str,
    text: str,
    decision: IntakeDecision,
    *,
    source: str,
    mission_id: str = "",
) -> OperatorRecord | None:
    """Persist one Manager intake decision before the message is routed onward."""
    normalized = str(text or "").strip()
    if decision.kind == "ephemeral":
        return None
    store = OperatorContextStore(life_dir)
    expected = store.revision
    if decision.kind == "preference":
        return append_preference(
            life_dir,
            kind=decision.preference_kind,
            value=decision.preference_value.strip() or normalized,
            scope=decision.scope,
            applies_to_roles=decision.applies_to_roles,
            expected_revision=expected,
        )
    if decision.kind == "revocation" and decision.target_revision > 0:
        return append_revoke(
            life_dir,
            decision.target_revision,
            reason=normalized,
            expected_revision=expected,
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
) -> tuple[str, int]:
    if life_dir is None:
        return "", 0
    projection = OperatorContextStore(life_dir).project(
        role, mission_id=mission_id, consume_once=consume_once
    )
    lines = [
        "## OperatorContext",
        f"operator_context_revision={projection.revision}",
        "Safety and correctness policy outrank every preference. This context may "
        "tighten behavior but never grants sandbox or authorization permission.",
    ]
    if live_turn.strip():
        lines.append(f"- live turn (highest precedence): {live_turn.strip()}")
    if projection.directives:
        lines.append("## Operator steering (standing)")
    for directive in projection.directives:
        flag = (
            "; standing-sounding answer: classify its durable scope"
            if role == "manager" and directive.source.endswith(".standing_sounding")
            else ""
        )
        lines.append(f"- directive [{directive.scope}{flag}]: {directive.text}")
    allowed_preferences = {
        "manager": {"interaction"},
        "planner": {"autonomy", "workflow"},
        "engineer": {"autonomy", "workflow"},
        "teammate": {"autonomy", "workflow"},
        "reviewer": {"interaction", "workflow"},
    }[role]
    for preference in projection.preferences:
        if preference.kind in allowed_preferences:
            lines.append(
                f"- {preference.kind} preference [{preference.scope}]: {preference.value}"
            )
    if role in {"engineer", "teammate"}:
        for capability in projection.capabilities:
            lines.append(
                f"- capability {capability.kind}: "
                f"available={'yes' if capability.available else 'no'}, "
                f"handle={capability.route}"
            )
    if role == "reviewer":
        lines.append(
            "- Reviewer boundary: acceptance preferences may clarify or tighten "
            "the bar; they never reduce correctness, evidence, or independent-review "
            "standards."
        )
    lines.extend(("", JUDGMENT_INSTRUCTION))
    return "\n".join(lines), projection.revision


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
    "OperatorContextProjection",
    "OperatorContextStore",
    "PreferenceRecord",
    "PROJECTION_FILENAME",
    "RevokeRecord",
    "StaleOperatorContextWrite",
    "append_capability",
    "append_directive",
    "append_preference",
    "append_revoke",
    "build_operator_context_block",
    "import_deterministic_credential",
    "operator_context_revision_from_text",
    "persist_intake_decision",
    "persist_once_answer",
    "standing_sounding",
]
