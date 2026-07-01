"""Stage-aware checklist runner for the research pipeline.

Reads the current pipeline stage from research/PIPELINE_STATE.json and
runs shell checks relevant to that stage. Outputs a reviewer checklist
for critical stages — the reviewer agent (codex) reads the checklist,
loads the referenced skill, and inspects the artifacts itself.

Usage:
    python -m argus_skill.tools.stage_check --project-root .
"""
from __future__ import annotations

# ruff: noqa: I001
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Stage definitions live in the research vertical. The shell-check runner
# in this module is generic across verticals; the stage list / per-stage
# checks / reviewer checklists are paper-specific and authoritative at
# ``argus_skill.verticals.research.stages``. Imported here as module-level
# re-exports for backward compatibility with the many callers that import
# ``STAGE_ORDER`` / ``STAGE_CHECKS`` / ``REVIEWER_CHECKLISTS`` directly from
# ``argus_skill.tools.stage_check``.
# --------------------------------------------------------------------------
from ..verticals.research.stages import (  # noqa: E402
    REVIEWER_CHECKLISTS,
    STAGE_CHECKS,
    STAGE_ORDER,
    _PIPELINE_CHECK,
)

__all__ = [
    "REVIEWER_CHECKLISTS",
    "STAGE_CHECKS",
    "STAGE_ORDER",
    "_PIPELINE_CHECK",
    "main",
]


def _reviewer_checklist_for(
    stage: str, venue: Any
) -> tuple[str, str, list[str]] | None:
    """Return the venue-adjusted (skill, instructions, files) for a stage.

    EMNLP returns the static template byte-for-byte. For other venues the
    load-bearing reviewer-skill filename, the page-budget line, and the
    reviewer-persona references are rewritten from the profile so an AAAI
    reviewer is not pointed at the EMNLP skill or told to enforce ACL pages.
    """
    entry = REVIEWER_CHECKLISTS.get(stage)
    if entry is None:
        return None
    skill, instructions, files = entry
    if getattr(venue, "key", "EMNLP") == "EMNLP":
        return skill, instructions, files
    persona = venue.reviewer_persona
    if skill == "reviewer/emnlp-academic-language-review.md":
        skill = venue.review_skill_path
    instructions = (
        instructions.replace("an actual EMNLP reviewer", f"an actual {persona} reviewer")
        .replace("EMNLP reviewers find", f"{persona} reviewers find")
        .replace("Reject at EMNLP", f"Reject at {persona}")
        .replace("support an EMNLP paper", f"support an {persona} paper")
        .replace("ACL format, page budget", f"{persona} format, page budget")
        .replace(
            "body ≤8 pages, conclusion on page 8, references start page 9+",
            venue.page_budget_line(),
        )
    )
    return skill, instructions, files


def _get_current_stage(project_root: Path) -> str:
    state_path = project_root / "research" / "PIPELINE_STATE.json"
    if not state_path.exists():
        return "research"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return data.get("current_stage", "research")
    except (json.JSONDecodeError, OSError):
        return "research"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def _certified_math_synth_setup_override(
    project_root: Path,
    *,
    state: dict[str, Any],
) -> tuple[str, str, Path] | None:
    """Return a project-local math_synth override for a stale speedrun setup route.

    Some migrated projects can carry Manager-owned ``PIPELINE_STATE`` fields from
    a generic speedrun setup while their project-local setup packet has already
    certified the math_synth route.  The unqualified checker should consume that
    certification instead of demanding irrelevant speedrun ``baseline/`` and
    ``reference/`` artifacts.  This is read-only and intentionally does not
    mutate stage authority state.
    """
    vertical = str(state.get("vertical") or "").strip().lower().split("-needed", 1)[0]
    current_stage = str(state.get("current_stage") or "").strip().lower()
    declared_stage = str(state.get("stage") or "").strip().lower()
    if vertical != "speedrun" or current_stage != "setup":
        return None
    if declared_stage and declared_stage != "optimize":
        return None

    candidates = [project_root]
    projects_dir = project_root / "projects"
    if projects_dir.is_dir():
        try:
            children = sorted(
                p.parent.parent
                for p in projects_dir.glob("*/research/MANAGER_SETUP_ACCEPTANCE.md")
                if p.is_file()
            )
        except OSError:
            children = []
        candidates.extend(children)

    accepted_roots: list[Path] = []
    for candidate_root in candidates:
        acceptance = candidate_root / "research" / "MANAGER_SETUP_ACCEPTANCE.md"
        if not acceptance.exists():
            continue
        if not (candidate_root / "MISSION.md").exists():
            continue
        if not (candidate_root / "run_eval.py").exists():
            continue
        if not (candidate_root / "attempts").is_dir():
            continue
        try:
            text = acceptance.read_text(encoding="utf-8").lower()
        except OSError:
            continue

        required_markers = (
            "math_synth setup gate is accepted",
            "explicit math_synth checker",
            "2 shell pass, 0 shell fail",
        )
        if not all(marker in text for marker in required_markers):
            continue
        if "speedrun" not in text or "baseline" not in text or "reference" not in text:
            continue
        accepted_roots.append(candidate_root)

    if len(accepted_roots) != 1:
        return None

    return "math_synth", "optimize", accepted_roots[0]


def _format_blockers(payload: dict[str, Any], *, max_items: int = 4) -> str:
    blockers = payload.get("blockers")
    if not isinstance(blockers, list) or not blockers:
        return ""
    rendered: list[str] = []
    for item in blockers[:max_items]:
        if not isinstance(item, dict):
            continue
        family = item.get("family_id")
        bid = item.get("id")
        message = item.get("message")
        prefix = ".".join(str(part) for part in (family, bid) if part)
        rendered.append(f"{prefix}: {message}" if message else prefix)
    return "; ".join(part for part in rendered if part)


def _benchmark_external_findings(project_root: Path) -> list[str]:
    findings: list[str] = []
    provenance = _read_json(project_root / "experiments" / "BENCHMARK_PROVENANCE.json")
    if isinstance(provenance, dict):
        viability = provenance.get("plan_viability")
        if isinstance(viability, dict):
            status = viability.get("status")
            if status == "blocked_plan_stage_benchmark_package_viability":
                reason = viability.get("reason") or "benchmark package viability is blocked"
                findings.append(f"plan viability is blocked: {reason}")
            count = viability.get("local_authentic_scored_family_count")
            minimum = viability.get("minimum_required_family_count")
            if isinstance(count, int) and isinstance(minimum, int) and count < minimum:
                findings.append(
                    "local authentic scored benchmark family count below minimum: "
                    f"{count} < {minimum}"
                )

    gate_files = (
        ("benchmark access review", project_root / "experiments" / "BENCHMARK_ACCESS_REVIEW.json"),
        ("benchmark artifact bundle", project_root / "experiments" / "BENCHMARK_ARTIFACT_BUNDLE_STATUS.json"),
        ("benchmark evaluator authenticity", project_root / "experiments" / "BENCHMARK_EVALUATOR_AUTHENTICITY.json"),
    )
    for label, path in gate_files:
        payload = _read_json(path)
        if not isinstance(payload, dict) or "passed" not in payload:
            continue
        if payload.get("passed") is False:
            details = _format_blockers(payload)
            findings.append(f"{label} is blocked" + (f": {details}" if details else ""))
    return findings


def _plan_outline_findings(project_root: Path) -> list[str]:
    """Blocking draft-outline issues (missing / underfilled) as findings.

    The draft-first contract requires ``paper/DRAFT_OUTLINE.md`` to be
    filled by the end of the plan stage so downstream figures/experiments
    fill declared placeholders instead of being invented ad-hoc. We surface
    the validator's ``missing`` / ``unfilled`` issues as findings here so
    they flow through the same M0.7 bounded downgrade as the other
    paper-pipeline-readiness findings: fail-closed in normal mode, advisory
    under ``--bounded``. Soft (``incomplete``) issues are intentionally not
    returned — the validator is permissive by design.
    """
    try:
        from argus_skill.verticals.research.draft_outline import load_outline, validate_outline
    except Exception:
        return []
    issues = validate_outline(load_outline(project_root))
    return [
        f"draft outline: {issue.message}"
        for issue in issues
        if issue.severity in ("missing", "unfilled")
    ]


def _blocked_pipeline_findings(project_root: Path, *, requested_stage: str) -> list[str]:
    findings: list[str] = []
    state = _read_json(project_root / "research" / "PIPELINE_STATE.json")
    if isinstance(state, dict):
        if state.get("status") == "blocked":
            reason = state.get("last_gate", {}).get("reason") if isinstance(state.get("last_gate"), dict) else ""
            findings.append(f"pipeline status is blocked: {reason or 'no reason recorded'}")
        stages = state.get("stages")
        if isinstance(stages, dict):
            for stage_name in {str(state.get("current_stage") or ""), requested_stage}:
                if not stage_name:
                    continue
                stage_payload = stages.get(stage_name)
                if isinstance(stage_payload, dict) and stage_payload.get("status") == "blocked":
                    reason = stage_payload.get("reason") or stage_payload.get("gate") or "no reason recorded"
                    findings.append(f"stage {stage_name!r} status is blocked: {reason}")

    findings.extend(_benchmark_external_findings(project_root))
    if requested_stage == "plan":
        findings.extend(_plan_outline_findings(project_root))
    return findings


def _pipeline_stage_fields_clean(project_root: Path) -> bool:
    """Return whether PIPELINE_STATE.json has no tracked git diff."""
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(project_root),
                "diff",
                "--quiet",
                "--",
                "research/PIPELINE_STATE.json",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return True


def _positive_evidence_rollback_packet(
    project_root: Path,
    *,
    requested_stage: str,
    current_stage: str,
    stage_order: list[str],
) -> dict[str, Any] | None:
    if requested_stage not in {"draft", "review", "submission"}:
        return None
    if current_stage != requested_stage:
        return None

    evidence_files = {
        "analysis_route_decision": "paper/ANALYSIS_ROUTE_DECISION.json",
        "evidence_bundle": "experiments/run_stage/EVIDENCE_BUNDLE.json",
        "manager_action_request": "research/MANAGER_ACTION_REQUEST.json",
        "pipeline_state": "research/PIPELINE_STATE.json",
        "run_stage_routing_request": "experiments/run_stage/RUN_STAGE_ROUTING_REQUEST.json",
    }
    loaded: dict[str, dict[str, Any]] = {}
    for key, rel in evidence_files.items():
        path = project_root / rel
        if not path.exists():
            return None
        payload = _read_json(path)
        if payload is None:
            return None
        loaded[key] = payload

    analysis = loaded["analysis_route_decision"]
    manager_request = loaded["manager_action_request"]
    evidence_bundle = loaded["evidence_bundle"]
    if analysis.get("earliest_broken_stage") != "run":
        return None
    if manager_request.get("earliest_broken_stage") != "run":
        return None
    if manager_request.get("requested_action") != "rollback_stage_to_run":
        return None
    if analysis.get("engineer_modified_pipeline_stage_fields") is not False:
        return None
    if manager_request.get("engineer_modified_pipeline_stage_fields") is not False:
        return None

    allowed = evidence_bundle.get("paper_evidence_allowed_values")
    if not isinstance(allowed, list) or not allowed or any(value is True for value in allowed):
        return None
    blockers = evidence_bundle.get("full_scale_blockers")
    if not isinstance(blockers, list) or not blockers:
        return None

    try:
        current_idx = stage_order.index(current_stage)
        rollback_idx = stage_order.index("run")
    except ValueError:
        return None
    if rollback_idx >= current_idx:
        return None

    if not _pipeline_stage_fields_clean(project_root):
        return None

    return {
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "current_stage": current_stage,
        "earliest_broken_stage": "run",
        "evidence_files": evidence_files,
        "manager_action_required": "rollback_stage_to_run",
        "outcome": "MANAGER_BLOCKED",
        "pipeline_stage_fields_clean": True,
        "requested_stage": requested_stage,
        "rollback_target": "run",
        "status": "rollback-accepted",
    }


def _existing_manager_blocked_packet_is_valid(
    project_root: Path,
    *,
    requested_stage: str,
    current_stage: str,
    stage_order: list[str],
) -> bool:
    payload = _read_json(project_root / "research" / "STAGE_CHECK_MANAGER_BLOCKED.json")
    if payload is None:
        return False
    if payload.get("outcome") != "MANAGER_BLOCKED":
        return False
    if payload.get("status") != "rollback-accepted":
        return False
    if payload.get("requested_stage") != requested_stage:
        return False
    if payload.get("current_stage") != current_stage:
        return False
    if payload.get("earliest_broken_stage") != "run":
        return False
    if payload.get("rollback_target") != "run":
        return False
    if payload.get("manager_action_required") != "rollback_stage_to_run":
        return False
    if payload.get("pipeline_stage_fields_clean") is not True:
        return False
    try:
        current_idx = stage_order.index(current_stage)
        rollback_idx = stage_order.index("run")
    except ValueError:
        return False
    if rollback_idx >= current_idx:
        return False
    evidence_files = payload.get("evidence_files")
    if not isinstance(evidence_files, dict) or not evidence_files:
        return False
    for rel in evidence_files.values():
        if not isinstance(rel, str) or not rel:
            return False
        if not (project_root / rel).exists():
            return False
    return True


def _maybe_accept_manager_blocked_rollback(
    project_root: Path,
    *,
    requested_stage: str,
    current_stage: str,
    stage_order: list[str],
    bounded: bool,
) -> bool:
    default_submission_packet = (
        requested_stage == current_stage == "submission"
        and _existing_manager_blocked_packet_is_valid(
            project_root,
            requested_stage=requested_stage,
            current_stage=current_stage,
            stage_order=stage_order,
        )
    )
    if not bounded and not default_submission_packet:
        return False
    packet = _positive_evidence_rollback_packet(
        project_root,
        requested_stage=requested_stage,
        current_stage=current_stage,
        stage_order=stage_order,
    )
    if packet is None:
        return False
    out_path = project_root / "research" / "STAGE_CHECK_MANAGER_BLOCKED.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("🧭 MANAGER_BLOCKED / rollback-accepted")
    print(f"   requested_stage={packet['requested_stage']}")
    print(f"   current_stage={packet['current_stage']}")
    print(f"   earliest_broken_stage={packet['earliest_broken_stage']}")
    print(f"   rollback_target={packet['rollback_target']}")
    print(f"   manager_action_required={packet['manager_action_required']}")
    print(f"   evidence={out_path.relative_to(project_root)}")
    return True


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="stage-check")
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--stage", default=None)
    # WHY M0.7: bounded diagnostic/survey missions cannot satisfy broad
    # paper-pipeline benchmark readiness; this flag downgrades only those
    # state findings while structural anti-fraud gates still block below.
    parser.add_argument("--bounded", action="store_true")
    # Vertical selector: which vertical's stage_list / shell_checks /
    # reviewer_checklists to use. Defaults to "research" for backward
    # compatibility (the only vertical that existed before vertical-split).
    # Auto-detected from research/PIPELINE_STATE.json `vertical` field if
    # present and `--vertical` is not given.
    parser.add_argument(
        "--vertical", default=None,
        help="Vertical to load stages from (research|speedrun|...). "
             "Defaults to PIPELINE_STATE.vertical field or 'research'.",
    )
    args = parser.parse_args()

    root = args.project_root.resolve()
    python = sys.executable

    # Resolve vertical: CLI flag > PIPELINE_STATE.json `vertical` field > "research"
    vertical_name = args.vertical
    state = _read_json(root / "research" / "PIPELINE_STATE.json") or {}
    override: tuple[str, str, Path] | None = None
    if vertical_name is None:
        vertical_name = state.get("vertical") or "research"
        # Strip "speedrun-needed" / "research-needed" sentinels back to bare name
        vertical_name = str(vertical_name).split("-needed", 1)[0]
        if args.stage is None:
            override = _certified_math_synth_setup_override(root, state=state)
            if override is not None:
                vertical_name, override_stage, override_root = override
                args.stage = override_stage
                root = override_root.resolve()
                state = _read_json(root / "research" / "PIPELINE_STATE.json") or {}

    stage = args.stage or _get_current_stage(root)

    # Late-bind the stage tables so non-default verticals don't pull paper
    # imports they don't need. Module-level re-exports (STAGE_ORDER etc.)
    # remain pointed at the research vertical for backward compat.
    global STAGE_CHECKS, STAGE_ORDER, REVIEWER_CHECKLISTS  # noqa: PLW0603
    # Use the canonical resolver — the SAME one the runtime uses
    # (supervisor/_core.py, loop.py, _runtime.py). It resolves both packaged
    # verticals AND project-local DATA domains (research/DOMAINS/<name>.json),
    # returning a duck-typed shim that exposes STAGE_CHECKS/STAGE_ORDER/
    # REVIEWER_CHECKLISTS. Raw importlib saw ONLY packaged verticals, so
    # stage_check crashed ("unknown vertical") on every data-domain vertical the
    # runtime resolves fine — the acceptance gate could never run for them.
    # 用规范解析器(与运行时一致):兼容打包 vertical 与项目本地 data-domain,否则
    # data-domain vertical 运行时能解析、stage_check 却崩,验收门永远跑不起来。
    from ..verticals._base import load_vertical
    try:
        vmod = load_vertical(vertical_name, project_root=root)
    except Exception as exc:  # noqa: BLE001 — a real vertical whose module errored
        print(f"❌ vertical {vertical_name!r} failed to load: {exc}", file=sys.stderr)
        return 2
    STAGE_CHECKS = vmod.STAGE_CHECKS
    STAGE_ORDER = vmod.STAGE_ORDER
    REVIEWER_CHECKLISTS = vmod.REVIEWER_CHECKLISTS

    # venue_profiles is research-vertical-specific; skip it for other verticals.
    if vertical_name == "research":
        from argus_skill.skills.venue_profiles import resolve_venue_profile
        venue = resolve_venue_profile(root)
    else:
        venue = None

    print(f"📋 Stage: {stage}  (vertical: {vertical_name})")
    print()

    current_pipeline_stage = _get_current_stage(root)
    if _maybe_accept_manager_blocked_rollback(
        root,
        requested_stage=stage,
        current_stage=current_pipeline_stage,
        stage_order=[str(item).strip().lower() for item in STAGE_ORDER],
        bounded=args.bounded,
    ):
        return 0

    # 1. Run shell checks
    checks = STAGE_CHECKS.get(stage, [])
    passed = 0
    failed = 0
    for desc, cmd in checks:
        cmd = cmd.replace("{python}", python)
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=120, cwd=str(root),
            )
            if result.returncode == 0:
                print(f"  ✅ {desc}")
                passed += 1
            else:
                output = (result.stdout + result.stderr).strip()
                print(f"  ❌ {desc}")
                if output:
                    for line in output.splitlines():
                        print(f"     {line}")
                failed += 1
        except subprocess.TimeoutExpired:
            print(f"  ⏰ {desc} (timeout)")
            failed += 1

    blocked_findings = _blocked_pipeline_findings(root, requested_stage=stage)
    # WHY M0.7: the root cause was bounded missions looping because
    # paper-pipeline blocked state was counted as a hard per-round failure.
    # In bounded mode it is advisory only; structural gates are unchanged.
    blocked_state_fail = 0 if args.bounded else len(blocked_findings)
    bounded_state_advisory = len(blocked_findings) if args.bounded else 0
    if blocked_findings:
        print()
        print(
            "📋 Advisory paper-pipeline state:"
            if args.bounded
            else "🚫 Fail-closed pipeline state:"
        )
        for finding in blocked_findings:
            print(f"  {'📋' if args.bounded else '❌'} {finding}")

    # 2. Run automated F4 (structural) + F3 (advisory) gates that apply
    #    at this stage. STRUCTURAL gate failures count into the round
    #    exit code — they are anti-fraud / provenance guards (e.g. broken
    #    evidence chains). ADVISORY findings (mediocrity facts) NEVER
    #    count — they are facts the reviewer reads to make their own
    #    judgment. The gate map + kind lives in
    #    argus_skill.skills.automated_gates.{STAGE_GATES,GATE_KINDS}.
    from argus_skill.skills.automated_gates import (
        run_stage_gates,
    )

    gate_results = run_stage_gates(
        root,
        stage=stage,
        proposed_condition=os.environ.get("ARGUS_SKILL_PROPOSED_CONDITION") or None,
        baseline_condition=os.environ.get("ARGUS_SKILL_BASELINE_CONDITION") or None,
    )
    structural_block = 0
    advisory_count = bounded_state_advisory
    structural_pass = 0
    structural_fail = 0
    if gate_results:
        print()
        print(f"🛡  Automated gates for stage '{stage}':")
        for gate in gate_results:
            if gate.kind == "advisory":
                mark = "📋"  # advisory — surface, never block
                advisory_count += 1
            elif gate.passed:
                mark = "✅"
                structural_pass += 1
            else:
                mark = "❌"
                structural_fail += 1
                if gate.is_blocking:
                    structural_block += 1
            print(f"  {mark} {gate.name} ({gate.kind}) — {gate.summary}")
            if gate.detail:
                for line in gate.detail.splitlines():
                    print(f"     {line}")

    # 3. Output reviewer checklist for critical stages
    #    Reviewer is a codex agent — it reads the skill and files itself.
    reviewer_checklist = _reviewer_checklist_for(stage, venue)
    if reviewer_checklist is not None:
        skill_name, instructions, files = reviewer_checklist
        print()
        print(f"📋 REVIEWER CHECKLIST for stage '{stage}'")
        print(f"   Load skill: argus_builtin_skills/{skill_name}")
        print(f"   Read and review: {', '.join(files)}")
        print()
        print(instructions)

    # Exit code: shell-check failures + STRUCTURAL gate failures only.
    # Advisory findings (mediocrity facts) never appear here.
    total_failed = failed + structural_block + blocked_state_fail
    print(
        f"\n{'✅' if total_failed == 0 else '❌'} "
        f"{passed} shell pass, {failed} shell fail, "
        f"{structural_pass} structural-gate pass, "
        f"{structural_fail} structural-gate fail, "
        f"{blocked_state_fail} fail-closed state finding(s), "
        f"{advisory_count} advisory finding(s) "
        f"(reviewer rules)"
    )
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
