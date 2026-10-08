"""``argus cost``: inspect and release calls held by the unpriced-cost policy.

``acknowledge`` is the operator's explicit decision to approve one held call
with a budgeted liability; nothing here settles a call on its own.
Exit status 0 on success, 1 when cost control refuses, 2 for a bad argument.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def _unresolved() -> list[dict[str, Any]]:
    from ...core.cost_control import cost_control_snapshot

    return list(cost_control_snapshot().get("unresolved") or [])


def _cmd_list(args: argparse.Namespace) -> int:
    rows = _unresolved()
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    if not rows:
        print("no calls are awaiting cost reconciliation")
        return 0
    for row in rows:
        print(
            f"{row.get('call_id')}  project={row.get('project_id')}  "
            f"role={row.get('run_label') or '-'}  provider={row.get('provider') or '-'}  "
            f"model={row.get('model') or '-'}  {str(row.get('reason') or '')[:120]}"
        )
    return 0


def _cmd_acknowledge(args: argparse.Namespace) -> int:
    from ...core.cost_control import acknowledge_unpriced_call, cost_admission_reason

    project_id = args.project
    if not project_id:
        matches = {str(row.get("project_id") or "") for row in _unresolved()
                   if row.get("call_id") == args.call_id}
        matches.discard("")
        if len(matches) != 1:
            print(
                f"argus: no single unresolved call {args.call_id!r} today; "
                "check `argus cost list` or pass --project",
                file=sys.stderr,
            )
            return 1
        project_id = matches.pop()
    decision = acknowledge_unpriced_call(
        global_root=None, project_id=project_id, call_id=args.call_id,
        liability_usd=args.liability_usd, reason=args.reason,
    )
    print(
        f"acknowledged {args.call_id} (project {project_id}) with a budgeted "
        f"liability of ${float(decision['liability_usd']):.4f}"
    )
    remaining = cost_admission_reason()
    print(f"still held: {remaining}" if remaining else "new model calls are admitted again")
    return 0


def run_cost_command(args: argparse.Namespace) -> int:
    from ...core.cost_control import CostControlStateError

    try:
        if args.cost_cmd == "list":
            return _cmd_list(args)
        if args.cost_cmd == "acknowledge":
            return _cmd_acknowledge(args)
    except (LookupError, CostControlStateError) as exc:
        print(f"argus: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"argus: {exc}", file=sys.stderr)
        return 2
    print(f"argus: unknown cost command {args.cost_cmd!r}", file=sys.stderr)
    return 2


def add_cost_subcommand(subparsers: argparse._SubParsersAction) -> None:
    cost_parser = subparsers.add_parser(
        "cost", help="List or acknowledge calls held by the unpriced-cost policy",
    )
    commands = cost_parser.add_subparsers(dest="cost_cmd", required=True)
    list_parser = commands.add_parser("list", help="Calls awaiting cost reconciliation today")
    list_parser.add_argument("--json", action="store_true", help="print the rows as JSON")
    ack = commands.add_parser(
        "acknowledge",
        help="Approve one held call with a budgeted liability (an explicit operator decision)",
    )
    ack.add_argument("call_id", metavar="CALL_ID")
    ack.add_argument("--liability-usd", type=float, required=True,
                     help="USD to book against today's budget for this call")
    ack.add_argument("--reason", required=True, help="why this call is approved")
    ack.add_argument("--project", default="",
                     help="project id (inferred when the call id is unique)")


__all__ = ["add_cost_subcommand", "run_cost_command"]
