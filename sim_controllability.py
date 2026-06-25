"""Deterministic simulation: prove the agent system is CONTROLLABLE after the
livelock (A/B'/C) + operator-escalation (no-progress streak) + notify-surfacing fixes.

It drives a real LifeSupervisor through the two failure modes we actually hit on
the nanochat-B200 mission and shows: nothing loops invisibly; every stuck state
and every escalation reaches the operator's notify channel; the human is pulled
in after a bounded number of hollow missions.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import argus_skill.life.notify as notify_mod
from argus_skill.life.memory import LifeMemory
from argus_skill.life.supervisor import LifeSupervisor, LifeSupervisorConfig, LifeBudget
from argus_skill.life.supervisor._core import (
    _VERIFICATION_PROBE_AFTER_IDLE_CYCLES as K,
    _STALL_ESCALATION_AFTER_NO_PROGRESS_MISSIONS as N,
)
from argus_skill.planner import PlannerVerdict

# ---- capture what would reach the operator (Telegram/webhook) ----
operator_feed: list[tuple[str, str]] = []
_orig_dispatch = notify_mod.dispatch_journal_entry


def _recording_dispatch(entry, **kw):
    kind = getattr(entry, "kind", "?")
    if kind in notify_mod.DEFAULT_NOTIFY_KINDS:
        emoji, label = notify_mod._KIND_LABELS.get(kind, ("🔔", kind))
        operator_feed.append((kind, f"{emoji} {label}: {getattr(entry, 'summary', '')[:70]}"))


notify_mod.dispatch_journal_entry = _recording_dispatch


def make_sup(tmp: Path) -> LifeSupervisor:
    mem = LifeMemory.open(tmp / "life")
    proj = tmp / "project"
    proj.mkdir()
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(),
        poll_interval_seconds=0.01,
        continuous=True,
        continuous_objective="minimize val_bpb on B200 (shared pod)",
        open_ended=False,
        full_emnlp_gate=False,
        project_worktree=proj,
    )

    class _Sink:
        def handle_event(self, event: dict) -> None:  # noqa: D401
            pass

    class _Runner:
        pass

    sup = LifeSupervisor(memory=mem, runner=_Runner(), sink=_Sink(), config=cfg)
    sup._vertical_resolved = True
    sup.planner_runner = object()
    return sup


def count(sup, kind):
    return sum(1 for e in sup.memory.journal.all() if e.kind == kind)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="argus_sim_"))
    sup = make_sup(tmp)

    # The planner is stuck: it keeps concluding `waiting` on the SAME blocker,
    # rewriting the reason every cycle (the real failure mode).
    cycle = {"n": 0}

    def _waiting_planner(_planner, **kwargs):
        cycle["n"] += 1
        # surface whether B' injected the current-reality staleness note
        rcs = kwargs.get("runtime_change_summary", "") or ""
        _waiting_planner.last_note = "CURRENT-REALITY CHECK" in rcs
        r = f"sol-execbench neighbor present; fresh audit #{cycle['n']} at pod"
        return PlannerVerdict(project_done=False, reason=r, waiting=True, waiting_reason=r)

    _waiting_planner.last_note = False
    import argus_skill.planner as planner_pkg
    planner_pkg.Planner.plan_next = _waiting_planner  # type: ignore[assignment]

    print("=" * 78)
    print("SIMULATION — agent-system controllability (livelock + over-caution fixes)")
    print(f"  thresholds: verification-probe at K={K} idle cycles, "
          f"operator escalation at N={N} no-progress missions")
    print("=" * 78)

    print("\nPHASE 1 — planner stuck `waiting` on a stale blocker (drive 9 cycles):")
    probes = 0
    for _ in range(9):
        out = sup._plan_next_work()
        c = cycle["n"]
        if out is True:  # a verification probe was dispatched this cycle
            probes += 1
            print(f"  cycle {c}: waiting -> 🔬 VERIFICATION PROBE dispatched (C); "
                  f"idle counter reset")
        else:
            note = " [B': current-reality note injected]" if _waiting_planner.last_note else ""
            print(f"  cycle {c}: waiting (journal planner_waiting total={count(sup,'planner_waiting')})"
                  f"{note}")

    print(f"\n  -> A (no echo chamber): 9 waiting cycles wrote only "
          f"{count(sup,'planner_waiting')} planner_waiting journal entr(ies).")
    print(f"  -> B' (perceive reality): staleness note injected once idle>=2.")
    print(f"  -> C (forced reality-check): {probes} verification probe(s) dispatched "
          f"(every K={K} idle cycles).")

    print("\nPHASE 2 — agent completes missions but the L2 reviewer judges NO forward")
    print("          progress (the over-caution / no-score-blocked loop):")
    for i in range(1, N + 2):
        before = count(sup, "planner_stall_escalation")
        sup._update_no_progress_streak(kind="mission_complete",
                                       report={"forward_progress": False})
        after = count(sup, "planner_stall_escalation")
        fired = " -> 🚨 STALL ESCALATION (operator pulled in)" if after > before else ""
        print(f"  mission {i} (forward_progress=false): streak="
              f"{sup._consecutive_no_progress_missions}{fired}")

    print("\n  -> a genuinely productive mission resets the streak:")
    sup._update_no_progress_streak(kind="mission_complete", report={"forward_progress": True})
    print(f"     after a forward_progress=true mission: streak="
          f"{sup._consecutive_no_progress_missions}")

    print("\nOPERATOR NOTIFICATION STREAM (what reaches Telegram/webhook):")
    seen = set()
    for kind, line in operator_feed:
        if kind not in seen:
            seen.add(kind)
            print(f"  {line}")
    print(f"  (total {len(operator_feed)} notifications; distinct kinds: {sorted(seen)})")

    # ---- controllability assertions ----
    print("\nCONTROLLABILITY ASSERTIONS:")
    checks = []

    def chk(name, ok):
        checks.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")

    chk("no silent echo-chamber: many waiting cycles -> <=1 journal entry per heartbeat",
        count(sup, "planner_waiting") <= 1)
    chk("harness forces a reality-check probe when stuck (C fired)",
        probes >= 1 and count(sup, "planner_verification_probe") >= 1)
    chk("planner perceives current reality (B' staleness note injected)",
        _waiting_planner.last_note is True)
    chk(f"hollow-work loop escalates to operator after N={N} missions",
        count(sup, "planner_stall_escalation") >= 1)
    chk("real progress resets the no-progress streak",
        sup._consecutive_no_progress_missions == 0)
    chk("every stuck/escalation state reaches the operator notify channel",
        {"planner_waiting", "planner_verification_probe", "planner_stall_escalation"} <= seen)

    ok = all(checks)
    print("\n" + ("ALL CONTROLLABILITY PROPERTIES HOLD ✅" if ok
                  else "SOME PROPERTIES FAILED ❌"))
    notify_mod.dispatch_journal_entry = _orig_dispatch
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
