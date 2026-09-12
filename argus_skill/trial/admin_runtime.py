"""Preserve an existing local workspace while enrolling it as an ordinary trial."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

from . import CLIENT_MODEL
from .copilot import BASE_URL, HEADERS, Copilot
from .secrets import Vault, write_private


def configure_provider(root: Path, agent_bin: Path, vault: Vault, *, tenant: str | None = None,
                       model_port: int = 18765, training_socket: Path | None = None) -> None:
    from ..core.knob_store import write_persisted_knobs
    from ..core.knobs import KNOBS

    if not agent_bin.is_file() or not os.access(agent_bin, os.X_OK):
        raise ValueError("The configured Argus-Pi executable is unavailable")
    if tenant and training_socket is None:
        raise ValueError("An enrolled workspace requires a training socket")
    token = vault.credential(tenant) if tenant else vault.read()
    agent_dir = root / ("argus-pi" if tenant else "argus-pi-admin")
    provider = {
        "baseUrl": f"http://127.0.0.1:{model_port}/v1" if tenant else BASE_URL,
        "api": "openai-completions" if tenant else "openai-responses",
        "apiKey": token if tenant else "$ARGUS_ADMIN_PROVIDER_TOKEN",
        "models": [{"id": CLIENT_MODEL, "reasoning": True}],
    }
    if not tenant:
        provider["headers"] = {**HEADERS, "X-Initiator": "agent"}
        provider["models"][0]["compat"] = {"supportsStrictMode": True}
    write_private(agent_dir / "models.json", json.dumps({"providers": {"argus": provider}}).encode())
    knobs = {
        "ARGUS_SKILL_RUNNER_BACKEND": "pi",
        "ARGUS_SKILL_LIFE_BACKEND": "pi",
        "ARGUS_SKILL_RUNNER_BIN": str(agent_bin),
        "ARGUS_SKILL_BACKEND_AUTH_MODE": "subscription_cli",
        "ARGUS_SKILL_PI_PROVIDER": "argus",
        "ARGUS_SKILL_COPILOT_TRIAL": "0",
        **{knob.name: CLIENT_MODEL for knob in KNOBS if knob.name.endswith("_MODEL")},
    }
    for role in ("ENGINEER", "REVIEWER", "PLANNER", "MANAGER", "SUPERVISOR", "CURATOR"):
        knobs[f"ARGUS_SKILL_{role}_BACKEND"] = "pi"
        knobs[f"ARGUS_SKILL_{role}_RUNNER_BIN"] = str(agent_bin)
    os.environ.update({
        **knobs,
        "PI_CODING_AGENT_DIR": str(agent_dir),
        "PI_HARNESS_PROFILE": "argus", "PI_OFFLINE": "1",
    })
    if tenant:
        os.environ.pop("ARGUS_ADMIN_PROVIDER_TOKEN", None)
        os.environ.update({"ARGUS_TRIAL_HARNESS": "argus-pi",
                           "ARGUS_TRAINING_BRIDGE_SOCKET": str(training_socket),
                           "ARGUS_SKILL_PI_SESSION_DIR": str(root / "pi-sessions")})
    else:
        os.environ["ARGUS_ADMIN_PROVIDER_TOKEN"] = token
    if not write_persisted_knobs(knobs):
        raise RuntimeError("Could not persist the administrator provider configuration")


def main() -> None:
    import uvicorn

    from .web_portal import Settings

    parser = argparse.ArgumentParser()
    parser.add_argument("--portal-config", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--pi-bin", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8896)
    parser.add_argument("--tenant", help="Existing workspace's ordinary invitation account, e.g. trial-11")
    parser.add_argument("--uds", type=Path, help="Private workspace socket used by the portal and capture bridge")
    parser.add_argument("--model-socket", type=Path, help="The same metered model socket used by invitation accounts")
    parser.add_argument("--model-port", type=int, default=18765)
    args = parser.parse_args()
    os.umask(0o077)
    settings = Settings.load(args.portal_config)
    root = args.state_dir.resolve(strict=True)
    os.chdir(args.workdir.resolve(strict=True))
    source = Path(__file__).resolve().parents[2]
    os.environ.update({
        "ARGUS_SKILL_HOME": str(root), "ARGUS_SKILL_SOURCE_ROOT": str(source),
        "ARGUS_SKILL_PYTHON": sys.executable, "PYTHONPATH": str(source),
    })
    vault = Vault(settings.key_file, settings.state_dir / "github-token.enc")

    if args.tenant:
        if args.tenant not in settings.tenants or args.uds is None or args.model_socket is None:
            parser.error("An enrolled workspace requires a configured tenant, --uds and --model-socket")
        endpoint = settings.tenants[args.tenant]
        if endpoint.uds != str(args.uds.resolve()):
            parser.error("The workspace socket must match its configured tenant endpoint")
        args.uds.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        configure_provider(root, args.pi_bin.resolve(strict=True), vault, tenant=args.tenant,
                           model_port=args.model_port, training_socket=args.uds.parent / "training.sock")
        from ..webapi.server import create_app
        from .socket_forward import start_forward

        forward = start_forward(args.model_port, str(args.model_socket))
        try:
            uvicorn.run(create_app(global_root=root, auth_token=endpoint.token),
                        uds=str(args.uds), access_log=False, proxy_headers=False,
                        timeout_graceful_shutdown=15)
        finally:
            forward.terminate()
            forward.wait(timeout=10)
        return

    if settings.admin is None:
        parser.error("Use --tenant to run the enrolled workspace")

    async def verify_provider():
        async with httpx.AsyncClient(timeout=20) as client:
            await Copilot(client, vault).verify()

    asyncio.run(verify_provider())
    configure_provider(root, args.pi_bin.resolve(strict=True), vault)
    from ..webapi.server import create_app

    uvicorn.run(create_app(global_root=root, auth_token=settings.admin.token),
                host="127.0.0.1", port=args.port, access_log=False,
                proxy_headers=False, timeout_graceful_shutdown=15)


if __name__ == "__main__":
    main()
