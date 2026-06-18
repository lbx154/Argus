"""Process self-distillation — STEP 1: deterministic, read-only process ledger.

Self-improvement of PROCESS (how the agent works), never of OUTCOME (what counts as
winning — the metric/verifier stays frozen and external). This module is the passive,
vertical-AGNOSTIC instrumenter: it reads a project's ``events.jsonl`` and extracts a
structured process ledger so a meta-critic has hard features to reason over instead of
raw logs. It emits NOTHING actionable; pure observation.

The raw material already exists in every project's event stream — the reviewer emits
``failure_cause`` + ``mission_lesson`` per round, the planner emits ``forward_progress``,
checks emit pass/fail, the runner emits stalls — but nothing consumes it. This turns that
stream into deduplicated, quantified process signal.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# "checks: 3/5 pass"
_CHECKS_RE = re.compile(r"(\d+)\s*/\s*(\d+)\s*pass")
# planner_report / checklist are Python-repr strings; pull the booleans robustly.
_FP_TRUE_RE = re.compile(r"['\"]forward_progress['\"]\s*:\s*True")
_FP_FALSE_RE = re.compile(r"['\"]forward_progress['\"]\s*:\s*False")
_UNSAT_RE = re.compile(r"['\"]satisfied['\"]\s*:\s*False")


def _b(v) -> bool:
    return str(v).strip().lower() in ("true", "1", "yes")


def _f(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


@dataclass
class RoundFeat:
    idx: int
    checks_pass: int | None = None
    checks_total: int | None = None
    review_status: str = ""          # continue | done | blocked
    failure_cause: str = ""          # skill_gap | execution_mistake | ...
    forward_progress: bool | None = None
    n_unsatisfied: int | None = None  # checklist items still false
    stalled: bool = False
    failure_nudge: bool = False
    mission_lesson: str = ""
    process_lesson: str = ""

    @property
    def checks_failed(self) -> bool:
        return self.checks_total is not None and (self.checks_pass or 0) < self.checks_total

    @property
    def fp_contradiction(self) -> bool:
        # the crux incentive contradiction: the planner signal says "forward progress"
        # while the round is NOT actually done (still continue/blocked, or checklist unmet).
        if self.forward_progress is not True:
            return False
        if self.review_status and self.review_status != "done":
            return True
        return bool(self.n_unsatisfied)


@dataclass
class MissionFeat:
    mission_id: str = ""
    success: bool = False
    status: str = ""
    rounds: int = 0
    cost_usd: float = 0.0
    round_feats: list[RoundFeat] = field(default_factory=list)

    @property
    def max_check_fail_streak(self) -> int:
        best = cur = 0
        for r in self.round_feats:
            if r.checks_failed:
                cur += 1
                best = max(best, cur)
            else:
                cur = 0
        return best

    @property
    def n_stalls(self) -> int:
        return sum(1 for r in self.round_feats if r.stalled)

    @property
    def n_failure_nudges(self) -> int:
        return sum(1 for r in self.round_feats if r.failure_nudge)

    @property
    def n_fp_contradictions(self) -> int:
        return sum(1 for r in self.round_feats if r.fp_contradiction)

    @property
    def failure_causes(self) -> list[str]:
        return [r.failure_cause for r in self.round_feats if r.failure_cause]

    @property
    def mission_lessons(self) -> list[str]:
        return [r.mission_lesson for r in self.round_feats if r.mission_lesson]

    @property
    def process_lessons(self) -> list[str]:
        return [r.process_lesson for r in self.round_feats if r.process_lesson]


def _iter_events(events_path: Path):
    with events_path.open(errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def extract_mission_feats(events_path: Path) -> list[MissionFeat]:
    """Segment the event stream into missions and extract per-round process features.

    Robust to interleaving: a round's signals (checks.done, round.review.completed,
    round.stall, engineer.failure_nudge) are bucketed by their ``round``/``round_index``
    into the CURRENT open mission.
    """
    missions: list[MissionFeat] = []
    cur: MissionFeat | None = None
    rounds: dict[int, RoundFeat] = {}

    def _flush():
        nonlocal cur, rounds
        if cur is not None:
            cur.round_feats = [rounds[k] for k in sorted(rounds)]
            missions.append(cur)
        cur, rounds = None, {}

    def _round(idx: int) -> RoundFeat:
        if idx not in rounds:
            rounds[idx] = RoundFeat(idx=idx)
        return rounds[idx]

    for ev in _iter_events(events_path):
        t = ev.get("type") or ev.get("event") or ""
        if t == "life.mission.started":
            _flush()
            cur = MissionFeat(mission_id=str(ev.get("item_id", "")))
        elif t == "life.mission.completed":
            if cur is None:
                cur = MissionFeat()
            cur.mission_id = cur.mission_id or str(ev.get("item_id", ""))
            cur.success = _b(ev.get("success"))
            cur.status = str(ev.get("status", ""))
            cur.rounds = _i(ev.get("rounds"))
            cur.cost_usd = _f(ev.get("cost_usd"))
            _flush()
        elif t == "checks.done":
            r = _round(_i(ev.get("round")))
            m = _CHECKS_RE.search(str(ev.get("text", "")))
            if m:
                r.checks_pass, r.checks_total = int(m.group(1)), int(m.group(2))
        elif t == "round.review.completed":
            r = _round(_i(ev.get("round_index", ev.get("round"))))
            r.review_status = str(ev.get("status", ""))
            r.failure_cause = str(ev.get("failure_cause", "") or "")
            r.mission_lesson = str(ev.get("mission_lesson", "") or "").strip()
            r.process_lesson = str(ev.get("process_lesson", "") or "").strip()
            pr = str(ev.get("planner_report", ""))
            if _FP_TRUE_RE.search(pr):
                r.forward_progress = True
            elif _FP_FALSE_RE.search(pr):
                r.forward_progress = False
            cl = str(ev.get("checklist", ""))
            if cl:
                r.n_unsatisfied = len(_UNSAT_RE.findall(cl))
        elif t == "round.stall":
            _round(_i(ev.get("round_index", ev.get("round")))).stalled = True
        elif t == "engineer.failure_nudge":
            _round(_i(ev.get("round"))).failure_nudge = True

    _flush()
    return [m for m in missions if m.mission_id or m.round_feats]


def extract_process_ledger(project_dir: str | Path) -> dict:
    """Read one project's events.jsonl → a structured, quantified process ledger.

    Vertical-agnostic: depends only on the universal life/round/check/review event
    schema, not on any task's artifact layout.
    """
    project_dir = Path(project_dir)
    events_path = project_dir / "events.jsonl"
    if not events_path.exists():
        return {"project": project_dir.name, "n_missions": 0, "missions": []}

    feats = extract_mission_feats(events_path)
    n = len(feats)
    n_success = sum(1 for m in feats if m.success)
    cause_hist = Counter(c for m in feats for c in m.failure_causes)
    # recurring lessons: same lesson (normalized prefix) emitted across >=2 missions =
    # a process gap the system kept re-discovering but never internalized.
    lesson_missions: dict[str, set[str]] = {}
    for m in feats:
        for les in m.mission_lessons:
            key = re.sub(r"\s+", " ", les.lower())[:120]
            lesson_missions.setdefault(key, set()).add(m.mission_id)
    recurring = {k: len(v) for k, v in lesson_missions.items() if len(v) >= 2}
    # the PRIMARY per-mission self-distillation channel: reviewer-judged process lessons.
    proc_lessons = [pl for m in feats for pl in m.process_lessons]

    return {
        "project": project_dir.name,
        "n_missions": n,
        "success_rate": round(n_success / n, 3) if n else 0.0,
        "total_cost_usd": round(sum(m.cost_usd for m in feats), 4),
        "mean_rounds": round(sum(m.rounds for m in feats) / n, 2) if n else 0.0,
        # process-waste signals (the distillation inputs)
        "missions_with_stalls": sum(1 for m in feats if m.n_stalls),
        "missions_with_check_fail_streak_ge3": sum(1 for m in feats if m.max_check_fail_streak >= 3),
        "missions_with_failure_nudge": sum(1 for m in feats if m.n_failure_nudges),
        "fp_contradiction_rounds": sum(m.n_fp_contradictions for m in feats),
        "failure_cause_hist": dict(cause_hist.most_common()),
        "n_mission_lessons": sum(len(m.mission_lessons) for m in feats),
        "n_process_lessons": len(proc_lessons),
        "process_lessons": proc_lessons,
        "recurring_lessons": dict(sorted(recurring.items(), key=lambda kv: -kv[1])[:10]),
        "missions": [
            {
                "id": m.mission_id, "success": m.success, "status": m.status,
                "rounds": m.rounds, "cost_usd": round(m.cost_usd, 4),
                "max_check_fail_streak": m.max_check_fail_streak,
                "n_stalls": m.n_stalls, "n_failure_nudges": m.n_failure_nudges,
                "n_fp_contradictions": m.n_fp_contradictions,
                "failure_causes": m.failure_causes,
            }
            for m in feats
        ],
    }


def aggregate_ledgers(project_dirs: list[str | Path]) -> dict:
    """Merge many per-project ledgers into ONE corpus-level process view — the real
    distillation input the meta-critic reasons over. Recurring signals that survive
    across projects (not one fluke run) are what a process fix should target.
    """
    feats: list[MissionFeat] = []
    lesson_projects: dict[str, set[str]] = {}
    proc_lessons: list[str] = []
    cause_hist: Counter = Counter()
    n_projects = 0
    for pd in project_dirs:
        pd = Path(pd)
        ep = pd / "events.jsonl"
        if not ep.exists():
            continue
        n_projects += 1
        mf = extract_mission_feats(ep)
        feats.extend(mf)
        for m in mf:
            for c in m.failure_causes:
                cause_hist[c] += 1
            for les in m.mission_lessons:
                key = re.sub(r"\s+", " ", les.lower())[:120]
                lesson_projects.setdefault(key, set()).add(pd.name)
            proc_lessons.extend(m.process_lessons)

    n = len(feats)
    if not n:
        return {"n_projects": n_projects, "n_missions": 0}
    failed = [m for m in feats if not m.success]
    # cross-PROJECT recurring lessons: the same lesson re-discovered in >=2 different
    # projects is a SYSTEMIC un-internalized process gap, not a one-off.
    recurring = {k: len(v) for k, v in lesson_projects.items() if len(v) >= 2}

    def _rate(pred) -> float:
        return round(sum(1 for m in feats if pred(m)) / n, 3)

    return {
        "n_projects": n_projects,
        "n_missions": n,
        "success_rate": round(sum(1 for m in feats if m.success) / n, 3),
        "total_cost_usd": round(sum(m.cost_usd for m in feats), 2),
        # dominant process pathologies (quantified, corpus-wide)
        "fp_contradiction_rate": _rate(lambda m: m.n_fp_contradictions > 0),
        "stall_rate": _rate(lambda m: m.n_stalls > 0),
        "check_fail_streak_ge3_rate": _rate(lambda m: m.max_check_fail_streak >= 3),
        "failure_nudge_rate": _rate(lambda m: m.n_failure_nudges > 0),
        "failure_cause_hist": dict(cause_hist.most_common()),
        "n_mission_lessons": sum(len(m.mission_lessons) for m in feats),
        "n_process_lessons": len(proc_lessons),
        "process_lessons": proc_lessons[:200],
        # the highest-value distillation targets: lessons the system kept re-learning
        "cross_project_recurring_lessons": dict(
            sorted(recurring.items(), key=lambda kv: -kv[1])[:20]
        ),
        "failed_missions": len(failed),
    }
