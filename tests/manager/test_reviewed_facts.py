from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from argus_skill.core.models import RunnerResult
from argus_skill.manager.reviewed_facts import review_and_append_fact


@dataclass
class _Backend:
    replies: list[dict[str, Any]]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def run_exec(self, **kwargs: Any) -> RunnerResult:
        self.calls.append(kwargs)
        return RunnerResult(
            exit_code=0,
            agent_messages=[json.dumps(self.replies.pop(0))],
        )


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
