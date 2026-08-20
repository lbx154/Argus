"""modern_poetry runtime gates — one consolidated CLI wired into STAGE_CHECKS.

    intake-validate  poetry/task_envelope.json      -> Task Envelope (loop 1)
    form-check       poetry/draft_poem.txt poetry/form_spec.json  -> hard constraints
    review-validate  poetry/review.json             -> Review contract (loop 2)
    check-plan       poetry/review.json poetry/revision_plan.json
    manifest-validate poetry/artifact_manifest.json -> Artifact manifest (loop 3)
    manifest-content poetry/artifact_manifest.json
    source-registry                                  -> Source registry (loop 4)
    check-usage      poetry/source_usage.json        -> Provenance ledger (loop 4)

The form-check is the ONLY machine-quality gate and it is honestly thin: it fails
on a declared line-count/language/banned-word/empty violation, nothing more.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..literary.shared.artifact_manifest import (
    ManifestError,
    assert_content_present,
    normalize_manifest,
)
from ..literary.shared.artifact_manifest import (
    load_json_artifact as _load_json,
)
from ..literary.shared.provenance import ProvenanceError, normalize_usage
from ..literary.shared.review_contract import (
    ReviewError,
    assert_plan_covers,
    normalize_review,
)
from ..literary.shared.source_registry import RegistryError, load_validated_registry
from ..literary.shared.task_envelope import EnvelopeError
from .artifacts import MODERN_ARTIFACT_KINDS, MODERN_FINDING_TYPES
from .form import FormError, check_form
from .intake import ModernPoetryIntakeError, brief_from_envelope

_SOURCE_REGISTRY = Path(__file__).resolve().parent / "sources.yaml"
_ERRORS = (EnvelopeError, ModernPoetryIntakeError, ReviewError, ManifestError,
           RegistryError, ProvenanceError, FormError)


def _cmd_intake_validate(a) -> int:
    b = brief_from_envelope(_load_json(a.envelope))
    print(f"OK: envelope valid + modern-poetry-consumable (form={b['form']})")
    return 0


def _cmd_form_check(a) -> int:
    text = Path(a.poem).read_text(encoding="utf-8")
    spec = _load_json(a.spec) if a.spec else {}
    findings = check_form(text, spec)
    if findings:
        for f in findings:
            print(f"  FAIL [{f['type']}] {f['detail']}", file=sys.stderr)
        return 1
    print("OK: poem meets declared hard constraints")
    return 0


def _cmd_review_validate(a) -> int:
    r = normalize_review(_load_json(a.review), type_vocabulary=MODERN_FINDING_TYPES)
    print(f"OK: review conforms ({len(r['findings'])} findings)")
    return 0


def _cmd_check_plan(a) -> int:
    r = normalize_review(_load_json(a.review), type_vocabulary=MODERN_FINDING_TYPES)
    assert_plan_covers(r, _load_json(a.plan))
    print("OK: revision plan covers every blocking finding")
    return 0


def _cmd_manifest_validate(a) -> int:
    m = normalize_manifest(_load_json(a.manifest), kind_vocabulary=MODERN_ARTIFACT_KINDS)
    print(f"OK: manifest conforms ({len(m['artifacts'])} artifacts)")
    return 0


def _cmd_manifest_content(a) -> int:
    m = normalize_manifest(_load_json(a.manifest), kind_vocabulary=MODERN_ARTIFACT_KINDS)
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
    parser = argparse.ArgumentParser(prog="modern-poetry-checks")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("intake-validate")
    p.add_argument("envelope")
    p.set_defaults(func=_cmd_intake_validate)
    p = sub.add_parser("form-check")
    p.add_argument("poem")
    p.add_argument("spec", nargs="?")
    p.set_defaults(func=_cmd_form_check)
    p = sub.add_parser("review-validate")
    p.add_argument("review")
    p.set_defaults(func=_cmd_review_validate)
    p = sub.add_parser("check-plan")
    p.add_argument("review")
    p.add_argument("plan")
    p.set_defaults(func=_cmd_check_plan)
    p = sub.add_parser("manifest-validate")
    p.add_argument("manifest")
    p.set_defaults(func=_cmd_manifest_validate)
    p = sub.add_parser("manifest-content")
    p.add_argument("manifest")
    p.set_defaults(func=_cmd_manifest_content)
    p = sub.add_parser("source-registry")
    p.set_defaults(func=_cmd_source_registry)
    p = sub.add_parser("check-usage")
    p.add_argument("usage")
    p.set_defaults(func=_cmd_check_usage)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except OSError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    except _ERRORS as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
