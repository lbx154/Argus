"""Read-only historical maps, stored separately from runnable sessions."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from fastapi import Depends, HTTPException

from ..map_view import with_revisions


def register_map_dataset_routes(app, ctx) -> None:
    def read(name: str):
        configured = os.environ.get("ARGUS_MAP_DATASETS_DIR", "")
        if not configured:
            if name == "index":
                return {"datasets": []}
            raise HTTPException(404, "map dataset unavailable")
        root = Path(configured).resolve()
        if not re.fullmatch(r"[a-z0-9-]{1,80}", name):
            raise HTTPException(404, "unknown dataset")
        path = root / f"{name}.json"
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 5 * 1024 * 1024:
            raise HTTPException(404, "map dataset unavailable")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(503, "map dataset could not be read") from exc
        if not isinstance(value, dict):
            raise HTTPException(503, "invalid map dataset document")
        return with_revisions(value) if name != "index" else value

    @app.get("/api/map-datasets", dependencies=[Depends(ctx.require_auth)])
    def index():
        return read("index")

    @app.get("/api/map-datasets/{dataset_id}", dependencies=[Depends(ctx.require_auth)])
    def dataset(dataset_id: str):
        if dataset_id == "index":
            raise HTTPException(404, "unknown dataset")
        value = read(dataset_id)
        if value.get("read_only") is not True or value.get("id") != dataset_id:
            raise HTTPException(503, "invalid map dataset")
        return value

    from .map_live import register_map_live_routes

    register_map_live_routes(app, ctx, read)
