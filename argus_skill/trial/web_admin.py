"""Operator-only provisioning of the separate, ten-account hosted web trial."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import CLIENT_MODEL
from .admin import issue_keys
from .secrets import Vault, write_private

DEFAULT_WEB_IMAGE = "argus-web-trial:pi-data-20260911-r6"


def initialize(root: Path, admin_token_file: Path, admin_url: str) -> None:
    from cryptography.fernet import Fernet

    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    for directory in ("meter", "secrets", "compute", "model-socket",
                      "compute-socket", "egress-socket", "volumes"):
        (root / directory).mkdir(exist_ok=True, mode=0o700)
    master = root / "secrets/master.key"
    if not master.exists():
        if (root / "meter/usage.sqlite3").exists():
            raise ValueError("Restore the missing master key; existing allowances must not be reset")
        write_private(master, Fernet.generate_key())
    vault = Vault(master, root / "meter/github-token.enc")
    issue_keys(vault, root / "meter", root / "invitations.json")
    admin_access = root / "admin-access.json"
    if not admin_access.exists():
        write_private(admin_access, json.dumps({
            "admin_login_token": secrets.token_urlsafe(48),
        }).encode())
    portal = {
        "state_dir": str(root / "meter"),
        "key_file": str(master),
        "token_limit": None,
        "tenants": {},
        "admin": {"url": admin_url, "token": admin_token_file.read_text().strip()},
        "admin_login_token": json.loads(admin_access.read_text())["admin_login_token"],
        "compute_url": "http://localhost",
        "compute_uds": str(root / "compute-socket/compute.sock"),
    }
    frontend = Path(__file__).resolve().parents[2] / "frontend/web/dist"
    if (frontend / "index.html").is_file():
        portal["frontend_dir"] = str(frontend)
    compute = {
        "state_dir": str(root / "compute"),
        "trial_db": str(root / "meter/usage.sqlite3"),
        "tenants": {}, "image": "argus-web-compute:20260911",
        "uid": os.getuid(), "gid": os.getgid(),
        "cpu_limit": 120, "memory_gib": 900,
        "interactive_memory_gib": 320,
        "gpu_devices": [0, 1, 2, 3], "gpu_hours_per_tenant": 200,
        "egress_socket_dir": str(root / "egress-socket"),
        "cgroup_parent": "argus-trial.slice",
    }
    for number in range(1, 11):
        key = f"trial-{number:02d}"
        tenant = root / "tenants" / key
        for part in ("bootstrap", "run", "data"):
            (tenant / part).mkdir(parents=True, exist_ok=True, mode=0o700)
        bootstrap = tenant / "bootstrap/runtime.json"
        if not bootstrap.exists():
            write_private(bootstrap, json.dumps({
                "api_key": vault.credential(key),
                "web_token": secrets.token_urlsafe(32),
                "name": f"Argus {key}", "tenant_id": key,
            }).encode())
        config = json.loads(bootstrap.read_text())
        portal["tenants"][key] = {
            "url": "http://localhost", "uds": str(tenant / "run/web.sock"),
            "token": config["web_token"],
        }
        compute["tenants"][key] = {"data_dir": str(tenant / "data")}
    write_private(root / "portal.json", json.dumps(portal, indent=2).encode())
    write_private(root / "compute.json", json.dumps(compute, indent=2).encode())
    analytics = root / "analytics.json"
    if not analytics.exists():
        write_private(analytics, json.dumps({
            "state_dir": str(root / "analytics"),
            "trial_db": str(root / "meter/usage.sqlite3"),
            "compute_db": str(root / "compute/compute.sqlite3"),
            "notice_version": "operator-analytics-v1",
            "retention_days": 30,
            "tenants": {
                key: {
                    "data_dir": value["data_dir"],
                    "internal_test": True,
                }
                for key, value in compute["tenants"].items()
            },
        }, indent=2).encode())
    print(f"Private invitations and service configuration saved under {root}")


def prepare_tenant_directory(data: Path, key: str, uid: int, gid: int) -> None:
    """Anchor root writes to open directories, never tenant-controlled symlinks."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd = os.open(data, flags)
    try:
        os.fchown(root_fd, uid, gid)
        os.fchmod(root_fd, 0o700)
        marker_fd = os.open(
            ".tenant-volume", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600, dir_fd=root_fd,
        )
        try:
            if not stat.S_ISREG(os.fstat(marker_fd).st_mode):
                raise ValueError(f"Tenant storage identity is not a regular file: {data}")
            existing = os.read(marker_fd, 128)
            if existing and existing.strip() != key.encode():
                raise ValueError(f"Unexpected tenant storage identity at {data}")
            os.ftruncate(marker_fd, 0)
            os.lseek(marker_fd, 0, os.SEEK_SET)
            os.write(marker_fd, key.encode())
            os.fchown(marker_fd, uid, gid)
        finally:
            os.close(marker_fd)
        for part in ("home", "workspace"):
            try:
                os.mkdir(part, mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
            directory_fd = os.open(part, flags, dir_fd=root_fd)
            try:
                os.fchown(directory_fd, uid, gid)
                if part != "workspace":
                    continue
                try:
                    file_fd = os.open(
                        "AGENTS.md", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600, dir_fd=directory_fd,
                    )
                except FileExistsError:
                    continue
                with os.fdopen(file_fd, "w") as handle:
                    os.fchown(handle.fileno(), uid, gid)
                    handle.write(
                        "# Argus hosted research workspace\n\n"
                        "This workspace and its chat history belong only to this invitation account.\n"
                        "Run GPU experiments and large CPU/memory jobs through the shared queue:\n\n"
                        "    python -m argus_skill.trial.compute_client submit --gpus 1 "
                        "--cpus 8 --memory-gib 32 --timeout 3600 -- python experiment.py\n\n"
                        "Use the same CLI's status/jobs/logs/wait/cancel commands to inspect work.\n"
                        "Check status before planning resource use. One account may borrow available "
                        "pool capacity; GPU jobs queue fairly and have a 200 GPU-hour lifetime allowance.\n"
                        "Do not assume a submitted or queued experiment has run. Wait for completion, "
                        "inspect real outputs, compare metrics, and preserve reproducible artifacts.\n"
                        "Autoresearch loops must be bounded by remaining model tokens, GPU hours, "
                        "job timeouts, and the operator's objective. Never invent experiment results.\n"
                        "HTTPS downloads use the configured proxy; private networks and the host "
                        "are intentionally unreachable. Do not disable TLS verification.\n"
                        "Do not display or copy the private trial profile or invitation key into artifacts.\n"
                    )
            finally:
                os.close(directory_fd)
    finally:
        os.close(root_fd)


def mount_storage(root: Path, uid: int, gid: int) -> None:
    """Bound each tenant's disk writes with its own persistent ext4 filesystem."""
    if os.geteuid() != 0:
        raise ValueError("Mounting trial storage requires operator root permission")
    failures = []
    for number in range(1, 11):
        key = f"trial-{number:02d}"
        image = root / "volumes" / f"{key}.ext4"
        data = root / "tenants" / key / "data"
        if not image.exists():
            if any(data.iterdir()):
                raise ValueError(f"Refusing to hide pre-existing tenant data: {data}")
            with image.open("xb") as handle:
                handle.truncate(100 * 1024**3)
            image.chmod(0o600)
            subprocess.run(["mkfs.ext4", "-q", "-m", "0", str(image)], check=True)
        if not os.path.ismount(data):
            subprocess.run(["mount", "-o", "loop,nodev,nosuid", str(image), str(data)], check=True)
        try:
            prepare_tenant_directory(data, key, uid, gid)
        except (OSError, ValueError) as exc:
            failures.append(f"{key}: {exc}")
    if failures:
        raise RuntimeError("Unsafe tenant storage entries rejected: " + "; ".join(failures))
    print("Ten independent 100-GiB tenant filesystems mounted; existing data preserved")


def _tenant_number(number) -> None:
    if type(number) is not int or not 1 <= number <= 10:
        raise ValueError("Invalid tenant number")


def _inspect_container(name: str) -> dict | None:
    """Return the container's inspect record, or None only when it truly is absent."""
    result = subprocess.run(
        ["docker", "container", "inspect", name], capture_output=True, text=True,
    )
    if result.returncode == 0:
        return json.loads(result.stdout)[0]
    # Failure to reach Docker is not proof that the container doesn't exist.
    if "No such container" not in result.stderr and "No such object" not in result.stderr:
        raise RuntimeError(result.stderr.strip())
    return None


def _run_command(root: Path, key: str, tenant: Path, image: str) -> list[str]:
    """The full isolation-flag argv that launches one tenant workspace container."""
    name = f"argus-web-{key}"
    command = [
        "docker", "run", "-d", "--name", name,
        "--label", f"argus.web.tenant={key}", "--restart", "unless-stopped",
        "--cgroup-parent", "argus-trial.slice",
        "--network", "none", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--pids-limit", "-1",
        "--cpus", "8", "--memory", "32g", "--memory-swap", "32g",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=1g,mode=1777",
        "--shm-size", "256m", "--log-opt", "max-size=10m", "--log-opt", "max-file=3",
        "--mount", f"type=bind,src={tenant / 'data'},dst=/tenant",
        "--mount", f"type=bind,src={tenant / 'run'},dst=/run/argus-web",
        "--mount", f"type=bind,src={tenant / 'bootstrap'},dst=/bootstrap,readonly",
    ]
    for source, destination in (
        ("model-socket", "meter"), ("compute-socket", "compute"), ("egress-socket", "egress"),
    ):
        command += ["--mount", f"type=bind,src={root / source},dst=/{destination},readonly"]
    command.append(image)
    return command


def _launch(root: Path, key: str, tenant: Path, image: str) -> None:
    subprocess.run(_run_command(root, key, tenant, image), check=True)
    # Docker can normalize -1 to null on creation and inherit a daemon limit.
    subprocess.run(["docker", "update", "--pids-limit", "-1", f"argus-web-{key}"], check=True)


def start_containers(root: Path, *, numbers=range(1, 11),
                     image: str = DEFAULT_WEB_IMAGE) -> None:
    for number in numbers:
        _tenant_number(number)
        key = f"trial-{number:02d}"
        tenant = root / "tenants" / key
        if not os.path.ismount(tenant / "data"):
            raise ValueError(f"Tenant filesystem is not mounted: {key}")
        name = f"argus-web-{key}"
        data = _inspect_container(name)
        if data is not None:
            if data["Config"].get("Labels", {}).get("argus.web.tenant") != key:
                raise ValueError(f"Container name is already owned by something else: {name}")
            # Existing containers keep their image; upgrading them is `roll-containers`.
            subprocess.run(["docker", "update", "--pids-limit", "-1", name], check=True)
            subprocess.run(["docker", "start", name], check=True)
            continue
        _launch(root, key, tenant, image)


def roll_containers(root: Path, *, numbers=range(1, 11),
                    image: str = DEFAULT_WEB_IMAGE) -> dict[str, str]:
    """Recreate every tenant on ``image`` as one version, keeping a rollback each.

    Unlike ``start-containers`` this replaces running containers, so a tenant's
    active tasks are interrupted; the previous container is drained and retained
    as ``<name>-rollback`` for recovery. A tenant already on ``image`` is left
    untouched, so the roll is idempotent.
    """
    results: dict[str, str] = {}
    for number in numbers:
        _tenant_number(number)
        key = f"trial-{number:02d}"
        tenant = root / "tenants" / key
        if not os.path.ismount(tenant / "data"):
            raise ValueError(f"Tenant filesystem is not mounted: {key}")
        name = f"argus-web-{key}"
        data = _inspect_container(name)
        if data is None:
            _launch(root, key, tenant, image)
            results[key] = "created"
            continue
        if data["Config"].get("Labels", {}).get("argus.web.tenant") != key:
            raise ValueError(f"Container name is already owned by something else: {name}")
        if data["Config"]["Image"] == image:
            results[key] = "current"
            continue
        rollback = f"{name}-rollback"
        previous = _inspect_container(rollback)
        if previous is not None:
            if previous["Config"].get("Labels", {}).get("argus.web.tenant") != key:
                raise ValueError(f"Rollback name is already owned by something else: {rollback}")
            subprocess.run(["docker", "rm", "-f", rollback], check=True)
        # Drain the running container, then set it aside under the rollback name.
        subprocess.run(["docker", "stop", "--time", "30", name], check=True)
        subprocess.run(["docker", "rename", name, rollback], check=True)
        try:
            _launch(root, key, tenant, image)
        except subprocess.CalledProcessError:
            # Recreate failed: discard the half-made container and restore service.
            if _inspect_container(name) is not None:
                subprocess.run(["docker", "rm", "-f", name], check=True)
            subprocess.run(["docker", "rename", rollback, name], check=True)
            subprocess.run(["docker", "start", name], check=True)
            results[key] = "failed-rolled-back"
            raise
        results[key] = "upgraded"
    return results


def _release_version(source: Path, *, allow_dirty: bool = False) -> str:
    """The single version stamp for a release: the source checkout's commit."""
    sha = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", str(source), "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if dirty and not allow_dirty:
        raise ValueError("Refusing to release a dirty checkout; commit first or pass --allow-dirty")
    return sha + ("+dirty" if dirty else "")


def release(root: Path, *, image: str, source: Path, numbers=range(1, 11),
            allow_dirty: bool = False, restart_portal: bool = True) -> dict:
    """Ship frontend, portal and tenant image as one version, backend first.

    The tenant containers roll to ``image`` before the portal is pointed at this
    source's built frontend and restarted, so the frontend never advertises a
    route the running backend lacks. A ``release.json`` manifest records the one
    version tying the three pieces together, for audit and rollback.
    """
    version = _release_version(source, allow_dirty=allow_dirty)
    rolled = roll_containers(root, image=image, numbers=numbers)
    manifest = {
        "version": version,
        "image": image,
        "source": str(source),
        "frontend_dir": None,
        "rolled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tenants": rolled,
    }
    frontend = source / "frontend/web/dist"
    if (frontend / "index.html").is_file():
        portal_path = root / "portal.json"
        portal = json.loads(portal_path.read_text())
        portal["frontend_dir"] = str(frontend)
        write_private(portal_path, json.dumps(portal, indent=2).encode())
        manifest["frontend_dir"] = str(frontend)
    write_private(root / "release.json", json.dumps(manifest, indent=2).encode())
    if restart_portal:
        subprocess.run(["systemctl", "--user", "restart", "argus-web-trial-portal"], check=True)
    print(f"Released {version} on {image}; tenants: " +
          ", ".join(f"{key}={state}" for key, state in rolled.items()))
    return manifest


def serve_meter(root: Path) -> None:
    import uvicorn

    from .gateway import Settings, create_app

    uvicorn.run(
        create_app(Settings(
            root / "meter", root / "secrets/master.key",
            model=CLIENT_MODEL, token_limit=None,
        )),
        uds=str(root / "model-socket/gateway.sock"), access_log=False,
        proxy_headers=False, timeout_graceful_shutdown=15,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=(
        "init", "mount-storage", "start-containers", "roll-containers", "release", "serve-meter",
    ))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--admin-token-file", type=Path)
    parser.add_argument("--admin-url", default="http://127.0.0.1:8896")
    parser.add_argument("--image", default=DEFAULT_WEB_IMAGE,
                        help="Image for new workspace containers; existing containers keep their "
                             "image unless rolled with roll-containers/release")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[2],
                        help="Checkout whose commit stamps the release and supplies the frontend")
    parser.add_argument("--allow-dirty", action="store_true",
                        help="Permit releasing a source checkout with uncommitted changes")
    parser.add_argument("--no-restart-portal", action="store_true",
                        help="Roll containers and write the manifest without restarting the portal")
    parser.add_argument("--uid", type=int, default=os.getuid())
    parser.add_argument("--gid", type=int, default=os.getgid())
    args = parser.parse_args()
    root = args.root.resolve()
    os.umask(0o077)
    if args.command == "init":
        if args.admin_token_file is None:
            parser.error("--admin-token-file is required for init")
        initialize(root, args.admin_token_file, args.admin_url)
    elif args.command == "mount-storage":
        mount_storage(root, args.uid, args.gid)
    elif args.command == "start-containers":
        start_containers(root, image=args.image)
    elif args.command == "roll-containers":
        roll_containers(root, image=args.image)
    elif args.command == "release":
        release(root, image=args.image, source=args.source.resolve(),
                allow_dirty=args.allow_dirty, restart_portal=not args.no_restart_portal)
    else:
        serve_meter(root)


if __name__ == "__main__":
    main()
