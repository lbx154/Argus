"""Call-bound peer messaging CLI; sender and tenant cannot be supplied here."""
from __future__ import annotations

import argparse
import json

from ..messaging.transport import request


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m argus_skill.tools.peer")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("projects")
    send = sub.add_parser("send")
    send.add_argument("--recipient", required=True)
    send.add_argument("--text", required=True)
    send.add_argument("--evidence-ref", action="append", default=[])
    send.add_argument("--request-id")
    status = sub.add_parser("status")
    status.add_argument("message_id")
    args = parser.parse_args(argv)
    payload = ({"recipient": args.recipient, "text": args.text, "evidence_refs": args.evidence_ref, "request_id": args.request_id}
               if args.command == "send" else {"message_id": args.message_id} if args.command == "status" else {})
    try:
        result = request(args.command, payload)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
