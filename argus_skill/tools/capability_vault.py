"""Local capability vault for pre-approved model/API access.

The vault is intentionally outside the repository and readable only by the
current Unix user. Prompts receive capability metadata and tool commands, while
the raw API key is loaded only by tool subprocesses.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py311+ in this project
    tomllib = None  # type: ignore[assignment]

_VAULT_ENV = "ARGUS_SKILL_CAPABILITY_VAULT"
_AUTH_JSON_ENV = "ARGUS_SKILL_MODEL_API_AUTH_JSON"
_CODEX_CONFIG_ENV = "ARGUS_SKILL_CODEX_CONFIG"
_BASE_URL_ENV = "ARGUS_SKILL_MODEL_API_BASE_URL"
_TEXT_MODELS_ENV = "ARGUS_SKILL_TEXT_MODELS"
_IMAGE_MODEL_ENV = "ARGUS_SKILL_IMAGE_MODEL"
_IMAGE_REVIEW_MODEL_ENV = "ARGUS_SKILL_IMAGE_REVIEW_MODEL"
# Unified default text model for every agent route. The single source of
# truth for production model selection is the vault file
# ``~/.argus-skill/capabilities/model_api.json``; these literals are only the
# offline fallback when a route is absent from the vault.
_DEFAULT_TEXT_MODEL = "gpt-5.5"
_DEFAULT_TEXT_MODELS = (_DEFAULT_TEXT_MODEL, _DEFAULT_TEXT_MODEL)
_DEFAULT_IMAGE_MODEL = "gpt-image-2"
_DEFAULT_IMAGE_REVIEW_MODEL = _DEFAULT_TEXT_MODEL
_DEFAULT_ROUTE_MODELS = {
    "engineer": _DEFAULT_TEXT_MODEL,
    "reviewer": _DEFAULT_TEXT_MODEL,
    "planner": _DEFAULT_TEXT_MODEL,
    "curator": _DEFAULT_TEXT_MODEL,
    "text": _DEFAULT_TEXT_MODEL,
    "image": _DEFAULT_IMAGE_MODEL,
    "image_review": _DEFAULT_IMAGE_REVIEW_MODEL,
}
_ROUTE_FALLBACKS = {
    "engineer": ("engineer", "text", "default"),
    "reviewer": ("reviewer", "text", "default"),
    "planner": ("planner", "reviewer", "text", "default"),
    "curator": ("curator", "reviewer", "text", "default"),
    "text": ("text", "default"),
    "image": ("image", "default"),
    "image_review": ("image_review", "reviewer", "text", "default"),
}


@dataclass(frozen=True)
class CodexProviderConfig:
    name: str
    base_url: str
    wire_api: str


@dataclass(frozen=True)
class ModelApiGrant:
    api_key: str = field(repr=False)
    base_url: str
    provider: str = "codex"
    wire_api: str = "responses"
    text_models: tuple[str, ...] = _DEFAULT_TEXT_MODELS
    image_model: str = _DEFAULT_IMAGE_MODEL
    image_review_model: str = _DEFAULT_IMAGE_REVIEW_MODEL
    key_source: str = "missing"
    base_url_source: str = "missing"
    vault_path: Path | None = None

    @property
    def usable(self) -> bool:
        return bool(self.api_key and self.base_url)


@dataclass(frozen=True)
class ModelApiRoute:
    """One independently configurable model/API route.

    Examples: ``engineer``, ``reviewer``, ``author``, ``image``, and
    ``image_review`` may all point at different providers, base URLs, models,
    and API keys.
    """

    name: str
    api_key: str = field(repr=False)
    base_url: str = ""
    model: str = ""
    provider: str = "codex"
    wire_api: str = "responses"
    key_source: str = "missing"
    base_url_source: str = "missing"
    model_source: str = "missing"
    vault_path: Path | None = None

    @property
    def usable(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)


def _env_text(env: Mapping[str, str], key: str) -> str:
    return str(env.get(key) or "").strip()


def default_vault_path(env: Mapping[str, str] | None = None) -> Path:
    source = env if env is not None else os.environ
    raw = _env_text(source, _VAULT_ENV)
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".argus-skill" / "capabilities" / "model_api.json"


def default_auth_json_path(env: Mapping[str, str] | None = None) -> Path:
    source = env if env is not None else os.environ
    raw = _env_text(source, _AUTH_JSON_ENV)
    return Path(raw).expanduser() if raw else Path.home() / ".codex" / "auth.json"


def default_codex_config_path(env: Mapping[str, str] | None = None) -> Path:
    source = env if env is not None else os.environ
    raw = _env_text(source, _CODEX_CONFIG_ENV)
    return Path(raw).expanduser() if raw else Path.home() / ".codex" / "config.toml"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def read_auth_json_key(env: Mapping[str, str] | None = None) -> str:
    data = _read_json(default_auth_json_path(env))
    return str(data.get("OPENAI_API_KEY") or "").strip()


def read_codex_provider_config(
    env: Mapping[str, str] | None = None,
) -> CodexProviderConfig | None:
    if tomllib is None:
        return None
    path = default_codex_config_path(env)
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    provider_name = str(data.get("model_provider") or "codex")
    providers = data.get("model_providers")
    if not isinstance(providers, dict):
        return None
    provider = providers.get(provider_name)
    if not isinstance(provider, dict):
        return None
    base_url = str(provider.get("base_url") or "").strip()
    if not base_url:
        return None
    wire_api = str(provider.get("wire_api") or "responses").strip() or "responses"
    return CodexProviderConfig(name=provider_name, base_url=base_url, wire_api=wire_api)


def _split_models(raw: str) -> tuple[str, ...]:
    vals = tuple(part.strip() for part in raw.split(",") if part.strip())
    return vals or _DEFAULT_TEXT_MODELS


def _read_vault_payload(path: Path) -> dict[str, Any]:
    payload = _read_json(path)
    caps = payload.get("capabilities")
    if isinstance(caps, dict) and isinstance(caps.get("model_api"), dict):
        return caps["model_api"]
    return payload


def _read_vault_model_api(path: Path) -> dict[str, Any]:
    return _read_vault_payload(path)


def _vault_grant(env: Mapping[str, str]) -> tuple[dict[str, Any], Path]:
    path = default_vault_path(env)
    return _read_vault_payload(path), path


def _has_vault_grant(payload: dict[str, Any], path: Path) -> bool:
    return path.exists() or any(
        key in payload
        for key in (
            "api_key",
            "base_url",
            "provider",
            "wire_api",
            "text_models",
            "image_model",
            "image_review_model",
        )
    )


def _grant_from_vault(payload: dict[str, Any], path: Path) -> ModelApiGrant:
    raw_text_models = ",".join(str(m) for m in payload.get("text_models", []) if m)
    text_models = _split_models(raw_text_models) if raw_text_models else _DEFAULT_TEXT_MODELS
    api_key = str(payload.get("api_key") or "").strip()
    base_url = str(payload.get("base_url") or "").strip()
    return ModelApiGrant(
        api_key=api_key,
        base_url=base_url,
        provider=str(payload.get("provider") or "codex"),
        wire_api=str(payload.get("wire_api") or "responses"),
        text_models=text_models,
        image_model=str(payload.get("image_model") or _DEFAULT_IMAGE_MODEL).strip(),
        image_review_model=str(
            payload.get("image_review_model") or _DEFAULT_IMAGE_REVIEW_MODEL
        ).strip(),
        key_source=f"vault:{path}" if api_key else "missing",
        base_url_source=f"vault:{path}" if base_url else "missing",
        vault_path=path,
    )


def _route_env_prefix(route: str) -> str:
    return f"ARGUS_SKILL_{route.upper()}_"


def _route_env_value(env: Mapping[str, str], route: str, field_name: str) -> tuple[str, str]:
    route_key = _route_env_prefix(route) + field_name.upper()
    value = _env_text(env, route_key)
    if value:
        return value, f"env:{route_key}"
    if route == "image" and field_name == "model":
        value = _env_text(env, _IMAGE_MODEL_ENV)
        if value:
            return value, f"env:{_IMAGE_MODEL_ENV}"
    if route == "image_review" and field_name == "model":
        value = _env_text(env, _IMAGE_REVIEW_MODEL_ENV)
        if value:
            return value, f"env:{_IMAGE_REVIEW_MODEL_ENV}"
    return "", ""


def _payload_routes(payload: dict[str, Any]) -> dict[str, Any]:
    routes = payload.get("routes")
    return routes if isinstance(routes, dict) else {}


def _apply_route_env_overrides(
    route: str,
    loaded: ModelApiRoute,
    env: Mapping[str, str],
) -> ModelApiRoute:
    """Apply explicit process overrides to a loaded route.

    The capability vault remains the authorization boundary, but documented
    per-route environment variables such as ``ARGUS_SKILL_IMAGE_MODEL`` must be
    able to override deployment names for one command invocation. Without this
    overlay, an operator cannot diagnose or temporarily repair a stale image
    deployment name without editing the vault itself.
    """
    updates: dict[str, Any] = {}
    for field_name, attr_name, source_name in (
        ("api_key", "api_key", "key_source"),
        ("base_url", "base_url", "base_url_source"),
        ("model", "model", "model_source"),
    ):
        value, value_source = _route_env_value(env, route, field_name)
        if value:
            updates[attr_name] = value
            updates[source_name] = value_source
    wire_api, _wire_source = _route_env_value(env, route, "wire_api")
    if wire_api:
        updates["wire_api"] = wire_api
    if not updates:
        return loaded
    return ModelApiRoute(**{**loaded.__dict__, **updates})


def _legacy_route_from_payload(
    route: str,
    payload: dict[str, Any],
    path: Path,
) -> ModelApiRoute | None:
    api_key = str(payload.get("api_key") or "").strip()
    base_url = str(payload.get("base_url") or "").strip()
    if not api_key and not base_url:
        return None
    raw_text_models = ",".join(str(m) for m in payload.get("text_models", []) if m)
    text_models = _split_models(raw_text_models) if raw_text_models else _DEFAULT_TEXT_MODELS
    if route == "engineer":
        model = text_models[0]
    elif route == "image":
        model = str(payload.get("image_model") or _DEFAULT_IMAGE_MODEL).strip()
    elif route == "image_review":
        model = str(payload.get("image_review_model") or _DEFAULT_IMAGE_REVIEW_MODEL).strip()
    else:
        model = text_models[-1] if text_models else _DEFAULT_ROUTE_MODELS.get(route, "")
    return ModelApiRoute(
        name=route,
        api_key=api_key,
        base_url=base_url,
        model=model,
        provider=str(payload.get("provider") or "codex"),
        wire_api=str(payload.get("wire_api") or "responses"),
        key_source=f"vault:{path}" if api_key else "missing",
        base_url_source=f"vault:{path}" if base_url else "missing",
        model_source=f"vault:{path}" if model else "missing",
        vault_path=path,
    )


def _route_from_vault(
    route: str,
    payload: dict[str, Any],
    path: Path,
) -> ModelApiRoute | None:
    routes = _payload_routes(payload)
    route_data = routes.get(route)
    if not isinstance(route_data, dict):
        return None
    api_key = str(route_data.get("api_key") or "").strip()
    base_url = str(route_data.get("base_url") or "").strip()
    model = str(route_data.get("model") or _DEFAULT_ROUTE_MODELS.get(route, "")).strip()
    return ModelApiRoute(
        name=route,
        api_key=api_key,
        base_url=base_url,
        model=model,
        provider=str(route_data.get("provider") or route_data.get("name") or "codex"),
        wire_api=str(route_data.get("wire_api") or "responses"),
        key_source=f"vault:{path}:routes.{route}" if api_key else "missing",
        base_url_source=f"vault:{path}:routes.{route}" if base_url else "missing",
        model_source=f"vault:{path}:routes.{route}" if model else "missing",
        vault_path=path,
    )


def _route_from_explicit_sources(
    route: str,
    env: Mapping[str, str],
    vault_path: Path,
) -> ModelApiRoute | None:
    provider_cfg = read_codex_provider_config(env)
    explicit_auth_json = bool(_env_text(env, _AUTH_JSON_ENV))
    api_key, key_source = _route_env_value(env, route, "api_key")
    if not api_key and route != "image":
        api_key = _env_text(env, "OPENAI_API_KEY")
        key_source = "env:OPENAI_API_KEY" if api_key else ""
    if not api_key and route == "image":
        api_key = _env_text(env, "OPENAI_API_KEY")
        key_source = "env:OPENAI_API_KEY" if api_key else ""
    if not api_key and explicit_auth_json:
        api_key = read_auth_json_key(env)
        key_source = f"file:{default_auth_json_path(env)}" if api_key else ""

    base_url, base_url_source = _route_env_value(env, route, "base_url")
    if not base_url and route != "image":
        base_url = _env_text(env, "OPENAI_BASE_URL")
        base_url_source = "env:OPENAI_BASE_URL" if base_url else ""
    if not base_url and route == "image":
        base_url = _env_text(env, "OPENAI_BASE_URL")
        base_url_source = "env:OPENAI_BASE_URL" if base_url else ""
    if not base_url:
        base_url = _env_text(env, _BASE_URL_ENV)
        base_url_source = f"env:{_BASE_URL_ENV}" if base_url else ""
    if not base_url and provider_cfg is not None:
        base_url = provider_cfg.base_url
        base_url_source = f"file:{default_codex_config_path(env)}"

    model, model_source = _route_env_value(env, route, "model")
    if not model:
        model = _DEFAULT_ROUTE_MODELS.get(route, "")
        model_source = "default"
    wire_api, _wire_source = _route_env_value(env, route, "wire_api")
    if not wire_api:
        wire_api = (
            "images"
            if route == "image"
            else (provider_cfg.wire_api if provider_cfg is not None else "responses")
        )

    if not api_key and not base_url:
        return None
    return ModelApiRoute(
        name=route,
        api_key=api_key,
        base_url=base_url,
        model=model,
        provider=provider_cfg.name if provider_cfg is not None else "codex",
        wire_api=wire_api,
        key_source=key_source or "missing",
        base_url_source=base_url_source or "missing",
        model_source=model_source or "missing",
        vault_path=vault_path,
    )


def load_model_api_route(
    route: str,
    env: Mapping[str, str] | None = None,
) -> ModelApiRoute | None:
    """Load one named model/API route.

    Vault v2 supports independent route configuration. Vault v1 and explicit
    environment/Codex sources remain supported as compatibility fallbacks.
    """
    source = env if env is not None else os.environ
    path = default_vault_path(source)
    payload = _read_vault_model_api(path)
    for candidate in _ROUTE_FALLBACKS.get(route, (route,)):
        from_vault = _route_from_vault(candidate, payload, path)
        if from_vault is not None:
            loaded = from_vault
            if candidate == route:
                return _apply_route_env_overrides(route, loaded, source)
            loaded = ModelApiRoute(**{**from_vault.__dict__, "name": route})
            return _apply_route_env_overrides(route, loaded, source)
    if path.exists() and _payload_routes(payload):
        return None
    if _has_vault_grant(payload, path):
        legacy = _legacy_route_from_payload(route, payload, path)
        if legacy is not None:
            return legacy
    return _route_from_explicit_sources(route, source, path)


def resolve_route_model(
    route: str,
    env: Mapping[str, str] | None = None,
) -> str:
    """Single entry point for "which model does this route use".

    Reads the model name from the vault file
    ``~/.argus-skill/capabilities/model_api.json`` (route -> model), following
    the same fallback chain as :func:`load_model_api_route`. When the route is
    absent from the vault and no explicit source provides a model, the offline
    default from :data:`_DEFAULT_ROUTE_MODELS` is returned.

    This is intentionally decoupled from :func:`load_model_api_route`, which
    returns ``None`` when credentials are unavailable. Model selection must
    always yield a concrete name, even in offline/test environments.
    """
    loaded = load_model_api_route(route, env)
    if loaded is not None and loaded.model:
        return loaded.model
    return _DEFAULT_ROUTE_MODELS.get(route, _DEFAULT_TEXT_MODEL)


def _route_to_legacy_grant(route: ModelApiRoute, env: Mapping[str, str]) -> ModelApiGrant:
    engineer_route = load_model_api_route("engineer", env)
    reviewer_route = load_model_api_route("reviewer", env)
    image_route = load_model_api_route("image", env)
    image_review_route = load_model_api_route("image_review", env)
    return ModelApiGrant(
        api_key=route.api_key,
        base_url=route.base_url,
        provider=route.provider,
        wire_api=route.wire_api,
        text_models=(
            engineer_route.model if engineer_route is not None else _DEFAULT_ROUTE_MODELS["engineer"],
            reviewer_route.model if reviewer_route is not None else _DEFAULT_ROUTE_MODELS["reviewer"],
        ),
        image_model=image_route.model if image_route is not None else _DEFAULT_IMAGE_MODEL,
        image_review_model=(
            image_review_route.model
            if image_review_route is not None
            else _DEFAULT_IMAGE_REVIEW_MODEL
        ),
        key_source=route.key_source,
        base_url_source=route.base_url_source,
        vault_path=route.vault_path,
    )


def _grant_from_explicit_sources(env: Mapping[str, str], vault_path: Path) -> ModelApiGrant | None:
    explicit_auth_json = bool(_env_text(env, _AUTH_JSON_ENV))
    explicit_codex_config = bool(_env_text(env, _CODEX_CONFIG_ENV))
    provider_cfg = read_codex_provider_config(env) if explicit_codex_config else None

    api_key = _env_text(env, "OPENAI_API_KEY")
    key_source = "env:OPENAI_API_KEY" if api_key else ""
    if not api_key and explicit_auth_json:
        api_key = read_auth_json_key(env)
        key_source = f"file:{default_auth_json_path(env)}" if api_key else ""

    base_url = _env_text(env, "OPENAI_BASE_URL")
    base_url_source = "env:OPENAI_BASE_URL" if base_url else ""
    if not base_url:
        base_url = _env_text(env, _BASE_URL_ENV)
        base_url_source = f"env:{_BASE_URL_ENV}" if base_url else ""
    if not base_url and provider_cfg is not None:
        base_url = provider_cfg.base_url
        base_url_source = f"file:{default_codex_config_path(env)}"

    if not api_key and not base_url:
        return None
    provider = provider_cfg.name if provider_cfg is not None else "codex"
    wire_api = provider_cfg.wire_api if provider_cfg is not None else "responses"
    raw_text_models = _env_text(env, _TEXT_MODELS_ENV)
    text_models = _split_models(raw_text_models) if raw_text_models else _DEFAULT_TEXT_MODELS
    image_model = _env_text(env, _IMAGE_MODEL_ENV) or _DEFAULT_IMAGE_MODEL
    image_review_model = _env_text(env, _IMAGE_REVIEW_MODEL_ENV) or _DEFAULT_IMAGE_REVIEW_MODEL
    return ModelApiGrant(
        api_key=api_key,
        base_url=base_url,
        provider=provider,
        wire_api=wire_api,
        text_models=text_models,
        image_model=image_model,
        image_review_model=image_review_model,
        key_source=key_source or "missing",
        base_url_source=base_url_source or "missing",
        vault_path=vault_path,
    )


def load_model_api_grant(env: Mapping[str, str] | None = None) -> ModelApiGrant | None:
    """Load the runtime grant.

    Runtime access is vault-first by design: the fixed capability vault is the
    default authorization boundary. Codex auth/config files are not consulted
    implicitly at runtime; they are import sources for ``init-model-api``.
    """
    source = env if env is not None else os.environ
    route = load_model_api_route("text", source)
    if route is not None:
        return _route_to_legacy_grant(route, source)
    vault, vault_path = _vault_grant(source)
    if _has_vault_grant(vault, vault_path):
        return _grant_from_vault(vault, vault_path)
    return _grant_from_explicit_sources(source, vault_path)



def save_model_api_grant(grant: ModelApiGrant, path: Path | None = None) -> Path:
    target = path or grant.vault_path or default_vault_path()
    target = target.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)
    routes = {
        "engineer": ModelApiRoute(
            name="engineer",
            api_key=grant.api_key,
            base_url=grant.base_url,
            model=grant.text_models[0] if grant.text_models else _DEFAULT_ROUTE_MODELS["engineer"],
            provider=grant.provider,
            wire_api=grant.wire_api,
        ),
        "reviewer": ModelApiRoute(
            name="reviewer",
            api_key=grant.api_key,
            base_url=grant.base_url,
            model=grant.text_models[-1] if grant.text_models else _DEFAULT_ROUTE_MODELS["reviewer"],
            provider=grant.provider,
            wire_api=grant.wire_api,
        ),
        "text": ModelApiRoute(
            name="text",
            api_key=grant.api_key,
            base_url=grant.base_url,
            model=grant.text_models[-1] if grant.text_models else _DEFAULT_ROUTE_MODELS["text"],
            provider=grant.provider,
            wire_api=grant.wire_api,
        ),
        "image": ModelApiRoute(
            name="image",
            api_key=grant.api_key,
            base_url=grant.base_url,
            model=grant.image_model,
            provider=grant.provider,
            wire_api="images",
        ),
        "image_review": ModelApiRoute(
            name="image_review",
            api_key=grant.api_key,
            base_url=grant.base_url,
            model=grant.image_review_model,
            provider=grant.provider,
            wire_api=grant.wire_api,
        ),
    }
    return save_model_api_routes(routes.values(), target)


def save_model_api_routes(routes: Iterable[ModelApiRoute], path: Path | None = None) -> Path:
    route_list = list(routes)
    target = path or (route_list[0].vault_path if route_list else None) or default_vault_path()
    target = target.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)
    payload = {
        "version": 2,
        "capabilities": {
            "model_api": {
                "routes": {
                    route.name: {
                        "provider": route.provider,
                        "base_url": route.base_url,
                        "wire_api": route.wire_api,
                        "api_key": route.api_key,
                        "model": route.model,
                    }
                    for route in route_list
                }
            }
        },
    }
    tmp = target.with_name(target.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, target)
        os.chmod(target, 0o600)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
    return target


def discover_model_api_routes(env: Mapping[str, str] | None = None) -> list[ModelApiRoute]:
    source = env if env is not None else os.environ
    vault_path = default_vault_path(source)
    provider_cfg = read_codex_provider_config(source)
    global_key = _env_text(source, "OPENAI_API_KEY") or read_auth_json_key(source)
    global_key_source = (
        "env:OPENAI_API_KEY"
        if _env_text(source, "OPENAI_API_KEY")
        else f"file:{default_auth_json_path(source)}"
    )
    global_base = (
        _env_text(source, "OPENAI_BASE_URL")
        or _env_text(source, _BASE_URL_ENV)
        or (provider_cfg.base_url if provider_cfg is not None else "")
    )
    global_base_source = (
        "env:OPENAI_BASE_URL"
        if _env_text(source, "OPENAI_BASE_URL")
        else (
            f"env:{_BASE_URL_ENV}"
            if _env_text(source, _BASE_URL_ENV)
            else f"file:{default_codex_config_path(source)}"
        )
    )
    routes: list[ModelApiRoute] = []
    for route_name in ("engineer", "reviewer", "planner", "text", "image", "image_review"):
        api_key, key_source = _route_env_value(source, route_name, "api_key")
        if not api_key:
            api_key = global_key
            key_source = global_key_source if global_key else "missing"
        base_url, base_url_source = _route_env_value(source, route_name, "base_url")
        if not base_url:
            base_url = global_base
            base_url_source = global_base_source if global_base else "missing"
        model, model_source = _route_env_value(source, route_name, "model")
        if not model:
            model = _DEFAULT_ROUTE_MODELS[route_name]
            model_source = "default"
        wire_api, _wire_source = _route_env_value(source, route_name, "wire_api")
        if not wire_api:
            wire_api = (
                "images"
                if route_name == "image"
                else (provider_cfg.wire_api if provider_cfg is not None else "responses")
            )
        routes.append(
            ModelApiRoute(
                name=route_name,
                api_key=api_key,
                base_url=base_url,
                model=model,
                provider=provider_cfg.name if provider_cfg is not None else "codex",
                wire_api=wire_api,
                key_source=key_source,
                base_url_source=base_url_source,
                model_source=model_source,
                vault_path=vault_path,
            )
        )
    return routes


def bootstrap_model_api_vault(env: Mapping[str, str] | None = None) -> Path:
    routes = discover_model_api_routes(env)
    if not any(route.api_key for route in routes):
        raise RuntimeError("missing OPENAI_API_KEY or ~/.codex/auth.json")
    if not any(route.base_url for route in routes):
        raise RuntimeError("missing OPENAI_BASE_URL, ARGUS_SKILL_MODEL_API_BASE_URL, or ~/.codex/config.toml")
    return save_model_api_routes(routes)


def status_payload(env: Mapping[str, str]) -> dict[str, Any]:
    grant = load_model_api_grant(env)
    vault = default_vault_path(env)
    route_names = ("engineer", "reviewer", "text", "image", "image_review")
    routes: dict[str, dict[str, Any]] = {}
    for route_name in route_names:
        route = load_model_api_route(route_name, env)
        routes[route_name] = {
            "available": bool(route and route.usable),
            "provider": route.provider if route else "",
            "wire_api": route.wire_api if route else "",
            "base_url": route.base_url if route else "",
            "model": route.model if route else "",
            "key_source": route.key_source if route else "missing",
            "base_url_source": route.base_url_source if route else "missing",
            "model_source": route.model_source if route else "missing",
        }
    any_route_available = any(route.get("available") for route in routes.values())
    image_route = routes.get("image", {})
    image_review_route = routes.get("image_review", {})
    return {
        "vault_path": str(vault),
        "vault_exists": vault.exists(),
        "model_api_available": any_route_available,
        "provider": grant.provider if grant else "",
        "wire_api": grant.wire_api if grant else "",
        "base_url": grant.base_url if grant else "",
        "key_source": grant.key_source if grant else "missing",
        "base_url_source": grant.base_url_source if grant else "missing",
        "text_models": list(grant.text_models) if grant else [],
        "image_model": str(image_route.get("model") or (grant.image_model if grant else "")),
        "image_review_model": str(
            image_review_route.get("model") or (grant.image_review_model if grant else "")
        ),
        "routes": routes,
    }


def _gpu_resources_path() -> Path:
    return Path.home() / ".argus-skill" / "capabilities" / "gpu_resources.json"


def load_gpu_resources() -> dict[str, Any] | None:
    """Load GPU resource allocation from the capability vault."""
    path = _gpu_resources_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def gpu_env_vars() -> dict[str, str]:
    """Return env vars that restrict GPU access to allocated devices."""
    config = load_gpu_resources()
    if not config:
        return {}
    cuda_vis = config.get("cuda_visible_devices", "")
    if not cuda_vis:
        return {}
    return {"CUDA_VISIBLE_DEVICES": str(cuda_vis)}


def format_gpu_context() -> str:
    """Format GPU allocation info for agent runtime context."""
    config = load_gpu_resources()
    if not config:
        return ""
    devices = config.get("allowed_devices", [])
    cuda_vis = config.get("cuda_visible_devices", "")
    max_mem = config.get("max_gpu_memory_gb")
    notes = config.get("notes", "")
    lines = [
        "## GPU Resource Allocation",
        f"- CUDA_VISIBLE_DEVICES={cuda_vis}",
        f"- Allowed devices: {devices}",
    ]
    if max_mem:
        lines.append(f"- Max GPU memory: {max_mem} GB")
    if notes:
        lines.append(f"- {notes}")
    lines.append("- ALL training/inference commands MUST set CUDA_VISIBLE_DEVICES={} before running.".format(cuda_vis))
    lines.append("- Do NOT use devices outside this allocation.")
    return "\n".join(lines)


def format_api_context() -> str:
    """Format available API routes for agent runtime context."""
    lines = ["## Available APIs"]
    vault_path = Path.home() / ".argus-skill" / "capabilities" / "model_api.json"
    lines.append(f"- Vault file: `{vault_path}` (contains API keys, read with Python)")
    lines.append("- Load API key in code: `json.load(open('{vault}'))['capabilities']['model_api']['routes']['<route>']['api_key']`".format(vault=vault_path))

    for route_name in ("text", "image", "image_review"):
        route = load_model_api_route(route_name)
        if route and route.usable:
            lines.append(f"- **{route_name}**: model=`{route.model}`, base_url=`{route.base_url}`")

    lines.append("")
    lines.append("Use these APIs for reward models, VLM scoring, image generation, etc.")
    lines.append("Example: Qwen-VL or GPT-5.4 as reward model via the text route.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m argus_skill.tools.capability_vault")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="print capability status without secrets")
    init = sub.add_parser("init-model-api", help="persist the pre-approved model API grant")
    init.add_argument("--force", action="store_true", help="accepted for idempotent scripts")
    args = parser.parse_args(argv)

    if args.cmd == "status":
        print(json.dumps(status_payload(os.environ), indent=2, sort_keys=True))
        return 0
    if args.cmd == "init-model-api":
        path = bootstrap_model_api_vault(os.environ)
        print(f"argus-skill: model API capability saved at {path} (0600, secret not printed)")
        return 0
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
