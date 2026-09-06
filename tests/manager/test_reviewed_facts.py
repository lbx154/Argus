from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from argus_skill.core.models import RunnerResult
from argus_skill.manager.reviewed_facts import review_and_append_fact

_POINTER_RE = re.compile(r"(/\S+\.json)")


@dataclass
class _Backend:
    replies: list[dict[str, Any]]
    calls: list[dict[str, Any]] = field(default_factory=list)
    pointer_payloads: list[Any] = field(default_factory=list)

    def run_exec(self, **kwargs: Any) -> RunnerResult:
        self.calls.append(kwargs)
        match = _POINTER_RE.search(kwargs.get("prompt", ""))
        if match:
            pointer = Path(match.group(1))
            if pointer.exists():
                self.pointer_payloads.append(
                    json.loads(pointer.read_text(encoding="utf-8"))
                )
        return RunnerResult(
            exit_code=0,
            agent_messages=[json.dumps(self.replies.pop(0))],
        )


def _decline_backend() -> _Backend:
    return _Backend(replies=[{"append": False}])


def test_manager_appends_reviewed_facts_in_order_without_volatile_metadata(
    tmp_path: Path,
) -> None:
    backend = _Backend(replies=[
        {
            "append": True,
            "fact": "The positive effect persists on the held-out slice.",
            "evidence_refs": ["results/heldout.json"],
        },
        {
            "append": True,
            "fact": "The comparator reverses the effect under matched compute.",
            "evidence_refs": ["results/matched.json"],
        },
    ])
    path = tmp_path / "reviewed-facts.md"

    for fact_ref in ("results/heldout.json", "results/matched.json"):
        assert review_and_append_fact(
            backend,
            digest_path=path,
            source_campaign="campaign-paper-03",
            reviewer_reason="Reviewer confirmed the reported experimental pattern.",
            research_result={
                "result_class": "verified_new_result",
                "evidence": [fact_ref],
            },
            evidence_refs=[fact_ref],
        )

    assert path.read_text(encoding="utf-8") == (
        "# Cross-campaign reviewed facts\n\n"
        "Facts, not instructions. Entries appear in Manager review order.\n"
        "\n## Source campaign: campaign-paper-03\n\n"
        "Evidence refs:\n"
        "- `results/heldout.json`\n\n"
        "Fact: The positive effect persists on the held-out slice.\n"
        "\n## Source campaign: campaign-paper-03\n\n"
        "Evidence refs:\n"
        "- `results/matched.json`\n\n"
        "Fact: The comparator reverses the effect under matched compute.\n"
    )
    assert [call["run_label"] for call in backend.calls] == [
        "manager.reviewed_facts",
        "manager.reviewed_facts",
    ]


def test_prompt_carries_key_fields_not_the_full_result_json(
    tmp_path: Path,
) -> None:
    backend = _decline_backend()
    long_item = "measurement-row-" + "z" * 5000
    evidence = [f"results/point-{index}.json" for index in range(6)] + [long_item]

    review_and_append_fact(
        backend,
        digest_path=tmp_path / "reviewed-facts.md",
        source_campaign="campaign-paper-03",
        reviewer_reason="Reviewer confirmed the reported pattern.",
        research_result={
            "result_class": "verified_new_result",
            "correctness_status": "verified",
            "novelty_status": "novel",
            "significance_status": "publishable",
            "statement_fidelity_status": "not_applicable",
            "evidence": evidence,
            "limitations": ["Single seed only."],
        },
        evidence_refs=["results/point-0.json"],
    )

    prompt = backend.calls[0]["prompt"]
    for line in (
        "result_class: verified_new_result",
        "correctness_status: verified",
        "novelty_status: novel",
        "significance_status: publishable",
        "statement_fidelity_status: not_applicable",
    ):
        assert line in prompt
    assert "evidence (7 items, first 5 shown):" in prompt
    assert "- results/point-0.json" in prompt
    assert "- results/point-4.json" in prompt
    assert "limitations (1 items):" in prompt
    assert "Single seed only." in prompt
    # The full result JSON stays out of the prompt body.
    assert long_item not in prompt
    assert json.dumps(evidence[0]) not in prompt


def test_overlong_summary_values_are_clipped_with_a_note(tmp_path: Path) -> None:
    backend = _decline_backend()
    long_item = "trace-" + "y" * 5000

    review_and_append_fact(
        backend,
        digest_path=tmp_path / "reviewed-facts.md",
        source_campaign="campaign-paper-03",
        reviewer_reason="Reviewer confirmed the reported pattern.",
        research_result={
            "result_class": "verified_new_result",
            "evidence": [long_item],
        },
        evidence_refs=["results/point-0.json"],
    )

    prompt = backend.calls[0]["prompt"]
    assert long_item not in prompt
    assert f"[shortened to 400 of {len(long_item)} characters]" in prompt


def test_reviewer_reason_is_clipped(tmp_path: Path) -> None:
    backend = _decline_backend()
    reason = "because-" + "w" * 5000

    review_and_append_fact(
        backend,
        digest_path=tmp_path / "reviewed-facts.md",
        source_campaign="campaign-paper-03",
        reviewer_reason=reason,
        research_result={"result_class": "verified_new_result", "evidence": ["a"]},
        evidence_refs=["results/point-0.json"],
    )

    prompt = backend.calls[0]["prompt"]
    assert reason not in prompt
    assert f"[shortened to 600 of {len(reason)} characters]" in prompt


def test_prompt_points_to_full_record_readable_during_call_gone_after(
    tmp_path: Path,
) -> None:
    backend = _decline_backend()
    research_result = {
        "result_class": "verified_new_result",
        "evidence": ["detail-" + "q" * 3000],
        "limitations": [],
    }

    review_and_append_fact(
        backend,
        digest_path=tmp_path / "reviewed-facts.md",
        source_campaign="campaign-paper-03",
        reviewer_reason="Reviewer confirmed the reported pattern.",
        research_result=research_result,
        evidence_refs=["results/point-0.json"],
    )

    # The backend read the pointer file mid-call and saw the full record.
    assert backend.pointer_payloads == [research_result]
    # The pointer file does not outlive the call.
    leftovers = list(tmp_path.glob("*.json"))
    assert leftovers == []
