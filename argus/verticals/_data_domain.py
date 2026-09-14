"""Project-local DATA domains — a Manager-routed vertical stored as JSON.

The on-disk Python verticals (``argus_skill.verticals.<name>.stages``) are the
factory-shipped pipelines. When the Manager meets a task that matches NO existing
vertical, it names a new domain and the runtime stores it with the canonical
candidate lifecycle under ``<project_root>/research/DOMAINS/`` rather than writing
a Python module at runtime. This module is the loader + IO + the duck-typed shim
that lets such a data domain flow through the SAME resolver path the Python
verticals use.

``DataDomain`` exposes the exact attribute surface the optional-hook accessors in
:mod:`argus_skill.verticals._base` read via ``getattr``
(``STAGE_ORDER`` / ``CHECKLIST_STAGE_ORDER`` / ``CHECKLIST_ITEMS`` /
``completion_gate`` / ``role_banner``), so ``_base`` needs no changes to consume
it. ``role_banners`` may map role names to separate prompt contracts; the legacy
``role_banner`` string remains the fallback. A fresh data domain ships an EMPTY
``CHECKLIST_ITEMS`` (the Planner authors
the per-stage checklist at runtime via :mod:`argus_skill.skills.checklist_store`)
and ``completion_gate="none"`` so it does not demand the paper submission gate.

Hybrid lifecycle: a data domain that proves out is later PROMOTED into a real
``argus_skill/verticals/<name>/`` package by
:mod:`argus_skill.manager.domain_tidy`; after promotion ``load_vertical`` resolves
the Python package first, so the data domain becomes inert (idempotent).

All reads are FAIL-OPEN: a missing / corrupt / malformed domain yields ``None``
(or an empty listing) and never raises, so resolution and prompt building can
never break on a bad data domain. ``ChecklistItem`` is late-imported to avoid a
module-load cycle (``stage_machine`` ↔ ``verticals._base`` ↔ this module).
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Project-local data-domain layout: ``<root>/research/DOMAINS/<name>.json`` plus
#: a fast ``INDEX.json`` listing every domain's metadata (minus checklist items).
DOMAINS_RELDIR = ("research", "DOMAINS")
LEARNED_DOMAINS_RELDIR = ("learned_verticals",)
INDEX_FILE = "INDEX.json"

#: A valid data-domain name: lowercase slug, no path separators, no leading dot.
_NAME_RE = re.compile(r"^[a-z0-9_]+$")

#: A fresh data domain does not demand the paper submission gate.
DEFAULT_COMPLETION_GATE = "none"

#: Runtime-owned lifecycle for every freshly authored candidate domain.
CANDIDATE_DOMAIN_STAGES = ("execute", "validate")


def is_valid_domain_name(name: object) -> bool:
    """Whether ``name`` is a structurally-valid data-domain name (slug only)."""
    return isinstance(name, str) and bool(_NAME_RE.match(name))


def _domains_dir(project_root: object) -> Path:
    return Path(str(project_root)).joinpath(*DOMAINS_RELDIR)


def _domain_path(project_root: object, name: str) -> Path:
    return _domains_dir(project_root) / f"{name}.json"


def _index_path(project_root: object) -> Path:
    return _domains_dir(project_root) / INDEX_FILE


def _learned_domains_dir(global_root: object) -> Path:
    return Path(str(global_root)).joinpath(*LEARNED_DOMAINS_RELDIR)


def _learned_domain_path(global_root: object, name: str) -> Path:
    return _learned_domains_dir(global_root) / f"{name}.json"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomic tmp+rename write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _normalize_role_banners(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(role).strip().lower(): banner.strip()
        for role, banner in value.items()
        if str(role).strip() and isinstance(banner, str) and banner.strip()
    }


class DataDomain:
    """Duck-typed, module-contract-compatible view over a project-local domain.

    Exposes the SAME attribute names the ``verticals._base`` accessors read, so a
    ``DataDomain`` instance is a drop-in for a Python ``stages`` module. It is NOT
    a ``ModuleType``; the accessors only ``getattr`` the attributes below.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        name = str(payload.get("name") or "").strip().lower()
        stages = [
            str(s).strip().lower()
            for s in (payload.get("stages") or [])
            if str(s).strip()
        ]
        order = payload.get("checklist_stage_order") or stages
        checklist_stage_order = [
            str(s).strip().lower() for s in order if str(s).strip()
        ]
        gate = str(payload.get("completion_gate") or DEFAULT_COMPLETION_GATE).strip().lower()
        # Persisted data-domain adapter. New framework code uses the generic
        # certification strength; historical paper-specific names stay here.
        if gate in {"full_emnlp", "full_paper"}:
            gate = "certified"

        self.name = name
        self.status = str(payload.get("status") or "formal").strip().lower()
        self.purpose = str(
            payload.get("purpose") or ""
        ).strip()
        self.STAGE_ORDER = stages
        self.CHECKLIST_STAGE_ORDER = tuple(checklist_stage_order)
        self.CHECKLIST_OPTIONAL_STAGES = tuple(checklist_stage_order)
        self.completion_gate = gate or DEFAULT_COMPLETION_GATE
        self._role_banner = str(payload.get("role_banner") or "")
        self.REQUIRE_INDEPENDENT_REVIEW = bool(
            payload.get("require_independent_review", True)
        )
        self.ROLE_BANNERS = _normalize_role_banners(payload.get("role_banners"))

        # Optional per-stage seed checklist (usually empty for a fresh
        # Manager-authored domain; the Planner authors items at runtime via the
        # checklist store). Late-import ChecklistItem to avoid a load cycle.
        self.CHECKLIST_ITEMS = self._build_checklist_items(payload.get("checklist"))

    @staticmethod
    def _build_checklist_items(raw: object) -> dict[str, tuple[Any, ...]]:
        if not isinstance(raw, dict):
            return {}
        from ..skills.stage_machine import ChecklistItem  # late import (cycle)

        out: dict[str, tuple[Any, ...]] = {}
        for stage, items in raw.items():
            if not isinstance(items, list):
                continue
            built: list[Any] = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                item_id = str(it.get("id") or "").strip()
                statement = str(it.get("statement") or "").strip()
                if not item_id or not statement:
                    continue
                built.append(
                    ChecklistItem(
                        id=item_id,
                        statement=statement,
                        evidence_hint=str(it.get("evidence_hint") or "").strip(),
                    )
                )
            out[str(stage).strip().lower()] = tuple(built)
        return out

    def role_banner(self, role: str) -> str:
        role_name = str(role or "").strip().lower()
        return (
            self.ROLE_BANNERS.get(role_name)
            or self.ROLE_BANNERS.get("default")
            or self._role_banner
        )


def load_data_domain(name: object, project_root: object = ".") -> "DataDomain | None":
    """Return the :class:`DataDomain` for ``name`` under ``project_root``.

    Fail-open: an invalid name, a missing/unreadable/malformed file, a non-dict
    payload, or a domain with no stages all yield ``None`` so the caller falls
    through to the next resolution source. Never raises.
    """
    if not is_valid_domain_name(name):
        return None
    try:
        payload = json.loads(
            _domain_path(project_root, str(name)).read_text(encoding="utf-8")
        )
    except Exception:  # noqa: BLE001 — missing/unreadable/malformed → None
        return None
    if not isinstance(payload, dict):
        return None
    try:
        domain = DataDomain(payload)
    except Exception:  # noqa: BLE001 — never raise during resolution
        log.debug("load_data_domain(%r): bad payload", name, exc_info=True)
        return None
    return domain if domain.STAGE_ORDER else None


def data_domain_exists(name: object, project_root: object = ".") -> bool:
    """Whether a loadable data domain ``name`` exists under ``project_root``."""
    if not is_valid_domain_name(name):
        return False
    return _domain_path(project_root, str(name)).is_file()


def _read_payload(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def list_data_domains(project_root: object = ".") -> list[str]:
    """Return the names of every project-local data domain (fail-open to [])."""
    out: list[str] = []
    try:
        entries = sorted(_domains_dir(project_root).iterdir(), key=lambda p: p.name)
    except Exception:  # noqa: BLE001 — missing dir → no domains
        return out
    for entry in entries:
        if entry.name == INDEX_FILE or not entry.name.endswith(".json"):
            continue
        stem = entry.name[: -len(".json")]
        if is_valid_domain_name(stem):
            out.append(stem)
    return out


def list_formal_data_domain_purposes(
    project_root: object = ".",
    *,
    learned_root: object | None = None,
) -> dict[str, str]:
    """Return reusable domains and concise semantic descriptions."""
    purposes: dict[str, str] = {}
    roots = [_domains_dir(project_root)]
    if learned_root is not None:
        roots.append(_learned_domains_dir(learned_root))
    for root in roots:
        try:
            entries = sorted(root.glob("*.json"), key=lambda path: path.name)
        except OSError:
            continue
        for entry in entries:
            if entry.name == INDEX_FILE:
                continue
            name = entry.stem
            if not is_valid_domain_name(name):
                continue
            payload = _read_payload(entry)
            if payload is None:
                continue
            if str(payload.get("status") or "formal").strip().lower() != "formal":
                continue
            purpose = str(
                payload.get("purpose")
                or payload.get("role_banner")
                or name
            ).strip()
            purposes.setdefault(name, purpose[:600])
    return purposes


def list_selectable_data_domain_summaries(
    project_root: object = ".",
    *,
    learned_root: object | None = None,
) -> dict[str, str]:
    """Return local candidate/formal domains plus learned formal domains.

    Candidate domains are selectable only inside their owning project and stay
    explicitly labelled ``status=candidate`` so Manager can reuse them without
    treating them as independently verified. A learned formal domain overrides
    a stale local candidate with the same name.
    """
    summaries: dict[str, tuple[str, str]] = {}
    roots = [(_domains_dir(project_root), False)]
    if learned_root is not None:
        roots.append((_learned_domains_dir(learned_root), True))
    for root, learned in roots:
        try:
            entries = sorted(root.glob("*.json"), key=lambda path: path.name)
        except OSError:
            continue
        for entry in entries:
            if entry.name == INDEX_FILE or not is_valid_domain_name(entry.stem):
                continue
            payload = _read_payload(entry)
            if payload is None:
                continue
            status = str(payload.get("status") or "formal").strip().lower()
            if status not in {"candidate", "formal"}:
                continue
            if learned and status != "formal":
                continue
            purpose = str(
                payload.get("purpose")
                or payload.get("role_banner")
                or entry.stem
            ).strip()[:600]
            current = summaries.get(entry.stem)
            if current is None or (current[0] == "candidate" and status == "formal"):
                summaries[entry.stem] = (status, purpose)
    return {
        name: f"status={status}; {purpose}"
        for name, (status, purpose) in sorted(summaries.items())
    }


def data_domain_summaries(project_root: object = ".") -> dict[str, str]:
    """Compatibility summaries for formal project-local domains."""
    return {
        name: f"status=formal; {purpose}"
        for name, purpose in list_formal_data_domain_purposes(project_root).items()
    }


def list_all_data_domain_names(
    project_root: object = ".",
    *,
    learned_root: object | None = None,
) -> list[str]:
    names = set(list_data_domains(project_root))
    if learned_root is not None:
        try:
            names.update(
                entry.stem
                for entry in _learned_domains_dir(learned_root).glob("*.json")
                if is_valid_domain_name(entry.stem)
            )
        except OSError:
            pass
    return sorted(names)


def materialize_learned_data_domain(
    learned_root: object,
    project_root: object,
    name: str,
) -> bool:
    """Copy one formal learned vertical into the active session."""
    local_path = _domain_path(project_root, name)
    local = _read_payload(local_path) if local_path.is_file() else None
    if (
        local is not None
        and str(local.get("status") or "formal").strip().lower() == "formal"
    ):
        return False
    payload = _read_payload(_learned_domain_path(learned_root, name))
    if payload is None:
        return False
    if str(payload.get("status") or "").strip().lower() != "formal":
        return False
    _atomic_write_json(local_path, payload)
    _update_index(
        project_root,
        name,
        {
            "status": "formal",
            "purpose": str(payload.get("purpose") or ""),
            "stages": list(payload.get("stages") or []),
        },
    )
    return True


def migrate_data_domains(source_root: object, target_root: object) -> None:
    """Copy valid legacy domains into an empty session state root."""
    for name in list_data_domains(source_root):
        source = _domain_path(source_root, name)
        target = _domain_path(target_root, name)
        payload = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or DataDomain(payload).STAGE_ORDER == ():
            raise ValueError(f"invalid legacy data domain: {name}")
        if target.exists():
            existing = json.loads(target.read_text(encoding="utf-8"))
            if existing != payload:
                raise ValueError(f"conflicting migrated data domain: {name}")
            continue
        _atomic_write_json(target, payload)


def write_data_domain(
    project_root: object,
    name: str,
    *,
    stages: list[str],
    checklist_stage_order: list[str] | None = None,
    completion_gate: str = DEFAULT_COMPLETION_GATE,
    role_banner: str | dict[str, str] = "",
    created_by: str = "manager",
    status: str = "formal",
    purpose: str = "",
    require_independent_review: bool = True,
    overwrite: bool = False,
) -> Path:
    """Persist a new data domain (create-only by default) and update the INDEX.

    Raises ``ValueError`` on an invalid name / empty stages / a name collision
    when ``overwrite`` is False. Manager domain authoring is create-only; the
    janitor promotion path removes the data file once promoted to source.
    """
    if not is_valid_domain_name(name):
        raise ValueError(f"invalid data-domain name: {name!r}")
    norm_stages = [str(s).strip().lower() for s in (stages or []) if str(s).strip()]
    if not norm_stages:
        raise ValueError("a data domain needs at least one stage")
    path = _domain_path(project_root, name)
    if path.exists() and not overwrite:
        raise ValueError(f"data domain {name!r} already exists")

    order = [
        str(s).strip().lower()
        for s in (checklist_stage_order or norm_stages)
        if str(s).strip()
    ]
    from datetime import datetime, timezone

    created_at = datetime.now(timezone.utc).isoformat()
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in {"candidate", "formal"}:
        raise ValueError("data domain status must be candidate or formal")
    role_banners = _normalize_role_banners(role_banner)
    payload = {
        "name": name,
        "stages": norm_stages,
        "checklist_stage_order": order,
        "completion_gate": (completion_gate or DEFAULT_COMPLETION_GATE).strip().lower(),
        "role_banner": role_banner if isinstance(role_banner, str) else "",
        "created_by": created_by or "manager",
        "created_at": created_at,
        "status": normalized_status,
        "purpose": str(purpose or "").strip()[:600],
        "require_independent_review": bool(require_independent_review),
        "promoted": False,
    }
    if role_banners:
        payload["role_banners"] = role_banners
    _atomic_write_json(path, payload)
    _update_index(project_root, name, {k: payload[k] for k in (
        "stages", "checklist_stage_order", "completion_gate", "created_by",
        "created_at", "status", "purpose", "promoted",
    )})
    return path


def revise_data_domain_stages(
    project_root: object,
    name: str,
    *,
    stages: list[str] | tuple[str, ...],
    reason: str = "",
) -> Path:
    """Refine one existing project domain without creating a competing domain."""
    path = _domain_path(project_root, name)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"data domain {name!r} is not an object")
    current = DataDomain(payload)
    if not current.STAGE_ORDER:
        raise ValueError(f"data domain {name!r} has no valid stages")
    revised = list(dict.fromkeys(
        str(stage or "").strip().lower()
        for stage in stages
        if str(stage or "").strip()
    ))
    if len(revised) < 2 or any(
        not is_valid_domain_name(stage) for stage in revised
    ):
        raise ValueError("a revised data domain needs at least 2 valid stage slugs")
    if revised == list(current.STAGE_ORDER):
        return path

    from datetime import datetime, timezone

    payload["adapted_from_stages"] = list(current.STAGE_ORDER)
    payload["stages"] = revised
    payload["checklist_stage_order"] = revised
    payload["adapted_at"] = datetime.now(timezone.utc).isoformat()
    payload["adapted_by"] = "manager"
    payload["adaptation_reason"] = str(reason or "").strip()[:600]
    checklist = payload.get("checklist")
    if isinstance(checklist, dict):
        payload["checklist"] = {
            stage: items
            for stage, items in checklist.items()
            if stage in revised
        }
    _atomic_write_json(path, payload)
    _update_index(
        project_root,
        name,
        {
            key: payload[key]
            for key in (
                "stages",
                "checklist_stage_order",
                "completion_gate",
                "created_by",
                "created_at",
                "status",
                "purpose",
                "promoted",
                "adapted_at",
                "adaptation_reason",
            )
            if key in payload
        },
    )
    return path


def promote_data_domain(
    project_root: object,
    learned_root: object,
    name: str,
    *,
    review_reason: str = "",
) -> bool:
    """Promote one verified candidate and publish it for later sessions."""
    local_path = _domain_path(project_root, name)
    payload = _read_payload(local_path)
    if payload is None:
        return False
    if str(payload.get("status") or "formal").strip().lower() != "candidate":
        return False
    from datetime import datetime, timezone

    payload["status"] = "formal"
    payload["verified_at"] = datetime.now(timezone.utc).isoformat()
    payload["review_reason"] = str(review_reason or "").strip()[:1000]
    _atomic_write_json(_learned_domain_path(learned_root, name), payload)
    _atomic_write_json(local_path, payload)
    _update_index(
        project_root,
        name,
        {
            "status": "formal",
            "purpose": str(payload.get("purpose") or ""),
            "stages": list(payload.get("stages") or []),
            "verified_at": payload["verified_at"],
        },
    )
    return True


def record_data_domain_failure(
    project_root: object,
    name: str,
    *,
    reason: str,
) -> bool:
    """Keep one failed candidate and the reason needed to revise it."""
    path = _domain_path(project_root, name)
    payload = _read_payload(path)
    if payload is None:
        return False
    if str(payload.get("status") or "formal").strip().lower() != "candidate":
        return False
    payload["last_failure"] = str(reason or "").strip()[:1000]
    _atomic_write_json(path, payload)
    return True


def _update_index(project_root: object, name: str, meta: dict[str, Any]) -> None:
    """Best-effort update of ``DOMAINS/INDEX.json`` (never raises)."""
    try:
        idx_path = _index_path(project_root)
        try:
            index = json.loads(idx_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — missing/corrupt → fresh
            index = {}
        if not isinstance(index, dict):
            index = {}
        index[name] = meta
        _atomic_write_json(idx_path, index)
    except Exception:  # noqa: BLE001 — index is an optimization, not a source of truth
        log.debug("failed to update data-domain index for %r", name, exc_info=True)


def mark_promoted(project_root: object, name: str) -> None:
    """Flag a data domain as promoted-to-source (best-effort)."""
    try:
        path = _domain_path(project_root, name)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload["promoted"] = True
            _atomic_write_json(path, payload)
            _update_index(project_root, name, {"promoted": True})
    except Exception:  # noqa: BLE001 — best-effort
        log.debug("failed to mark data domain %r promoted", name, exc_info=True)


__all__ = [
    "CANDIDATE_DOMAIN_STAGES",
    "DataDomain",
    "DEFAULT_COMPLETION_GATE",
    "LEARNED_DOMAINS_RELDIR",
    "is_valid_domain_name",
    "load_data_domain",
    "data_domain_exists",
    "data_domain_summaries",
    "list_data_domains",
    "list_all_data_domain_names",
    "list_formal_data_domain_purposes",
    "list_selectable_data_domain_summaries",
    "materialize_learned_data_domain",
    "migrate_data_domains",
    "promote_data_domain",
    "record_data_domain_failure",
    "revise_data_domain_stages",
    "write_data_domain",
    "mark_promoted",
]
