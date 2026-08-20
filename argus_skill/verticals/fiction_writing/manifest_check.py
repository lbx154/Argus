"""fiction_writing runtime artifact-manifest gate — invoked by STAGE_CHECKS at
run time.

Binds the shared artifact-manifest contract
(:mod:`argus_skill.verticals.literary.shared.artifact_manifest`) to fiction's
artifact VOCABULARY
so the revise stage is gated on a real lineage record at RUN TIME (via the
STAGE_CHECKS shell runner), not only in unit tests. This is what makes the
artifact chain an auditable runtime fact rather than a helper only tests call.

Subcommands (run from the mission dir; cwd holds ``fiction/``):

    validate      fiction/artifact_manifest.json
        exit 0 iff the manifest conforms to the contract under fiction's vocab
        (unique ids, existing parents/supersedes, acyclic, supersede coherence).
    check-content fiction/artifact_manifest.json
        exit 0 iff every recorded artifact's content_path exists as a non-empty
        file — the manifest must describe artifacts that were actually produced.
    check-lineage fiction/artifact_manifest.json
        exit 0 iff the 'final' deliverable traces back to both a 'draft' and a
        'review' — provenance of the shipped prose is closed, not fabricated.

Exit 1 with a diagnostic on any violation, so the STAGE_CHECK fails the stage.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..literary.shared.artifact_manifest import (
    ManifestError,
    assert_content_present,
    lineage,
    normalize_manifest,
)
from ..literary.shared.artifact_manifest import (
    load_json_artifact as _load_json,
)
from .artifacts import FICTION_ARTIFACT_KINDS

_REQUIRED_ANCESTOR_KINDS = frozenset({"draft", "review"})


def _load_manifest(path: str) -> dict:
    return normalize_manifest(_load_json(path), kind_vocabulary=FICTION_ARTIFACT_KINDS)


def _cmd_validate(args: argparse.Namespace) -> int:
    manifest = _load_manifest(args.manifest)
    print(f"OK: manifest conforms ({len(manifest['artifacts'])} artifacts)")
    return 0


def _cmd_check_content(args: argparse.Namespace) -> int:
    manifest = _load_manifest(args.manifest)
    assert_content_present(manifest, Path.cwd())
    print(f"OK: all {len(manifest['artifacts'])} artifact files present")
    return 0


def _cmd_check_lineage(args: argparse.Namespace) -> int:
    manifest = _load_manifest(args.manifest)
    by_id = {a["artifact_id"]: a for a in manifest["artifacts"]}
    finals = [a for a in manifest["artifacts"] if a["kind"] == "final"]
    if not finals:
        raise ManifestError(
            "no artifact of kind 'final' — the deliverable's provenance cannot "
            "be traced"
        )
    fin = finals[0]
    ancestor_kinds = {by_id[i]["kind"] for i in lineage(manifest, fin["artifact_id"])}
    missing = _REQUIRED_ANCESTOR_KINDS - ancestor_kinds
    if missing:
        raise ManifestError(
            f"final artifact {fin['artifact_id']!r} does not trace back to "
            f"{sorted(missing)} — broken provenance"
        )
    print(f"OK: final traces back through {sorted(ancestor_kinds)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fiction-manifest-check")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pv = sub.add_parser("validate", help="validate fiction/artifact_manifest.json")
    pv.add_argument("manifest")
    pv.set_defaults(func=_cmd_validate)

    pc = sub.add_parser("check-content", help="every artifact file is present")
    pc.add_argument("manifest")
    pc.set_defaults(func=_cmd_check_content)

    pl = sub.add_parser("check-lineage", help="final traces back to draft + review")
    pl.add_argument("manifest")
    pl.set_defaults(func=_cmd_check_lineage)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ManifestError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
