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

This is what ``tools/team.py spawn`` launches, so teams work out of the box —
the lead never hand-rolls a launcher.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import task_board


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


_DEFAULT_RESEARCH_PROMPT = (
    "Use the web_search tool to research how the best-known / SOTA approach "
    "handles the problem in the TASK below. Output ONLY a short plain-text summary "
    "(5-12 lines): the SOTA approach, the key technique(s), and the specific "
    "sources/references to build on. Do NOT write code or read local files."
    "\n\nTASK:\n{objective}"
)

_DEFAULT_PROFILE_HEADER = (
    "[LIVE PROFILE — measured just now. The flagged bottlenecks are below. "
    "Address the #1 bottleneck first; do not write a candidate that ignores this "
    "data.]"
)


def _forced_web_research(objective: str, *, cwd: str) -> str:
    """Opt-in pre-mission grounding step (general; gated by env).

    When ``ARGUS_TEAMMATE_FORCE_RESEARCH`` is set, run ONE dedicated
    ``codex exec`` web_search call FIRST — on a clean slate, BEFORE the mission's
    matcher/inherited-context machinery can anchor the model on "continue the
    previous bespoke direction" — and prepend the real findings to the objective
    so the engineer builds on the actual SOTA. Observed problem this fixes: an
    optional "search first" instruction buried in a large mission prompt is
    reliably skipped (the model claims it already knows the SOTA without ever
    calling the tool); a separate forced call cannot be faked. Best-effort: any
    failure/timeout degrades to the unmodified objective.
    """
    flag = os.environ.get("ARGUS_TEAMMATE_FORCE_RESEARCH", "").strip().lower()
    if flag in ("", "0", "false", "no"):
        return objective
    agent_bin = os.environ.get("ARGUS_TEAMMATE_RESEARCH_CODEX", "codex")
    timeout_s = float(os.environ.get("ARGUS_TEAMMATE_RESEARCH_TIMEOUT_S", "180"))
    # Domain-neutral default; an operator pins the domain (e.g. the specific
    # libraries / prior art to search) via ARGUS_TEAMMATE_RESEARCH_PROMPT — a
    # template with a ``{objective}`` placeholder. No domain is baked into the library.
    template = os.environ.get("ARGUS_TEAMMATE_RESEARCH_PROMPT", "").strip() or _DEFAULT_RESEARCH_PROMPT
    prompt = template.replace("{objective}", objective[:1000])
    # Route this raw codex spawn through the same gated containment policy as the
    # run_exec chokepoint. Gate OFF (default): no extra flag + inherited env, so
    # the command is byte-for-byte unchanged. Gate ON: confined to ``cwd`` with
    # the writable allowlist and scrubbed VCS creds, so a forced-research turn
    # cannot escape the sandbox the operator turned on for the rest of the fleet.
    from ..core.sandbox import codex_sandbox_args, codex_sandbox_env, engineer_sandbox_mode

    _sbx = codex_sandbox_args(working_dir=cwd) if engineer_sandbox_mode() else []
    try:
        res = subprocess.run(
            [agent_bin, "exec", "--skip-git-repo-check", *_sbx, prompt],
            cwd=str(cwd), capture_output=True, text=True, timeout=timeout_s,
            env=codex_sandbox_env(),
        )
        raw = res.stdout or ""
    except Exception as exc:  # noqa: BLE001 — research is best-effort
        sys.stderr.write(f"teammate_entry: forced web research skipped: {exc}\n")
        return objective
    lines = [ln for ln in raw.splitlines()
             if ln.strip() and "pynvml" not in ln and "FutureWarning" not in ln
             and not ln.startswith("OpenAI Codex")]
    summary = "\n".join(lines).strip()
    if "web search" not in summary.lower() and len(summary) < 40:
        return objective  # nothing useful came back
    summary = summary[-1800:]
    sys.stderr.write("teammate_entry: forced web research prepended "
                     f"({len(summary)} chars)\n")
    return ("[LIVE RESEARCH — performed via web_search just now, BEFORE you loaded "
            "any local/inherited context. Ground your work in this; do NOT ignore "
            "it to continue a previous bespoke direction.]\n"
            + summary + "\n--- end research ---\n\n" + objective)


def _forced_profile(objective: str, *, cwd: str) -> str:
    """Opt-in pre-mission profiling step (general; gated by env).

    When ``ARGUS_TEAMMATE_FORCE_PROFILE`` is set, run ONE operator-supplied
    profiling command (``ARGUS_TEAMMATE_PROFILE_CMD``) FIRST and prepend its
    stdout to the objective, so the engineer optimizes from measured bottleneck
    data instead of guessing. Same rationale as the forced web-research step: an
    optional "profile first" instruction buried in a large prompt is reliably
    skipped; a separate forced call cannot be faked. The command is
    operator-supplied (keeps the lib general — no profiler/box specifics live
    here); the objective is exported as ``ARGUS_OBJECTIVE`` so the command can
    target the right task. Best-effort: any failure/timeout/empty output degrades
    to the unmodified objective and never blocks the mission.
    """
    flag = os.environ.get("ARGUS_TEAMMATE_FORCE_PROFILE", "").strip().lower()
    if flag in ("", "0", "false", "no"):
        return objective
    cmd = os.environ.get("ARGUS_TEAMMATE_PROFILE_CMD", "").strip()
    if not cmd:
        return objective
    timeout_s = float(os.environ.get("ARGUS_TEAMMATE_PROFILE_TIMEOUT_S", "420"))
    env = dict(os.environ, ARGUS_OBJECTIVE=objective[:2000])
    try:
        res = subprocess.run(
            cmd, shell=True, cwd=str(cwd), capture_output=True, text=True,
            timeout=timeout_s, env=env,
        )
        raw = res.stdout or ""
    except Exception as exc:  # noqa: BLE001 — profiling is best-effort
        sys.stderr.write(f"teammate_entry: forced profile skipped: {exc}\n")
        return objective
    report = raw.strip()
    # Keep any non-empty profile. The library is domain-agnostic, so it does NOT
    # require a particular word (an earlier hard-coded domain-word check silently
    # discarded a perfectly good py-spy / cProfile / EXPLAIN-ANALYZE report). An
    # operator can demand a substring via ARGUS_TEAMMATE_PROFILE_REQUIRE_SUBSTR.
    _require = os.environ.get("ARGUS_TEAMMATE_PROFILE_REQUIRE_SUBSTR", "").strip()
    if len(report) < 40 or (_require and _require.lower() not in report.lower()):
        return objective  # nothing usable came back
    # no cap: the #1 bottleneck can sit anywhere in the profile — feed it whole
    sys.stderr.write(f"teammate_entry: forced profile prepended ({len(report)} chars)\n")
    # Domain-neutral default; an operator pins the framing (e.g. the profiler's
    # name and how to read its output) via ARGUS_TEAMMATE_PROFILE_HEADER. No
    # box/profiler specifics here.
    header = os.environ.get("ARGUS_TEAMMATE_PROFILE_HEADER", "").strip() or _DEFAULT_PROFILE_HEADER
    return header + "\n" + report + "\n--- end profile ---\n\n" + objective


def run_one_engineer_mission(objective: str, *, cwd: str, life_dir: Path,
                             paper_mission: bool = False, max_rounds: int | None = None,
                             timeout_s: float | None = None) -> bool:
    """Run ONE headless engineer mission in-process on ``objective`` in ``cwd``.

    Reuses ``_SkillLoopRunner.execute`` — the exact per-mission call the
    daemon's supervisor makes. No cockpit, no daemon lock, no planner, no
    recursion. Events go to the isolated ``life_dir``. Returns True on success.

    Time-boxed: capped at ``max_rounds`` engineer rounds AND a wall-clock
    ``timeout_s`` (a watchdog sets the runner's stop_event), so a hard task
    can never hang a teammate for hours. Both default low and are env-tunable
    (ARGUS_TEAMMATE_MAX_ROUNDS, ARGUS_TEAMMATE_TIMEOUT_S).
    """
    if max_rounds is None:
        max_rounds = int(os.environ.get("ARGUS_TEAMMATE_MAX_ROUNDS", "200"))
    if timeout_s is None:
        timeout_s = float(os.environ.get("ARGUS_TEAMMATE_TIMEOUT_S", "5400"))  # 90 min: measure + iterate >=3-4 distinct approaches (aligned with the full engineer, not a shallow one-shot)
    # A teammate's events go to its isolated ``life_dir``, NOT the daemon's
    # ``<global_root>/projects/<fingerprint>/events.jsonl`` that the reviewer's
    # engineer-execution-log audit greps — so that audit would inspect a
    # co-located daemon's shared log and mis-attribute other missions' commands.
    # Disable checkpoint persistence: the audit is then omitted, and a single-shot
    # teammate (no cross-mission continuity) won't collide with sibling teammates
    # on a shared checkpoint.json.
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
            watchdog = threading.Timer(timeout_s, stop_event.set)
            watchdog.daemon = True
            watchdog.start()
            objective = _forced_web_research(objective, cwd=cwd)
            objective = _forced_profile(objective, cwd=cwd)
            ns = _build_runner_ns(
                cwd,
                max_rounds=max_rounds,
                paper_mission=paper_mission,
                stop_event=stop_event,
            )
            runner = _SkillLoopRunner(ns)
            sink = JsonlEventSink(LifeStderrSink(quiet=False), life_dir=life_dir)
            outcome = runner.execute(objective=objective, sink=sink)
        except SystemExit as exc:  # codex extra missing, etc.
            sys.stderr.write(f"teammate_entry: runner unavailable: {exc}\n")
            return False
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"teammate_entry: mission error: {exc!r}\n")
            return False
        finally:
            if watchdog is not None:
                watchdog.cancel()
        return bool(getattr(outcome, "success", False))


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


_PRIOR_NOISE_DEFAULT = (
    "diagnostic", "self_test", "selftest", "required_poll", "_poll", "poll_",
    "ground_truth", "teammate_status", "_marker", "marker_", ".lock", "_audit",
    "audit_", "_gate.", "gate_check", "stability_check",
)


def _prior_noise_terms() -> tuple:
    raw = os.environ.get("ARGUS_TEAMMATE_PRIOR_NOISE", "")
    if raw.strip():
        return tuple(t.strip().lower() for t in raw.split(",") if t.strip())
    return _PRIOR_NOISE_DEFAULT


def _is_prior_noise(name: str) -> bool:
    """A FRESH teammate should inherit distilled KNOWLEDGE, not PROCESS ARTIFACTS.

    Files whose names look like bookkeeping (diagnostics, status, poll, marker,
    lock, gate-check, ground-truth) are self-generated coordination noise — when
    inherited verbatim they teach each new teammate the same plumbing ritual,
    which compounds across the pool (observed: thousands of ``*required_poll*`` /
    ``GROUND_TRUTH`` files none of which any human contract ever asked for). Skip
    them so only genuine notes (idea-wiki, design writeups) propagate. General
    word-level filter — no project-specific paths baked in; override via
    ``ARGUS_TEAMMATE_PRIOR_NOISE``.
    """
    low = name.lower()
    return any(t in low for t in _prior_noise_terms())


def _gather_prior_work(cwd: Path, owns_paths: list[str], *, per_file_bytes: int = 4000,
                       total_bytes: int = 16000) -> str:
    """Read the task's already-owned artifacts so a FRESH teammate INHERITS prior
    exploration instead of restarting from a blank page.

    This is what turns a stateless rolling pool into something that EVOLVES across
    teammates: each new teammate on a task starts with the accumulated notes,
    status, and the list of candidates already tried by prior teammates — so it
    builds on them rather than re-deriving from zero. General by construction: it
    only reads the task's own ``owns_paths`` (no project-specific paths baked into
    the library). Markdown/text notes are inlined; directories are listed (so the
    teammate sees what attempts already exist) with their notes inlined. The
    teammate's own result shard is skipped. Bounded so it never blows the context.
    """
    if os.environ.get("ARGUS_TEAMMATE_INHERIT_PRIOR", "").strip().lower() in ("0", "false", "no"):
        return ""
    cwd = Path(cwd)
    chunks: list[str] = []
    used = 0

    def _add(label: str, text: str) -> None:
        nonlocal used
        if used >= total_bytes or not text:
            return
        text = text[:per_file_bytes]
        chunks.append(f"### {label}\n{text}")
        used += len(text)

    for rel in owns_paths or []:
        if used >= total_bytes:
            break
        rel = str(rel)
        if rel.endswith(".jsonl") or "/shards/" in rel or rel.startswith("shards/"):
            continue  # the teammate's own output, not inherited knowledge
        p = cwd / rel
        try:
            if p.is_file() and p.suffix in (".md", ".txt") and not _is_prior_noise(p.name):
                _add(rel, p.read_text(encoding="utf-8", errors="replace"))
            elif p.is_dir():
                names = sorted(x.name for x in p.iterdir() if x.is_file() and not _is_prior_noise(x.name))
                if names:
                    _add(f"{rel} — candidates/attempts already on disk", "\n".join(names[:80]))
                for x in sorted(p.iterdir()):
                    if used >= total_bytes:
                        break
                    if x.is_file() and x.suffix in (".md", ".txt") and not _is_prior_noise(x.name):
                        _add(rel + x.name, x.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue

    if not chunks:
        return ""
    return ("## PRIOR WORK ON THIS TASK — you INHERIT this; build on it, do NOT restart "
            "from a blank page. Earlier teammates already explored this; read their notes and "
            "the candidates on disk, then improve on the best instead of re-deriving from zero:\n\n"
            + "\n\n".join(chunks) + "\n\n---\n\n")


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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="argus_skill.team.teammate_entry")
    p.add_argument("--root", required=True)
    p.add_argument("--member-id", required=True)
    p.add_argument("--task-id", default="")
    p.add_argument("--cwd", default="")
    p.add_argument("--mission-cmd", default="",
                   help="test override: run this command (+objective) instead of the in-process mission")
    args = p.parse_args(argv)

    root = Path(args.root)
    task = _owned_task(root, args.member_id, args.task_id or None)
    if task is None:
        sys.stderr.write(f"teammate_entry: no task for {args.member_id}\n")
        return 2
    task_id = task["task_id"]
    objective = task.get("objective", "")
    cwd = args.cwd or os.getcwd()
    # Inherit prior exploration on this task (cross-teammate evolution): a fresh
    # teammate starts with earlier teammates' notes + the candidates already tried,
    # so it builds on the best instead of re-deriving from a blank page.
    prior = _gather_prior_work(Path(cwd), task.get("owns_paths", []))
    if prior:
        objective = prior + objective
        sys.stderr.write(f"teammate_entry: inherited {len(prior)} chars of prior work "
                         f"for {task_id} (cross-teammate evolution)\n")
    # Leaderboard inheritance: tell a fresh teammate what's already been tried on
    # this target so it builds on the best instead of re-running the same breadth.
    # No-op until a leaderboard exists; disable with ARGUS_TEAMMATE_INHERIT_LEADERBOARD=0.
    if os.environ.get("ARGUS_TEAMMATE_INHERIT_LEADERBOARD", "").strip().lower() not in ("0", "false", "no"):
        from . import leaderboard as _lb
        lb_block = _lb.objective_block(root, task.get("target") or task_id)
        if lb_block:
            objective = lb_block + objective
    member_safe = args.member_id.replace(":", "_")

    (root / "shards").mkdir(parents=True, exist_ok=True)
    shard = root / "shards" / (member_safe + ".jsonl")

    task_board.heartbeat(root, task_id, now=time.time())
    stop = threading.Event()
    threading.Thread(target=_heartbeat_loop, args=(root, task_id, stop), daemon=True).start()

    if args.mission_cmd:
        # test/escape-hatch path: run an arbitrary stub command instead of the mission
        log_dir = root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / (member_safe + ".log"), "ab") as log, open(os.devnull, "rb") as devnull:
            proc = subprocess.Popen(
                args.mission_cmd.split() + [objective],
                cwd=cwd,
                stdin=devnull,
                stdout=log,
                stderr=log,
                start_new_session=os.name != "nt",
            )
            success = proc.wait() == 0
    else:
        success = run_one_engineer_mission(
            objective, cwd=cwd, life_dir=root / "life" / member_safe)

    stop.set()
    _result = _read_optional_result(expected_target=task.get("target") or task_id)
    _rec = {
        "member_id": args.member_id, "task_id": task_id,
        "target": task.get("target") or task_id, "success": success,
        "metric": _result.get("metric"), "mechanism": _result.get("mechanism", ""),
    }
    # Carry the target's optimization direction so the leaderboard ranks per-target;
    # omit when the task didn't set it → the leaderboard uses its global default.
    if task.get("lower_is_better") is not None:
        _rec["lower_is_better"] = bool(task["lower_is_better"])
    shard.write_text(json.dumps(_rec) + "\n", encoding="utf-8")
    if success:
        task_board.complete(root, task_id, shard=str(shard))
    else:
        task_board.fail(root, task_id, reason="teammate mission did not succeed")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
