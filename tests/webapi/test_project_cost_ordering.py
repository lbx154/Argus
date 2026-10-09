"""Cost polling must cover the same recent sessions as the project picker."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.webapi import server


@pytest.mark.parametrize("limit", [1, 2, 3])
def test_cost_feed_sorts_across_roots_before_applying_limit(tmp_path: Path, limit: int) -> None:
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    for root, sid, activity in [
        (primary, "s-old", 10),
        (secondary, "s-newest", 30),
        (secondary, "s-middle", 20),
    ]:
        write_session_meta(root, SessionMeta(
            id=sid, objective="cost ordering", created=1, last_active=activity,
        ))

    with TestClient(server.create_app(global_root=primary, session_roots=[secondary])) as client:
        projects = client.get(f"/api/projects?limit={limit}").json()["projects"]
        costs = client.get(f"/api/projects/costs?limit={limit}").json()["projects"]

    expected = ["s-newest", "s-middle", "s-old"][:limit]
    assert [row["id"] for row in projects] == expected
    assert [row["id"] for row in costs] == expected


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
