from __future__ import annotations

import struct
import zlib
from pathlib import Path

from argus_skill.skills.paper_layout_review import (
    _deterministic_assessment,
    _png_is_nearly_blank,
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
    assert "Official ACL/EMNLP anonymous review-mode line numbers" in prompt
    assert "must not be treated as debug gutters" in prompt
    assert "Shortening an underfilled body makes the early-References defect worse" in prompt
    assert "Do not require References to begin exactly on page 9" in prompt
    assert "page 10 or later is acceptable" in prompt
    assert "Data/metric/result plots may be regenerated from canonical data" in prompt
    assert "never require image-2 for benchmark-effect" in prompt
    assert "Every other paper-facing figure" in prompt
    assert "must remain an actual image-2/codex-image2 raster" in prompt
    assert "never suggest vector PDF/SVG/TikZ/matplotlib/PIL" in prompt
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


def test_vision_guidance_routes_conceptual_figure_repairs_to_image2() -> None:
    issues = _vision_issues(
        {
            "major_issues": [
                {
                    "issue": "Figure 1 conceptual overview is oversized.",
                    "page": 6,
                    "target": "Figure 1 upper half",
                    "action": "resize_figure",
                    "guidance": {
                        "root_cause": "The conceptual overview consumes too much space.",
                        "source_targets": ["figure source for Figure 1", "paper/main.tex"],
                        "specific_edits": [
                            "Keep labels in sentence case and maintain vector rendering.",
                        ],
                        "visual_goal": "Figure 1 should be smaller but keep vector rendering.",
                        "verification": ["Rerun layout review."],
                    },
                }
            ]
        }
    )

    guidance = issues[0]["guidance"]
    joined_edits = " ".join(guidance["specific_edits"])
    assert "maintain vector rendering" not in joined_edits
    assert "accepted image-2 raster rendering" in joined_edits
    assert "Non-data figure policy:" in joined_edits
    assert "image-2 raster" in guidance["visual_goal"]

    directive = _revision_directives(issues)[0]
    directive_edits = " ".join(directive["implementation_guidance"]["specific_edits"])
    assert "Non-data figure policy:" in directive_edits
    assert "accepted image-2 raster rendering" in directive_edits


def test_mixed_conceptual_and_data_figure_guidance_keeps_data_plot_scripted() -> None:
    issues = _vision_issues(
        {
            "major_issues": [
                {
                    "issue": "Figure 1 overview and Figure 2 benchmark-level effect summary are hard to read.",
                    "page": 8,
                    "target": "Figure 1 overview and Figure 2 benchmark-effects data plot",
                    "action": "regenerate_figure",
                    "guidance": {
                        "root_cause": "The overview and benchmark-effect plot have tiny labels.",
                        "source_targets": ["IMAGE2_FIGURES.json", "code/plot_benchmark_effects.py"],
                        "specific_edits": [
                            "Regenerate Figure 1 through the image-2 prompt/select/review pipeline.",
                            "Regenerate Figure 2 through the image-2 prompt/select/review pipeline as a wide result plot.",
                        ],
                        "visual_goal": "Both figures should read immediately at page view.",
                        "verification": ["Confirm the regenerated rasters are listed in IMAGE2_FIGURES.json."],
                    },
                }
            ]
        }
    )

    guidance = issues[0]["guidance"]
    joined_edits = " ".join(guidance["specific_edits"])
    assert "Regenerate Figure 2 through the image-2" not in joined_edits
    assert "Regenerate Figure 2 from canonical data through its plotting script" in joined_edits
    assert "Regenerate Figure 1 through the image-2" in joined_edits
    assert "Data figure policy:" in joined_edits
    assert "Non-data figure policy:" in joined_edits
    assert "The conceptual figure and any data/result figure should read immediately" in guidance["visual_goal"]

    directive = _revision_directives(issues)[0]
    directive_edits = " ".join(directive["implementation_guidance"]["specific_edits"])
    assert "Regenerate Figure 2 through the image-2" not in directive_edits
    assert "Regenerate Figure 2 from canonical data through its plotting script" in directive_edits


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
    assert "source-backed body content" in guidance["specific_edits"][0]


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


def test_deterministic_review_routes_line_numbered_early_references() -> None:
    pages = [
        "001 Title",
        "082 Related Work",
        "155 Method",
        "238 Results",
        "499 References\n[1] Example",
    ]

    result = _deterministic_assessment(
        tex_text="",
        log_text="",
        layout_text="\f".join(pages),
        threshold=4.0,
    )

    issues = {issue["code"]: issue for issue in result["issues"]}
    assert issues["references_before_full_body"]["page"] == 5


def test_deterministic_review_routes_two_column_line_numbered_references() -> None:
    pages = [
        "001 Title",
        "082 Related Work",
        "155 Method",
        "238 Results",
        "499 References        Hyungjoo Chae et al.\n500 Reference text",
    ]

    result = _deterministic_assessment(
        tex_text="",
        log_text="",
        layout_text="\f".join(pages),
        threshold=4.0,
    )

    issues = {issue["code"]: issue for issue in result["issues"]}
    assert issues["references_before_full_body"]["page"] == 5


def test_deterministic_review_flags_right_column_references_after_body_text() -> None:
    pages = [
        "001 Title",
        "082 Related Work",
        "155 Method",
        "238 Results",
        "545 where the decision point is insufficient.                  References      593\n"
        "546 Conclusion                                                  Minju Gwak and others\n"
        "567 Limitations and Ethical Considerations",
    ]

    result = _deterministic_assessment(
        tex_text="",
        log_text="",
        layout_text="\f".join(pages),
        threshold=4.0,
    )

    issues = {issue["code"]: issue for issue in result["issues"]}
    assert issues["references_share_body_page"]["page"] == 5


def test_deterministic_review_allows_postbody_matter_sharing_valid_reference_page() -> None:
    pages = [
        "Title",
        "Related Work",
        "Method",
        "Experimental Setup\nFigure 1: overview",
        "Main Results\nTable 1: main results",
        "Analysis\nTable 2: supporting analyses",
        "Failure Cases\nFigure 2: profile",
        "Conclusion",
        "Limitations and Ethical Considerations\nReferences\n[1] Example",
        "More references",
    ]

    result = _deterministic_assessment(
        tex_text="",
        log_text="",
        layout_text="\f".join(pages),
        threshold=4.0,
    )

    issues = {issue["code"]: issue for issue in result["issues"]}
    assert "references_share_body_page" not in issues
    assert result["page_flow_contract"]["conclusion_page"] == 8
    assert result["page_flow_contract"]["references_page"] == 9
    assert result["page_flow_contract"]["post_body_pages_uncapped"] is True


def test_deterministic_review_flags_page_six_conclusion_as_underfilled() -> None:
    pages = [
        "Title",
        "Related Work",
        "Method",
        "Experimental Setup\nFigure 1: overview",
        "Main Results\nTable 1: main results",
        "Conclusion\nLimitations and Ethical Considerations",
        "Body tail",
        "References\n[1] Example",
    ]

    result = _deterministic_assessment(
        tex_text="",
        log_text="",
        layout_text="\f".join(pages),
        threshold=4.0,
    )

    issues = {issue["code"]: issue for issue in result["issues"]}
    assert issues["rendered_main_body_underfilled"]["action"] == "expand_evidence_content"
    assert issues["rendered_main_body_underfilled"]["page"] == 6


def test_deterministic_review_flags_forced_break_before_conclusion() -> None:
    result = _deterministic_assessment(
        tex_text="\\section{Analysis}\nEvidence.\n\\clearpage\n\\section{Conclusion}\nDone.",
        log_text="",
        layout_text="\f".join(
            [
                "Title",
                "Related Work",
                "Method",
                "Experimental Setup\nFigure 1: overview",
                "Main Results\nTable 1: main results",
                "Analysis\nTable 2: supporting analyses",
                "Failure Cases\nFigure 2: profile",
                "Conclusion\nLimitations and Ethical Considerations",
                "References\n[1] Example",
            ]
        ),
        threshold=4.0,
    )

    issues = {issue["code"]: issue for issue in result["issues"]}
    assert issues["forced_page_break_before_conclusion"]["action"] == "rebalance_columns"


def test_png_blank_detector_distinguishes_empty_renderer_pages(tmp_path: Path) -> None:
    blank = tmp_path / "blank.png"
    nonblank = tmp_path / "nonblank.png"
    _write_rgb_png(blank, width=2, height=2, pixels=[(255, 255, 255)] * 4)
    _write_rgb_png(
        nonblank,
        width=2,
        height=2,
        pixels=[(255, 255, 255), (0, 0, 0), (255, 255, 255), (255, 255, 255)],
    )

    assert _png_is_nearly_blank(blank)
    assert not _png_is_nearly_blank(nonblank)


def _write_rgb_png(path: Path, *, width: int, height: int, pixels: list[tuple[int, int, int]]) -> None:
    rows = bytearray()
    for row in range(height):
        rows.append(0)
        for red, green, blue in pixels[row * width : (row + 1) * width]:
            rows.extend((red, green, blue))
    payload = zlib.compress(bytes(rows))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", payload)
        + _png_chunk(b"IEND", b"")
    )


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", crc)
