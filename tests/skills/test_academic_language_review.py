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
