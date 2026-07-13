"""Shared literary ARTIFACT MANIFEST contract — the creative-artifact version &
lineage record every literary vertical maintains for one authoring mission.

Where :mod:`.review_contract` governs the reviewer's finding payload and
:mod:`.task_envelope` governs the intake request, this module governs the
*bookkeeping of what was produced*: brief, plan, draft, state, review, revision,
final — each recorded as a node in a DAG that names what it was derived from
(``parent_artifact_ids``) and, optionally, the earlier version it replaces
(``supersedes``). That makes two things auditable that loose files cannot:

* **provenance** — a ``final`` deliverable can be traced back to the draft it
  revised and the review that drove the revision (:func:`lineage`);
* **version history** — a superseded artifact is explicitly marked, so you can
  tell the current version from the ones it replaced, and roll reasoning back.

``kind`` is a free string; each vertical owns its artifact VOCABULARY (fiction:
creative_brief/draft/story_state/…; poetry: form_plan/prosody_report/…) and
passes it as ``kind_vocabulary``. Explicit extension policy, mirroring the review
contract: reject an unknown kind when a vocabulary is supplied; accept any
non-empty kind when none is.

Semantic invariants the JSON schema cannot express, all enforced by
:func:`validate_manifest`:

* ``artifact_id`` is unique across the manifest;
* every ``parent_artifact_ids`` entry and every ``supersedes`` target references
  an artifact that actually exists in the manifest (no dangling lineage), and no
  artifact is its own parent or supersedes itself;
* the parent + supersedes edges form a DAG — a lineage cycle is rejected;
* ``supersedes`` and ``status`` are COHERENT in both directions: if X supersedes
  Y then Y must be marked ``"superseded"``, and conversely any artifact marked
  ``"superseded"`` must be replaced by exactly one successor — you cannot quietly
  retire an artifact nothing replaced, nor claim to replace one still active.

Content-on-disk existence is deliberately a SEPARATE, runtime-only check
(:func:`assert_content_present`) so the pure-data contract stays testable without
a filesystem, and a vertical's STAGE_CHECKS can enforce presence at run time.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import jsonschema

_SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"


def _load_schema(name: str) -> dict[str, Any]:
    with (_SCHEMA_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


ARTIFACT_MANIFEST_SCHEMA: dict[str, Any] = _load_schema("artifact_manifest.schema.json")


class ManifestError(ValueError):
    """Raised when an artifact manifest is structurally or semantically invalid."""


def _lineage_edges(artifact: dict[str, Any]) -> list[str]:
    """The ids an artifact directly derives from: its parents plus what it
    supersedes (both are strictly-earlier nodes in the lineage DAG)."""
    edges = list(artifact.get("parent_artifact_ids", []))
    sup = artifact.get("supersedes")
    if sup is not None:
        edges.append(sup)
    return edges


def _assert_acyclic(artifacts: list[dict[str, Any]]) -> None:
    by_id = {a["artifact_id"]: a for a in artifacts}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {aid: WHITE for aid in by_id}
    path: list[str] = []

    def visit(aid: str) -> None:
        color[aid] = GRAY
        path.append(aid)
        for nxt in _lineage_edges(by_id[aid]):
            if nxt not in by_id:  # unknown ref already reported elsewhere
                continue
            if color[nxt] == GRAY:
                cycle = path[path.index(nxt):] + [nxt]
                raise ManifestError(
                    f"artifact lineage has a cycle: {' -> '.join(cycle)}"
                )
            if color[nxt] == WHITE:
                visit(nxt)
        path.pop()
        color[aid] = BLACK

    for aid in by_id:
        if color[aid] == WHITE:
            visit(aid)


def validate_manifest(manifest: dict[str, Any], *,
                      kind_vocabulary: Iterable[str] | None = None) -> None:
    """Structural + semantic validation of a normalized artifact manifest.

    Structural: JSON schema (required fields, version>=1, status enum, no stray
    keys). Semantic: unique ids, parent/supersedes existence & no self-reference,
    acyclic lineage, supersedes<->superseded coherence, and (when supplied) the
    per-vertical kind vocabulary.
    """
    try:
        jsonschema.validate(manifest, ARTIFACT_MANIFEST_SCHEMA)
    except jsonschema.ValidationError as exc:
        raise ManifestError(f"invalid artifact_manifest: {exc.message}") from exc

    artifacts = manifest["artifacts"]
    ids = [a["artifact_id"] for a in artifacts]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ManifestError(f"duplicate artifact_id(s): {dupes}")
    id_set = set(ids)
    by_id = {a["artifact_id"]: a for a in artifacts}

    superseded_targets: dict[str, list[str]] = {}
    for a in artifacts:
        aid = a["artifact_id"]
        for parent in a.get("parent_artifact_ids", []):
            if parent == aid:
                raise ManifestError(f"{aid!r} lists itself as a parent")
            if parent not in id_set:
                raise ManifestError(
                    f"{aid!r} references unknown parent {parent!r}"
                )
        sup = a.get("supersedes")
        if sup is not None:
            if sup == aid:
                raise ManifestError(f"{aid!r} supersedes itself")
            if sup not in id_set:
                raise ManifestError(f"{aid!r} supersedes unknown artifact {sup!r}")
            if by_id[sup]["status"] != "superseded":
                raise ManifestError(
                    f"{aid!r} supersedes {sup!r} but {sup!r} status is "
                    f"{by_id[sup]['status']!r}, not 'superseded'"
                )
            superseded_targets.setdefault(sup, []).append(aid)

    # Backward coherence: a 'superseded' artifact must have exactly one successor.
    for a in artifacts:
        if a["status"] == "superseded":
            succ = superseded_targets.get(a["artifact_id"], [])
            if not succ:
                raise ManifestError(
                    f"{a['artifact_id']!r} is marked 'superseded' but no artifact "
                    f"supersedes it"
                )
            if len(succ) > 1:
                raise ManifestError(
                    f"{a['artifact_id']!r} is superseded by more than one artifact "
                    f"{sorted(succ)} — version history must be linear"
                )

    _assert_acyclic(artifacts)

    if kind_vocabulary is not None:
        vocab = set(kind_vocabulary)
        for a in artifacts:
            if a["kind"] not in vocab:
                raise ManifestError(
                    f"{a['artifact_id']!r}: unknown kind {a['kind']!r} "
                    f"(vocabulary: {sorted(vocab)})"
                )


def normalize_manifest(raw: dict[str, Any], *,
                       kind_vocabulary: Iterable[str] | None = None) -> dict[str, Any]:
    """Fill per-artifact defaults (parent/constraint/source refs, change_reason,
    supersedes, status), then validate. Returns a new dict; input is not mutated."""
    if not isinstance(raw, dict):
        raise ManifestError("artifact_manifest must be a JSON object")
    manifest = dict(raw)
    artifacts = manifest.get("artifacts", [])
    if not isinstance(artifacts, list):
        raise ManifestError("artifact_manifest.artifacts must be an array")
    norm: list[dict[str, Any]] = []
    for a in artifacts:
        if not isinstance(a, dict):
            raise ManifestError("each artifact must be an object")
        g = dict(a)
        g.setdefault("parent_artifact_ids", [])
        g.setdefault("constraint_refs", [])
        g.setdefault("source_refs", [])
        g.setdefault("change_reason", "")
        g.setdefault("supersedes", None)
        g.setdefault("status", "active")
        norm.append(g)
    manifest["artifacts"] = norm
    validate_manifest(manifest, kind_vocabulary=kind_vocabulary)
    return manifest


def lineage(manifest: dict[str, Any], artifact_id: str) -> list[str]:
    """Ordered transitive ANCESTORS of ``artifact_id`` via ``parent_artifact_ids``
    (breadth-first, nearest first). This is the provenance query: the ancestry of
    a ``final`` deliverable is the exact set of artifacts it descends from.
    """
    by_id = {a["artifact_id"]: a for a in manifest["artifacts"]}
    if artifact_id not in by_id:
        raise ManifestError(f"unknown artifact_id {artifact_id!r}")
    seen: set[str] = set()
    order: list[str] = []
    frontier = list(by_id[artifact_id].get("parent_artifact_ids", []))
    while frontier:
        pid = frontier.pop(0)
        if pid in seen or pid not in by_id:
            continue
        seen.add(pid)
        order.append(pid)
        frontier.extend(by_id[pid].get("parent_artifact_ids", []))
    return order


def assert_content_present(manifest: dict[str, Any], base_dir: str | Path) -> None:
    """Raise :class:`ManifestError` unless every artifact's ``content_path`` exists
    as a non-empty file under ``base_dir``. Runtime-only: a manifest that records
    an artifact whose file is missing or empty is not a faithful record.
    """
    base = Path(base_dir)
    for a in manifest["artifacts"]:
        path = base / a["content_path"]
        if not path.is_file():
            raise ManifestError(
                f"{a['artifact_id']!r}: content_path {a['content_path']!r} not "
                f"found under {base}"
            )
        if path.stat().st_size == 0:
            raise ManifestError(
                f"{a['artifact_id']!r}: content_path {a['content_path']!r} is empty"
            )


__all__ = [
    "ARTIFACT_MANIFEST_SCHEMA",
    "ManifestError",
    "validate_manifest",
    "normalize_manifest",
    "lineage",
    "assert_content_present",
]
