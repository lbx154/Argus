"""Single Copilot analysis, native by default; Docker is optional."""

from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path

from argus.release_tools.pr_gate import owned_process

if __package__:
    from . import entry
    from . import runtime as run
    from .snapshot import CleanupError, GateError, git
else:
    import entry
    import runtime as run
    from snapshot import CleanupError, GateError, git

def stage_assets(root: Path, skill: Path) -> None:
    run.stage_assets(root, skill)


def stage_native(root: Path, metadata: dict[str, object]) -> None:
    work = root / "work"
    for label in ("base", "candidate"):
        side = work / label
        git(work, "init", "--quiet", "--template=", str(side))
        git(side, "fetch", "--quiet", "--depth=1", str(root / "repository.git"), str(metadata[f"{label}_sha"]))
        git(side, "checkout", "--quiet", "--detach", "FETCH_HEAD")


def audit_native(work: Path, metadata: dict[str, object]) -> None:
    for label in ("base", "candidate"):
        side = work / label
        if (side / ".git").is_symlink():
            raise GateError(f"{label} Git directory was replaced by a symlink.")
        actual = git(side, "rev-parse", "HEAD").decode().strip()
        if actual != metadata[f"{label}_sha"]:
            raise GateError(f"{label} source revision was changed during analysis.")
        changed = git(side, "diff", "--no-ext-diff", "--no-textconv", "--name-only", "HEAD")
        if changed.strip():
            raise GateError(f"{label} tracked source was changed during analysis.")


def native_environment(root: Path, token: str) -> dict[str, str]:
    home, work = root / "home", root / "work"
    home.mkdir(exist_ok=True)
    retained = {
        "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TMP", "TEMP",
        "VIRTUAL_ENV", "CONDA_PREFIX", "LD_LIBRARY_PATH",
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
        "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS",
    }
    environment = {key: value for key, value in os.environ.items() if key in retained}
    environment.update({
        "HOME": str(home), "COPILOT_HOME": str(home / ".copilot"),
        "XDG_CONFIG_HOME": str(home / ".config"), "XDG_CACHE_HOME": str(home / ".cache"),
        "COPILOT_GITHUB_TOKEN": token, "COPILOT_AUTO_UPDATE": "false",
        "ARGUS_SKILL_HOME": str(home / "argus"), "ARGUS_SKILL_SAFE_MODE": "1",
        "PR_GATE_WORK_ROOT": str(work), "PYTHONDONTWRITEBYTECODE": "1",
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
        "USE_TGREP": "false", "USE_BUILTIN_RIPGREP": "false", "CI": "1",
    })
    return environment


def native(
    root: Path, metadata: dict[str, object], *, model: str, timeout: int,
) -> dict[str, object]:
    executable = shutil.which("copilot")
    if executable is None:
        raise GateError("Copilot CLI is missing from PATH; install/authenticate it before check.")
    token = run.active_token()
    stage_native(root, metadata)
    work = root / "work"
    environment = native_environment(root, token)
    version = owned_process.run_owned(
        [executable, "--no-auto-update", "--version"], cwd=work, environment=environment,
        stdout_path=root / "version.log", timeout=30, output_limit=32768,
        cancelled=run.STOP.is_set,
    )
    if not version.cleanup_complete:
        raise CleanupError("Copilot version process cleanup did not complete.")
    version_text = (root / "version.log").read_text().strip()
    if version.returncode or version.reason or not version_text:
        raise GateError("Copilot CLI could not start with its temporary home.")
    skill = root / "SKILL.md"
    prompt = entry.build_prompt(work=work, runner=root / "runner", skill=skill)
    prompt += (
        " This is trusted local development, NOT an OS sandbox. Use only the "
        "assigned temporary work and evidence paths. Do not contact running "
        "services, use host credentials, start daemons, or change other "
        "repositories. Read only the provided snapshots for repository analysis. "
        "The probe helper reuses this Python environment; do not install packages."
    )
    command = entry.copilot_command(
        executable, prompt, model=model, effort="high", usage=work / "usage.json",
    ) + ["--add-dir", str(root / "runner"), "--add-dir", str(root)]
    result = owned_process.run_owned(
        command, cwd=work, environment=environment,
        stdout_path=root / "copilot.jsonl", timeout=timeout,
        output_limit=64 * 1024 * 1024, cancelled=run.STOP.is_set,
    )
    if not result.cleanup_complete:
        raise CleanupError("Copilot/probe descendants did not settle; workspace retained.")
    code = result.returncode
    audit_native(work, metadata)
    if code or result.reason:
        raise GateError(f"Copilot invocation ended with exit {code} ({result.reason}); inspect the private run log.")
    tools = run.validate_skill_session(root / "copilot.jsonl", skill_path=str(skill))
    return {
        "status": "completed", "isolation": "native", "source_audit": True,
        "observed_tools": tools, "python": sys.version.split()[0],
        "model": model, "reasoning_effort": "high",
        "copilot_version": version_text.splitlines()[0],
        "copilot_exit_code": code,
        "limits": "Temporary source/state, bounded invocation; NOT an OS security sandbox.",
    }


def docker(
    root: Path, metadata: dict[str, object], *, model: str, timeout: int,
) -> dict[str, object]:
    if shutil.which("docker") is None:
        raise GateError("Docker mode was requested but Docker is unavailable.")
    executable = shutil.which("copilot")
    if executable is None:
        raise GateError("Copilot CLI is missing from PATH.")
    with Path(executable).open("rb") as stream:
        is_elf = stream.read(4) == b"\x7fELF"
    if not is_elf or platform.machine() != "x86_64":
        raise GateError("Docker prototype requires the standalone Linux x86-64 Copilot executable.")
    token = run.active_token()
    study_id = root.name
    image = f"pr-gate-local:{study_id}"
    name = f"{study_id}-worker"
    built = False
    try:
        run.command([
            "docker", "build", "--quiet", "--label", f"copilot.study={study_id}",
            "--tag", image, str(root / "runner"),
        ], timeout=600)
        built = True
        image_id = run.command(["docker", "image", "inspect", "--format", "{{.Id}}", image])
        (root / "bin").mkdir()
        shutil.copy2(executable, root / "bin/copilot")
        run.write_json(root / "manifest.json", {"copilot_binary": str(root / "bin/copilot")})
        work = root / "work"
        args = run.worker_args(root, work, name=name, network="none", study_id=study_id)
        run.command(args + [
            "--mount", f"type=bind,src={root / 'repository.git'},dst=/mirror,readonly",
            image, "python", "/runner/entry.py", "stage",
        ], timeout=240)
        with run.network(root, study_id=study_id, image=image) as network:
            code = run.invoke(
                root, work, name=name, net=network, mode="analyze", model=model,
                token=token, log_path=root / "copilot.jsonl", timeout=timeout,
                study_id=study_id, image=image,
            )
        args = run.worker_args(root, work, name=name, network="none", study_id=study_id)
        run.command(args + [
            image, "python", "/runner/entry.py", "audit",
            "--base-sha", str(metadata["base_sha"]),
            "--candidate-sha", str(metadata["candidate_sha"]),
        ], timeout=120)
        if code:
            raise GateError(f"Copilot invocation ended with exit {code}; inspect the private run log.")
        tools = run.validate_skill_session(root / "copilot.jsonl")
        return {
            "status": "completed", "isolation": "docker", "source_audit": True,
            "observed_tools": tools, "image_id": image_id,
            "model": model, "reasoning_effort": "high", "copilot_exit_code": code,
            "copilot_sha256": run.digest(root / "bin/copilot"),
        }
    finally:
        run.remove_container(name, study_id=study_id)
        if built:
            run.command(["docker", "image", "rm", image])


def analyze(
    root: Path, metadata: dict[str, object], *, isolation: str, model: str, timeout: int,
) -> dict[str, object]:
    if sys.platform != "linux":
        raise GateError("Local execution is currently supported on Linux; no OS sandbox is implied.")
    return (docker if isolation == "docker" else native)(
        root, metadata, model=model, timeout=timeout,
    )
