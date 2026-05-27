"""Launcher for TB v2 fullbench comparison conditions.

This module builds the exact `harbor run` command for the supported
comparison conditions, then hands it off to the generic detached
experiment launcher.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmarks.experiment_launcher import LaunchSpec, launch_detached

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parents[1]
TB2_DATASET_ID = "terminal-bench@2.0"
TB2_DATASET_COMMIT = "69671fbaac6d67a7ef0dfec016cc38a64ef7a77c"
DEFAULT_CONCURRENCY = 8
DEFAULT_REASONING_EFFORT = "high"
DEFAULT_REVIEWER_EFFORT = "high"
DEFAULT_SCIENTIST_MODEL = "gpt-5.4"
DEFAULT_REVIEWER_MODEL = "gpt-5.4"
DEFAULT_ENGINEER_MODEL = "openai/gpt-5.4-mini"
DEFAULT_BARE_GPT54 = "openai/gpt-5.4"
DEFAULT_BARE_GPT54_MINI = "openai/gpt-5.4-mini"
SUPPORTED_CONDITIONS = (
    "argus-v12-redux",
    "argus-v12-true",
    "bare-gpt54",
    "bare-gpt54-mini",
)
_TB2_PREFLIGHT_MODE_ENV = "ARGUS_SKILL_TB2_PREFLIGHT_MODE"
_TB2_PREFLIGHT_CACHE_ROOT_ENV = "ARGUS_SKILL_TB2_PREFLIGHT_CACHE_ROOT"
_TB2_PREFLIGHT_RATE_LIMIT_MARKERS = (
    "rate limit",
    "toomanyrequests",
    "too many requests",
    "unauthenticated pull rate limit",
)


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in value).strip(".-_")


def _normalize_openai_base_url(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        return normalized
    return normalized.rstrip("/") + "/"


def _shared_env(exp_dir: Path) -> dict[str, str]:
    env: dict[str, str] = {
        "OPENAI_BASE_URL": _normalize_openai_base_url(
            os.environ.get("OPENAI_BASE_URL", "https://ai4m6.openai.azure.com/openai/v1/")
        ),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    }

    explicit_auth_path = os.environ.get("CODEX_AUTH_JSON_PATH")
    if explicit_auth_path:
        env["CODEX_AUTH_JSON_PATH"] = explicit_auth_path
    elif os.environ.get("CODEX_FORCE_AUTH_JSON"):
        env["CODEX_FORCE_AUTH_JSON"] = os.environ["CODEX_FORCE_AUTH_JSON"]
    else:
        default_auth = Path.home() / ".codex" / "auth.json"
        if default_auth.is_file():
            env["CODEX_FORCE_AUTH_JSON"] = "1"
        else:
            api_key = os.environ.get("OPENAI_API_KEY")
            if api_key:
                env["OPENAI_API_KEY"] = api_key

    return env


def _preflight_mode() -> str:
    # Default to the conservative paper-grade behavior: fail closed when the
    # launcher cannot stage or inspect task images. Deliberate smoke/debug
    # runs can still opt into the permissive mode explicitly.
    return os.environ.get(_TB2_PREFLIGHT_MODE_ENV, "pull").strip().lower() or "pull"


def _task_cache_root() -> Path:
    override = os.environ.get(_TB2_PREFLIGHT_CACHE_ROOT_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cache" / "harbor" / "tasks"


def _artifact_roots() -> tuple[Path, ...]:
    return (
        REPO_ROOT / "benchmarks" / "evidence",
        REPO_ROOT / "experiments",
    )


def _collect_docker_images_from_json_tree(root: Path, images: set[str]) -> None:
    if not root.exists():
        return
    for json_path in root.rglob("*.json"):
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        stack: list[Any] = [payload]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                image = node.get("docker_image")
                if isinstance(image, str):
                    cleaned = image.strip()
                    if cleaned.startswith("alexgshaw/") and cleaned.endswith(":20251031"):
                        images.add(cleaned)
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)


def _discover_task_images(
    *,
    task_cache_root: Path | None = None,
    artifact_roots: tuple[Path, ...] | None = None,
) -> list[str]:
    cache_root = task_cache_root or _task_cache_root()
    images: set[str] = set()
    if not cache_root.exists():
        cache_images: list[str] = []
    else:
        for task_toml in cache_root.glob("*/*/task.toml"):
            try:
                payload = tomllib.loads(task_toml.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError):
                continue
            environment = payload.get("environment")
            if not isinstance(environment, dict):
                continue
            image = environment.get("docker_image")
            if isinstance(image, str) and image.strip().startswith("alexgshaw/") and image.strip().endswith(":20251031"):
                images.add(image.strip())
        cache_images = sorted(images)
    if cache_images:
        return cache_images
    for root in artifact_roots or _artifact_roots():
        _collect_docker_images_from_json_tree(root, images)
    return sorted(images)


def _docker_image_inspect(image: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["docker", "image", "inspect", image],
        check=False,
        capture_output=True,
        text=True,
    )


def _docker_image_pull(image: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["docker", "pull", image],
        check=False,
        capture_output=True,
        text=True,
    )


def _looks_like_rate_limit(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _TB2_PREFLIGHT_RATE_LIMIT_MARKERS)


def _preflight_tb2_images(
    *,
    task_cache_root: Path | None = None,
    artifact_roots: tuple[Path, ...] | None = None,
) -> dict[str, Any] | None:
    mode = _preflight_mode()
    if mode in {"", "off", "none", "disabled"}:
        return None
    if mode not in {"pull", "inspect", "auto"}:
        mode = "auto"

    images = _discover_task_images(task_cache_root=task_cache_root, artifact_roots=artifact_roots)
    checked: list[dict[str, Any]] = []
    if not images:
        cache_root = task_cache_root or _task_cache_root()
        payload: dict[str, Any] = {
            "state": "preflight_complete",
            "message": (
                "no TB2 task images discovered under "
                f"{cache_root}; continuing without image staging"
            ),
            "exit_code": 0,
            "task_cache_root": str(cache_root),
            "missing_task_metadata": True,
            "checked_images": checked,
            "staged_images": [],
            "deferred_images": [],
        }
        if mode == "pull":
            payload.update(
                {
                    "state": "launch_failed",
                    "message": (
                        "no TB2 task images discovered under "
                        f"{cache_root}; task metadata missing or cache cold"
                    ),
                    "exit_code": 1,
                }
            )
        return payload
    staged: list[str] = []
    deferred: list[str] = []
    for image in images:
        try:
            inspect = _docker_image_inspect(image)
        except FileNotFoundError as exc:
            payload = {
                "state": "preflight_complete" if mode != "pull" else "launch_failed",
                "message": f"docker unavailable during preflight: {exc}",
                "exit_code": 0 if mode != "pull" else 1,
                "missing_image": image,
                "checked_images": checked,
                "staged_images": staged,
                "deferred_images": deferred + [image],
            }
            if mode == "pull":
                return payload
            checked.append({"image": image, "present": False, "deferred": True})
            deferred.append(image)
            continue
        entry: dict[str, Any] = {
            "image": image,
            "present": inspect.returncode == 0,
        }
        if inspect.returncode == 0:
            checked.append(entry)
            staged.append(image)
            continue

        entry["deferred"] = True
        entry["staged"] = False
        checked.append(entry)
        deferred.append(image)
        if mode == "pull":
            try:
                pull = _docker_image_pull(image)
            except FileNotFoundError as exc:
                return {
                    "state": "launch_failed",
                    "message": f"docker unavailable during preflight: {exc}",
                    "exit_code": 1,
                    "missing_image": image,
                    "checked_images": checked,
                    "staged_images": staged,
                    "deferred_images": deferred,
                }
            entry["pulled"] = pull.returncode == 0
            entry["pull_stdout"] = (pull.stdout or "").strip()
            entry["pull_stderr"] = (pull.stderr or "").strip()
            if pull.returncode != 0:
                combined = "\n".join(
                    part for part in (pull.stdout or "", pull.stderr or "") if part
                ).strip()
                message = f"docker pull failed for {image}"
                if combined:
                    message = f"{message}: {combined.splitlines()[-1]}"
                if _looks_like_rate_limit(combined):
                    message = f"{message} (docker hub rate limit)"
                return {
                    "state": "launch_failed",
                    "message": message,
                    "exit_code": pull.returncode or 1,
                    "missing_image": image,
                    "rate_limit": _looks_like_rate_limit(combined),
                    "checked_images": checked,
                    "staged_images": staged,
                    "deferred_images": deferred,
                }
            staged.append(image)

    return {
        "state": "preflight_complete",
        "exit_code": 0,
        "task_cache_root": str(task_cache_root or _task_cache_root()),
        "checked_images": checked,
        "staged_images": staged,
        "deferred_images": deferred,
    }


def _argus_env(exp_dir: Path, *, reviewer_gate: int, verifier_short_circuit: int) -> dict[str, str]:
    return {
        "ARGUS_SKILL_HARBOR_SCIENTIST_MODEL": DEFAULT_SCIENTIST_MODEL,
        "ARGUS_SKILL_HARBOR_SCIENTIST_EFFORT": DEFAULT_REASONING_EFFORT,
        "ARGUS_SKILL_HARBOR_REVIEWER_MODEL": DEFAULT_REVIEWER_MODEL,
        "ARGUS_SKILL_HARBOR_REVIEWER_EFFORT": DEFAULT_REVIEWER_EFFORT,
        "ARGUS_SKILL_HARBOR_SKILLS_DIR": str(exp_dir / "skills"),
        "ARGUS_SKILL_HARBOR_DECISIONS_LOG": str(exp_dir / "logs" / "decisions.jsonl"),
        "ARGUS_SKILL_HARBOR_DISTILL_BUDGET": "120",
        "ARGUS_SKILL_HARBOR_REVIEWER_BUDGET": "60",
        "ARGUS_SKILL_HARBOR_ROUND_TIMEOUT": "1800",
        "ARGUS_SKILL_HARBOR_MAX_ROUNDS": "2",
        "ARGUS_SKILL_HARBOR_REVIEWER_GATE": str(reviewer_gate),
        "ARGUS_SKILL_HARBOR_RUNTIME_PROBE": "1",
        "ARGUS_SKILL_HARBOR_V12_VERIFIER": "1",
        "ARGUS_SKILL_HARBOR_VERIFIER_PASS_SHORT_CIRCUIT": str(verifier_short_circuit),
    }


def _build_argus_command(exp_dir: Path, *, model: str) -> list[str]:
    return [
        "sg",
        "docker",
        "-c",
        (
            "cd /home/argustest/argus-skill && "
            'OPENAI_API_KEY="$OPENAI_API_KEY" '
            'OPENAI_BASE_URL="$OPENAI_BASE_URL" '
            'PYTHONPATH="$PYTHONPATH" '
            'ARGUS_SKILL_HARBOR_SCIENTIST_MODEL="$ARGUS_SKILL_HARBOR_SCIENTIST_MODEL" '
            'ARGUS_SKILL_HARBOR_SCIENTIST_EFFORT="$ARGUS_SKILL_HARBOR_SCIENTIST_EFFORT" '
            'ARGUS_SKILL_HARBOR_REVIEWER_MODEL="$ARGUS_SKILL_HARBOR_REVIEWER_MODEL" '
            'ARGUS_SKILL_HARBOR_REVIEWER_EFFORT="$ARGUS_SKILL_HARBOR_REVIEWER_EFFORT" '
            'ARGUS_SKILL_HARBOR_SKILLS_DIR="$ARGUS_SKILL_HARBOR_SKILLS_DIR" '
            'ARGUS_SKILL_HARBOR_DECISIONS_LOG="$ARGUS_SKILL_HARBOR_DECISIONS_LOG" '
            'ARGUS_SKILL_HARBOR_DISTILL_BUDGET="$ARGUS_SKILL_HARBOR_DISTILL_BUDGET" '
            'ARGUS_SKILL_HARBOR_REVIEWER_BUDGET="$ARGUS_SKILL_HARBOR_REVIEWER_BUDGET" '
            'ARGUS_SKILL_HARBOR_ROUND_TIMEOUT="$ARGUS_SKILL_HARBOR_ROUND_TIMEOUT" '
            'ARGUS_SKILL_HARBOR_MAX_ROUNDS="$ARGUS_SKILL_HARBOR_MAX_ROUNDS" '
            'ARGUS_SKILL_HARBOR_REVIEWER_GATE="$ARGUS_SKILL_HARBOR_REVIEWER_GATE" '
            'ARGUS_SKILL_HARBOR_RUNTIME_PROBE="$ARGUS_SKILL_HARBOR_RUNTIME_PROBE" '
            'ARGUS_SKILL_HARBOR_V12_VERIFIER="$ARGUS_SKILL_HARBOR_V12_VERIFIER" '
            'ARGUS_SKILL_HARBOR_VERIFIER_PASS_SHORT_CIRCUIT="$ARGUS_SKILL_HARBOR_VERIFIER_PASS_SHORT_CIRCUIT" '
            "harbor run "
            "--dataset terminal-bench@2.0 "
            "--agent-import-path benchmarks.harbor_adapter:ArgusSkillCodex "
            f"--model {model} "
            "--ak reasoning_effort=high "
            "--agent-setup-timeout-multiplier 3 "
            f"-n {DEFAULT_CONCURRENCY} "
            f"--jobs-dir '{exp_dir / 'jobs'}' "
            "-y"
        ),
    ]


def _build_bare_command(exp_dir: Path, *, model: str) -> list[str]:
    return [
        "sg",
        "docker",
        "-c",
        (
            "cd /home/argustest/argus-skill && "
            'OPENAI_API_KEY="$OPENAI_API_KEY" '
            'OPENAI_BASE_URL="$OPENAI_BASE_URL" '
            'PYTHONPATH="$PYTHONPATH" '
            "harbor run "
            "--dataset terminal-bench@2.0 "
            f"--model {model} "
            "--ak reasoning_effort=high "
            "--agent-setup-timeout-multiplier 3 "
            f"-n {DEFAULT_CONCURRENCY} "
            f"--jobs-dir '{exp_dir / 'jobs'}' "
            "-y"
        ),
    ]


def _build_spec(condition: str, run_root: Path, run_id: str | None) -> LaunchSpec:
    run_root = run_root.resolve()
    started = _utc_stamp()
    if condition == "argus-v12-redux":
        exp_dir = run_root / (run_id or f"tb2-{condition}-{started}")
        command = _build_argus_command(exp_dir, model=DEFAULT_ENGINEER_MODEL)
        env = _shared_env(exp_dir)
        env.update(_argus_env(exp_dir, reviewer_gate=0, verifier_short_circuit=1))
        metadata: dict[str, Any] = {
            "condition": condition,
            "dataset_id": TB2_DATASET_ID,
            "dataset_commit": TB2_DATASET_COMMIT,
            "model_ids": {
                "scientist": DEFAULT_SCIENTIST_MODEL,
                "reviewer": DEFAULT_REVIEWER_MODEL,
                "engineer": DEFAULT_ENGINEER_MODEL,
            },
            "reasoning_effort": DEFAULT_REASONING_EFFORT,
            "verifier_reward_source": "terminal-bench official verifier /tests/test.sh",
            "pricing_source": "argus_skill.core.pricing.usd_for_tokens",
        }
    elif condition == "argus-v12-true":
        exp_dir = run_root / (run_id or f"tb2-{condition}-{started}")
        command = _build_argus_command(exp_dir, model=DEFAULT_ENGINEER_MODEL)
        env = _shared_env(exp_dir)
        env.update(_argus_env(exp_dir, reviewer_gate=1, verifier_short_circuit=0))
        metadata = {
            "condition": condition,
            "dataset_id": TB2_DATASET_ID,
            "dataset_commit": TB2_DATASET_COMMIT,
            "model_ids": {
                "scientist": DEFAULT_SCIENTIST_MODEL,
                "reviewer": DEFAULT_REVIEWER_MODEL,
                "engineer": DEFAULT_ENGINEER_MODEL,
            },
            "reasoning_effort": DEFAULT_REASONING_EFFORT,
            "verifier_reward_source": "terminal-bench official verifier /tests/test.sh",
            "pricing_source": "argus_skill.core.pricing.usd_for_tokens",
        }
    elif condition == "bare-gpt54":
        exp_dir = run_root / (run_id or f"tb2-{condition}-{started}")
        command = _build_bare_command(exp_dir, model=DEFAULT_BARE_GPT54)
        env = _shared_env(exp_dir)
        metadata = {
            "condition": condition,
            "dataset_id": TB2_DATASET_ID,
            "dataset_commit": TB2_DATASET_COMMIT,
            "model_ids": {"engineer": DEFAULT_BARE_GPT54},
            "reasoning_effort": DEFAULT_REASONING_EFFORT,
            "verifier_reward_source": "terminal-bench official verifier /tests/test.sh",
            "pricing_source": "harbor default pricing",
        }
    elif condition == "bare-gpt54-mini":
        exp_dir = run_root / (run_id or f"tb2-{condition}-{started}")
        command = _build_bare_command(exp_dir, model=DEFAULT_BARE_GPT54_MINI)
        env = _shared_env(exp_dir)
        metadata = {
            "condition": condition,
            "dataset_id": TB2_DATASET_ID,
            "dataset_commit": TB2_DATASET_COMMIT,
            "model_ids": {"engineer": DEFAULT_BARE_GPT54_MINI},
            "reasoning_effort": DEFAULT_REASONING_EFFORT,
            "verifier_reward_source": "terminal-bench official verifier /tests/test.sh",
            "pricing_source": "harbor default pricing",
        }
    else:
        raise SystemExit(f"unknown condition: {condition}")

    return LaunchSpec(
        run_root=run_root,
        run_id=exp_dir.name,
        command=command,
        cwd=Path("/home/argustest/argus-skill"),
        env={key: value for key, value in env.items() if value},
        metadata=metadata,
        preflight=(
            lambda: _preflight_tb2_images(
                task_cache_root=None,
                artifact_roots=_artifact_roots(),
            )
            if _preflight_mode() != "off"
            else None
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--condition",
        required=True,
        choices=SUPPORTED_CONDITIONS,
    )
    parser.add_argument(
        "--run-root",
        default="experiments",
        help="Root directory for detached run bundles.",
    )
    parser.add_argument("--run-id", help="Optional explicit run id.")
    args = parser.parse_args(argv)

    spec = _build_spec(args.condition, Path(args.run_root), args.run_id)
    run_dir = launch_detached(spec)
    print(run_dir)
    print(run_dir / "manifest.json")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
