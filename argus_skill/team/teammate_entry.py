"""Headless teammate entrypoint.

Run as::

    python -m argus_skill.team.teammate_entry --root <team_root> --member-id <id> \
        [--task-id <id>] [--cwd <dir>]

Finds the task this member owns on the shared board and runs ONE headless Argus
engineer mission on that task's objective — **in-process**, reusing the exact
per-mission call the daemon's supervisor makes (``_SkillLoopRunner.execute``)
— heartbeating the board while it runs, then marking the task done/failed and
writing a result shard when the mission returns.

Why in-process (not ``python -m argus_skill ...``): the CLI only offers the
interactive cockpit (dies on EOF, no-op ``rc=0``) or a full
``--daemon-fg`` daemon (acquires the per-project daemon lock + runs its own
planner → would recurse into nested teams). Calling the runner directly gives a
single headless engineer mission with **no cockpit, no daemon lock, no planner,
no recursion**, and needs no project memory. ``life_dir`` only scopes where this
teammate's ``events.jsonl`` is written, so each teammate is isolated.

The resident Curator is the only launcher for this entrypoint; the lead only
forms the durable backlog and sets pool intent.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import task_board


@dataclass(frozen=True)
class TeammateMissionResult:
    success: bool
    status: str
    reason: str = ""
    operator_question: str = ""
    operator_options: tuple[dict, ...] = ()
    last_thread_id: str = ""

    @property
    def waits_for_operator(self) -> bool:
        return bool(self.operator_question) and self.status in {
            "blocked",
            "operator_wait",
            "paused_operator",
        }

    def __bool__(self) -> bool:
        return self.success


def _mission_result(outcome) -> TeammateMissionResult:
    return TeammateMissionResult(
        success=bool(getattr(outcome, "success", False)),
        status=str(getattr(outcome, "status", "") or ""),
        reason=str(getattr(outcome, "stop_reason", "") or ""),
        operator_question=str(getattr(outcome, "operator_question", "") or "").strip(),
        operator_options=tuple(
            dict(option)
            for option in (getattr(outcome, "operator_options", []) or [])
            if isinstance(option, dict)
        ),
        last_thread_id=str(getattr(outcome, "last_thread_id", "") or ""),
    )


def _coerce_mission_result(result) -> TeammateMissionResult:
    if isinstance(result, TeammateMissionResult):
        return result
    return TeammateMissionResult(
        success=bool(result),
        status="done" if result else "error",
        reason="" if result else "teammate mission did not succeed",
    )


@contextmanager
def _temporary_env(name: str, value: str):
    prior = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = prior


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def _build_runner_ns(cwd: str, *, max_rounds: int, paper_mission: bool,
                     stop_event=None) -> argparse.Namespace:
    """Replicate the daemon's runner namespace (life_worker._runner_namespace)."""
    from argus_skill.core import paths as core_paths
    from argus_skill.core.knobs import resolve_role_model

    ns = argparse.Namespace()
    ns.backend = os.environ.get("ARGUS_SKILL_LIFE_BACKEND", "codex")
    ns.engineer_model = resolve_role_model("engineer", role_env="ARGUS_SKILL_ENGINEER_MODEL")
    ns.reviewer_model = resolve_role_model("reviewer", role_env="ARGUS_SKILL_REVIEWER_MODEL")
    ns.engineer_reasoning_effort = os.environ.get("ARGUS_SKILL_ENGINEER_REASONING_EFFORT", "xhigh")
    ns.reviewer_reasoning_effort = os.environ.get("ARGUS_SKILL_REVIEWER_REASONING_EFFORT", "high")
    ns.skills_dir = os.environ.get("ARGUS_SKILL_SKILLS_DIR", str(core_paths.shared_skills_root()))
    ns.workdir = str(cwd)
    ns.max_rounds = int(os.environ.get("ARGUS_SKILL_MAX_ROUNDS", str(max_rounds)))
    ns.plan_mode = os.environ.get("ARGUS_SKILL_PLAN_MODE", "auto")
    ns.plan_model = os.environ.get("ARGUS_SKILL_PLAN_MODEL")
    ns.color = None
    ns.verbose = False
    ns.quiet = True
    # Paper gates default OFF for a teammate (the common optimize case);
    # a paper-fan-out team enables them per teammate via ARGUS_TEAMMATE_PAPER_MISSION.
    ns.paper_mission = _env_bool("ARGUS_TEAMMATE_PAPER_MISSION", paper_mission)
    # Time-box: the runner interrupts the codex mission when this event is set,
    # so a hard task can't hang a teammate for hours.
    if stop_event is not None:
        ns.stop_event = stop_event
    return ns


def run_one_engineer_mission(
    objective: str,
    *,
    cwd: str,
    life_dir: Path,
    paper_mission: bool = False,
    max_rounds: int | None = None,
    timeout_s: float | None = None,
    prelude_context: str = "",
) -> TeammateMissionResult:
    """Run ONE headless engineer mission in-process on ``objective`` in ``cwd``.

    Reuses ``_SkillLoopRunner.execute`` — the exact per-mission call the
    daemon's supervisor makes. No cockpit, no daemon lock, no planner, no
    recursion. Events go to the     isolated ``life_dir``. Returns the full settlement fields needed by the board.

    ``prelude_context`` is the same channel the supervisor uses for a
    mission-level agent: text prepended above the live objective rather than
    folded into it, so the Reviewer still reviews the task and not the briefing.
    Empty for the common case; see ``_vertical_prelude``.

    Positive ``max_rounds`` or ``timeout_s`` values remain available as explicit
    limits; both default to unlimited.
    """
    if max_rounds is None:
        max_rounds = int(os.environ.get("ARGUS_TEAMMATE_MAX_ROUNDS", "0"))
    if timeout_s is None:
        timeout_s = float(os.environ.get("ARGUS_TEAMMATE_TIMEOUT_S", "0"))
    # A teammate's events go to its isolated ``life_dir``, NOT the daemon's
    # ``<global_root>/projects/<fingerprint>/events.jsonl`` that the reviewer's
    # engineer-execution-log audit greps — so that audit would inspect a
    # co-located daemon's shared log and mis-attribute other missions' commands.
    # Disable checkpoint persistence: the audit is then omitted, and a single-shot
    # teammate (no cross-mission continuity) won't collide with sibling teammates
    # on a shared CHECKPOINT.md.
    with _temporary_env("ARGUS_SKILL_CHECKPOINT_PERSIST", "0"):
        watchdog: threading.Timer | None = None
        try:
            from argus_skill.apps._runtime import LifeStderrSink, _SkillLoopRunner
            from argus_skill.life.event_log import JsonlEventSink

            life_dir = Path(life_dir)
            life_dir.mkdir(parents=True, exist_ok=True)
            # Soft time-box: a Timer sets stop_event at timeout_s; the runner
            # polls it between rounds and exits cleanly.
            stop_event = threading.Event()
            if timeout_s > 0:
                watchdog = threading.Timer(timeout_s, stop_event.set)
                watchdog.daemon = True
                watchdog.start()
            ns = _build_runner_ns(
                cwd,
                max_rounds=max_rounds,
                paper_mission=paper_mission,
                stop_event=stop_event,
            )
            runner = _SkillLoopRunner(ns)
            sink = JsonlEventSink(LifeStderrSink(quiet=False), life_dir=life_dir)
            outcome = runner.execute(
                objective=objective, sink=sink, prelude_context=prelude_context,
                # A teammate is not the project's stage authority. Left at the
                # default, ``execute`` runs the Manager's stage-transition pass
                # and that pass WRITES ``<cwd>/.argus/PIPELINE_STATE.json``
                # — and a teammate's ``cwd`` is the project root, because that
                # is what keeps it reading the one shared ledger instead of a
                # private one nobody reads. So the two facts compose into N
                # concurrent workers with write authority over the campaign's
                # stage, each judging the pipeline from the single task it was
                # handed and none of them holding the campaign's own review.
                #
                # Stage authority belongs to the Manager running the mission
                # that dispatched this pool, which decides once against the
                # whole round rather than once per teammate. What is dropped
                # here is only that write: the mission still runs, still
                # reviews, and still reports its verdict back through the board
                # and its result shard, which is what the dispatching Engineer
                # consumes.
                #
                # Not ``skip_stage_transition``: that flag is half of the
                # Planner's review-only node contract and is only honored
                # alongside ``require_independent_review`` on a bounded scope,
                # neither of which a teammate has — so setting it here reads as
                # a fix and silently does nothing.
                holds_stage_authority=False,
            )
        except SystemExit as exc:  # codex extra missing, etc.
            sys.stderr.write(f"teammate_entry: runner unavailable: {exc}\n")
            return TeammateMissionResult(False, "error", str(exc))
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"teammate_entry: mission error: {exc!r}\n")
            return TeammateMissionResult(False, "error", repr(exc))
        finally:
            if watchdog is not None:
                watchdog.cancel()
        return _mission_result(outcome)


def _owned_task(root: Path, member_id: str, task_id: str | None) -> dict | None:
    tasks = task_board.snapshot(root)
    if task_id:
        for x in tasks:
            if x["task_id"] == task_id:
                return x
    for x in tasks:
        if x.get("owner") == member_id:
            return x
    return None


def _heartbeat_loop(root: Path, task_id: str, stop: threading.Event) -> None:
    while not stop.wait(30.0):
        task_board.heartbeat(root, task_id, now=time.time())


def _read_optional_result(expected_target: str | None = None) -> dict:
    """Read an optional ``{metric, mechanism}`` the teammate's mission left at
    ``ARGUS_TEAMMATE_RESULT_FILE`` (operator-wired into the objective). General —
    no metric source is baked into the library; absent/corrupt → empty, so the
    shard records a null metric and the leaderboard simply doesn't rank it.

    Anti-forge (opt-in): when ``ARGUS_TEAMMATE_RESULT_VERIFY_KEY`` is set, the file
    MUST carry a valid Ed25519 signature from the isolated scorer over its result
    fields AND ``correct is True`` — and, when ``expected_target`` is given, its
    signed ``target`` must match it (so a signed result for one kernel cannot be
    replayed as another's). Otherwise it is rejected and NOT banked, so an
    unsandboxed engineer cannot forge its own metric. Off by default → unchanged
    for fleets that have not wired signing (see ``team.result_provenance``)."""
    path = os.environ.get("ARGUS_TEAMMATE_RESULT_FILE", "").strip()
    if not path:
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    verify_key = os.environ.get("ARGUS_TEAMMATE_RESULT_VERIFY_KEY", "").strip()
    if verify_key:
        from . import result_provenance as _rp
        ok = (
            data.get("correct") is True
            and (expected_target is None or data.get("target") == expected_target)
            and _rp.verify_result(data, _rp.read_key(verify_key))
        )
        if not ok:
            sys.stderr.write(
                "teammate_entry: result.json failed provenance verification "
                "(bad/missing signature, correct!=true, or target mismatch) — "
                "NOT banking its metric\n"
            )
            return {}
    return data


def _vertical_prelude(task: dict, *, cwd: str, state_root: Path) -> str:
    """The active vertical's per-mission block for the board task this teammate owns.

    A dispatched teammate is an Engineer mission like any other, and until now
    it was the only one that started without whatever its vertical wanted every
    mission to know. Parallel fan-out is where that costs most: N teammates each
    re-derive the project's shared context because none of them was handed it.

    Routed through ``vertical_mission_prelude`` — the same seam the daemon's
    supervisor uses — so a teammate and the Engineer that dispatched it read one
    project through one resolution, not two that can disagree.

    ``cwd`` serves as both roots. The teammate works in the project tree and the
    Manager's ``PIPELINE_STATE.json`` decision is read relative to that same
    tree, so unlike the supervisor (session artifact root vs. adopted mission
    workdir) there are not two candidates to choose between. The stage is read
    from that state for the same reason the supervisor reads it rather than
    assuming one: a teammate runs inside the project's current stage, and
    passing a blank would tell a stage-sensitive vertical it has no stage when
    it plainly does. ``state_root`` is the teammate's own isolated ``life_dir``:
    a vertical that materializes per-mission scratch from it then gets one per
    teammate, instead of N concurrent siblings racing on a single shared
    directory.

    The board record is passed as the mission with its real fields exposed as
    attributes — nothing synthesized. A vertical that reads a field the board
    does not carry (``acceptance_check``, notably) sees it absent, which is the
    truth, rather than a plausible-looking value invented here.

    Everything is caught and degraded to no prelude. This is a deliberate
    departure from the supervisor, which lets the hook halt the run: there the
    loud failure is the point, because a mission-blind vertical would stay wrong
    for the life of the project and the operator is watching. Here the caller is
    one subordinate worker of many, its parent already fails loudly on the same
    fault, and killing it before it starts would discard real work to report a
    defect that has already been reported.
    """
    try:
        from types import SimpleNamespace

        from ..skills.stage_machine import current_stage
        from ..verticals._base import vertical_mission_prelude

        return vertical_mission_prelude(
            vertical_root=Path(cwd),
            project_root=Path(cwd),
            state_root=Path(state_root),
            stage=current_stage(cwd),
            mission=SimpleNamespace(**task),
        )
    except Exception as exc:  # noqa: BLE001 - a briefing must not cost the mission
        sys.stderr.write(f"teammate_entry: no vertical prelude: {exc!r}\n")
        return ""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="argus_skill.team.teammate_entry")
    p.add_argument("--root", required=True)
    p.add_argument("--member-id", required=True)
    p.add_argument("--task-id", default="")
    p.add_argument("--cwd", default="")
    args = p.parse_args(argv)

    root = Path(args.root)
    task = _owned_task(root, args.member_id, args.task_id or None)
    if task is None:
        sys.stderr.write(f"teammate_entry: no task for {args.member_id}\n")
        return 2
    task_id = task["task_id"]
    objective = task.get("objective", "")
    cwd = args.cwd or os.getcwd()
    # Tell a fresh teammate what has already been tried on this target. This is
    # a no-op until the deterministic leaderboard has at least one attempt.
    from . import leaderboard as _lb

    lb_block = _lb.objective_block(root, task.get("target") or task_id)
    if lb_block:
        objective = lb_block + objective
    operator_answer = str(task.get("operator_answer") or "").strip()
    if operator_answer:
        objective = (
            objective
            + "\n\nThe operator answered the prior blocking question:\n"
            + operator_answer
            + "\nContinue the same task from its persisted artifacts."
        )
    member_safe = args.member_id.replace(":", "_")

    (root / "shards").mkdir(parents=True, exist_ok=True)
    shard = root / "shards" / (member_safe + ".jsonl")

    task_board.heartbeat(root, task_id, now=time.time())
    stop = threading.Event()
    threading.Thread(target=_heartbeat_loop, args=(root, task_id, stop), daemon=True).start()

    life_dir = root / "life" / member_safe
    operator_context = ""
    operator_context_root = os.environ.get("ARGUS_OPERATOR_CONTEXT_DIR", "").strip()
    if operator_context_root:
        from ..core.operator_context import build_operator_context_block

        operator_context, _ = build_operator_context_block(
            "teammate",
            operator_context_root,
            mission_id=task_id,
            consume_once=False,
        )
    launch_prelude = "\n\n".join(
        part
        for part in (
            _vertical_prelude(task, cwd=cwd, state_root=life_dir),
            operator_context,
        )
        if part
    )
    with _temporary_env("ARGUS_SKILL_TEAM_TASK_ID", task_id):
        mission = _coerce_mission_result(
            run_one_engineer_mission(
                objective,
                cwd=cwd,
                life_dir=life_dir,
                timeout_s=float(task.get("timeout_s", 0) or 0) or None,
                prelude_context=launch_prelude,
            )
        )

    stop.set()
    _result = _read_optional_result(expected_target=task.get("target") or task_id)
    _rec = {
        "member_id": args.member_id, "task_id": task_id,
        "target": task.get("target") or task_id, "success": mission.success,
        "status": mission.status,
        "metric": _result.get("metric"), "mechanism": _result.get("mechanism", ""),
    }
    # Carry the target's optimization direction so the leaderboard ranks per-target;
    # omit when the task didn't set it → the leaderboard uses its global default.
    if task.get("lower_is_better") is not None:
        _rec["lower_is_better"] = bool(task["lower_is_better"])
    shard.write_text(json.dumps(_rec) + "\n", encoding="utf-8")
    if mission.success:
        task_board.complete(root, task_id, shard=str(shard))
    elif mission.waits_for_operator:
        task_board.block_for_operator(
            root,
            task_id,
            question=mission.operator_question,
            options=list(mission.operator_options),
            reason=mission.reason or "teammate mission is waiting for an operator decision",
            last_thread_id=mission.last_thread_id,
        )
    else:
        task_board.fail(
            root,
            task_id,
            reason=mission.reason or "teammate mission did not succeed",
        )
    return 0 if mission.success or mission.waits_for_operator else 1


if __name__ == "__main__":
    sys.exit(main())
