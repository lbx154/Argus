"""Argus tenant compute CLI, available inside the isolated trial runtime.

python -m argus_skill.trial.compute_client submit --gpus 1 -- python train.py
python -m argus_skill.trial.compute_client wait JOB_ID
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

from .client import profile_path

BASE_URL = "http://127.0.0.1:18766/compute"


def request(method, path, payload=None):
    profile = json.loads(profile_path().read_text(encoding="utf-8"))
    key = profile["api_key"]
    if not isinstance(key, str) or not key or "\r" in key or "\n" in key:
        raise ValueError("Invalid trial profile api_key")
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(BASE_URL + path, data=data, method=method,
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    # A tenant's proxy environment must not forward its invitation credential.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read(65536).decode("utf-8", errors="replace")
        raise ValueError(f"Compute HTTP {exc.code}: {detail}") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    submit = commands.add_parser("submit")
    submit.add_argument("--cpus", type=int, default=8)
    submit.add_argument("--memory-gib", type=int, default=32)
    submit.add_argument("--gpus", type=int, default=1)
    submit.add_argument("--timeout", type=int, default=3600)
    submit.add_argument("--workdir", default=".")
    submit.add_argument("--name", default="")
    submit.add_argument("--shm-gib", type=int, default=1)
    submit.add_argument("command", nargs=argparse.REMAINDER)
    commands.add_parser("status")
    commands.add_parser("jobs")
    for action in ("logs", "cancel", "wait"):
        sub = commands.add_parser(action)
        sub.add_argument("job_id", type=int)
    args = parser.parse_args(argv)
    try:
        if args.action == "submit":
            command = args.command[1:] if args.command[:1] == ["--"] else args.command
            # Do not import the server (and FastAPI) into the runtime CLI.
            if (not 1 <= len(command) <= 128 or not command[0] or "\0" in "".join(command)
                    or sum(len(arg.encode("utf-8")) for arg in command) > 32768):
                parser.error("command must contain 1..128 arguments, at most 32768 UTF-8 bytes")
            result = request("POST", "/jobs", {
                "command": command, "cpus": args.cpus, "memory_gib": args.memory_gib,
                "gpus": args.gpus, "timeout_seconds": args.timeout,
                "workdir": args.workdir, "name": args.name, "shm_gib": args.shm_gib,
            })
        elif args.action in ("status", "jobs"):
            result = request("GET", "/" + args.action)
        elif args.action == "logs":
            result = request("GET", f"/jobs/{args.job_id}/logs")
            print(result["text"], end="")
            if result.get("truncated"):
                print("\n[logs truncated to the bounded tail]", file=sys.stderr)
            return 0
        elif args.action == "cancel":
            result = request("POST", f"/jobs/{args.job_id}/cancel")
        else:
            while True:
                result = request("GET", f"/jobs/{args.job_id}")
                status = result["job"]["status"]
                if status in ("succeeded", "failed", "cancelled", "timed_out", "blocked"):
                    print(json.dumps(result["job"], indent=2))
                    return 0 if status == "succeeded" else 1
                time.sleep(2)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError, urllib.error.URLError) as exc:
        print(f"argus compute: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
