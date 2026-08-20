"""classical_poetry runtime gates — invoked by STAGE_CHECKS at run time.

One consolidated CLI that binds this vertical to every contract it consumes, so a
poetry mission is gated on real contracts at RUN TIME exactly like fiction:

    intake-validate  poetry/task_envelope.json     -> Task Envelope (loop 1)
    prosody          poetry/draft_poem.txt         -> machine prosody (crown)
    review-validate  poetry/review.json            -> Review contract (loop 2)
    check-plan       poetry/review.json  poetry/revision_plan.json
    manifest-validate poetry/artifact_manifest.json -> Artifact manifest (loop 3)
    manifest-content poetry/artifact_manifest.json
    source-registry                                 -> Source registry (loop 4)
    check-usage      poetry/source_usage.json       -> Provenance ledger (loop 4)

Each exits 1 with a diagnostic on any violation, failing the stage. The prosody
gate is the crown: a poem with 出韵/失替/三平尾/孤平 fails here mechanically.
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
from .artifacts import POETRY_ARTIFACT_KINDS, POETRY_FINDING_TYPES
from .intake import PoetryIntakeError, brief_from_envelope
from .prosody import ProsodyError, analyze, render_report

_SOURCE_REGISTRY = Path(__file__).resolve().parent / "sources.yaml"

_ERRORS = (
    EnvelopeError,
    PoetryIntakeError,
    ReviewError,
    ManifestError,
    RegistryError,
    ProvenanceError,
    ProsodyError,
)


def _cmd_intake_validate(args) -> int:
    brief = brief_from_envelope(_load_json(args.envelope))
    print(
        f"OK: envelope valid + poetry-consumable (form={brief['form']}, jinti={brief['is_jinti']})"
    )
    return 0


def _cmd_prosody(args) -> int:
    text = Path(args.poem).read_text(encoding="utf-8")
    result = analyze(text)
    print(render_report(result))
    if not result["compliant"]:
        print("FAIL: 存在机检可判的出律/出韵/硬伤", file=sys.stderr)
        return 1
    return 0


def _cmd_review_validate(args) -> int:
    review = normalize_review(_load_json(args.review), type_vocabulary=POETRY_FINDING_TYPES)
    print(f"OK: review conforms ({len(review['findings'])} findings, verdict={review['verdict']})")
    return 0


def _cmd_check_plan(args) -> int:
    review = normalize_review(_load_json(args.review), type_vocabulary=POETRY_FINDING_TYPES)
    assert_plan_covers(review, _load_json(args.plan))
    print("OK: revision plan covers every blocking finding")
    return 0


def _cmd_manifest_validate(args) -> int:
    m = normalize_manifest(_load_json(args.manifest), kind_vocabulary=POETRY_ARTIFACT_KINDS)
    print(f"OK: manifest conforms ({len(m['artifacts'])} artifacts)")
    return 0


def _cmd_manifest_content(args) -> int:
    m = normalize_manifest(_load_json(args.manifest), kind_vocabulary=POETRY_ARTIFACT_KINDS)
    assert_content_present(m, Path.cwd())
    print(f"OK: all {len(m['artifacts'])} artifact files present")
    return 0


def _cmd_source_registry(args) -> int:
    reg = load_validated_registry(_SOURCE_REGISTRY)
    print(f"OK: source registry valid ({len(reg.get('items') or [])} items)")
    return 0


def _cmd_check_usage(args) -> int:
    reg = load_validated_registry(_SOURCE_REGISTRY)
    usage = normalize_usage(_load_json(args.usage), reg)
    print(f"OK: {len(usage['uses'])} source use(s) rights-defensible")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="poetry-checks")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("intake-validate")
    p.add_argument("envelope")
    p.set_defaults(func=_cmd_intake_validate)
    p = sub.add_parser("prosody")
    p.add_argument("poem")
    p.set_defaults(func=_cmd_prosody)
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
    except (OSError,) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    except _ERRORS as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
