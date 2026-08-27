"""Command-line interface for the host resource ledger."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .ledger import DEFAULT_TTL_SECONDS, ResourceLedger, owner_identity


def parse_duration(value: str) -> int:
    token = str(value).strip().lower()
    factors = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    suffix = token[-1:] if token[-1:] in factors else "s"
    number = token[:-1] if token[-1:] in factors else token
    try:
        seconds = float(number) * factors[suffix]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid duration: {value!r}") from exc
    if seconds < 0:
        raise argparse.ArgumentTypeError("duration must be non-negative")
    return int(seconds)


def _add_demand(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--accelerator", choices=["cuda", "rocm", "any", "none"], default="any")
    parser.add_argument("--device-count", "--gpu-count", type=int, default=None)
    parser.add_argument("--mem-mib", "--gpu-mem-mib", type=int, default=0)
    parser.add_argument("--expected-duration", type=parse_duration, default=0)
    parser.add_argument("--checkpointable", action="store_true")
    parser.add_argument("--intent", default="")


def _demand(args: argparse.Namespace) -> dict[str, Any]:
    count = args.device_count
    if count is None:
        count = 0 if args.accelerator == "none" else 1
    return {
        "accelerator": args.accelerator,
        "device_count": count,
        "mem_mib_estimate": args.mem_mib,
        "expected_duration_seconds": args.expected_duration,
        "checkpointable": args.checkpointable,
        "intent": args.intent,
    }


def _owner(args: argparse.Namespace, *, pid: int | None = None) -> dict[str, Any]:
    return owner_identity(
        project_root=Path(getattr(args, "project_root", None) or Path.cwd()),
        task_id=str(getattr(args, "task_id", None) or "resource-ledger-cli"),
        pid=pid,
    )


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def cmd_acquire(args: argparse.Namespace) -> int:
    result = ResourceLedger().acquire(
        _demand(args),
        owner=_owner(args, pid=os.getppid()),
        ttl_seconds=args.ttl,
        request_id=args.request_id,
    )
    _print_json(result)
    return 0


def cmd_renew(args: argparse.Namespace) -> int:
    record = ResourceLedger().renew(args.grant_id, ttl_seconds=args.ttl)
    _print_json({"state": "renewed", "grant": record} if record else {"state": "missing"})
    return 0 if record else 1


def cmd_release(args: argparse.Namespace) -> int:
    released = ResourceLedger().release(args.record_id)
    _print_json({"state": "released" if released else "missing", "id": args.record_id})
    return 0 if released else 1


def cmd_yield_request(args: argparse.Namespace) -> int:
    request = ResourceLedger().yield_request(
        args.grant_id,
        args.reason,
        requester=_owner(args),
    )
    _print_json({"state": "recorded", "request": request})
    return 0


def cmd_yield_response(args: argparse.Namespace) -> int:
    request = ResourceLedger().respond_yield(
        args.grant_id,
        args.request_id,
        args.reason,
        decision=args.decision,
    )
    _print_json({"state": "recorded", "request": request})
    return 0


def _human_status(status: dict[str, Any]) -> None:
    probe = status.get("probe") or {}
    print(
        f"Ledger: {status['ledger_root']}  scope={status['scope']}  "
        f"enforcement={probe.get('enforcement', 'unknown')}"
    )
    if status.get("scope_detail"):
        print(f"Scope: {status['scope_detail']}")
    for accelerator in probe.get("accelerators", []):
        detail = f" — {accelerator.get('detail')}" if accelerator.get("detail") else ""
        print(
            f"{str(accelerator.get('kind', '?')).upper()}: "
            f"{accelerator.get('status', 'unknown')} "
            f"({len(accelerator.get('devices') or [])} devices){detail}"
        )
    print("\nGRANTS")
    if not status["grants"]:
        print("  (none)")
    for grant in status["grants"]:
        devices = ",".join(grant.get("grant", {}).get("device_identities", [])) or "advisory/none"
        demand = grant.get("demand", {})
        owner = grant.get("owner", {})
        print(
            f"  {grant.get('id')}  {devices}  {owner.get('unix_user')} "
            f"task={owner.get('task_id')}  intent={demand.get('intent', '')}"
        )
        for request in grant.get("yield_requests", []):
            print(f"    yield {request.get('id')}: {request.get('reason')} response={request.get('response')}")
    print("\nQUEUE")
    if not status["queue"]:
        print("  (none)")
    for position, request in enumerate(status["queue"], 1):
        owner = request.get("owner", {})
        print(
            f"  {position}. {request.get('id')}  task={owner.get('task_id')}  "
            f"intent={request.get('demand', {}).get('intent', '')}"
        )


def cmd_status(args: argparse.Namespace) -> int:
    status = ResourceLedger().status()
    if args.json:
        _print_json(status)
    else:
        _human_status(status)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        raise SystemExit("resource-ledger run requires a command after --")
    ledger = ResourceLedger()
    demand = _demand(args)
    owner = _owner(args, pid=os.getpid())
    started = time.monotonic()
    request_id: str | None = None
    result: dict[str, Any] | None = None
    delay = 1.0
    while True:
        result = ledger.acquire(
            demand,
            owner=owner,
            ttl_seconds=args.ttl,
            request_id=request_id,
        )
        request_id = str(result["id"])
        if result["state"] == "granted":
            break
        if result["state"] == "unsatisfiable":
            _print_json({"state": "unsatisfiable", "queue": result})
            return 2
        if args.max_wait and time.monotonic() - started >= args.max_wait:
            ledger.release(request_id)
            _print_json({"state": "max_wait_exceeded", "queue": result})
            return 2
        time.sleep(min(delay, float(result.get("poll_after_seconds") or delay)))
        delay = min(delay * 1.5, 15.0)
    admitted = ledger.admit(request_id, demand=demand, owner=owner)
    if admitted is None:
        ledger.release(request_id)
        _print_json({"state": "grant_lost_before_launch", "grant_id": request_id})
        return 2
    env = os.environ.copy()
    env.update({str(key): str(value) for key, value in admitted["grant"]["env"].items()})
    if admitted.get("warning"):
        print(f"resource-ledger warning: {admitted['warning']}", file=sys.stderr)
    proc: subprocess.Popen[Any] | None = None
    try:
        proc = subprocess.Popen(command, env=env, start_new_session=False)
        renew_every = max(1.0, float(args.ttl) / 3.0)
        while True:
            try:
                return proc.wait(timeout=renew_every)
            except subprocess.TimeoutExpired:
                if ledger.renew(request_id, ttl_seconds=args.ttl) is None:
                    print("resource-ledger warning: grant renewal failed", file=sys.stderr)
    finally:
        ledger.release(request_id)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m argus_skill.tools.resource_ledger")
    sub = parser.add_subparsers(dest="command_name", required=True)
    acquire = sub.add_parser("acquire")
    _add_demand(acquire)
    acquire.add_argument("--ttl", type=float, default=DEFAULT_TTL_SECONDS)
    acquire.add_argument("--request-id")
    acquire.add_argument("--project-root")
    acquire.add_argument("--task-id")
    acquire.set_defaults(handler=cmd_acquire)

    renew = sub.add_parser("renew")
    renew.add_argument("grant_id")
    renew.add_argument("--ttl", type=float, default=None)
    renew.set_defaults(handler=cmd_renew)

    release = sub.add_parser("release")
    release.add_argument("record_id")
    release.set_defaults(handler=cmd_release)

    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    status.set_defaults(handler=cmd_status)

    request = sub.add_parser("yield-request")
    request.add_argument("grant_id")
    request.add_argument("reason")
    request.add_argument("--project-root")
    request.add_argument("--task-id")
    request.set_defaults(handler=cmd_yield_request)

    response = sub.add_parser("yield-response")
    response.add_argument("grant_id")
    response.add_argument("request_id")
    response.add_argument("--decision", choices=["decline", "yield"], default="decline")
    response.add_argument("--reason", required=True)
    response.set_defaults(handler=cmd_yield_response)

    run = sub.add_parser("run")
    _add_demand(run)
    run.add_argument("--ttl", type=float, default=DEFAULT_TTL_SECONDS)
    run.add_argument("--max-wait", type=parse_duration, default=3600)
    run.add_argument("--project-root")
    run.add_argument("--task-id")
    run.add_argument("command", nargs=argparse.REMAINDER)
    run.set_defaults(handler=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (KeyError, ValueError, OSError) as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
