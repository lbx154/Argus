from __future__ import annotations

import os
from pathlib import Path

import pytest

from argus.domains.chemistry.playground import (
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
