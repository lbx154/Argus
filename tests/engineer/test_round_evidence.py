"""The round-evidence registry: providers run in order, a raising provider is
skipped, and each provider's opaque state round-trips under its own key."""
from __future__ import annotations

from pathlib import Path

import pytest

from argus.engineer import round_evidence as registry
from argus.engineer.round_evidence import (
    RoundEvidence,
    RoundEvidenceRequest,
    collect_round_evidence,
    provider_key,
    register_round_evidence_provider,
    registered_round_evidence_providers,
)


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> list:
    providers: list = []
    monkeypatch.setattr(registry, "_PROVIDERS", providers)
    return providers


def _request(tmp_path: Path, previous_state: dict | None = None, round_index: int = 1) -> RoundEvidenceRequest:
    return RoundEvidenceRequest(
        workdir=tmp_path, life_dir=tmp_path / "life", round_index=round_index,
        previous_state=previous_state or {},
    )


def test_providers_run_in_registration_order_and_are_tagged(tmp_path: Path, isolated_registry: list) -> None:
    calls: list[str] = []

    @register_round_evidence_provider
    def first(request: RoundEvidenceRequest) -> RoundEvidence:
        calls.append("first")
        return RoundEvidence(reviewer_text="A", engineer_note="a", state={"n": 1})

    def second(request: RoundEvidenceRequest) -> RoundEvidence:
        calls.append("second")
        return RoundEvidence(reviewer_text="B", engineer_note="b")

    register_round_evidence_provider(second)
    register_round_evidence_provider(second)  # idempotent
    assert registered_round_evidence_providers() == (first, second)

    items = collect_round_evidence(_request(tmp_path))

    assert calls == ["first", "second"]
    assert [item.reviewer_text for item in items] == ["A", "B"]
    assert [item.provider for item in items] == [provider_key(first), provider_key(second)]
    assert items[0].state == {"n": 1} and items[1].state == {}


def test_registry_knows_no_test_runner() -> None:
    source = Path(registry.__file__).read_text(encoding="utf-8")
    assert "pytest" not in source
    assert "subprocess" not in source
