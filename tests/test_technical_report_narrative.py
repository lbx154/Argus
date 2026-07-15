from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "technical_report"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _all_report_source() -> str:
    main = _read("technical_report/main.tex")
    sections = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((REPORT / "sections").glob("*.tex"))
    )
    return main + "\n" + sections


def test_report_identity_is_dense_intelligence_03() -> None:
    main = _read("technical_report/main.tex")
    source = _all_report_source()

    assert "Technical Report 0.3" in main
    assert "Dense Intelligence for an Expanding Research Frontier" in main
    assert "Technical Report 0.2" not in source
    assert "version 0.2" not in source


def test_cover_is_light_blue_gold() -> None:
    main = _read("technical_report/main.tex")

    assert r"\definecolor{systemblue}{HTML}{315BCE}" in main
    assert r"\definecolor{deepblue}{HTML}{214884}" in main
    assert r"\definecolor{frontiergold}{HTML}{C38A20}" in main
    assert r"\pagecolor{bonewhite}" in main
    assert "Dark cover" not in main


def test_act_one_sections_and_master_spine_are_wired() -> None:
    main = _read("technical_report/main.tex")

    assert r"\input{sections/01_executive_thesis}" in main
    assert r"\input{sections/02_dense_intelligence}" in main
    assert r"\input{sections/03_episodic_agents}" in main
    thesis = _read("technical_report/sections/01_executive_thesis.tex")
    assert r"\includegraphics" in thesis
    assert "master_spine.png" in thesis
    assert "Every run expands the frontier." in thesis


def test_dense_intelligence_not_presented_as_measured_score() -> None:
    dense = _read("technical_report/sections/02_dense_intelligence.tex")

    assert r"\rho_{\mathrm{DI}}(T)" in dense
    assert "explanatory construct" in dense
    assert "not a reported benchmark metric" in dense
    assert "universal superiority" not in dense


def test_no_banned_rhetoric_in_report_source() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((REPORT / "sections").glob("*.tex"))
    )
    assert len(re.findall(r"\bguardrails?\b", source, flags=re.IGNORECASE)) <= 2
    assert not re.search(r"\bdumb pipe\b|\bplumbing\b|not smarter than", source, flags=re.IGNORECASE)


def test_act_two_sections_are_wired() -> None:
    main = _read("technical_report/main.tex")
    for section in (
        "04_argus_life",
        "05_roles_planes",
        "06_lifecycle_state",
        "07_evidence_review",
        "08_reliability_resources",
    ):
        assert rf"\input{{sections/{section}}}" in main


def test_act_two_preserves_committed_runtime_facts() -> None:
    source = "\n".join(
        _read(f"technical_report/sections/{name}.tex")
        for name in (
            "04_argus_life",
            "05_roles_planes",
            "06_lifecycle_state",
            "07_evidence_review",
            "08_reliability_resources",
        )
    )
    for required in (
        "Manager",
        "Planner",
        "Engineer",
        "Reviewer",
        "control plane",
        "execution plane",
        "evidence plane",
        "checkpoint.json",
        "1.5 million",
        "1,800",
        "112",
        "75",
        "artifact-digest",
    ):
        assert required in source
    assert "CHECKPOINT.md" not in source
    assert "fresh session every round" not in source


def test_result_signing_is_stated_as_optional_and_unused_not_absent() -> None:
    evidence = _read("technical_report/sections/07_evidence_review.tex")

    for required in (
        "optional",
        "off by default",
        "Ed25519",
        "does not rely",
        "not used for the results reported here",
    ):
        assert required in evidence, f"missing required concept: {required!r}"

    assert "no cryptographic result-signing component exists" not in evidence


def test_runtime_evolution_formula_fixes_model_parameters() -> None:
    source = _read("technical_report/sections/09_runtime_evolution.tex")

    assert r"H_{t+1}" in source
    assert r"U(H_t,\tau_t,E_t)" in source
    assert r"\theta_{t+1}=\theta_t" in source
    for symbol in ("M_t", "S_t", "A_t", "V_t", "R_t", "Q_t"):
        assert symbol in source
    assert "online parameter training" in source


def test_runtime_evolution_ownership_is_source_accurate() -> None:
    source = _read("technical_report/sections/09_runtime_evolution.tex")

    # Prefer honest "change source / authoritative owner" columns over a false
    # proposer/accepter abstraction.
    assert "Change source" in source
    assert "Authoritative owner" in source
    assert "Proposing role" not in source
    assert "Accepting owner" not in source

    # The Planner is the SOLE owner of the per-stage checklist: it authors and
    # applies checklist_ops. The Reviewer is feedback-only via checklist_feedback.
    assert "authors and applies" in source
    assert r"checklist\_ops" in source
    assert r"checklist\_feedback" in source
    assert "Planner-owned" in source
    assert "feedback-only" in source

    # The A row is honestly operator-owned, not part of a self-certification split.
    assert "operator-owned" in source

    # No false universal "every update has a distinct accepting owner /
    # proposer never certifies" claim.
    assert "no role both proposes and certifies" not in source
    assert "distinct accepting owner" not in source
    assert "never certifies its own output" not in source

    # M/S/R/Q persistence facts preserved.
    for fact in ("checkpoint.json", r"skill\_ops", "backlog.jsonl", "continuous.json"):
        assert fact in source


def test_reliability_global_daily_cap_default_is_source_accurate() -> None:
    source = _read("technical_report/sections/08_reliability_resources.tex")

    # Canonical resolver default is $30, disabled at 0 — not "off unless set".
    assert "off unless set" not in source
    assert r"global daily cap of \$30" in source
    assert r"resolve\_budget\_caps" in source
    assert r"\srcpath{core/knobs.py}" in source
    assert "disables it" in source

    # Preserve the other live defaults.
    assert r"per-mission preflight cap of \$30" in source
    assert r"daily cap of \$180" in source
    assert "two active" in source
    assert "daemons across projects" in source


def test_process_data_strictly_contains_final_artifact() -> None:
    source = _read("technical_report/sections/10_process_data.tex")

    assert r"D_{\mathrm{process}}" in source
    assert r"D_{\mathrm{final}}" in source
    assert "states, actions, evidence, feedback" in source


def test_ood_expansion_has_non_monotone_caveat() -> None:
    source = _read("technical_report/sections/11_ood_expansion.tex")

    assert r"C_{t+1}" in source
    assert r"\operatorname{Verify}(c,E_t)" in source
    assert "does not guarantee" in source
    assert "monotone" in source
    assert "negative result" in source


FINAL_SECTION_INPUTS = (
    "01_executive_thesis",
    "02_dense_intelligence",
    "03_episodic_agents",
    "04_argus_life",
    "05_roles_planes",
    "06_lifecycle_state",
    "07_evidence_review",
    "08_reliability_resources",
    "09_runtime_evolution",
    "10_process_data",
    "11_ood_expansion",
    "12_frontier_evidence",
    "13_limitations_roadmap",
)


def test_report_has_exactly_thirteen_main_inputs() -> None:
    main = _read("technical_report/main.tex")
    inputs = re.findall(r"\\input\{sections/([0-9]{2}_[^}]+)\}", main)
    assert tuple(inputs[:13]) == FINAL_SECTION_INPUTS
    assert len([item for item in inputs if not item.startswith("90_")]) == 13


def test_frontier_evidence_preserves_public_values() -> None:
    source = _read("technical_report/sections/12_frontier_evidence.tex")
    for value in (
        "Global \\#6",
        "0.9636",
        "0.9855",
        "79.77",
        "63/82",
        "76.8\\%",
        "28.0",
        "41",
        "35 manuscripts",
        "6 drafts",
    ):
        assert value in source
    assert "accepted papers" in source
    assert "does not claim" in source


def test_appendix_defines_all_formal_symbols() -> None:
    source = _read("technical_report/sections/90_appendix.tex")
    for symbol in (
        r"\rho_{\mathrm{DI}}",
        r"\lambda",
        r"\eta_d",
        r"\eta_x",
        r"\eta_v",
        "H_t",
        r"\tau_t",
        "E_t",
        r"\theta_t",
        "C_t",
        r"\epsilon",
    ):
        assert symbol in source


def _prose_word_count(text: str) -> int:
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    text = "\n".join(line for line in text.splitlines() if not line.startswith("|"))
    return len(re.findall(r"\b[\w×·#.–]+\b", text))


def test_english_readme_uses_expanding_frontier_spine() -> None:
    readme = _read("README.md")

    assert "Every run expands the frontier." in readme
    assert "Dense Intelligence" in readme
    assert "Runtime Evolution" in readme
    assert "master_spine.png" in readme
    assert "Technical Report 0.3" in readme
    assert 1200 <= _prose_word_count(readme) <= 1600


def test_readmes_preserve_public_proof_points() -> None:
    english = _read("README.md")
    chinese = _read("README.zh-CN.md")
    for value in (
        "Global #6",
        "0.9636",
        "0.9855",
        "79.77",
        "63/82",
        "76.8%",
        "28.0",
        "41",
    ):
        assert value in english
        assert value in chinese
    assert "35 manuscripts" in english
    assert "6 drafts" in english
    assert "35 篇 manuscript" in chinese
    assert "6 篇 draft" in chinese


def test_readmes_bound_runtime_evolution_claim() -> None:
    english = _read("README.md")
    chinese = _read("README.zh-CN.md")

    assert "does not require online parameter training" in english
    assert "does not guarantee that every run adds capability" in english
    assert "不依赖在线参数训练" in chinese
    assert "不保证每次 run 都增加能力" in chinese


def test_reliability_decision_stall_default_is_four() -> None:
    # Rebase pulled in runner.py commit 61c53cad which set
    # SupervisedConfig.stall_threshold = 4; the guard fires at FOUR consecutive
    # nondecision rounds, not two. The unrelated no-progress bail default stays 2.
    source = _read("technical_report/sections/08_reliability_resources.tex")
    norm = re.sub(r"\s+", " ", source)

    assert "Four consecutive nondecision rounds trip the stall guard" in norm
    assert "Two consecutive nondecision rounds trip the stall guard" not in norm

    # Guard table: the Decision-stall row default is 4.
    assert re.search(r"Decision stall &.*? & 4 & no", norm)
    assert not re.search(r"Decision stall &.*? & 2 & no", norm)

    # Do NOT change the unrelated no-progress bail default (still 2).
    assert re.search(r"No-progress bail &.*? & 2 & no", norm)


def test_manager_front_door_emits_six_axes() -> None:
    # The single front-door model call classifies on SIX axes and requires
    # "EXACTLY six lines" of output (life/router.py), not three.
    source = _read("technical_report/sections/05_roles_planes.tex")
    norm = re.sub(r"\s+", " ", source)

    assert "six structured axes" in norm
    assert "three structured axes" not in norm

    for axis in (
        r"\code{CONFIG}",
        r"\code{CONTROL}",
        r"\code{ROUTE}",
        r"\code{LIFETIME}",
        r"\code{FAST\_REPLY}",
        r"\code{NAME}",
    ):
        assert axis in source, f"missing front-door axis: {axis!r}"


def test_appendix_figure_provenance_counts_six_structural_two_data() -> None:
    # Final hybrid contract: SIX structural figures are image-2 outputs and
    # TWO deterministic data figures are matplotlib-drawn. The appendix
    # provenance paragraph must state the split and point at both manifests;
    # the six structural figures are authoritatively enumerated in
    # IMAGE2_FIGURES.json (verified by the AI-figure manifest tests).
    source = _read("technical_report/sections/90_appendix.tex")
    norm = re.sub(r"\s+", " ", source)

    assert "six structural figures" in norm
    assert "gpt-image-2" in norm
    assert "two deterministic data figures" in norm
    assert "six deterministic figures" not in norm

    for fig in (r"\code{public\_results}", r"\code{paper\_portfolio}"):
        assert fig in source, f"missing data figure: {fig!r}"

    assert r"IMAGE2\_FIGURES.json" in source
    assert r"REPORT\_FIGURES.json" in source


def test_readmes_scope_ownership_separation_honestly() -> None:
    # The public READMEs must NOT universalize the work-vs-certification
    # separation to all six H components. Only M and S get a distinct certifying
    # owner (the Reviewer certifies work it did not author); A is operator-owned;
    # V is Planner-owned with the Reviewer feedback-only; R/Q keep their owners.
    english = _read("README.md")
    chinese = _read("README.zh-CN.md")
    en_norm = re.sub(r"\s+", " ", english)
    zh_ns = re.sub(r"\s+", "", chinese)

    # The false universal is gone from BOTH languages.
    assert (
        "same separation that governs completion extends to memory, skills, "
        "tools, verifiers, routing, and evaluations" not in en_norm
    )
    assert (
        "这套职责分离，因此同样延伸到记忆、skill、工具、verifier、路由与评测"
        not in zh_ns
    )

    # English: honest per-component scoping.
    assert "Reviewer certifies" in en_norm          # M/S distinct-owner certification
    assert "operator-owned" in en_norm              # A (tools)
    assert "Planner-owned" in en_norm               # V (verifiers)
    assert "feedback-only" in en_norm               # Reviewer on checklists

    # Chinese: mirror the same honest scoping (whitespace stripped, so no spaces).
    assert "Reviewer认证" in zh_ns                   # M/S certification
    assert "operator拥有" in zh_ns                    # A operator-owned
    assert "Planner拥有" in zh_ns                     # V Planner-owned
    assert "仅提供反馈" in zh_ns                       # Reviewer feedback-only
