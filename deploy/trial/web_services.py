"""Install only this deployment's user services; never touch existing Argus units."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--portal-port", type=int, default=8898)
    args = parser.parse_args()
    root = args.root.resolve()
    source = Path(__file__).resolve().parents[2]
    executable = sys.executable
    units = Path.home() / ".config/systemd/user"
    units.mkdir(parents=True, exist_ok=True)
    commands = {
        "argus-web-trial-meter": (
            f"-m argus_skill.trial.web_admin serve-meter --root {root}"
        ),
        "argus-web-trial-egress": (
            f"-m argus_skill.trial.egress --uds {root}/egress-socket/egress.sock"
        ),
        "argus-web-trial-compute": (
            f"-m argus_skill.trial.compute --config {root}/compute.json "
            f"--uds {root}/compute-socket/compute.sock"
        ),
        "argus-web-trial-relay-guardian": (
            f"-m argus_skill.trial.relay_guardian --root {root}"
        ),
        "argus-web-trial-portal": (
            f"-m argus_skill.trial.web_portal --config {root}/portal.json "
            f"--analytics-config {root}/analytics.json "
            f"--host 127.0.0.1 --port {args.portal_port}"
        ),
    }
    for name, command in commands.items():
        content = (
            f"[Unit]\nDescription={name}\nAfter=network.target\n\n"
            "[Service]\nType=simple\nUMask=0077\n"
            f"WorkingDirectory={source}\nEnvironment=PYTHONPATH={source}\n"
            f"ExecStart={executable} {command}\n"
            "Restart=on-failure\nRestartSec=5\nTimeoutStopSec=30\n"
            "NoNewPrivileges=yes\n\n[Install]\nWantedBy=default.target\n"
        )
        if name == "argus-web-trial-portal":
            content = content.replace("Restart=on-failure\n", (
                "ExecStartPost=/usr/bin/curl --retry 30 --retry-delay 1 --retry-connrefused "
                "--fail --silent --show-error --output /dev/null "
                f"http://127.0.0.1:{args.portal_port}/invite\nRestart=on-failure\n"
            ))
        path = units / f"{name}.service"
        path.write_text(content)
        path.chmod(0o600)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", *commands], check=True)
    print("Installed private service units; starting them is a separate rollout step")


if __name__ == "__main__":
    os.umask(0o077)
    main()
