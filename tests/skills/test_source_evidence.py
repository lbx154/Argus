from __future__ import annotations

import importlib
import json


def _module():
    return importlib.import_module(
        "argus_skill.verticals.research.source_evidence"
    )


def _source(
    source_id: str,
    source_type: str,
    *,
    url: str = "https://code.claude.com/docs/en/workflows",
) -> dict:
    return {
        "source_id": source_id,
        "url": url,
        "title": "Dynamic Workflows",
        "publisher": "Anthropic",
        "source_type": source_type,
        "published_at": "",
        "updated_at": "",
        "accessed_at": "2026-07-14T12:00:00+00:00",
        "retrieval_method": "web_fetch",
        "supporting_excerpt": "The runtime executes a JavaScript workflow.",
        "content_hash": "sha256:" + ("a" * 64),
    }


def _claim(
    claim_id: str,
    classification: str,
    scope: str,
    *,
    source_ids: list[str] | None = None,
    premise_claim_ids: list[str] | None = None,
    confidence: str = "",
    falsifier: str = "",
    search_scope: str = "",
) -> dict:
    return {
        "claim_id": claim_id,
        "text": f"claim {claim_id}",
        "classification": classification,
        "scope": scope,
        "source_ids": list(source_ids or []),
        "premise_claim_ids": list(premise_claim_ids or []),
        "confidence": confidence,
        "falsifier": falsifier,
        "search_scope": search_scope,
    }


def _payload(*, sources: list[dict], claims: list[dict]) -> dict:
    return {"version": 1, "sources": sources, "claims": claims}


def _codes(payload: dict) -> set[str]:
    return {issue.code for issue in _module().validate_source_evidence(payload)}


def test_valid_fact_inference_unknown_and_vendor_claim() -> None:
    payload = _payload(
        sources=[
            _source("docs", "official_technical_docs"),
            _source(
                "launch",
                "vendor_marketing",
                url="https://claude.com/blog/introducing-dynamic-workflows-in-claude-code",
            ),
        ],
        claims=[
            _claim(
                "documented-runtime",
                "public_fact",
                "documented_behavior",
                source_ids=["docs"],
            ),
            _claim(
                "vendor-case-study",
                "public_fact",
                "vendor_claim",
                source_ids=["launch"],
            ),
            _claim(
                "sandbox-inference",
                "reasonable_inference",
                "documented_behavior",
                premise_claim_ids=["documented-runtime"],
                confidence="medium",
                falsifier="Official source code shows unrestricted Node execution.",
            ),
            _claim(
                "sandbox-technology",
                "unknown_closed_source",
                "unknown",
                search_scope=(
                    "Official docs, changelog, repository tree, and public talks "
                    "searched through 2026-07-14."
                ),
            ),
        ],
    )

    assert _module().validate_source_evidence(payload) == []


def test_vendor_marketing_cannot_support_public_implementation() -> None:
    payload = _payload(
        sources=[_source("launch", "vendor_marketing")],
        claims=[
            _claim(
                "internal-runtime",
                "public_fact",
                "public_implementation",
                source_ids=["launch"],
            )
        ],
    )

    assert "implementation_evidence_missing" in _codes(payload)


def test_public_source_code_can_support_public_implementation() -> None:
    payload = _payload(
        sources=[
            _source(
                "source",
                "public_source_code",
                url="https://github.com/example/project/blob/abc/runtime.py",
            )
        ],
        claims=[
            _claim(
                "runtime-code",
                "public_fact",
                "public_implementation",
                source_ids=["source"],
            )
        ],
    )

    assert _module().validate_source_evidence(payload) == []


def test_reasonable_inference_requires_premise_confidence_and_falsifier() -> None:
    payload = _payload(
        sources=[],
        claims=[
            _claim(
                "unsupported-inference",
                "reasonable_inference",
                "documented_behavior",
            )
        ],
    )

    assert {
        "inference_premise_missing",
        "inference_confidence_missing",
        "inference_falsifier_missing",
    } <= _codes(payload)


def test_unknown_closed_source_requires_search_scope() -> None:
    payload = _payload(
        sources=[],
        claims=[
            _claim(
                "unknown-runtime",
                "unknown_closed_source",
                "unknown",
            )
        ],
    )

    assert "unknown_search_scope_missing" in _codes(payload)


def test_access_date_and_references_are_mechanically_validated() -> None:
    source = _source("docs", "official_technical_docs")
    source["accessed_at"] = "2026-07-14"
    payload = _payload(
        sources=[source],
        claims=[
            _claim(
                "bad-links",
                "reasonable_inference",
                "documented_behavior",
                source_ids=["missing-source"],
                premise_claim_ids=["missing-claim"],
                confidence="high",
                falsifier="A public implementation contradicts it.",
            )
        ],
    )

    assert {
        "accessed_at_invalid",
        "source_reference_missing",
        "premise_reference_missing",
    } <= _codes(payload)


def test_content_hash_must_be_one_sha256_digest() -> None:
    source = _source("docs", "official_technical_docs")
    source["content_hash"] = "sha256:abc+sha256:def"

    assert "content_hash_invalid" in _codes(
        _payload(sources=[source], claims=[])
    )


def test_duplicate_source_and_claim_ids_are_rejected() -> None:
    payload = _payload(
        sources=[
            _source("same", "official_technical_docs"),
            _source("same", "official_changelog"),
        ],
        claims=[
            _claim(
                "same-claim",
                "public_fact",
                "documented_behavior",
                source_ids=["same"],
            ),
            _claim(
                "same-claim",
                "public_fact",
                "documented_behavior",
                source_ids=["same"],
            ),
        ],
    )

    assert {"source_id_duplicate", "claim_id_duplicate"} <= _codes(payload)


def test_file_validator_reports_missing_and_invalid_json(tmp_path) -> None:
    missing = _module().validate_source_evidence_file(tmp_path / "missing.json")
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text("{", encoding="utf-8")
    invalid = _module().validate_source_evidence_file(invalid_path)

    assert [issue.code for issue in missing] == ["file_missing"]
    assert [issue.code for issue in invalid] == ["file_invalid"]


def test_cli_validates_project_source_evidence(tmp_path, capsys) -> None:
    path = tmp_path / "research" / "SOURCE_EVIDENCE.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            _payload(
                sources=[_source("docs", "official_technical_docs")],
                claims=[
                    _claim(
                        "documented-runtime",
                        "public_fact",
                        "documented_behavior",
                        source_ids=["docs"],
                    )
                ],
            )
        ),
        encoding="utf-8",
    )

    exit_code = _module().main(["--project-root", str(tmp_path)])

    assert exit_code == 0
    assert f"PASS {path}" in capsys.readouterr().out
