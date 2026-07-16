#!/usr/bin/env python3
"""Trigger and verify one manual Argus npm beta release."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "lbx154/argus-skill"
WORKFLOW = "binary-release.yml"
PACKAGE = "@argusevolve/argus"
VERSION_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)-beta\.(0|[1-9]\d*)$"
)


def validate_version(value: str) -> str:
    version = value.strip()
    if not VERSION_RE.fullmatch(version):
        raise ValueError("version must match <major>.<minor>.<patch>-beta.<number>")
    return version


def expected_versions(version: str) -> tuple[str, str, str]:
    return (
        f"{version}-linux-x64",
        f"{version}-win32-x64",
        version,
    )


def select_new_run(
    rows: list[dict[str, Any]], before: set[int], head_sha: str
) -> dict[str, Any] | None:
    candidates = [
        row
        for row in rows
        if int(row.get("databaseId") or 0) not in before
        and str(row.get("headSha") or "") == head_sha
        and str(row.get("event") or "") == "workflow_dispatch"
        and str(row.get("headBranch") or "") == "main"
    ]
    return max(candidates, key=lambda row: int(row["databaseId"])) if candidates else None


def run(
    argv: list[str],
    *,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=ROOT,
        check=check,
        text=True,
        capture_output=capture,
    )


def output(argv: list[str]) -> str:
    return run(argv, capture=True).stdout.strip()


def require_commands() -> None:
    missing = [name for name in ("git", "gh", "npm") if shutil.which(name) is None]
    if missing:
        raise RuntimeError(f"missing required commands: {', '.join(missing)}")


def ensure_repository_ready() -> str:
    branch = output(["git", "branch", "--show-current"])
    if branch != "main":
        raise RuntimeError(f"release must run from main, found {branch or 'detached HEAD'}")
    dirty = output(["git", "status", "--porcelain", "--untracked-files=no"])
    if dirty:
        raise RuntimeError(
            "tracked files have uncommitted changes; commit or stash them before release"
        )
    run(["git", "fetch", "origin", "main"])
    head = output(["git", "rev-parse", "HEAD"])
    remote = output(["git", "rev-parse", "origin/main"])
    if head != remote:
        raise RuntimeError(
            f"local main ({head[:12]}) does not match origin/main ({remote[:12]})"
        )
    run(["gh", "auth", "status"])
    return head


def version_exists(version: str) -> bool:
    result = run(
        ["npm", "view", f"{PACKAGE}@{version}", "version"],
        check=False,
        capture=True,
    )
    return result.returncode == 0 and result.stdout.strip() == version


def list_dispatch_runs() -> list[dict[str, Any]]:
    raw = output(
        [
            "gh",
            "run",
            "list",
            "--repo",
            REPOSITORY,
            "--workflow",
            WORKFLOW,
            "--limit",
            "20",
            "--json",
            "databaseId,headSha,headBranch,event,status,createdAt,url",
        ]
    )
    rows = json.loads(raw or "[]")
    return rows if isinstance(rows, list) else []


def trigger(version: str, *, publish: bool) -> dict[str, Any]:
    before_rows = list_dispatch_runs()
    before = {int(row["databaseId"]) for row in before_rows}
    head = output(["git", "rev-parse", "HEAD"])
    command = [
        "gh",
        "workflow",
        "run",
        WORKFLOW,
        "--repo",
        REPOSITORY,
        "--ref",
        "main",
        "-f",
        f"version={version}",
        "-f",
        f"publish={'true' if publish else 'false'}",
    ]
    if publish:
        command.extend(
            ["-f", f"confirm=PUBLISH @argusevolve/argus {version}"]
        )
    run(command)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        selected = select_new_run(list_dispatch_runs(), before, head)
        if selected is not None:
            return selected
        time.sleep(2)
    raise RuntimeError("GitHub accepted the dispatch but no matching run appeared")


def wait_for_registry(version: str, timeout: int = 120) -> None:
    expected = expected_versions(version)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(version_exists(item) for item in expected):
            beta = output(["npm", "view", PACKAGE, "dist-tags.beta"])
            if beta == version:
                return
        time.sleep(5)
    raise RuntimeError(
        f"workflow succeeded but npm did not expose {expected} with beta={version}"
    )


def verify_install(version: str) -> None:
    with tempfile.TemporaryDirectory(prefix="argus-release-install-") as prefix:
        run(["npm", "install", "--prefix", prefix, f"{PACKAGE}@beta"])
        executable = Path(prefix) / "node_modules" / ".bin" / "argus-skill"
        result = run([str(executable), "--version"], capture=True)
        rendered = result.stdout.strip()
        if version not in rendered:
            raise RuntimeError(
                f"installed command reported {rendered!r}, expected {version}"
            )
        print(f"install smoke passed: {rendered}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build, publish, monitor, and verify one Argus npm beta."
    )
    parser.add_argument("version", help="immutable version such as 0.1.1-beta.3")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="build and audit Linux/Windows artifacts without publishing",
    )
    parser.add_argument(
        "--no-watch",
        action="store_true",
        help="return after dispatching instead of waiting for completion",
    )
    args = parser.parse_args(argv)

    try:
        version = validate_version(args.version)
        require_commands()
        head = ensure_repository_ready()
        publish = not args.dry_run
        if publish:
            occupied = [item for item in expected_versions(version) if version_exists(item)]
            if occupied:
                raise RuntimeError(
                    f"npm versions are immutable and already exist: {', '.join(occupied)}"
                )
        selected = trigger(version, publish=publish)
        run_id = int(selected["databaseId"])
        url = str(selected.get("url") or "")
        print(f"release run: {run_id} · {head[:12]} · {url}")
        if args.no_watch:
            return 0
        run(
            [
                "gh",
                "run",
                "watch",
                str(run_id),
                "--repo",
                REPOSITORY,
                "--exit-status",
            ]
        )
        if publish:
            wait_for_registry(version)
            verify_install(version)
            print(f"published and verified: {PACKAGE}@{version} (beta)")
        else:
            print(f"dry run verified: {version}")
        return 0
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"release_beta: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
