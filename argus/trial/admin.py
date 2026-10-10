"""Server-only initialization, GitHub device login, and gateway process."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import re
import sys
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx

from . import DEFAULT_UPSTREAM_MODEL, TRIAL_KEY_COUNT
from .secrets import Vault, write_private
from .store import TrialError


def issue_keys(vault: Vault, state_dir: Path, output: Path, *, key_count: int = TRIAL_KEY_COUNT):
    """Issue an explicitly sized pool; reruns retain the same keys and balances."""
    from .store import Store

    store = Store(state_dir / "usage.sqlite3", key_limit=key_count)
    keys = []
    for i in range(1, key_count + 1):
        key_id = f"trial-{i:02d}"
        credential = vault.credential(key_id)
        store.issue(key_id, credential)
        keys.append({"key_id": key_id, "api_key": credential})
    write_private(output, json.dumps(keys, indent=2).encode())
    print(f"{len(keys)} trial keys saved privately to {output}. Existing balances preserved.")


MAX_CODES = 100


def usd_amount(value: str) -> int:
    """A positive dollar amount as exact nano-AIU."""
    from .store import NANO_AIU_PER_USD

    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise ValueError("Allowance must be a dollar amount such as 5 or 2.50") from None
    nano = amount * NANO_AIU_PER_USD
    if not amount.is_finite() or nano <= 0 or nano != nano.to_integral_value():
        raise ValueError("Allowance must be a positive dollar amount")
    return int(nano)


def expiry_timestamp(value: str | None) -> float | None:
    """ISO date or date-time; a bare date expires at the end of that day (server time)."""
    if value is None:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("Expiry must be an ISO date such as 2026-10-31 or 2026-10-31T18:00+08:00") from None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        moment = moment.replace(hour=23, minute=59, second=59)
    stamp = moment.timestamp()
    if stamp <= time.time():
        raise ValueError("Expiry must be in the future")
    return stamp


def issue_codes(vault: Vault, state_dir: Path, output: Path, *, count: int, usd_limit: int,
                label: str = "", expires_at: float | None = None) -> list[str]:
    """Issue new activation codes after any existing trial-NN keys.

    Codes are written once to a private operator file; only their hashes enter
    the ledger and nothing here prints or logs them.
    """
    from .store import Store, usd

    if type(count) is not int or count < 1:
        raise ValueError("Issue at least one activation code")
    store = Store(state_dir / "usage.sqlite3", key_limit=MAX_CODES)
    with store.transaction() as db:
        taken = {row[0] for row in db.execute("SELECT key_id FROM trial_keys")}
    numbers = [int(key[6:]) for key in taken if re.fullmatch(r"trial-\d{2,3}", key)]
    first = max(numbers, default=0) + 1
    if first + count - 1 > MAX_CODES:
        raise ValueError(f"At most {MAX_CODES} codes can exist in one gateway ledger")
    issued = []
    for number in range(first, first + count):
        key_id = f"trial-{number:02d}"
        credential = vault.credential(key_id)
        store.issue_code(key_id, credential, usd_limit=usd_limit, label=label, expires_at=expires_at)
        issued.append({"key_id": key_id, "label": label, "usd_limit": usd(usd_limit),
                       "expires_at": expires_at, "code": credential})
    previous = json.loads(output.read_text()) if output.exists() else []
    write_private(output, json.dumps(previous + issued, indent=2).encode())
    print(f"{count} activation codes ({', '.join(item['key_id'] for item in issued)}) saved privately to {output}.")
    return [item["key_id"] for item in issued]


def format_codes(codes: list[dict]) -> str:
    def when(stamp):
        return "-" if stamp is None else datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M")

    lines = [f"{'code':<10} {'label':<20} {'allowance':>10} {'spent':>10} {'remaining':>10} "
             f"{'last used':<16} {'expires':<16} status"]
    for row in codes:
        lines.append(
            f"{row['key_id']:<10} {row['label'][:20]:<20} {row['usd_limit']:>10.2f} {row['usd_spent']:>10.4f} "
            f"{row['usd_remaining']:>10.4f} {when(row['last_used_at']):<16} {when(row['expires_at']):<16} "
            + ("active" if row["active"] else "used up" if row["usd_remaining"] <= 0 else "inactive")
        )
    return "\n".join(lines)


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
    parser.add_argument("command", choices=("init", "login", "import-token", "import-copilot-login", "issue-keys",
                                            "issue-codes", "list-codes", "serve"))
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".local/share/argus-trial-gateway")
    parser.add_argument("--key-file", type=Path, default=Path.home() / ".config/argus-trial-gateway/master.key")
    parser.add_argument("--key-count", type=int, default=TRIAL_KEY_COUNT,
                        help="Explicit invitation pool size for issue-keys (default: 10)")
    parser.add_argument("--copilot-home", type=Path, default=Path(os.environ.get("COPILOT_HOME") or Path.home() / ".copilot"))
    parser.add_argument("--model", default=DEFAULT_UPSTREAM_MODEL, help="Copilot model supporting Responses with high reasoning")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--keys-output", type=Path, help="Private operator export for issue-keys")
    parser.add_argument("--site-dir", type=Path, help="Trial site root containing trial/downloads")
    parser.add_argument("--count", type=int, default=1, help="issue-codes: number of activation codes")
    parser.add_argument("--usd", help="issue-codes: USD allowance per code, e.g. 5 or 2.50")
    parser.add_argument("--label", default="", help="issue-codes: operator label stored with each code")
    parser.add_argument("--expires", help="issue-codes: ISO expiry date or date-time (server time if no offset)")
    parser.add_argument("--json", action="store_true", help="list-codes: machine-readable output")
    parser.add_argument("--prices", type=Path,
                        help="serve: JSON price table, model ID -> USD per million tokens (input/output/cache_read/cache_write)")
    parser.add_argument("--uds", help="serve: listen on this Unix socket instead of host/port")
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
                       args.keys_output or args.state_dir / "trial-keys.json", key_count=args.key_count)
        elif args.command == "issue-codes":
            if args.usd is None:
                raise ValueError("issue-codes requires --usd")
            issue_codes(Vault(args.key_file, args.state_dir / "github-token.enc"), args.state_dir,
                        args.keys_output or args.state_dir / "activation-codes.json", count=args.count,
                        usd_limit=usd_amount(args.usd), label=args.label, expires_at=expiry_timestamp(args.expires))
        elif args.command == "list-codes":
            from .store import Store

            codes = Store(args.state_dir / "usage.sqlite3", key_limit=MAX_CODES).list_codes()
            print(json.dumps(codes, indent=2) if args.json else format_codes(codes))
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
            from .pricing import load_prices

            listen = {"uds": args.uds} if args.uds else {"host": args.host, "port": args.port}
            uvicorn.run(
                create_app(Settings(args.state_dir, args.key_file, args.model, site_dir=args.site_dir,
                                    prices=load_prices(args.prices))),
                **listen, workers=1, access_log=False,
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
