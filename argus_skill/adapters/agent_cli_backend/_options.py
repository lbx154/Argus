"""Codex CLI argument parsing and model-selection resolution.

Everything here is about *what model a Codex call will actually use* and
*how to clean up its ``extra_args``* — the model precedence rules Codex's
own CLI applies (direct ``-m``/``--model`` beats ``-c model=...`` config
beats the on-disk config file), stripping obsolete profile flags, and
composing external-interrupt providers. None of it talks to a subprocess.
"""
from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)

_LEGACY_CODEX_PROFILE_SWITCHES = {"-c", "--config"}
_LEGACY_CODEX_PROFILE_PAYLOADS = {"profile=auto-max", "config_profile=auto-max"}


def _normalize_codex_config_arg(arg: str) -> str:
    return re.sub(r"\s+", "", str(arg)).replace('"', "").replace("'", "").casefold()


def _is_legacy_codex_profile_arg(arg: str) -> bool:
    return _normalize_codex_config_arg(arg) in _LEGACY_CODEX_PROFILE_PAYLOADS


def _strip_legacy_codex_profile_args(
    extra_args: list[str] | None,
) -> list[str] | None:
    """Remove obsolete auto-max profile flags that break matcher startup.

    The old launcher path forwarded ``-c profile = "auto-max"`` into the
    Codex CLI. Current matching runs do not need that profile, and the legacy
    flag now trips a config parse failure before the skill matcher can start.
    Keep other extra args intact so explicit runner overrides still work.
    """
    if not extra_args:
        return None
    cleaned: list[str] = []
    removed = False
    i = 0
    while i < len(extra_args):
        arg = extra_args[i]
        if arg in _LEGACY_CODEX_PROFILE_SWITCHES and i + 1 < len(extra_args):
            next_arg = extra_args[i + 1]
            if _is_legacy_codex_profile_arg(next_arg):
                removed = True
                log.warning(
                    "stripping legacy Codex auto-max profile args from runner config"
                )
                i += 2
                continue
        if _is_legacy_codex_profile_arg(arg):
            removed = True
            log.warning(
                "stripping legacy Codex auto-max profile arg from runner config"
            )
            i += 1
            continue
        cleaned.append(arg)
        i += 1
    if removed:
        return cleaned or None
    return list(extra_args)


def resolve_pricing_model(
    response_model: str | None,
    request_model: str | None,
    configured_default: str | None,
) -> tuple[str, str]:
    """Pick the model id to record for pricing, with a traceable fallback source.

    Returns ``(model, fallback_source)``.  ``fallback_source`` is ``""`` when the
    model came straight from the provider response (no fallback needed); it names
    where the value was recovered from otherwise: ``"request"`` (the caller's
    configured ``options.model``), ``"configured_default"`` (the backend's
    resolved default model), or ``"none"`` (nothing usable — recorded empty so
    pricing still, honestly, marks the call ``unpriced`` and the cost gate can
    block).

    The bug this fixes: a codex call that does not pin a model — e.g. every
    ``Manager`` classify call, which builds ``RunnerOptions(...)`` with no
    ``model=`` — gets no ``model`` echoed back in the codex response, so the
    usage record used to be written with an empty model.  An empty model is
    ``unpriced``, and one unresolved ``unpriced`` call trips ``cost_control``'s
    block guard, freezing every subsequent provider call on the whole root.
    Falling back to the configured/canonical model prices the call truthfully
    (it IS the model codex used) instead of silently wedging the gate.
    """
    resp = str(response_model or "").strip()
    if resp:
        return resp, ""
    req = str(request_model or "").strip()
    if req:
        return req, "request"
    default = str(configured_default or "").strip()
    if default:
        return default, "configured_default"
    return "", "none"


def _normalize_codex_selection_args(
    args: list[str] | None,
) -> tuple[list[str], str, str, str, bool]:
    """Remove model selectors while preserving unrelated Codex CLI args."""
    cleaned: list[str] = []
    direct_model = ""
    config_model = ""
    profile = ""
    ignore_user_config = False
    values = list(args or [])
    index = 0
    while index < len(values):
        value = str(values[index] or "").strip()
        if value in {"-m", "--model"} and index + 1 < len(values):
            direct_model = str(values[index + 1] or "").strip()
            index += 2
            continue
        if value.startswith("--model="):
            direct_model = value.partition("=")[2].strip()
            index += 1
            continue
        if value in {"-c", "--config"} and index + 1 < len(values):
            payload = str(values[index + 1] or "")
            key, sep, raw = payload.partition("=")
            if sep and key.strip() == "model":
                config_model = raw.strip().strip("\"'")
            else:
                cleaned.extend([value, payload])
            index += 2
            continue
        if value.startswith("--config="):
            payload = value.partition("=")[2]
            key, sep, raw = payload.partition("=")
            if sep and key.strip() == "model":
                config_model = raw.strip().strip("\"'")
            else:
                cleaned.append(value)
            index += 1
            continue
        if value in {"-p", "--profile"} and index + 1 < len(values):
            profile = str(values[index + 1] or "").strip()
            index += 2
            continue
        if value.startswith("--profile="):
            profile = value.partition("=")[2].strip()
            index += 1
            continue
        if value == "--ignore-user-config":
            ignore_user_config = True
        cleaned.append(values[index])
        index += 1
    return cleaned, direct_model, config_model, profile, ignore_user_config


def resolve_codex_execution_model(
    request_model: str | None,
    configured_model: str | None,
    default_extra_args: list[str] | None = None,
    call_extra_args: list[str] | None = None,
) -> str:
    """Resolve one model using Codex CLI's direct/config/file precedence."""
    _cleaned, default_direct, default_config, _profile, _ignore = (
        _normalize_codex_selection_args(default_extra_args)
    )
    _cleaned, call_direct, call_config, _profile, _ignore = (
        _normalize_codex_selection_args(call_extra_args)
    )
    direct = (
        str(request_model or "").strip()
        or call_direct
        or default_direct
    )
    return (
        direct
        or call_config
        or default_config
        or str(configured_model or "").strip()
    )


def _interrupt_reason(provider: Any) -> str:
    if provider is None:
        return ""
    try:
        return str(provider() or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _compose_interrupt_providers(*providers):
    active = [provider for provider in providers if provider is not None]
    if not active:
        return None
    if len(active) == 1:
        return active[0]

    def _provider() -> str | None:
        for provider in active:
            reason = provider()
            if reason:
                return str(reason)
        return None

    return _provider
