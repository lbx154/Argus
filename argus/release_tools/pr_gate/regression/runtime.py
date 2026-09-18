"""Shared file, Copilot, and optional-container support; no experiment policy."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

ASSETS = Path(__file__).resolve().parent
STOP = threading.Event()
WORKER_FILES = (
    "entry.py", "probe.py", "probe_contract.py", "oracle.py",
    "origin_guard/sitecustomize.py", "egress_proxy.py", "report.schema.json",
    "probe-receipt.schema.json", "Dockerfile", "requirements.txt", "owned_process.py",
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def asset_source(name: str) -> Path:
    path = ASSETS / name
    if name == "owned_process.py" and not path.is_file():
        path = ASSETS.parent / name
    return path


def stage_assets(root: Path, skill: Path) -> None:
    (root / "runner").mkdir()
    for name in WORKER_FILES:
        target = root / "runner" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(asset_source(name), target)
    shutil.copy2(skill, root / "SKILL.md")


def safe_file(root: Path, relative: str) -> Path:
    supplied = PurePosixPath(relative)
    if supplied.is_absolute():
        if not supplied.is_relative_to("/work"):
            raise ValueError(f"Missing or escaping artifact: {relative}")
        supplied = supplied.relative_to("/work")
    if ".." in supplied.parts:
        raise ValueError(f"Missing or escaping artifact: {relative}")
    path = root.joinpath(*supplied.parts)
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"Missing or escaping artifact: {relative}")
    current = path
    while current != root:
        if current.is_symlink():
            raise ValueError(f"Symlink artifact is not permitted: {relative}")
        current = current.parent
    return path


def copy_evidence(source: Path, destination: Path) -> None:
    def ignore_links(directory: str, names: list[str]) -> list[str]:
        return [name for name in names if (Path(directory) / name).is_symlink()]
    shutil.copytree(source, destination, ignore=ignore_links)


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def command(args: list[str], *, timeout: int = 180) -> str:
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed ({result.returncode}): {result.stderr[-1500:]}")
    return result.stdout.strip()


def active_token() -> str:
    for key in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(key):
            return os.environ[key]
    home = Path(os.environ.get("COPILOT_HOME", str(Path.home() / ".copilot")))
    text = "\n".join(
        line for line in (home / "config.json").read_text().splitlines()
        if not line.lstrip().startswith("//")
    )
    config = json.loads(text)
    account = config["lastLoggedInUser"]
    token = config["copilotTokens"][f"{account['host']}:{account['login']}"]
    if not isinstance(token, str) or not token:
        raise ValueError("Active Copilot account has no usable token")
    return token


def worker_args(root: Path, work: Path, *, name: str, network: str, study_id: str) -> list[str]:
    return [
        "docker", "run", "--rm", "--name", name, "--label", f"copilot.study={study_id}",
        "--network", network, "--user", f"{os.getuid()}:{os.getgid()}",
        "--cpus=2", "--memory=4g", "--memory-swap=4g", "--pids-limit=256",
        "--cap-drop=ALL", "--security-opt=no-new-privileges", "--read-only",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m",
        "--mount", f"type=bind,src={work},dst=/work",
        "--mount", f"type=bind,src={root / 'runner'},dst=/runner,readonly",
        "--mount", f"type=bind,src={root / 'SKILL.md'},dst=/skill/SKILL.md,readonly",
    ]


def remove_container(name: str, *, study_id: str) -> None:
    inspection = subprocess.run(
        ["docker", "inspect", "--format", '{{index .Config.Labels "copilot.study"}}', name],
        text=True, capture_output=True,
    )
    if inspection.returncode == 0:
        if inspection.stdout.strip() != study_id:
            raise RuntimeError("Refusing to remove a container owned by another run")
        command(["docker", "rm", "--force", name])


@contextlib.contextmanager
def network(root: Path, *, study_id: str, image: str):
    name, proxy = f"{study_id}-internal", f"{study_id}-proxy"
    command(["docker", "network", "create", "--internal", "--label", f"copilot.study={study_id}", name])
    try:
        command([
            "docker", "run", "--detach", "--rm", "--name", proxy,
            "--label", f"copilot.study={study_id}", "--network", "bridge",
            "--cpus=0.25", "--memory=128m", "--pids-limit=64",
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--read-only",
            "--mount", f"type=bind,src={root / 'runner'},dst=/runner,readonly",
            image, "python", "/runner/egress_proxy.py",
        ])
        command(["docker", "network", "connect", "--alias", "egress", name, proxy])
        command(["docker", "exec", proxy, "python", "-c",
                 "import socket; socket.create_connection(('127.0.0.1',3128),timeout=5).close()"])
        yield name
    finally:
        remove_container(proxy, study_id=study_id)
        command(["docker", "network", "rm", name])


def invoke(
    root: Path, work: Path, *, name: str, net: str, mode: str,
    model: str, token: str, log_path: Path, timeout: int, study_id: str, image: str,
) -> int:
    args = worker_args(root, work, name=name, network=net, study_id=study_id)
    binary = json.loads((root / "manifest.json").read_text())["copilot_binary"]
    args += [
        "--mount", f"type=bind,src={binary},dst=/opt/copilot,readonly",
        "--env", "COPILOT_GITHUB_TOKEN", "--env", "HTTPS_PROXY=http://egress:3128",
        "--env", "HTTP_PROXY=http://egress:3128",
        image, "python", "/runner/entry.py", mode, "--model", model, "--effort", "high",
    ]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("wb") as output:
        process = subprocess.Popen(
            args, stdout=output, stderr=subprocess.STDOUT,
            env=dict(os.environ, COPILOT_GITHUB_TOKEN=token),
        )
        try:
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                if STOP.is_set() or time.monotonic() >= deadline:
                    remove_container(name, study_id=study_id)
                    process.wait(timeout=30)
                    return 130 if STOP.is_set() else 124
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    continue
            return process.returncode
        finally:
            remove_container(name, study_id=study_id)


def session_tools(path: Path) -> list[dict]:
    calls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "tool.execution_start":
            calls.append(event["data"])
    return calls


def validate_skill_session(path: Path, *, skill_path: str = "/skill/SKILL.md") -> list[str]:
    calls = session_tools(path)
    allowed = {"bash", "read_bash", "stop_bash", "view", "glob", "rg", "apply_patch"}
    first = calls[0] if calls and isinstance(calls[0], dict) else {}
    arguments = first.get("arguments")
    if first.get("toolName") != "view" or not isinstance(arguments, dict) or arguments.get("path") != skill_path:
        raise ValueError("Session did not begin by reading the frozen Skill")
    if any(not isinstance(call, dict) or call.get("toolName") not in allowed for call in calls):
        raise ValueError("Session used a tool outside the local-tool contract")
    return sorted({call["toolName"] for call in calls})
