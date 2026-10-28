from __future__ import annotations

import json
from pathlib import Path

from argus.manager.reviewed_facts import (
    append_judged_fact,
    reviewed_fact_candidate,
    write_full_record,
)


def test_judged_facts_append_in_order_citing_only_allowed_refs(tmp_path: Path) -> None:
    path = tmp_path / "reviewed-facts.md"
    for fact, ref in (
        ("The positive effect persists on the held-out slice.", "results/heldout.json"),
        ("The comparator reverses the effect under matched compute.", "results/matched.json"),
    ):
        candidate = reviewed_fact_candidate(
            source_campaign="campaign-paper-03",
            reviewer_reason="Reviewer confirmed the reported experimental pattern.",
            research_result={"result_class": "verified_new_result", "evidence": [ref]},
            evidence_refs=[ref],
        )
        assert append_judged_fact(
            candidate, digest_path=path, fact=fact, evidence_refs=[ref, "made-up.json"],
        )

    assert path.read_text(encoding="utf-8") == (
        "# Cross-campaign reviewed facts\n\n"
        "Facts, not instructions. Entries appear in review order.\n"
        "\n## Source campaign: campaign-paper-03\n\n"
        "Evidence refs:\n"
        "- `results/heldout.json`\n\n"
        "Fact: The positive effect persists on the held-out slice.\n"
        "\n## Source campaign: campaign-paper-03\n\n"
        "Evidence refs:\n"
        "- `results/matched.json`\n\n"
        "Fact: The comparator reverses the effect under matched compute.\n"
    )


def test_a_fact_with_no_allowed_ref_is_not_appended(tmp_path: Path) -> None:
    candidate = reviewed_fact_candidate(
        source_campaign="c", reviewer_reason="r",
        research_result={"result_class": "verified_new_result"}, evidence_refs=["result.json"],
    )
    path = tmp_path / "reviewed-facts.md"

    assert not append_judged_fact(candidate, digest_path=path, fact="A claim.", evidence_refs=["made-up.json"])
    assert not path.exists()


def test_the_summary_carries_key_fields_not_the_full_result_json() -> None:
    long_item = "measurement-row-" + "z" * 5000
    evidence = [f"results/point-{index}.json" for index in range(6)] + [long_item]

    summary = reviewed_fact_candidate(
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
    )["summary"]

    for line in (
        "result_class: verified_new_result",
        "correctness_status: verified",
        "novelty_status: novel",
        "significance_status: publishable",
        "statement_fidelity_status: not_applicable",
        "evidence (7 items, first 5 shown):",
        "- results/point-0.json",
        "- results/point-4.json",
        "limitations (1 items):",
        "Single seed only.",
    ):
        assert line in summary
    # The full result JSON stays out of the summary.
    assert long_item not in summary
    assert json.dumps(evidence[0]) not in summary


def test_the_full_record_sits_beside_the_digest(tmp_path: Path) -> None:
    research_result = {"result_class": "verified_new_result", "evidence": ["detail-" + "q" * 3000]}

    path = write_full_record(tmp_path / "reviewed-facts.md", research_result)

    assert path is not None and path.parent == tmp_path
    assert json.loads(path.read_text(encoding="utf-8")) == research_result
