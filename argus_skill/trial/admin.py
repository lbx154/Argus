"""Server-only initialization, GitHub device login, and gateway process."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
import time
from pathlib import Path

import httpx

from .secrets import Vault, write_private
from .store import TrialError


def issue_keys(vault: Vault, state_dir: Path, output: Path):
    """Issue the fixed pool once; reruns retain the same keys and balances."""
    from . import TRIAL_KEY_COUNT
    from .store import Store

    store = Store(state_dir / "usage.sqlite3")
    keys = []
    for i in range(1, TRIAL_KEY_COUNT + 1):
        key_id = f"trial-{i:02d}"
        credential = vault.credential(key_id)
        store.issue(key_id, credential)
        keys.append({"key_id": key_id, "api_key": credential})
    write_private(output, json.dumps(keys, indent=2).encode())
    print(f"10 trial keys saved privately to {output}. Existing balances preserved.")

# Public OAuth application identifier, as used by the Copilot device flow.
GITHUB_CLIENT_ID = "Iv1.b507a08c87ecfe98"


async def import_cli_login(vault: Vault, home: Path):
    """Use the CLI's selected account; never print or copy plaintext auth."""
    from ..agent_cli.copilot_home import _read_managed_config
    from .copilot import Copilot

    config = _read_managed_config(home / "config.json")
    if not config or not isinstance(config.get("lastLoggedInUser"), dict):
        raise ValueError("No selected Copilot CLI account. Run `copilot login` on the server.")
    active = config["lastLoggedInUser"]
    host, username = active.get("host"), active.get("login")
    if host not in {"github.com", "https://github.com"} or not isinstance(username, str) or not username:
        raise ValueError("The selected CLI account must use github.com.")
    entries = config.get("authTokens", {})
    entry = entries.get(f"{host}:{username}", {}) if isinstance(entries, dict) else {}
    token = entry.get("token") if isinstance(entry, dict) else None
    if not isinstance(token, str) or not token:
        raise ValueError("Selected CLI login has no reusable OAuth credential; log in with the current Copilot CLI.")
    previous = vault.token_path.read_bytes() if vault.token_path.exists() else None
    vault.save(token)
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
            await Copilot(client, vault).verify()
    except BaseException:
        if previous is None:
            vault.token_path.unlink(missing_ok=True)
        else:
            write_private(vault.token_path, previous)
        raise
    print("Selected Copilot CLI login verified and stored encrypted. Restart the trial gateway.")


async def login(vault: Vault):
    from .copilot import HEADERS, Copilot

    async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
        response = await client.post(
            "https://github.com/login/device/code",
            headers={"Accept": "application/json", **HEADERS},
            data={"client_id": GITHUB_CLIENT_ID, "scope": "read:user"},
        )
        response.raise_for_status()
        device = response.json()
        print("Open https://github.com/login/device and enter: " + device["user_code"], flush=True)
        deadline = time.monotonic() + device["expires_in"]
        interval = max(5, device.get("interval", 5))
        while time.monotonic() < deadline:
            await asyncio.sleep(interval)
            response = await client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json", **HEADERS},
                data={
                    "client_id": GITHUB_CLIENT_ID, "device_code": device["device_code"],
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data.get("access_token"), str):
                vault.save(data["access_token"])
                await Copilot(client, vault).verify()
                print("Copilot login verified; encrypted credential saved. Restart the trial gateway.")
                return
            if data.get("error") == "slow_down":
                interval += 5
            elif data.get("error") != "authorization_pending":
                raise ValueError("GitHub device authorization was rejected.")
        raise ValueError("GitHub device authorization expired; rerun login.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Argus trial gateway administration (server only)")
    parser.add_argument("command", choices=("init", "login", "import-token", "import-copilot-login", "issue-keys", "serve"))
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".local/share/argus-trial-gateway")
    parser.add_argument("--key-file", type=Path, default=Path.home() / ".config/argus-trial-gateway/master.key")
    parser.add_argument("--copilot-home", type=Path, default=Path(os.environ.get("COPILOT_HOME") or Path.home() / ".copilot"))
    parser.add_argument("--model", default="gpt-4.1", help="Copilot model supporting text/tool Chat Completions")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--keys-output", type=Path, help="Private operator export for issue-keys")
    parser.add_argument("--site-dir", type=Path, help="Trial site root containing trial/downloads")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.command == "init":
            from cryptography.fernet import Fernet

            if args.key_file.exists():
                print("Master key already exists; preserved.")
                return 0
            if (args.state_dir / "usage.sqlite3").exists() or (args.state_dir / "github-token.enc").exists():
                raise ValueError("Existing trial state has no key. Restore its master key from backup.")
            write_private(args.key_file, Fernet.generate_key())
            args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            print("Server master key initialized. Next run: argus-trial-server login")
        elif args.command == "issue-keys":
            issue_keys(Vault(args.key_file, args.state_dir / "github-token.enc"), args.state_dir,
                       args.keys_output or args.state_dir / "trial-keys.json")
        elif args.command in {"login", "import-token", "import-copilot-login"}:
            vault = Vault(args.key_file, args.state_dir / "github-token.enc")
            if args.command == "login":
                asyncio.run(login(vault))
            elif args.command == "import-copilot-login":
                asyncio.run(import_cli_login(vault, args.copilot_home))
            else:
                vault.save(getpass.getpass("GitHub Copilot OAuth token (hidden): "))
                print("Encrypted credential saved. Restart the trial gateway.")
        else:
            import uvicorn

            from .gateway import Settings, create_app

            uvicorn.run(
                create_app(Settings(args.state_dir, args.key_file, args.model, site_dir=args.site_dir)),
                host=args.host, port=args.port, workers=1, access_log=False,
                proxy_headers=False, timeout_graceful_shutdown=15,
            )
        return 0
    except (ValueError, OSError, TrialError) as exc:
        print(f"Trial server: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError:
        print("Trial server: provider connection or authorization request failed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
