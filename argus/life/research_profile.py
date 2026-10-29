"""Research-profile context for long-running life-mode projects."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, MutableMapping

from ..tools.capability_vault import (
    default_vault_path,
    load_model_api_grant,
    load_model_api_route,
    status_payload,
)

_PROFILE_ENV = "ARGUS_SKILL_RESEARCH_PROFILE"
_PROFILE_PATH_ENV = "ARGUS_SKILL_RESEARCH_PROFILE_PATH"
_DEFAULT_TEXT_MODELS = "gpt-5.5,gpt-5.5"
_DEFAULT_IMAGE_MODEL = "gpt-image-2"
_SHARED_MODEL_CACHE_ROOT_ENV = "ARGUS_SKILL_SHARED_MODEL_CACHE_ROOT"


@dataclass(frozen=True)
class ResearchProfile:
    name: str
    text: str


def _env_text(env: Mapping[str, str], key: str) -> str:
    return str(env.get(key) or "").strip()


def _read_profile_file(path: str) -> str:
    if not path:
        return ""
    try:
        return Path(path).expanduser().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _shared_model_cache_root(env: Mapping[str, str]) -> Path:
    raw = _env_text(env, _SHARED_MODEL_CACHE_ROOT_ENV)
    return Path(raw).expanduser() if raw else Path.home() / ".cache"


def shared_model_cache_defaults(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the host-shared model/data cache env used by research missions."""

    source = env if env is not None else os.environ
    root = _shared_model_cache_root(source)
    hf_home = root / "huggingface"
    return {
        "XDG_CACHE_HOME": str(root),
        "HF_HOME": str(hf_home),
        "HUGGINGFACE_HUB_CACHE": str(hf_home / "hub"),
        "HF_DATASETS_CACHE": str(hf_home / "datasets"),
        "TRANSFORMERS_CACHE": str(hf_home / "hub"),
        "TORCH_HOME": str(root / "torch"),
    }


def ensure_shared_model_cache_environment(
    env: MutableMapping[str, str] | None = None,
) -> None:
    """Default all model/data downloads to the host-shared cache tree.

    Argus research missions often spawn Codex, Python, HuggingFace, and Torch
    subprocesses from different project directories. Setting these defaults in
    the daemon environment keeps model weights and datasets under one host cache
    instead of repeatedly downloading them into each workspace. Explicit
    operator-provided cache variables still win.
    """

    target = env if env is not None else os.environ
    for key, value in shared_model_cache_defaults(target).items():
        if not _env_text(target, key):
            target[key] = value


def ensure_research_api_environment(env: MutableMapping[str, str] | None = None) -> None:
    """Populate process env with pre-granted model API credentials if available.

    This never prints secrets. It copies the pre-approved key from the local
    capability vault (or the operator's Codex auth file) into the daemon process
    environment so child tool processes can call model APIs without repeatedly
    asking the human.
    """
    target = env if env is not None else os.environ
    ensure_shared_model_cache_environment(target)
    engineer = load_model_api_route("engineer", target)
    reviewer = load_model_api_route("reviewer", target)
    image = load_model_api_route("image", target)
    image_review = load_model_api_route("image_review", target)
    # OPENAI_* remains a compatibility export for generic child tools. Use
    # the engineer/text route as the default; route-aware tools read the vault.
    if engineer is not None and not _env_text(target, "OPENAI_API_KEY") and engineer.api_key:
        target["OPENAI_API_KEY"] = engineer.api_key
    if engineer is not None and not _env_text(target, "OPENAI_BASE_URL") and engineer.base_url:
        target["OPENAI_BASE_URL"] = engineer.base_url
    text_models = tuple(
        route.model for route in (engineer, reviewer) if route is not None and route.model
    )
    if not _env_text(target, "ARGUS_SKILL_TEXT_MODELS") and text_models:
        target["ARGUS_SKILL_TEXT_MODELS"] = ",".join(dict.fromkeys(text_models))
    if image is not None and not _env_text(target, "ARGUS_SKILL_IMAGE_MODEL") and image.model:
        target["ARGUS_SKILL_IMAGE_MODEL"] = image.model
    if (
        image_review is not None
        and not _env_text(target, "ARGUS_SKILL_IMAGE_REVIEW_MODEL")
        and image_review.model
    ):
        target["ARGUS_SKILL_IMAGE_REVIEW_MODEL"] = image_review.model


def _capability_context(env: Mapping[str, str]) -> str:
    grant = load_model_api_grant(env)
    key_source = grant.key_source if grant is not None else "missing"
    base_url_source = grant.base_url_source if grant is not None else "missing"
    text_models = ",".join(grant.text_models) if grant is not None else _DEFAULT_TEXT_MODELS
    image_model = grant.image_model if grant is not None else _DEFAULT_IMAGE_MODEL
    review_model = grant.image_review_model if grant is not None else "gpt-5.5"
    api_available = bool(grant and grant.usable)
    vault_path = (
        grant.vault_path if grant is not None and grant.vault_path else default_vault_path(env)
    )
    status = status_payload(env)
    raw_routes = status.get("routes")
    routes: dict[str, Any] = raw_routes if isinstance(raw_routes, dict) else {}
    raw_image_route_status = routes.get("image")
    image_route_status: dict[str, Any] = (
        raw_image_route_status if isinstance(raw_image_route_status, dict) else {}
    )
    image_tool_available = bool(image_route_status.get("available"))
    cache_defaults = shared_model_cache_defaults(env)
    cache_lines = []
    for key in (
        "HF_HOME",
        "HUGGINGFACE_HUB_CACHE",
        "HF_DATASETS_CACHE",
        "TRANSFORMERS_CACHE",
        "TORCH_HOME",
        "XDG_CACHE_HOME",
    ):
        cache_lines.append(f"- {key}: {_env_text(env, key) or cache_defaults[key]}")
    route_lines = []
    for route_name in ("engineer", "reviewer", "author", "image", "image_review"):
        route = routes.get(route_name)
        if not isinstance(route, dict):
            continue
        route_lines.append(
            "- route.{name}: available={available}, model={model}, "
            "provider={provider}, wire_api={wire_api}, base_url_source={base_url_source}, "
            "key_source={key_source}".format(
                name=route_name,
                available="yes" if route.get("available") else "no",
                model=route.get("model") or "",
                provider=route.get("provider") or "",
                wire_api=route.get("wire_api") or "",
                base_url_source=route.get("base_url_source") or "missing",
                key_source=route.get("key_source") or "missing",
            )
        )
    tool_block = (
        "- image_tool_generate: `python -m argus.tools.image_api generate "
        "--prompt-file figures/<name>.prompt.txt --out figures/<name>.png --force`\n"
        "- image_tool_inspect: `python -m argus.tools.image_api inspect "
        "--image figures/<name>.png`\n"
        "- image_tool_review (paper figures): `python -m "
        "argus.verticals.research.figure_tool review "
        "--image figures/<name>.png --out figures/<name>.review.json`\n"
    )
    return (
        "## Granted capability layer\n"
        f"- model_api_available: {'yes' if api_available else 'no'}\n"
        f"- capability_vault_path: {vault_path}\n"
        f"- model_api_key_source: {key_source}\n"
        f"- model_api_base_url_source: {base_url_source}\n"
        f"- text_models_allowed: {text_models}\n"
        f"- image_model_allowed: {image_model}\n"
        f"- image_review_model_allowed: {review_model}\n"
        f"{chr(10).join(route_lines)}\n"
        f"- image_tool_available: {'yes' if image_tool_available else 'no'}\n"
        f"{tool_block if image_tool_available else ''}"
        "- Default authorization source: the fixed capability vault above. "
        "Treat Codex auth/config files only as one-time import sources for "
        "`python -m argus.tools.capability_vault init-model-api`.\n"
        "- API routes are independent: engineer/reviewer/author/image/"
        "image_review may use different URLs, keys, providers, and models.\n"
        "- Permission model: the human has pre-approved these capabilities. Do not "
        "ask again for API access when model_api_available=yes; call the approved "
        "tool instead.\n"
        "- Isolation rule: prompts may mention capability availability and file "
        "paths, but must never read, print, summarize, or commit raw API keys. "
        "Only tool subprocesses may load the vault.\n"
        "- Use the image model for paper figures only when a figure specification "
        "is saved under figures/ or paper/; always keep the generated image, "
        "sidecar metadata, inspect output, and review output as artifacts.\n"
        "\n## Shared model/data cache layer\n"
        f"- cache_root: {_shared_model_cache_root(env)}\n"
        f"{chr(10).join(cache_lines)}\n"
        "- Rule: do not download model weights, HuggingFace hub files, datasets, "
        "or Torch checkpoints into project directories. Reuse the shared host "
        "cache paths above for every project and experiment subprocess.\n"
        "- Manuscript boundary: capability-vault paths, cache paths, local device "
        "IDs, daemon configuration, and internal route names are agent-only "
        "runtime facts. Keep them out of rendered paper prose, captions, tables, "
        "and appendix text; use paper-facing evaluated system facts instead.\n"
    )


def load_research_profile(
    env: Mapping[str, str] | None = None,
) -> ResearchProfile | None:
    source = env if env is not None else os.environ
    path_text = _read_profile_file(_env_text(source, _PROFILE_PATH_ENV))
    if path_text:
        name = _env_text(source, _PROFILE_ENV) or "custom-file"
        return ResearchProfile(name=name, text=path_text)

    name = _env_text(source, _PROFILE_ENV)
    if not name:
        return None
    text = (
        "## Research profile\n"
        f"- Active profile: {name}\n"
        "- No built-in profile text is available for this name. Use "
        f"{_PROFILE_PATH_ENV} to provide project-specific instructions."
    )
    return ResearchProfile(name=name, text=text)


def render_research_profile_context(
    env: Mapping[str, str] | None = None,
) -> str:
    if env is None:
        ensure_research_api_environment()
    profile = load_research_profile(env)
    if profile is None:
        return ""
    source = env if env is not None else os.environ
    return (
        f"{profile.text.strip()}\n\n"
        f"{_capability_context(source)}\n"
        "Profile metadata:\n"
        f"- profile_name: {profile.name}\n"
    )
