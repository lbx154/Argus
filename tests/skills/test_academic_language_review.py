from argus_skill.skills.academic_language_review import (
    _deterministic_assessment,
    _numbered_source_excerpt,
    _review_prompt,
    _revision_directives,
    _section_text,
    find_introduction_readability_issues,
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


def test_intro_word_count_is_reviewer_signal_not_hard_gate() -> None:
    tex = "\n".join(
        [
            r"\begin{abstract}Agent evaluations need benchmark-grounded memory controls. "
            r"We study a verifier-gated skill memory on public tasks. "
            r"The method uses a hosted backend with fixed decoding and budget. "
            r"It improves task success by 8 points over the strongest baseline. "
            r"The result scopes the contribution to admission policy.\end{abstract}",
            r"\section{Introduction}",
            r"Tool agents can reuse solved episodes, but prior memory systems leave the "
            r"admission decision underspecified \citep{react2023}. Benchmarks expose the "
            r"same gap from another angle \citep{webarena2024}: a reusable hint can "
            r"help one source family and hurt another. Evaluation work also shows that "
            r"agent gains must be tied to source-specific metrics \citep{agentbench2023}. "
            r"This paper evaluates SkillGuard, a verifier-gated memory policy that keeps "
            r"the model, task order, decoding budget, and scorer fixed while changing "
            r"which solved episodes become reusable skills. Across the completed matrix, "
            r"SkillGuard improves verified completion by 8 points over the strongest "
            r"runnable baseline. Our contribution is a scoped admission protocol, a "
            r"three-source benchmark comparison, and an analysis of rejected memories.",
            r"\section{Related Work}",
            "Prior work motivates the benchmark and memory design.",
            r"\section{Method}",
            "The method describes a controller, verifier, skill memory, benchmark harness, "
            "gpt-5-mini backend, temperature 0.0, max_tokens 512, fixed token budget, "
            "seed policy, and stopping rules.",
            r"\section{Experimental Setup}",
            "The evaluation uses public tasks, baselines, metrics, paired tests, and a "
            "fixed budget across conditions.",
            r"\section{Results}",
            "SkillGuard improves success by 8 points on the benchmark matrix.",
            r"\section{Limitations}",
            "The claim is scoped to the evaluated public tasks.",
        ]
    )

    result = _deterministic_assessment(tex)
    issue = next(
        issue
        for issue in result["issues"]
        if issue["code"] == "thin_introduction_depth_signal"
    )

    assert issue["severity"] == "minor"
    assert "hard_gate" not in issue
    assert result["required_checks"]["clear_problem_gap_contribution"] is True
    assert result["section_scores"]["introduction"] >= 4.0


def test_intro_result_preview_accepts_natural_metric_units() -> None:
    preview_sentences = [
        r"PairScorer reaches 87.70\% Mind2Web operation accuracy under the fixed split.",
        "PairScorer improves Mind2Web operation accuracy by 84.42 percentage points.",
    ]
    for preview in preview_sentences:
        tex = "\n".join(
            [
                r"\section{Introduction}",
                r"Prior planning and web-agent work motivate the decision problem "
                r"\citep{react2023,webarena2024,agentbench2023}.",
                preview,
                r"We evaluate PairScorer and report a scoped benchmark contribution.",
                r"\section{Related Work}",
                "Prior work motivates the benchmark and method.",
            ]
        )

        codes = {code for code, _message in find_introduction_readability_issues(tex)}

        assert "introduction_missing_quantified_result_preview" not in codes


def test_review_prompt_tells_model_not_to_use_fixed_intro_word_gate() -> None:
    prompt = _review_prompt(
        source_text_by_path={"paper/main.tex": r"\section{Introduction}Short but complete."},
        deterministic={
            "score_1_to_5": 5.0,
            "section_scores": {},
            "required_checks": {},
            "issues": [],
        },
        threshold=4.0,
    )

    assert "Introduction word count is only a reviewer signal" in prompt
    assert "do not reject solely because a word counter is below a fixed target" in prompt
    assert "emit at most one revision directive per section/action pair" in prompt


def test_intro_revision_directives_are_bundled_by_section() -> None:
    directives = _revision_directives(
        issues=[
            {
                "severity": "major",
                "hard_gate": True,
                "action": "tighten_contribution_sentence",
                "target": "paper/main.tex",
                "message": "section score contribution_framing=3.4 is below 4",
            },
            {
                "severity": "major",
                "hard_gate": True,
                "action": "calibrate_claim",
                "target": "paper/main.tex",
                "message": "Introduction does not satisfy clear_problem_gap_contribution",
            },
        ],
        model_review={
            "revision_directives": [
                {
                    "action": "rewrite_introduction",
                    "target": "Introduction",
                    "rationale": "Strengthen the opening into a clearer EMNLP-style arc.",
                },
                {
                    "action": "rewrite_introduction",
                    "target": "paper/main.tex",
                    "rationale": "Introduction must preview the main empirical result.",
                },
            ]
        },
    )

    intro_directives = [
        directive for directive in directives if directive["target"] == "Introduction"
    ]
    assert intro_directives == [
        {
            "action": "rewrite_introduction",
            "target": "Introduction",
            "rationale": "Strengthen the opening into a clearer EMNLP-style arc.",
            "expected_effect": "replace generic setup with problem-specific motivation",
        }
    ]


def test_section_text_expands_simple_paper_macros() -> None:
    tex = "\n".join(
        [
            r"\newcommand{\PairScorerBase}{PairScorer-Base}",
            r"\section{Experimental Setup}",
            r"The evaluated backend is \PairScorerBase{} for all lanes.",
        ]
    )

    assert "PairScorer-Base" in _section_text(tex, "Experimental Setup")


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
