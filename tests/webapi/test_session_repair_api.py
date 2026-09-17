"""Authenticated operator surface cannot supply evidence or arbitrary paths."""
from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.webapi.server import create_app
from tests.core.test_session_repair import (
    evidence,  # noqa: F401 -- shared synthetic original-store fixture
)


def test_repair_api_is_authenticated_preview_first_and_rejects_forged_evidence(evidence, tmp_path):  # noqa: F811
    project, session, call_id, _ = evidence
    write_session_meta(tmp_path, SessionMeta(id=project.name, created=1, last_active=1))
    write_session_meta(tmp_path, SessionMeta(id="other-project", created=1, last_active=1))
    path = f"/api/projects/{project.name}/cost-control/session-repair"
    body = {"call_id": call_id, "session_id": session}
    headers = {"Authorization": "Bearer fixture-token"}
    with TestClient(create_app(global_root=tmp_path, auth_token="fixture-token")) as client:
        assert client.post(path, json=body).status_code == 401
        for extra in ({"events": []}, {"evidence_path": "/private"}, {"cost_usd": 0}):
            assert client.post(path, json=body | extra, headers=headers).status_code == 422
        wrong = path.replace(project.name, "other-project")
        assert client.post(wrong, json=body, headers=headers).status_code in {404, 409}
        result = client.post(path, json=body, headers=headers)
        assert result.status_code == 200, result.text
        view = result.json()["repair"]
        assert not view["applied"]
        commit = body | {"dry_run": False, "expected_row_hash": view["expected_row_hash"],
                         "expected_evidence_hash": view["evidence_hash"], "reason": "Synthetic operator approval"}
        refused = client.post(path, json=body | {"dry_run": False}, headers=headers)
        assert refused.status_code == 409
        assert "preview hashes" in refused.json()["detail"]
        applied = client.post(path, json=commit, headers=headers)
        assert applied.status_code == 200, applied.text
        assert applied.json()["repair"]["applied"]
        assert not applied.json()["repair"]["billing_reconciled"]
        status = client.get(path.removesuffix("/session-repair"), headers=headers)
        assert status.status_code == 200, status.text
        assert status.json()["cost_control"]["accounting_state"] == "accounting_pending"
        assert status.json()["admission_reason"].startswith("unresolved provider cost")
        acknowledged = client.post(path.removesuffix("/session-repair") + "/acknowledge",
            json={"call_id": call_id, "liability_usd": 1.0, "reason": "Separate synthetic liability acceptance"},
            headers=headers)
        assert acknowledged.status_code == 200, acknowledged.text
        accepted_state = acknowledged.json()["cost_control"]
        assert accepted_state["blocking_unresolved_calls"] == 0
        assert accepted_state["accounting_state"] == "accounting_pending"
        assert accepted_state["unresolved_calls"] == 1
