"""Isolated local Argus CLI launch planning and execution."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence


class ArgusBackend(str, Enum):
    PI = "pi"
    COPILOT = "copilot"
    CODEX = "codex"
    CLAUDE = "claude"
    OPENCODE = "opencode"
    GROK = "grok"
    QODER = "qoder"
    DSH = "dsh"


@dataclass(frozen=True)
class CliLaunchPlan:
    argv: tuple[str, ...]
    cwd: Path
    project_root: Path
    life_dir: Path
    objective_file: Path
    backend: ArgusBackend
    dry_run: bool

    def redacted_dict(self) -> dict[str, object]:
        return {
            "argv": list(self.argv),
            "cwd": str(self.cwd),
            "project_root": str(self.project_root),
            "life_dir": str(self.life_dir),
            "objective_file": str(self.objective_file),
            "backend": self.backend.value,
            "dry_run": self.dry_run,
        }


class ArgusCliAdapter:
    """Builds an explicit argv; never invokes a shell or a shared Argus workdir."""

    def __init__(self, executable: str = "argus-skill") -> None:
        if not executable.strip():
            raise ValueError("Argus executable is required")
        self.executable = executable

    def build_launch(
        self,
        *,
        campaign_root: Path,
        objective_file: Path,
        backend: ArgusBackend | str,
        dry_run: bool = True,
        mission_width: int = 2,
        bounded: bool = True,
        continuous: bool = True,
    ) -> CliLaunchPlan:
        root = campaign_root.expanduser().resolve()
        objective = objective_file.expanduser().resolve()
        try:
            selected = backend if isinstance(backend, ArgusBackend) else ArgusBackend(backend)
        except ValueError as exc:
            raise ValueError(f"unsupported Argus backend: {backend}") from exc
        if mission_width < 1:
            raise ValueError("mission_width must be at least 1")
        if objective.parent != root and root not in objective.parents:
            raise ValueError("objective_file must live inside the isolated campaign root")
        project_root = root / "workspace"
        life_dir = root / "life"
        argv: list[str] = [
            self.executable,
            "--daemon",
            "--new",
            "--backend",
            selected.value,
            "--objective-file",
            str(objective),
            "--project-root",
            str(project_root),
            "--life-dir",
            str(life_dir),
            "--mission-width",
            str(mission_width),
        ]
        if continuous:
            argv.append("--continuous")
        if bounded:
            argv.append("--bounded")
        return CliLaunchPlan(
            argv=tuple(argv), cwd=project_root, project_root=project_root,
            life_dir=life_dir, objective_file=objective, backend=selected, dry_run=dry_run,
        )

    def launch(
        self,
        plan: CliLaunchPlan,
        *,
        environment: Mapping[str, str] | None = None,
        extra_environment_keys: Sequence[str] = (),
    ) -> subprocess.Popen[bytes] | CliLaunchPlan:
        if plan.dry_run:
            return plan
        if not plan.objective_file.is_file():
            raise FileNotFoundError(plan.objective_file)
        plan.project_root.mkdir(parents=True, exist_ok=True)
        plan.life_dir.mkdir(parents=True, exist_ok=True)
        safe_env = dict(os.environ)
        if environment:
            allowed = {"PATH", "PYTHONPATH", "LANG", "LC_ALL", *extra_environment_keys}
            rejected = set(environment) - allowed
            if rejected:
                raise ValueError(f"environment contains non-allowlisted keys: {sorted(rejected)}")
            safe_env.update(environment)
        log_path = plan.life_dir / "foundry-launch.log"
        log_handle = log_path.open("ab")
        try:
            process = subprocess.Popen(
                list(plan.argv), cwd=plan.cwd, env=safe_env, stdin=subprocess.DEVNULL,
                stdout=log_handle, stderr=subprocess.STDOUT, shell=False,
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
            )
        finally:
            log_handle.close()
        return process
