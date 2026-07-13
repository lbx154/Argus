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

from ..core.models import RunnerOptions
from ..core.run_gateway import run_exec as gateway_run_exec

log = logging.getLogger(__name__)

#: Provenance marker delimiting the codex-web-search block in IDEA_CANDIDATES.md.
SOURCE_MARKER = "<!-- source: codex-web-search -->"

_CANDIDATES_RELPATH = ("research", "IDEA_CANDIDATES.md")
_BRIEF_RELPATH = ("research", "RESEARCH_BRIEF.md")

#: Corpus-derived research-move menu (a compact vocabulary of ideation
#: operators distilled from ML-conference outcomes). Baked into the prompt so
#: generation stays at "move-applied-to-gap" rather than open brainstorming.
#: The move is diagnostic vocabulary — never the contribution claim itself.
_RESEARCH_MOVES = (
    "1. Prove an equivalence/duality to unify two views\n"
    "2. Substitute the operator or representation\n"
    "3. Encode structure by construction (bake in an invariant/constraint)\n"
    "4. Manufacture the supervisory signal (self / weak / synthetic supervision)\n"
    "5. Design a property-targeting pretext / auxiliary objective\n"
    "6. Adapt by conditioning, not retraining (inference-time / steering / prompt)\n"
    "7. Relax or REMOVE a load-bearing assumption every prior method inherits\n"
    "8. Characterize a limit / derive a scaling law\n"
    "9. Reallocate compute or capacity to where it decides the outcome\n"
    "10. Add a verifier / test-time search or selection\n"
    "11. Decompose then recompose (phase- or stage-wise)\n"
    "12. Transfer a mechanism across domains / modalities\n"
    "13. Tighten a bound / add a guarantee\n"
    "14. Exploit an asymmetry (train/test, cost, or information)\n"
    "15. Compress / distill while provably preserving the target property\n"
)


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
        "paper that introduces a METHOD and beats a competitive baseline.\n"
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
        "STEP 2 — RESEARCH MOVE: pick exactly ONE move from the menu below whose "
        "operational signature structurally closes that gap. The move is thinking "
        "vocabulary, never the contribution itself:\n"
        f"{_RESEARCH_MOVES}"
        "STEP 3 — INSTANTIATE: turn the chosen move applied to the specific gap "
        "into one concrete, named mechanism that plausibly BEATS a reproduced, "
        "competitive baseline — NOT a diagnostic, a probe, a benchmark, or a "
        "'we measure that model M does X' study.\n\n"
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
        "**Research move**: <the ONE menu move, by name, that closes the gap>\n\n"
        "**Proposed method**: <the move instantiated as a concrete, named "
        "technique/mechanism you introduce — the contribution, not a "
        "measurement>\n\n"
        "**Baseline to beat + target**: <a reproduced, published, competitive "
        "baseline (name it), the real benchmark(s), and the margin you expect to "
        "win by>\n\n"
        "**Why it wins (thesis)**: <one sentence — the mechanism/insight that "
        "makes the gain non-obvious>\n\n"
        "**Grounding**: <cite 1-2 REAL papers you found via search, "
        "title + year + arxiv id; state what they did and the gap they leave>\n\n"
        "**Resource & 8h fit**: <compute needed vs what realistically exists "
        "(assume ~1 modern GPU unless the direction states otherwise); the "
        "training approach if any (LoRA/QLoRA/PEFT, small/base-model FT, trained "
        "probe/steering, distillation); confirm the MAIN experiment — training + "
        "baselines + method + key ablation — fits <=8h wall-clock, or how to "
        "descope so it does>\n\n"
        "**Anticipated kill-argument**: <the strongest ~40-word rejection>\n\n"
        "Rules: cite ONLY papers you actually found via search (no fabricated "
        "ids); the bottleneck and regression check must trace to retrieved "
        "papers, not memory. The research move is diagnostic vocabulary, never "
        "the contribution claim itself. Every candidate MUST propose a method "
        "with a concrete baseline it aims to beat and a nameable reason it should "
        "win — REJECT pure diagnostic / probing / benchmark-only ideas. Design "
        "each idea for compute that realistically exists (discover it; if the "
        "direction or operator states resource or time limits, honor those over "
        "any assumption), and keep the main experiment feasible in <=8h. Venue is "
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
