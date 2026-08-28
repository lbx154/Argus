"""Math-Reasoning Data Synthesis vertical (Arbor paper App. C.6) — maximize the
mean ``pass@4 - pass@1`` gap of a synthesized problem set.

Objective: MAXIMIZE ``score = mean(pass@4 - pass@1)`` over all generated
candidates, by REFACTORING the data-synthesis PIPELINE (``baseline.py``,
``configs/pipeline.yaml``, ``prompts/generate_problem.md``, new modules under
``src/math_synth_bench/``). The fixed reference solver (gpt-5.5, effort=low,
temp=1.0, 4 samples), the validity/novelty/dedup verification, the metric, the
seeds, and ``run_eval.py`` are FROZEN. HIGHER score is better — it rewards
problems the solver gets WRONG on the first sample but RIGHT within four
(calibrated, valid, novel, diverse problems).

Mirrors the metric-MAXIMIZING browsecomp vertical's shape; the role banner,
stage checklist, and altitude facts are pinned to the pass-gap objective and
the editable-pipeline freeze.
"""
from __future__ import annotations

import json
import re
import statistics
from pathlib import Path

from ..optimization_base import speedrun_base_contract

_BASE = speedrun_base_contract()
CHECKLIST_STAGE_ORDER = _BASE.stage_order
CHECKLIST_ITEMS = _BASE.checklist_items

#: Mechanical metric gate (not a paper): the supervisor stops when the score
#: stops improving, not on paper-completeness.
completion_gate = "metric"

STAGE_ORDER = ["setup", "optimize", "measure", "report"]


#: The productive, mechanism-CHANGING axes for raising the pass-gap, biggest-
#: lever-first. ALL live in the EDITABLE pipeline; solver/metric/verify FROZEN.
_CATEGORY_AXES = (
    "1. DIFFICULTY CALIBRATION — the biggest lever: steer generated problems into "
    "the solver's UNRELIABLE-BUT-SOLVABLE band (first sample often wrong, but "
    "solvable within four). Too easy => pass@1 already 1 (gap 0); too hard / "
    "ill-posed => pass@4 also 0 (gap 0). Estimate/iterate difficulty, re-sample "
    "or adjust parameters to hit the band.\n"
    "2. PROGRAMMATIC / PARAMETRIC GENERATION — generate from a structured template "
    "with a PROGRAMMATICALLY COMPUTED answer (so the stated answer is provably "
    "correct and passes the consistency filter), with randomized parameters for "
    "diversity. Beats free-form LLM problems that fail format/consistency.\n"
    "3. VALIDITY & CONSISTENCY YIELD — raise the SURVIVAL rate past the frozen "
    "filters (integer 0..999 format, rationale-matches-answer consistency, "
    "novelty vs held-out, near-duplicate dedup); a filtered candidate scores 0, "
    "so yield is a direct multiplier on the metric.\n"
    "4. NOVELTY & DIVERSITY — spread candidates across techniques/structures so "
    "they clear the held-out-overlap and near-duplicate filters and don't collapse "
    "onto one template.\n"
    "5. PROMPTING — the generation prompt / format (a lever, but usually SMALLER "
    "than the structural ones above).\n"
    "FROZEN (NEVER edit — doing so INVALIDATES the result): run_eval.py, the "
    "solver (solver.py), verification.py, metrics.py, seeds/heldout data, and the "
    "fixed reference-solver config. Generate only through the pipeline; never "
    "hardcode solver answers, read the solver's outputs into generation, or "
    "special-case the metric."
)


def role_banner(role: str) -> str:
    """Pin the pass-gap MAXIMIZE objective + the editable-pipeline freeze."""
    common = (
        "MISSION — Math-Reasoning Data Synthesis (Arbor App. C.6). The single\n"
        "objective: MAXIMIZE score = mean(pass@4 - pass@1) over the generated\n"
        "problem set, by REFACTORING the data-synthesis PIPELINE. HIGHER is\n"
        "better. You build a generator that produces VALID, NOVEL, DIVERSE,\n"
        "integer-answer (0..999) competition problems CALIBRATED so a FIXED\n"
        "gpt-5.5 solver fails on the first sample but succeeds within four.\n"
        "\n"
        "EDITABLE — the pipeline only: src/math_synth_bench/baseline.py (entry\n"
        "point generate(seed, n)->list), configs/pipeline.yaml,\n"
        "prompts/generate_problem.md, and NEW modules under src/math_synth_bench/.\n"
        "FROZEN (touching any INVALIDATES the result): run_eval.py, solver.py,\n"
        "verification.py, metrics.py, the seed/heldout data, and the fixed\n"
        "reference-solver config. Never hardcode solver answers or read the\n"
        "solver's outputs into generation.\n"
        "FIRST establish the baseline: run the pristine pipeline on the dev split\n"
        "to MEASURE the starting score — do not assume a number. Score ONLY with\n"
        "`python run_eval.py --split dev` (iterate) and `--split test`\n"
        "(held-out, milestone/final). Each candidate must beat the measured\n"
        "baseline on the SAME split.\n"
    )
    if role == "planner":
        return common + (
            "\nSEARCH DISCIPLINE (HARD RULE):\n"
            "DIAGNOSE BEFORE YOU PROPOSE. Read the per-candidate results.json from "
            "the last run: is the score low because (a) too many candidates are "
            "FILTERED (bad format / inconsistent rationale / overlap / duplicate -> "
            "a YIELD problem), (b) survivors are TOO EASY (pass@1 already 1 -> a "
            "DIFFICULTY-too-low problem), or (c) survivors are TOO HARD (pass@4 = 0 "
            "-> difficulty-too-high / ill-posed)? Every candidate must name the "
            "diagnosed failure mode and the mechanistic reason the change fixes IT.\n"
            "A prompt-only tweak is worth AT MOST one try and rarely moves the "
            "floor; prefer the STRUCTURAL levers (programmatic/parametric "
            "generation, difficulty calibration, validity yield).\n"
            "NOISE GATE: the dev split is small (10 seeds x 5 = up to 50 candidates "
            "before filtering), so a small score delta is within run-to-run noise "
            "(the solver is stochastic by design) — do NOT bank a sub-noise gain; "
            "confirm a promising candidate on the test split (or a larger n) before "
            "promoting.\n"
            "Use two modes: (1) a single structural change per candidate, biggest-"
            "unexplored-lever first:\n"
            f"{_CATEGORY_AXES}\n"
            "  (2) a CO-DESIGNED BUNDLE (2-4 mechanisms together) once single "
            "changes thin out — e.g. parametric generation + programmatic answers + "
            "a difficulty-calibration loop, as ONE candidate. Name the lever(s) "
            "each candidate explores.\n"
        )
    if role == "engineer":
        return common + (
            "\nWhen the task is a PIPELINE change OR a CO-DESIGNED BUNDLE, implement "
            "it FAITHFULLY end-to-end in the editable pipeline — a correct, "
            "informative REGRESSION is more valuable than a within-noise prompt "
            "tweak. Keep the freeze inviolate: edit ONLY the pipeline files; keep "
            "the generate(seed, n) entry point; do not modify any frozen file, the "
            "solver, the verification, the metric, or the seeds; never hardcode "
            "solver answers. Iterate on dev; CONFIRM a promising candidate on test "
            "before promoting. Snapshot each version to attempts/<name>/ with a "
            "short CHANGES.md.\n"
            "When you write each attempt's summary.json, record: `score` (dev "
            "split, mean(pass@4-pass@1); HIGHER better), `n_candidates`, "
            "`n_survived`, `pass1_rate`, `pass4_rate`, `test_score` (when "
            "measured), `decision`, `promoted`, and `strategy_type` (one of: "
            "`difficulty` | `parametric` | `yield` | `diversity` | `prompt`). "
            "REPORT whether the run CONFIRMED or REFUTED the candidate's hypothesis "
            "about the binding failure mode.\n"
        )
    if role == "reviewer":
        return common + (
            "\nVALIDITY FIRST: a candidate is a real result ONLY if it changed "
            "nothing in the freeze (run_eval.py, solver.py, verification.py, "
            "metrics.py, the seed/heldout data byte-identical; generate() entry "
            "point intact; no hardcoded solver answers) AND its score comes from a "
            "clean run of the frozen run_eval.py on the stated split. Re-read the "
            "score from the run's summary.json; do not trust a self-reported "
            "number.\n"
            "INNOVATION CHECK: the dev split is small and the solver is stochastic, "
            "so a small score gain may be within noise — say so plainly; it must "
            "NOT be banked without test-split confirmation. Record in the handoff "
            "that the next candidate should be a structural pipeline change or a "
            "co-designed bundle, not another prompt nibble. Watch dev/test "
            "divergence.\n"
        )
    return common


def stage_completion_issues(stage: str, project_root: Path) -> tuple[str, ...]:
    from ..metric_evidence import EvidenceError, validate_math_synth_evidence

    issues: list[str] = []
    if stage == "measure":
        try:
            validate_math_synth_evidence(project_root)
        except EvidenceError as exc:
            issues.append(str(exc))
    if stage == "report":
        report = project_root / "RESULTS.md"
        if not report.is_file() or report.stat().st_size <= 0:
            issues.append("report requires non-empty RESULTS.md")
    return tuple(issues)


# ---------------------------------------------------------------------------
# Altitude facts (NO verdict — pure visibility). MAXIMIZE, so the "floor" is the
# BEST (max) score promoted so far.
# ---------------------------------------------------------------------------

#: Aspirational reference (Arbor reported dev pass-gap ~0.24 on its own setup —
#: a DIFFERENT pipeline/solver/seeds, so a loose bar, not a target to match).
_REF_SOTA = 0.24
_ALTITUDE_RECENT_N = 8
_ALTITUDE_TOKEN_WINDOW = 25
_ALTITUDE_TOKEN_TOP = 12


def _attempt_index(name: str) -> int:
    m = re.match(r"a?(\d+)", name)
    return int(m.group(1)) if m else 10**18


def _num(v: object) -> float | None:
    if isinstance(v, bool):
        return None
    return float(v) if isinstance(v, (int, float)) else None


def _read_attempt_record(adir: Path) -> tuple[float | None, str]:
    decision = ""
    sj = adir / "summary.json"
    if sj.exists():
        try:
            obj = json.loads(sj.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                decision = str(obj.get("decision") or "").strip().lower()
                if obj.get("score_valid") is False:
                    return None, decision
                for key in ("score", "pass_gap", "mean_pass_gap", "SCORE"):
                    n = _num(obj.get(key))
                    if n is not None:
                        return n, decision
        except Exception:  # noqa: BLE001
            pass
    accs = []
    for rs in adir.glob("runs/*/summary.json"):
        try:
            o = json.loads(rs.read_text(encoding="utf-8"))
            n = _num(o.get("score"))
            if n is not None and str(o.get("split")) == "dev":
                accs.append(n)
        except Exception:  # noqa: BLE001
            pass
    if accs:
        return statistics.mean(accs), decision
    return None, decision


def _read_attempt_strategy(adir: Path) -> str:
    sj = adir / "summary.json"
    if sj.exists():
        try:
            obj = json.loads(sj.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                return str(obj.get("strategy_type") or "").strip().lower()
        except Exception:  # noqa: BLE001
            pass
    return ""


def _read_attempt_promoted(adir: Path) -> bool | None:
    sj = adir / "summary.json"
    if sj.exists():
        try:
            obj = json.loads(sj.read_text(encoding="utf-8"))
            if isinstance(obj, dict) and isinstance(obj.get("promoted"), bool):
                return obj["promoted"]
        except Exception:  # noqa: BLE001
            pass
    return None


_PROMOTE_NEG = re.compile(r"reject|restore|revert|regress|un[\s_-]*promot|no[t]?[\s_-]*promot")


def _is_promote(promoted_flag: object, decision: str) -> bool:
    if promoted_flag is True:
        return True
    if promoted_flag is False:
        return False
    d = (decision or "").strip().lower()
    if not d or _PROMOTE_NEG.search(d):
        return False
    return d.startswith("promote") or bool(re.search(r"[\s_\-]promote", d))


def _scored_attempts(project_root: object) -> list[tuple[int, str, float, str, str, object]]:
    try:
        adir = Path(str(project_root)) / "attempts"
        if not adir.is_dir():
            return []
        out: list[tuple[int, str, float, str, str, object]] = []
        for d in sorted(adir.iterdir()):
            if not d.is_dir():
                continue
            score, decision = _read_attempt_record(d)
            if score is None:
                continue
            out.append((_attempt_index(d.name), d.name, score, decision,
                        _read_attempt_strategy(d), _read_attempt_promoted(d)))
        out.sort(key=lambda t: (t[0], t[1]))
        return out
    except Exception:  # noqa: BLE001
        return []


def _name_tokens(name: str) -> list[str]:
    toks = []
    for t in re.split(r"[_\-]+", name):
        t = t.strip().lower()
        if not t or t.isdigit() or re.fullmatch(r"a\d+", t):
            continue
        toks.append(t)
    return toks


def _frozen_since(project_root: object, floor_index: int) -> int:
    try:
        adir = Path(str(project_root)) / "attempts"
        if not adir.is_dir():
            return 0
        n = 0
        for d in sorted(adir.iterdir()):
            if not d.is_dir() or _attempt_index(d.name) <= floor_index:
                continue
            if (d / "summary.json").exists():
                n += 1
        return n
    except Exception:  # noqa: BLE001
        return 0


def search_altitude_context(project_root: object) -> str:
    """NO-VERDICT altitude fact block (score, HIGHER better)."""
    try:
        attempts = _scored_attempts(project_root)
        if not attempts:
            return ""
        scores = [t[2] for t in attempts]
        promoted = [i for i, t in enumerate(attempts) if _is_promote(t[5], t[3])]
        best_pos = (max(promoted, key=lambda i: scores[i]) if promoted
                    else max(range(len(scores)), key=lambda i: scores[i]))
        best = scores[best_pos]
        best_name = attempts[best_pos][1]
        since_improve = _frozen_since(project_root, attempts[best_pos][0])
        raw_pos = max(range(len(scores)), key=lambda i: scores[i])
        raw_best = scores[raw_pos]
        raw_note = ""
        if raw_best > best + 1e-9:
            raw_note = (
                f"- Highest RAW score measured: {raw_best:.3f} (from "
                f"`{attempts[raw_pos][1]}`) — but YOU did not promote it (within "
                "noise / not test-confirmed), so the BEST above is your promoted "
                "best.\n"
            )
        recent = "\n".join(
            f"    {t[1]} | score {t[2]:.3f} | Δ {t[2] - best:+.3f}"
            for t in attempts[-_ALTITUDE_RECENT_N:]
        )
        from collections import Counter
        ctr: Counter[str] = Counter()
        for t in attempts[-_ALTITUDE_TOKEN_WINDOW:]:
            ctr.update(set(_name_tokens(t[1])))
        token_hint = ", ".join(f"{k}x{n}" for k, n in ctr.most_common(_ALTITUDE_TOKEN_TOP)) or "(none)"
        return (
            "## Search altitude — LIVE facts from attempts/ (NO verdict; YOU judge)\n"
            "Re-surfaced from your OWN attempts/*/summary.json (score=mean(pass@4-"
            "pass@1), HIGHER is better; dev split). Visibility only.\n"
            f"- Scored attempts so far: {len(attempts)}\n"
            f"- Live BEST score (your latest PROMOTED best): {best:.3f}  "
            f"(from `{best_name}`)\n"
            f"{raw_note}"
            f"- Aspirational bar (Arbor's different setup): {_REF_SOTA:.3f}  "
            f"(distance {best - _REF_SOTA:+.3f}) — MEASURE your own baseline.\n"
            f"- Consecutive attempts since BEST last improved: {since_improve}\n"
            f"- Last {min(_ALTITUDE_RECENT_N, len(attempts))} attempts (name | score | Δ vs best):\n"
            + recent + "\n"
            f"- Attempt-name token frequency (last {min(_ALTITUDE_TOKEN_WINDOW, len(attempts))}): "
            f"{token_hint}\n\n"
        )
    except Exception:  # noqa: BLE001
        return ""


__all__ = [
    "STAGE_ORDER",
    "CHECKLIST_STAGE_ORDER", "CHECKLIST_ITEMS",
    "completion_gate", "role_banner", "search_altitude_context",
    "stage_completion_issues",
]
