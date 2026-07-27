from __future__ import annotations

import os
from pathlib import Path

import pytest

from argus_skill.domains.chemistry.playground import (
    candidate_path,
    initialize_candidate,
    main,
    validate_candidate,
    validate_idea_id,
)


def _replace_metadata(path: Path, **changes: str) -> None:
    text = path.read_text(encoding="utf-8")
    for key, value in changes.items():
        text = text.replace(f"{key}: {text.split(f'{key}: ', 1)[1].splitlines()[0]}", f"{key}: {value}")
    path.write_text(text, encoding="utf-8")


def _replace_section(path: Path, heading: str, content: str) -> None:
    text = path.read_text(encoding="utf-8")
    marker = f"## {heading}\n\n"
    before, separator, tail = text.partition(marker)
    assert separator
    current, next_separator, after = tail.partition("\n\n## ")
    replacement = f"{before}{marker}{content}"
    if next_separator:
        replacement += f"\n\n## {after}"
    path.write_text(replacement, encoding="utf-8")


def test_init_creates_single_project_local_protocol_and_does_not_touch_pipeline(
    tmp_path: Path,
) -> None:
    pipeline = tmp_path / "research" / "PIPELINE_STATE.json"
    pipeline.parent.mkdir()
    pipeline.write_text('{"current_stage":"research"}', encoding="utf-8")

    candidate = initialize_candidate(
        tmp_path,
        "radical-electrolyte-idea",
        question="Can a radical shuttle suppress one decomposition pathway?",
        hypothesis="A bounded redox window changes the dominant pathway.",
    )

    assert candidate == candidate_path(tmp_path, "radical-electrolyte-idea")
    assert {
        "QUESTION.md",
        "RESULT.md",
        "work/scripts",
        "work/notebooks",
        "evidence/inputs",
        "evidence/outputs",
    } <= {
        str(path.relative_to(candidate)).replace("\\", "/")
        for path in candidate.rglob("*")
    }
    assert validate_candidate(tmp_path, "radical-electrolyte-idea").valid
    assert pipeline.read_text(encoding="utf-8") == '{"current_stage":"research"}'


def test_init_rejects_unsafe_ids_and_never_overwrites(tmp_path: Path) -> None:
    for idea_id in ("../escape", "UpperCase", "two--hyphens", "x" * 65):
        with pytest.raises(ValueError):
            validate_idea_id(idea_id)

    initialize_candidate(
        tmp_path,
        "safe-id",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    question = candidate_path(tmp_path, "safe-id") / "QUESTION.md"
    original = question.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        initialize_candidate(
            tmp_path,
            "safe-id",
            question="Replacement?",
            hypothesis="Replacement.",
        )
    assert question.read_text(encoding="utf-8") == original


def test_validator_rejects_missing_sections_and_escaping_references(tmp_path: Path) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "unsafe-reference",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    result = candidate / "RESULT.md"
    text = result.read_text(encoding="utf-8")
    text = text.replace("## Summary\n\n- No probe has been completed.\n\n", "")
    text = text.replace(
        "Add entries as `- [evidence_class] relative/path-or-URL - claim supported`.",
        "- [retrieved] ../../outside.txt - unsafe path",
    )
    result.write_text(text, encoding="utf-8")

    report = validate_candidate(tmp_path, "unsafe-reference")

    assert not report.valid
    assert {issue.code for issue in report.issues} >= {
        "missing_section",
        "unsafe_reference",
    }


def test_malformed_evidence_ledger_entry_is_rejected(tmp_path: Path) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "malformed-evidence-entry",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    result = candidate / "RESULT.md"
    text = result.read_text(encoding="utf-8").replace(
        "Add entries as `- [evidence_class] relative/path-or-URL - claim supported`.",
        "- [measured] ../../outside.csv supports claim",
    )
    result.write_text(text, encoding="utf-8")

    report = validate_candidate(tmp_path, "malformed-evidence-entry")

    assert "invalid_evidence_entry" in {issue.code for issue in report.issues}


def test_evidence_ledger_requires_a_claim_suffix(tmp_path: Path) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "missing-evidence-claim",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    result = candidate / "RESULT.md"
    text = result.read_text(encoding="utf-8").replace(
        "Add entries as `- [evidence_class] relative/path-or-URL - claim supported`.",
        "- [retrieved] https://pubchem.ncbi.nlm.nih.gov",
    )
    result.write_text(text, encoding="utf-8")

    report = validate_candidate(tmp_path, "missing-evidence-claim")

    assert "invalid_evidence_entry" in {issue.code for issue in report.issues}


def test_fenced_evidence_entry_cannot_satisfy_grounding(tmp_path: Path) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "fenced-evidence",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    output = candidate / "evidence" / "outputs" / "fake.txt"
    output.write_text("hidden result", encoding="utf-8")
    result = candidate / "RESULT.md"
    _replace_metadata(
        result,
        status="computationally_probed",
        status_history="speculative -> computationally_probed",
    )
    text = result.read_text(encoding="utf-8").replace(
        "Add entries as `- [evidence_class] relative/path-or-URL - claim supported`.",
        "```\n"
        "- [computed] evidence/outputs/fake.txt - hidden code example\n"
        "```",
    )
    result.write_text(text, encoding="utf-8")

    report = validate_candidate(tmp_path, "fenced-evidence")

    assert "missing_computational_evidence" in {
        issue.code for issue in report.issues
    }


@pytest.mark.parametrize(
    "claim",
    [
        "placeholder",
        "claim supported",
        "`claim supported`",
        "**claim supported**",
        "[claim supported](#claim)",
    ],
)
def test_evidence_ledger_rejects_placeholder_claim(
    tmp_path: Path,
    claim: str,
) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "placeholder-evidence-claim",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    source = candidate / "evidence" / "inputs" / "source.txt"
    source.write_text("real source", encoding="utf-8")
    result = candidate / "RESULT.md"
    text = result.read_text(encoding="utf-8").replace(
        "Add entries as `- [evidence_class] relative/path-or-URL - claim supported`.",
        f"- [retrieved] evidence/inputs/source.txt - {claim}",
    )
    result.write_text(text, encoding="utf-8")

    report = validate_candidate(tmp_path, "placeholder-evidence-claim")

    assert "invalid_evidence_claim" in {issue.code for issue in report.issues}


def test_candidate_root_link_or_junction_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "research" / "chem_playground"
    root.mkdir(parents=True)
    outside = tmp_path / "outside-candidate"
    outside.mkdir()
    linked = root / "linked-candidate"
    try:
        os.symlink(outside, linked, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory links are unavailable: {exc}")

    report = validate_candidate(tmp_path, "linked-candidate")

    assert "symlink_not_allowed" in {issue.code for issue in report.issues}


def test_candidate_hardlink_is_rejected(tmp_path: Path) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "hardlinked-evidence",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    outside = tmp_path / "outside-evidence.txt"
    outside.write_text("shared evidence", encoding="utf-8")
    linked = candidate / "evidence" / "outputs" / "shared.txt"
    try:
        os.link(outside, linked)
    except OSError as exc:
        pytest.skip(f"hard links are unavailable: {exc}")

    report = validate_candidate(tmp_path, "hardlinked-evidence")

    assert "hardlink_not_allowed" in {issue.code for issue in report.issues}


def test_empty_file_cannot_satisfy_computational_evidence(tmp_path: Path) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "empty-computational-evidence",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    output = candidate / "evidence" / "outputs" / "probe.json"
    output.write_bytes(b"")
    result = candidate / "RESULT.md"
    _replace_metadata(
        result,
        status="computationally_probed",
        status_history="speculative -> computationally_probed",
    )
    text = result.read_text(encoding="utf-8").replace(
        "Add entries as `- [evidence_class] relative/path-or-URL - claim supported`.",
        "- [computed] evidence/outputs/probe.json - primary bounded probe result",
    )
    result.write_text(text, encoding="utf-8")

    report = validate_candidate(tmp_path, "empty-computational-evidence")

    assert {issue.code for issue in report.issues} >= {
        "invalid_evidence_file",
        "missing_computational_evidence",
    }


def test_reviewer_gated_promoted_status_requires_grounding_and_probe_evidence(
    tmp_path: Path,
) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "promotable-idea",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    source = candidate / "evidence" / "inputs" / "source.txt"
    output = candidate / "evidence" / "outputs" / "probe.json"
    source.write_text("source notes", encoding="utf-8")
    output.write_text('{"supported": true}', encoding="utf-8")
    question = candidate / "QUESTION.md"
    result = candidate / "RESULT.md"
    for section, content in {
        "Explicit assumptions": (
            "- The bounded probe assumes the retrieved source describes the same "
            "chemical regime as the candidate."
        ),
        "Competing explanations": (
            "- The observed signal may arise from a correlated structural factor "
            "rather than the proposed mechanism."
        ),
        "Falsifiable predictions": (
            "- The hypothesis weakens if the computed response changes sign under "
            "the documented perturbation."
        ),
        "Known evidence": (
            "- The retained source reports a response in the candidate chemical regime."
        ),
        "Missing evidence": (
            "- Independent experimental replication and broader sensitivity data "
            "remain unavailable."
        ),
        "Allowed probe budget": (
            "- One deterministic calculation, one sensitivity perturbation, and no "
            "physical execution are authorized."
        ),
    }.items():
        _replace_section(question, section, content)
    _replace_metadata(
        result,
        status="promoted",
        status_history=(
            "speculative -> literature_grounded -> computationally_probed -> "
            "reviewer_candidate -> promoted"
        ),
        reviewer="独立审查员-第一轮",
        reviewer_recommendation="promoted",
    )
    text = result.read_text(encoding="utf-8").replace(
        "Add entries as `- [evidence_class] relative/path-or-URL - claim supported`.",
        "- [retrieved] evidence/inputs/source.txt - 该来源支持有边界的化学机理前提\n"
        "- [computed] evidence/outputs/probe.json - 该输出支持预先声明的计算预测",
    )
    result.write_text(text, encoding="utf-8")
    _replace_section(
        result,
        "Summary",
        "- The bounded literature and computational probe support retaining the "
        "hypothesis for formal Research consideration.",
    )
    _replace_section(
        result,
        "Work performed",
        "- Reviewed the retained source, executed the documented deterministic "
        "probe, and compared its output with the falsifiable prediction.",
    )
    _replace_section(
        result,
        "Computational probes",
        "- The retained probe output supports the predicted response within the "
        "declared parameter domain.",
    )
    _replace_section(
        result,
        "Uncertainty and applicability",
        "- The conclusion is limited to the bounded model assumptions and lacks "
        "independent experimental replication.",
    )
    _replace_section(
        result,
        "Competing explanations revisited",
        "- A correlated structural factor remains plausible but was less consistent "
        "with the retained perturbation result.",
    )
    _replace_section(
        result,
        "Next discriminating test",
        "- Repeat the perturbation with an orthogonal model and predeclared sign "
        "criterion before formal use.",
    )
    _replace_section(
        result,
        "Reviewer decision basis",
        "- 独立审查确认该有边界计算可以复现，并且与所引用来源支持的化学前提一致。",
    )

    assert validate_candidate(tmp_path, "promotable-idea").valid


def test_terminal_status_without_reviewer_gate_is_invalid(tmp_path: Path) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "ungated-idea",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    result = candidate / "RESULT.md"
    _replace_metadata(
        result,
        status="retained",
        status_history="speculative -> reviewer_candidate -> retained",
        reviewer_recommendation="retained",
    )

    report = validate_candidate(tmp_path, "ungated-idea")

    assert not report.valid
    assert "reviewer_gate_missing" in {issue.code for issue in report.issues}


def test_reviewer_placeholder_is_case_insensitively_rejected(tmp_path: Path) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "placeholder-reviewer",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    result = candidate / "RESULT.md"
    _replace_metadata(
        result,
        status="retained",
        status_history="speculative -> reviewer_candidate -> retained",
        reviewer="PENDING",
        reviewer_recommendation="retained",
    )

    report = validate_candidate(tmp_path, "placeholder-reviewer")

    assert not report.valid
    assert "reviewer_gate_missing" in {issue.code for issue in report.issues}


def test_blocked_terminal_status_does_not_require_unavailable_grounding(
    tmp_path: Path,
) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "blocked-idea",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    question = candidate / "QUESTION.md"
    result = candidate / "RESULT.md"
    for section, content in {
        "Explicit assumptions": (
            "- The requested licensed dataset is assumed to be necessary for the "
            "claim-critical comparison."
        ),
        "Competing explanations": (
            "- A public proxy dataset might be informative but cannot establish "
            "equivalence to the licensed input."
        ),
        "Falsifiable predictions": (
            "- The hypothesis would weaken if an authorized equivalent dataset "
            "produced the opposite bounded response."
        ),
        "Known evidence": (
            "- Only metadata describing the unavailable licensed source is currently known."
        ),
        "Missing evidence": (
            "- The claim-critical licensed records and an authorized equivalent are absent."
        ),
        "Allowed probe budget": (
            "- Metadata inspection is permitted; no substitute data fabrication or "
            "physical execution is authorized."
        ),
    }.items():
        _replace_section(question, section, content)
    _replace_metadata(
        result,
        status="blocked",
        status_history="speculative -> reviewer_candidate -> blocked",
        reviewer="independent-review-round-1",
        reviewer_recommendation="blocked",
    )
    _replace_section(
        result,
        "Summary",
        "- The candidate is blocked because the claim-critical licensed input is "
        "unavailable and no authorized equivalent was identified.",
    )
    _replace_section(
        result,
        "Work performed",
        "- Confirmed the required input boundary and evaluated whether an authorized "
        "public proxy could answer the same question.",
    )
    _replace_section(
        result,
        "Uncertainty and applicability",
        "- No scientific conclusion is supported while the required input remains "
        "unavailable.",
    )
    _replace_section(
        result,
        "Competing explanations revisited",
        "- A public proxy remains possible, but its equivalence has not been established.",
    )
    _replace_section(
        result,
        "Next discriminating test",
        "- Obtain authorized access or document an independently reviewed equivalence "
        "argument for a public substitute.",
    )
    _replace_section(
        result,
        "Reviewer decision basis",
        "- A claim-critical licensed input is unavailable, so no discriminating "
        "bounded computation can continue.",
    )

    assert validate_candidate(tmp_path, "blocked-idea").valid


def test_claimed_computational_history_keeps_primary_evidence_requirement(
    tmp_path: Path,
) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "missing-probe-output",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    source = candidate / "evidence" / "inputs" / "source.txt"
    source.write_text("source notes", encoding="utf-8")
    result = candidate / "RESULT.md"
    _replace_metadata(
        result,
        status="retained",
        status_history=(
            "speculative -> literature_grounded -> computationally_probed -> "
            "reviewer_candidate -> retained"
        ),
        reviewer="independent-review-round-1",
        reviewer_recommendation="retained",
    )
    text = result.read_text(encoding="utf-8").replace(
        "Add entries as `- [evidence_class] relative/path-or-URL - claim supported`.",
        "- [retrieved] evidence/inputs/source.txt - grounds the question",
    )
    result.write_text(text, encoding="utf-8")

    report = validate_candidate(tmp_path, "missing-probe-output")

    assert not report.valid
    assert "missing_computational_evidence" in {issue.code for issue in report.issues}


def test_directory_cannot_masquerade_as_computational_primary_evidence(
    tmp_path: Path,
) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "directory-evidence",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    result = candidate / "RESULT.md"
    _replace_metadata(
        result,
        status="computationally_probed",
        status_history="speculative -> computationally_probed",
    )
    text = result.read_text(encoding="utf-8").replace(
        "Add entries as `- [evidence_class] relative/path-or-URL - claim supported`.",
        "- [computed] evidence/outputs - directory is not primary output",
    )
    result.write_text(text, encoding="utf-8")

    report = validate_candidate(tmp_path, "directory-evidence")

    assert not report.valid
    assert {issue.code for issue in report.issues} >= {
        "invalid_evidence_file",
        "missing_computational_evidence",
    }


def test_placeholder_anchor_cannot_satisfy_literature_grounding(
    tmp_path: Path,
) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "placeholder-grounding",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    result = candidate / "RESULT.md"
    _replace_metadata(
        result,
        status="literature_grounded",
        status_history="speculative -> literature_grounded",
    )
    text = result.read_text(encoding="utf-8").replace(
        "Add entries as `- [evidence_class] relative/path-or-URL - claim supported`.",
        "- [retrieved] # - placeholder is not a source",
    )
    result.write_text(text, encoding="utf-8")

    report = validate_candidate(tmp_path, "placeholder-grounding")

    assert not report.valid
    assert {issue.code for issue in report.issues} >= {
        "invalid_evidence_claim",
        "missing_grounding_evidence",
    }


def test_malformed_or_placeholder_urls_cannot_satisfy_grounding(
    tmp_path: Path,
) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "malformed-grounding-url",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    result = candidate / "RESULT.md"
    _replace_metadata(
        result,
        status="literature_grounded",
        status_history="speculative -> literature_grounded",
    )
    text = result.read_text(encoding="utf-8").replace(
        "Add entries as `- [evidence_class] relative/path-or-URL - claim supported`.",
        "- [retrieved] https://example.com|../../outside.txt - fake source",
    )
    result.write_text(text, encoding="utf-8")

    report = validate_candidate(tmp_path, "malformed-grounding-url")

    assert {issue.code for issue in report.issues} >= {
        "invalid_external_evidence",
        "missing_grounding_evidence",
    }


@pytest.mark.parametrize(
    "target",
    [
        "https://127.0.0.1/source",
        "https://169.254.169.254/source",
        "https://224.0.0.1/source",
        "https://[ff0e::1]/source",
        "https://[::ffff:224.0.0.1]/source",
        "https://999.999.999.999/source",
        "https://intranet/source",
        "https://source.example.com/paper",
        "https://source.local/paper",
        "https://example。com/paper",
        "https://source。local/paper",
        "https://１２７。０。０。１/source",
        "https://" + ".".join(["a" * 60] * 5) + "/paper",
    ],
)
def test_nonpublic_or_malformed_hosts_cannot_satisfy_grounding(
    tmp_path: Path,
    target: str,
) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "nonpublic-grounding",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    result = candidate / "RESULT.md"
    _replace_metadata(
        result,
        status="literature_grounded",
        status_history="speculative -> literature_grounded",
    )
    text = result.read_text(encoding="utf-8").replace(
        "Add entries as `- [evidence_class] relative/path-or-URL - claim supported`.",
        f"- [retrieved] {target} - invalid external grounding",
    )
    result.write_text(text, encoding="utf-8")

    report = validate_candidate(tmp_path, "nonpublic-grounding")

    assert {issue.code for issue in report.issues} >= {
        "invalid_external_evidence",
        "missing_grounding_evidence",
    }


def test_evidence_class_must_match_input_or_output_location(tmp_path: Path) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "misclassified-evidence",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    misplaced = candidate / "evidence" / "outputs" / "source.txt"
    misplaced.write_text("retrieved source", encoding="utf-8")
    result = candidate / "RESULT.md"
    _replace_metadata(
        result,
        status="literature_grounded",
        status_history="speculative -> literature_grounded",
    )
    text = result.read_text(encoding="utf-8").replace(
        "Add entries as `- [evidence_class] relative/path-or-URL - claim supported`.",
        "- [retrieved] evidence/outputs/source.txt - wrong storage class",
    )
    result.write_text(text, encoding="utf-8")

    report = validate_candidate(tmp_path, "misclassified-evidence")

    assert {issue.code for issue in report.issues} >= {
        "evidence_class_location_mismatch",
        "missing_grounding_evidence",
    }


def test_promoted_status_rejects_unedited_initialization_sections(
    tmp_path: Path,
) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "stale-promoted-idea",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    source = candidate / "evidence" / "inputs" / "source.txt"
    output = candidate / "evidence" / "outputs" / "probe.json"
    source.write_text("source notes", encoding="utf-8")
    output.write_text('{"supported": true}', encoding="utf-8")
    result = candidate / "RESULT.md"
    _replace_metadata(
        result,
        status="promoted",
        status_history=(
            "speculative -> literature_grounded -> computationally_probed -> "
            "reviewer_candidate -> promoted"
        ),
        reviewer="independent-review-round-1",
        reviewer_recommendation="promoted",
    )
    text = result.read_text(encoding="utf-8").replace(
        "Add entries as `- [evidence_class] relative/path-or-URL - claim supported`.",
        "\n".join(
            [
                "- [retrieved] evidence/inputs/source.txt - source supports the "
                "bounded scientific premise",
                "- [computed] evidence/outputs/probe.json - probe supports the "
                "bounded prediction",
            ]
        ),
    )
    result.write_text(text, encoding="utf-8")
    _replace_section(
        result,
        "Reviewer decision basis",
        "- The retained source and probe satisfy the bounded evidence criteria, "
        "but the narrative sections were not completed.",
    )

    report = validate_candidate(tmp_path, "stale-promoted-idea")

    assert "stale_template_section" in {issue.code for issue in report.issues}


def test_terminal_status_rejects_template_decision_and_placeholder_identity(
    tmp_path: Path,
) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "template-review",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    result = candidate / "RESULT.md"
    _replace_metadata(
        result,
        status="retained",
        status_history="speculative -> reviewer_candidate -> retained",
        reviewer="placeholder-reviewer",
        reviewer_recommendation="retained",
    )

    report = validate_candidate(tmp_path, "template-review")
    codes = {issue.code for issue in report.issues}

    assert "reviewer_gate_missing" in codes
    assert "missing_reviewer_decision_basis" in codes


def test_terminal_status_rejects_engineer_self_review_identity(
    tmp_path: Path,
) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "self-reviewed-result",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    result = candidate / "RESULT.md"
    _replace_metadata(
        result,
        status="retained",
        status_history="speculative -> reviewer_candidate -> retained",
        reviewer="engineer-self-review",
        reviewer_recommendation="retained",
    )
    _replace_section(
        result,
        "Reviewer decision basis",
        "- The evidence remains ambiguous within the declared bounded domain and "
        "does not justify promotion.",
    )

    report = validate_candidate(tmp_path, "self-reviewed-result")

    assert "reviewer_gate_missing" in {issue.code for issue in report.issues}


@pytest.mark.parametrize(
    "reviewer",
    [
        "ｅｎｇｉｎｅｅｒ",
        "eng\u200bineer-self\u200b-review",
        "self_review",
        "self review",
        "self&nbsp;review",
        "p-e-n-d-i-n-g",
        "t b d",
        "un-assigned",
        "re view er",
        "---",
    ],
)
def test_terminal_status_rejects_unicode_disguised_self_review_identity(
    tmp_path: Path,
    reviewer: str,
) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "unicode-self-review",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    result = candidate / "RESULT.md"
    _replace_metadata(
        result,
        status="retained",
        status_history="speculative -> reviewer_candidate -> retained",
        reviewer=reviewer,
        reviewer_recommendation="retained",
    )
    _replace_section(
        result,
        "Reviewer decision basis",
        "- The bounded evidence remains insufficient to justify promotion.",
    )

    report = validate_candidate(tmp_path, "unicode-self-review")

    assert "reviewer_gate_missing" in {issue.code for issue in report.issues}


def test_terminal_status_rejects_markdown_formatted_template_decision(
    tmp_path: Path,
) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "formatted-template-review",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    result = candidate / "RESULT.md"
    _replace_metadata(
        result,
        status="retained",
        status_history="speculative -> reviewer_candidate -> retained",
        reviewer="independent-review-round-1",
        reviewer_recommendation="retained",
    )
    _replace_section(
        result,
        "Reviewer decision basis",
        "- **Pending independent Reviewer assessment.**",
    )

    report = validate_candidate(tmp_path, "formatted-template-review")

    assert "missing_reviewer_decision_basis" in {
        issue.code for issue in report.issues
    }


def test_terminal_status_rejects_html_encoded_template_decision(
    tmp_path: Path,
) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "encoded-template-review",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    result = candidate / "RESULT.md"
    _replace_metadata(
        result,
        status="retained",
        status_history="speculative -> reviewer_candidate -> retained",
        reviewer="independent-review-round-1",
        reviewer_recommendation="retained",
    )
    _replace_section(
        result,
        "Reviewer decision basis",
        "- Pending&nbsp;independent Reviewer assessment.",
    )

    report = validate_candidate(tmp_path, "encoded-template-review")

    assert "missing_reviewer_decision_basis" in {
        issue.code for issue in report.issues
    }


def test_terminal_status_rejects_zero_width_template_decision(
    tmp_path: Path,
) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "zero-width-template-review",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    result = candidate / "RESULT.md"
    _replace_metadata(
        result,
        status="retained",
        status_history="speculative -> reviewer_candidate -> retained",
        reviewer="independent-review-round-1",
        reviewer_recommendation="retained",
    )
    _replace_section(
        result,
        "Reviewer decision basis",
        "- P\u200bending independent Reviewer assessment.",
    )

    report = validate_candidate(tmp_path, "zero-width-template-review")

    assert "missing_reviewer_decision_basis" in {
        issue.code for issue in report.issues
    }


@pytest.mark.parametrize(
    "decision",
    [
        "+ Pending independent Reviewer assessment.",
        "－ Pending independent Reviewer assessment.",
        "1. Pending independent Reviewer assessment.",
        "- [ ] Pending independent Reviewer assessment.",
    ],
)
def test_terminal_status_rejects_equivalent_template_bullets(
    tmp_path: Path,
    decision: str,
) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "equivalent-bullet-review",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    result = candidate / "RESULT.md"
    _replace_metadata(
        result,
        status="retained",
        status_history="speculative -> reviewer_candidate -> retained",
        reviewer="independent-review-round-1",
        reviewer_recommendation="retained",
    )
    _replace_section(result, "Reviewer decision basis", decision)

    report = validate_candidate(tmp_path, "equivalent-bullet-review")

    assert "missing_reviewer_decision_basis" in {
        issue.code for issue in report.issues
    }


def test_fenced_heading_cannot_supply_reviewer_decision_basis(
    tmp_path: Path,
) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "fenced-review-bypass",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    result = candidate / "RESULT.md"
    _replace_metadata(
        result,
        status="retained",
        status_history="speculative -> reviewer_candidate -> retained",
        reviewer="independent-review-round-1",
        reviewer_recommendation="retained",
    )
    text = result.read_text(encoding="utf-8")
    text += (
        "\n```markdown\n"
        "## Reviewer decision basis\n\n"
        "- This example text must not count as a real independent decision.\n"
        "```\n"
    )
    result.write_text(text, encoding="utf-8")

    report = validate_candidate(tmp_path, "fenced-review-bypass")

    assert "missing_reviewer_decision_basis" in {
        issue.code for issue in report.issues
    }


def test_multiline_code_span_heading_cannot_supply_reviewer_decision_basis(
    tmp_path: Path,
) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "multiline-code-span-review-bypass",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    result = candidate / "RESULT.md"
    _replace_metadata(
        result,
        status="retained",
        status_history="speculative -> reviewer_candidate -> retained",
        reviewer="independent-review-round-1",
        reviewer_recommendation="retained",
    )
    text = result.read_text(encoding="utf-8").replace(
        "## Reviewer decision basis",
        "## Reviewer decision placeholder",
    )
    text += (
        "\n``\n"
        "## Reviewer decision basis\n\n"
        "- This code-span example must not count as an independent decision.\n"
        "``\n"
    )
    result.write_text(text, encoding="utf-8")

    report = validate_candidate(
        tmp_path,
        "multiline-code-span-review-bypass",
    )

    assert {issue.code for issue in report.issues} >= {
        "missing_section",
        "missing_reviewer_decision_basis",
    }


@pytest.mark.parametrize(
    "code_only_content",
    [
        "    This indented code must not count as an independent decision.",
        (
            "<pre><code>\n"
            "This long illustrative code block must not count as an independent "
            "Reviewer decision.\n"
            "</code></pre>"
        ),
        (
            '<div hidden aria-hidden="true">\n'
            "This hidden HTML text must not count as an independent Reviewer "
            "decision.\n"
            "</div>"
        ),
        (
            '<div data-note=">" hidden>\n'
            "This quote-bearing hidden HTML text must not count as an independent "
            "Reviewer decision.\n"
            "</div>"
        ),
        (
            '<div style="visibility:hidden">\n'
            "This visibility-hidden HTML text must not count as an independent "
            "Reviewer decision.\n"
            "</div>"
        ),
        (
            '> ~~~\n'
            "> This blockquoted fence must not count as an independent Reviewer "
            "decision.\n"
            "> ~~~"
        ),
        (
            "- ```text\n"
            "  This list-contained fence must not count as an independent Reviewer "
            "decision.\n"
            "  ```"
        ),
        (
            "````markdown\n"
            "## Reviewer decision basis\n\n"
            "- This example must remain inside the four-backtick fence.\n"
            "```\n"
            "````"
        ),
    ],
)
def test_code_only_text_cannot_supply_reviewer_decision_basis(
    tmp_path: Path,
    code_only_content: str,
) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "code-only-review-bypass",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    result = candidate / "RESULT.md"
    _replace_metadata(
        result,
        status="retained",
        status_history="speculative -> reviewer_candidate -> retained",
        reviewer="independent-review-round-1",
        reviewer_recommendation="retained",
    )
    _replace_section(
        result,
        "Reviewer decision basis",
        "- Pending independent Reviewer assessment.\n\n" + code_only_content,
    )

    report = validate_candidate(tmp_path, "code-only-review-bypass")

    assert "missing_reviewer_decision_basis" in {
        issue.code for issue in report.issues
    }


def test_visible_indented_paragraph_continuation_is_substantive(
    tmp_path: Path,
) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "indented-visible-review",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    result = candidate / "RESULT.md"
    _replace_metadata(
        result,
        status="retained",
        status_history="speculative -> reviewer_candidate -> retained",
        reviewer="independent-review-round-1",
        reviewer_recommendation="retained",
    )
    _replace_section(
        result,
        "Reviewer decision basis",
        "Decision:\n"
        "    The independent Reviewer found this bounded result sufficiently "
        "supported for retention.",
    )

    report = validate_candidate(tmp_path, "indented-visible-review")

    assert "missing_reviewer_decision_basis" not in {
        issue.code for issue in report.issues
    }


def test_duplicate_required_section_is_rejected(tmp_path: Path) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "duplicate-result-section",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    result = candidate / "RESULT.md"
    result.write_text(
        result.read_text(encoding="utf-8")
        + "\n## Summary\n\n- Duplicate summary must be rejected.\n",
        encoding="utf-8",
    )

    report = validate_candidate(tmp_path, "duplicate-result-section")

    assert "duplicate_section" in {issue.code for issue in report.issues}


def test_falsified_status_requires_retained_negative_or_failed_artifact(
    tmp_path: Path,
) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "unsupported-falsification",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    result = candidate / "RESULT.md"
    _replace_metadata(
        result,
        status="falsified",
        status_history="speculative -> reviewer_candidate -> falsified",
        reviewer="independent-review-round-1",
        reviewer_recommendation="falsified",
    )
    _replace_section(
        result,
        "Negative and failed results",
        "- The narrative claims a negative outcome but retains no primary artifact.",
    )
    _replace_section(
        result,
        "Reviewer decision basis",
        "- The reviewer considered the stated outcome decisive within the bounded "
        "domain, pending protocol validation.",
    )

    report = validate_candidate(tmp_path, "unsupported-falsification")

    assert "missing_falsification_evidence" in {
        issue.code for issue in report.issues
    }


def test_references_ledger_requires_typed_existing_references(tmp_path: Path) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "reference-ledger",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    result = candidate / "RESULT.md"
    _replace_section(result, "References", "- evidence/inputs/missing.txt")

    report = validate_candidate(tmp_path, "reference-ledger")

    assert "invalid_reference_entry" in {issue.code for issue in report.issues}


def test_reference_entry_requires_nonempty_purpose(tmp_path: Path) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "reference-purpose",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    result = candidate / "RESULT.md"
    _replace_section(
        result,
        "References",
        "- [reference] https://www.nist.gov",
    )

    report = validate_candidate(tmp_path, "reference-purpose")

    assert "invalid_reference_entry" in {issue.code for issue in report.issues}


def test_markdown_links_inside_code_are_not_artifact_references(
    tmp_path: Path,
) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "code-example-reference",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    result = candidate / "RESULT.md"
    _replace_section(
        result,
        "Work performed",
        "```markdown\n[illustrative link](not-a-real-artifact.txt)\n```\n\n"
        "The example documents syntax only and is not an evidence reference.",
    )
    _replace_section(
        result,
        "Summary",
        "The inline examples `[illustrative](also-not-real.txt)` and "
        "``[double marker](also-not-real-2.txt)`` are not retained artifact "
        "references.",
    )

    report = validate_candidate(tmp_path, "code-example-reference")

    assert "missing_reference" not in {issue.code for issue in report.issues}


def test_illegal_state_transition_is_rejected(tmp_path: Path) -> None:
    candidate = initialize_candidate(
        tmp_path,
        "illegal-transition",
        question="Question?",
        hypothesis="Hypothesis.",
    )
    result = candidate / "RESULT.md"
    _replace_metadata(
        result,
        status="promoted",
        status_history="speculative -> promoted",
        reviewer="reviewer-1",
        reviewer_recommendation="promoted",
    )

    report = validate_candidate(tmp_path, "illegal-transition")

    assert not report.valid
    assert "illegal_status_transition" in {issue.code for issue in report.issues}


def test_cli_init_and_validate_return_machine_readable_status(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "init",
                "--project-root",
                str(tmp_path),
                "--idea-id",
                "cli-idea",
                "--question",
                "Question?",
                "--hypothesis",
                "Hypothesis.",
            ]
        )
        == 0
    )
    assert '"created"' in capsys.readouterr().out
    assert (
        main(
            [
                "validate",
                "--project-root",
                str(tmp_path),
                "--idea-id",
                "cli-idea",
            ]
        )
        == 0
    )
    assert '"valid": true' in capsys.readouterr().out
