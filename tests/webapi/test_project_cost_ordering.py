"""Cost polling must cover the same recent sessions as the project picker."""

from pathlib import Path

from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.webapi import server


def test_shadowed_costs_do_not_displace_visible_projects(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    (primary / "projects" / "s-shadowed").mkdir(parents=True)
    for sid, activity in [("s-shadowed", 100), ("s-visible", 10)]:
        write_session_meta(secondary, SessionMeta(
            id=sid, objective="cost ownership", created=1, last_active=activity,
        ))

    with TestClient(server.create_app(global_root=primary, session_roots=[secondary])) as client:
        projects = client.get("/api/projects?limit=1").json()["projects"]
        costs = client.get("/api/projects/costs?limit=1").json()["projects"]

    assert [row["id"] for row in projects] == ["s-visible"]
    assert [row["id"] for row in costs] == ["s-visible"]
