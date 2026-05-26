from __future__ import annotations

import io
from email.message import Message
from urllib.error import HTTPError

import pytest

from argus_skill.tools.capability_vault import ModelApiRoute
from argus_skill.tools.project_templates.code import llm


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _route() -> ModelApiRoute:
    return ModelApiRoute(
        name="engineer",
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model="gpt-test",
    )


def _http_error(status: int, body: bytes, *, retry_after: str | None = None) -> HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return HTTPError(
        "https://example.invalid/v1/responses",
        status,
        "test error",
        headers,
        io.BytesIO(body),
    )


def test_project_template_llm_retries_transient_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    sleeps: list[float] = []

    def fake_urlopen(request: object, timeout: float) -> _Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _http_error(429, b"rate limit", retry_after="0")
        return _Response(b'{"output_text": "ok"}')

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(llm.time, "sleep", sleeps.append)

    data = llm._post(_route(), "/responses", {"model": "gpt-test"}, max_retries=2)

    assert data == {"output_text": "ok"}
    assert calls == 2
    assert sleeps == [1.0]


def test_project_template_llm_does_not_retry_bad_request(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    sleeps: list[float] = []

    def fake_urlopen(request: object, timeout: float) -> _Response:
        nonlocal calls
        calls += 1
        raise _http_error(400, b"bad payload")

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(llm.time, "sleep", sleeps.append)

    with pytest.raises(llm.ModelCallError, match="HTTP 400: bad payload"):
        llm._post(_route(), "/responses", {"model": "gpt-test"}, max_retries=3)

    assert calls == 1
    assert sleeps == []
