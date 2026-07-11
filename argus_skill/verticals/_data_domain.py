"""Project-local DATA domains — a Manager-authored vertical stored as JSON.

The on-disk Python verticals (``argus_skill.verticals.<name>.stages``) are the
factory-shipped pipelines. When the Manager meets a task that matches NO existing
vertical, it AUTHORS a new domain (a name + an ordered Stage list) and stores it
as project-local DATA first — under ``<project_root>/research/DOMAINS/`` — rather
than writing a Python module at runtime. This module is the loader + IO + the
duck-typed shim that lets such a data domain flow through the SAME resolver path
the Python verticals use.

``DataDomain`` exposes the exact attribute surface the optional-hook accessors in
:mod:`argus_skill.verticals._base` read via ``getattr``
(``STAGE_ORDER`` / ``CHECKLIST_STAGE_ORDER`` / ``CHECKLIST_ITEMS`` /
``completion_gate`` / ``role_banner`` / ``STAGE_CHECKS`` / ``REVIEWER_CHECKLISTS``),
so ``_base`` needs no changes to consume it. A fresh data domain ships an EMPTY
``CHECKLIST_ITEMS`` (the Planner authors the per-stage checklist at runtime via
:mod:`argus_skill.skills.checklist_store`) and ``completion_gate="none"`` so it
does not demand the paper submission gate.

Hybrid lifecycle: a data domain that proves out is later PROMOTED into a real
``argus_skill/verticals/<name>/`` package by
:mod:`argus_skill.manager.domain_tidy`; after promotion ``load_vertical`` resolves
the Python package first, so the data domain becomes inert (idempotent).

All reads are FAIL-OPEN: a missing / corrupt / malformed domain yields ``None``
(or an empty listing) and never raises, so resolution and prompt building can
never break on a bad data domain. ``ChecklistItem`` is late-imported to avoid a
module-load cycle (``stage_checklists`` ↔ ``verticals._base`` ↔ this module).
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
INDEX_FILE = "INDEX.json"

#: A valid data-domain name: lowercase slug, no path separators, no leading dot.
_NAME_RE = re.compile(r"^[a-z0-9_]+$")

#: Shared minimal stage check (mirrors the Python verticals' ``_PIPELINE_CHECK``):
#: every stage at least needs the pipeline-state file present.
_PIPELINE_CHECK = ("Pipeline state present", "test -f research/PIPELINE_STATE.json")

#: A fresh data domain does not demand the paper submission gate.
DEFAULT_COMPLETION_GATE = "none"


def is_valid_domain_name(name: object) -> bool:
    """Whether ``name`` is a structurally-valid data-domain name (slug only)."""
    return isinstance(name, str) and bool(_NAME_RE.match(name))


def _domains_dir(project_root: object) -> Path:
    return Path(str(project_root)).joinpath(*DOMAINS_RELDIR)


def _domain_path(project_root: object, name: str) -> Path:
    return _domains_dir(project_root) / f"{name}.json"


def _index_path(project_root: object) -> Path:
    return _domains_dir(project_root) / INDEX_FILE


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomic tmp+rename write (mirrors ``harness_overlay._atomic_write_json``)."""
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
        # Back-compat: the paper/report completion gate was historically keyed
        # "full_emnlp"; accept that legacy value from persisted data-domain
        # payloads and normalize to the venue-neutral "full_paper".
        if gate == "full_emnlp":
            gate = "full_paper"

        self.name = name
        self.STAGE_ORDER = stages
        self.CHECKLIST_STAGE_ORDER = tuple(checklist_stage_order)
        self.completion_gate = gate or DEFAULT_COMPLETION_GATE
        self._role_banner = str(payload.get("role_banner") or "")

        # Minimal System-A shell checks per stage (the markdown checklist + the
        # reviewer are the real gate; these only confirm artifacts physically
        # exist). Kept for any future per-vertical dispatch / promotion template.
        self.STAGE_CHECKS = {stage: [_PIPELINE_CHECK] for stage in stages}
        self.REVIEWER_CHECKLISTS: dict[str, list[str]] = {}

        # Optional per-stage seed checklist (usually empty for a fresh
        # Manager-authored domain; the Planner authors items at runtime via the
        # checklist store). Late-import ChecklistItem to avoid a load cycle.
        self.CHECKLIST_ITEMS = self._build_checklist_items(payload.get("checklist"))

    @staticmethod
    def _build_checklist_items(raw: object) -> dict[str, tuple[Any, ...]]:
        if not isinstance(raw, dict):
            return {}
        from ..skills.stage_checklists import ChecklistItem  # late import (cycle)

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

    def role_banner(self, role: str) -> str:  # noqa: ARG002 - role-agnostic banner
        return self._role_banner


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


def write_data_domain(
    project_root: object,
    name: str,
    *,
    stages: list[str],
    checklist_stage_order: list[str] | None = None,
    completion_gate: str = DEFAULT_COMPLETION_GATE,
    role_banner: str = "",
    created_by: str = "manager",
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
    payload = {
        "name": name,
        "stages": norm_stages,
        "checklist_stage_order": order,
        "completion_gate": (completion_gate or DEFAULT_COMPLETION_GATE).strip().lower(),
        "role_banner": role_banner or "",
        "created_by": created_by or "manager",
        "created_at": created_at,
        "promoted": False,
    }
    _atomic_write_json(path, payload)
    _update_index(project_root, name, {k: payload[k] for k in (
        "stages", "checklist_stage_order", "completion_gate", "created_by",
        "created_at", "promoted",
    )})
    return path


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
    "DataDomain",
    "DEFAULT_COMPLETION_GATE",
    "is_valid_domain_name",
    "load_data_domain",
    "data_domain_exists",
    "list_data_domains",
    "write_data_domain",
    "mark_promoted",
]
