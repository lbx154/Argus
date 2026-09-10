"""Install a real native package, configure its frozen trial, and restart its GUI.

Run only on an isolated release runner. A private smoke key arrives through the
CI secret environment and is sent to the frozen helper over stdin, never argv.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


def install(package: Path, directory: Path) -> tuple[Path, Path]:
    if sys.platform == "win32":
        subprocess.run([str(package), "/S", f"/D={directory}"], check=True, timeout=180)
        return directory / "Argus.exe", directory / "argus-backend/argus-backend.exe"
    if sys.platform != "darwin":
        raise RuntimeError("Native release smoke requires macOS or Windows")
    mount = directory.parent / "mounted-dmg"
    mount.mkdir()
    subprocess.run(["hdiutil", "attach", str(package), "-nobrowse", "-mountpoint", str(mount)],
                   check=True, stdout=subprocess.DEVNULL, timeout=90)
    try:
        subprocess.run(["ditto", str(mount / "Argus.app"), str(directory / "Argus.app")],
                       check=True, timeout=90)
    finally:
        subprocess.run(["hdiutil", "detach", str(mount)], check=True, timeout=90)
    app = directory / "Argus.app/Contents"
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app.parent)],
                   check=True, timeout=90)
    return app / "MacOS/Argus", app / "Resources/argus-backend/argus-backend"


def host_roundtrip(binary: Path, directory: Path, env: dict[str, str], key: str, token: str):
    log = directory / "logs/desktop.log"
    offset = len(log.read_text(encoding="utf-8")) if log.exists() else 0
    process = subprocess.Popen([str(binary)], cwd=binary.parent, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=sys.platform != "win32")
    try:
        deadline = time.monotonic() + 90
        stable_since = None
        while time.monotonic() < deadline:
            content = log.read_text(encoding="utf-8", errors="replace")[offset:] if log.exists() else ""
            if key in content:
                raise RuntimeError("Trial key appeared in desktop logs")
            if "backend Error:" in content or "runner preflight failed" in content:
                raise RuntimeError(content[-3000:].replace(key, "[hidden]").replace(token, "[hidden]"))
            if "backend Ready:" in content and "authenticated cockpit URL issued" in content:
                stable_since = stable_since or time.monotonic()
                if time.monotonic() - stable_since > 8:
                    settings = json.loads((directory / "settings.json").read_text())
                    with httpx.Client(
                        headers={"Authorization": "Bearer " + token},
                        timeout=10, trust_env=False,
                    ) as client:
                        response = client.get(f"http://127.0.0.1:{settings['port']}/api/projects")
                        response.raise_for_status()
                    print("Installed native GUI opened its authenticated cockpit and stayed ready.", flush=True)
                    return settings["port"]
            if process.poll() is not None:
                raise RuntimeError(f"Desktop exited before ready: {process.returncode}")
            time.sleep(0.5)
        raise RuntimeError("Native GUI did not open its authenticated cockpit: " + content[-3000:].replace(key, "[hidden]").replace(token, "[hidden]"))
    finally:
        if process.poll() is None:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/pid", str(process.pid), "/t", "/f"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            else:
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                if sys.platform != "win32":
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    args = parser.parse_args()
    key = os.environ.get("ARGUS_TRIAL_SMOKE_KEY", "").strip()
    if not key.startswith("argus_trial_") or len(key) != 76:
        raise RuntimeError("ARGUS_TRIAL_SMOKE_KEY is required for release verification")
    with httpx.Client(headers={"Authorization": "Bearer " + key, "User-Agent": "Argus/0.1.1"},
                      timeout=30, trust_env=False) as client:
        response = client.get("https://argusbot.cn/trial/status")
        response.raise_for_status()
        before = response.json()["tokens_used"]
        with tempfile.TemporaryDirectory(prefix="argus-trial-release-") as temporary:
            # macOS /var is a symlink to /private/var. Tauri deliberately rejects
            # an executable launched through any symlink; use the real bundle
            # path, as Finder does, without disabling that protection.
            root = Path(temporary).resolve()
            binary, frozen = install(args.package.resolve(), root / "installed")
            assert binary.is_file() and frozen.is_file(), "Installed native runtime is missing"
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(("ARGUS_", "COPILOT_", "GH_", "GITHUB_", "PYTHON"))
                   and "TOKEN" not in k and "API_KEY" not in k}
            env.update(ARGUS_SKILL_HOME=str(root / "argus-home"), PYTHONUTF8="1",
                       PYTHONIOENCODING="utf-8", ARGUS_DESKTOP_DISABLE_SINGLE_INSTANCE="1",
                       ARGUS_DESKTOP_DISABLE_UPDATE_CHECK="1")
            if sys.platform == "win32":
                env.update(APPDATA=str(root / "appdata"), LOCALAPPDATA=str(root / "localappdata"))
                env["PATH"] = str(Path(env.get("SystemRoot", r"C:\Windows")) / "System32")
                desktop = root / "appdata/argus-desktop"
            else:
                env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
                desktop = Path.home() / "Library/Application Support/argus-desktop"
            if desktop.exists():
                raise RuntimeError("Refusing to overwrite existing desktop state; use a clean release runner")
            try:
                run = subprocess.run([str(frozen), "-m", "argus_skill.trial.desktop"],
                                     input=json.dumps({"api_key": key}) + "\n", capture_output=True,
                                     text=True, encoding="utf-8", cwd=root, env=env, timeout=600)
                assert key not in run.stdout + run.stderr, "Trial helper leaked its key"
                assert run.returncode == 0, run.stdout + run.stderr
                events = [json.loads(line) for line in run.stdout.splitlines()]
                assert events[-1]["event"] == "complete", "Trial helper did not complete"
                runner = events[-1]["runner_bin"]
                profile = root / "argus-home/copilot-trial.json"
                assert json.loads(profile.read_text())["api_key"] == key
                if sys.platform != "win32":
                    assert profile.stat().st_mode & 0o777 == 0o600
                print("Installed frozen runtime automatically downloaded Copilot and verified the public trial.", flush=True)
                desktop.mkdir(parents=True)
                token = secrets.token_urlsafe(32)
                with socket.socket() as existing_service:
                    existing_service.bind(("127.0.0.1", 0))
                    existing_service.listen(8)
                    occupied_port = existing_service.getsockname()[1]
                    (desktop / "settings.json").write_text(json.dumps({
                        "host": "127.0.0.1", "port": occupied_port, "token": token,
                        "runnerKind": "copilot", "runnerBins": {"copilot": runner},
                        "runnerConfigured": True, "setupComplete": True, "trialMode": True,
                    }), encoding="utf-8")
                    selected_port = host_roundtrip(binary, desktop, env, key, token)
                    assert selected_port != occupied_port, "Trial did not avoid the occupied port"
                    assert host_roundtrip(binary, desktop, env, key, token) == selected_port
                    with socket.create_connection(existing_service.getsockname(), timeout=5):
                        pass
                    print("Trial preserved the existing service and reused its saved port on restart.", flush=True)
                evidence = root / "evidence.txt"
                evidence.write_text("native-installed-trial-evidence")
                prompt = f"Read {evidence} and return its exact content followed by NATIVE_TRIAL_OK."
                script = (
                    "from argus_skill.core.agent_probe import run_read_only_agent_prompt; "
                    "from argus_skill.core.knob_store import read_persisted_knobs; "
                    "from argus_skill.trial import CLIENT_MODEL; "
                    "k=read_persisted_knobs(); assert k['ARGUS_SKILL_COPILOT_TRIAL']=='1'; "
                    "assert k['ARGUS_SKILL_MODEL']=='gpt-5.5' and k['ARGUS_SKILL_ENGINEER_REASONING_EFFORT']=='high'; "
                    "r=run_read_only_agent_prompt(backend='copilot',executable=k['ARGUS_SKILL_RUNNER_BIN'],"
                    f"model=CLIENT_MODEL,run_label='native-release-smoke',prompt={prompt!r}); "
                    "assert r.ok and 'native-installed-trial-evidence' in r.output and 'NATIVE_TRIAL_OK' in r.output; "
                    "print('Persisted native local-tool round trip passed.')"
                )
                run = subprocess.run([str(frozen), "-c", script], cwd=root, env=env,
                                     capture_output=True, text=True, encoding="utf-8", timeout=180)
                assert key not in run.stdout + run.stderr, "Native worker leaked its key"
                assert run.returncode == 0, run.stdout + run.stderr
                print(run.stdout, flush=True)
            finally:
                if desktop.exists():
                    shutil.rmtree(desktop)
        after = client.get("https://argusbot.cn/trial/status").json()["tokens_used"]
        assert after > before
        print(f"Native installer, automatic trial setup, GUI restart and local tool verified; pool smoke usage increased by {after - before} tokens.")


if __name__ == "__main__":
    main()
