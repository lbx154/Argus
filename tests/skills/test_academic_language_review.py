from argus_skill.skills.academic_language_review import (
    _deterministic_assessment,
    _numbered_source_excerpt,
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


def test_review_prompt_pins_late_limitations_and_tables_after_truncation() -> None:
    long_body = "\n".join(
        f"Long setup sentence {index}. " * 8 for index in range(700)
    )
    tex = "\n".join(
        [
            r"\begin{abstract}This paper studies a concrete benchmark gap. "
            r"We evaluate PairScorer on three benchmarks. "
            r"The result previews 6.15\% held-out step accuracy. "
            r"The implication is scoped to branch selection. "
            r"The paper states limitations explicitly.\end{abstract}",
            r"\section{Introduction}",
            long_body,
            r"\section{Experimental Setup}",
            r"\begin{table*}[t]",
            r"\caption{Cross-benchmark matrix: PairScorer reaches 6.15\% on Mind2Web.}",
            r"\label{tab:cross-benchmark-matrix}",
            r"\begin{tabular}{llll}",
            r"Benchmark & Backend & Metric & Score \\",
            r"Mind2Web & PairScorer & step accuracy & 6.15\% \\",
            r"\end{tabular}",
            r"\end{table*}",
            r"\section{Limitations and Ethical Considerations}",
            "TravelPlanner remains a null-control lane and the claim is scoped.",
        ]
    )

    prompt = _review_prompt(
        source_text_by_path={"paper/main.tex": tex},
        deterministic={"score_1_to_5": 5.0, "issues": []},
        threshold=4.0,
    )

    assert "Pinned structural LaTeX excerpts" in prompt
    assert "tab:cross-benchmark-matrix" in prompt
    assert "Limitations and Ethical Considerations" in prompt


def test_numbered_source_excerpt_preserves_tail_when_truncated() -> None:
    tex = "\n".join(f"line {index}" for index in range(400))
    tex += "\nTAIL_MARKER_LIMITATIONS"

    excerpt = _numbered_source_excerpt({"paper/main.tex": tex}, limit=900)

    assert "preserving source tail" in excerpt
    assert "TAIL_MARKER_LIMITATIONS" in excerpt
