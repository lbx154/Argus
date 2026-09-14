#!/usr/bin/env python3
"""Launch the packaged Tauri host against isolated per-run state.

This guards the two paths that are easy to miss in static checks: Windows DLL
placement beside the desktop executable and the authenticated backend readiness
handshake. It never reads or writes the operator's real AppData or ARGUS home.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--binary",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "src-tauri" / "target" / "release" / "Argus.exe",
    )
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--keep-state", action="store_true", help="Preserve this run's isolated QA directory as private evidence")
    parser.add_argument("--report", type=Path, help="Write a new credential-free minimum-check report")
    parser.add_argument("--timeout", type=float, default=55.0)
    parser.add_argument(
        "--health-window",
        type=float,
        default=16.0,
        help="seconds to observe post-ready health probes before passing",
    )
    args = parser.parse_args()
    binary = args.binary.resolve()
    if args.report and args.report.exists():
        raise SystemExit("Report already exists; inspect it before retrying")
    if os.name != "nt":
        raise SystemExit("desktop host smoke is Windows-only")
    if not binary.is_file():
        raise SystemExit(f"desktop binary not found: {binary}")
    loader = binary.with_name("WebView2Loader.dll")
    if not loader.is_file() or loader.stat().st_size < 100_000:
        raise SystemExit(f"WebView2Loader.dll is missing beside {binary.name}")

    # Exercise the bug-prone path deliberately: the selected npm launcher is
    # absolute, while the host PATH below has no Node entry.  nvm's persisted
    # settings provide the verified node directory that the Tauri supervisor
    # must inject for the frozen backend and its later agent subprocesses.
    codex_runner = shutil.which("codex.cmd") or shutil.which("codex")
    node_binary = shutil.which("node.exe") or shutil.which("node")
    if not codex_runner or not node_binary:
        raise SystemExit("host smoke requires a locally installed Codex npm launcher and Node")
    node_dir = Path(node_binary).resolve().parent

    sandbox = Path(tempfile.mkdtemp(prefix="argus-tauri-host-smoke-"))
    app_data = sandbox / "appdata"
    desktop_data = app_data / ("argus-desktop-preview-integration-20260913" if args.preview else "argus-desktop")
    desktop_data.mkdir(parents=True)
    token = secrets.token_urlsafe(32)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    (desktop_data / "settings.json").write_text(
        json.dumps(
            {
                "host": "127.0.0.1",
                "port": port,
                "token": token,
                "runnerKind": "codex",
                "runnerBins": {"codex": str(Path(codex_runner).resolve())},
                # This is the returning-user scenario. A fresh installation
                # must explicitly confirm its CLI before the backend can start.
                "runnerConfigured": True,
                "setupComplete": True,
                "appearanceTheme": "light",
                # Old saved choices are ignored by the fixed-on host.
                "startupEyeMotion": "off",
            }
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    for name in list(env):
        if any(word in name.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "API_KEY")) or name.startswith(("ARGUS_", "PI_", "WEBVIEW2_")):
            env.pop(name, None)
    env.update(
        {
            "APPDATA": str(app_data),
            "LOCALAPPDATA": str(sandbox / "localappdata"),
            "HOME": str(sandbox / "home"), "USERPROFILE": str(sandbox / "home"),
            "CODEX_HOME": str(sandbox / "codex"), "COPILOT_HOME": str(sandbox / "copilot"),
            "PI_CODING_AGENT_DIR": str(sandbox / "pi"), "CLAUDE_CONFIG_DIR": str(sandbox / "claude"),
            "ARGUS_SKILL_HOME": str(sandbox / "argus-home"),
            "XDG_CONFIG_HOME": str(sandbox / "config"),
            "XDG_CACHE_HOME": str(sandbox / "cache"),
            "XDG_DATA_HOME": str(sandbox / "data"),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "ARGUS_DESKTOP_DISABLE_SINGLE_INSTANCE": "1",
            "ARGUS_DESKTOP_TEST_INSTANCE": secrets.token_hex(16),
            "ARGUS_DESKTOP_QA_ROOT": str(sandbox),
            # Packaged-host smoke must be deterministic and must not depend on
            # GitHub availability or compete with the startup measurement.
            "ARGUS_DESKTOP_DISABLE_UPDATE_CHECK": "1",
        }
    )
    env.pop("ARGUS_DESKTOP_DEV", None)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    for name in ("APPDATA", "LOCALAPPDATA", "HOME", "USERPROFILE", "ARGUS_SKILL_HOME",
                 "CODEX_HOME", "COPILOT_HOME", "PI_CODING_AGENT_DIR", "CLAUDE_CONFIG_DIR"):
        Path(env[name]).mkdir(parents=True, exist_ok=True)
    # Do not let the smoke inherit nvm's live variables/PATH: this emulates a
    # stale Explorer environment after Node installation.  The temporary nvm
    # settings are the only allowed recovery source for the desktop host.
    nvm_settings = Path(env["LOCALAPPDATA"]) / "nvm" / "settings.txt"
    nvm_settings.parent.mkdir(parents=True)
    nvm_settings.write_text(f"path: {node_dir}\n", encoding="utf-8")
    system_root = Path(env.get("SystemRoot") or r"C:\Windows")
    env["PATH"] = str(system_root / "System32")
    env.pop("NVM_HOME", None)
    env.pop("NVM_SYMLINK", None)
    launched_at = time.monotonic()
    process = subprocess.Popen(
        [str(binary)],
        cwd=binary.parent,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        log = desktop_data / "logs" / "desktop.log"
        deadline = time.monotonic() + args.timeout
        ready_observed_at: float | None = None
        ready_log_offset = 0
        while time.monotonic() < deadline:
            if log.is_file():
                text = log.read_text(encoding="utf-8", errors="replace")
                if "runner preflight failed" in text or "runner preflight could not start" in text:
                    detail = "\n".join(
                        line for line in text.splitlines()[-40:] if "runner preflight" in line
                    )
                    raise RuntimeError(f"desktop runner preflight failed:\n{detail}")
                if (
                    "backend Ready:" in text
                    and "runner preflight passed:" in text
                    and "authenticated cockpit URL issued" in text
                ):
                    if ready_observed_at is None:
                        ready_observed_at = time.monotonic()
                        ready_log_offset = len(text)
                    elif time.monotonic() - ready_observed_at >= args.health_window:
                        post_ready_log = text[ready_log_offset:]
                        if "backend health probe transient failure" in post_ready_log:
                            raise RuntimeError(
                                "desktop health probes timed out after readiness; "
                                "the host must not create periodic loopback stalls"
                            )
                        ready_seconds = ready_observed_at - launched_at
                        expected = json.loads((binary.parent / "argus-backend/_internal/argus/release_manifest.json").read_text(encoding="utf-8"))
                        request = urllib.request.Request(f"http://127.0.0.1:{port}/api/meta", headers={"Authorization": f"Bearer {token}"})
                        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=5) as response:
                            metadata = json.load(response)
                        if metadata.get("authentication", {}).get("authenticated") is not True or metadata.get("runtime", {}).get("release_id") != expected["release_id"]:
                            raise RuntimeError("Native backend authentication or release identity did not match")
                        if args.report:
                            with args.report.open("x", encoding="utf-8") as report:
                                json.dump({"scope": "isolated Windows packaged-host check", "release_id": expected["release_id"],
                                           "preview": args.preview, "qa_state_preserved": args.keep_state,
                                           "backend_health_soak_passed": args.health_window >= 1800,
                                           "native_host_started": True, "authenticated_backend": True,
                                           "real_cli_version_preflight": True, "ready_seconds": round(ready_seconds, 3),
                                           "health_observation_seconds": args.health_window, "paid_model_calls": 0,
                                           "pixel_motion_checked": False, "full_ui_checked": False, "long_soak_checked": False}, report, indent=2)
                        print(
                            "Tauri desktop host smoke passed: authenticated backend and cockpit "
                            f"ready in {ready_seconds:.3f}s, npm runner launched with recovered "
                            "Node PATH, and health probes stable."
                        )
                        return 0
                if "backend Error:" in text:
                    detail = "\n".join(
                        line for line in text.splitlines()[-20:] if "backend " in line.lower()
                    )
                    raise RuntimeError(f"desktop reported backend error:\n{detail}")
            if process.poll() is not None:
                raise RuntimeError(f"desktop exited before ready: {process.returncode}")
            time.sleep(0.25)
        raise TimeoutError("desktop did not report authenticated backend readiness")
    finally:
        if process.poll() is None:
            # This exact PID was created above; /T cannot affect an unrelated
            # user process and avoids leaving a temporary loopback backend alive.
            subprocess.run(
                ["taskkill", "/pid", str(process.pid), "/t", "/f"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        if args.keep_state:
            print(f"Private isolated QA state preserved: {sandbox}")
        else:
            shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
