"""MetaPrompter — frame the jump decision, parse the planner's verdict.

This module does NOT decide anything. It (a) renders the JUMP-mode context the
planner sees when the harness convenes a regime-jump turn, and (b) parses the
structured ``meta_decision`` the planner emits back. The decision — is the
basin really dead, and which regime to jump to — is the planner LLM's.

The jump framing is a CONTEXT RESET (spec §5), built deliberately to avoid the
EMNLP-2025 negative result (dumping all prior attempts is worse than ignoring
them): it carries a BOUNDED state — the never-cleared forbidden ledger, the
coverage map, and a "parent floor + top-k diverse inspirations" strategy pool —
NOT the local trajectory. The planner is told the local line is suppressed on
purpose and that the next candidate is validated against the forbidden set, so
a re-anchoring tweak is rejected by the harness before it spends 300 s.
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
        "## META-CONTROL — REGIME JUMP CONVENED (harness-convened on a counter; "
        "YOU decide the regime)"
    )
    lines.append(
        "The harness convened this turn because a domain-agnostic counter tripped "
        f"— the promoted floor has been frozen for {signal.frozen_rounds} "
        f"consecutive attempts while the recent window collapsed onto "
        f"{signal.diversity_score} regime axis(es). This is NOT a verdict that any "
        "idea is wrong; it is a scheduling signal that this turn must propose a "
        "REGIME JUMP, not another local lever on the current backbone."
    )
    lines.append("")
    lines.append("### Binding constraints the harness WILL enforce on your next candidate")
    if forbidden:
        lines.append(
            "- FORBIDDEN DIRECTIONS (these were declared dead by YOU/your "
            "predecessors and are never cleared — a candidate whose `strategy_type` "
            "is one of these is rejected before it spends 300 s):"
        )
        lines.extend(f"    · {f}" for f in forbidden)
    else:
        lines.append("- FORBIDDEN DIRECTIONS: (none yet — you may declare some below)")
    lines.append(
        f"- The next candidate's `strategy_type` MUST be an UNDER-EXPLORED axis. "
        f"Coverage so far (recorded): {cov_str}. Untouched axes: {untouched}."
    )
    lines.append("")
    lines.append("### Context reset (intentional)")
    lines.append(
        "Your local trajectory (active_line / recent maturing tweaks) is "
        "SUPPRESSED this turn on purpose — continuing it is exactly the trap. The "
        "never-lost global-best floor is safe and recoverable from attempts/; you "
        "are not abandoning it, you are opening a NEW line in a different regime."
    )
    lines.append("")
    lines.append("### Strategy pool — parent floor + diverse inspirations")
    lines.append(strategy_pool or "(no strategy pool surfaced)")
    lines.append("")
    lines.append("### What to output this turn")
    lines.append(
        "Queue ONE candidate that changes REGIME (a different optimizer family, a "
        "different architecture paradigm, a data/curriculum change, or a different "
        "training-schedule regime) — co-designed as needed, but NOT a tweak of the "
        "current backbone. Then emit a fenced `meta_decision` JSON block:"
    )
    lines.append(
        "```json\n"
        "{\n"
        '  "mode": "jump",\n'
        '  "confidence": 0.0,\n'
        '  "reasoning": "<why THIS regime, tied to the diagnosed binding constraint>",\n'
        f'  "strategy_type": "<one of: {", ".join(a for a in STRATEGY_TYPES if a not in ("local","unknown"))}>",\n'
        '  "forbidden": ["<the now-dead direction(s) you are leaving behind>"]\n'
        "}\n```"
    )
    lines.append("")
    return "\n".join(lines)


_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


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
) -> MetaDecision:
    """Parse + validate the planner's ``meta_decision`` (fail-soft).

    Validation (structural, on the agent's OWN declared label — never a content
    judgment): ``mode`` and ``strategy_type`` must be in the known vocab; for a
    jump, ``strategy_type`` must not be ``local``/``unknown`` and must not be in
    the never-cleared ``forbidden_axes``. ``valid=False`` + ``violations`` flags
    a jump that tried to re-anchor on a dead regime, so the caller can re-ask.
    """
    obj = _find_meta_json(text or "")
    if obj is None:
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
