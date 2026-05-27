from argus_skill.skills.academic_language_review import _deterministic_assessment


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
            "Results remain benchmark-scoped with a quantified 240-task comparison.",
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
