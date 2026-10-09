"""Launch one tenant in a filesystem, user, PID and network namespace.

The tenant gets a fresh /proc, only its own writable directory, read-only
language runtimes/source, and Unix sockets for metered models/public egress.
There is no host network, operator home, provider login or other tenant mount.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import resource
import subprocess
import time
from pathlib import Path

MODEL = "gpt-6.1-sol"


def sandbox_command(config: dict, tenant: str, command: list[str] | None = None) -> list[str]:
    row = config["tenants"][tenant]
    source = Path(config["source"])
    python = Path(config["python_runtime"])
    venv = Path(config["venv"])
    node = Path(config["node_runtime"])
    args = ["/usr/bin/bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL"]
    for path in ("/usr", "/bin", "/sbin", "/lib", "/lib64"):
        if Path(path).exists():
            args.extend(("--ro-bind", path, path))
    args.extend(("--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/etc"))
    for path in ("/etc/ssl", "/etc/ca-certificates", "/etc/hosts", "/etc/nsswitch.conf", "/etc/passwd", "/etc/group", "/etc/ld.so.cache"):
        if Path(path).exists():
            args.extend(("--ro-bind", path, path))
    for path in (source, python, venv, node):
        args.extend(("--ro-bind", str(path), str(path)))
    args.extend(("--bind", row["directory"], "/tenant", "--ro-bind", config["meter_runtime"], "/meter",
                 "--ro-bind", config["copilot_package"], "/tenant/home/.cache/copilot/pkg"))
    env = {
        "HOME": "/tenant/home", "PATH": f"/tenant/bin:{venv}/bin:{node}/bin:/usr/bin:/bin",
        "PYTHONPATH": str(source), "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "LANG": "C.UTF-8", "TMPDIR": "/tmp", "ARGUS_SKILL_HOME": "/tenant/state",
        "ARGUS_SKILL_SOURCE_ROOT": str(source), "ARGUS_SKILL_BUILD_REVISION": config["revision"],
        "ARGUS_SKILL_WEB_TOKEN": row["web_token"], "ARGUS_SKILL_COPILOT_TRIAL": "0",
        "ARGUS_SKILL_RUNNER_BACKEND": "copilot", "ARGUS_SKILL_MODEL": MODEL,
        "ARGUS_SKILL_RUNNER_BIN": "/tenant/bin/copilot", "COPILOT_HOME": "/tenant/home/.copilot",
        "COPILOT_MODEL": MODEL, "COPILOT_PROVIDER_MODEL_ID": MODEL,
        "COPILOT_PROVIDER_WIRE_MODEL": MODEL, "COPILOT_PROVIDER_TYPE": "openai",
        "COPILOT_PROVIDER_WIRE_API": "responses", "COPILOT_PROVIDER_TRANSPORT": "http",
        "COPILOT_PROVIDER_BASE_URL": "http://127.0.0.1:9011/v1",
        "COPILOT_PROVIDER_BEARER_TOKEN": row["model_token"],
        "COPILOT_PROVIDER_MAX_PROMPT_TOKENS": "128000", "COPILOT_PROVIDER_MAX_OUTPUT_TOKENS": "16384",
        "HTTP_PROXY": "http://127.0.0.1:9012", "HTTPS_PROXY": "http://127.0.0.1:9012",
        "http_proxy": "http://127.0.0.1:9012", "https_proxy": "http://127.0.0.1:9012",
        "NO_PROXY": "localhost,127.0.0.1", "no_proxy": "localhost,127.0.0.1",
        "ARGUS_SKILL_MAX_ACTIVE_DAEMONS": "1", "ARGUS_SKILL_RESEARCH_BUDGET": "2",
    }
    for role in ("ENGINEER", "REVIEWER", "PLANNER", "MANAGER"):
        env[f"ARGUS_SKILL_{role}_MODEL"] = MODEL
        env[f"ARGUS_SKILL_{role}_BACKEND"] = "copilot"
    for key, value in env.items():
        args.extend(("--setenv", key, value))
    args.extend(("--chdir", "/tenant/workspace", "--"))
    return args + (command or [str(venv / "bin/python"), str(source / "deploy/trial/native_runtime.py"), "inside"])


async def inside():
    import uvicorn
    from native_egress import bridge

    from argus.webapi.server import create_app

    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024, 32 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (1024, 1024))
    model = await asyncio.start_server(lambda r, w: bridge(r, w, target_socket="/meter/model.sock"), "127.0.0.1", 9011)
    proxy = await asyncio.start_server(lambda r, w: bridge(r, w, target_socket="/meter/egress.sock"), "127.0.0.1", 9012)
    socket_path = Path("/tenant/run/web.sock")
    socket_path.unlink(missing_ok=True)
    app = create_app(global_root="/tenant/state")
    server = uvicorn.Server(uvicorn.Config(app, uds=str(socket_path), access_log=False, proxy_headers=False))
    async with model, proxy:
        await server.serve()


def storage_exceeded(directory: str) -> bool:
    count = size = 0
    for root, dirs, files in os.walk(directory, followlinks=False):
        count += len(dirs) + len(files)
        if count > 10000:
            return True
        for name in files:
            try:
                size += os.lstat(os.path.join(root, name)).st_size
            except FileNotFoundError:
                continue
            if size > 200 * 1024 * 1024:
                return True
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["launch", "inside", "exec"])
    parser.add_argument("--config", type=Path)
    parser.add_argument("--tenant")
    args, extra = parser.parse_known_args()
    if args.mode == "inside":
        asyncio.run(inside())
    else:
        config = json.loads(args.config.read_text())
        command = extra or None
        if command and command[0] == "--":
            command = command[1:]
        with subprocess.Popen(sandbox_command(config, args.tenant, command), env={}) as child:
            directory = config["tenants"][args.tenant]["directory"]
            while child.poll() is None:
                if args.mode == "launch" and storage_exceeded(directory):
                    print("Trial workspace storage limit reached; pausing runtime.", flush=True)
                    child.terminate()
                    child.wait(timeout=10)
                    return  # A clean exit avoids repeated restarts of a full workspace.
                time.sleep(5 if args.mode == "launch" else 0.1)
            raise SystemExit(child.returncode)


if __name__ == "__main__":
    main()
