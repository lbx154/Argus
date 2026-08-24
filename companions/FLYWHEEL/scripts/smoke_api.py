"""In-process smoke test; never contacts Argus or external research sources."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from foundry.app import create_app
from foundry.config import Settings


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="argus-flywheel-smoke-") as temporary:
        settings = Settings(
            database_path=Path(temporary) / "flywheel.db",
            data_dir=Path(temporary) / "data",
            seed_data_dir=root / "data" / "seeds",
            cors_origins=("http://127.0.0.1:5175",),
            poll_interval_seconds=0,
        )
        with TestClient(create_app(settings)) as client:
            health = client.get("/api/health")
            assert health.status_code == 200 and health.json()["ok"] is True
            dashboard = client.get("/api/dashboard").json()
            assert dashboard["counts"]["venues"] == 58
            assert dashboard["counts"]["ideas"] == 290
            assert len(dashboard["upcoming_deadlines"]) == 85
            evidence_states = {
                item["evidence_status"] for item in dashboard["upcoming_deadlines"]
            }
            assert evidence_states <= {"official_confirmed", "forecast"}
            venues = client.get("/api/venues").json()
            assert venues["total"] == 58
            assert sum(item["deadline_count"] for item in venues["items"]) == 85
            ideas = client.get("/api/ideas", params={"limit": 500}).json()
            assert ideas["total"] == 290
            calendar = client.get("/api/calendar.ics")
            assert calendar.status_code == 200
            assert "BEGIN:VCALENDAR" in calendar.text
    print("smoke-api-ok: 58 venues, 85 deadlines, 290 ideas, health and calendar")


if __name__ == "__main__":
    main()
