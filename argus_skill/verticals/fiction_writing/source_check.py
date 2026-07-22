"""fiction_writing runtime PROVENANCE gate — invoked by STAGE_CHECKS at run time.

Binds the shared source-registry + provenance contracts
(:mod:`argus_skill.verticals.literary.shared.source_registry`,
:mod:`argus_skill.verticals.literary.shared.provenance`) to fiction's committed
rights catalog so
that source consumption is gated on real rights at RUN TIME, not only in unit
tests.

Subcommands (run from the mission dir; cwd holds ``fiction/``):

    validate-registry
        exit 0 iff fiction's source registry (sources.yaml) is well-formed
        (providers/items coherent, allowed/prohibited uses in-vocab and disjoint,
        ingested items carry a checksum + cleared rights). Unconditional.
    check-usage  fiction/source_usage.json
        exit 0 iff every recorded source use is defensible against the registry:
        the source is registered, the use is permitted by its rights, an
        evidence_citation carries attribution, and a use implying ingestion names
        an actually-ingested source. This file is OPTIONAL — a mission that
        consults no external source produces none — so the stage wires this behind
        ``test ! -f fiction/source_usage.json ||``: absent → pass, present →
        strictly enforced.

Exit 1 with a diagnostic on any violation, so the STAGE_CHECK fails the stage.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..literary.shared.provenance import ProvenanceError, normalize_usage
from ..literary.shared.source_registry import RegistryError
from .sources import load_fiction_registry


def _load_json(path: str) -> object:
    p = Path(path)
    if not p.is_file():
        raise ProvenanceError(f"file not found: {path}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProvenanceError(f"{path} is not valid JSON: {exc}") from exc


def _cmd_validate_registry(args: argparse.Namespace) -> int:
    registry = load_fiction_registry()  # loads + validates, raises on malformed
    print(f"OK: source registry valid ({len(registry.get('items') or [])} items)")
    return 0


def _cmd_check_usage(args: argparse.Namespace) -> int:
    registry = load_fiction_registry()
    usage = normalize_usage(_load_json(args.usage), registry)
    print(f"OK: {len(usage['uses'])} source use(s) rights-defensible")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fiction-source-check")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("validate-registry", help="validate fiction sources.yaml")
    pr.set_defaults(func=_cmd_validate_registry)

    pu = sub.add_parser("check-usage", help="check fiction/source_usage.json")
    pu.add_argument("usage")
    pu.set_defaults(func=_cmd_check_usage)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (RegistryError, ProvenanceError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
