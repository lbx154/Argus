"""Physics-specific context-compaction / artifact-pointer policy.

Long runs (many model<->execute pivots) inflate context; on s-cbac6ede an 18.2M-token
call priced $5.54 and tripped the per-call fence. This module builds a compact
ROUTE_STATE_DIGEST.md (pointers + short summaries, NOT full artifact bodies) and a
banner directive telling roles to read the digest + latest artifacts rather than
replaying the full history. When a token/cost threshold is exceeded it flags a
compression gate instead of continuing with a giant call. Pure/deterministic; no
Argus core edit.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ENV_TOKEN_SOFT = "ARGUS_SKILL_PHYSICS_CONTEXT_TOKEN_SOFT"
ENV_TOKEN_HARD = "ARGUS_SKILL_PHYSICS_CONTEXT_TOKEN_HARD"
DEFAULT_TOKEN_SOFT = 8_000_000
DEFAULT_TOKEN_HARD = 15_000_000

#: Large artifacts that must be referenced by pointer + digest, never inlined wholesale.
_POINTER_ONLY = (
    "events.jsonl", "usage.jsonl", "NUMERICAL_STUDY_PLAN.csv",
    "PRIOR_WORK_MATRIX.csv", "THEORY_OPPORTUNITY_AUDIT.csv",
)
#: Small, decision-bearing artifacts worth a short inline summary.
_SUMMARY_FILES = (
    "ROUTE_CLOSURE_STATUS.json", "PAPER_TYPE_CLASSIFIER.json", "research/TIER_STATE.json",
    "research/DOWNGRADE_DECISION.json", "research/NEXT_ROLE_DIRECTIVE.json",
)


def _token_threshold(env: str, default: int) -> int:
    raw = os.environ.get(env)
    if raw and str(raw).strip():
        try:
            return int(float(raw))
        except ValueError:
            return default
    return default


def should_compress(*, token_estimate: int | None = None, cost_usd: float | None = None,
                    per_call_cap_usd: float | None = None) -> tuple[bool, str]:
    """Whether to trigger a compression gate. Returns (compress, reason)."""
    hard = _token_threshold(ENV_TOKEN_HARD, DEFAULT_TOKEN_HARD)
    soft = _token_threshold(ENV_TOKEN_SOFT, DEFAULT_TOKEN_SOFT)
    if token_estimate is not None and token_estimate >= hard:
        return True, f"context tokens {token_estimate} >= hard {hard}: compress before the next call"
    if per_call_cap_usd and cost_usd is not None and cost_usd >= 0.9 * per_call_cap_usd:
        return True, f"projected call cost ${cost_usd:.2f} near per-call cap ${per_call_cap_usd:.2f}: compress"
    if token_estimate is not None and token_estimate >= soft:
        return True, f"context tokens {token_estimate} >= soft {soft}: prefer digest+pointers"
    return False, ""


def _file_line(root: Path, rel: str) -> str:
    p = root / rel
    if not p.is_file():
        return f"- (absent) {rel}"
    try:
        size = p.stat().st_size
    except OSError:
        size = 0
    return f"- {rel} — {size} bytes (reference by pointer; do not inline)"


def _summarize_json(root: Path, rel: str, keys: tuple[str, ...]) -> str:
    p = root / rel
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return f"- {rel}: (absent/unreadable)"
    if not isinstance(d, dict):
        return f"- {rel}: (non-dict)"
    picked = {k: d.get(k) for k in keys if k in d}
    return f"- {rel}: {json.dumps(picked, ensure_ascii=False)[:400]}"


def build_context_digest(project_root: object) -> str:
    """A compact digest: current stage/tier, closure summary, and artifact POINTERS."""
    root = Path(str(project_root or "."))
    state = {}
    try:
        state = json.loads((root / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    lines = [
        "# ROUTE STATE DIGEST (read THIS + the latest artifacts; do not replay full history)", "",
        f"current_stage: {state.get('current_stage', '')}",
        f"rollbacks: {len(state.get('rollback_history') or [])}  |  advances: {len(state.get('stage_history') or [])}",
        "",
        "## Decision-bearing summaries",
        _summarize_json(root, "ROUTE_CLOSURE_STATUS.json",
                        ("route_status", "manuscript_completion_authorized",
                         "diagnostic_method_win_supported", "original_research_no_go_manuscript_supported")),
        _summarize_json(root, "research/TIER_STATE.json", ("current_tier", "start_tier")),
        _summarize_json(root, "research/NEXT_ROLE_DIRECTIVE.json",
                        ("responsible_role", "required_action", "expected_next_stage")),
        "",
        "## Large artifacts — POINTERS ONLY (open on demand; never paste wholesale)",
    ]
    for rel in _POINTER_ONLY:
        lines.append(_file_line(root, rel))
    lines += [
        "",
        "## Context policy",
        "- Reference prior evidence by file path + one-line claim, not by re-pasting the artifact.",
        "- For CLAIMS / numerical evidence, cite the row/figure id and the number, not the whole table.",
        "- If a call would exceed the context/cost threshold, compress to this digest first.",
    ]
    return "\n".join(lines) + "\n"


def write_digest(project_root: object) -> Path:
    root = Path(str(project_root or "."))
    rdir = root / "research"
    rdir.mkdir(parents=True, exist_ok=True)
    path = rdir / "ROUTE_STATE_DIGEST.md"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(build_context_digest(root), encoding="utf-8")
    tmp.replace(path)
    return path


def context_policy_banner() -> str:
    """Short banner block instructing roles to use the digest + pointers."""
    return (
        "## CONTEXT POLICY (physics)\n"
        "- Read research/ROUTE_STATE_DIGEST.md + only the latest relevant artifacts; do NOT replay "
        "the full run history or paste large artifacts (events/telemetry/usage/large CSVs) wholesale.\n"
        "- Reference prior evidence by file path + one-line claim; cite a CLAIMS row / figure id + the "
        "number rather than the whole table.\n"
        "- If a call would exceed the context/cost budget, compress to the digest first instead of a "
        "single giant call.\n"
    )


__all__ = [
    "ENV_TOKEN_SOFT", "ENV_TOKEN_HARD", "should_compress", "build_context_digest",
    "write_digest", "context_policy_banner",
]
