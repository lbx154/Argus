from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from argus_skill.webapi.server import create_app


def test_timeline_preview_through_real_app_is_authenticated_and_read_only(tmp_path):
    client = TestClient(create_app(global_root=tmp_path, auth_token="test-token"))
    route = "/api/research/timeline/estimate"
    assert client.post(route, json={}).status_code == 401
    headers = {"Authorization": "Bearer test-token"}
    assert client.post(route, headers=headers, json={}).status_code == 422
    body = dict(
        selected_proposal_id="idea",
        proposals=[
            dict(
                id="idea",
                title="Paper",
                tasks=[
                    dict(
                        id="pilot",
                        title="Validation",
                        phase="validation",
                        duration_hours=[1, 2, 9],
                        basis="Related published runtime",
                    )
                ],
            )
        ],
    )
    response = client.post(route, headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["proposals"][0]["finish_hours"]["expected"] == 3
    assert not list(tmp_path.rglob("backlog.jsonl"))
    assert not list(tmp_path.rglob("000001.json"))
