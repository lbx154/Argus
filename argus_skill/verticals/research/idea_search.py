"""codex web-search as an ADDITIONAL candidate source for research ideation.

argus's research stage works in two phases: GENERATE candidates
(``idea-discovery`` -> ``research/IDEA_CANDIDATES.md``) then SELECT the feasible
one (``idea-creator`` ranks + pilots, ``novelty-check`` de-dupes,
``signal-derisk`` validates). This module adds ONE MORE candidate *source*: a
single codex call with native live web_search that surfaces literature-grounded
gaps and appends them to the candidate pool. The existing selection machinery is
untouched — it simply ranks over a richer pool.

Design rules:
  * SOURCE only — never selects, never rewrites existing candidates; it APPENDS
    under a provenance marker so ``idea-creator`` merges both sources.
  * The prompt bakes in the house standard: each candidate must propose a METHOD
    with a concrete, reproduced baseline it aims to beat, scoped to compute that
    realistically exists with the main experiment feasible in <=8h (see the
    ``15-research-ideation-standard`` operator directive). Pure diagnostic /
    probing / benchmark-only ideas are rejected at generation. Target venue and
    the exact resource ceiling stay operator-level (discovered / user-overridden
    downstream), so they are NOT hardcoded here.
  * Fail-open + run-once: any error returns 0 and never raises; a provenance
    marker prevents re-appending on later research rounds.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ...core.models import RunnerOptions
from ...core.run_gateway import run_exec as gateway_run_exec

log = logging.getLogger(__name__)

#: Provenance marker delimiting the codex-web-search block in IDEA_CANDIDATES.md.
SOURCE_MARKER = "<!-- source: codex-web-search -->"

_CANDIDATES_RELPATH = ("research", "IDEA_CANDIDATES.md")
_BRIEF_RELPATH = ("research", "RESEARCH_BRIEF.md")

#: Corpus-derived research-move menu — the 15 ideation patterns induced from
#: 1,947 ICLR/ICML/NeurIPS papers (ResearchStudio-Idea / IdeaSpark, arXiv
#: 2607.04439; MIT). Baked into the prompt so generation stays at
#: "move-applied-to-gap" rather than open brainstorming. The move is diagnostic
#: vocabulary — never the contribution claim itself. Full pattern + 31
#: sub-pattern tactical cards live under
#: ``builtin_skills/engineer/references/ideation/`` (read by ``idea-discovery``).
_RESEARCH_MOVES = (
    "1. Audit and Pivot an Assumption — relax or violate a load-bearing assumption\n"
    "2. Substitute the Operator or Representation — swap a costly operator/representation for a cheaper property-preserving surrogate\n"
    "3. Liberate a Fixed Generative Component — treat a conventionally-fixed part of a generative process as a design variable\n"
    "4. Design a Confound-Isolating Diagnostic — construct instances that hold a suspected confound fixed or varied\n"
    "5. Unify Heterogeneous Inputs into One Space — map heterogeneous inputs into one shared representation\n"
    "6. Reframe as a Solvable Object — reformulate the unsolved as a solvable object (game, program, equilibrium)\n"
    "7. Manufacture the Supervisory Signal — build self / weak / synthetic / comparative supervision\n"
    "8. Encode Structure by Construction — bake a symmetry / topology / forward process into the model\n"
    "9. Prove Equivalence to Unify — prove two procedures or objectives coincide, then exploit it\n"
    "10. Decompose for Differentiated Treatment — split a heterogeneous artifact and treat its parts unequally\n"
    "11. Decompose and Delegate to Solvers — hand an unreliable sub-problem to a sound external solver/oracle\n"
    "12. Relax Discrete Search to Continuous — relax a hand-designed discrete choice into continuous search\n"
    "13. Adapt by Conditioning, Not Retraining — adapt at inference via conditioning/steering, no retrain\n"
    "14. Characterize a Limit, Then Surpass It — characterize a method's limit, then exceed it\n"
    "15. Design a Property-Targeting Pretext Objective — inject a relational/geometric property via a pretext objective\n"
)

def _pattern_reference() -> str:
    """Compact move vocabulary for the one-shot live-search source.

    The full 15-pattern/31-sub-pattern cards remain available to the downstream
    ``idea-discovery`` skill. Reinjecting those ~20k characters into this call
    duplicates that later reasoning and materially increases every web-search
    turn's context. The compact menu preserves mechanism diversity while leaving
    detailed tactical refinement to candidate selection.
    """
    return "Research-move menu (15 corpus-derived patterns):\n" + _RESEARCH_MOVES


def _candidates_path(workdir: Any) -> Path:
    return Path(workdir).joinpath(*_CANDIDATES_RELPATH)


def _already_seeded(workdir: Any) -> bool:
    """True if a codex-web-search block was already appended (run-once guard)."""
    try:
        p = _candidates_path(workdir)
        return p.is_file() and SOURCE_MARKER in p.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001 — never let the guard raise
        return False


def _resolve_direction(workdir: Any, direction: str | None) -> str:
    """The broad research direction to search around: caller-provided objective,
    else the top of RESEARCH_BRIEF.md, else empty (caller decides to skip)."""
    if direction and direction.strip():
        return direction.strip()
    try:
        brief = Path(workdir).joinpath(*_BRIEF_RELPATH)
        if brief.is_file():
            text = brief.read_text(encoding="utf-8").strip()
            # first non-empty, non-heading paragraph is the framing
            for para in text.split("\n\n"):
                para = para.strip()
                if para and not para.startswith("#"):
                    return para[:1200]
    except Exception:  # noqa: BLE001
        pass
    return ""


def _build_prompt(direction: str, n: int) -> str:
    return (
        "You are a senior ML researcher doing candidate discovery for a STRONG "
        "paper that resolves an important question with a nontrivial contribution.\n"
        f"Research direction:\n{direction}\n\n"
        "Using LIVE web_search, find REAL recent papers (roughly the last 18 "
        "months) closely related to this direction on arXiv / Semantic Scholar, "
        "including the current strong methods / reported SOTA baselines.\n\n"
        "Construct each idea in THREE steps — reason first, then commit:\n"
        "STEP 1 — BOTTLENECK (grounded, not a topic): arrange the 3-5 closest "
        "retrieved methods into a refine/replace lineage (each node refines or "
        "replaces an earlier one). From the lineage name ONE concrete structural "
        "gap and classify it: an ADDITIVE gap (an unmet need at a leaf) OR a "
        "SUBTRACTIVE gap (a load-bearing assumption every method in the lineage "
        "inherits that you could remove — often the stronger move). Then run a "
        "REGRESSION CHECK: confirm your fix is NOT something an older ancestor "
        "already did. The gap must rest on what the retrieved papers actually "
        "show, not on model memory.\n"
        "STEP 2 — RESEARCH MOVE: from the corpus-derived ideation patterns below, "
        "pick the ONE pattern whose operational signature structurally closes the "
        "gap. The pattern is thinking vocabulary, never the contribution itself, "
        "and never a hard filter (a common pattern is fine if the delivery is "
        "substantive):\n\n"
        f"{_pattern_reference()}\n\n"
        "STEP 3 — INSTANTIATE: turn the chosen move applied to the specific gap "
        "into one concrete research contribution. Valid shapes include a method, "
        "system, theorem, diagnostic, characterization, evaluation, benchmark/data "
        "contribution, negative result, or boundary finding. Do not require every "
        "candidate to beat a baseline; require it to answer an important question "
        "against a strong reference. Apply an AMBITION GATE: the candidate needs a "
        "nontrivial technical core, verified originality, a genuine formal or causal "
        "foundation with derived predictions, and a field-level consequence. Require "
        "load-bearing mathematics to determine an algorithm, bound, impossibility "
        "result, scaling law, threshold, or quantitative prediction. For an Agent "
        "direction, devote at least one third of the candidates to independent "
        "foundation-first searches across relevant areas such as probability/learning "
        "theory, information theory, control/dynamical systems, causal inference, game "
        "theory, formal methods, or network/statistical physics. Physical concepts must "
        "map to measurable Agent variables and beat simpler explanations; reject "
        "decorative equations and analogy-only transfers.\n\n"
        f"Output EXACTLY {n} candidate ideas, each as a markdown block in this "
        "format (ids WS-1, WS-2, ...). Make the ideas DIVERSE: different gaps and "
        "different moves, not variants of one idea (include at least one "
        "SUBTRACTIVE-gap idea).\n\n"
        "## Candidate WS-1: <one line: the proposed method and what it beats>\n\n"
        "**Bottleneck**: <the concrete structural gap from STEP 1 and the strong "
        "prior work/baseline that leaves it open>\n\n"
        "**Lineage & gap type**: <the 3-5 method refine/replace chain; label the "
        "gap ADDITIVE or SUBTRACTIVE; one line for the regression check — which "
        "ancestor could already do this, and why yours differs>\n\n"
        "**Research move**: <the ONE pattern by number and name>\n\n"
        "**Contribution shape**: <method, system, theorem, diagnostic, "
        "characterization, evaluation, benchmark/data, negative result, or "
        "boundary finding>\n\n"
        "**Hard technical core**: <the nontrivial algorithm/system/formal "
        "mechanism; why a prompt, schema, wrapper, or scale-up is insufficient>\n\n"
        "**Formal or causal foundation**: <objects, assumptions, invariants, and "
        "the derived algorithm/bound/threshold/scaling law/falsifier; map any physical "
        "quantity to measurable Agent behavior; no decorative math>\n\n"
        "**Reference comparison + target**: <a strong published/standard "
        "reference, the public benchmark(s), and the outcome that would support "
        "or refute the claim>\n\n"
        "**Why it matters (thesis)**: <one sentence — the non-obvious insight or "
        "decision-relevant value>\n\n"
        "**Frontier significance**: <what general belief, design principle, or "
        "capability changes if the claim is true>\n\n"
        "**Grounding**: <cite 1-2 REAL papers you found via search, "
        "title + year + arxiv id; state what they did and the gap they leave>\n\n"
        "**Resource plan**: <compute/data/access needed vs what realistically "
        "exists; staged execution and the operator-approved budget>\n\n"
        "**Anticipated kill-argument**: <the strongest ~40-word rejection>\n\n"
        "Rules: cite ONLY papers you actually found via search (no fabricated "
        "ids); the bottleneck and regression check must trace to retrieved "
        "papers, not memory. The research move is diagnostic vocabulary, never "
        "the contribution claim itself. Every candidate MUST name a strong "
        "reference and a falsifiable outcome, but diagnostic, probing, benchmark, "
        "negative, and boundary contributions are allowed when valuable. Design "
        "each idea for resources that realistically exist or state a credible "
        "staged plan, but never let ease rescue an incremental, shallow, weakly "
        "grounded, or unimportant idea; honor operator limits without imposing an "
        "8h cutoff. Venue is "
        "decided elsewhere. Keep the whole answer under ~1100 words. Output ONLY "
        "the candidate blocks, nothing else."
    )


def _extract_message(result: Any) -> str:
    """Last agent message from a RunnerResult-shaped object (fail-open to '')."""
    try:
        if getattr(result, "exit_code", 1) != 0:
            return ""
        msgs = getattr(result, "agent_messages", None) or []
        return str(msgs[-1]) if msgs else ""
    except Exception:  # noqa: BLE001
        return ""


def augment_idea_candidates(
    runner: Any,
    workdir: Any,
    *,
    direction: str | None = None,
    model: str = "gpt-5.5",
    n: int = 6,
) -> int:
    """Run ONE codex web-search ideation call and APPEND its candidates to
    ``research/IDEA_CANDIDATES.md``. Returns the number of candidate blocks
    appended (0 on any skip/error). Never raises.

    Reuses the ``RunnerOptions.live_search`` flag (-> codex ``web_search="live"``)
    so the call does real live literature search. Run-once guarded by
    :data:`SOURCE_MARKER`.
    """
    try:
        if runner is None or not hasattr(runner, "run_exec"):
            return 0
        if _already_seeded(workdir):
            return 0
        resolved = _resolve_direction(workdir, direction)
        if not resolved:
            return 0

        # Several labs' models, asked separately and then set against each
        # other, beat one model asked once — when this box has them.
        from .idea_panel import run_panel

        panel = run_panel(
            Path(workdir).expanduser().resolve(),
            direction=resolved,
            proposal_prompt=_build_prompt(resolved, n),
        )
        if panel:
            path = _candidates_path(workdir)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(f"\n\n{SOURCE_MARKER}\n# Panel ideation\n{panel}")
            return panel.count("## Candidate ")

        log.info(
            "idea-search: running codex live web-search (model=%s, n=%d) for %r",
            model, n, resolved[:80],
        )
        result = gateway_run_exec(
            runner,
            prompt=_build_prompt(resolved, n),
            options=RunnerOptions(
                model=model,
                reasoning_effort="high",
                working_dir=str(Path(workdir).expanduser().resolve()),
                skip_git_repo_check=True,
                full_auto=True,
                live_search=True,
            ),
            run_label="idea-search",
        )
        body = _extract_message(result).strip()
        if "## Candidate" not in body:
            return 0

        block = (
            f"\n\n{SOURCE_MARKER}\n"
            "## Web-search candidates (codex live search — merge & rank with the above)\n\n"
            f"{body}\n"
        )
        path = _candidates_path(workdir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(block)

        count = body.count("## Candidate ")
        log.info("idea-search: appended %d web-search candidate(s) to %s", count, path)
        return count
    except Exception:  # noqa: BLE001 — a candidate SOURCE must never break the loop
        log.debug("idea-search augment failed", exc_info=True)
        return 0
