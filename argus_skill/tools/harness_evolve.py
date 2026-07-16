"""Per-project prompt-rule overlay CLI.

Checklist ownership is intentionally absent here: framework/vertical code
provides the seed, Planner ``checklist_ops`` is the sole runtime write path, and
Reviewer emits feedback only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..skills import harness_overlay as ho


def _cmd_add_rule(args: argparse.Namespace, root: Path) -> int:
    entry = ho.add_prompt_rule(
        root,
        rule={
            "id": args.id,
            "role": args.role,
            "text": args.text,
            "reason": args.reason,
        },
    )
    print(f"✓ added prompt rule {entry['id']!r} (state={entry['state']})")
    return 0


def _cmd_list(_args: argparse.Namespace, root: Path) -> int:
    print(json.dumps({
        "active": ho.load_overlay(root, state="active"),
        "pending": ho.load_overlay(root, state="pending"),
    }, indent=2, sort_keys=True))
    return 0


def _cmd_promote(args: argparse.Namespace, root: Path) -> int:
    if not ho.promote(root, entry_id=args.id):
        print(f"✗ no promotable prompt rule {args.id!r}", file=sys.stderr)
        return 1
    print(f"✓ promoted {args.id!r}")
    return 0


def _cmd_revert(args: argparse.Namespace, root: Path) -> int:
    if not ho.revert(root, entry_id=args.id):
        print(f"✗ no overlay entry {args.id!r}", file=sys.stderr)
        return 1
    print(f"✓ reverted {args.id!r}")
    return 0


def _cmd_reset(_args: argparse.Namespace, root: Path) -> int:
    removed = ho.reset(root)
    print(f"✓ reset overlay: removed {removed} entr{'y' if removed == 1 else 'ies'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="harness-evolve")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help="Project root (default: ARGUS_SKILL_PROJECT_ROOT env, else cwd).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add-rule", help="Add a project-local role prompt rule.")
    p.add_argument("--role", default="engineer", choices=list(ho.VALID_ROLES))
    p.add_argument("--id", required=True)
    p.add_argument("--text", required=True)
    p.add_argument("--reason", required=True)
    p.set_defaults(func=_cmd_add_rule)

    sub.add_parser("list", help="List active and pending prompt rules.").set_defaults(
        func=_cmd_list
    )

    p = sub.add_parser("promote", help="Promote a pending prompt rule.")
    p.add_argument("--id", required=True)
    p.set_defaults(func=_cmd_promote)

    p = sub.add_parser("revert", help="Remove one overlay rule by id.")
    p.add_argument("--id", required=True)
    p.set_defaults(func=_cmd_revert)

    sub.add_parser("reset", help="Clear project prompt rules.").set_defaults(
        func=_cmd_reset
    )

    args = parser.parse_args()
    root = ho.resolve_project_root(args.project_root)
    try:
        return args.func(args, root)
    except ho.OverlayValidationError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
