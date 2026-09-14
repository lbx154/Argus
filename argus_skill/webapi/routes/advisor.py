"""Project advisor configuration; reads never start a consultation."""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from ...advisor.config import AdvisorConfigError, load_advisor_config, save_advisor_config
from ...agent_cli.runner_backend import SUPPORTED_BACKENDS
from .context import ServerContext


class AdvisorSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: bool | None = None
    backend: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=1024)
    effort: str | None = Field(default=None, max_length=30)
    timeout_seconds: int | None = Field(default=None, ge=1, le=1800)
    max_calls_per_turn: int | None = Field(default=None, ge=1, le=20)
    max_evidence_bytes: int | None = Field(default=None, ge=1024, le=262144)


def _settings(root) -> dict:
    saved = load_advisor_config(root, env={}).to_dict()
    effective = load_advisor_config(root).to_dict()
    # Executable selection belongs to the local runtime, not a browser form.
    saved.pop("runner_bin", None)
    effective.pop("runner_bin", None)
    return {
        "saved": saved,
        "config": effective,
        "overridden_fields": [key for key in effective if effective[key] != saved[key]],
        "supported_backends": list(SUPPORTED_BACKENDS),
        "model_options": _model_options(),
    }


def _model_options() -> list[dict[str, str]]:
    """Expose registered model IDs, never provider URLs or credentials."""
    directory = os.environ.get("PI_CODING_AGENT_DIR", "")
    if not directory:
        return []
    try:
        with (Path(directory) / "models.json").open("rb") as handle:
            raw = handle.read(262145)
        if len(raw) > 262144:
            return []
        value = json.loads(raw)
        providers = value.get("providers", {})
        if not isinstance(providers, dict):
            return []
        options = []
        for provider, config in providers.items():
            if not isinstance(config, dict) or not isinstance(config.get("models"), list):
                continue
            for row in config["models"]:
                if isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"]:
                    options.append({"backend": "pi", "model": f"{provider}/{row['id']}"})
                    if len(options) == 128:
                        return options
        return options
    except (OSError, ValueError, AttributeError):
        return []


def register_advisor_routes(app, ctx: ServerContext) -> None:
    @app.get("/api/projects/{sid}/advisor/config", dependencies=[Depends(ctx.require_auth)])
    def settings(sid: str) -> dict:
        root = ctx.resolve_or_404(sid)
        try:
            return _settings(root)
        except AdvisorConfigError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/projects/{sid}/advisor/config", dependencies=[Depends(ctx.require_auth)])
    def update_settings(sid: str, body: AdvisorSettingsIn) -> dict:
        root = ctx.resolve_or_404(sid)
        try:
            save_advisor_config(root, body.model_dump(exclude_unset=True, exclude_none=True))
            return _settings(root)
        except AdvisorConfigError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/projects/{sid}/advisor/consultations", dependencies=[Depends(ctx.require_auth)])
    def consultations(sid: str, limit: int = Query(default=10, ge=1, le=50)) -> dict:
        from ...advisor.receipts import recent_receipts

        return {"consultations": recent_receipts(ctx.resolve_or_404(sid), limit=limit)}
