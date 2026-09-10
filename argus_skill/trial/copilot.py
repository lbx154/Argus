"""Use the server CLI's encrypted login with Copilot's native HTTP API."""
from __future__ import annotations

import httpx

from .secrets import Vault
from .store import TrialError

BASE_URL = "https://api.githubcopilot.com"
HEADERS = {
    "User-Agent": "GithubCopilot/1.0.84",
    "Copilot-Integration-Id": "copilot-developer-cli",
}


class Copilot:
    def __init__(self, client: httpx.AsyncClient, vault: Vault):
        self.client, self.vault = client, vault

    async def authorization(self) -> tuple[str, dict[str, str]]:
        try:
            token = self.vault.read()
        except ValueError:
            raise TrialError(503, "trial_not_ready", "Trial provider is not configured.") from None
        # Current Copilot CLI authenticates directly with its GitHub OAuth
        # credential. The editor's v2 token exchange is a different auth path.
        # Neither client requests nor provider responses can change this origin.
        return BASE_URL, {
            **HEADERS, "Authorization": f"Bearer {token}",
            "Content-Type": "application/json", "X-Initiator": "agent",
        }

    async def verify(self):
        base_url, headers = await self.authorization()
        response = await self.client.get(base_url + "/models", headers=headers)
        if response.status_code != 200:
            raise TrialError(503, "provider_auth_failed", "Trial provider authentication failed.")
        try:
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("data"), list) or not data["data"]:
                raise ValueError("No models")
        except ValueError:
            raise TrialError(502, "provider_protocol_error", "Invalid provider models response.") from None
