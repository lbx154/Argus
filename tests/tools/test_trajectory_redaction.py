"""Tests for trajectory redaction (B-line data-productization脱敏 pipeline)."""
from __future__ import annotations

from argus_skill.tools.trajectory_redaction import redact_record, redact_text


def test_redacts_openai_and_github_and_aws_keys():
    t = "key sk-abcdef0123456789ABCDEF and ghp_0123456789abcdefghijABCD and AKIAABCDEFGHIJKLMNOP"
    out = redact_text(t)
    assert "sk-abcdef0123456789ABCDEF" not in out
    assert "<REDACTED:openai-key>" in out
    assert "<REDACTED:github-token>" in out
    assert "<REDACTED:aws-key>" in out


def test_redacts_bearer_and_kv_secrets():
    assert "<REDACTED:token>" in redact_text("Authorization: Bearer abcdef0123456789xyz")
    out = redact_text('api_key="s3cr3t-value-1234"')
    assert "s3cr3t-value-1234" not in out
    assert "<REDACTED:secret>" in out
    # prose mention of "token" (no =/:) is NOT redacted
    assert redact_text("the token was rejected") == "the token was rejected"


def test_redacts_url_credentials_and_email():
    assert "<REDACTED:creds>@" in redact_text("clone https://user:pa55w0rd@github.com/x/y.git")
    assert "<REDACTED:email>" in redact_text("author me@example.com wrote it")


def test_collapses_home_path():
    out = redact_text("/home/alice/argus-skill/run.py failed", home="/home/alice")
    assert "/home/alice" not in out
    assert out.startswith("~/argus-skill/run.py")


def test_fail_soft_on_non_string_and_empty():
    assert redact_text("") == ""
    assert redact_text(None) is None  # type: ignore[arg-type]
    assert redact_text(123) == 123  # type: ignore[arg-type]


def test_redact_record_recurses_and_preserves_structure_and_keys():
    rec = {
        "api_key": "sk-abcdefghij0123456789KLMN",  # value redacted, key kept
        "nested": {"msg": "email me@x.com"},
        "items": ["Bearer abcdefghij0123456789", "plain text"],
        "count": 7,  # non-string preserved
    }
    out = redact_record(rec)
    assert set(out.keys()) == {"api_key", "nested", "items", "count"}  # keys intact
    assert "sk-abcdefghij0123456789KLMN" not in out["api_key"]
    assert "<REDACTED:email>" in out["nested"]["msg"]
    assert "<REDACTED:token>" in out["items"][0]
    assert out["items"][1] == "plain text"
    assert out["count"] == 7
