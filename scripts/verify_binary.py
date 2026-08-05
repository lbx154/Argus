#!/usr/bin/env python3
"""Smoke-test a frozen Argus executable before packaging it for npm."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

EXPECTED_API_SERVICE = "argus-skill-webapi"
EXPECTED_API_PROTOCOL = "argus.webapi"


def _combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stdout or "") + (result.stderr or "")


def _run_cli_smoke(binary: Path, env: dict[str, str]) -> None:
    for arguments, expected in ((["--help"], "usage: argus"), (["--version"], "argus-skill")):
        result = subprocess.run(
            [str(binary), *arguments],
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=30,
        )
        rendered = _combined_output(result)
        if result.returncode != 0 or expected.lower() not in rendered.lower():
            raise RuntimeError(
                f"binary CLI smoke {arguments!r} failed ({result.returncode}):\n"
                f"{rendered[-4000:]}"
            )


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _validate_api_meta(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError("frozen WebAPI /api/meta did not return an object")
    if payload.get("service") != EXPECTED_API_SERVICE:
        raise RuntimeError(
            f"frozen WebAPI reported unexpected service {payload.get('service')!r}"
        )
    protocol = payload.get("protocol")
    if not isinstance(protocol, dict) or protocol.get("name") != EXPECTED_API_PROTOCOL:
        raise RuntimeError(f"frozen WebAPI reported invalid protocol {protocol!r}")
    runtime = payload.get("runtime")
    if not isinstance(runtime, dict) or not str(runtime.get("release_id") or "").strip():
        raise RuntimeError("frozen WebAPI did not report a release identity")
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, list) or "release.identity.v1" not in capabilities:
        raise RuntimeError("frozen WebAPI is missing release.identity.v1")
    return payload


def _read_api_meta(port: int) -> dict[str, Any]:
    # Release verification must not depend on host proxy settings for loopback.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{port}/api/meta", timeout=0.75) as response:
        if response.status != 200:
            raise RuntimeError(f"GET /api/meta returned HTTP {response.status}")
        return _validate_api_meta(json.load(response))


def _stop_process(process: subprocess.Popen[str]) -> str:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    stdout, _ = process.communicate(timeout=2)
    return stdout or ""


def _run_web_smoke(binary: Path, base_env: dict[str, str]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="argus-binary-smoke-") as raw_home:
        home = Path(raw_home)
        prompts = home / "special_prompts"
        prompts.mkdir(parents=True)
        house_rules = prompts / "10-house-rules.md"
        house_rules.write_text(
            "Release smoke: operate only inside explicitly assigned test resources.\n",
            encoding="utf-8",
        )
        house_rules.chmod(0o644)

        port = _available_loopback_port()
        env = dict(base_env)
        env["ARGUS_SKILL_HOME"] = str(home)
        env["ARGUS_BINARY_MODE"] = "cli"
        process = subprocess.Popen(
            [
                str(binary),
                "--web",
                "--web-host",
                "127.0.0.1",
                "--web-port",
                str(port),
            ],
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 30.0
        last_error = "WebAPI did not answer"
        try:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    output = _stop_process(process)
                    raise RuntimeError(
                        f"frozen WebAPI exited with {process.returncode}:\n{output[-4000:]}"
                    )
                try:
                    return _read_api_meta(port)
                except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
                    last_error = str(exc)
                    time.sleep(0.2)
            raise RuntimeError(f"frozen WebAPI startup timed out: {last_error}")
        finally:
            _stop_process(process)


def verify(binary: Path) -> dict[str, Any]:
    resolved = binary.expanduser().resolve()
    if not resolved.is_file():
        raise RuntimeError(f"missing binary: {resolved}")

    env = os.environ.copy()
    env["ARGUS_BINARY_MODE"] = "cli"
    _run_cli_smoke(resolved, env)
    meta = _run_web_smoke(resolved, env)

    leaked = sorted(resolved.parent.glob("**/*.py"))
    if leaked:
        raise RuntimeError(f"binary output contains Python source files: {leaked[:5]}")
    return meta


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    args = parser.parse_args()
    try:
        meta = verify(args.binary)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"binary smoke failed: {exc}") from exc
    runtime = meta["runtime"]
    print(
        "binary smoke passed: "
        f"{args.binary.expanduser().resolve()} · {runtime['release_id']} · WebAPI ready"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
