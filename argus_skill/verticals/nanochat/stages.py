"""NanoChat Autoresearch vertical — Recursive "First Steps" **Task 1**.

Objective: MINIMIZE the mean validation bits-per-byte (``val_bpb``) of a small
GPT trained from scratch under a FIXED 300-second single-GPU budget (B200),
scored by the frozen harness over N seeds. Reference scores to beat (Recursive,
single B200, 10-seed mean):

    vanilla_transformer       1.0587   (the naive baseline / start point)
    optimized_from_vanilla    0.9344   (first target to beat)
    optimized_from_karpathy   0.9109   (Recursive's best — the bar)

This is its OWN vertical, DISTINCT from the nanoGPT *speedrun* (minimize wall
TIME to a target loss) and KernelBench/SOL (maximize a Speed-of-Light score)
verticals. It reuses the generic 4-stage setup→optimize→measure→report
structure and the flat-workspace STAGE_CHECKS / reviewer checklists (which are
already BPB-shaped); only the role banner pins the nanochat objective.
"""
from __future__ import annotations

import json
import os
import re
import statistics
from pathlib import Path

# Reuse the BPB-shaped structure + flat-workspace checks from the generic
# optimization vertical. This is code reuse, not identity: this module is its
# OWN named vertical (so the nanochat task is never classified as "speedrun"),
# free to diverge from speedrun's checklists later.
from ..speedrun.stages import (  # noqa: F401  (re-exported as this vertical's contract)
    CHECKLIST_ITEMS,
    CHECKLIST_STAGE_ORDER,
    REVIEWER_CHECKLISTS,
    STAGE_CHECKS,
    STAGE_ORDER,
)

#: Mechanical metric gate (not a paper); the supervisor stops when the metric
#: stops improving rather than on paper-completeness.
completion_gate = "metric"


#: The productive, mechanism-CHANGING optimization axes for the 300s-budget
#: from-scratch LM task, biggest-lever-first. The planner is steered to spend
#: candidates here instead of re-sweeping a saturated scalar knob.
_CATEGORY_AXES = (
    "1. OPTIMIZER ALGORITHM — the biggest known lever for fixed-budget "
    "from-scratch LM training: Muon (Newton-Schulz orthogonalized momentum), "
    "Lion, Sophia, Shampoo/SOAP, schedule-free AdamW, Adam-mini; and their "
    "momentum/preconditioner/decoupling.\n"
    "2. ARCHITECTURE — QK-norm, RMSNorm placement (pre/post/sandwich), "
    "RoPE/positional scheme, GQA/MQA, sliding-window/local attention, SwiGLU "
    "hidden sizing, embedding tying/untying, logit soft-cap, value/residual "
    "scaling, depth<->width reshape at fixed params.\n"
    "3. EFFECTIVE-UPDATE MECHANICS — EMA / weight-averaging (Polyak/SWA), "
    "z-loss, label smoothing, grad-clip regime, lr x batch scaling laws.\n"
    "4. DATA — sequence packing, ordering/curriculum, dedup, doc boundaries.\n"
    "5. NUMERICS & INIT — init scale, muP-style width scaling, fp8/bf16 matmul, "
    "QK clipping."
)


def role_banner(role: str) -> str:
    """Pin the nanochat-BPB objective; steer the PLANNER off knob-tweak ruts.

    The banner is role-aware: every role gets the frozen-constraint mission
    framing, but the PLANNER additionally gets a hard SEARCH-DISCIPLINE rule
    that (a) forbids re-sweeping a saturated scalar hyperparameter, (b) gates
    keep/reject at the seed-to-seed NOISE so sub-noise deltas are never banked,
    and (c) replaces greedy one-lever-at-a-time screening with a two-mode
    search: single-lever sweep while it still clears the noise, then CO-DESIGNED
    BUNDLES (2-4 levers proposed together) once single-lever wins thin out —
    because several frontier levers regress in isolation and only pay off
    together, so greedy search can never assemble them. The engineer/reviewer
    get the matching reinforcement (implement bundles faithfully + ablate the
    winner; never bank a sub-noise screen; retry regressed-alone levers inside a
    bundle). This is what stops both the scalar micro-tweak loop and the
    greedy-single-lever plateau.
    """
    common = (
        "MISSION — NanoChat Autoresearch (Recursive Task 1). This is NOT a\n"
        "speedrun and NOT a paper. The single objective: LOWER the mean\n"
        "validation bits-per-byte (val_bpb) of a small GPT trained FROM SCRATCH\n"
        "in a FIXED 300-second single-GPU budget on B200. Beat 0.9344\n"
        "(optimized_from_vanilla), then 0.9109 (Recursive's best). Start from\n"
        "vanilla 1.0587. Edit ONLY train.py; the metric, 300s budget, val shard,\n"
        "and harness (lib.py) are frozen. Do NOT optimize for wall-time or\n"
        "throughput for its own sake — only the final val_bpb matters.\n"
    )
    # Island mode (multi-island search): when this lineage runs as one island of
    # a population, soft-pin it to its seeded regime. Diversity / migration /
    # reseeding are the orchestrator's job, so the island agent does NOT need to
    # jump regimes itself — it develops its OWN axis and mines the population-best
    # from inspirations/. Soft (bias only); the agent is still free to co-design.
    _regime = os.environ.get("ARGUS_ISLAND_REGIME", "").strip()
    if _regime:
        common = common + (
            f"\nISLAND MODE — this lineage is SEEDED toward the `{_regime}` regime. "
            "Bias your candidates toward that axis (it is where this island is meant "
            "to explore); you may still co-design within/around it. Cross-island "
            "diversity, migration of the population-best, and reseeding a stalled "
            "island are handled by the ORCHESTRATOR — you do NOT need to jump "
            "regimes yourself. Check the `inspirations/` dir for the population-best "
            "candidate(s) to study (derive, do not blindly copy).\n"
        )
    if role == "planner":
        return common + (
            "\nSEARCH DISCIPLINE (HARD RULE — overrides the safe-incremental pull):\n"
            "DIAGNOSE BEFORE YOU PROPOSE — every candidate is a TEST OF A HYPOTHESIS "
            "about the binding constraint, never plausible-guessing. Maintain a "
            "CURRENT diagnosis (re-measure it when the floor moves or after a couple "
            "of regressions): WHERE does the 300s budget land on the loss curve (still "
            "steep = sample-efficiency-bound; flattening = capacity/throughput-bound), "
            "what is the per-step bottleneck (profile a step — torch.profiler/timing, "
            "the B200 hardware perf counters are blocked), and which lever CLASS the "
            "current floor is most STARVED on. EVERY candidate (single lever OR bundle) "
            "MUST name that diagnosed constraint and explain MECHANISTICALLY why this "
            "change addresses IT — 'these levers should combine well' is NOT a reason. "
            "A change with no measured diagnosis behind it is a guess; do not propose "
            "it.\n"
            "Before proposing the next candidate, READ the attempt history "
            "(attempts/, RESULTS.md). A lone single-scalar tweak (peak LR, "
            "weight-decay, batch size, warmup/warmdown/final-LR fraction, dropout) "
            "is worth AT MOST one value. If the recent screens are single-knob "
            "tweaks clustering within run-to-run noise (~0.001-0.002 BPB) of the "
            "verified floor, that basin is SATURATED: do NOT propose another value of "
            "an already-swept knob — that is wasted 300s budget.\n"
            "NOISE GATE: a keep/reject decided on a val_bpb delta SMALLER than the "
            "seed-to-seed run noise (~0.001-0.002) is a COIN FLIP, not a win. Do NOT "
            "treat a sub-noise screen as progress or bank it as a floor; spend the "
            "next candidate on a lever big enough to clear the noise.\n"
            "DO NOT SEARCH GREEDILY ONE-LEVER-AT-A-TIME. Use two modes:\n"
            "  (1) SINGLE-LEVER sweep — while a new category change still clears the "
            "noise, propose ONE category-level change per candidate, biggest "
            "UNEXPLORED lever first, roughly in this order:\n"
            f"{_CATEGORY_AXES}\n"
            "  (2) CO-DESIGNED BUNDLE (the non-greedy move — use it as soon as "
            "single-lever wins thin out, i.e. the last several category changes land "
            "within noise or regress): propose 2-4 levers TOGETHER as ONE candidate, "
            "motivated by a structural hypothesis (e.g. reshape the capacity "
            "allocation AND widen the output head AND match the init/residual scaling "
            "for the new shape, all in one candidate). CRITICAL: several frontier "
            "levers REGRESS IN ISOLATION and only pay off TOGETHER — so a greedy 'one "
            "lever vs the floor' search rejects each piece and NEVER reaches the "
            "combination. Therefore: (a) a lever that regressed ALONE but is plausibly "
            "synergistic is NOT dead — keep a synergy-shortlist and RETRY it inside a "
            "bundle; (b) after a bundle WINS, the next candidates ABLATE within it "
            "(one lever off at a time) to find who carries the gain and drop dead "
            "weight. Bundles are first-class candidates, not a fallback.\n"
            "The gap to 0.9344 is the last leg of a COORDINATED STRUCTURE, not "
            "one more standalone trick — single-knob noise will never close it "
            "(see the live Search-altitude facts for the current distance). "
            "Name the lever(s) each candidate explores. (Method: skills 'NanoChat "
            "Autoresearch Hands-on Trace' / 'NanoChat Autoresearch SOTA Optimization' "
            "— learn the loop, but do NOT copy any reference recipe; derive and "
            "measure your own.)\n"
        )
    if role == "engineer":
        return common + (
            "\nWhen the task is a CATEGORY change OR a CO-DESIGNED BUNDLE (2-4 levers "
            "as one hypothesis), implement it FAITHFULLY and correctly end-to-end — a "
            "correct, informative REGRESSION is more valuable than a safe "
            "within-noise non-result, so do not water a bold bet down into a knob "
            "tweak. For a BUNDLE, implement ALL of its levers coherently (they are "
            "designed to pay off TOGETHER, not separately); once a bundle wins, expect "
            "the next tasks to ABLATE within it (one lever off at a time). REPORT "
            "whether the screened result CONFIRMED or REFUTED the candidate's stated "
            "hypothesis about the binding constraint — that read, not just the number, "
            "is what updates the diagnosis for the next candidate. Still "
            "1-seed screen first; keep lib.py and the scorer frozen; "
            "real flash_attn.cute FA-4 only (never SDPA/fallback/FA2).\n"
            "When you write the attempt's `summary.json`, record a "
            "`strategy_type` field naming which REGIME AXIS this candidate "
            "explores — one of: `optimizer` | `architecture` | "
            "`update_mechanics` | `data` | `numerics` | `local` (use `local` for "
            "a within-regime tweak). This lets the search track regime coverage "
            "so a frozen basin is detected honestly from your OWN labels.\n"
        )
    if role == "reviewer":
        return common + (
            "\nINNOVATION CHECK: if the screened candidate is yet another single-"
            "scalar tweak landing within run-to-run noise (~0.001-0.002 BPB) of the "
            "floor, say so plainly — a sub-noise delta is a COIN FLIP, not a win, and "
            "must NOT be banked as a real improvement. Record in the handoff that the "
            "next candidate must either be a bigger single lever OR a CO-DESIGNED "
            "BUNDLE (2-4 levers proposed TOGETHER), NOT another greedy one-lever "
            "screen — and that a lever which regressed ALONE may still be a synergy "
            "candidate to RETRY inside a bundle, not discarded. Still verify the hard "
            "gates: real FA-4, frozen lib.py, honest real-run score.\n"
        )
    return common


# ---------------------------------------------------------------------------
# Search-altitude fact surfacer (NO verdict — pure visibility).
#
# The planner/reviewer banners forbid greedy single-lever search, but the agent
# was found re-running "A237 + one knob -> reject -> restore A237" for 25+
# attempts because it had NO live view of its own search state: the prompt never
# carried the live floor, the distance to target, how long the floor had been
# frozen, or which levers it had already recombined. This surfaces exactly those
# facts — re-read from the AGENT's own recorded ``attempts/*/summary.json`` — so
# the agent's OWN judgment ("is this basin saturated? change regime?") finally
# has the data to bite on. It asserts no threshold and makes no keep/reject
# call; that decision stays with the agent (same posture as the legitimate
# ``mediocrity_finding`` / ``method_differentiation`` fact-surfacers). Metric
# parsing lives HERE in the vertical (which knows its ``mean_val_bpb`` schema),
# so the cross-vertical harness stays metric-blind.
# ---------------------------------------------------------------------------

#: Recursive single-B200 10-seed reference scores (see module docstring).
_REF_VANILLA = 1.0587
_REF_OPTIMIZED_FROM_VANILLA = 0.9344  # first target to beat
_REF_BEST = 0.9109  # Recursive's best — the bar
_ALTITUDE_RECENT_N = 8
_ALTITUDE_TOKEN_WINDOW = 25
_ALTITUDE_TOKEN_TOP = 12


def _attempt_index(name: str) -> int:
    """Leading ``aNNN`` index for ordering. A non-aNNN name sorts as NEWEST (a
    large sentinel), never as the oldest — so a stray non-aNNN dir cannot be
    mistaken for the earliest attempt and freeze the since-improve counter."""
    m = re.match(r"a(\d+)", name)
    return int(m.group(1)) if m else 10**18


def _read_attempt_record(adir: Path) -> tuple[float | None, str]:
    """Return ``(mean_val_bpb, decision)`` for one attempt dir from the
    AGENT-authored ``summary.json`` (preferred) or ``results.csv`` (fallback).

    Returns ``(None, decision)`` when no usable score. The harness only
    RE-SURFACES the agent's own recorded number; it never measures the metric.
    Three integrity rules, all deferring to the AGENT's own record:
    * key-casing: accept ``mean_val_bpb`` OR ``MEAN_VAL_BPB`` (a casing drift
      must never silently drop recent attempts and freeze a stale floor);
    * official fallback: an attempt scored only under ``official_val_bpb`` (a
      later-era key for an officially-rescored candidate) still contributes its
      number, so officially-scored rejects are not invisibly dropped;
    * validity: a record the agent flagged ``score_valid=False`` contributes NO
      score, so an explicitly-invalid run can never seed the "verified FLOOR".
    """
    def _num(v: object) -> float | None:
        if isinstance(v, bool):
            return None
        return float(v) if isinstance(v, (int, float)) else None

    decision = ""
    sj = adir / "summary.json"
    if sj.exists():
        try:
            obj = json.loads(sj.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                decision = str(obj.get("decision") or "").strip().lower()
                if obj.get("score_valid") is False:
                    return None, decision  # agent flagged this run invalid
                for key in ("mean_val_bpb", "MEAN_VAL_BPB", "official_val_bpb"):
                    n = _num(obj.get(key))
                    if n is not None:
                        return n, decision
                for key, val in obj.items():  # any-case fallback
                    if key.lower() == "mean_val_bpb":
                        n = _num(val)
                        if n is not None:
                            return n, decision
        except Exception:  # noqa: BLE001 — fail-soft per attempt
            pass
    cf = adir / "results.csv"
    if cf.exists():
        try:
            import csv

            rows = list(csv.DictReader(cf.open()))
            vals = [float(r["val_bpb"]) for r in rows if r.get("val_bpb")]
            if vals:
                return statistics.mean(vals), decision
        except Exception:  # noqa: BLE001
            pass
    return None, decision



def _read_attempt_strategy(adir: Path) -> str:
    """Agent-recorded ``strategy_type`` label from ``summary.json`` (or '').

    This is a GENERIC regime label (which axis a candidate explores —
    optimizer / architecture / data / …), NOT the metric, so the meta layer may
    read it directly without breaking the harness's metric-blindness. Legacy
    attempts have no label → ''.
    """
    sj = adir / "summary.json"
    if sj.exists():
        try:
            obj = json.loads(sj.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                return str(obj.get("strategy_type") or "").strip().lower()
        except Exception:  # noqa: BLE001 — fail-soft per attempt
            pass
    return ""


def _read_attempt_promoted(adir: Path) -> bool | None:
    """The AGENT's structured ``promoted`` boolean from ``summary.json``.

    ``True``/``False`` when the agent recorded it; ``None`` for legacy attempts
    with no flag (the floor logic then falls back to an anchored decision check).
    Reading the structured flag — instead of testing ``"promote" in decision`` —
    is what stops a rejected candidate whose reject text merely *references* a
    prior promote ("restored root to promoted A374") from re-anchoring the floor.
    """
    sj = adir / "summary.json"
    if sj.exists():
        try:
            obj = json.loads(sj.read_text(encoding="utf-8"))
            if isinstance(obj, dict) and isinstance(obj.get("promoted"), bool):
                return obj["promoted"]
        except Exception:  # noqa: BLE001 — fail-soft per attempt
            pass
    return None


#: Tokens whose presence means a decision string is REFERENCING a promote in a
#: negative/restore context, not declaring one (the live floor-anchor bug).
_PROMOTE_NEG = re.compile(r"reject|restore|revert|regress|un[\s_-]*promot|no[t]?[\s_-]*promot")


def _is_promote(promoted_flag: object, decision: str) -> bool:
    """Did the agent PROMOTE this attempt to be the new floor?

    Prefer the AGENT's structured ``promoted`` boolean; only when it is absent
    (legacy attempts) fall back to an ANCHORED decision check that excludes
    restore/reject context — never a bare ``"promote" in decision`` substring,
    which the live nanochat-B200 mission proved re-anchors the floor onto a
    rejected, *regressed* candidate ("...restored to promoted A374...").
    """
    if promoted_flag is True:
        return True
    if promoted_flag is False:
        return False
    d = (decision or "").strip().lower()
    if not d or _PROMOTE_NEG.search(d):
        return False
    return d.startswith("promote") or bool(re.search(r"[\s_\-]promote", d))


def _frozen_since(project_root: object, floor_index: int) -> int:
    """Consecutive COMPLETED attempts since the floor's attempt last improved.

    The saturation counter. Counts every recorded attempt with an ``aNNN`` index
    after ``floor_index`` — INCLUDING candidate attempts that ran but produced no
    official score (e.g. ``PROFILE_GATE_FAIL_NO_SCORE``): those are genuine frozen
    steps, and excluding them lets the counter be *starved* (more gate-failures →
    a LOWER freeze count, a perverse incentive that hid the live saturation).
    Pure DIAGNOSIS attempts (no candidate; summary carries ``diagnosis_type``) do
    NOT count, so a legitimately diagnosing agent is never force-jumped for
    diagnosing. Fail-soft → 0.
    """
    try:
        adir = Path(str(project_root)) / "attempts"
        if not adir.is_dir():
            return 0
        n = 0
        for d in sorted(adir.iterdir()):
            if not d.is_dir() or _attempt_index(d.name) <= floor_index:
                continue
            sj = d / "summary.json"
            if sj.exists():
                try:
                    obj = json.loads(sj.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    obj = {}
                if isinstance(obj, dict) and obj.get("diagnosis_type"):
                    continue  # pure diagnosis, not a frozen candidate step
                n += 1
            elif (d / "results.csv").exists():
                n += 1
        return n
    except Exception:  # noqa: BLE001
        return 0


def _scored_attempts(
    project_root: object,
) -> list[tuple[int, str, float, str, str, object]]:
    """Shared read loop: ``(index, name, score, decision, strategy_type,
    promoted)`` for every attempt dir with a usable score, sorted oldest→newest.
    Used by both the rendered altitude block and the structured facts hook so the
    read logic is defined once. ``promoted`` is the agent's structured flag
    (``True``/``False``/``None``). Fail-soft: ``[]`` on any error / no attempts.
    """
    try:
        root = Path(str(project_root))
        adir = root / "attempts"
        if not adir.is_dir():
            return []
        out: list[tuple[int, str, float, str, str, object]] = []
        for d in sorted(adir.iterdir()):
            if not d.is_dir():
                continue
            score, decision = _read_attempt_record(d)
            if score is None:
                continue
            out.append(
                (
                    _attempt_index(d.name),
                    d.name,
                    score,
                    decision,
                    _read_attempt_strategy(d),
                    _read_attempt_promoted(d),
                )
            )
        out.sort(key=lambda t: (t[0], t[1]))
        return out
    except Exception:  # noqa: BLE001
        return []


def _name_tokens(name: str) -> list[str]:
    """Split an attempt name into lever-ish word tokens, dropping the aNNN
    prefix, pure digits, and ubiquitous filler so the frequency hint is
    informative."""
    raw = re.split(r"[_\-]+", name)
    toks: list[str] = []
    for t in raw:
        t = t.strip().lower()
        if not t or t.isdigit():
            continue
        if re.fullmatch(r"a\d+", t):  # the aNNN index token
            continue
        toks.append(t)
    return toks


def search_altitude_context(project_root: object) -> str:
    """Return a NO-VERDICT 'search altitude' fact block, or ``""``.

    Pure visibility re-surfaced from ``attempts/*/summary.json``: the live
    floor, distance to the two reference targets, the count of consecutive
    non-improving attempts, the last few attempt deltas, and an APPROXIMATE
    attempt-name token frequency (what has been recombined). It states no
    threshold and makes no keep/reject decision. Fail-soft: any error / no
    scored attempts → empty string, so prompt building never breaks on it.
    """
    try:
        root = Path(str(project_root))
        adir = root / "attempts"
        if not adir.is_dir():
            return ""
        attempts = _scored_attempts(project_root)
        if not attempts:
            return ""
        scores = [t[2] for t in attempts]

        # FLOOR = the agent's OWN PROMOTED best (re-surface its judgment), NOT a
        # raw min(): a rejected sub-noise dip must not be labelled the floor and
        # contradict the agent's recorded floor. Anchor on the structured
        # ``promoted`` flag (a rejected candidate whose reject text merely says
        # "restored to promoted A374" is NOT a promote), and take the BEST such
        # promote so a later regressed re-promote can never raise the floor. Fall
        # back to the best raw score only if the agent never recorded a promote.
        promoted = [i for i, t in enumerate(attempts) if _is_promote(t[5], t[3])]
        if promoted:
            floor_pos = min(promoted, key=lambda i: scores[i])
        else:
            floor_pos = min(range(len(scores)), key=lambda i: scores[i])
        floor = scores[floor_pos]
        floor_name = attempts[floor_pos][1]
        # Frozen count over ALL recorded candidate attempts since the floor's
        # index (incl. no-score gate-failures; excl. pure diagnosis), not just the
        # scored sub-list — else gate-failures STARVE the saturation counter.
        since_improve = _frozen_since(project_root, attempts[floor_pos][0])

        # Best RAW measured — may be a rejected sub-noise dip BELOW the floor;
        # surfaced separately so the block never looks like it is hiding a
        # lower number from the agent.
        raw_pos = min(range(len(scores)), key=lambda i: scores[i])
        raw_best = scores[raw_pos]
        raw_name = attempts[raw_pos][1]
        raw_note = ""
        if raw_best < floor - 1e-9:
            raw_note = (
                f"- Best RAW measured: {raw_best:.6f} (from `{raw_name}`) — but "
                "YOU did not promote it (sub-noise / rejected), so the FLOOR "
                "above is your promoted best.\n"
            )

        d_target = floor - _REF_OPTIMIZED_FROM_VANILLA
        d_best = floor - _REF_BEST

        recent_lines = []
        for t in attempts[-_ALTITUDE_RECENT_N:]:
            recent_lines.append(f"    {t[1]} | {t[2]:.6f} | {t[2] - floor:+.6f}")

        # Approximate lever recombination hint from recent attempt names.
        from collections import Counter

        ctr: Counter[str] = Counter()
        for t in attempts[-_ALTITUDE_TOKEN_WINDOW:]:
            ctr.update(set(_name_tokens(t[1])))
        token_hint = ", ".join(
            f"{tok}×{n}" for tok, n in ctr.most_common(_ALTITUDE_TOKEN_TOP)
        ) or "(none)"

        return (
            "## Search altitude — LIVE facts from attempts/ (NO verdict; YOU judge)\n"
            "Re-surfaced from your OWN recorded attempts/*/summary.json "
            "(mean_val_bpb, lower is better). The harness asserts no threshold "
            "and makes no keep/reject call — this is visibility only so your "
            "research judgment has data to bite on.\n"
            f"- Attempts scored so far: {len(attempts)}\n"
            f"- Live verified FLOOR (your latest PROMOTED best): {floor:.6f}  "
            f"(from `{floor_name}`)\n"
            f"{raw_note}"
            f"- Distance to go: to optimized_from_vanilla {_REF_OPTIMIZED_FROM_VANILLA} "
            f"= {d_target:+.4f}; to Recursive best {_REF_BEST} = {d_best:+.4f}  "
            f"(start point: vanilla {_REF_VANILLA})\n"
            f"- Consecutive attempts since the FLOOR last improved: {since_improve}\n"
            f"- Last {len(recent_lines)} attempts (name | mean_val_bpb | Δ vs floor):\n"
            + "\n".join(recent_lines)
            + "\n"
            f"- Attempt-name token frequency over the last "
            f"{min(_ALTITUDE_TOKEN_WINDOW, len(attempts))} "
            "(APPROXIMATE hint at what has been recombined): "
            f"{token_hint}\n"
            "Interpretation is YOURS: e.g. a floor frozen across many sub-noise "
            "attempts that recombine the same tokens may mean the basin is "
            "saturated and the next candidate should change regime (per the "
            "SEARCH DISCIPLINE banner) — but that call is your research "
            "judgment, not the harness's.\n\n"
        )
    except Exception:  # noqa: BLE001 — must never break prompt building
        return ""


def search_altitude_facts(project_root: object) -> dict:
    """Structured twin of :func:`search_altitude_context` for the meta layer.

    Returns ``{floor, floor_name, since_improve, raw_best, n_attempts,
    attempts:[{name,index,score,decision,strategy_type}]}`` — the same numbers
    the rendered block shows, re-surfaced as data so the cross-vertical meta
    layer can detect saturation WITHOUT re-implementing this vertical's
    ``val_bpb`` parsing (the harness stays metric-blind). Floor anchoring is
    identical to the rendered block: the agent's BEST structured-``promoted``
    attempt, else the best raw score; ``since_improve`` counts candidate attempts
    (incl. no-score) since the floor. Fail-soft: ``{}`` on any error.
    """
    try:
        attempts = _scored_attempts(project_root)
        if not attempts:
            return {}
        scores = [t[2] for t in attempts]
        promoted = [i for i, t in enumerate(attempts) if _is_promote(t[5], t[3])]
        floor_pos = (
            min(promoted, key=lambda i: scores[i])
            if promoted
            else min(range(len(scores)), key=lambda i: scores[i])
        )
        raw_pos = min(range(len(scores)), key=lambda i: scores[i])
        return {
            "floor": scores[floor_pos],
            "floor_name": attempts[floor_pos][1],
            "since_improve": _frozen_since(project_root, attempts[floor_pos][0]),
            "raw_best": scores[raw_pos],
            "n_attempts": len(attempts),
            "attempts": [
                {
                    "name": t[1],
                    "index": t[0],
                    "score": t[2],
                    "decision": t[3],
                    "strategy_type": t[4],
                }
                for t in attempts
            ],
        }
    except Exception:  # noqa: BLE001 — meta detection must never break prompts
        return {}


def strategy_pool(project_root: object) -> str:
    """Regime strategy pool for a JUMP: the menu + coverage + diverse inspirations.

    Composed from things that already exist for this vertical:
      * the ``_CATEGORY_AXES`` taxonomy (the mechanism-CHANGING axes menu),
      * a coverage annotation (which canonical regime axes the agent's recorded
        ``strategy_type`` labels have already touched vs are UNTOUCHED),
      * the promoted FLOOR as the "parent", plus a handful of the EARLIEST,
        most behaviourally-distinct attempts as diverse "inspirations"
        (AlphaEvolve parent+inspirations; bounded per the EMNLP negative
        result — NOT a full-history dump).
    Fail-soft: ``""`` on any error.
    """
    try:
        try:
            from ...meta.config import REGIME_AXES
        except Exception:  # noqa: BLE001
            REGIME_AXES = (
                "optimizer",
                "architecture",
                "update_mechanics",
                "data",
                "numerics",
            )
        facts = search_altitude_facts(project_root)
        attempts = facts.get("attempts", []) if facts else []
        cov = {ax: 0 for ax in REGIME_AXES}
        for a in attempts:
            st = str(a.get("strategy_type") or "").strip().lower()
            if st in cov:
                cov[st] += 1
        touched = [ax for ax in REGIME_AXES if cov[ax] > 0]
        untouched = [ax for ax in REGIME_AXES if cov[ax] == 0]

        # Diverse early inspirations: walk oldest→newest, take the first attempt
        # of each distinct name-axis token, up to a small bound.
        seen: set[str] = set()
        inspirations: list[str] = []
        for a in attempts:
            toks = _name_tokens(str(a.get("name") or ""))
            key = toks[0] if toks else ""
            if key and key not in seen:
                seen.add(key)
                inspirations.append(f"{a['name']} ({a['score']:.6f})")
            if len(inspirations) >= 6:
                break

        floor = facts.get("floor")
        floor_name = facts.get("floor_name", "?")
        parent = (
            f"{floor:.6f} (from `{floor_name}`)"
            if isinstance(floor, (int, float))
            else "(unknown)"
        )
        # Island mode: migrated population-best candidates dropped by the
        # orchestrator into inspirations/ are first-class diverse parents.
        migrated = ""
        try:
            insp = Path(str(project_root)) / "inspirations"
            if insp.is_dir():
                names = sorted(p.name for p in insp.iterdir() if p.is_file())
                if names:
                    migrated = (
                        "MIGRATED population-best (from sibling islands — study, do "
                        "NOT blindly copy):\n"
                        + "\n".join(f"  - inspirations/{n}" for n in names[:6])
                        + "\n"
                    )
        except Exception:  # noqa: BLE001
            pass
        return (
            "REGIME AXES MENU (biggest-lever-first; pick an UNDER-EXPLORED one):\n"
            f"{_CATEGORY_AXES}\n\n"
            f"COVERAGE (from your recorded strategy_type labels): "
            f"touched = {', '.join(touched) or '(none labelled)'}; "
            f"UNTOUCHED = {', '.join(untouched) or '(all touched)'}.\n"
            f"PARENT (the floor to beat, your safe deliverable): {parent}\n"
            + migrated
            + "DIVERSE INSPIRATIONS (early, behaviourally-distinct attempts to mine "
            "for a different regime — NOT to copy):\n"
            + ("\n".join(f"  - {x}" for x in inspirations) or "  (none)")
            + "\n"
        )
    except Exception:  # noqa: BLE001
        return ""


__all__ = [
    "REVIEWER_CHECKLISTS",
    "STAGE_CHECKS",
    "STAGE_ORDER",
    "CHECKLIST_STAGE_ORDER",
    "CHECKLIST_ITEMS",
    "completion_gate",
    "role_banner",
    "search_altitude_context",
    "search_altitude_facts",
    "strategy_pool",
]
