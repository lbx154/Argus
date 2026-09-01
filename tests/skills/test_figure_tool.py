from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from argus_skill.verticals.research import figure_tool

_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def test_render_paper_figure_prompt_uses_figure_studio_template() -> None:
    prompt = figure_tool.render_paper_figure_prompt(figure_title="SkillCycle")

    assert "Prompt template: argus-image2-paper-prompt-v2" in prompt
    assert "Prompt source: paper-framework-figure-studio-pro-v3.2.0" in prompt
    assert "SkillCycle" in prompt
    assert "General style:" in prompt
    assert "Pinned content that must appear exactly:" in prompt
    assert "Layout variant:" in prompt
    assert "Negative prompt / Avoid:" in prompt


def test_render_paper_figure_prompt_with_free_content() -> None:
    content = (
        '- Title: "PairScorer Pipeline"\n'
        '- Show: "Context+Candidate Pairs" -> "BoW Encoder" -> "Candidate Ranking" -> "Auxiliary Op Head" -> "Joint Prediction".\n'
        '- Operation types: "CLICK", "SELECT", "TYPE", "HOVER".\n'
        '- Baselines: "keyword overlap", "random", "no_skill".'
    )
    prompt = figure_tool.render_paper_figure_prompt(
        figure_title="PairScorer Pipeline",
        content=content,
        layout_variant="17 nested containers: big containers for Offline and Online; nested subcards inside.",
    )
    assert "PairScorer Pipeline" in prompt
    assert "BoW Encoder" in prompt
    assert "Auxiliary Op Head" in prompt
    assert "keyword overlap" in prompt
    assert "nested containers" in prompt
    # Should NOT contain legacy generic labels
    assert "Literature-grounded inputs" not in prompt
    assert "Reusable agent skill loop" not in prompt
    # Should contain research.md features
    assert "Aspect ratio:" in prompt
    assert "1536x1024 landscape" in prompt
    assert "清晰、克制" in prompt  # Chinese style intent


def test_render_paper_figure_prompt_legacy_compat() -> None:
    prompt = figure_tool.render_paper_figure_prompt(
        figure_title="TestMethod",
        input_label="Raw Data",
        mechanism_label="Encoder",
        output_label="Predictions",
        benefit_label="Higher F1",
    )
    assert '"Raw Data"' in prompt
    assert '"Encoder"' in prompt


def test_prompt_sha256_matches_raw_file_bytes(tmp_path: Path) -> None:
    """Prompt SHA-256 in manifest/sidecar must match raw file bytes on disk.

    This is the bug that caused the infinite regeneration loop: the tool
    used stripped-text hash but the validator used raw-file-bytes hash,
    so they never matched.
    """
    result = figure_tool.write_paper_figure_prompt(
        tmp_path / "test.prompt.txt",
        figure_title="HashTest",
        content='- Title: "HashTest"\n- Show: "A" -> "B".',
        force=True,
    )
    # The SHA in the result must match raw file bytes
    raw_bytes = (tmp_path / "test.prompt.txt").read_bytes()
    raw_hash = hashlib.sha256(raw_bytes).hexdigest()
    assert result["prompt_sha256"] == raw_hash, (
        f"prompt_sha256 must match raw file bytes! "
        f"got {result['prompt_sha256'][:16]}... "
        f"expected {raw_hash[:16]}..."
    )


def test_render_paper_figure_prompt_custom_aspect_ratio() -> None:
    prompt = figure_tool.render_paper_figure_prompt(
        figure_title="Tall Diagram",
        content='- Title: "Tall Diagram"\n- Show: "A" -> "B" -> "C".',
        aspect_ratio="1024x1536 portrait",
    )
    assert "1024x1536 portrait" in prompt
    assert "1536x1024" not in prompt.split("Aspect ratio:")[1].split("\n")[0]


def test_sync_paper_metadata_writes_manifest_and_provenance(tmp_path: Path) -> None:
    figures = tmp_path / "paper" / "figures"
    figures.mkdir(parents=True)
    prompt_path = figures / "method.prompt.txt"
    prompt = figure_tool.render_paper_figure_prompt(figure_title="SkillCycle").strip()
    prompt_path.write_text(prompt + "\n", encoding="utf-8")
    output_path = figures / "method.png"
    output_path.write_bytes(_PNG_BYTES)
    info = figure_tool.inspect_image(output_path)
    prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    sidecar_path = output_path.with_suffix(output_path.suffix + ".json")
    sidecar_path.write_text(
        json.dumps(
            {
                "model": "gpt-image-2",
                "created_at_unix": 1700000000,
                "prompt": prompt,
                "prompt_path": "paper/figures/method.prompt.txt",
                "prompt_sha256": prompt_sha,
                "output_path": "paper/figures/method.png",
                "output_sha256": info["sha256"],
                "requested_size": "1536x1024",
                "image": info,
                "api": {"endpoint": "/images/generations"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    review_path = output_path.with_suffix(output_path.suffix + ".review.json")
    review_path.write_text(
        json.dumps(
            {
                "image": info,
                "model": "gpt-5.4",
                "endpoint": "/responses",
                "review_contract": figure_tool.PAPER_FIGURE_STUDIO_SOURCE_ID,
                "review": "score_1_to_5: 5\nkeep_or_regenerate: keep",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    entry = figure_tool.sync_paper_metadata(
        project_root=tmp_path,
        image=Path("paper/figures/method.png"),
        prompt_file=Path("paper/figures/method.prompt.txt"),
        figure_id="method-overview",
        figure_type="method",
    )

    provenance = json.loads(
        output_path.with_suffix(output_path.suffix + ".provenance.json").read_text(
            encoding="utf-8"
        )
    )
    manifest = json.loads((figures / "IMAGE2_FIGURES.json").read_text(encoding="utf-8"))
    unified = json.loads(
        (figures / "FIGURE_PROVENANCE.json").read_text(encoding="utf-8")
    )
    manifest_entry = manifest["figures"][0]
    unified_entry = unified["figures"][0]
    assert entry["prompt_template_id"] == "argus-image2-paper-prompt-v2"
    assert entry["figure_studio_source"] == "paper-framework-figure-studio-pro-v3.2.0"
    assert manifest_entry["output_sha256"] == info["sha256"]
    assert provenance["output_sha256"] == info["sha256"]
    assert unified_entry["renderer"] == "image2"
    assert unified_entry["output_sha256"] == info["sha256"]
    assert (figures / "method.png.inspect.json").exists()


def test_sync_paper_metadata_accepts_raw_file_prompt_hash_with_stripped_sidecar_prompt(
    tmp_path: Path,
) -> None:
    figures = tmp_path / "paper" / "figures"
    figures.mkdir(parents=True)
    prompt_path = figures / "method.prompt.txt"
    prompt = figure_tool.render_paper_figure_prompt(figure_title="SkillCycle").strip()
    prompt_path.write_text(prompt + "\n", encoding="utf-8")
    output_path = figures / "method.png"
    output_path.write_bytes(_PNG_BYTES)
    info = figure_tool.inspect_image(output_path)
    prompt_sha = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
    output_path.with_suffix(output_path.suffix + ".json").write_text(
        json.dumps(
            {
                "model": "gpt-image-2",
                "created_at_unix": 1700000000,
                "prompt": prompt,
                "prompt_path": "../stale.prompt.txt",
                "prompt_sha256": prompt_sha,
                "output_path": "paper/figures/method.png",
                "output_sha256": info["sha256"],
                "requested_size": "1536x1024",
                "image": info,
                "api": {"endpoint": "/images/generations"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output_path.with_suffix(output_path.suffix + ".review.json").write_text(
        json.dumps(
            {
                "image": info,
                "model": "gpt-5.4",
                "endpoint": "/responses",
                "review_contract": figure_tool.PAPER_FIGURE_STUDIO_SOURCE_ID,
                "review": "score_1_to_5: 5\nkeep_or_regenerate: keep",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    entry = figure_tool.sync_paper_metadata(
        project_root=tmp_path,
        image=Path("paper/figures/method.png"),
        figure_id="method-overview",
        figure_type="method",
    )

    assert entry["prompt_sha256"] == prompt_sha
    assert entry["prompt_path"] == "paper/figures/method.prompt.txt"


def _seed_frozen_paper_context(root: Path) -> dict[str, Any]:
    (root / "research").mkdir(parents=True, exist_ok=True)
    (root / "research" / "RESEARCH_BRIEF.md").write_text(
        "# Brief\n\nStable research thesis.\n",
        encoding="utf-8",
    )
    style = root / "paper" / "style_ref"
    style.mkdir(parents=True, exist_ok=True)
    (style / "PAPER_STRUCTURE_BLUEPRINT.md").write_text(
        "# Blueprint\n\nFigure 1 explains the frozen mechanism.\n",
        encoding="utf-8",
    )
    (root / "paper" / "CLAIM_GRAPH.json").write_text(
        json.dumps({"claims": [{"id": "c1", "claim": "Mechanism improves X"}]}),
        encoding="utf-8",
    )
    return figure_tool.freeze_paper_figure_context(project_root=root)


def test_freeze_accepts_canonical_claim_evidence_without_removing_blueprint(
    tmp_path: Path,
) -> None:
    (tmp_path / "research").mkdir(parents=True)
    (tmp_path / "research" / "RESEARCH_BRIEF.md").write_text(
        "# Brief\n\nStable thesis.\n",
        encoding="utf-8",
    )
    (tmp_path / "research" / "NARRATIVE_REPORT.md").write_text(
        "# Narrative\n\nThe evidence supports the bounded claim.\n",
        encoding="utf-8",
    )
    style = tmp_path / "paper" / "style_ref"
    style.mkdir(parents=True)
    (style / "PAPER_STRUCTURE_BLUEPRINT.md").write_text(
        "# Blueprint\n\nFigure 1 explains the method and result.\n",
        encoding="utf-8",
    )
    (tmp_path / "paper" / "claims_to_evidence.tsv").write_text(
        "claim_id\tevidence\nc1\tresult.json\n",
        encoding="utf-8",
    )

    frozen = figure_tool.freeze_paper_figure_context(project_root=tmp_path)
    paths = {row["path"] for row in frozen["inputs"]}

    assert "paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md" in paths
    assert "paper/claims_to_evidence.tsv" in paths
    assert "research/NARRATIVE_REPORT.md" in paths


def _sync_reviewed_candidate(root: Path, index: int) -> dict[str, Any]:
    figures = root / "paper" / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    prompt_path = figures / f"method-{index}.prompt.txt"
    prompt = figure_tool.render_paper_figure_prompt(
        figure_title=f"Method Candidate {index}",
        layout_variant=f"variant {index}",
    ).strip()
    prompt_path.write_text(prompt + "\n", encoding="utf-8")
    output_path = figures / f"method-{index}.png"
    output_path.write_bytes(_PNG_BYTES + bytes([index]))
    info = figure_tool.inspect_image(output_path)
    prompt_sha = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
    output_path.with_suffix(output_path.suffix + ".json").write_text(
        json.dumps(
            {
                "model": "gpt-image-2",
                "created_at_unix": 1700000000 + index,
                "prompt": prompt,
                "prompt_path": f"paper/figures/{prompt_path.name}",
                "prompt_sha256": prompt_sha,
                "output_path": f"paper/figures/{output_path.name}",
                "output_sha256": info["sha256"],
                "requested_size": "1536x1024",
                "image": info,
                "api": {"endpoint": "/images/generations"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output_path.with_suffix(output_path.suffix + ".review.json").write_text(
        json.dumps(
            {
                "image": info,
                "model": "gpt-5.4",
                "endpoint": "/responses",
                "review_contract": figure_tool.PAPER_FIGURE_STUDIO_SOURCE_ID,
                "review": "score_1_to_5: 5\nkeep_or_regenerate: keep",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return figure_tool.sync_paper_metadata(
        project_root=root,
        image=Path(f"paper/figures/{output_path.name}"),
        prompt_file=Path(f"paper/figures/{prompt_path.name}"),
        figure_id=f"method-candidate-{index}",
        figure_type="method",
    )


def test_sync_preflights_canonical_manifest_before_legacy_update(
    tmp_path: Path,
) -> None:
    _seed_frozen_paper_context(tmp_path)
    figures = tmp_path / "paper" / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    canonical = figures / "FIGURE_PROVENANCE.json"
    canonical.write_text("{bad json", encoding="utf-8")

    with pytest.raises(figure_tool.ImageToolError, match="canonical figure provenance"):
        _sync_reviewed_candidate(tmp_path, 1)

    assert not (figures / "IMAGE2_FIGURES.json").exists()
    assert canonical.read_text(encoding="utf-8") == "{bad json"


def test_sync_rolls_back_legacy_manifest_when_canonical_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_frozen_paper_context(tmp_path)

    def fail_registration(**_kwargs):
        raise OSError("simulated canonical write failure")

    monkeypatch.setattr(
        "argus_skill.verticals.research.figure_provenance.register_figure",
        fail_registration,
    )
    with pytest.raises(OSError, match="simulated canonical write failure"):
        _sync_reviewed_candidate(tmp_path, 1)

    figures = tmp_path / "paper" / "figures"
    assert not (figures / "IMAGE2_FIGURES.json").exists()
    assert not (figures / "FIGURE_PROVENANCE.json").exists()


def test_reviewed_candidate_cache_reuses_frozen_context(
    tmp_path: Path,
    capsys,
) -> None:
    frozen = _seed_frozen_paper_context(tmp_path)
    for index in range(6):
        entry = _sync_reviewed_candidate(tmp_path, index)
    assert entry["candidate_cache_reusable"] is True
    assert entry["candidate_cache_passed_count"] == 6

    status = figure_tool.paper_figure_cache_status(
        project_root=tmp_path,
        figure_type="method",
    )
    assert status["context_sha256"] == frozen["context_sha256"]
    assert status["reusable"] is True
    assert status["passed_candidates"] == 6
    assert all(
        row["review_contract"] == figure_tool.PAPER_FIGURE_STUDIO_SOURCE_ID
        for row in status["candidates"]
    )

    main_tex = tmp_path / "paper" / "main.tex"
    main_tex.write_text("minor prose v1", encoding="utf-8")
    main_tex.write_text("minor prose v2", encoding="utf-8")
    assert figure_tool.paper_figure_cache_status(
        project_root=tmp_path,
        figure_type="method",
    )["reusable"] is True

    prompt_out = tmp_path / "paper" / "figures" / "should-not-exist.prompt.txt"
    rc = figure_tool.main(
        [
            "paper-prompt",
            "--project-root",
            str(tmp_path),
            "--figure-type",
            "method",
            "--out",
            str(prompt_out),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["cache_hit"] is True
    assert not prompt_out.exists()


def test_candidate_cache_requires_current_review_contract(tmp_path: Path) -> None:
    _seed_frozen_paper_context(tmp_path)
    _sync_reviewed_candidate(tmp_path, 0)
    cache = tmp_path / figure_tool.PAPER_FIGURE_CANDIDATE_CACHE_PATH
    payload = json.loads(cache.read_text(encoding="utf-8"))
    payload["candidates"][0].pop("review_contract")
    cache.write_text(json.dumps(payload), encoding="utf-8")

    status = figure_tool.paper_figure_cache_status(
        project_root=tmp_path,
        figure_type="method",
    )

    assert status["reusable"] is False
    assert status["passed_candidates"] == 0


def test_candidate_cache_invalidates_only_on_frozen_input_change(
    tmp_path: Path,
) -> None:
    _seed_frozen_paper_context(tmp_path)
    for index in range(6):
        _sync_reviewed_candidate(tmp_path, index)
    claim_graph = tmp_path / "paper" / "CLAIM_GRAPH.json"
    claim_graph.write_text(
        json.dumps({"claims": [{"id": "c2", "claim": "Changed evidence"}]}),
        encoding="utf-8",
    )
    status = figure_tool.paper_figure_cache_status(
        project_root=tmp_path,
        figure_type="method",
    )
    assert status["reusable"] is False
    assert status["reason"] == "evidence_or_structure_changed"


def test_paper_figure_prompt_requires_semantic_geometry() -> None:
    template = figure_tool.PAPER_FIGURE_PROMPT_TEMPLATE
    normalized = " ".join(template.split())
    assert "Semantic geometry:" in template
    assert "Figma tokens" not in template
    assert "Draw exactly the declared nodes and edges" in template
    assert "Every arrowhead points to its named target" in template
    assert "Connectors meet explicit node boundaries" in template
    assert "No shaft or arrowhead enters a node fill" in normalized
    assert "No node, label, legend, callout, or panel overlaps another" in normalized
    assert "rectangles for processes, diamonds for decisions" in template


def test_review_prompt_requires_submission_ready_geometry() -> None:
    no_rubric = figure_tool._review_prompt(original_prompt="a diagram", rubric="")
    assert "connector penetration" in no_rubric
    assert "node-boundary termination" in no_rubric
    assert "non-overlapping, unclipped" in no_rubric
    assert "semantically correct and submission-ready" in no_rubric

    rubric_text = (
        "Output a JSON object with fields: keep_or_regenerate, confirmed_labels, "
        "findings, prohibited_content_present."
    )
    with_rubric = figure_tool._review_prompt(original_prompt="a diagram", rubric=rubric_text)
    assert "connector penetration" in with_rubric
    assert "element overlap" in with_rubric
    assert "unreadable final-size type" in with_rubric


def test_review_prompt_without_rubric_uses_generic_schema() -> None:
    # When no rubric is supplied, callers still receive the generic structured
    # review schema plus the canonical publication-geometry requirements.
    prompt = figure_tool._review_prompt(original_prompt="a diagram", rubric="")
    assert "score_1_to_5" in prompt
    assert "Return JSON with:" in prompt
    assert "communicates" in prompt


def test_review_prompt_with_rubric_is_rubric_authoritative() -> None:
    # When a real rubric is supplied it becomes authoritative: the prompt must
    # not force the generic score_1_to_5 schema (which would swamp the rubric's
    # requested fields such as confirmed_labels), and it must tell the model to
    # emit every field the rubric requests plus keep_or_regenerate.
    rubric = (
        "Output a JSON object with fields: keep_or_regenerate, confirmed_labels, "
        "findings, prohibited_content_present."
    )
    prompt = figure_tool._review_prompt(original_prompt="a diagram", rubric=rubric)
    assert "AUTHORITATIVE" in prompt
    assert "keep_or_regenerate" in prompt
    assert "score_1_to_5" not in prompt
    # the caller's rubric text is passed through verbatim
    assert rubric in prompt


def test_review_image_threads_rubric_into_authoritative_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # End-to-end: a rubric passed to figure_tool.review_image reaches the
    # model request as an authoritative instruction, not buried under the
    # generic schema, via the generic tools.image_api.review_image call.
    image = tmp_path / "figure.png"
    image.write_bytes(_PNG_BYTES)
    captured: dict[str, Any] = {}

    def fake_urlopen(req: Any, timeout: float) -> Any:
        captured["body"] = json.loads(req.data.decode("utf-8"))

        class _FakeResponse:
            def __enter__(self) -> "_FakeResponse":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps({"output_text": '{"keep_or_regenerate": "keep"}'}).encode(
                    "utf-8"
                )

        return _FakeResponse()

    monkeypatch.setattr("argus_skill.tools.image_api._urlopen", fake_urlopen)
    from argus_skill.tools.capability_vault import ModelApiGrant, save_model_api_grant

    vault = tmp_path / "vault.json"
    save_model_api_grant(
        ModelApiGrant(
            api_key="dummy-key",
            base_url="https://example.invalid/openai/v1/",
            image_model="gpt-image-2",
            image_review_model="gpt-5.4",
            vault_path=vault,
        )
    )
    env = {"ARGUS_SKILL_CAPABILITY_VAULT": str(vault)}

    result = figure_tool.review_image(
        image=image,
        prompt="hierarchy diagram",
        rubric="Output JSON with keep_or_regenerate and confirmed_labels.",
        out=tmp_path / "review.json",
        env=env,
    )
    sent_text = captured["body"]["input"][0]["content"][0]["text"]
    assert "AUTHORITATIVE" in sent_text
    assert "confirmed_labels" in sent_text
    assert "score_1_to_5" not in sent_text
    # the paper wrapper preserves the historical output shape, including the
    # "rubric" field the domain-neutral tools.image_api.review_image no
    # longer writes itself.
    assert result["rubric"] == "Output JSON with keep_or_regenerate and confirmed_labels."
    assert result["review_contract"] == figure_tool.PAPER_FIGURE_STUDIO_SOURCE_ID
    sidecar = json.loads((tmp_path / "review.json").read_text(encoding="utf-8"))
    assert sidecar["rubric"] == "Output JSON with keep_or_regenerate and confirmed_labels."
    assert sidecar["review_contract"] == figure_tool.PAPER_FIGURE_STUDIO_SOURCE_ID


def test_review_cli_builds_paper_prompt_and_calls_generic_reviewer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    image = tmp_path / "figure.png"
    image.write_bytes(_PNG_BYTES)
    captured: dict[str, Any] = {}

    def fake_urlopen(req: Any, timeout: float) -> Any:
        captured["body"] = json.loads(req.data.decode("utf-8"))

        class _FakeResponse:
            def __enter__(self) -> "_FakeResponse":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps({"output_text": "score_1_to_5: 5"}).encode("utf-8")

        return _FakeResponse()

    monkeypatch.setattr("argus_skill.tools.image_api._urlopen", fake_urlopen)
    from argus_skill.tools.capability_vault import ModelApiGrant, save_model_api_grant

    vault = tmp_path / "vault.json"
    save_model_api_grant(
        ModelApiGrant(
            api_key="dummy-key",
            base_url="https://example.invalid/openai/v1/",
            image_model="gpt-image-2",
            image_review_model="gpt-5.4",
            vault_path=vault,
        )
    )
    monkeypatch.setenv("ARGUS_SKILL_CAPABILITY_VAULT", str(vault))

    rc = figure_tool.main(
        [
            "review",
            "--image",
            str(image),
            "--prompt",
            "hierarchy diagram",
            "--out",
            str(tmp_path / "review.json"),
        ]
    )
    assert rc == 0
    sent_text = captured["body"]["input"][0]["content"][0]["text"]
    # figure_tool's CLI still builds the paper-oriented review prompt, unlike
    # the domain-neutral tools.image_api CLI.
    assert "academic paper figure" in sent_text
    payload = json.loads(capsys.readouterr().out)
    assert payload["review"] == "score_1_to_5: 5"
