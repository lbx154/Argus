"""Capability consumption trace — makes capability usage measurable.

V4 showed the capability library was *exposed* to the gates but never *consumed*
in a traceable way (see V4_CAPABILITY_USAGE_AUDIT). This module records, per gate,
which capabilities were available / exposed / applicable / selected / used, which
evidence files backed them, and what effect they had (failures, repairs, claim
changes, paper-type effect). Each gate calls :func:`record_gate_consumption` on run;
the merged result lives in ``research/CAPABILITY_CONSUMPTION_TRACE.json``.

Discipline-agnostic and dependency-free: it only stores ids + counts + strings.
"""
from __future__ import annotations

import json
from pathlib import Path

TRACE_REL = "research/CAPABILITY_CONSUMPTION_TRACE.json"

#: The per-gate record fields (kept explicit so the schema is stable/testable).
RECORD_FIELDS: tuple[str, ...] = (
    "gate",
    "available_count",
    "exposed_capability_ids",
    "applicable_capability_ids",
    "selected_capability_ids",
    "used_capability_ids",
    "evidence_files",
    "failure_ids_caused_by_missing_capability",
    "repair_actions_triggered",
    "claim_changes_caused",
    "paper_type_effect",
)


def _path(project_root: object) -> Path:
    return Path(str(project_root or ".")) / TRACE_REL


def read_trace(project_root: object) -> dict:
    try:
        data = json.loads(_path(project_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _norm_list(v: object) -> list:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [x for x in v]
    return [v]


def record_gate_consumption(project_root: object, gate: str, **fields: object) -> dict:
    """Merge one gate's consumption record into CAPABILITY_CONSUMPTION_TRACE.json.

    Unknown kwargs are ignored; list-valued fields are normalised to lists; the
    record is keyed by ``gate`` so re-runs overwrite that gate's entry only.
    """
    root = Path(str(project_root or "."))
    trace = read_trace(root)
    if "gates" not in trace or not isinstance(trace.get("gates"), dict):
        trace["gates"] = {}

    rec: dict = {"gate": gate}
    list_fields = {
        "exposed_capability_ids", "applicable_capability_ids", "selected_capability_ids",
        "used_capability_ids", "evidence_files", "failure_ids_caused_by_missing_capability",
        "repair_actions_triggered", "claim_changes_caused",
    }
    for f in RECORD_FIELDS:
        if f == "gate":
            continue
        val = fields.get(f)
        if f in list_fields:
            rec[f] = _norm_list(val)
        elif f == "available_count":
            rec[f] = int(val) if isinstance(val, (int, float)) else 0
        else:  # paper_type_effect (string)
            rec[f] = str(val) if val is not None else ""
    trace["gates"][gate] = rec

    # roll up a small summary for quick reading
    gates = trace["gates"]
    trace["summary"] = {
        "gates_recorded": sorted(gates.keys()),
        "total_available": sum(int(g.get("available_count", 0) or 0) for g in gates.values()),
        "total_exposed": sum(len(g.get("exposed_capability_ids", [])) for g in gates.values()),
        "total_used": sum(len(g.get("used_capability_ids", [])) for g in gates.values()),
    }

    p = _path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(trace, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(p)
    return rec


def trace_gate_run(
    project_root: object,
    gate_id: str,
    failures: list,
    *,
    used_capability_ids: object = None,
    evidence_files: object = None,
    claim_changes_caused: object = None,
    paper_type_effect: str = "",
) -> dict:
    """Convenience wrapper: record a gate run's capability consumption.

    Pulls the gate's exposed capabilities from the CapabilityRegistry, derives the
    standard fields from ``failures`` (a list of GateFailure dicts), and merges the
    record. Fail-open: any error is swallowed so tracing never breaks a gate.
    """
    try:
        from .capability_registry import CapabilityRegistry

        root = Path(str(project_root or "."))
        caps = CapabilityRegistry(project_root=root).for_gate(gate_id)
        exposed = [c.capability_id for c in caps]
        fail_ids = [f.get("failure_id") for f in (failures or []) if isinstance(f, dict) and f.get("failure_id")]
        repair_actions = [f.get("required_action") for f in (failures or [])
                          if isinstance(f, dict) and f.get("required_action")]
        return record_gate_consumption(
            root, gate_id,
            available_count=len(caps),
            exposed_capability_ids=exposed,
            applicable_capability_ids=exposed,  # all exposed are candidate-applicable pending per-cap execution
            selected_capability_ids=exposed,
            used_capability_ids=used_capability_ids if used_capability_ids is not None else [],
            evidence_files=evidence_files,
            failure_ids_caused_by_missing_capability=fail_ids,
            repair_actions_triggered=repair_actions,
            claim_changes_caused=claim_changes_caused,
            paper_type_effect=paper_type_effect,
        )
    except Exception:  # noqa: BLE001 — tracing must never break a gate
        return {}


__all__ = ["TRACE_REL", "RECORD_FIELDS", "read_trace", "record_gate_consumption", "trace_gate_run"]
