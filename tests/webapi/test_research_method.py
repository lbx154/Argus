from __future__ import annotations

import importlib
import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.webapi.routes.research_method import ERROR_DETAIL_LIMIT
from argus.webapi.server import create_app

MODULE_NAME = "argus.verticals.research.method_card"

METHOD = """# Contrastive pretraining with a queue

The encoder feeds a queue of negatives that is refreshed every step.

## Components

| Component | Prescribes | Notes |
|---|---|---|
| Encoder | "a ResNet-50 backbone with a two-layer projection head" | |
| Queue | "65536 negatives, first-in first-out" | simplified to 4096 on one card |

## Protocol

Five seeds, report the median.

## What would falsify the claim

Removing the queue leaves accuracy unchanged.
"""

DERIVED = {
    "exists": True,
    "path": "METHOD.md",
    "updated_at": 1_700_000_000.0,
    "title": "Contrastive pretraining with a queue",
    "statement": "The encoder feeds a queue of negatives that is refreshed every step.",
    "markdown": METHOD,
    "truncated": False,
    "components": [
        {
            "component": "Encoder",
            "prescribes": "a ResNet-50 backbone with a two-layer projection head",
            "notes": "",
            "status": "proven",
            "tests": [
                {
                    "id": "tests/spec/test_knockouts.py::test_encoder",
                    "kind": "knockout",
                    "outcome": "PASSED",
                }
            ],
        },
        {
            "component": "Queue",
            "prescribes": "65536 negatives",
            "notes": "",
            "status": "untested",
            "tests": [],
        },
    ],
    "protocol": "Five seeds, report the median.",
    "falsifiers": "Removing the queue leaves accuracy unchanged.",
    "reused_code": [
        {
            "name": "encoder-lib",
            "kind": "third_party",
            "revision_or_version": "abc123",
            "remote": "https://example.invalid/encoder-lib.git",
            "modules": ["encoder_lib.backbone"],
            "imported_from": ["src/model.py"],
        }
    ],
    "hyperparameters": [
        {
            "key": "queue.size",
            "value": "65536",
            "file": "configs/pretrain.yaml",
            "why": "matches the route",
            "changed": True,
            "previous": "32768",
        }
    ],
    "change_log": [{"when": "2026-09-16", "summary": "Add the queue", "files": ["src/queue.py"]}],
    "checks": {
        "round_index": 3,
        "ran_at": 1_700_000_100.0,
        "exit_code": 0,
        "counts": {"PASSED": 1},
    },
}


def _client(tmp_path, *, method: str | bytes | None = None):
    home, workspace = tmp_path / "state", tmp_path / "workspace"
    workspace.mkdir()
    if isinstance(method, str):
        (workspace / "METHOD.md").write_text(method, encoding="utf-8")
    elif isinstance(method, bytes):
        (workspace / "METHOD.md").write_bytes(method)
    write_session_meta(home, SessionMeta(id="demo", workdir=str(workspace)))
    client = TestClient(create_app(global_root=home, auth_token="token"))
    return client, workspace, {"Authorization": "Bearer token"}


def _stub_derivation(monkeypatch, derive):
    """Install ``derive`` as the vertical's derive_method_card, whether or not the real module exists."""
    module = types.ModuleType(MODULE_NAME)
    module.derive_method_card = derive  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, MODULE_NAME, module)
    return module


def test_method_route_requires_auth_and_unknown_project_is_404(tmp_path, monkeypatch):
    _stub_derivation(monkeypatch, lambda workdir: dict(DERIVED))
    client, _workspace, headers = _client(tmp_path, method=METHOD)
    assert client.get("/api/projects/demo/research/method").status_code == 401
    assert client.get("/api/projects/missing/research/method", headers=headers).status_code == 404


def test_method_route_reports_absence_without_deriving_or_creating_anything(tmp_path, monkeypatch):
    calls: list[Path] = []
    _stub_derivation(monkeypatch, lambda workdir: calls.append(workdir) or dict(DERIVED))
    client, workspace, headers = _client(tmp_path)
    response = client.get("/api/projects/demo/research/method", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"exists": False}
    assert calls == []
    assert not (workspace / "METHOD.md").exists()


def test_method_route_returns_the_derived_card_for_the_workspace(tmp_path, monkeypatch):
    calls: list[Path] = []
    _stub_derivation(monkeypatch, lambda workdir: calls.append(workdir) or dict(DERIVED))
    client, workspace, headers = _client(tmp_path, method=METHOD)
    response = client.get("/api/projects/demo/research/method", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == DERIVED
    assert calls == [workspace.resolve()]
    # The route only reads; a POST is not offered.
    assert (
        client.post("/api/projects/demo/research/method", headers=headers, json={}).status_code
        == 405
    )


def test_method_route_rejects_symlink_leaving_the_workspace(tmp_path, monkeypatch):
    _stub_derivation(monkeypatch, lambda workdir: dict(DERIVED))
    client, workspace, headers = _client(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "METHOD.md").write_text("# Secret\n", encoding="utf-8")
    (workspace / "METHOD.md").symlink_to(elsewhere / "METHOD.md")
    assert client.get("/api/projects/demo/research/method", headers=headers).status_code == 409


def test_method_route_turns_a_broken_derivation_into_a_soft_error(tmp_path, monkeypatch):
    def explode(workdir):
        raise RuntimeError("config parser fell over " + "x" * 500)

    _stub_derivation(monkeypatch, explode)
    client, _workspace, headers = _client(tmp_path, method=METHOD)
    response = client.get("/api/projects/demo/research/method", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["exists"] is False
    assert body["error"].startswith("RuntimeError: config parser fell over")
    assert len(body["error"]) <= ERROR_DETAIL_LIMIT


def test_method_route_soft_fails_when_the_derivation_module_is_missing(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, MODULE_NAME, None)  # makes the import raise ImportError
    client, _workspace, headers = _client(tmp_path, method=METHOD)
    response = client.get("/api/projects/demo/research/method", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["exists"] is False
    assert "ImportError" in body["error"] or "ModuleNotFoundError" in body["error"]


def test_method_route_soft_fails_when_the_derivation_returns_garbage(tmp_path, monkeypatch):
    _stub_derivation(monkeypatch, lambda workdir: ["not", "a", "card"])
    client, _workspace, headers = _client(tmp_path, method=METHOD)
    body = client.get("/api/projects/demo/research/method", headers=headers).json()
    assert body["exists"] is False
    assert "error" in body


def test_method_route_serves_the_real_derivation_end_to_end(tmp_path):
    try:
        module = importlib.import_module(MODULE_NAME)
    except (
        ImportError
    ) as exc:  # pragma: no cover - only while the vertical module is still being written
        pytest.skip(f"{MODULE_NAME} is not importable yet: {exc}")
    assert callable(getattr(module, "derive_method_card", None)), (
        "vertical must expose derive_method_card"
    )
    client, workspace, headers = _client(tmp_path, method=METHOD)
    response = client.get("/api/projects/demo/research/method", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exists"] is True
    assert body["path"] == "METHOD.md"
    assert body["title"] == "Contrastive pretraining with a queue"
    assert body["statement"].startswith("The encoder feeds a queue of negatives")
    assert body["markdown"] == METHOD
    assert body["truncated"] is False
    updated = body["updated_at"]
    if isinstance(updated, str):  # ISO-8601 is the other accepted spelling of the mtime
        updated = datetime.fromisoformat(updated).timestamp()
    assert updated == pytest.approx((workspace / "METHOD.md").stat().st_mtime, abs=1.0)
    for key in ("components", "reused_code", "hyperparameters", "change_log"):
        assert isinstance(body[key], list), key
    assert body["checks"] is None or isinstance(body["checks"], dict)
    assert isinstance(body["protocol"], str) and isinstance(body["falsifiers"], str)
    for row in body["components"]:
        assert {"component", "prescribes", "notes", "status", "tests"} <= set(row)
        assert row["status"] in {"proven", "contradicted", "partial", "untested", "unchecked"}
        assert row["tests"] == []  # no spec tests were ever run in this workspace
    # A read must not leave anything visible behind in the project (a hidden .argus/ cache is tolerated).
    assert sorted(p.name for p in workspace.iterdir() if not p.name.startswith(".")) == [
        "METHOD.md"
    ]
