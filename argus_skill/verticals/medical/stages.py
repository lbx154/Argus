"""Medical and pharmaceutical evidence-research vertical."""

from __future__ import annotations

from pathlib import Path

from ...skills.stage_machine import ChecklistItem

STAGE_ORDER = ("scope", "retrieve", "normalize", "analyze", "review", "deliver")
CHECKLIST_STAGE_ORDER = STAGE_ORDER
WORKFLOW_MODE = "staged"
MISSION_KIND = "research"
REQUIRE_INDEPENDENT_REVIEW = True
COMPLETION_CONTRACT_VERSION = 1
completion_gate = "certified"

STAGE_PRIMARY_DELIVERABLES = {
    "deliver": (
        "medical/evidence.jsonl",
        "medical/evidence_matrix.csv",
        "medical/target_disease_memo.md",
        "medical/review.json",
    ),
}

CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "scope": (
        ChecklistItem(
            id="scope.medical-scope",
            statement=(
                "The target, disease, subtype or population, decision question, date "
                "bounds, exclusions, and non-diagnostic use are explicit; the request "
                "contains no patient-identifying information."
            ),
            evidence_hint=(
                "a target-disease scope record with population, decision boundary, "
                "time window, exclusions, and research-only purpose"
            ),
        ),
        ChecklistItem(
            id="scope.medical-identity",
            statement=(
                "Submitted and canonical target and disease identities, aliases, "
                "species, molecular level, biomarker context, and unresolved ambiguity "
                "are recorded without silently merging genes, proteins, pathways, "
                "drugs, indications, or disease subtypes."
            ),
            evidence_hint=(
                "submitted terms, canonical identifiers or explicit unresolved status, "
                "alias provenance, and disambiguation decisions"
            ),
        ),
    ),
    "retrieve": (
        ChecklistItem(
            id="retrieve.medical-source-plan",
            statement=(
                "Source selection, exact queries, date bounds, deduplication, full-text "
                "needs, update policy, and stop conditions are explicit before synthesis."
            ),
            evidence_hint=(
                "source/query registry covering PubMed, ClinicalTrials.gov, selection "
                "rules, provenance fields, and infrastructure-failure treatment"
            ),
        ),
        ChecklistItem(
            id="retrieve.medical-provenance",
            statement=(
                "Every retrieved record preserves source type, source identifier, "
                "canonical URL, exact query, retrieval time, raw response, normalized "
                "fields, and the raw response file each evidence row consumed."
            ),
            evidence_hint=(
                "query journal, immutable raw responses, normalized evidence JSONL, and "
                "source-local raw_artifact paths"
            ),
        ),
        ChecklistItem(
            id="retrieve.medical-failures",
            statement=(
                "Provider, transport, parsing, rate-limit, resource, and interrupted "
                "records remain separate from biomedical evidence and do not count as "
                "negative target-disease results."
            ),
            evidence_hint=(
                "retained infrastructure-failure rows with source, request, time, error "
                "class, and retryability"
            ),
        ),
    ),
    "normalize": (
        ChecklistItem(
            id="normalize.medical-comparability",
            statement=(
                "Source and scope fields are distinct, and any evidence comparison aligns "
                "or explicitly distinguishes model or population, disease subtype, "
                "biomarker, treatment line, intervention, comparator, endpoint, follow-up, "
                "sample size, registry update, and actual data cutoff."
            ),
            evidence_hint=(
                "row-level normalized fields and explicit non-comparability notes for "
                "mismatched studies or trials"
            ),
        ),
    ),
    "analyze": (
        ChecklistItem(
            id="analyze.medical-evidence-strata",
            statement=(
                "The analysis proportionally covers mechanism, human genetics, "
                "preclinical, clinical, safety, failed-program, and contradictory "
                "evidence needed by the decision rather than treating publication count "
                "as evidence strength."
            ),
            evidence_hint=(
                "a question-driven evidence matrix with applicable strata, missing "
                "strata, and explicit evidence ceilings"
            ),
        ),
        ChecklistItem(
            id="analyze.medical-claim-ceiling",
            statement=(
                "Mechanism, association, target engagement, efficacy, safety, and causal "
                "claims remain at or below the strongest directly inspected evidence; "
                "metadata, registration, prediction, and preclinical results are not "
                "promoted into clinical conclusions."
            ),
            evidence_hint=(
                "claim-by-claim evidence class, source support, uncertainty, and maximum "
                "defensible wording"
            ),
        ),
        ChecklistItem(
            id="analyze.medical-conflicts",
            statement=(
                "Contradictory results, failed programs, null findings, missing sources, "
                "and alternative explanations are retained and analyzed rather than "
                "removed by convenient source selection."
            ),
            evidence_hint=(
                "conflict and gap ledger linked to both supportive and opposing records"
            ),
        ),
    ),
    "review": (
        ChecklistItem(
            id="review.medical-source-support",
            statement=(
                "Each material conclusion is supported by the cited source at the stated "
                "scope; PubMed metadata is not described as full-text review and trial "
                "registration is not described as efficacy evidence."
            ),
            evidence_hint=(
                "independent claim-source checks against primary text or exact registry "
                "fields, with unresolved full-text needs marked"
            ),
        ),
        ChecklistItem(
            id="review.medical-numeric-fidelity",
            statement=(
                "Numbers, units, sample sizes, phases, statuses, endpoints, dates, follow-up, "
                "registry updates, and data cutoffs match their source and are not compared "
                "across incompatible study contexts."
            ),
            evidence_hint=(
                "a source-located check of every decision-relevant number and trial field"
            ),
        ),
    ),
    "deliver": (
        ChecklistItem(
            id="deliver.medical-nondiagnostic-boundary",
            statement=(
                "The delivery is explicitly limited to research and portfolio decisions, "
                "contains no patient-specific diagnosis or treatment selection, and does "
                "not imply that Argus designed or clinically validated a new drug."
            ),
            evidence_hint=(
                "research-use statement plus a scan for patient-specific or prescriptive claims"
            ),
        ),
        ChecklistItem(
            id="deliver.medical-auditability",
            statement=(
                "The final dossier includes the scope, query history, raw-source links, "
                "normalized evidence ledger, comparison matrix, memo, unresolved gaps, "
                "cumulative infrastructure failures, and independent review state."
            ),
            evidence_hint=(
                "the complete dossier with inspectable source IDs, raw response "
                "files, and the independent review's conclusion"
            ),
        ),
    ),
}


def stage_completion_issues(stage: str, project_root: Path) -> tuple[str, ...]:
    if str(stage or "").strip().lower() != "deliver":
        return ()
    from .dossier import validate_dossier

    return tuple(validate_dossier(project_root))


def role_banner(role: str) -> str:
    """Load concise medical context for one persistent role."""
    name = {
        "manager": "manager/medical-manager.md",
        "planner": "planner/medical-planning.md",
        "engineer": "engineer/target-disease-research.md",
        "reviewer": "reviewer/medical-evidence-review.md",
    }.get(str(role or "").strip().lower())
    if name is None:
        return ""
    text = (Path(__file__).parent / "skills" / name).read_text(encoding="utf-8")
    if text.startswith("---"):
        _frontmatter, _separator, body = text[3:].partition("---")
        return body.strip()
    return text.strip()


__all__ = [
    "CHECKLIST_ITEMS",
    "CHECKLIST_STAGE_ORDER",
    "COMPLETION_CONTRACT_VERSION",
    "MISSION_KIND",
    "REQUIRE_INDEPENDENT_REVIEW",
    "STAGE_ORDER",
    "STAGE_PRIMARY_DELIVERABLES",
    "WORKFLOW_MODE",
    "completion_gate",
    "role_banner",
    "stage_completion_issues",
]
