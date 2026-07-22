"""Project-local model helpers — standalone, no argus_skill dependency.

Reads API credentials from ~/.argus-skill/capabilities/model_api.json
(configured via `argus-skill --setup`). Works in any venv.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT_SECONDS = 180.0
DEFAULT_MAX_RETRIES = 5
TRANSIENT_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}
_ARGUS_HOME = Path(os.environ.get("ARGUS_SKILL_HOME") or (Path.home() / ".argus-skill"))
_VAULT_PATH = _ARGUS_HOME / "capabilities" / "model_api.json"


class ModelCallError(RuntimeError):
    """Raised when a configured model route cannot complete a request."""


@dataclass
class Route:
    name: str
    base_url: str
    api_key: str
    model: str
    wire_api: str = "responses"
    provider: str = "codex"

    @property
    def usable(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)


def _load_vault() -> dict[str, Any]:
    vault_path = Path(os.environ.get("ARGUS_VAULT_PATH", str(_VAULT_PATH)))
    if not vault_path.exists():
        return {}
    return json.loads(vault_path.read_text(encoding="utf-8"))


def load_route(route_name: str = "text") -> Route | None:
    """Load a named API route from the capability vault."""
    vault = _load_vault()
    routes = vault.get("capabilities", {}).get("model_api", {}).get("routes", {})
    r = routes.get(route_name)
    if not r or not isinstance(r, dict):
        return None
    return Route(
        name=route_name,
        base_url=r.get("base_url", ""),
        api_key=r.get("api_key", ""),
        model=r.get("model", ""),
        wire_api=r.get("wire_api", "responses"),
        provider=r.get("provider", "codex"),
    )


def route_status(route_name: str = "text") -> dict[str, Any]:
    """Return secret-free status for a configured route."""
    route = load_route(route_name)
    if route is None:
        return {"route": route_name, "usable": False}
    return {
        "route": route.name,
        "usable": route.usable,
        "provider": route.provider,
        "wire_api": route.wire_api,
        "model": route.model,
        "base_url": route.base_url[:40] + "..." if len(route.base_url) > 40 else route.base_url,
    }


def _route(route_name: str) -> Route:
    route = load_route(route_name)
    if route is None or not route.usable:
        raise ModelCallError(
            f"model API route {route_name!r} is unavailable; run "
            "`argus-skill --setup` to configure"
        )
    return route


def _endpoint_url(route: Route, endpoint: str) -> str:
    return f"{route.base_url.rstrip('/')}/{endpoint.lstrip('/')}"


def _retry_delay_seconds(exc: BaseException, attempt_index: int) -> float | None:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code not in TRANSIENT_HTTP_STATUS_CODES:
            return None
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        if retry_after:
            try:
                return max(1.0, float(retry_after))
            except ValueError:
                pass
    elif not isinstance(exc, urllib.error.URLError):
        return None
    return min(60.0, 2.0 * (2**attempt_index))


def _post(
    route: Route,
    endpoint: str,
    payload: dict[str, Any],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
    req = urllib.request.Request(
        _endpoint_url(route, endpoint),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {route.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    attempts = max(1, int(max_retries))
    raw = ""
    for attempt_index in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - operator route
                raw = resp.read().decode("utf-8")
            break
        except urllib.error.HTTPError as exc:
            delay = _retry_delay_seconds(exc, attempt_index)
            if delay is not None and attempt_index < attempts - 1:
                time.sleep(delay)
                continue
            body = exc.read().decode("utf-8", errors="replace")
            raise ModelCallError(f"{endpoint} failed with HTTP {exc.code}: {body[:500]}") from exc
        except urllib.error.URLError as exc:
            delay = _retry_delay_seconds(exc, attempt_index)
            if delay is not None and attempt_index < attempts - 1:
                time.sleep(delay)
                continue
            raise ModelCallError(f"{endpoint} failed after {attempt_index + 1} attempt(s): {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ModelCallError(f"{endpoint} returned non-JSON: {raw[:500]}") from exc
    if not isinstance(data, dict):
        raise ModelCallError(f"{endpoint} returned {type(data).__name__}, expected object")
    return data


def _parse_responses_text(data: dict[str, Any]) -> str:
    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    chunks: list[str] = []
    output = data.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    chunks.append(part["text"].strip())
    return "\n".join(chunk for chunk in chunks if chunk).strip()


def _parse_chat_text(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        chunks = [
            str(part.get("text") or "").strip()
            for part in content
            if isinstance(part, dict) and str(part.get("text") or "").strip()
        ]
        return "\n".join(chunks).strip()
    return ""


def complete(
    prompt: str,
    *,
    route_name: str = "text",
    system: str = "",
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Call a configured text route and return the response text."""
    route = _route(route_name)
    if route.wire_api == "chat":
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload: dict[str, Any] = {"model": route.model, "messages": messages}
        if temperature is not None:
            payload["temperature"] = temperature
        if max_output_tokens is not None:
            payload["max_tokens"] = max_output_tokens
        data = _post(route, "/chat/completions", payload, timeout=timeout)
        text = _parse_chat_text(data)
    else:
        inputs = []
        if system:
            inputs.append({"role": "system", "content": system})
        inputs.append({"role": "user", "content": prompt})
        payload = {"model": route.model, "input": inputs}
        if temperature is not None:
            payload["temperature"] = temperature
        if max_output_tokens is not None:
            payload["max_output_tokens"] = max_output_tokens
        data = _post(route, "/responses", payload, timeout=timeout)
        text = _parse_responses_text(data)
    if not text:
        raise ModelCallError(f"route {route_name!r} returned no text")
    return text


def complete_json(prompt: str, **kwargs: Any) -> Any:
    """Call `complete` and parse a JSON object/array from the response."""
    text = complete(prompt, **kwargs)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
        if match:
            return json.loads(match.group(1))
        raise
