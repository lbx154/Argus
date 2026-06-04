"""Harness self-evolution CLI — the agent's interface to adapt its own harness.

The framework's stage checklists and reviewer/planner/engineer house rules are an
immutable FLOOR. This tool lets the running agent layer a **per-project overlay**
on top of that floor — adding new checklist items, annotating existing ones, or
adding house rules — without a daemon restart and without touching framework code.
Every change is recorded in ``.argus/harness/journal.jsonl`` and is fully
revertible, so the project can both *recover* and *apply*.

Routing (safety): engineer-targeted additions activate immediately (they can only
add work, never relax a judge). Reviewer/planner rule changes and any
``supersede`` land in PENDING and take effect only after ``promote``.

Usage (run from inside a mission; project root auto-resolved):

    python -m argus_skill.tools.harness_evolve add-item \
        --stage run --id run.hparam_log --role engineer \
        --statement "Log lr, max_completion_length, num_generations per RL run." \
        --evidence "experiments/*/run_config.json" \
        --reason "RL collapse last mission traced to an unlogged max_completion_length."

    python -m argus_skill.tools.harness_evolve amend-item \
        --id run.score_variance --role reviewer \
        --note "For RL runs, require >=3 seeds before declaring an improvement." \
        --reason "Single-seed RL deltas were noise last mission."

    python -m argus_skill.tools.harness_evolve add-rule \
        --role engineer --id eng.rl_defaults \
        --text "Before any RL run, sanity-check max_completion_length vs task length." \
        --reason "Recurring misconfiguration."

    python -m argus_skill.tools.harness_evolve list
    python -m argus_skill.tools.harness_evolve promote --id run.score_variance
    python -m argus_skill.tools.harness_evolve revert --id run.hparam_log
    python -m argus_skill.tools.harness_evolve reset            # clear all
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..skills import harness_overlay as ho
from ..skills.stage_checklists import (
    CANONICAL_STAGE_ORDER,
    STAGE_CHECKLISTS,
    format_full_pipeline_checklist,
    format_stage_checklist,
)


def _known() -> tuple[frozenset[str], frozenset[str]]:
    stages = frozenset(CANONICAL_STAGE_ORDER)
    item_ids = frozenset(
        item.id for items in STAGE_CHECKLISTS.values() for item in items
    )
    return stages, item_ids


def _render_smoke(project_root: Path, *, stage: str | None, role: str) -> None:
    """Render the affected checklist(s) to prove the overlay does not break
    prompt building. Raises if rendering fails."""

    if stage:
        format_stage_checklist(stage, role=role, project_root=project_root)
    format_full_pipeline_checklist(role="reviewer", project_root=project_root)


def _cmd_add_item(args: argparse.Namespace, root: Path) -> int:
    stages, item_ids = _known()
    item = {
        "id": args.id,
        "stage": args.stage,
        "role": args.role,
        "op": "add",
        "statement": args.statement,
        "evidence_hint": args.evidence,
        "reason": args.reason,
    }
    snap = ho.snapshot(root)
    entry = ho.add_checklist_item(root, item=item, known_stages=stages, known_item_ids=item_ids)
    try:
        _render_smoke(root, stage=args.stage, role=args.role)
    except Exception as exc:  # noqa: BLE001 - rollback to exact prior state
        ho.restore(root, snap)
        print(f"✗ render smoke test failed; change rolled back: {exc}", file=sys.stderr)
        return 1
    print(f"✓ added checklist item {entry['id']!r} (state={entry['state']})")
    return 0


def _cmd_amend_item(args: argparse.Namespace, root: Path) -> int:
    stages, item_ids = _known()
    base_stage = next(
        (s for s, items in STAGE_CHECKLISTS.items() for it in items if it.id == args.id),
        None,
    )
    item = {
        "id": args.id,
        "stage": base_stage or "",
        "role": args.role,
        "op": "amend",
        "note": args.note,
        "reason": args.reason,
    }
    snap = ho.snapshot(root)
    try:
        entry = ho.add_checklist_item(root, item=item, known_stages=stages, known_item_ids=item_ids)
    except ho.OverlayValidationError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    try:
        _render_smoke(root, stage=base_stage, role=args.role)
    except Exception as exc:  # noqa: BLE001
        ho.restore(root, snap)
        print(f"✗ render smoke test failed; change rolled back: {exc}", file=sys.stderr)
        return 1
    print(f"✓ annotated floor item {entry['id']!r} (state={entry['state']})")
    return 0


def _cmd_add_rule(args: argparse.Namespace, root: Path) -> int:
    rule = {"id": args.id, "role": args.role, "text": args.text, "reason": args.reason}
    snap = ho.snapshot(root)
    try:
        entry = ho.add_prompt_rule(root, rule=rule)
    except ho.OverlayValidationError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    try:
        _render_smoke(root, stage=None, role=args.role)
    except Exception as exc:  # noqa: BLE001
        ho.restore(root, snap)
        print(f"✗ render smoke test failed; change rolled back: {exc}", file=sys.stderr)
        return 1
    print(f"✓ added house rule {entry['id']!r} (state={entry['state']})")
    return 0


def _cmd_list(args: argparse.Namespace, root: Path) -> int:
    for state in ("active", "pending"):
        overlay = ho.load_overlay(root, state=state)
        items = overlay.get("checklist_items", [])
        rules = overlay.get("prompt_rules", [])
        print(f"== {state} (revision {overlay.get('revision', 0)}) ==")
        for it in items:
            print(f"  [item] {it.get('id')} stage={it.get('stage')} role={it.get('role')} op={it.get('op')}")
        for r in rules:
            print(f"  [rule] {r.get('id')} role={r.get('role')}")
        if not items and not rules:
            print("  (empty)")
    return 0


def _cmd_promote(args: argparse.Namespace, root: Path) -> int:
    snap = ho.snapshot(root)
    if ho.promote(root, entry_id=args.id):
        try:
            _render_smoke(root, stage=None, role="reviewer")
        except Exception as exc:  # noqa: BLE001
            ho.restore(root, snap)
            print(f"✗ render smoke test failed after promote; rolled back: {exc}", file=sys.stderr)
            return 1
        print(f"✓ promoted {args.id!r} to active")
        return 0
    print(f"✗ no pending entry with id {args.id!r}", file=sys.stderr)
    return 1


def _cmd_revert(args: argparse.Namespace, root: Path) -> int:
    if ho.revert(root, entry_id=args.id):
        print(f"✓ reverted {args.id!r}")
        return 0
    print(f"✗ no overlay entry with id {args.id!r}", file=sys.stderr)
    return 1


def _cmd_reset(args: argparse.Namespace, root: Path) -> int:
    removed = ho.reset(root, stage=args.stage)
    scope = f"stage {args.stage}" if args.stage else "all"
    print(f"✓ reset {scope}: removed {removed} entr{'y' if removed == 1 else 'ies'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="harness-evolve")
    parser.add_argument(
        "--project-root", type=Path, default=None,
        help="Project root (default: ARGUS_SKILL_PROJECT_ROOT env, else cwd).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add-item", help="Add a new checklist item.")
    p.add_argument("--stage", required=True, choices=list(CANONICAL_STAGE_ORDER))
    p.add_argument("--id", required=True)
    p.add_argument("--role", default="engineer", choices=list(ho.VALID_ROLES))
    p.add_argument("--statement", required=True)
    p.add_argument("--evidence", required=True)
    p.add_argument("--reason", required=True)
    p.set_defaults(func=_cmd_add_item)

    p = sub.add_parser("amend-item", help="Annotate (strengthen) an existing floor item.")
    p.add_argument("--id", required=True)
    p.add_argument("--role", default="engineer", choices=list(ho.VALID_ROLES))
    p.add_argument("--note", required=True)
    p.add_argument("--reason", required=True)
    p.set_defaults(func=_cmd_amend_item)

    p = sub.add_parser("add-rule", help="Add a self-authored house rule.")
    p.add_argument("--role", default="engineer", choices=list(ho.VALID_ROLES))
    p.add_argument("--id", required=True)
    p.add_argument("--text", required=True)
    p.add_argument("--reason", required=True)
    p.set_defaults(func=_cmd_add_rule)

    p = sub.add_parser("list", help="List active and pending overlay entries.")
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser("promote", help="Promote a pending entry to active.")
    p.add_argument("--id", required=True)
    p.set_defaults(func=_cmd_promote)

    p = sub.add_parser("revert", help="Remove a single overlay entry by id.")
    p.add_argument("--id", required=True)
    p.set_defaults(func=_cmd_revert)

    p = sub.add_parser("reset", help="Clear the overlay (optionally one stage).")
    p.add_argument("--stage", default=None, choices=list(CANONICAL_STAGE_ORDER))
    p.set_defaults(func=_cmd_reset)

    args = parser.parse_args()
    root = ho.resolve_project_root(args.project_root)
    try:
        return args.func(args, root)
    except ho.OverlayValidationError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
