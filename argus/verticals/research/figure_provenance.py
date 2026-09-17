"""Renderer-neutral provenance for research-paper figures."""

from __future__ import annotations

import argparse
import json
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

import portalocker

from ...core.file_digest import sha256_file as _sha256

FIGURE_PROVENANCE_PATH = Path("paper/figures/FIGURE_PROVENANCE.json")
FIGURE_PROVENANCE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class FigureProvenanceIssue:
    code: str
    figure_id: str
    detail: str


@dataclass
class FigureProvenanceReport:
    manifest_path: Path
    entries: list[dict[str, object]] = field(default_factory=list)
    output_paths: set[str] = field(default_factory=set)
    issues: list[FigureProvenanceIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


def _normalized_resolved_path(path: Path) -> Path:
    r"""Resolve a path while normalizing Windows' optional extended prefix.

    ``Path.resolve()`` can intermittently return ``\\?\C:\...`` while another
    thread is creating a parent directory, even when the same path normally
    resolves to ``C:\...``.  Those spellings identify the same object but
    ``Path.relative_to`` treats them as different anchors and falsely reports a
    project escape.  Normalize only the Win32 namespace spelling; containment
    is still checked against fully resolved paths.
    """
    resolved = path.resolve()
    if os.name != "nt":
        return resolved
    raw = str(resolved)
    if raw.startswith("\\\\?\\UNC\\"):
        return Path("\\\\" + raw[8:])
    if raw.startswith("\\\\?\\"):
        return Path(raw[4:])
    return resolved


def _project_path(project_root: Path, raw: Path | str) -> Path:
    root = _normalized_resolved_path(project_root)
    candidate = Path(raw).expanduser()
    resolved = _normalized_resolved_path(
        candidate if candidate.is_absolute() else root / candidate
    )
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"figure provenance path escapes project root: {raw}") from exc
    return resolved


def _relative(project_root: Path, path: Path) -> str:
    return _normalized_resolved_path(path).relative_to(
        _normalized_resolved_path(project_root)
    ).as_posix()


def _load_manifest(path: Path) -> dict[str, object]:
    if not path.exists():
        return {
            "schema_version": FIGURE_PROVENANCE_SCHEMA_VERSION,
            "figures": [],
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"figure provenance manifest must be a JSON object: {path}")
    if payload.get("schema_version") != FIGURE_PROVENANCE_SCHEMA_VERSION:
        raise ValueError(
            "unsupported figure provenance schema: "
            f"{payload.get('schema_version')!r}"
        )
    if not isinstance(payload.get("figures"), list):
        raise ValueError("figure provenance manifest `figures` must be a list")
    return payload


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _file_record(project_root: Path, raw: Path | str) -> dict[str, str]:
    path = _project_path(project_root, raw)
    if not path.is_file():
        raise FileNotFoundError(path)
    return {"path": _relative(project_root, path), "sha256": _sha256(path)}


def _transaction_lock_path(project_root: Path) -> Path:
    return (
        _normalized_resolved_path(project_root)
        / "paper"
        / "figures"
        / ".figure-manifests.lock"
    )


@contextmanager
def figure_manifest_transaction(project_root: Path):
    lock = _transaction_lock_path(project_root)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with portalocker.Lock(lock, mode="a+"):
        yield


def _upsert_entry_unlocked(
    manifest: Path,
    entry: dict[str, object],
) -> None:
    payload = _load_manifest(manifest)
    figure_id = str(entry["figure_id"])
    figures = [
        item
        for item in payload["figures"]
        if not (
            isinstance(item, dict)
            and str(item.get("figure_id") or "").strip() == figure_id
        )
    ]
    figures.append(entry)
    payload["figures"] = sorted(
        figures,
        key=lambda item: (
            str(item.get("figure_id") or "")
            if isinstance(item, dict)
            else ""
        ),
    )
    _atomic_write_json(manifest, payload)


def register_figure(
    *,
    project_root: Path,
    figure_id: str,
    role: str,
    renderer: str,
    source_path: Path,
    output_path: Path,
    inputs: Iterable[Path] = (),
    review_path: Path | None = None,
    render_metadata_path: Path | None = None,
    command: str = "",
    manifest_path: Path = FIGURE_PROVENANCE_PATH,
    _transaction_locked: bool = False,
) -> dict[str, object]:
    """Upsert one figure from real source/output files and computed hashes."""
    if not figure_id.strip():
        raise ValueError("figure_id must be non-empty")
    if not role.strip():
        raise ValueError("role must be non-empty")
    if not renderer.strip():
        raise ValueError("renderer must be non-empty")
    input_paths = list(inputs)
    root = project_root.resolve()
    source = _file_record(root, source_path)
    output = _file_record(root, output_path)
    input_records = [_file_record(root, item) for item in input_paths]
    review = _file_record(root, review_path) if review_path is not None else None
    render_metadata = (
        _file_record(root, render_metadata_path)
        if render_metadata_path is not None
        else None
    )
    entry: dict[str, object] = {
        "figure_id": figure_id.strip(),
        "role": role.strip(),
        "renderer": renderer.strip(),
        "source_path": source["path"],
        "source_sha256": source["sha256"],
        "output_path": output["path"],
        "output_sha256": output["sha256"],
        "inputs": input_records,
        "registered_at": datetime.now(UTC).isoformat(),
    }
    if review is not None:
        entry["review_path"] = review["path"]
        entry["review_sha256"] = review["sha256"]
    if render_metadata is not None:
        entry["render_metadata_path"] = render_metadata["path"]
        entry["render_metadata_sha256"] = render_metadata["sha256"]
    if command.strip():
        entry["command"] = command.strip()

    manifest = _project_path(root, manifest_path)
    if _transaction_locked:
        _upsert_entry_unlocked(manifest, entry)
    else:
        with figure_manifest_transaction(root):
            _upsert_entry_unlocked(manifest, entry)
    return entry


def _validate_recorded_file(
    *,
    report: FigureProvenanceReport,
    project_root: Path,
    figure_id: str,
    entry: dict[str, object],
    path_field: str,
    hash_field: str,
) -> str | None:
    raw_path = str(entry.get(path_field) or "").strip()
    expected_hash = str(entry.get(hash_field) or "").strip().lower()
    if not raw_path or not expected_hash:
        report.issues.append(
            FigureProvenanceIssue(
                code="missing_file_record",
                figure_id=figure_id,
                detail=f"{path_field} and {hash_field} are required",
            )
        )
        return None
    try:
        path = _project_path(project_root, raw_path)
    except ValueError as exc:
        report.issues.append(
            FigureProvenanceIssue("path_escape", figure_id, str(exc))
        )
        return None
    if not path.is_file():
        report.issues.append(
            FigureProvenanceIssue(
                "missing_file",
                figure_id,
                f"{path_field} does not exist: {raw_path}",
            )
        )
        return None
    actual_hash = _sha256(path)
    if actual_hash != expected_hash:
        report.issues.append(
            FigureProvenanceIssue(
                "hash_mismatch",
                figure_id,
                f"{hash_field} does not match {raw_path}",
            )
        )
        return None
    return _relative(project_root, path)


def validate_figure_provenance(
    project_root: Path,
    *,
    manifest_path: Path = FIGURE_PROVENANCE_PATH,
) -> FigureProvenanceReport:
    root = project_root.resolve()
    unresolved_manifest = (
        manifest_path.expanduser()
        if manifest_path.is_absolute()
        else root / manifest_path
    )
    try:
        manifest = _project_path(root, manifest_path)
    except ValueError as exc:
        report = FigureProvenanceReport(manifest_path=unresolved_manifest)
        report.issues.append(
            FigureProvenanceIssue("path_escape", "", str(exc))
        )
        return report
    report = FigureProvenanceReport(manifest_path=manifest)
    if not manifest.is_file():
        report.issues.append(
            FigureProvenanceIssue(
                "missing_manifest",
                "",
                f"figure provenance manifest not found: {_relative(root, manifest)}",
            )
        )
        return report
    try:
        payload = _load_manifest(manifest)
    except (OSError, ValueError) as exc:
        report.issues.append(
            FigureProvenanceIssue("invalid_manifest", "", str(exc))
        )
        return report

    figures = payload["figures"]
    if not figures:
        report.issues.append(
            FigureProvenanceIssue("empty_manifest", "", "manifest contains no figures")
        )
        return report
    seen: set[str] = set()
    for raw_entry in figures:
        if not isinstance(raw_entry, dict):
            report.issues.append(
                FigureProvenanceIssue(
                    "invalid_entry",
                    "",
                    "every figure entry must be a JSON object",
                )
            )
            continue
        entry = dict(raw_entry)
        figure_id = str(entry.get("figure_id") or "").strip()
        if not figure_id:
            report.issues.append(
                FigureProvenanceIssue("missing_figure_id", "", "figure_id is required")
            )
            continue
        if figure_id in seen:
            report.issues.append(
                FigureProvenanceIssue("duplicate_figure_id", figure_id, "duplicate entry")
            )
        seen.add(figure_id)
        for field_name in ("role", "renderer"):
            if not str(entry.get(field_name) or "").strip():
                report.issues.append(
                    FigureProvenanceIssue(
                        f"missing_{field_name}",
                        figure_id,
                        f"{field_name} is required",
                    )
                )
        _validate_recorded_file(
            report=report,
            project_root=root,
            figure_id=figure_id,
            entry=entry,
            path_field="source_path",
            hash_field="source_sha256",
        )
        output = _validate_recorded_file(
            report=report,
            project_root=root,
            figure_id=figure_id,
            entry=entry,
            path_field="output_path",
            hash_field="output_sha256",
        )
        if output:
            report.output_paths.add(output)
        inputs = entry.get("inputs")
        if inputs is None:
            inputs = []
        if not isinstance(inputs, list):
            report.issues.append(
                FigureProvenanceIssue(
                    "invalid_inputs",
                    figure_id,
                    "inputs must be a JSON list",
                )
            )
            inputs = []
        for index, input_record in enumerate(inputs):
            if not isinstance(input_record, dict):
                report.issues.append(
                    FigureProvenanceIssue(
                        "invalid_input_record",
                        figure_id,
                        f"inputs[{index}] must be an object",
                    )
                )
                continue
            _validate_recorded_file(
                report=report,
                project_root=root,
                figure_id=figure_id,
                entry=input_record,
                path_field="path",
                hash_field="sha256",
            )
        for path_field, hash_field in (
            ("review_path", "review_sha256"),
            ("render_metadata_path", "render_metadata_sha256"),
        ):
            if path_field in entry or hash_field in entry:
                _validate_recorded_file(
                    report=report,
                    project_root=root,
                    figure_id=figure_id,
                    entry=entry,
                    path_field=path_field,
                    hash_field=hash_field,
                )
        report.entries.append(entry)
    return report


def preflight_figure_provenance(
    project_root: Path,
    *,
    manifest_path: Path = FIGURE_PROVENANCE_PATH,
) -> Path:
    """Fail before renderer-specific metadata changes if canonical state is invalid."""
    root = project_root.resolve()
    manifest = _project_path(root, manifest_path)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    if manifest.exists():
        _load_manifest(manifest)
    return manifest


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command_name", required=True)
    register = subparsers.add_parser("register")
    register.add_argument("--project-root", type=Path, default=Path("."))
    register.add_argument("--figure-id", required=True)
    register.add_argument("--role", required=True)
    register.add_argument("--renderer", required=True)
    register.add_argument("--source", type=Path, required=True)
    register.add_argument("--output", type=Path, required=True)
    register.add_argument("--input", type=Path, action="append", default=[])
    register.add_argument("--review", type=Path)
    register.add_argument("--render-metadata", type=Path)
    register.add_argument("--command", default="")
    validate = subparsers.add_parser("validate")
    validate.add_argument("--project-root", type=Path, default=Path("."))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command_name == "register":
        entry = register_figure(
            project_root=args.project_root,
            figure_id=args.figure_id,
            role=args.role,
            renderer=args.renderer,
            source_path=args.source,
            output_path=args.output,
            inputs=args.input,
            review_path=args.review,
            render_metadata_path=args.render_metadata,
            command=args.command,
        )
        print(json.dumps(entry, indent=2, sort_keys=True))
        return 0
    report = validate_figure_provenance(args.project_root)
    print(
        json.dumps(
            {
                "ok": report.ok,
                "manifest": str(report.manifest_path),
                "figures": len(report.entries),
                "issues": [issue.__dict__ for issue in report.issues],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
