from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from foundry.app import create_app
from foundry.config import Settings
from foundry.flywheel_models import MAX_PDF_BYTES, PdfReviewPayload


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_path=tmp_path / "flywheel.db",
        data_dir=tmp_path / "runtime",
        seed_data_dir=tmp_path / "missing-seeds",
        cors_origins=("http://localhost:5174",),
        poll_interval_seconds=0,
        auto_seed=False,
    )
    with TestClient(create_app(settings)) as value:
        yield value


def _episode(client: TestClient) -> str:
    response = client.post(
        "/api/episodes",
        json={
            "title": "External review evidence",
            "objective": "Import reviewer evidence through an explicit human gate.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _confirm(client: TestClient, batch_id: str) -> dict:
    response = client.post(
        f"/api/review-imports/{batch_id}/confirm",
        json={
            "actor": "paper-owner",
            "redaction_confirmed": True,
            "training_consent": False,
            "license_basis": "author-controlled review evidence; archival use only",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _stored_object_bytes(client: TestClient, digest: str) -> tuple[dict, bytes]:
    row = client.app.state.db.fetch_one(
        "SELECT * FROM content_objects WHERE sha256=?", (digest,)
    )
    assert row is not None
    path = (
        client.app.state.settings.data_dir
        / "data-vault"
        / "objects"
        / row["storage_path"]
    )
    return dict(row), path.read_bytes()


def test_pdf_is_staged_as_base64_but_sealed_as_exact_pdf_bytes(
    client: TestClient,
) -> None:
    episode_id = _episode(client)
    pdf = b"%PDF-1.7\n% FLYWHEEL review packet\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
    encoded = base64.b64encode(pdf).decode("ascii")
    staged = client.post(
        f"/api/episodes/{episode_id}/review-imports",
        json={
            "source_kind": "pdf",
            "payload": {
                "filename": "review-packet.pdf",
                "mime_type": "application/pdf",
                "content_base64": encoded,
            },
            "source_ref": "author upload",
        },
    )
    assert staged.status_code == 201, staged.text
    draft = staged.json()
    assert draft["fetch_performed"] is False
    assert draft["needs_human_confirmation"] is True
    assert draft["raw_object_sha256"] is None
    assert client.app.state.db.fetch_all("SELECT * FROM content_objects") == []
    batch = client.app.state.db.fetch_one(
        "SELECT raw_payload_json FROM review_import_batches WHERE id=?", (draft["id"],)
    )
    staged_payload = json.loads(batch["raw_payload_json"])
    assert staged_payload["content_base64"] == encoded
    assert "content_utf8" not in staged_payload

    confirmed = _confirm(client, draft["id"])
    row, stored = _stored_object_bytes(client, confirmed["raw_object_sha256"])
    assert row["media_type"] == "application/pdf"
    assert row["byte_length"] == len(pdf)
    assert stored == pdf
    assert not stored.startswith(b'{"content_base64"')


@pytest.mark.parametrize(
    "payload",
    [
        {
            "filename": "../review.pdf",
            "mime_type": "application/pdf",
            "content_base64": base64.b64encode(b"%PDF-1.4\n").decode("ascii"),
        },
        {
            "filename": "review.pdf",
            "mime_type": "text/plain",
            "content_base64": base64.b64encode(b"%PDF-1.4\n").decode("ascii"),
        },
        {
            "filename": "review.pdf",
            "mime_type": "application/pdf",
            "content_base64": "not+strict/base64!",
        },
        {
            "filename": "review.pdf",
            "mime_type": "application/pdf",
            "content_base64": base64.b64encode(b"not a PDF").decode("ascii"),
        },
    ],
)
def test_pdf_transport_validation_rejects_unsafe_payloads(
    client: TestClient, payload: dict[str, str]
) -> None:
    episode_id = _episode(client)
    response = client.post(
        f"/api/episodes/{episode_id}/review-imports",
        json={"source_kind": "pdf", "payload": payload},
    )
    assert response.status_code == 422
    assert client.app.state.db.fetch_all("SELECT * FROM review_import_batches") == []


def test_pdf_size_limit_is_ten_mib() -> None:
    encoded = base64.b64encode(b"%PDF-" + b"x" * (MAX_PDF_BYTES - 5)).decode("ascii")
    accepted = PdfReviewPayload(
        filename="limit.pdf", mime_type="application/pdf", content_base64=encoded
    )
    assert len(accepted.decoded_bytes()) == MAX_PDF_BYTES
    oversized = base64.b64encode(b"%PDF-" + b"x" * (MAX_PDF_BYTES - 4)).decode("ascii")
    with pytest.raises(ValueError, match="10 MiB|at most"):
        PdfReviewPayload(
            filename="too-large.pdf",
            mime_type="application/pdf",
            content_base64=oversized,
        )


class _FakeResponse:
    def __init__(
        self,
        payload: bytes,
        *,
        url: str,
        content_type: str = "application/json; charset=utf-8",
        content_length: str | None = None,
    ) -> None:
        self.payload = payload
        self.url = url
        self.status = 200
        self.headers = {
            "Content-Type": content_type,
            "Content-Length": content_length if content_length is not None else str(len(payload)),
        }

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def geturl(self) -> str:
        return self.url

    def read(self, amount: int) -> bytes:
        return self.payload[:amount]


def test_openreview_public_api2_fetch_is_restricted_staged_and_human_gated(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    episode_id = _episode(client)
    forum_id = "AbC_123-note"
    raw_json = b'{"notes":[{"id":"AbC_123-note","content":{"rating":{"value":7}}}]}'
    captured: dict[str, object] = {}

    def fake_open(request, *, timeout: int):  # type: ignore[no-untyped-def]
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        return _FakeResponse(raw_json, url=request.full_url)

    monkeypatch.setattr("foundry.flywheel_api._openreview_http_open", fake_open)
    staged_response = client.post(
        f"/api/episodes/{episode_id}/review-imports/openreview",
        json={"forum_id": forum_id},
    )
    assert staged_response.status_code == 201, staged_response.text
    staged = staged_response.json()
    parsed_url = urlsplit(str(captured["url"]))
    assert parsed_url.scheme == "https"
    assert parsed_url.hostname == "api2.openreview.net"
    assert parsed_url.path == "/notes"
    assert parse_qs(parsed_url.query) == {"forum": [forum_id]}
    headers = {key.lower(): value for key, value in dict(captured["headers"]).items()}
    assert headers["accept"] == "application/json"
    assert headers["user-agent"].startswith("ARGUS-FLYWHEEL/")
    assert "authorization" not in headers
    assert "cookie" not in headers
    assert captured["timeout"] == 10
    assert staged["fetch_performed"] is True
    assert staged["source_ref"] == captured["url"]
    assert staged["needs_human_confirmation"] is True
    assert staged["raw_object_sha256"] is None
    assert staged["parsed"] == json.loads(raw_json)
    row = client.app.state.db.fetch_one(
        "SELECT raw_payload_json FROM review_import_batches WHERE id=?", (staged["id"],)
    )
    assert json.loads(row["raw_payload_json"])["content_utf8"] == raw_json.decode("utf-8")
    assert client.app.state.db.fetch_all("SELECT * FROM content_objects") == []

    confirmed = _confirm(client, staged["id"])
    object_row, stored = _stored_object_bytes(client, confirmed["raw_object_sha256"])
    assert object_row["media_type"] == "application/json"
    assert stored == raw_json


def test_openreview_rejects_credentials_redirects_and_oversize_before_staging(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    episode_id = _episode(client)
    calls = 0

    def must_not_fetch(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        raise AssertionError("invalid request must not reach the network")

    monkeypatch.setattr("foundry.flywheel_api._openreview_http_open", must_not_fetch)
    credentialed = client.post(
        f"/api/episodes/{episode_id}/review-imports/openreview",
        json={"forum_id": "AbC123", "credentials": "never-store-this"},
    )
    assert credentialed.status_code == 422
    assert calls == 0

    def redirected(request, *, timeout: int):  # type: ignore[no-untyped-def]
        return _FakeResponse(b'{"notes":[]}', url="https://example.com/stolen")

    monkeypatch.setattr("foundry.flywheel_api._openreview_http_open", redirected)
    redirect_response = client.post(
        f"/api/episodes/{episode_id}/review-imports/openreview",
        json={"forum_id": "AbC123"},
    )
    assert redirect_response.status_code == 502

    def oversized(request, *, timeout: int):  # type: ignore[no-untyped-def]
        return _FakeResponse(
            b"{}",
            url=request.full_url,
            content_length=str(2 * 1024 * 1024 + 1),
        )

    monkeypatch.setattr("foundry.flywheel_api._openreview_http_open", oversized)
    size_response = client.post(
        f"/api/episodes/{episode_id}/review-imports/openreview",
        json={"forum_id": "AbC123"},
    )
    assert size_response.status_code == 502
    assert client.app.state.db.fetch_all("SELECT * FROM review_import_batches") == []


def test_paste_and_json_import_contracts_remain_available(client: TestClient) -> None:
    episode_id = _episode(client)
    paste = client.post(
        f"/api/episodes/{episode_id}/review-imports",
        json={"source_kind": "paste", "raw_text": "Reviewer: add an ablation."},
    )
    structured = client.post(
        f"/api/episodes/{episode_id}/review-imports",
        json={"source_kind": "json", "payload": {"score": 6, "confidence": 4}},
    )
    assert paste.status_code == 201, paste.text
    assert structured.status_code == 201, structured.text
    assert paste.json()["fetch_performed"] is False
    assert structured.json()["parsed"] == {"score": 6, "confidence": 4}
