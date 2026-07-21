"""No-go / negative-result TERMINAL path gate (execute+review) — ADVISORY.

The single missing bridge from the s-cbac6ede stall: when a bounded negative result
is fully evidenced and gate-passing, the vertical must be able to advance
Execute -> Review -> Manuscript and complete a no-go paper WITHOUT a human typing the
authorization. This gate reproduces, autonomously, the exact action the operator took
54 s before the pipeline advanced.

Behaviour (never edits Argus core; the terminal manuscript HARD gate is unchanged):
* If the no-go evidence is SUFFICIENT and Tier-D no-go terminal is enabled, set
  ``manuscript_completion_authorized=true`` (NO_GO scope) in ROUTE_CLOSURE_STATUS.json
  and emit a NEXT_ROLE_DIRECTIVE + gate-feedback telling the ManuscriptBuilder to write
  the bounded no-go manuscript. NEVER dispatch generic hygiene closure.
* If sufficient but the no-go terminal is operator-gated (e.g. a stretch tier), emit a
  single OPERATOR_AUTHORIZATION_REQUEST.json + operator-prompt feedback, then idle.
* If not sufficient, emit gate-feedback naming the ONE missing piece.

Requires (deterministic): >=2 preregistered diagnostics falsified, a closure artifact,
a bounded no-go claim in CLAIMS.csv, no evidence fabrication (every claim has an
evidence pointer), and bounded (finite-volume/failure-regime, not universal) scope.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from ....skills.research_gates import (
    clear_gate_state,
    read_csv_rows,
    update_gate_state,
    write_gate_outputs,
)
from ..downgrade import read_current_tier
from ..gate_feedback import (
    clear_feedback,
    feedback_manuscript_completion_unauthorized,
    write_feedback,
)
from ..tiers import nogo_terminal_enabled

GATE_ID = "nogo_terminal"
STAGE = "review"
CLOSURE_STATUS = "ROUTE_CLOSURE_STATUS.json"
NO_GO_FILE = "ORIGINAL_RESEARCH_NO_GO.md"
CLAIMS = "CLAIMS.csv"
_MIN_FALSIFIED = 2
_UNIVERSAL_TOKENS = ("universal", "for all", "theorem for every", "in the thermodynamic limit for all")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _falsified_count(root: Path, closure: dict) -> int:
    n = len(closure.get("failed_round2_candidates") or [])
    # Also count no-go family files as falsified-route evidence.
    rdir = root / "research"
    if rdir.is_dir():
        n += sum(1 for p in rdir.glob("*_NO_GO.md"))
    return n


def _claims_support_nogo(root: Path) -> tuple[bool, bool]:
    """Return (has_bounded_nogo_claim, no_fabrication)."""
    header, rows = read_csv_rows(root / CLAIMS)
    if not header or not rows:
        return False, True
    has_nogo = False
    no_fab = True
    for r in rows:
        status = str(r.get("status", "") or "").strip().lower()
        ctype = str(r.get("claim_type", "") or "").strip().lower()
        if status in {"no-go", "no_go", "negative", "null"} or "no-go" in ctype or "negative" in ctype:
            has_nogo = True
        # fabrication check: a supported/no-go claim must carry an evidence pointer.
        if status in {"supported", "no-go", "no_go", "negative"} and not str(r.get("evidence_pointer", "") or "").strip():
            no_fab = False
    return has_nogo, no_fab


def _bounded_scope(closure: dict) -> bool:
    ptype = str(closure.get("paper_type", "") or "").lower()
    scope = str(closure.get("manuscript_completion_authorized_scope", "") or "").lower()
    text = ptype + " " + scope
    if any(tok in text for tok in _UNIVERSAL_TOKENS):
        return False
    return ("bounded" in text or "finite" in text or "failure-regime" in text or "no_go" in text
            or "no-go" in text or str(closure.get("route_status", "")).upper() == "ORIGINAL_RESEARCH_NO_GO")


def nogo_evidence_status(project_root: object) -> dict:
    """Deterministic sufficiency check for a bounded no-go manuscript."""
    root = Path(str(project_root or "."))
    closure = _read_json(root / CLOSURE_STATUS) or {}
    falsified = _falsified_count(root, closure)
    closure_artifact = (root / NO_GO_FILE).is_file() or (root / CLOSURE_STATUS).is_file()
    has_nogo_claim, no_fab = _claims_support_nogo(root)
    bounded = _bounded_scope(closure)
    reasons = []
    if falsified < _MIN_FALSIFIED:
        reasons.append(f"only {falsified} falsified diagnostic(s); need >= {_MIN_FALSIFIED}")
    if not closure_artifact:
        reasons.append(f"no closure artifact ({NO_GO_FILE} or {CLOSURE_STATUS})")
    if not has_nogo_claim:
        reasons.append("CLAIMS.csv has no bounded no-go / negative claim row")
    if not no_fab:
        reasons.append("a supported/no-go claim lacks an evidence_pointer (possible fabrication)")
    if not bounded:
        reasons.append("scope is not bounded (finite-volume/failure-regime) or overextrapolates to universal")
    sufficient = not reasons
    return {
        "falsified_diagnostics": falsified,
        "closure_artifact_exists": closure_artifact,
        "claims_support_nogo": has_nogo_claim,
        "no_fabrication": no_fab,
        "bounded_scope": bounded,
        "sufficient": sufficient,
        "missing": reasons,
    }


def authorize_nogo_manuscript(project_root: object, *, now_iso: str | None = None) -> dict:
    """Set manuscript_completion_authorized=true (NO_GO scope) + emit the advance directive."""
    root = Path(str(project_root or "."))
    ts = now_iso or _now_iso()
    closure = _read_json(root / CLOSURE_STATUS) or {"route_status": "ORIGINAL_RESEARCH_NO_GO"}
    closure["original_research_no_go_manuscript_supported"] = True
    closure["manuscript_completion_authorized"] = True
    closure["manuscript_completion_authorized_scope"] = (
        "ORIGINAL_RESEARCH_NO_GO path only; bounded finite-volume / failure-regime manuscript; "
        "no positive diagnostic-method-win claim is authorized")
    closure["manuscript_completion_authorized_at"] = ts
    closure["manuscript_completion_authorized_by"] = "physics-vertical-nogo-terminal-gate"
    tmp = (root / CLOSURE_STATUS).with_name(CLOSURE_STATUS + ".tmp")
    tmp.write_text(json.dumps(closure, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(root / CLOSURE_STATUS)
    directive = {
        "responsible_role": "ManuscriptBuilder",
        "required_action": "Write the bounded failure-regime / no-go manuscript from the existing evidence",
        "expected_next_stage": "manuscript",
        "advance": "execute -> review -> manuscript",
        "acceptance_test": "manuscript_package gate 0 failures; manuscript check --layer all satisfied",
        "do_not_do": ["chase a positive diagnostic", "run another novelty pivot", "generic hygiene closure"],
    }
    tmp2 = (root / "research" / "NEXT_ROLE_DIRECTIVE.json").with_name("NEXT_ROLE_DIRECTIVE.json.tmp")
    tmp2.parent.mkdir(parents=True, exist_ok=True)
    tmp2.write_text(json.dumps(directive, indent=2, sort_keys=True), encoding="utf-8")
    tmp2.replace(root / "research" / "NEXT_ROLE_DIRECTIVE.json")
    return {"authorized": True, "scope": "ORIGINAL_RESEARCH_NO_GO", "at": ts, "directive": directive}


def _operator_request(project_root: object, status: dict, *, tier: str, now_iso: str | None = None) -> dict:
    root = Path(str(project_root or "."))
    req = {
        "request_id": "nogo_manuscript_authorization",
        "created_at": now_iso or _now_iso(),
        "tier": tier,
        "question": ("A bounded no-go result is fully evidenced and gate-passing. Authorize advancing it "
                     "to a bounded failure-regime manuscript, or downgrade the target tier?"),
        "options": ["authorize no-go manuscript advancement", "downgrade target tier", "keep closed",
                    "authorize a genuinely NEW mechanism"],
        "evidence": status,
        "do_not_do": ["dispatch Engineer for generic hygiene closure", "run another novelty pivot"],
    }
    tmp = (root / "research" / "OPERATOR_AUTHORIZATION_REQUEST.json").with_name("OPERATOR_AUTHORIZATION_REQUEST.json.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(req, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(root / "research" / "OPERATOR_AUTHORIZATION_REQUEST.json")
    return req


def run_gate(project_root: object, *, now_iso: str | None = None) -> tuple[bool, list[dict]]:
    """Advisory gate: authorize the no-go manuscript when sufficient, else surface the gap.

    ``passed`` here means "no blocking gap surfaced this round" (advisory). It writes
    gate outputs + a GATE_STATE / GATE_FAIL record so the physics role_banner surfaces
    the directive to the responsible role.
    """
    root = Path(str(project_root or "."))
    status = nogo_evidence_status(root)
    tier = read_current_tier(root)
    at_stretch = tier in {"S", "A"}
    failures: list[dict] = []
    action = "none"

    if status["sufficient"]:
        if nogo_terminal_enabled() and not at_stretch:
            authorize_nogo_manuscript(root, now_iso=now_iso)
            rec = feedback_manuscript_completion_unauthorized(at_stretch_tier=False, tier=tier)
            # authorized now — record the advance directive as the active feedback
            write_feedback(root, rec)
            action = "authorized_nogo_manuscript"
        else:
            _operator_request(root, status, tier=tier, now_iso=now_iso)
            rec = feedback_manuscript_completion_unauthorized(at_stretch_tier=True, tier=tier)
            write_feedback(root, rec)
            action = "operator_authorization_requested"
    else:
        # Not sufficient — surface the single missing piece; do NOT infinitely chase.
        clear_feedback(root, "manuscript_completion")
        failures = [{
            "failure_id": f"NGT-{i:03d}", "severity": "major", "stage": STAGE,
            "artifact": CLOSURE_STATUS, "field": "",
            "message": f"no-go evidence not yet sufficient: {reason}",
            "required_action": "Complete this ONE missing piece of the no-go closure (do not chase a positive diagnostic).",
            "blocks_progress": False,
        } for i, reason in enumerate(status["missing"], 1)]
        action = "insufficient_surface_gap"

    passed = not failures
    result = {
        "gate_id": GATE_ID, "stage": STAGE, "passed": passed, "advisory": True,
        "tier": tier, "action": action, "evidence_status": status,
        "nogo_terminal_enabled": nogo_terminal_enabled(),
    }
    write_gate_outputs(root, GATE_ID, result=result, failures=failures,
                       human_review=_render_review(status, tier, action))
    if failures:
        update_gate_state(root, GATE_ID, failures)
    else:
        clear_gate_state(root, GATE_ID)
    return passed, failures


def _render_review(status: dict, tier: str, action: str) -> str:
    lines = [
        "# No-go terminal gate review (advisory)", "",
        f"Tier: {tier}  |  action: {action}  |  sufficient: {status['sufficient']}", "",
        "This gate provides the AUTONOMOUS no-go -> manuscript bridge. It NEVER dispatches "
        "generic hygiene closure and NEVER requires a positive diagnostic once the bounded "
        "negative result is evidenced.", "",
        "## Evidence status",
    ]
    for k, v in status.items():
        if k != "missing":
            lines.append(f"- {k}: {v}")
    if status.get("missing"):
        lines += ["", "## Missing (complete these, do NOT chase a positive diagnostic)"]
        lines += [f"- {m}" for m in status["missing"]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="physics-nogo-terminal-gate")
    parser.add_argument("command", choices=["check"], nargs="?", default="check")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--advisory", action="store_true")
    args = parser.parse_args(argv)
    passed, failures = run_gate(args.project_root)
    if passed:
        print("nogo-terminal gate: no blocking gap (authorized / operator-requested / not applicable)")
        return 0
    print("nogo-terminal gate: no-go evidence not yet sufficient:", file=sys.stderr)
    for f in failures:
        print(f"  - {f['failure_id']} {f['message']}", file=sys.stderr)
    return 0  # always advisory


__all__ = [
    "GATE_ID", "STAGE", "nogo_evidence_status", "authorize_nogo_manuscript", "run_gate", "main",
]

if __name__ == "__main__":
    raise SystemExit(main())
