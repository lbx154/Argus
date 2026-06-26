"""MetaPrompter — frame the jump decision, parse the planner's verdict.

This module does NOT decide anything. It (a) renders the JUMP-mode context the
planner sees when the harness convenes a regime-jump turn, and (b) parses the
structured ``meta_decision`` the planner emits back. The decision — is the
basin really dead, and which regime to jump to — is the planner LLM's.

The jump framing is a CONTEXT RESET (spec §5), built deliberately to avoid the
EMNLP-2025 negative result (dumping all prior attempts is worse than ignoring
them): it carries a BOUNDED state — the never-cleared forbidden ledger, the
coverage map, and a "parent floor + top-k diverse inspirations" strategy pool —
NOT the local trajectory. The planner is told the local line is held back for
this turn and is shown the directions IT has previously declared dead, re-
surfaced as VISIBILITY — not as a hard gate. The harness convenes the turn (a
counter) and re-surfaces the agent's own ledger; whether to jump, and where,
stays the planner's research call (harness 没 agent 聪明 — no mechanical reject).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .config import STRATEGY_TYPES, MetaConfig
from .ledger import MetaLedger
from .saturation import SaturationSignal


@dataclass
class MetaDecision:
    """Parsed ``meta_decision`` from the planner (fail-soft)."""

    mode: str = "exploit"  # explore | exploit | jump
    confidence: float = 0.0
    reasoning: str = ""
    strategy_type: str = "unknown"
    forbidden: list[str] = field(default_factory=list)
    valid: bool = True
    violations: list[str] = field(default_factory=list)
    present: bool = False  # True if a meta_decision block was actually found


def build_meta_block(
    signal: SaturationSignal,
    ledger: MetaLedger,
    strategy_pool: str,
    mode: str,
    config: MetaConfig | None = None,
) -> str:
    """Render the meta-control block appended to the planner prompt.

    ``mode == "jump"`` → the full regime-jump context reset. ``"explore"`` → a
    lighter diversity nudge. ``"exploit"`` → ``""`` (unchanged planner path).
    """
    if mode == "exploit":
        return ""

    forbidden = ledger.forbidden if ledger else []
    cov = signal.coverage or {}
    cov_str = ", ".join(f"{k}×{v}" for k, v in sorted(cov.items())) or "(none recorded)"
    untouched = ", ".join(signal.untouched_axes) or "(none — all axes touched)"

    if mode == "explore":
        return (
            "## META-CONTROL — diversity nudge (NOT a jump; you still choose)\n"
            f"Recent window explored only {signal.diversity_score} distinct regime "
            f"axis(es). Untouched axes: {untouched}. Consider widening the next "
            "candidate toward an under-explored axis before the basin freezes.\n\n"
        )

    # mode == "jump"
    lines: list[str] = []
    lines.append(
        "## META-CONTROL — REGIME-JUMP TURN (harness-convened on a counter; YOU decide)"
    )
    lines.append(
        "The harness convened this turn because a domain-agnostic counter tripped "
        f"— the promoted floor has been frozen for {signal.frozen_rounds} "
        f"consecutive attempts while the recent window collapsed onto "
        f"{signal.diversity_score} regime axis(es). This is NOT a verdict that any "
        "idea is wrong, and it is NOT a hard gate: it is a scheduling nudge that "
        "now is a good moment to step OUT of the current backbone and open a line "
        "in a different regime. You are the researcher — if you have a strong, "
        "specific reason to stay on the current line, you may; just say why in "
        "meta_decision.reasoning."
    )
    lines.append("")
    lines.append("### What the harness re-surfaces (visibility — not enforced)")
    if forbidden:
        lines.append(
            "- DIRECTIONS YOU ALREADY DECLARED DEAD (your own, never cleared, "
            "re-surfaced every cycle so you don't unknowingly loop back — nothing "
            "is mechanically blocked; the call stays yours):"
        )
        lines.extend(f"    · {f}" for f in forbidden)
    else:
        lines.append(
            "- DEAD DIRECTIONS: (none recorded yet — if this turn convinces you a "
            "direction is exhausted, list it in meta_decision.forbidden and it will "
            "be re-surfaced to you next time)"
        )
    lines.append(
        f"- Regime coverage so far (from your recorded labels): {cov_str}. "
        f"Under-explored / untouched axes worth a look: {untouched}."
    )
    lines.append("")
    lines.append("### Context reset (intentional)")
    lines.append(
        "Your local trajectory (active_line / recent maturing tweaks) is held back "
        "this turn on purpose — continuing it is exactly the trap the counter "
        "flagged. The never-lost global-best floor is safe and recoverable from "
        "attempts/; you are not abandoning it, you are opening a NEW line."
    )
    lines.append("")
    lines.append("### Strategy pool — parent floor + diverse inspirations")
    lines.append(strategy_pool or "(no strategy pool surfaced)")
    lines.append("")
    lines.append("### What to output this turn")
    lines.append(
        "Queue ONE candidate — ideally one that changes REGIME (a different "
        "optimizer family, a different architecture paradigm, a data/curriculum "
        "change, or a different training-schedule regime), co-designed however you "
        "see fit. Then ALSO fill the OPTIONAL `meta_decision` field of your "
        "structured output so the harness can re-surface your own reasoning + "
        "declared dead-ends to you next cycle (it is visibility, not a contract):"
    )
    lines.append(
        "```json\n"
        '"meta_decision": {\n'
        '  "mode": "jump",\n'
        '  "confidence": 0.0,\n'
        '  "reasoning": "<why THIS regime — or, if you chose to stay, why>",\n'
        f'  "strategy_type": "<one of: {", ".join(a for a in STRATEGY_TYPES if a not in ("local","unknown"))}>",\n'
        '  "forbidden": ["<direction(s) you are now declaring dead, if any>"]\n'
        "}\n```"
    )
    lines.append("")
    return "\n".join(lines)


_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def attribution_fact(summary: dict) -> str:
    """NO-VERDICT visibility line: are the regime-jumps causing floor improvements
    or just churning? Pure counts from the decision log (see
    ``ledger.attribution_summary``). Empty until at least one jump or improvement
    is recorded, so it never adds noise on a fresh mission.
    """
    jumps = int(summary.get("jumps_fired", 0) or 0)
    exploits = int(summary.get("exploits_fired", 0) or 0)
    imp = int(summary.get("floor_improvements", 0) or 0)
    after_jump = int(summary.get("improvements_after_jump", 0) or 0)
    after_exploit = int(summary.get("improvements_after_exploit", 0) or 0)
    if not jumps and not imp:
        return ""
    return (
        "### Meta attribution (visibility — NO verdict)\n"
        f"- Regime-jumps fired so far: {jumps}; exploit cycles: {exploits}.\n"
        f"- Promoted-floor improvements: {imp} "
        f"({after_jump} right after a jump-cycle, {after_exploit} after an "
        "exploit-cycle).\n"
        "- This is bookkeeping so you can see whether jumping is MOVING the floor "
        "or just churning regimes; the interpretation is YOURS.\n\n"
    )


def explore_window_block(rounds_left: int, total: int = 0) -> str:
    """Valley-immunity framing — injected into the planner AND engineer while a
    post-jump exploration window is open.

    A new regime almost always REGRESSES before it is tuned; without protection
    the agent's own train-only proxy gate kills it on round 1 and restores the
    floor, so no regime change ever gets developed. This block tells the agent:
    the frozen floor is safe, a regressing candidate is EXPECTED, MEASURE it +
    iterate (do not skip on the proxy gate, do not restore on round 1), and do
    NOT jump to yet another regime — develop THIS one until the window closes.
    """
    k = max(0, int(rounds_left))
    span = f" ({k} round(s) left)" if not total else f" (round {max(1, total - k + 1)} of {total})"
    return (
        f"## META-CONTROL — REGIME EXPLORATION WINDOW{span}\n"
        "You recently JUMPED to a NEW regime and are now DEVELOPING it. Crucial:\n"
        "- The frozen global-best FLOOR is SAFE and unchanged — you are NOT risking "
        "it by exploring. It is recoverable from attempts/ at any time.\n"
        "- A new regime almost always REGRESSES before it is tuned. So a candidate "
        "on this line that is WORSE than the floor — or that your train-only proxy "
        "gate would normally skip — is EXPECTED here. RUN the 1-seed official scorer "
        "on it and ITERATE: do NOT skip scoring on a proxy 'it will regress' gate, "
        "and do NOT abandon/restore the line just because an early round is below "
        "the floor. The whole point of this window is to give the new regime the "
        "rounds it needs to cross its initial regression valley.\n"
        "- Spend this window TUNING the new regime (its hyperparameters / coupling "
        "with the backbone), NOT re-screening the old line and NOT jumping to yet "
        "another regime — that churns through regimes without ever developing one.\n"
        "- After the window closes, normal keep/reject + the noise gate resume; if "
        "the line still has not cleared the floor by then, a fresh jump convenes.\n\n"
    )


def _find_meta_json(text: str) -> dict | None:
    """Best-effort extract the ``meta_decision`` object from planner output."""
    if not text:
        return None
    # Prefer a fenced block that mentions a meta key.
    for m in _JSON_FENCE.finditer(text):
        blob = m.group(1)
        if '"strategy_type"' in blob or '"mode"' in blob:
            try:
                obj = json.loads(blob)
                if isinstance(obj, dict):
                    return obj
            except Exception:  # noqa: BLE001
                continue
    # Fallback: a bare {...} containing strategy_type.
    for m in re.finditer(r"\{[^{}]*\"strategy_type\"[^{}]*\}", text, re.DOTALL):
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except Exception:  # noqa: BLE001
            continue
    return None


def parse_meta_decision(
    text: str,
    *,
    forbidden_axes: set[str] | None = None,
    require_jump: bool = False,
    obj: dict | None = None,
) -> MetaDecision:
    """Parse + validate the planner's ``meta_decision`` (fail-soft).

    Prefer the structured ``obj`` (the ``meta_decision`` field the planner now
    returns directly in its schema'd output); fall back to scraping a fenced
    block out of ``text`` for older/looser outputs. Validation is structural,
    on the agent's OWN declared label — never a content judgment: ``mode`` and
    ``strategy_type`` must be in the known vocab; for a jump, ``strategy_type``
    must not be ``local``/``unknown`` and must not be in the never-cleared
    ``forbidden_axes``. ``valid``/``violations`` are RECORDED for the operator's
    audit trail; they do NOT gate the candidate (the harness re-surfaces the
    forbidden ledger as visibility, it does not mechanically reject).
    """
    if obj is None:
        obj = _find_meta_json(text or "")
    if not isinstance(obj, dict):
        return MetaDecision(present=False, valid=not require_jump)

    dec = MetaDecision(present=True)
    dec.mode = str(obj.get("mode") or "exploit").strip().lower()
    try:
        dec.confidence = float(obj.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        dec.confidence = 0.0
    dec.reasoning = str(obj.get("reasoning") or "").strip()[:600]
    dec.strategy_type = str(obj.get("strategy_type") or "unknown").strip().lower()
    raw_forbidden = obj.get("forbidden") or []
    if isinstance(raw_forbidden, str):
        raw_forbidden = [raw_forbidden]
    dec.forbidden = [str(x).strip() for x in raw_forbidden if str(x).strip()][:8]

    violations: list[str] = []
    if dec.mode not in ("explore", "exploit", "jump"):
        violations.append(f"unknown mode {dec.mode!r}")
    if dec.strategy_type not in STRATEGY_TYPES:
        violations.append(f"unknown strategy_type {dec.strategy_type!r}")
    if require_jump or dec.mode == "jump":
        if dec.strategy_type in ("local", "unknown"):
            violations.append(
                f"jump requires a regime axis, got {dec.strategy_type!r}"
            )
        if forbidden_axes and dec.strategy_type in forbidden_axes:
            violations.append(
                f"strategy_type {dec.strategy_type!r} is a FORBIDDEN (dead) regime"
            )
    dec.violations = violations
    dec.valid = not violations
    return dec
