"""Dispatch the research de-risk check selected by the Planner-authored checklist."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .checklist_store import store_items_for_stage


def selected_derisk_kind(project_root: Path) -> str:
    items = store_items_for_stage(project_root, "research")
    if items is not None:
        item = next((row for row in items if row.id == "research.signal_derisk"), None)
        if item is not None:
            rendered = f"{item.statement} {item.evidence_hint}"
            if "THEOREM_DERISK.json" in rendered:
                return "theorem"
    return "signal"


def validate_selected_gate(project_root: Path) -> tuple[bool, str, str]:
    kind = selected_derisk_kind(project_root)
    if kind == "theorem":
        from .theorem_derisk import DEFAULT_DERISK_PATH, validate_for_gate
    else:
        from .signal_derisk import DEFAULT_DERISK_PATH, validate_for_gate

    reject, concern = validate_for_gate(project_root, project_root / DEFAULT_DERISK_PATH)
    return reject, concern, kind


def _cmd_validate(args: argparse.Namespace) -> int:
    root = Path(args.project_root)
    reject, concern, kind = validate_selected_gate(root)
    if reject:
        print(f"REJECT ({kind}): {concern}", file=sys.stderr)
        return 1
    print(f"PASS: Planner-selected research de-risk gate ({kind})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="cmd", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--project-root", type=Path, default=Path("."))
    validate.set_defaults(func=_cmd_validate)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
