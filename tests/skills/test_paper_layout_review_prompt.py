from __future__ import annotations

from argus_skill.skills.paper_layout_review import (
    _deterministic_assessment,
    _revision_directives,
    _vision_issues,
    _vision_prompt,
)


def test_vision_prompt_frames_emnlp_2026_visual_submission_review() -> None:
    prompt = _vision_prompt(
        deterministic={
            "score_1_to_5": 5.0,
            "criteria_scores": {"float_balance": 5.0},
            "issues": [
                {
                    "code": "dense_table_float_page",
                    "page": 6,
                    "message": "page 6 contains dense table floats",
                }
            ],
        },
        threshold=4.0,
    )

    assert "independent visual reviewer for an EMNLP 2026 paper" in prompt
    assert "polished, standard two-column conference paper" in prompt
    assert "large blank lower-page regions" in prompt
    assert "page number when visible, the visual target" in prompt
    assert "visual_evidence" in prompt
    assert "Complete improvement guidance is mandatory" in prompt
    assert "source_targets" in prompt
    assert "specific_edits" in prompt
    assert "implementation_guidance" in prompt
    assert "Shortening an underfilled body makes the early-References defect worse" in prompt
    assert "Allowed action values:" in prompt
    assert "dense_table_float_page" in prompt


def test_vision_issues_preserve_specific_target_for_directives() -> None:
    issues = _vision_issues(
        {
            "blocking_issues": [
                {
                    "issue": "Page 6 has a severe float/layout imbalance.",
                    "page": 6,
                    "target": "page 6 lower half",
                    "action": "move_float",
                }
            ]
        }
    )

    assert issues == [
        {
            "code": "vision_blocking_issues_0",
            "severity": "major",
            "message": "Page 6 has a severe float/layout imbalance.",
            "action": "move_float",
            "page": 6,
            "hard_gate": True,
            "target": "page 6 lower half",
        }
    ]


def test_vision_guidance_is_preserved_in_revision_directives() -> None:
    issues = _vision_issues(
        {
            "major_issues": [
                {
                    "issue": "Page 6 is an audit-style float dump.",
                    "page": 6,
                    "target": "Tables 3-7 on page 6",
                    "visual_evidence": "Five small tables are stacked with almost no narrative flow.",
                    "action": "merge_tables",
                    "guidance": {
                        "root_cause": "Too many low-density audit tables are being used as body filler.",
                        "source_targets": ["paper/main.tex", "code/make_paper.py"],
                        "specific_edits": [
                            "Merge Tables 3-7 into one reader-facing evidence table.",
                            "Move traceability rows to the appendix.",
                        ],
                        "visual_goal": "Page 6 should read as a results-analysis page, not a checklist.",
                        "verification": ["Recompile and rerun the vision layout review."],
                    },
                }
            ]
        }
    )

    assert issues[0]["visual_evidence"] == "Five small tables are stacked with almost no narrative flow."
    assert issues[0]["guidance"]["specific_edits"] == [
        "Merge Tables 3-7 into one reader-facing evidence table.",
        "Move traceability rows to the appendix.",
    ]

    directives = _revision_directives(issues)

    assert directives == [
        {
            "action": "merge_tables",
            "target": "Tables 3-7 on page 6",
            "rationale": "Page 6 is an audit-style float dump.",
            "expected_effect": "reduce float clutter by combining redundant tables",
            "implementation_guidance": {
                "root_cause": "Too many low-density audit tables are being used as body filler.",
                "source_targets": ["paper/main.tex", "code/make_paper.py"],
                "specific_edits": [
                    "Merge Tables 3-7 into one reader-facing evidence table.",
                    "Move traceability rows to the appendix.",
                ],
                "visual_goal": "Page 6 should read as a results-analysis page, not a checklist.",
                "verification": ["Recompile and rerun the vision layout review."],
            },
        }
    ]


def test_deterministic_review_flags_references_sharing_body_page_with_boundary_action() -> None:
    pages = [
        "Title\nAbstract",
        "Related Work",
        "Method",
        "Results\nFigure 1: overview",
        "Analysis\nTable 1: main results",
        "Ablation\nTable 2: supporting analyses",
        "Limitations",
        "Conclusion\nLimitations and Ethical Considerations\nRelease and Reproducibility\nReferences\n[1] Example",
        "More references",
    ]

    result = _deterministic_assessment(
        tex_text="",
        log_text="",
        layout_text="\f".join(pages),
        threshold=4.0,
    )

    issues = {issue["code"]: issue for issue in result["issues"]}
    assert issues["references_share_body_page"]["action"] == "fix_reference_boundary"
    assert issues["references_share_body_page"]["page"] == 8

    directives = _revision_directives(result["issues"])
    boundary_directive = next(
        directive for directive in directives if directive["action"] == "fix_reference_boundary"
    )
    guidance = boundary_directive["implementation_guidance"]
    assert "evidence-backed body content" in guidance["specific_edits"][0]


def test_deterministic_review_routes_early_references_to_evidence_expansion() -> None:
    pages = [
        "Title",
        "Method",
        "Results",
        "Analysis\nFigure 1: overview",
        "References\n[1] Example",
    ]

    result = _deterministic_assessment(
        tex_text="",
        log_text="",
        layout_text="\f".join(pages),
        threshold=4.0,
    )

    issues = {issue["code"]: issue for issue in result["issues"]}
    assert issues["references_before_full_body"]["action"] == "expand_evidence_content"
