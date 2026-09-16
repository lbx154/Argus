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


def test_raising_provider_is_skipped_and_the_rest_still_run(
    tmp_path: Path, isolated_registry: list, caplog: pytest.LogCaptureFixture,
) -> None:
    def boom(request: RoundEvidenceRequest) -> RoundEvidence:
        raise RuntimeError("provider exploded")

    def quiet(request: RoundEvidenceRequest) -> None:
        return None

    def fine(request: RoundEvidenceRequest) -> RoundEvidence:
        return RoundEvidence(reviewer_text="fine")

    for provider in (boom, quiet, fine):
        register_round_evidence_provider(provider)

    with caplog.at_level("ERROR"):
        items = collect_round_evidence(_request(tmp_path))

    assert [item.reviewer_text for item in items] == ["fine"]
    assert any("provider exploded" in record.exc_text or "" for record in caplog.records if record.exc_text)


def test_each_provider_gets_only_its_own_previous_state(tmp_path: Path, isolated_registry: list) -> None:
    seen: dict[str, dict] = {}

    def counter(request: RoundEvidenceRequest) -> RoundEvidence:
        seen["counter"] = dict(request.previous_state)
        return RoundEvidence(state={"count": request.previous_state.get("count", 0) + 1})

    def other(request: RoundEvidenceRequest) -> RoundEvidence:
        seen["other"] = dict(request.previous_state)
        return RoundEvidence(state={"tag": "x"})

    register_round_evidence_provider(counter)
    register_round_evidence_provider(other)

    stored: dict[str, dict] = {}
    for round_index in (1, 2, 3):
        for item in collect_round_evidence(_request(tmp_path, stored, round_index)):
            stored[item.provider] = item.state

    assert stored[provider_key(counter)] == {"count": 3}
    assert stored[provider_key(other)] == {"tag": "x"}
    assert seen["counter"] == {"count": 2}
    assert seen["other"] == {"tag": "x"}


def test_provider_returning_none_keeps_its_previous_state(tmp_path: Path, isolated_registry: list) -> None:
    def silent(request: RoundEvidenceRequest) -> None:
        return None

    register_round_evidence_provider(silent)
    stored = {provider_key(silent): {"kept": True}}
    items = collect_round_evidence(_request(tmp_path, stored, 2))
    assert items == []
    for item in items:
        stored[item.provider] = item.state
    assert stored == {provider_key(silent): {"kept": True}}


def test_registry_knows_no_test_runner() -> None:
    source = Path(registry.__file__).read_text(encoding="utf-8")
    assert "pytest" not in source
    assert "subprocess" not in source
