"""Recover a tenant's failed loopback relays without restarting its workspace."""
from __future__ import annotations

import argparse
import json
import logging
import socket
import subprocess
import time
from pathlib import Path

from .secrets import write_private

LOG = logging.getLogger(__name__)
PORTS = {18765: "/meter/gateway.sock", 18766: "/compute/compute.sock", 3128: "/egress/egress.sock"}
def probe_script() -> str:
    return (
        "import json,socket\n"
        "result={}\n"
        "for port in (18765,18766,3128):\n"
        " with socket.socket() as stream:\n"
        "  stream.settimeout(1)\n"
        "  result[str(port)]=stream.connect_ex(('127.0.0.1',port))==0\n"
        "print(json.dumps(result))\n"
    )


class Guardian:
    def __init__(self, root: Path):
        self.root = root
        self.children: dict[tuple[str, int], subprocess.Popen] = {}
        source = Path(__file__).with_name("socket_forward.py").read_bytes()
        for number in range(1, 11):
            bootstrap = root / "tenants" / f"trial-{number:02}" / "bootstrap"
            if not (bootstrap / "runtime.json").is_file():
                raise RuntimeError(f"Missing tenant bootstrap: {bootstrap}")
            write_private(bootstrap / "socket_forward.py", source)

    def inspect_tenant(self, tenant: str) -> None:
        web_socket = self.root / "tenants" / tenant / "run/web.sock"
        with socket.socket(socket.AF_UNIX) as stream:
            stream.settimeout(1)
            if stream.connect_ex(str(web_socket)) != 0:
                return
        name = f"argus-web-{tenant}"
        inspected = subprocess.run(
            ["docker", "inspect", "--format", '{{index .Config.Labels "argus.web.tenant"}}', name],
            capture_output=True, text=True, timeout=10, check=True,
        )
        if inspected.stdout.strip() != tenant:
            raise RuntimeError(f"Container ownership mismatch: {name}")
        result = subprocess.run(
            ["docker", "exec", name, "python", "-c", probe_script()],
            capture_output=True, text=True, timeout=15, check=True,
        )
        healthy = json.loads(result.stdout)
        if (
            not isinstance(healthy, dict) or set(healthy) != {str(port) for port in PORTS}
            or any(type(value) is not bool for value in healthy.values())
        ):
            raise ValueError(f"Invalid relay health response: {name}")
        for port, path in PORTS.items():
            key = (tenant, port)
            child = self.children.get(key)
            if child is not None and child.poll() is not None:
                LOG.warning("Recovery relay exited for %s:%s with status %s", tenant, port, child.returncode)
                del self.children[key]
            if healthy[str(port)] is True or key in self.children:
                continue
            LOG.warning("Recovering failed relay for %s:%s", tenant, port)
            self.children[key] = subprocess.Popen([
                "docker", "exec", name, "python", "/bootstrap/socket_forward.py", str(port), path,
            ])

    def tick(self) -> None:
        for number in range(1, 11):
            tenant = f"trial-{number:02}"
            try:
                self.inspect_tenant(tenant)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                LOG.error("Relay inspection failed for %s: %s", tenant, type(exc).__name__)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    guardian = Guardian(args.root.resolve())
    while True:
        guardian.tick()
        if args.once:
            return
        time.sleep(30)


if __name__ == "__main__":
    main()
