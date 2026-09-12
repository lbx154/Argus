"""Activate a trusted offline PLATON copy in one tenant's private resources."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import portalocker

from ..core import plugin_manager as manager
from ..core import plugin_runtime as runtime
from ..core.process_identity import capture_process_identity


ACTIVATE = """
import json
import sys
from argus_crystalpilot import dependencies

payload = json.load(sys.stdin)
dependencies._register(payload["root"], "platon", payload["record"])
health = dependencies.health(payload["root"])
platon = next(row for row in health["components"] if row["id"] == "platon")
if platon["status"] != "ready":
    raise RuntimeError(platon["detail"])
print(json.dumps(health))
"""


def provision(root, directory, record):
    root = manager.host_root(root)
    plugin_root = manager.install_root(root) / "crystalpilot"
    resources = plugin_root / "resources"
    software = resources / "software"
    directory = Path(directory).resolve(strict=True)
    if not directory.is_relative_to(software) or directory == software:
        raise ValueError("PLATON must be copied into this tenant's resources/software directory")
    for path in directory.rglob("*"):
        if path.is_symlink() and not path.resolve().is_relative_to(directory):
            raise ValueError("PLATON copy contains a symlink outside its private directory")
    executable = directory / "platon"
    if runtime.sha256(executable) != record["sha256"]:
        raise ValueError("PLATON executable does not match its trusted source record")
    source = Path(record["path"]).parent
    libraries = [
        (directory / Path(path).relative_to(source)).resolve(strict=True)
        for path in record["libs"]
    ]
    if not libraries or any(not path.is_relative_to(directory) for path in libraries):
        raise ValueError("PLATON libraries must remain inside its private directory")
    relocated = {
        "path": str(executable),
        "libs": [str(path) for path in libraries],
        "environment": {"CRYSTALPILOT_PLATON": str(executable)},
        "version": record["version"],
        "recipe": record["recipe"],
        "sha256": record["sha256"],
    }
    with portalocker.Lock(str(plugin_root / "manage.lock"), timeout=5):
        operation = manager.read_json(plugin_root / "operation.json")
        if operation.get("status") == "running" and manager._job_alive(root, "crystalpilot", operation):
            raise manager.PluginError("CrystalPilot already has an active environment operation")
        manager._busy("crystalpilot", root)
        plugin = manager.load_plugin("crystalpilot", root)
        if plugin is None:
            raise manager.PluginError("CrystalPilot must be installed and enabled")
        operation = {
            "status": "running", "action": "configure", "progress": "Configuring PLATON",
            "started": time.time(), "pid": os.getpid(),
            "identity": capture_process_identity(os.getpid()),
        }
        manager.write_json(plugin_root / "operation.json", operation)
        try:
            plugin.shutdown_workers()
            python = manager.state_entry("crystalpilot", root)["python"]
            env = runtime.clean_env()
            # Dependency probes create scratch projects; keep them in this tenant.
            env.update(TMPDIR=str(resources), TMP=str(resources), TEMP=str(resources))
            output = runtime.run(
                [python, "-I", "-c", ACTIVATE],
                env=env,
                input=json.dumps({"root": str(resources), "record": relocated}),
                timeout=600,
            )
            health = json.loads(output)
            operation.update(status="completed", progress="PLATON ready")
            return health
        except BaseException as exc:
            operation.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            operation["completed"] = time.time()
            manager.write_json(plugin_root / "operation.json", operation)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="this tenant's Argus home")
    parser.add_argument("--directory", required=True, help="copied PLATON resource directory")
    parser.add_argument("--record", required=True, type=Path, help="trusted source PLATON software.json entry")
    args = parser.parse_args(argv)
    health = provision(args.root, args.directory, json.loads(args.record.read_text()))
    print(json.dumps(health, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
