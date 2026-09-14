"""Call-bound capsule tools; project and role are supplied only by Argus."""
from __future__ import annotations

import argparse
import json

from ..core.json_codec import loads_finite_json
from ..life.experience_tools import request


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m argus.tools.experience")
    sub = parser.add_subparsers(dest="command", required=True)
    search = sub.add_parser("search", help="find advisory prior experiences")
    search.add_argument("query")
    get = sub.add_parser("get", help="inspect current revision and evidence")
    get.add_argument("experience_id")
    for command in ("revise", "retract"):
        mutation = sub.add_parser(command)
        mutation.add_argument("experience_id")
        mutation.add_argument("--expected-revision", type=int, required=True)
        mutation.add_argument("--evidence-ref", action="append", required=True)
        mutation.add_argument("--reason", required=True)
        if command == "revise":
            mutation.add_argument("--changes", required=True, help="JSON object of corrected content fields")
    args = parser.parse_args(argv)
    try:
        if args.command == "search":
            payload = {"query": args.query}
        elif args.command == "get":
            payload = {"experience_id": args.experience_id}
        else:
            payload = {"experience_id": args.experience_id,
                       "expected_revision": args.expected_revision,
                       "evidence_refs": args.evidence_ref, "reason": args.reason}
            if args.command == "revise":
                payload["changes"] = loads_finite_json(args.changes)
        result = request(args.command, payload)
    except (OSError, TypeError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
