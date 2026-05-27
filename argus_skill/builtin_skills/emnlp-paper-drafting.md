---
name: EMNLP Paper Drafting
description: Draft an EMNLP or ACL-style LaTeX paper from local research artifacts, figures, and claims-evidence tables while preserving anti-fabrication discipline.
category: paper-writing
version: 1
scientist_model: gpt-5.4
created_at: 2026-05-23T00:00:00+00:00
---

## Title
EMNLP Paper Drafting

## Description
Write a paper draft from local evidence. This adapts ARIS paper-writing/paper-plan/paper-write into one argus-skill playbook: plan the story, scaffold LaTeX, insert figures/tables, and keep claims tied to artifacts.

## When to use
- The operator asks to write a paper, EMNLP paper, ACL paper, arXiv draft, or research manuscript.
- `research/`, `experiments/`, `paper/artifacts/`, or `paper/RESULTS_REPORT.md` contain evidence.
- The task is to turn an agent research project into a paper draft, not merely a README.

## When NOT to use
- No experiment evidence exists and the operator expects new empirical claims; plan or run experiments first.
- The task is only to polish an existing complete paper; use a review/revision skill.
- The operator asks for a survey-only document without experiments; create a narrative report, not an empirical paper.

## How to solve
1. Set the venue contract before writing:
   - Target `EMNLP/ACL long paper` by default unless the operator explicitly asks for a short paper.
   - Use the official ACL style files, not a generic `article` class. Prefer:
     `git clone --depth 1 https://github.com/acl-org/acl-style-files paper/acl-style-files`
     and use `acl.sty` / `acl_latex.tex` as the template source.
   - Record the exact template source and date in `paper/TEMPLATE_SOURCE.md`.
   - Use review/anonymized mode for drafts. Do not add real author names unless the operator explicitly asks.
   - Page target for a long-paper draft: aim for **7.5--8 main-content pages excluding references and appendix**. Do not stop at a 3--5 page note unless the evidence is only pilot-scale; if evidence is pilot-scale, say it is a short/workshop-style draft instead of padding unsupported content. The rendered PDF should visibly fill the body budget: Conclusion should not appear before page 7, and References and any Appendix material must begin on page 9 or later. References or Appendix on page 8 are not a success condition; they usually mean the paper only has about seven pages of body. The total article length after the body is unlimited: never enforce a total-page maximum for References or Appendix. If the body is short, repair by adding or moving source-backed body content before Conclusion: literature-grounded Introduction/Related Work framing, benchmark/Method detail, or evidence-bearing Results/Analysis/Ablation/Failure Cases content according to the page budget. Limitations, Ethics, release notes, references, or appendices after Conclusion do not count as fixing an underfilled main body.
   - Write `paper/PAGE_BUDGET.md` before prose with planned page allocation. Use this reference budget as the default starting point, then justify any evidence-driven adjustments: Abstract 0.3 pages; Introduction 1 page; Related Work 0.5--0.8 pages; Method 1--1.5 pages; Experimental Setup 0.5--1 page; Main Results 1--1.5 pages; Analysis/Ablation 1 page; Failure Cases 0.3--0.5 pages; Conclusion 0.2 pages.
   - Treat the `research.md` submission preflight as a hard formatting contract, not a style suggestion: official ACL/EMNLP review template, anonymous author block (`Anonymous EMNLP Submission`), conclusion by the end of page 8, Limitations and Ethical Considerations present after the conclusion, References before Appendix, References/Appendix starting on page 9 or later, no total-page maximum after the body, and a complete reproducibility appendix.
   - Section-depth floor for a final long paper: abstract 160--220 words, Introduction at least 750 words and about one full page with cited prior-work/benchmark hooks, Method at least 650 words, and Experimental Setup at least 500 words. If these sections are shorter, the paper is not reader-facing EMNLP prose; expand with evidence-backed problem framing, literature gap, mechanism explanation, model/runtime details, benchmark sources, baselines, budgets, and failure analysis.
   - PDF preflight is blocking: no undefined references, no citation warnings, no `[?]` in rendered text, no `Overfull \hbox > 5pt`, no `\textbf{[PLACEHOLDER]}` strings, and no `% UNVERIFIED` entries in `refs.bib` unless the operator has explicitly accepted unresolved bibliography verification.
   - Bibliography depth and correctness are blocking for final readiness: target a real EMNLP-sized reference section with at least 35 verified BibTeX entries, at least 30 unique cited keys in the paper source, and at least two rendered References pages before the Appendix when PDF text extraction is available. Use ACL/EMNLP author-year citations; do not add `\setcitestyle{numbers,square}` or natbib `numbers` overrides. Every BibTeX entry must have verified author/editor/organization metadata, and starter citation keys must match the fetched title rather than an unrelated arXiv paper.
   - Figure/table preflight is blocking: every figure has a `\label{}` and is referenced in text, every table has a caption with a numerical headline, at least one figure or table appears on each of pages 4--7, and at least one paired-significance table is included when comparative binary outcomes are reported.
   - Body-float budget: keep body figures to <=5 total, use only one `figure*` full-width float for the single most important visual, reserve `[t]` for at most two critical body figures per page, and move qualitative/secondary figures to the appendix.
   - Table styling must follow the `research.md` tokens: `\footnotesize`, `\tabcolsep=3-4pt`, `\arraystretch=1.15`, light-gray header, soft peach "ours" row, alternating row tint for long tables, coral accent only for meaningful degradation, and bold winning values.
   - Conceptual figures must be adaptive/landscape page-width assets, preferably `1536x1024 or 1920x1080`; never keep square `1024x1024` method figures, tiny text, decorative gradients, photorealism, sketchy/weird fonts, or code identifiers such as snake_case labels in paper-facing visuals.

2. Establish style and benchmark references:
   - Invoke the Paper Exemplar PDF Learning skill before drafting prose. URL-only exemplars are not enough.
   - If web access is available, find at least two open-access EMNLP/ACL/Findings papers from ACL Anthology/arXiv: one recent EMNLP/ACL best/outstanding/award paper for top-conference format calibration, and one same-direction paper for method/evaluation structure.
   - Download each exemplar PDF into `paper/style_ref/exemplars/<slug>/paper.pdf`, extract UTF-8 text into `paper/style_ref/exemplars/<slug>/paper.txt` with `pdftotext`, `pypdf`, `pdfminer.six`, or equivalent, and record the PDF SHA-256.
   - Every project must have `paper/style_ref/EXEMPLAR.json` with `exemplar_schema_version: 2`, at least two exemplars, and for each exemplar: `title`, `url`, `venue`, `year`, `source_type`, `award_status` when applicable, `open_access: true`, `license`, `pdf_storage_policy`, `usage: "structural_style_only"`, `no_prose_copy: true`, `local_pdf`, `pdf_sha256`, `text_extract`, and `structural_profile`.
   - Write a thick `paper/style_ref/STYLE_PROFILE.md`, not a one-line note. It must cover abstract shape, section/page allocation, figure/table inventory, related-work shape, evaluation layout, formatting/layout lessons, writing lessons, transfer plan, and no-prose-copy policy.
   - Write `paper/style_ref/EXEMPLAR_SUITABILITY.json` before locking the primary exemplar. It must use `verdict: "PASS"`, name a `primary_exemplar` slug that appears in `EXEMPLAR.json`, set `no_prose_copy_attestation: true`, and score task type, method family, experiment shape, figure/table density, related-work shape, and page rhythm. If no exemplar fits, fetch a better one instead of drafting from an unsuitable famous paper.
   - Write `paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md` before body prose. It must turn the downloaded exemplars into this paper's section order, page budget, paragraph roles, figure/table plan, related-work grouping, evaluation sequence, and local evidence mapping. Start from the primary exemplar's skeleton and page rhythm directly, then allow only evidence-justified title changes, section renames, merges, or splits. Use the reference page budget from `paper/PAGE_BUDGET.md` as an anchor, then adapt section lengths only when the local evidence and exemplar structure justify it. Use this blueprint as the paper organizer instead of improvising filler paragraphs.
   - After the final body draft exists, write `paper/style_ref/STRUCTURE_CONFORMANCE.md` and `paper/style_ref/STRUCTURE_CONFORMANCE.json` from the actual `paper/main.tex` section order. The JSON must use `conformance_schema_version: 1`, `verdict: "PASS"`, `no_prose_copy_attestation: true`, at least two `exemplar_lessons`, and `section_mappings` for every top-level section before references/appendix.
   - Never copy prose, claims, examples, terminology, figure design, or bibliography text from the style reference. Use it only as a structural scaffold.
   - Record source URLs and BibTeX/metadata in `paper/style_ref/SOURCES.md`.
   - Run `python -m argus_skill.skills.pipeline_contracts validate-exemplar --project-root .` before writing the paper body. If web is unavailable, create `paper/style_ref/TODO.md` listing the missing style-reference fetch and continue only as a blocked draft; do not mark the draft or submission assurance ready until `validate-exemplar` passes, including the structure-blueprint check.

   Standard starter citation targets for memory / agent-skills / hallucination papers (not a complete bibliography). Use this list only when the project topic matches those families; unrelated domains need their own literature-derived targets:
   - **Tool-use and agent loops:** `yao2023react` (ReAct), `shinn2023reflexion` (Reflexion), `madaan2023selfrefine` (SELF-REFINE), `schick2023toolformer` (Toolformer), `qin2023toolllm` (ToolLLM), `li2023apibank` (API-Bank), `patil2023gorilla` (Gorilla), `shen2023hugginggpt` (HuggingGPT), `karpas2022mrkl` (MRKL Systems).
   - **Memory, skills, and long-horizon agents:** `wang2024voyager` (Voyager), `zhao2024expel` (ExpeL), `packer2023memgpt` (MemGPT), `park2023generativeagents` (Generative Agents), `xu2025amem` (A-Mem), `zhong2024memorybank` (MemoryBank), `wang2023longmem` (LongMem).
   - **Self-evolution, process supervision, and skill learning:** `qi2024webrl` (WebRL), `li2025webevolver` (WebEvolver), `wang2025mobileagente` (Mobile-Agent-E), `tang2025sage` (SAGE), `zhang2025skillrl` (SkillRL), `lightman2023letsverify` (Let's Verify Step by Step), `zelikman2022star` (STaR).
   - **Evaluation, hallucination, and multi-agent surveys:** `zheng2023judging` (LLM-as-a-judge / MT-Bench), `ji2023survey` (hallucination survey), `huang2025hallucination` (hallucination survey), `guo2024llmmas` (LLM multi-agent survey), `manakul2023selfcheckgpt` (SelfCheckGPT), `lin2022truthfulqa` (TruthfulQA).
   - **Agent benchmarks:** `liu2023agentbench` (AgentBench), `zhou2023webarena` (WebArena), `mialon2023gaia` (GAIA), `maharana2024locomo` (LoCoMo), `jimenez2024swebench` (SWE-bench), `shridhar2020alfworld` (ALFWorld).
   - Treat these as search targets only. Fetch verified BibTeX from Semantic Scholar, arXiv, CrossRef, ACL Anthology, DBLP, or official project pages; never write BibTeX from memory. Add topic-specific EMNLP/ACL papers and benchmark/source papers until the final paper has at least 35 verified entries and 30 unique cited keys.
   - Distribute citations by claim/topic/paragraph. Each related-work paragraph should cite the specific papers it discusses and end with the gap for this paper. Do not concentrate citations into one giant paragraph, one mega-sentence, a table caption, or a bibliography-only dump.
   - Keep a `research/LIT_MATRIX.tsv` or equivalent with topic, verified source, BibTeX key, claim supported, and intended paper section so reference placement is auditable instead of clustered at the end.

3. Build the claims-evidence matrix first:
   - Read `research/NARRATIVE_REPORT.md`, `research/CLAIMS_TO_TEST.md`, `paper/artifacts/claims_evidence.tsv`, and result reports.
   - Write `paper/CLAIM_GRAPH.json` before final prose. Every major paper claim must record `id`, `claim`, paper `section`, status (`supported`, `weak`, `rejected`, or `missing`), evidence sources, result artifact(s), figure/table labels, citations, and the fallback wording or experiment needed if evidence is weak.
   - Write `paper/EVIDENCE_GAPS.json` for weak/missing/rejected claims. Each gap must say whether to soften/remove the claim, run an ablation or supplemental experiment, add a robustness/error-analysis slice, or move the point to limitations.
   - Remove or soften claims that lack local evidence. Do not leave weak/rejected claim text verbatim in the main body.
   - Drafting and experimentation are allowed to interleave: after initial full experiments, start writing the paper skeleton and claim graph; if the writing process exposes a missing ablation, weak result, unclear negative control, or insufficient qualitative evidence, return to experiments/analysis, update the canonical artifacts, then regenerate the affected paper sections. Do not force a single fixed order where experiments end forever before paper writing begins.
   - For final EMNLP/ACL drafting, run `python -m argus_skill.skills.pipeline_contracts validate-full-scale-evidence --project-root .` before writing final results, analysis, abstract, draft-readiness, assurance, or submission prose. If it reports `missing_full_scale_experiment_run`, `incomplete_full_scale_experiment_run`, `missing_baseline_condition_run`, or `pilot_pdf_without_full_scale_evidence`, keep the draft explicitly pilot/blocked and do not polish smoke-only evidence into a final long paper.
   - Benchmark construction is not executed evidence. `benchmarks/full/tasks.jsonl`, benchmark manifests, and `status.json task_count` are not enough; raw `experiments/**` rows must show completed scored trials for each required baseline/method condition.
   - If `research/NARRATIVE_REPORT.md` is missing, stop and write it from existing evidence before drafting; do not let LaTeX generation become the first narrative synthesis step.
   - Read `paper/ARTIFACT_MANIFEST.json` and identify the canonical tables/JSON that feed the draft. If the manifest is missing or invalid, return to analysis before writing LaTeX.
   - Read `research/LITERATURE_GROUNDING.json`. If it lacks 10 recent high-quality papers, 3 classic anchors, or trend-source metadata, return to planning instead of fabricating a related-work section. Trend/news sources can motivate framing without paper/benchmark/code backing, but cannot by themselves support technical claims.
   - Read `research/IDEA_PROVENANCE.json` and `research/CODE_REUSE_PLAN.json`. If the idea was not selected from surveyed papers/benchmarks/code, or implementation provenance ignores reusable official paper/open-source code, return to planning instead of drafting a paper around an agent-invented idea.

4. Create the paper scaffold:
   - Prefer `paper/main.tex`, `paper/sections/*.tex`, `paper/figures/`, `paper/artifacts/`, and `paper/references.bib`.
   - Use the official ACL/EMNLP template. Only fall back to a minimal LaTeX article when the official style cannot be downloaded, and mark that as a blocking formatting TODO in `paper/PAPER_DRAFT_REPORT.md`.
   - Read `paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md` and instantiate its section/page plan. If it is missing, too thin, or not mapped to local evidence, return to Paper Exemplar PDF Learning instead of writing freehand LaTeX.
   - Treat the blueprint as a skeleton lock, not inspiration. The initial LaTeX outline should follow the primary exemplar's sequence of rhetorical roles closely; title/section wording may change, but every deviation from the exemplar-derived skeleton must be recorded in the blueprint or `STRUCTURE_CONFORMANCE.json`.
   - Do not add paper-facing top-level sections from memory. Any final section that differs from the blueprint/exemplar phase must have a paper-specific reason and local evidence source; otherwise it is filler and must be removed or merged.
   - Keep references separate from the main page count. Use real BibTeX from ACL Anthology/DBLP/CrossRef when possible; otherwise leave `[VERIFY_CITATION]` markers and mark the draft as not submission-ready. Do not stop with a token bibliography: expand related work until the final reference section is at least 35 verified entries / 30 unique cited keys and renders across at least two reference pages.
   - Put References before any Appendix material, matching ACL/EMNLP submission order. Do not hide appendix content before the bibliography to inflate the main page count.

5. Draft the outline before prose:
   - Start from `paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md`; each planned section should state its paragraph roles, evidence source, expected figure/table, and which exemplar structural lesson it follows. Do not borrow exemplar sentences.
   - Keep the structure flexible but accountable: exemplar roles can be merged, renamed, or split when the local thesis needs it, but every final `\section` before references/appendix must map to an exemplar phase, evidence source, and applied lesson in `STRUCTURE_CONFORMANCE.json`.
   - Title and abstract.
   - Introduction with concrete problem, cited prior-work/benchmark gap, contribution, and evidence preview. The gap and contribution must cite `research/IDEA_PROVENANCE.json`; do not present agent brainstorming as novelty. The rendered Introduction should read like a real first page, not a short project note: include at least two verified citation hooks before Related Work, a motivating failure/example, the method insight, the main quantitative preview, contribution roadmap, and scope.
   - Use a paper thesis before drafting: "X is better for Y in Z because W." The contribution sentence must be concrete: "We propose X. We show X improves Y by Z because W."
   - Abstract should be about five evidence-backed sentences and normally 160--220 words: problem, gap, method, evaluated model/benchmark mix, result, implication. Avoid generic openings such as "Large language models have achieved remarkable success" or "In recent years..."
   - The abstract is reader-facing prose, not a validator worksheet: do not mention Appendix Figure/Table, `\ref{}` layout artifacts, raw artifact paths, review gates, evidence spans, or internal provenance files. Do not open with the headline number before the problem/gap.
   - Related work with verified citations where possible. Organize it by method family, benchmark gap, or failure mode, not as a chronological list. Placeholders are allowed only in early drafts and must be listed in `paper/PAPER_DRAFT_REPORT.md`.
   - Place citations adjacent to the claim they support; do not write a paragraph of unsupported claims followed by a citation pile.
   - Method/system section describing the evaluated system/runtime in reader-facing terms. A reviewer must be able to identify the agent framework or benchmark harness used by the paper's method, the controller or skill/memory mechanism, the LLM/model identifiers used for evaluated agent runs, tool access, state/memory boundaries, and what happens during one task episode. For no-GPU final agent experiments, use and report the approved hosted route such as `gpt-5-mini` with decoding/settings/budget; if the evaluation loop is deterministic/no-external-model, downgrade it to a deterministic baseline or pilot instead of presenting it as final agent-system evidence. Do not write Argus, Codex engineer/reviewer routes, daemon handoff, academic-language/layout review, or image-generation infrastructure into the paper as method details unless the paper's actual research object is that infrastructure. Do not hide these basics only in comments, JSON manifests, or appendix logs.
   - Benchmark provenance section: benchmark name, source URL, version/date, license or access notes, task count, filtering/sampling rules, and why it is appropriate for EMNLP agent evaluation. Final long-paper evidence needs at least 240 scored main tasks/episodes; 50/60-task evidence is a pilot and must trigger a scale-up/public-validation plan instead of final-ready prose.
   - Experiment setup with tasks, baselines, metrics, budget, evaluated-system model identifiers/routes when applicable, decoding settings when controlled, task source/version/date, and stopping/resume rules.
   - Results with figures/tables and conservative interpretation.
   - Analysis/failure taxonomy and error examples.
   - Limitations and ethics/reproducibility.
   - Avoid improvised filler headings such as `Protocol Notes`, `Track Mechanics`, `Release Detail`, `Mechanics`, or `Notes`. unmapped sections are blockers. If a genuinely paper-specific section is needed, use a reader-facing title and write a deviation rationale in `STRUCTURE_CONFORMANCE.json`.

6. Fill toward the page target with evidence-bearing content, not fluff:
   - If the paper is short because the Method or Experimental Setup is under-explained, add a compact system/configuration table and readable mechanism prose before adding more result paragraphs. The table should include framework/runtime, model IDs, routes/roles, tools, benchmark source, baselines, metrics, budget, and artifact provenance; omit secrets and API keys.
   - Add a complete benchmark/source section rather than just saying "synthetic tasks".
   - Add a full related-work section grounded in the style reference.
   - Add ablation/error-analysis tables if raw artifacts support them.
   - Add qualitative examples only if they come from saved run rows.
   - If the draft remains below target because evidence is too thin, state that explicitly and create a follow-up experiment plan instead of padding.
   - The goal is a complete EMNLP long paper, not a pilot PDF: target 7.5--8 main-content pages with problem framing, related work, method, benchmark/data, experiments, analysis, limitations, and reproducibility all present; references and appendix begin on page 9 or later and have no total-page cap.

7. Write LaTeX with evidence discipline:
   - Every numeric claim must cite a generated table/figure or raw artifact path in a LaTeX comment.
   - For abstract numeric claims, put evidence mapping in `paper/artifacts/result_to_claim.tsv` or a comment adjacent to the abstract block, not inside the abstract environment; the rendered/source abstract should read like a normal EMNLP abstract.
   - Use comments like `% evidence: experiments/<run_id>/summary.tsv`.
   - Numeric tables and summary sentences in `paper/main.tex` must be generated from canonical artifacts rather than copied from memory. Do not leave stale values in LaTeX after changing `paper/artifacts/*.tsv` or `*.json`.
   - Add `paper/main.tex` and any submission copy such as `paper/submission/main.tex` to `paper/ARTIFACT_MANIFEST.json` as generated artifacts whose `sources` reach the canonical result tables.
   - Do not invent citations. Use placeholders when metadata is missing.
   - Distinguish pilot evidence from full benchmark evidence. Do not write a 50/60-task benchmark as a complete EMNLP result; full-paper claims require at least 240 scored main tasks/episodes for every required method/baseline condition.
   - Use human-readable paper labels in titles, abstract, captions, and tables. Avoid code-style monospace labels such as `handoff_and_finalize`, raw artifact paths, and snake_case protocol names in the body; translate them to readable labels and keep raw identifiers in comments, manifests, or appendices only when necessary.
   - Reader readability is a hard requirement, not polish. Every main section should tell the outside reviewer what object is being studied, why it matters, what system was run, what model/backend powered it, how the benchmark was executed, and what the numbers mean. A validator-passing paper that omits framework/model/setup basics is not submission-ready.
   - Keep claims calibrated: no SOTA, "novel", "significant", "robust", or broad generalization language unless the local artifacts and citations support it. Calibration should not turn the abstract into repeated defensive caveats; move detailed scope limits to limitations/discussion.
   - Every figure/table caption should state the evidence-backed takeaway, not only describe visual content.

8. Integrate figures and tables:
   - Include generated files from `paper/figures/` and `paper/artifacts/`.
   - Write `paper/FIGURE_TABLE_STYLE_GUIDE.json` before final layout iteration. It must list every body and appendix float with label, type, body-vs-appendix placement, target section, source artifact, style decision, readability check, and caption/takeaway plan. Use it to move verbose diagnostics out of the body instead of padding pages with ugly tables.
   - For Figure 1, teaser, overall, and the main conceptual/method/framework/system overview, use image-2 / codex-image2 and write `paper/figures/<name>.prompt.txt`, the raw image-tool sidecar, an inspect sidecar, a model-backed image review sidecar, a generation provenance sidecar, the generated raster image, and `paper/figures/IMAGE2_FIGURES.json`.
   - `IMAGE2_FIGURES.json` must include `sidecar_path`, `inspect_path`, `review_path`, `generation_provenance_path`, `prompt_path`, `output_path`, `output_sha256`, width, and height. The sidecar/review must show an Argus image/image_review route or `/images/generations`/vision endpoint evidence. Do not satisfy this by hand-writing `"generator": "codex-image2"` around a local PNG.
   - Include that image-2 raster `output_path` directly in `paper/main.tex`. **Do not draw this overview yourself.** Do not replace it with matplotlib/FancyBboxPatch output, a TikZ node graph, an Inkscape/manual vector redraw, a PIL/SVG/HTML canvas, a cleaned PDF derivative, a screenshot, or a generic raster mockup, and do not label a local PNG/JPEG as `codex-image2`. Do not crop, downsample, resave, or overwrite the generated raster after provenance is written; its SHA-256 and actual dimensions must match the prompt/provenance/inspect/review sidecars. If the overview is ugly or cramped, improve the image-2 prompt and regenerate/select/review image-2 attempts; never self-draw a replacement. Data plots should still be generated from data scripts, and secondary precise TikZ/pgfplots diagrams are allowed only when they do not substitute for the core overview.
   - Figure 1 prompts must be scaffolded, not improvised. The prompt file must include `General style`, `Style intent`, `Pinned content`, exact spelling instructions such as `SPELL EXACTLY`, a named `Layout variant`, `Negative prompt / Avoid`, and Figma cleanup tokens. Use the imported `research.md` recipe: clean dense Figma-like rounded cards, full-width two-column landscape (`1536x1024` or `1920x1080`), soft pastel fills, dark-gray 2px borders, warm white `#fbfaf7` background, short reader-facing labels, and no code identifiers/raw paths. Generate 6--20 image-2 layout variants by changing only the layout block, then keep the best reviewed raster; a single thin prompt such as "draw method overview" is a blocker even if the generated image exists.
   - The preferred layout variant menu is: central hero, horizontal swimlanes, sankey funnel, exploded entry, layered architecture stack, pipeline plus gallery, modular dashboard, radial hub-spoke, zigzag pipeline, dense research-poster, grayscale accent, color-coded phases, card deck, computation graph, dataflow with sidebars, timeline plus insets, nested containers, multi-panel A/B/C/D, light blueprint, and polished Figma wireframe.
   - The negative block must explicitly forbid tiny unreadable text, vertical character text, dense paragraphs, code snippets, raw artifact paths, excessive logos, watermarks, photorealism, stock photos, glassmorphism, heavy gradients/shadows, texture, decorative blobs, messy whiteboard style, large empty areas, overlapping cards, squashed labels, inconsistent terminology, and dashboard-like extra captions.
   - Conceptual raster figures must be landscape/adaptive and reviewable at page width. Do not request or keep `1024x1024` square method figures. Prefer an adaptive or wide aspect ratio, record actual width/height, and include an image review sidecar with score at least 4/5 and `keep_or_regenerate: keep`.
   - Keep method figures in clean academic/vector-like style with readable labels and no decorative clutter, tiny text, brand/logo walls, heavy shadows, gradients, or sketchy handwriting fonts.
   - Add captions that state exactly what data source the figure uses.
   - Tables must fit the ACL layout without visual overlap. Split wide tables, use human labels, adjust `tabcolsep=3-4pt`/`\footnotesize`/`\arraystretch=1.15` conservatively, or move verbose diagnostics to appendix. Do not rely on over-wide `table*` floats that interleave with body text.

9. Compile and check page budget:
   - Run `latexmk -pdf -interaction=nonstopmode paper/main.tex` when LaTeX is available.
   - If `latexmk` is unavailable, run `pdflatex` twice and capture the log.
   - Run `pdfinfo paper/main.pdf` when available and record total pages in `paper/PAGE_BUDGET.md`, but do not treat total pages as capped after References/Appendix begin; only the main/body page budget is capped at 8 pages.
   - If the main content is below the long-paper target, stop and classify the cause before editing prose: missing full-scale experiment matrix, missing baseline, missing ablation, missing robustness/public-validation slice, missing failure taxonomy, weak claim graph, or only layout/float imbalance. If evidence is missing or weak, invoke the benchmark runner / experiment plan and produce the missing runs or analyses first; do not pad with generic motivation, repeated limitations, margin tricks, or oversized floats. If evidence cannot support a long paper, mark `submission_quality_self_assessment: blocked` or `pilot` instead of `ready`.
   - Underfilled-body and shallow-section issue codes such as `abstract_too_short`, `introduction_too_short`, `method_section_too_short`, `experimental_setup_too_short`, `underlength_emnlp_paper`, `rendered_main_body_underfilled`, `references_before_full_body`, and `missing_midpaper_visual_pages` should update `paper/EVIDENCE_GAPS.json`, `paper/CLAIM_GRAPH.json`, and `paper/VALIDATION_PRIORITY_POLICY.json` with `content_sufficiency` routing. If evidence is missing, the repair mode is more experiments, ablation, robustness, or failure study before prose expansion. If evidence exists but the narrative is thin, expand source-backed Introduction/Related Work framing, benchmark/Method detail, Results/Analysis/Ablation interpretation, or failure taxonomy prose; do not add generic motivation or repeated caveats.
   - If LaTeX is unavailable, run a syntax sanity check by reading all referenced files and searching for obvious missing includes.
   - Save compile logs and do not claim a PDF exists unless it does.
   - Treat any `Overfull \hbox` warning above 5pt as a blocking format failure, matching `research.md`; it usually indicates table/text overflow. Fix the source and recompile before marking the draft ready.
   - Inspect the rendered PDF or `pdftotext -layout` output when available. Reject table/body overlap, interleaved floats, references mixed into appendix pages, unreadable tiny table text, or any page that looks like a validator-passing but non-reviewable draft.
   - Write `paper/ARTIFACT_FRESHNESS.json` after each regeneration wave. It must hash the current inputs for the claim graph, result tables, paper skeleton/blueprint, figure/table guide, `paper/main.tex`, compiled PDF, and review artifacts so stale paper prose cannot cite old results.

10. Run dedicated format preflight:
   - Invoke the EMNLP Format Preflight skill after the final compile and before academic-language or layout scoring.
   - Run `python -m argus_skill.skills.pipeline_contracts validate-research-md-format --project-root .` and fix every issue; this command owns the full `research.md` contract for anonymity, section order, `[?]`, `Overfull \hbox > 5pt`, placeholders, `% UNVERIFIED`, bibliography depth, figure labels/refs, figure counts, numerical table captions, paired significance, table styling, and reproducibility appendix.
   - Write `paper/FORMAT_PREFLIGHT.md` with page count, conclusion page, figure/table inventory, bibliography status, fixes applied, and the final validator result.

11. Set validation repair order before final review loops:
   - Write `paper/VALIDATION_PRIORITY_POLICY.json` with a stable priority order: freshness, experiment evidence, claim evidence, and content sufficiency first; exemplar/skeleton conformance next; figure/table and format/layout next; visual layout next; academic language after evidence/structure are stable; then minor manifest/readiness cleanup.
   - Route failures to the right repair mode: stale artifacts trigger regeneration; `missing_full_scale_experiment_run`, `missing_baseline_condition_run`, and `pilot_pdf_without_full_scale_evidence` trigger more benchmark runs; weak claims trigger extra experiments or claim softening; underfilled pages trigger evidence-backed analysis/ablation/failure study when evidence is thin, or source-backed Introduction/Related Work/Method expansion when evidence exists but the paper body is underwritten; structure drift triggers skeleton reset; ugly floats trigger figure/table redesign; repeated non-improving layout/prose edits trigger a skeleton/float reset rather than endless paragraph churn.
   - Prefer the official scaffold over hand-written JSON: run `python -m argus_skill.skills.pipeline_contracts write-validation-priority-policy --project-root .`, then edit only if a paper-specific route is truly needed. The policy must include all failure classes: `freshness`, `experiment_evidence`, `claim_graph`, `content_sufficiency`, `exemplar_suitability`, `exemplar_structure`, `figure_table_style`, `format_layout`, `layout_vision`, `academic_language`, and `artifact_manifest`.
   - After regenerating manuscript, figures, review JSON, or submission artifacts, run `python -m argus_skill.skills.pipeline_contracts refresh-manifest --project-root .` and `python -m argus_skill.skills.pipeline_contracts refresh-artifact-freshness --project-root .`; do not manually bump digests without regenerating from current inputs.

12. Run final academic-language review:
   - After the paper content is stable, run `python -m argus_skill.skills.academic_language_review --project-root . --review-mode model --write`.
   - This must write `paper/ACADEMIC_LANGUAGE_REVIEW.json`, `paper/ACADEMIC_LANGUAGE_REVIEW.md`, and `paper/ACADEMIC_LANGUAGE_REVIEW_history.jsonl`.
   - Treat `score_1_to_5 < 4`, `needs_revision: true`, any blocking issue, heuristic-only review, stale source hash, missing evidence span, failed required check, or active revision directive as a failed draft.
   - Apply the directives to rewrite the abstract/introduction, tighten the contribution sentence, calibrate claims, reorganize related work, add evidence sentences, replace hype language, add limitation scope, or remove filler. Rerun the tool until it passes or a hard blocker remains.

13. Run final layout-aesthetic review:
   - After the final compile, run `python -m argus_skill.skills.paper_layout_review --project-root . --review-mode vision --write`.
   - This must write `paper/LAYOUT_REVIEW.json`, `paper/LAYOUT_REVIEW.md`, rendered page snapshots under `paper/layout_review/pages/`, and a history entry in `paper/LAYOUT_REVIEW_history.jsonl`.
   - Treat `score_1_to_5 < 4`, `needs_revision: true`, any blocking issue, non-vision review, or stale PDF/page hash as a failed draft. Do not self-report the score; use the tool output.
   - If the review fails for ugly layout, modify layout and content before handoff: split or move dense tables, shorten low-value prose, remove filler, regenerate or resize figures, rebalance columns, and replace code-like labels. Recompile and rerun layout review until it passes or a hard blocker remains.

14. Write `paper/PAPER_DRAFT_REPORT.md`:
   - Current draft path and PDF path if compiled.
   - Template source and whether official ACL style is active.
   - Page budget: target pages, actual pages, and whether the draft is long-paper, short-paper, or pilot-note scale.
   - Benchmark provenance: official benchmark vs synthetic pilot, source URLs, task counts, and caveats.
   - Style reference: source paper(s), structural profile path, and a statement that no prose was copied.
   - Claims kept, softened, removed.
   - Missing citations, missing figures, missing experiments.
   - Artifact-manifest status: whether `python -m argus_skill.skills.pipeline_contracts validate-manifest --project-root .` passes after drafting.
   - Next revision tasks for reviewer loop.
   - Also write `paper/PAPER_DRAFT_REPORT.json` with `target_venue: "EMNLP"`, `paper_scope: "long-paper"`, `main_content_pages`, `official_acl_template: true`, and `submission_quality_self_assessment: "ready"` only when the draft is truly a complete long paper. Use `pilot`, `not_ready`, or `blocked` instead of `ready` when evidence or formatting is incomplete.
   - If any display-context code label must remain, document it in `allowed_code_labels` with a human-readable rationale. This is an exception mechanism, not permission to leave snake_case labels throughout tables.
   - Write `paper/style_ref/STRUCTURE_CONFORMANCE.md` and `paper/style_ref/STRUCTURE_CONFORMANCE.json` after the final LaTeX section order is stable. Map each final section to `maps_to_exemplar_phase`, `evidence_sources`, `exemplar_lesson`, and any `deviation_rationale`. Keep significance/evidence tables with Results/Analysis or in the Appendix; do not strand them after Ethics before References.

15. Hand off to submission assurance:
   - Run `python -m argus_skill.skills.pipeline_contracts validate-grounding --project-root .`, `validate-idea-provenance`, `validate-code-reuse`, `validate-exemplar`, `validate-full-scale-evidence`, `validate-image2-figures`, `validate-paper-format`, `validate-research-md-format`, `validate-claim-graph`, `validate-figure-table-style`, `validate-validation-priority`, `validate-artifact-freshness`, `validate-paper-quality-contracts`, `validate-academic-language-review`, `validate-layout-review`, and `validate-paper-contract`; fix failures before handoff. `validate-paper-contract`/`validate-full-emnlp` check the final `STRUCTURE_CONFORMANCE` artifacts, so passing `validate-exemplar` alone is not enough after drafting.
   - If the draft is being claimed as final EMNLP-ready rather than a blocked draft, run `python -m argus_skill.skills.pipeline_contracts validate-full-emnlp --project-root .`; do not treat `validate-pipeline` alone as final readiness.
   - Run `python -m argus_skill.skills.pipeline_contracts refresh-manifest --project-root .` and then `validate-manifest`; fix drift before handoff.
   - Update `research/PIPELINE_STATE.json` with the draft artifact paths and draft scope (`long-paper`, `short-paper`, or `pilot-note`).
   - Do not mark the pipeline submission-ready from this skill. The Research Submission Assurance Gate must write `paper/SUBMISSION_ASSURANCE.md` and `paper/SUBMISSION_ASSURANCE.json` before any final readiness claim.

## Response shape
- Return artifact paths, compile status, and the strongest evidence-backed claim.
- Explicitly list any unsupported claims that were omitted or softened.
