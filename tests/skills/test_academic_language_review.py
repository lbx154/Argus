from argus_skill.skills.academic_language_review import (
    _deterministic_assessment,
    _review_prompt,
)


def test_hype_issue_reports_exact_terms_and_lines() -> None:
    tex = "\n".join(
        [
            r"\section{Introduction}",
            "We evaluate a novel repair-memory policy.",
            "The method is revolutionary for this benchmark slice.",
            r"\section{Experiments}",
            "Results remain benchmark-scoped.",
        ]
    )

    result = _deterministic_assessment(tex)

    issue = next(
        issue
        for issue in result["issues"]
        if issue["code"] == "salesy_novel_language"
    )
    spans = issue["evidence_spans"]
    assert issue["target"] == "paper/main.tex lines 2, 3"
    assert "line 2 `novel`" in issue["message"]
    assert "line 3 `revolutionary`" in issue["message"]
    assert spans == [
        {
            "source_path": "paper/main.tex",
            "line": 2,
            "term": "novel",
            "quote": "We evaluate a novel repair-memory policy.",
        },
        {
            "source_path": "paper/main.tex",
            "line": 3,
            "term": "revolutionary",
            "quote": "The method is revolutionary for this benchmark slice.",
        },
    ]


def test_formulaic_prose_overuse_is_a_hard_revision_issue() -> None:
    tex = "\n".join(
        [
            r"\begin{abstract}"
            "This paper studies a concrete agent-memory admission problem where "
            "unfiltered storage harms later task reuse. We evaluate a verifier "
            "gate with a hosted model, three benchmark sources, and paired "
            "baselines. The result supports a scoped contribution about storage "
            "quality. The implication is that memory papers should report what "
            "enters the store and why."
            r"\end{abstract}",
            r"\section{Introduction}",
            "Prior work motivates agent memory \\citep{a,b}.",
            " ".join(
                "The paper studies admission rather than storage volume."
                for _ in range(12)
            ),
            r"\section{Experiments}",
            "Results remain benchmark-scoped with quantified multi-source comparisons.",
        ]
    )

    result = _deterministic_assessment(tex)

    issue = next(
        issue
        for issue in result["issues"]
        if issue["code"] == "contrastive_template_overuse"
    )
    assert issue["hard_gate"] is True
    assert issue["action"] == "delete_filler"


def test_model_review_prompt_includes_structured_float_digest() -> None:
    tex = "\n".join(
        [
            r"\begin{abstract}We test a scoped memory gate on public tasks.\end{abstract}",
            r"\section{Introduction}",
            "Problem framing with a quantified result preview.",
            r"\section{Results}",
            r"\begin{table}[t]",
            r"\centering",
            r"\begin{tabular}{lcc}",
            r"\toprule",
            r"Benchmark / Source & Model / Backend & Result \\",
            r"\midrule",
            r"RepoBench-P & gpt-5-mini & 40/80 \\",
            r"\bottomrule",
            r"\end{tabular}",
            r"\caption{RepoBench-P reaches 40/80 under verifier gating.}",
            r"\label{tab:repobench}",
            r"\end{table}",
        ]
    )

    prompt = _review_prompt(
        source_text_by_path={"paper/main.tex": tex},
        deterministic={
            "score_1_to_5": 5.0,
            "section_scores": {},
            "required_checks": {},
            "issues": [],
        },
        threshold=4.0,
    )

    assert "Structured source digest" in prompt
    assert "label=tab:repobench" in prompt
    assert "Benchmark / Source & Model / Backend & Result" in prompt
