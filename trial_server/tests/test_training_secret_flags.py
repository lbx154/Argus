"""Training capture flags secrets even though Argus no longer masks them by default."""
from __future__ import annotations

import pytest

# Credential-shaped values that masking (when on) and detection (always) must
# catch: literals, prefix-only references, calls on arbitrary names, header
# literals.
_MUST_STAY_MASKED = (
    'token = "live-abcdef1234567890"',
    "password=Summer2024(xyz)",
    "password=hunter2hunter2",
    "api_key: AbCdEf0123456789",
    "SERVICE_TOKEN=abcd1234efgh5678",
    "token=$ecretPass99",
    "auth=Abc(defghijk",
    "secret = eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl",
    "Authorization: Bearer abcdefghijklmnopqrstu",
    "Authorization: Basic dXNlcjpwYXNzd29yZA==",
    "Authorization: Bearer <sk_live_abcdef1234567890abcdef>",
    "Authorization: Bearer <real token value 1234567890 abcdef>",
    "Authorization: Token {a1b2c3d4e5f6a7b8c9d0}",
    "Authorization: Basic $dXNlcjpwYXNzd29yZA",
    "Authorization: token $hunter2password",
    "Authorization: Bearer ${abc123realtokenvalue}",
    "api_key={real}Xk9fooBarBazQux1234",
    "api_key={realsecretvalue1234}",
    "password=${Xk9fooBarBaz",
    "password=$(Xk9!fooBarBaz",
    "password={{Xk9fooBarBaz",
    "password=$SECRETPASSWORD9",
    "password=$ADMINPASS2024",
    "password=$UPER_S3CR3T",
    "api_key=sk_live_51HabcdefGHIJ123(x)",
    "api_key=sk_live_51HabcdefGHIJ123[0]",
    "password=supersecret(",
    "password=correcthorse[1]battery",
    "token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcDEF123(",
    "token=f(abcdefghijklmnopqrstuvwxyz0123456789)",
    "token=a.b(REALSECRETVALUE1234567)",
    "OPENAI_API_KEY=os.environ_SECRETVALUE123",
    "token=process.envREALSECRET123",
    "token=os.environXYZREAL",
    "token=os.environ.get(REALSECRETVALUE1)x",
    "secret=my_s3cr3t_pass(word)",
    "client_secret=Ab_CdEfGh12345[",
)


@pytest.fixture
def redaction_default(monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_REDACT_SECRETS", raising=False)


@pytest.mark.parametrize("line", _MUST_STAY_MASKED)
def test_training_capture_still_flags_secrets_by_default(redaction_default, line: str) -> None:
    from argus_trial_server.training_capture import _content_diagnostic
    from argus_trial_server.training_data import _sensitive

    diagnostic = _content_diagnostic("message", {"content": line}, sid="s", mission_id="m")
    assert diagnostic is not None
    assert diagnostic["detector"] == "secret_redactor"
    assert _sensitive({"content": line})
