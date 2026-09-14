"""Shared Copilot HTTP identity; callers own credential discovery and lifetime."""

COPILOT_BASE_URL = "https://api.githubcopilot.com"
COPILOT_HEADERS = {
    "User-Agent": "GithubCopilot/1.0.84",
    "Copilot-Integration-Id": "copilot-developer-cli",
}
