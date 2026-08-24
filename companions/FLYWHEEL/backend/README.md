# ARGUS / FLYWHEEL backend

Standalone FastAPI control plane. It never imports or mutates the Argus source
tree. Argus installations are reached through their documented WebAPI.

```powershell
uv sync --extra test
uv run uvicorn foundry.app:app --reload --port 8743
uv run pytest
```

Environment variables:

- `FLYWHEEL_DATABASE_PATH`: SQLite path (default `runtime/flywheel.db`).
- `FLYWHEEL_DATA_DIR`: runtime objects, episodes, queues and snapshots.
- `FLYWHEEL_SEED_DATA_DIR`: directory containing the calendar and seed catalog.
- `FLYWHEEL_CORS_ORIGINS`: comma-separated frontend origins (default `http://127.0.0.1:5175`).
- `FLYWHEEL_POLL_INTERVAL_SECONDS`: connection poll interval; `0` disables it.
- `FLYWHEEL_AUTO_SEED`: `true`/`false`.

Legacy `FOUNDRY_*` variables are deliberately ignored. This prevents a FLYWHEEL
process from accidentally opening an older rollback service's database or runtime.

API documentation is available at `/docs`. Bearer tokens accepted for Argus
connections are write-only: responses expose only `has_token`.
