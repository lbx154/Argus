"""Run the existing administrator workspace on the hosted Argus-Pi provider."""
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


def configure_provider(root: Path, agent_bin: Path, vault: Vault) -> None:
    from ..core.knob_store import write_persisted_knobs
    from ..core.knobs import KNOBS

    if not agent_bin.is_file() or not os.access(agent_bin, os.X_OK):
        raise ValueError("The configured Argus-Pi executable is unavailable")
    token = vault.read()
    agent_dir = root / "argus-pi-admin"
    write_private(agent_dir / "models.json", json.dumps({"providers": {"argus": {
        "baseUrl": BASE_URL,
        "api": "openai-responses",
        "apiKey": "$ARGUS_ADMIN_PROVIDER_TOKEN",
        "headers": {**HEADERS, "X-Initiator": "agent"},
        "models": [{
            "id": CLIENT_MODEL, "reasoning": True,
            "compat": {"supportsStrictMode": True},
        }],
    }}}).encode())
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
        **knobs, "ARGUS_ADMIN_PROVIDER_TOKEN": token,
        "PI_CODING_AGENT_DIR": str(agent_dir),
        "PI_HARNESS_PROFILE": "argus", "PI_OFFLINE": "1",
    })
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
