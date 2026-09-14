"""Agent-facing advisor client. Project, role, model, and budget are host-bound."""
from __future__ import annotations

import argparse
import json

from ..advisor.transport import request


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m argus.tools.advisor")
    sub = parser.add_subparsers(dest="command", required=True)
    consult = sub.add_parser("consult")
    consult.add_argument("--question", required=True)
    consult.add_argument("--evidence-ref", action="append", default=[])
    consult.add_argument("--request-id")
    receipt = sub.add_parser("receipt")
    receipt.add_argument("consultation_id")
    sub.add_parser("mcp")
    args = parser.parse_args(argv)
    if args.command == "mcp":
        from ..advisor.mcp import run_server

        run_server()
        return 0
    try:
        payload = ({"consultation_id": args.consultation_id} if args.command == "receipt" else
                   {"question": args.question, "evidence_refs": args.evidence_ref, "request_id": args.request_id})
        result = request(args.command, payload)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
