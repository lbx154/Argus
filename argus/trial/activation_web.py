"""Operator tooling for a USD-metered activation-code web deployment on one host.

This reuses the hosted web trial as it is: the trial gateway (hashed codes and
the USD ledger), ``web_portal`` (code login and per-code routing), and
``web_runtime`` (one Argus per code, whose trial client sends every model call
to the gateway under that code), plus the egress proxy. Only the container
engine is replaced by bubblewrap, so a host without root-mounted volumes can
run it. Each code's runtime sees only its own files, has no network except the
gateway and egress sockets, and never sees the operator's provider login.

Layout under ``--root`` (keep it private and back up ``secrets/master.key``)::

    deployment.json            source/venv/Copilot paths fixed at init
    secrets/master.key         derives codes and encrypts the provider login
    meter/usage.sqlite3        gateway ledger: code hashes, USD allowance, spend
    prices.json                gateway price table (USD per million tokens)
    model-socket/gateway.sock  gateway, mounted read-only into runtimes
    egress-socket/egress.sock  public HTTPS egress proxy
    portal.json                web_portal configuration, rewritten by provision
    tenants/trial-NN/          bootstrap/, run/web.sock and data/ per code
    codes/                     activation codes, written once at issuance

Typical use::

    python -m argus.trial.activation_web init --root R --source S --venv V --copilot-bin B
    argus-trial-server import-copilot-login --state-dir R/meter --key-file R/secrets/master.key
    python -m argus.trial.activation_web issue --root R --count 5 --usd 5 --label beta
    python -m argus.trial.activation_web install-units --root R --port 8988
    python -m argus.trial.activation_web list --root R
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sys
import time
from pathlib import Path

from .secrets import Vault, write_private

# The default upstream model's tariff as reported by the provider's own
# per-request token_details (cost_per_batch per 1M tokens). Reservations need
# an upper bound; confirm it before enabling another model.
DEFAULT_PRICES = {"gpt-5.5": {"input": 5.0, "cache_read": 0.5, "cache_write": 5.0, "output": 30.0}}
SANDBOX_UID, SANDBOX_GID = 1000, 100


def _paths(root: Path) -> dict[str, Path]:
    return {
        "key": root / "secrets/master.key", "meter": root / "meter",
        "model": root / "model-socket", "egress": root / "egress-socket",
        "tenants": root / "tenants", "codes": root / "codes",
    }


def load_deployment(root: Path) -> dict:
    return json.loads((root / "deployment.json").read_text())


def initialize(root: Path, *, source: Path, venv: Path, copilot_bin: Path, copilot_pkg: Path | None,
               public_origin: str | None, secure_cookie: bool = True) -> None:
    from cryptography.fernet import Fernet

    paths = _paths(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    for name in ("meter", "model", "egress", "tenants", "codes"):
        paths[name].mkdir(parents=True, exist_ok=True, mode=0o700)
    if not paths["key"].exists():
        if (paths["meter"] / "usage.sqlite3").exists():
            raise ValueError("Restore the missing master key; existing codes and spend must not be reset")
        write_private(paths["key"], Fernet.generate_key())
    if not (root / "prices.json").exists():
        write_private(root / "prices.json", json.dumps(DEFAULT_PRICES, indent=2).encode())
    for path in (source, venv / "bin/python", copilot_bin):
        if not path.exists():
            raise ValueError(f"Missing deployment input: {path}")
    deployment = {
        "source": str(source.resolve()), "venv": str(venv.absolute()),
        "copilot_bin": str(copilot_bin.resolve()),
        "copilot_pkg": str(copilot_pkg.resolve()) if copilot_pkg else None,
        "public_origin": public_origin, "secure_cookie": secure_cookie,
    }
    write_private(root / "deployment.json", json.dumps(deployment, indent=2).encode())
    # The ledger must exist before the portal can load; creating it issues nothing.
    from .store import Store

    Store(paths["meter"] / "usage.sqlite3", key_limit=100)
    print(f"Activation-code deployment initialized under {root}. Next: import the provider login.")


def provision(root: Path) -> dict:
    """Give every issued code its own runtime directory and portal route."""
    from .store import Store

    paths = _paths(root)
    deployment = load_deployment(root)
    vault = Vault(paths["key"], paths["meter"] / "github-token.enc")
    codes = Store(paths["meter"] / "usage.sqlite3", key_limit=100).list_codes()
    portal = {
        "state_dir": str(paths["meter"]), "key_file": str(paths["key"]), "token_limit": None,
        "activation_codes": True, "secure_cookie": deployment["secure_cookie"], "tenants": {},
    }
    if deployment.get("public_origin"):
        portal["public_origin"] = deployment["public_origin"]
    frontend = Path(deployment["source"]) / "frontend/web/dist"
    if (frontend / "index.html").is_file():
        portal["frontend_dir"] = str(frontend)
    for code in codes:
        key = code["key_id"]
        tenant = paths["tenants"] / key
        for part in ("bootstrap", "run", "data/home", "data/workspace"):
            (tenant / part).mkdir(parents=True, exist_ok=True, mode=0o700)
        marker = tenant / "data/.tenant-volume"
        if not marker.exists():
            write_private(marker, key.encode())
        bootstrap = tenant / "bootstrap/runtime.json"
        if not bootstrap.exists():
            write_private(bootstrap, json.dumps({
                "api_key": vault.credential(key), "web_token": secrets.token_urlsafe(32),
                "name": f"Argus {code['label'] or key}", "tenant_id": key,
            }).encode())
        portal["tenants"][key] = {
            "url": "http://localhost", "uds": str(tenant / "run/web.sock"),
            "token": json.loads(bootstrap.read_text())["web_token"],
        }
    write_private(root / "portal.json", json.dumps(portal, indent=2).encode())
    return portal


def sandbox_command(root: Path, tenant: str) -> list[str]:
    """One code's runtime in user/PID/mount/IPC/UTS/network namespaces.

    The mounts mirror the hosted container: /tenant (its own data),
    /run/argus-web (its web socket), /bootstrap (read-only), /meter and /egress
    (read-only sockets). Host home, other codes, the master key and the
    operator's provider login are not mounted.
    """
    deployment = load_deployment(root)
    paths = _paths(root)
    directory = paths["tenants"] / tenant
    if not (directory / "bootstrap/runtime.json").is_file():
        raise ValueError(f"Unknown activation-code runtime: {tenant}")
    source, venv = deployment["source"], deployment["venv"]
    identity = directory / "identity"
    identity.mkdir(exist_ok=True, mode=0o700)
    write_private(identity / "passwd", f"trial:x:{SANDBOX_UID}:{SANDBOX_GID}::/tenant/home:/bin/bash\n".encode())
    write_private(identity / "group", f"users:x:{SANDBOX_GID}:\n".encode())
    args = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL",
            "--uid", str(SANDBOX_UID), "--gid", str(SANDBOX_GID), "--hostname", "argus-workspace"]
    for path in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/lib32"):
        if Path(path).exists():
            args += ["--ro-bind", path, path]
    args += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/etc"]
    for path in ("/etc/ssl", "/etc/ca-certificates", "/etc/alternatives", "/etc/ld.so.cache",
                 "/etc/localtime", "/etc/hosts", "/etc/nsswitch.conf", "/etc/gitconfig", "/etc/mime.types"):
        if Path(path).exists():
            args += ["--ro-bind", path, path]
    args += ["--ro-bind", str(identity / "passwd"), "/etc/passwd",
             "--ro-bind", str(identity / "group"), "/etc/group",
             "--tmpfs", "/usr/local", "--ro-bind", deployment["copilot_bin"], "/usr/local/bin/copilot",
             "--ro-bind", source, source, "--ro-bind", venv, venv,
             "--bind", str(directory / "data"), "/tenant",
             "--bind", str(directory / "run"), "/run/argus-web",
             "--ro-bind", str(directory / "bootstrap"), "/bootstrap",
             "--ro-bind", str(paths["model"]), "/meter",
             "--ro-bind", str(paths["egress"]), "/egress"]
    if deployment.get("copilot_pkg"):
        args += ["--ro-bind", deployment["copilot_pkg"], "/tenant/home/.cache/copilot/pkg"]
    env = {
        "HOME": "/tenant/home", "PATH": f"{venv}/bin:/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
        "TMPDIR": "/tmp", "PYTHONPATH": source, "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "ARGUS_SKILL_HOME": "/tenant/home/.argus-skill", "ARGUS_SKILL_SOURCE_ROOT": source,
        "ARGUS_SKILL_COPILOT_TRIAL": "1", "ARGUS_SKILL_RUNNER_BACKEND": "copilot",
        "ARGUS_SKILL_LIFE_BACKEND": "copilot", "ARGUS_SKILL_RUNNER_BIN": "/usr/local/bin/copilot",
        "ARGUS_SKILL_BACKEND_AUTH_MODE": "subscription_cli",
        # No host-managed plugin downloads; same plugins as an ordinary install.
        "ARGUS_PLUGINS_PREINSTALL": "",
    }
    args.append("--clearenv")
    for key, value in env.items():
        args += ["--setenv", key, value]
    return args + ["--chdir", "/tenant/workspace", "--", f"{venv}/bin/python", "-m", "argus.trial.web_runtime"]


def install_units(root: Path, *, port: int, prefix: str, unit_dir: Path) -> list[str]:
    """User services: gateway, egress, portal and one templated runtime per code."""
    deployment = load_deployment(root)
    python = f"{deployment['venv']}/bin/python"
    paths = _paths(root)
    env = f"Environment=PYTHONPATH={deployment['source']}\nEnvironment=PYTHONUNBUFFERED=1\n"
    services = {
        f"{prefix}-gateway.service": (
            f"{python} -m argus.trial.admin serve --state-dir {paths['meter']} --key-file {paths['key']} "
            f"--uds {paths['model']}/gateway.sock --prices {root}/prices.json", "2G"),
        f"{prefix}-egress.service": (f"{python} -m argus.trial.egress --uds {paths['egress']}/egress.sock", "1G"),
        f"{prefix}-portal.service": (
            f"{python} -m argus.trial.web_portal --config {root}/portal.json --host 127.0.0.1 --port {port}", "2G"),
        f"{prefix}-runtime@.service": (f"{python} -m argus.trial.activation_web run-tenant --root {root} --tenant %i", "8G"),
    }
    unit_dir.mkdir(parents=True, exist_ok=True)
    for name, (command, memory) in services.items():
        after = "" if "gateway" in name else f"After={prefix}-gateway.service\n"
        (unit_dir / name).write_text(
            f"[Unit]\nDescription=Argus activation-code web {name.removesuffix('.service')}\n{after}"
            f"[Service]\nType=simple\nWorkingDirectory={deployment['source']}\nExecStart={command}\n{env}"
            f"UMask=0077\nRestart=on-failure\nRestartSec=5\nMemoryMax={memory}\nTasksMax=1024\n"
            f"TimeoutStopSec=30\n\n[Install]\nWantedBy=default.target\n")
    return list(services)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("init", "issue", "list", "provision", "run-tenant", "install-units"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source", type=Path, help="init: Argus checkout with a built frontend")
    parser.add_argument("--venv", type=Path, help="init: virtualenv with the trial extra")
    parser.add_argument("--copilot-bin", type=Path, help="init: standalone Copilot CLI binary")
    parser.add_argument("--copilot-pkg", type=Path, help="init: optional extracted Copilot package cache")
    parser.add_argument("--public-origin", help="init: exact public HTTPS origin of the tunnel")
    parser.add_argument("--insecure-cookie", action="store_true", help="init: local HTTP testing only")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--usd", help="issue: USD allowance per code")
    parser.add_argument("--label", default="")
    parser.add_argument("--expires", help="issue: ISO expiry date or date-time")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--tenant")
    parser.add_argument("--port", type=int, default=8988)
    parser.add_argument("--prefix", default="argus-codes")
    parser.add_argument("--unit-dir", type=Path, default=Path.home() / ".config/systemd/user")
    args = parser.parse_args()
    root = args.root.absolute()
    os.umask(0o077)
    try:
        if args.command == "init":
            if not (args.source and args.venv and args.copilot_bin):
                parser.error("init requires --source, --venv and --copilot-bin")
            initialize(root, source=args.source, venv=args.venv, copilot_bin=args.copilot_bin,
                       copilot_pkg=args.copilot_pkg, public_origin=args.public_origin,
                       secure_cookie=not args.insecure_cookie)
        elif args.command == "issue":
            from .admin import expiry_timestamp, issue_codes, usd_amount

            if args.usd is None:
                parser.error("issue requires --usd")
            paths = _paths(root)
            output = paths["codes"] / time.strftime("codes-%Y%m%dT%H%M%S.json")
            keys = issue_codes(Vault(paths["key"], paths["meter"] / "github-token.enc"), paths["meter"], output,
                               count=args.count, usd_limit=usd_amount(args.usd), label=args.label,
                               expires_at=expiry_timestamp(args.expires))
            provision(root)
            print("Start their runtimes and reload the portal: systemctl --user enable --now "
                  + " ".join(f"{args.prefix}-runtime@{key}" for key in keys)
                  + f" && systemctl --user restart {args.prefix}-portal")
        elif args.command == "list":
            from .admin import format_codes
            from .store import Store

            codes = Store(_paths(root)["meter"] / "usage.sqlite3", key_limit=100).list_codes()
            print(json.dumps(codes, indent=2) if args.json else format_codes(codes))
        elif args.command == "provision":
            print(f"{len(provision(root)['tenants'])} activation-code runtimes provisioned.")
        elif args.command == "run-tenant":
            if shutil.which("bwrap") is None:
                raise ValueError("bubblewrap (bwrap) is required to isolate activation-code runtimes")
            command = sandbox_command(root, args.tenant)
            os.execvp(command[0], command)
        else:
            names = install_units(root, port=args.port, prefix=args.prefix, unit_dir=args.unit_dir)
            print("Wrote " + ", ".join(names) + f" to {args.unit_dir}. Run systemctl --user daemon-reload.")
        return 0
    except (ValueError, OSError) as exc:
        print(f"Activation-code deployment: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
