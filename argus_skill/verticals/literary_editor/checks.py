"""literary_editor runtime gates — one consolidated CLI wired into STAGE_CHECKS.

    intake-validate  editor/task_envelope.json      -> Task Envelope (loop 1)
    edit-check       editor/source.txt editor/edited.txt editor/edit_brief.json
                     -> EDIT DISCIPLINE (the machine gate)
    review-validate  editor/review.json             -> Review contract (loop 2)
    check-plan       editor/review.json editor/revision_plan.json
    manifest-validate editor/artifact_manifest.json -> Artifact manifest (loop 3)
    manifest-content editor/artifact_manifest.json
    source-registry                                  -> Source registry (loop 4)
    check-usage      editor/source_usage.json        -> Provenance ledger (loop 4)

edit-check is the crown: it enforces that the edit respected its MODE (a critique
does not rewrite, a proofread does not become a rewrite, an expand adds material)
and that every must-keep segment survived verbatim. Whether the edit reads better
is live-reviewer, not gated here.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ...literary.artifact_manifest import (
    ManifestError,
    assert_content_present,
    normalize_manifest,
)
from ...literary.provenance import ProvenanceError, normalize_usage
from ...literary.review_contract import (
    ReviewError,
    assert_plan_covers,
    normalize_review,
)
from ...literary.source_registry import RegistryError, load_validated_registry
from ...literary.task_envelope import EnvelopeError
from .artifacts import EDITOR_ARTIFACT_KINDS, EDITOR_FINDING_TYPES
from .edit_ops import EditError, check_edit
from .intake import EditorIntakeError, brief_from_envelope

_SOURCE_REGISTRY = Path(__file__).resolve().parent / "sources.yaml"
_ERRORS = (EnvelopeError, EditorIntakeError, ReviewError, ManifestError,
           RegistryError, ProvenanceError, EditError)


def _load_json(path: str):
    p = Path(path)
    if not p.is_file():
        raise ManifestError(f"file not found: {path}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{path} is not valid JSON: {exc}") from exc


def _read(path: str) -> str:
    p = Path(path)
    if not p.is_file():
        raise EditError(f"file not found: {path}")
    return p.read_text(encoding="utf-8")


def _cmd_intake_validate(a) -> int:
    b = brief_from_envelope(_load_json(a.envelope))
    print(f"OK: envelope valid + editor-consumable (mode={b['mode']})")
    return 0


def _cmd_edit_check(a) -> int:
    brief = _load_json(a.brief)
    if not isinstance(brief, dict) or "mode" not in brief:
        raise EditError("edit_brief must be an object carrying a 'mode'")
    findings = check_edit(_read(a.source), _read(a.edited),
                          brief["mode"], brief.get("must_keep"))
    if findings:
        for f in findings:
            print(f"  FAIL [{f['type']}] {f['detail']}", file=sys.stderr)
        return 1
    print(f"OK: edit respects its {brief['mode']!r} discipline and must-keep list")
    return 0


def _cmd_review_validate(a) -> int:
    r = normalize_review(_load_json(a.review), type_vocabulary=EDITOR_FINDING_TYPES)
    print(f"OK: review conforms ({len(r['findings'])} findings)")
    return 0


def _cmd_check_plan(a) -> int:
    r = normalize_review(_load_json(a.review), type_vocabulary=EDITOR_FINDING_TYPES)
    assert_plan_covers(r, _load_json(a.plan))
    print("OK: revision plan covers every blocking finding")
    return 0


def _cmd_manifest_validate(a) -> int:
    m = normalize_manifest(_load_json(a.manifest), kind_vocabulary=EDITOR_ARTIFACT_KINDS)
    print(f"OK: manifest conforms ({len(m['artifacts'])} artifacts)")
    return 0


def _cmd_manifest_content(a) -> int:
    m = normalize_manifest(_load_json(a.manifest), kind_vocabulary=EDITOR_ARTIFACT_KINDS)
    assert_content_present(m, Path.cwd())
    print("OK: all artifact files present")
    return 0


def _cmd_source_registry(a) -> int:
    reg = load_validated_registry(_SOURCE_REGISTRY)
    print(f"OK: source registry valid ({len(reg.get('items') or [])} items)")
    return 0


def _cmd_check_usage(a) -> int:
    reg = load_validated_registry(_SOURCE_REGISTRY)
    u = normalize_usage(_load_json(a.usage), reg)
    print(f"OK: {len(u['uses'])} source use(s) rights-defensible")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="editor-checks")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("intake-validate"); p.add_argument("envelope"); p.set_defaults(func=_cmd_intake_validate)
    p = sub.add_parser("edit-check"); p.add_argument("source"); p.add_argument("edited"); p.add_argument("brief"); p.set_defaults(func=_cmd_edit_check)
    p = sub.add_parser("review-validate"); p.add_argument("review"); p.set_defaults(func=_cmd_review_validate)
    p = sub.add_parser("check-plan"); p.add_argument("review"); p.add_argument("plan"); p.set_defaults(func=_cmd_check_plan)
    p = sub.add_parser("manifest-validate"); p.add_argument("manifest"); p.set_defaults(func=_cmd_manifest_validate)
    p = sub.add_parser("manifest-content"); p.add_argument("manifest"); p.set_defaults(func=_cmd_manifest_content)
    p = sub.add_parser("source-registry"); p.set_defaults(func=_cmd_source_registry)
    p = sub.add_parser("check-usage"); p.add_argument("usage"); p.set_defaults(func=_cmd_check_usage)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except OSError as exc:
        print(f"FAIL: {exc}", file=sys.stderr); return 1
    except _ERRORS as exc:
        print(f"FAIL: {exc}", file=sys.stderr); return 1


if __name__ == "__main__":
    sys.exit(main())
