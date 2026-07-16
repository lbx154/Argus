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
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "lbx154/argus-skill"
WORKFLOW = "binary-release.yml"
PACKAGE = "@argusevolve/argus"
VERSION_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)-beta\.g[0-9a-f]{12}$"
)
BASE_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def validate_version(value: str) -> str:
    version = value.strip()
    if not VERSION_RE.fullmatch(version):
        raise ValueError(
            "version must match <major>.<minor>.<patch>-beta.g<12-char-commit>"
        )
    return version


def project_base_version(path: Path = ROOT / "pyproject.toml") -> str:
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    version = str(payload["project"]["version"])
    if not BASE_VERSION_RE.fullmatch(version):
        raise ValueError(
            f"pyproject project.version must be a stable base X.Y.Z, found {version!r}"
        )
    return version


def version_from_commit(base_version: str, source_sha: str) -> str:
    if not BASE_VERSION_RE.fullmatch(base_version):
        raise ValueError(f"invalid base version {base_version!r}")
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError(f"source SHA must be 40 lowercase hex characters: {source_sha!r}")
    return f"{base_version}-beta.g{source_sha[:12]}"


def expected_versions(version: str) -> tuple[str, str, str]:
    return (
        f"{version}-linux-x64",
        f"{version}-win32-x64",
        version,
    )


def release_tag(version: str) -> str:
    return f"v{version}"


def expected_release_assets(version: str) -> set[str]:
    return {
        f"argus-{version}-linux-x64",
        f"argus-{version}-linux-x64.sha256",
        f"argus-{version}-win32-x64.exe",
        f"argus-{version}-win32-x64.exe.sha256",
        "THIRD_PARTY_NOTICES-linux.txt",
        "THIRD_PARTY_NOTICES-windows.txt",
        "release-metadata.json",
    }


def select_new_run(
    rows: list[dict[str, Any]], before: set[int], version: str, source_sha: str
) -> dict[str, Any] | None:
    expected_title = f"Argus {version} · {source_sha}"
    candidates = [
        row
        for row in rows
        if int(row.get("databaseId") or 0) not in before
        and str(row.get("displayTitle") or "") == expected_title
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


def release_exists(version: str) -> bool:
    result = run(
        [
            "gh",
            "release",
            "view",
            release_tag(version),
            "--repo",
            REPOSITORY,
        ],
        check=False,
        capture=True,
    )
    return result.returncode == 0


def list_dispatch_runs() -> list[dict[str, Any]]:
    raw = output(
        [
            "gh",
            "api",
            f"repos/{REPOSITORY}/actions/workflows/{WORKFLOW}/runs"
            "?event=workflow_dispatch&branch=main&per_page=20",
        ]
    )
    payload = json.loads(raw or "{}")
    raw_rows = payload.get("workflow_runs", []) if isinstance(payload, dict) else []
    return [
        {
            "databaseId": row.get("id"),
            "displayTitle": row.get("display_title"),
            "headSha": row.get("head_sha"),
            "headBranch": row.get("head_branch"),
            "event": row.get("event"),
            "status": row.get("status"),
            "createdAt": row.get("created_at"),
            "url": row.get("html_url"),
        }
        for row in raw_rows
        if isinstance(row, dict)
    ]


def trigger(version: str, source_sha: str, *, publish: bool) -> dict[str, Any]:
    before_rows = list_dispatch_runs()
    before = {int(row["databaseId"]) for row in before_rows}
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
        f"source_sha={source_sha}",
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
        selected = select_new_run(
            list_dispatch_runs(), before, version, source_sha
        )
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


def verify_github_release(version: str, head_sha: str) -> str:
    tag = release_tag(version)
    raw = output(
        [
            "gh",
            "release",
            "view",
            tag,
            "--repo",
            REPOSITORY,
            "--json",
            "tagName,isPrerelease,url,assets",
        ]
    )
    payload = json.loads(raw)
    if payload.get("tagName") != tag or payload.get("isPrerelease") is not True:
        raise RuntimeError(f"GitHub Release {tag} is missing or is not a prerelease")
    actual_assets = {
        str(asset.get("name") or "")
        for asset in payload.get("assets", [])
        if isinstance(asset, dict)
    }
    expected_assets = expected_release_assets(version)
    if actual_assets != expected_assets:
        raise RuntimeError(
            f"GitHub Release asset mismatch: missing={sorted(expected_assets - actual_assets)}, "
            f"extra={sorted(actual_assets - expected_assets)}"
        )
    remote_tag = output(
        ["git", "ls-remote", "origin", f"refs/tags/{tag}"]
    ).split()
    if not remote_tag or remote_tag[0] != head_sha:
        actual = remote_tag[0] if remote_tag else "missing"
        raise RuntimeError(f"Git tag {tag} points to {actual}, expected {head_sha}")
    url = str(payload.get("url") or "")
    print(f"GitHub prerelease verified: {tag} · {url}")
    return url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build, publish, monitor, and verify one Argus npm beta."
    )
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
        require_commands()
        head = ensure_repository_ready()
        version = validate_version(version_from_commit(project_base_version(), head))
        print(f"release version: {version} · source {head}")
        publish = not args.dry_run
        if publish:
            occupied = [item for item in expected_versions(version) if version_exists(item)]
            if release_exists(version):
                raise RuntimeError(
                    f"GitHub Release {release_tag(version)} already exists"
                )
            if occupied:
                print(
                    "recovering an incomplete release; existing npm versions will be "
                    f"verified by integrity: {', '.join(occupied)}"
                )
        selected = trigger(version, head, publish=publish)
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
            release_url = verify_github_release(version, head)
            print(
                f"published and verified: {PACKAGE}@{version} (beta) · {release_url}"
            )
        else:
            print(f"dry run verified: {version}")
        return 0
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"release_beta: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
