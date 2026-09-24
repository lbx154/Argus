"""Historical 50-PR experiment driver, separate from local gate policy."""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import fcntl
import json
import os
import random
import shutil
import signal
import subprocess
import time
import urllib.request
from pathlib import Path

import jsonschema

if __package__:
    from .runtime import (
        ASSETS,
        STOP,
        active_token,
        asset_source,
        command,
        copy_evidence,
        digest,
        invoke,
        network,
        now,
        remove_container,
        safe_file,
        validate_skill_session,
        worker_args,
        write_json,
    )
    from .validation import validate_report
else:
    from runtime import (
        ASSETS,
        STOP,
        active_token,
        asset_source,
        command,
        copy_evidence,
        digest,
        invoke,
        network,
        now,
        remove_container,
        safe_file,
        validate_skill_session,
        worker_args,
        write_json,
    )
    from validation import validate_report

STUDY = "pr-regression-50-20260916"
IMAGE = "argus-pr-regression:20260916-s391859ca"
SOURCE = Path(__file__).resolve().parents[4] if __package__ else Path.cwd()
DEFAULT_ROOT = Path.home() / "argus-experiments" / STUDY


def population() -> list[dict]:
    rows, page = [], 1
    while True:
        url = (
            "https://api.github.com/repos/lbx154/Argus/pulls"
            f"?state=all&sort=created&direction=asc&per_page=100&page={page}"
        )
        request = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json", "User-Agent": "Copilot-PR-Regression-Study",
        })
        with urllib.request.urlopen(request, timeout=60) as response:
            batch = json.load(response)
            has_next = 'rel="next"' in response.headers.get("Link", "")
        for pr in batch:
            rows.append({
                "pr_number": pr["number"], "url": pr["html_url"], "title": pr["title"],
                "body": pr["body"] or "", "created_at": pr["created_at"],
                "merged_at": pr["merged_at"], "state": pr["state"],
                "target_branch": pr["base"]["ref"], "merge_sha": pr["merge_commit_sha"],
                "head_sha": pr["head"]["sha"],
            })
        if not has_next:
            return rows
        page += 1


def sample(rows: list[dict], *, size: int, seed: int) -> list[dict]:
    if size != 50 or len(rows) < size:
        raise ValueError(f"Need at least 50 eligible PRs, found {len(rows)}")
    ordered = sorted(rows, key=lambda row: (row["merged_at"], row["pr_number"]))
    generator, selected = random.Random(seed), []
    for bucket in range(5):
        pool = ordered[bucket * len(rows) // 5:(bucket + 1) * len(rows) // 5]
        selected.extend(dict(row, temporal_stratum=bucket + 1) for row in generator.sample(pool, 10))
    return sorted(selected, key=lambda row: (row["merged_at"], row["pr_number"]))


def freeze_runner(root: Path) -> None:
    runner = root / "runner"
    runner.mkdir()
    for path in ASSETS.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        if path.suffix in {".py", ".json", ".txt", ".md"} or path.name == "Dockerfile":
            target = runner / path.relative_to(ASSETS)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    shutil.copy2(asset_source("owned_process.py"), runner / "owned_process.py")
    shutil.copy2(Path(__file__), runner / "run.py")
    shutil.copy2(ASSETS / "SKILL.md", root / "SKILL.md")


def prepare(root: Path, *, source: Path, seed: int) -> None:
    root.mkdir(parents=True, exist_ok=False)
    root.chmod(0o700)
    mirror = root / "repository.git"
    command(["git", "clone", "--quiet", "--bare", "--no-hardlinks", str(source), str(mirror)])
    git = ["git", "--git-dir", str(mirror)]
    command([*git, "remote", "set-url", "origin", "https://github.com/lbx154/Argus.git"])
    command([*git, "fetch", "--quiet", "origin", "+refs/heads/main:refs/heads/study-main"])
    all_rows = population()
    write_json(root / "population.json", {"retrieved_at": now(), "pull_requests": all_rows})
    eligible, excluded = [], []
    for row in all_rows:
        reason = ""
        if not row["merged_at"] or row["target_branch"] != "main":
            reason = "not_merged_into_main"
        else:
            result = subprocess.run(
                [*git, "rev-list", "--parents", "-n", "1", row["merge_sha"]],
                text=True, capture_output=True,
            )
            parents = result.stdout.strip().split()
            if result.returncode or len(parents) != 3 or parents[2] != row["head_sha"]:
                reason = "no_verified_two_parent_merge"
            else:
                ancestry = subprocess.run(
                    [*git, "merge-base", "--is-ancestor", row["merge_sha"], "refs/heads/study-main"],
                    capture_output=True,
                )
                if ancestry.returncode == 1:
                    reason = "merge_not_reachable_from_main"
                elif ancestry.returncode:
                    raise RuntimeError("Could not establish merge ancestry")
        if reason:
            excluded.append({"pr_number": row["pr_number"], "reason": reason})
        else:
            eligible.append(dict(row, base_sha=parents[1], candidate_sha=row["merge_sha"]))
    selected = sample(eligible, size=50, seed=seed)
    write_json(root / "selection.json", {
        "seed": seed, "rule": "10 uniform random PRs per equal-count temporal quintile",
        "population_count": len(all_rows), "eligible_count": len(eligible),
        "eligibility": "merged into main; verified two-parent merge matching PR head",
        "excluded": excluded, "selected": selected,
        "limits": [
            "Unmerged and unverifiable squash/rebase PRs are outside this cohort.",
            "Current PR title/body are context, not historical frozen oracles.",
            "No independent regression ground-truth labels exist for this pilot.",
        ],
    })
    freeze_runner(root)
    runner = root / "runner"
    for row in selected:
        directory = root / "inputs" / f"pr-{row['pr_number']}"
        directory.mkdir(parents=True)
        paths = subprocess.check_output(
            [*git, "diff", "--name-only", "-z", row["base_sha"], row["candidate_sha"]],
        ).decode().split("\0")
        (directory / "change.patch").write_bytes(subprocess.check_output(
            [*git, "diff", "--no-ext-diff", "--binary", row["base_sha"], row["candidate_sha"]],
        ))
        write_json(directory / "changed-paths.json", [path for path in paths if path])
        write_json(directory / "input.json", {
            key: row[key] for key in ("pr_number", "url", "title", "body", "base_sha", "candidate_sha")
        } | {"comparison": "integration_first_parent_to_merge",
             "metadata_note": "PR text is a current snapshot, not an acceptance oracle."})
    original_binary = Path(shutil.which("copilot") or "/missing/copilot")
    binary = root / "bin/copilot"
    binary.parent.mkdir()
    shutil.copy2(original_binary, binary)
    write_json(root / "manifest.json", {
        "schema": "copilot-only-regression-study/v1", "study": STUDY,
        "created_at": now(), "cohort_size": 50, "maximum_parallel_copilot_sessions": 5,
        "source_head": command(["git", "-C", str(source), "rev-parse", "HEAD"]),
        "main_head": command([*git, "rev-parse", "refs/heads/study-main"]),
        "skill_sha256": digest(root / "SKILL.md"), "copilot_binary": str(binary),
        "copilot_sha256": digest(binary), "copilot_source_binary": str(original_binary),
        "copilot_version": command([str(binary), "--version"]),
        "runner_files": {p.relative_to(runner).as_posix(): digest(p) for p in runner.rglob("*") if p.is_file()},
        "image_tag": IMAGE, "image_id": command(["docker", "image", "inspect", "--format", "{{.Id}}", IMAGE]),
        "cpu_per_worker": 2, "memory_per_worker": "4g", "pids_per_worker": 256,
        "source_and_host_state_mounted": False, "live_argus_runs_allowed": False,
        "nested_agents_allowed": False,
    })
    print(json.dumps({"root": str(root), "population": len(all_rows), "eligible": len(eligible), "selected": 50}))


def analyze_one(root: Path, row: dict, *, net: str, model: str, token: str, timeout: int) -> dict:
    number = row["pr_number"]
    work = root / "work" / f"pr-{number}"
    result_dir = root / "results" / f"pr-{number}"
    result_file = result_dir / "runner-result.json"
    if result_file.is_file():
        previous = json.loads(result_file.read_text())
        if previous["status"] == "running":
            raise RuntimeError(f"Interrupted PR {number} requires explicit recovery")
        return previous
    if STOP.is_set():
        result = {"pr_number": number, "status": "cancelled", "completed_at": now()}
        write_json(result_file, result)
        return result
    if work.exists():
        raise FileExistsError(f"Unfinished workspace requires explicit recovery: {work}")
    shutil.copytree(root / "inputs" / f"pr-{number}", work)
    result_dir.mkdir(parents=True)
    metadata = json.loads((work / "input.json").read_text())
    name = f"{STUDY}-pr-{number}"
    result = {"pr_number": number, "started_at": now(), "status": "running"}
    write_json(result_file, result)
    started = time.monotonic()
    try:
        args = worker_args(root, work, name=name, network="none", study_id=STUDY)
        command(args + [
            "--mount", f"type=bind,src={root / 'repository.git'},dst=/mirror,readonly",
            IMAGE, "python", "/runner/entry.py", "stage",
        ], timeout=240)
        code = invoke(
            root, work, name=name, net=net, mode="analyze", model=model, token=token,
            log_path=result_dir / "copilot.jsonl", timeout=timeout, study_id=STUDY, image=IMAGE,
        )
        result["copilot_exit_code"] = code
        if code:
            result["status"] = "timeout" if code == 124 else "cancelled" if code == 130 else "invocation_failed"
        else:
            result["observed_tools"] = validate_skill_session(result_dir / "copilot.jsonl")
            report = validate_report(work, json.loads((root / "runner/report.schema.json").read_text()), metadata)
            command(worker_args(root, work, name=name, network="none", study_id=STUDY) + [
                IMAGE, "python", "/runner/entry.py", "audit", "--base-sha", metadata["base_sha"],
                "--candidate-sha", metadata["candidate_sha"],
            ], timeout=120)
            result.update(status="completed", verdict=report["verdict"],
                          analysis_mode=report["analysis_mode"], finding_count=len(report["findings"]))
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError, jsonschema.ValidationError) as exc:
        result.update(status="invalid_or_failed", error=f"{type(exc).__name__}: {exc}"[:2000])
    finally:
        remove_container(name, study_id=STUDY)
        for filename in ("report.json", "REPORT.md", "usage.json"):
            if (work / filename).exists() or (work / filename).is_symlink():
                try:
                    path = safe_file(work, filename)
                except ValueError as exc:
                    result.update(status="invalid_or_failed", error=str(exc))
                    continue
                shutil.copy2(path, result_dir / filename)
        if (work / "evidence").is_dir() and not (work / "evidence").is_symlink():
            copy_evidence(work / "evidence", result_dir / "evidence")
        for label in ("base", "candidate"):
            probes = work / label / "tests/_regression_probe"
            if probes.is_dir() and not any(p.is_symlink() for p in (work / label, work / label / "tests", probes)):
                copy_evidence(probes, result_dir / f"{label}-probes")
        result.update(completed_at=now(), duration_seconds=round(time.monotonic() - started, 3))
        write_json(result_file, result)
        shutil.rmtree(work)
    return result


def aggregate(root: Path, *, state: str) -> dict:
    rows = [json.loads(p.read_text()) for p in sorted((root / "results").glob("*/runner-result.json"))]
    completed = [row for row in rows if row["status"] == "completed"]
    summary = {
        "state": state, "updated_at": now(), "selected": 50, "attempted": len(rows),
        "completed_reports": len(completed),
        "status_counts": dict(collections.Counter(row["status"] for row in rows)),
        "verdict_counts": dict(collections.Counter(row["verdict"] for row in completed)),
        "analysis_modes": dict(collections.Counter(row["analysis_mode"] for row in completed)),
        "limitations": [
            "No-regression-found is scoped evidence, not proof of safety.",
            "Copilot-produced findings require human adjudication; no accuracy estimate.",
            "No live Argus or additional model execution was authorized.",
        ],
    }
    write_json(root / "summary.json", summary)
    return summary


def execute(root: Path, *, smoke: bool, model: str, workers: int, timeout: int) -> None:
    if not 1 <= workers <= 5:
        raise ValueError("At most five Copilot sessions may run concurrently")
    manifest = json.loads((root / "manifest.json").read_text())
    if digest(root / "SKILL.md") != manifest["skill_sha256"]:
        raise ValueError("Frozen Skill changed")
    if digest(Path(manifest["copilot_binary"])) != manifest["copilot_sha256"]:
        raise ValueError("Copilot executable changed since preparation")
    for filename, expected in manifest["runner_files"].items():
        if digest(root / "runner" / filename) != expected:
            raise ValueError(f"Frozen runner changed: {filename}")
    if command(["docker", "image", "inspect", "--format", "{{.Id}}", IMAGE]) != manifest["image_id"]:
        raise ValueError("Worker image changed since preparation")
    with (root / "runner.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        token = active_token()
        write_json(root / "execution.json", {
            "started_at": now(), "model": model, "effort": "high", "workers": workers,
            "timeout_seconds_per_pr": timeout, "smoke": smoke, "pid": os.getpid(),
        })
        with network(root, study_id=STUDY, image=IMAGE) as net:
            if smoke:
                stamp = str(time.time_ns())
                work = root / "smokes" / stamp
                work.mkdir(parents=True, exist_ok=False)
                log = root / "smokes" / f"{stamp}.jsonl"
                code = invoke(
                    root, work, name=f"{STUDY}-smoke", net=net, mode="smoke", model=model,
                    token=token, log_path=log, timeout=180, study_id=STUDY, image=IMAGE,
                )
                if code or "ISOLATED_SKILL_READY" not in log.read_text():
                    raise RuntimeError(f"Isolated Copilot smoke failed (exit {code})")
                validate_skill_session(log)
                write_json(root / "smoke-result.json", {"status": "completed", "model": model, "log": str(log)})
                return
            selected = json.loads((root / "selection.json").read_text())["selected"]
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [
                    pool.submit(analyze_one, root, row, net=net, model=model, token=token, timeout=timeout)
                    for row in selected
                ]
                for future in concurrent.futures.as_completed(futures):
                    print(json.dumps(future.result()), flush=True)
                    aggregate(root, state="running")
        print(json.dumps(aggregate(root, state="cancelled" if STOP.is_set() else "finished"), indent=2))
        if not STOP.is_set():
            command(["docker", "image", "rm", IMAGE])


def main() -> None:
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "smoke", "run", "summary"))
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--seed", type=int, default=20260916)
    parser.add_argument("--model", default="gpt-6-astra")
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.root, source=args.source, seed=args.seed)
    elif args.action == "summary":
        print(json.dumps(aggregate(args.root, state="snapshot"), indent=2))
    else:
        execute(args.root, smoke=args.action == "smoke", model=args.model, workers=args.workers, timeout=args.timeout)


if __name__ == "__main__":
    main()
