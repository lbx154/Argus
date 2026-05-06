"""Minimal docker environment that satisfies argus-skill's
``ContainerCodexRunner`` duck-typed interface.

The runner only calls:
  * ``await environment.exec(command, env=None, timeout_sec=None,
    cwd=None, user=None) -> ExecResult`` where ExecResult exposes
    ``return_code``, ``stdout``, ``stderr``.
  * ``await environment.upload_dir(source_dir, target_dir)`` (only when
    the runner is configured with ``tests_src_dir``; SWE-Bench-Pro does
    not use it, but we implement it for completeness).

It also exposes a small helper API (``diff_repo``, ``reset_repo``,
``write_file``, ``read_file``) used by the SWE-Bench-Pro runner and
evaluator.
"""
from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator


def _resolve_host_paths() -> tuple[str | None, str | None, str | None]:
    """Locate node, codex binary and the @openai/codex npm package on the host.

    Tries (in order): /usr/bin defaults, $PATH lookups, the active nvm node
    install. Any of the three may be ``None`` if not found, in which case
    that mount is simply skipped.
    """
    import shutil

    codex = shutil.which("codex") or (
        "/usr/bin/codex" if os.path.exists("/usr/bin/codex") else None
    )
    # codex is typically a symlink into <node-prefix>/lib/node_modules/@openai/codex/bin/codex.js;
    # follow it to find both the node prefix and the package dir.
    pkg: str | None = None
    node_prefix: Path | None = None
    if codex:
        target = Path(codex)
        try:
            real = target.resolve()
            # .../lib/node_modules/@openai/codex/bin/codex.js  -> pkg = .../@openai/codex
            if real.parent.name == "bin":
                pkg = str(real.parent.parent)
                # node_modules/@openai/codex -> node_modules -> lib -> prefix
                node_modules = real.parent.parent.parent.parent
                if node_modules.name == "node_modules":
                    node_prefix = node_modules.parent.parent
        except Exception:  # noqa: BLE001
            pass

    # node binary: prefer the one from the same nvm prefix as codex.
    node: str | None = None
    if node_prefix is not None:
        cand = node_prefix / "bin" / "node"
        if cand.exists():
            node = str(cand)
    if not node:
        node = shutil.which("node") or (
            "/usr/bin/node" if os.path.exists("/usr/bin/node") else None
        )

    if pkg and not Path(pkg).is_dir():
        pkg = None
    return node, codex, pkg


HOST_NODE, HOST_CODEX_BIN, HOST_CODEX_PKG = _resolve_host_paths()
HOST_CODEX_CONFIG = Path(os.path.expanduser("~/.codex"))

#: Default repo workdir inside the sweap images.
DEFAULT_WORKDIR = "/app"


@dataclass
class ExecResult:
    return_code: int
    stdout: str
    stderr: str


class MinimalDockerEnvironment:
    """Async wrapper around ``docker exec`` for one running container.

    Constructed by :func:`docker_container` (async context manager).

    Notes
    -----
    * ``exec`` runs commands via ``docker exec -i -w <cwd> -e K=V <cid>
      bash -lc <command>``.
    * Default ``cwd`` is ``/app`` (canonical SWE-Bench-Pro repo path).
    * ``upload_dir`` is implemented via ``docker cp``.
    """

    def __init__(
        self,
        container_id: str,
        *,
        default_workdir: str = DEFAULT_WORKDIR,
        default_user: str | None = None,
    ) -> None:
        self.container_id = container_id
        self.default_workdir = default_workdir
        self.default_user = default_user
        # Compatibility attributes some argus-skill helpers may look at.
        self.environment_dir = None  # harbor-only

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult:
        argv = ["docker", "exec", "-i"]
        argv.extend(["-w", cwd or self.default_workdir])
        if env:
            for k, v in env.items():
                argv.extend(["-e", f"{k}={v}"])
        if user is not None:
            argv.extend(["--user", str(user)])
        elif self.default_user is not None:
            argv.extend(["--user", str(self.default_user)])
        argv.append(self.container_id)
        argv.extend(["bash", "-lc", command])

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_sec
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            await proc.wait()
            return ExecResult(
                return_code=124,
                stdout="",
                stderr=f"docker exec timed out after {timeout_sec}s",
            )
        return ExecResult(
            return_code=int(proc.returncode or 0),
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
        )

    async def upload_dir(self, source_dir: Path | str, target_dir: str) -> None:
        src = Path(source_dir)
        if not src.exists():
            raise FileNotFoundError(src)
        # ensure target's parent exists
        await self.exec(
            f"mkdir -p {shlex.quote(str(Path(target_dir).parent))}",
            timeout_sec=30,
        )
        # remove existing target so behaviour matches harbor (overwrite)
        await self.exec(
            f"rm -rf {shlex.quote(target_dir)}", timeout_sec=30
        )
        argv = [
            "docker", "cp", "-q", f"{src}/.", f"{self.container_id}:{target_dir}",
        ]
        # docker cp into a non-existent target dir auto-creates it via "/."
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"docker cp {src} -> {target_dir} failed: "
                f"{err.decode('utf-8', errors='replace')[:400]}"
            )

    # ---- convenience helpers used by SWE-Bench-Pro runner -----------

    async def diff_repo(self, repo_path: str = DEFAULT_WORKDIR) -> str:
        r = await self.exec(
            f"git -C {shlex.quote(repo_path)} add -A && "
            f"git -C {shlex.quote(repo_path)} diff --cached HEAD",
            timeout_sec=120,
        )
        return r.stdout if r.return_code == 0 else ""

    async def reset_repo(
        self, base_commit: str, repo_path: str = DEFAULT_WORKDIR
    ) -> ExecResult:
        cmd = (
            f"cd {shlex.quote(repo_path)} && "
            f"git reset --hard {shlex.quote(base_commit)} && "
            f"git clean -fd"
        )
        return await self.exec(cmd, timeout_sec=300)


# ----------------------------------------------------------------------------
# Container lifecycle (sync helpers used by the per-task runner)
# ----------------------------------------------------------------------------


def _docker_run(
    image: str,
    *,
    name: str,
    idle_seconds: int = 7200,
    extra_env: dict[str, str] | None = None,
    network_host: bool = False,
) -> str:
    """Start a long-idle container from *image*. Returns container id (cid)."""
    argv: list[str] = [
        "docker", "run", "-d", "--rm",
        "--name", name,
        "--entrypoint", "/bin/bash",
    ]

    # Mount host codex CLI + Node runtime read-only.
    # We resolve symlinks first because docker -v requires real files.
    if HOST_NODE and os.path.exists(HOST_NODE):
        argv.extend(["-v", f"{Path(HOST_NODE).resolve()}:/usr/bin/node:ro"])
    pkg_mounted = False
    if HOST_CODEX_PKG and os.path.isdir(HOST_CODEX_PKG):
        # Mount the whole package (incl. nested node_modules with the
        # native @openai/codex-linux-x64 binary). We will create a
        # /usr/bin/codex symlink at container startup so Node resolves
        # modules from inside the package directory.
        argv.extend([
            "-v",
            f"{HOST_CODEX_PKG}:/usr/lib/node_modules/@openai/codex:ro",
        ])
        pkg_mounted = True
    if HOST_CODEX_CONFIG.exists():
        argv.extend(["-v", f"{HOST_CODEX_CONFIG}:/root/.codex"])

    if network_host:
        argv.append("--network=host")

    env = dict(extra_env or {})
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
        val = os.environ.get(key)
        if val and key not in env:
            env[key] = val
    for k, v in env.items():
        argv.extend(["-e", f"{k}={v}"])

    argv.append(image)
    # Create /usr/bin/codex symlink into the mounted package on startup so
    # `which codex` works and Node resolves the package's bundled deps.
    bootstrap = (
        "ln -sf /usr/lib/node_modules/@openai/codex/bin/codex.js /usr/bin/codex 2>/dev/null; "
        if pkg_mounted else ""
    )
    argv.extend(["-lc", f"{bootstrap}sleep {idle_seconds}"])

    r = subprocess.run(argv, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError(
            f"docker run failed for {image}: "
            f"{r.stderr.strip()[:500] or r.stdout.strip()[:500]}"
        )
    return r.stdout.strip()


def _docker_rm(cid: str) -> None:
    subprocess.run(["docker", "rm", "-f", cid], capture_output=True, timeout=60)


@asynccontextmanager
async def docker_container(
    image: str,
    *,
    base_commit: str = "",
    repo_path: str = DEFAULT_WORKDIR,
    idle_seconds: int = 7200,
    extra_env: dict[str, str] | None = None,
    network_host: bool = False,
) -> AsyncIterator[MinimalDockerEnvironment]:
    """Run a sweap container; yield a ready-to-use MinimalDockerEnvironment.

    On entry, optionally resets *repo_path* to ``base_commit`` (with
    ``git clean -fd``). Always force-removes the container on exit.
    """
    name = f"argus-swebpro-{uuid.uuid4().hex[:10]}"
    cid = await asyncio.to_thread(
        _docker_run,
        image,
        name=name,
        idle_seconds=idle_seconds,
        extra_env=extra_env,
        network_host=network_host,
    )
    env = MinimalDockerEnvironment(cid)
    try:
        if base_commit:
            await env.reset_repo(base_commit, repo_path=repo_path)
        yield env
    finally:
        await asyncio.to_thread(_docker_rm, cid)
