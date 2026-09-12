"""One network-disabled trial container; its only upstream is the metered socket."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .secrets import write_private
from .socket_forward import start_forward


def configure_provider(root: Path, config: dict) -> None:
    from ..core.knob_store import write_persisted_knobs
    from ..core.knobs import KNOBS
    from . import CLIENT_MODEL, REASONING_EFFORT

    backend = os.environ.get("ARGUS_TRIAL_HARNESS", "copilot")
    if backend not in {"copilot", "argus-pi"}:
        raise ValueError("Unsupported hosted harness")
    write_private(root / "copilot-trial.json", json.dumps({
        "base_url": "http://127.0.0.1:18765/v1", "api_key": config["api_key"],
    }).encode())
    pi = backend == "argus-pi"
    knobs = {
        "ARGUS_SKILL_COPILOT_TRIAL": "0" if pi else "1",
        "ARGUS_SKILL_RUNNER_BACKEND": "pi" if pi else "copilot",
        "ARGUS_SKILL_LIFE_BACKEND": "pi" if pi else "copilot",
        "ARGUS_SKILL_RUNNER_BIN": "/usr/local/bin/argus-pi" if pi else "/usr/local/bin/copilot",
        "ARGUS_SKILL_MODEL": CLIENT_MODEL,
        "ARGUS_SKILL_BACKEND_AUTH_MODE": "subscription_cli",
    }
    if pi:
        agent_dir = root / "argus-pi"
        agent_dir.mkdir(exist_ok=True, mode=0o700)
        write_private(agent_dir / "models.json", json.dumps({"providers": {"argus": {
            "baseUrl": "http://127.0.0.1:18765/v1",
            "api": "openai-completions", "apiKey": config["api_key"],
            "models": [{"id": CLIENT_MODEL, "reasoning": True}],
        }}}).encode())
        os.environ.update({
            "PI_CODING_AGENT_DIR": str(agent_dir),
            "PI_HARNESS_PROFILE": "argus", "PI_OFFLINE": "1",
            "ARGUS_TRAINING_BRIDGE_SOCKET": "/run/argus-web/training.sock",
        })
        knobs["ARGUS_SKILL_PI_PROVIDER"] = "argus"
        for name in ("ENGINEER", "REVIEWER", "PLANNER", "MANAGER", "SUPERVISOR", "CURATOR"):
            knobs[f"ARGUS_SKILL_{name}_BACKEND"] = "pi"
    knobs.update({knob.name: REASONING_EFFORT for knob in KNOBS
                  if knob.name.endswith("_REASONING_EFFORT")})
    os.environ.update(knobs)
    if not write_persisted_knobs(knobs):
        raise RuntimeError("Could not persist the trial provider configuration")


def main() -> None:
    import uvicorn

    from ..core.paths import global_root
    from ..webapi.server import create_app, create_daemon, list_projects

    os.umask(0o077)
    config = json.loads(Path("/bootstrap/runtime.json").read_text())
    if Path("/tenant/.tenant-volume").read_text().strip() != config["tenant_id"]:
        raise RuntimeError("The account's isolated persistent filesystem is not mounted")
    root = global_root()
    root.mkdir(parents=True, exist_ok=True)
    from .plugins import configure_plugins

    configure_plugins(root)
    configure_provider(root, config)
    if not list_projects(global_root=root, include_empty=True):
        create_daemon(name=config["name"], workdir="/tenant/workspace", global_root=root)

    forwards = []
    try:
        for port, path in (
            (18765, "/meter/gateway.sock"),
            (18766, "/compute/compute.sock"),
            (3128, "/egress/egress.sock"),
        ):
            forwards.append(start_forward(port, path))
        os.environ.update({
            "HTTPS_PROXY": "http://127.0.0.1:3128",
            "HTTP_PROXY": "http://127.0.0.1:3128",
            "https_proxy": "http://127.0.0.1:3128",
            "http_proxy": "http://127.0.0.1:3128",
            "NO_PROXY": "localhost,127.0.0.1,::1",
        })
        uvicorn.run(
            create_app(global_root=root, auth_token=config["web_token"]),
            uds="/run/argus-web/web.sock", access_log=False, proxy_headers=False,
            timeout_graceful_shutdown=15,
        )
    finally:
        for forward in forwards:
            forward.terminate()
            forward.wait(timeout=10)


if __name__ == "__main__":
    main()
