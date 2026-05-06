"""Per-task runner for SWE-Bench-Pro.

Drives ``MissionLoopEngine`` against a sweap-images container, using the
exact same engineer/reviewer/skill-cache plumbing as
``benchmarks.harbor_adapter`` (we re-use ``_do_host_prep`` and import the
same engine and runners).

The async `run_one_task` returns a `TaskResult` with patch + telemetry.
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .docker_env import DEFAULT_WORKDIR, MinimalDockerEnvironment, docker_container
from .task_loader import Task

log = logging.getLogger("argus_skill.swebench_pro")


def _parse_str_list(raw: str) -> list[str]:
    """Parse a list-of-strings field that may be JSON or Python repr.

    SWE-Bench-Pro stores ``fail_to_pass`` / ``pass_to_pass`` /
    ``selected_test_files_to_run`` as Python ``repr()`` of a list, which
    is NOT valid JSON when items contain apostrophes (mixed quote styles).
    Try JSON first, then ``ast.literal_eval`` as the official evaluator
    does (via ``eval``), to stay bug-for-bug compatible.
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:  # noqa: BLE001
        try:
            parsed = ast.literal_eval(raw)
        except Exception:  # noqa: BLE001
            return []
    if isinstance(parsed, list):
        return [str(x) for x in parsed if x]
    return []

# Default engineer/reviewer model names mirror harbor_adapter defaults.
_DEFAULT_ENGINEER_MODEL = os.environ.get(
    "ARGUS_SKILL_HARBOR_MAIN_MODEL", "gpt-5.4-mini"
)
_DEFAULT_ENGINEER_EFFORT = os.environ.get(
    "ARGUS_SKILL_HARBOR_MAIN_EFFORT", "high"
)
_DEFAULT_REVIEWER_MODEL = os.environ.get(
    "ARGUS_SKILL_HARBOR_REVIEWER_MODEL",
    os.environ.get("ARGUS_SKILL_HARBOR_SCIENTIST_MODEL", "gpt-5.4"),
)
_DEFAULT_REVIEWER_EFFORT = os.environ.get(
    "ARGUS_SKILL_HARBOR_REVIEWER_EFFORT", "medium"
)
_DEFAULT_MAX_ROUNDS = int(os.environ.get("ARGUS_SKILL_HARBOR_MAX_ROUNDS", "6"))
_DEFAULT_ROUND_TIMEOUT = int(
    os.environ.get("ARGUS_SKILL_HARBOR_ROUND_TIMEOUT", "1500")
)

#: Where the agent writes scratch files inside the container.
AGENT_DIR = "/agent"
OUTPUT_FILENAME = "argus-skill-codex.txt"

#: Where we install the official SWE-Bench-Pro test harness inside the
#: container so the reviewer can run real tests after each round.
VERIFIER_DIR = "/workspace"
#: Default location of per-instance run_scripts on the host (cloned by
#: ``benchmarks/eval_swebench_pro_partial.sh`` on first eval run).
DEFAULT_RUN_SCRIPTS_HOST_DIR = os.environ.get(
    "ARGUS_SKILL_SWEBPRO_RUN_SCRIPTS",
    "/home/argustest/skill-agent/.swebench-pro-eval/run_scripts",
)


class InContainerVerifier:
    """Run the official SWE-Bench-Pro test harness inside the container and
    return the names of tests that are still failing.

    We copy ``run_script.sh`` + ``parser.py`` from the host into
    ``/workspace`` once at container startup, then re-execute them after
    each engineer round. The reviewer uses the parsed output as ground
    truth instead of trusting the engineer's self-report.
    """

    def __init__(
        self,
        *,
        environment: MinimalDockerEnvironment,
        run_script_host_dir: Path,
        selected_test_files: list[str],
        expected_to_pass: list[str],
        before_repo_set_cmd: str = "",
        repo_path: str = "/app",
        timeout_sec: int = 1800,
        logger: logging.Logger | None = None,
    ) -> None:
        self.env = environment
        self.run_script_host_dir = run_script_host_dir
        self.selected_test_files = list(selected_test_files)
        self.expected_to_pass = set(expected_to_pass)
        # ``before_repo_set_cmd`` from the dataset; the official entry
        # script keeps only the LAST line, so we mirror that.
        self.before_repo_set_cmd = (
            (before_repo_set_cmd or "").strip().split("\n")[-1].strip()
        )
        self.repo_path = repo_path
        self.timeout_sec = timeout_sec
        self.log = logger or log
        self._installed = False

    async def install(self) -> None:
        """Copy run_script.sh + parser.py into the container."""
        if self._installed:
            return
        await self.env.exec(f"mkdir -p {VERIFIER_DIR}", timeout_sec=30)
        for fname in ("run_script.sh", "parser.py"):
            if not (self.run_script_host_dir / fname).is_file():
                raise FileNotFoundError(
                    f"verifier asset not found: {self.run_script_host_dir / fname}"
                )
        await self.env.upload_dir(str(self.run_script_host_dir), VERIFIER_DIR)
        await self.env.exec(
            f"chmod +x {VERIFIER_DIR}/run_script.sh", timeout_sec=30
        )
        self._installed = True
        self.log.info("[verifier] installed harness at %s", VERIFIER_DIR)

    async def run_and_get_failing(self) -> list[str] | None:
        """Run the harness; return list of acceptance tests still FAILED.

        Returns:
            - list[str] of failing names (empty list = all green) on success
            - None if the harness errored out (reviewer should treat as
              "no signal" and fall back).
        """
        if not self._installed:
            try:
                await self.install()
            except Exception as exc:  # noqa: BLE001
                self.log.warning("[verifier] install failed: %s", exc)
                return None
        sel = ",".join(self.selected_test_files) if self.selected_test_files else ""
        prep = (self.before_repo_set_cmd + " && ") if self.before_repo_set_cmd else ""
        cmd = (
            f"cd {self.repo_path} && rm -f {VERIFIER_DIR}/stdout.log "
            f"{VERIFIER_DIR}/stderr.log {VERIFIER_DIR}/output.json && "
            f"{prep}"
            f"bash {VERIFIER_DIR}/run_script.sh {sel} "
            f"> {VERIFIER_DIR}/stdout.log 2> {VERIFIER_DIR}/stderr.log; "
            f"python3 {VERIFIER_DIR}/parser.py "
            f"{VERIFIER_DIR}/stdout.log {VERIFIER_DIR}/stderr.log "
            f"{VERIFIER_DIR}/output.json"
        )
        result = await self.env.exec(
            cmd, timeout_sec=self.timeout_sec, user="root"
        )
        if result.return_code != 0:
            self.log.info(
                "[verifier] harness exit=%s; trying parser fallback",
                result.return_code,
            )
        cat = await self.env.exec(
            f"cat {VERIFIER_DIR}/output.json 2>/dev/null || true",
            timeout_sec=30,
        )
        raw = (cat.stdout or "").strip()
        if not raw:
            self.log.warning("[verifier] no output.json produced")
            return None
        try:
            data = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            self.log.warning("[verifier] bad json: %s", exc)
            return None
        passed = {
            t.get("name", "")
            for t in (data.get("tests") or [])
            if t.get("status") == "PASSED"
        }
        # Restrict to the acceptance set we care about; raw harness may
        # also surface unrelated test results.
        if self.expected_to_pass:
            still_failing = sorted(self.expected_to_pass - passed)
        else:
            still_failing = sorted(
                t.get("name", "")
                for t in (data.get("tests") or [])
                if t.get("status") in ("FAILED", "ERROR")
            )
        self.log.info(
            "[verifier] passed=%d expected=%d still_failing=%d",
            len(passed),
            len(self.expected_to_pass),
            len(still_failing),
        )
        return still_failing



@dataclass
class TaskResult:
    instance_id: str
    repo: str
    method: str = "argus_skill"
    patch: str = ""
    error: str = ""
    elapsed_s: float = 0.0
    rounds: list[dict] = field(default_factory=list)
    skill_used: bool = False
    skill_match_names: list[str] = field(default_factory=list)
    fallback_reason: str | None = None
    scientist_tokens: int = 0
    match_tokens: int = 0
    final_review_status: str | None = None
    docker_image: str = ""

    def as_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "repo": self.repo,
            "method": self.method,
            "patch": self.patch,
            "has_patch": bool(self.patch),
            "apply_ok": bool(self.patch),
            "error": self.error,
            "model_time_sec": self.elapsed_s,
            "steps": " -> ".join(
                str(r.get("review_status") or "?") for r in self.rounds
            ),
            "rounds": self.rounds,
            "skill_used": self.skill_used,
            "skill_match_names": self.skill_match_names,
            "fallback_reason": self.fallback_reason,
            "scientist_tokens": self.scientist_tokens,
            "match_tokens": self.match_tokens,
            "final_review_status": self.final_review_status,
            "docker_image": self.docker_image,
        }


# ----------------------------------------------------------------------------
# Mission engine wiring (mirrors harbor_adapter._run_mission_engine)
# ----------------------------------------------------------------------------


def _summarise_engine_rounds(engine_result: Any) -> list[dict]:
    out: list[dict] = []
    for r in getattr(engine_result, "rounds", []) or []:
        review = getattr(r, "review", None)
        entry: dict = {
            "round": getattr(r, "round_index", None),
            "engineer_exit": getattr(r, "main_exit_code", None),
            "main_turn_completed": getattr(r, "main_turn_completed", None),
            "main_turn_failed": getattr(r, "main_turn_failed", None),
            "thread_id": getattr(r, "thread_id", None),
        }
        if review is not None:
            entry["review_status"] = getattr(review, "status", None)
            entry["review_confidence"] = getattr(review, "confidence", None)
            cause = getattr(review, "failure_cause", "") or ""
            if cause:
                entry["review_failure_cause"] = cause
        out.append(entry)
    return out


def _build_engineer_cli_flags(model: str, effort: str) -> str:
    # Codex CLI: same scheme harbor uses (model + reasoning effort only;
    # bypass-approvals + model are added by the launcher script).
    return f"-c model_reasoning_effort={effort}"


def _bool_promote_env() -> bool:
    """Auto-promote reviewer skill_gap lessons into the matched skill."""
    return os.environ.get("ARGUS_SKILL_AUTO_PROMOTE_LESSON", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _bool_writeback_env() -> bool:
    """On reviewer-clean ``done``, scientist-revise the matched skill."""
    return os.environ.get("ARGUS_SKILL_REVISE_ON_WRITEBACK", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _summarise_trajectory_for_writeback(res: "TaskResult") -> str:
    """Compact trajectory summary fed into the revise prompt as evidence.

    Includes the round-by-round reviewer verdicts and the patch so the
    scientist can see what concrete change was made and how the reviewer
    judged it. Bounded to ~12 KB to keep the revise prompt small.
    """
    lines: list[str] = []
    for r in res.rounds:
        lines.append(
            f"- round {r.get('round')}: review={r.get('review_status')} "
            f"conf={r.get('review_confidence')} "
            f"cause={r.get('review_failure_cause') or '-'}"
        )
    summary = "\n".join(lines)
    patch_block = (res.patch or "")[:8000]
    return (
        f"## Round verdicts\n{summary}\n\n"
        f"## Final patch (truncated to 8KB)\n```diff\n{patch_block}\n```"
    )


def _make_lesson_promoter(
    *,
    matched: "Any | None",
    store: "Any | None",
    distiller: "Any | None",
    scientist_model: str,
    objective: str,
    logger: logging.Logger,
):
    """Build the engine's ``on_skill_lesson`` callback.

    Returns ``None`` if any dependency is missing or the env flag is not
    set — the engine treats ``None`` as "do not auto-promote, just
    record to pending_lessons/ as before".
    """
    if not _bool_promote_env():
        return None
    if matched is None or store is None or distiller is None:
        return None

    def _cb(skill_id: str, lesson_text: str) -> None:
        try:
            ok = store.promote_lesson(
                skill=matched,
                lesson_text=lesson_text,
                task_description=objective,
                distiller=distiller,
                scientist_model=scientist_model,
            )
            if ok:
                logger.info(
                    "auto-promoted lesson into %s → v%s",
                    matched.name, matched.version,
                )
            else:
                logger.info(
                    "auto-promote rejected lesson for %s (kept pending)",
                    matched.name,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("auto-promote crashed: %s: %s",
                           type(exc).__name__, exc)

    return _cb


async def _run_mission_engine_in_container(
    *,
    instruction: str,
    environment: MinimalDockerEnvironment,
    engineer_model: str,
    engineer_effort: str,
    reviewer_model: str,
    reviewer_effort: str,
    skill_text: str,
    skill_name: str | None,
    max_rounds: int,
    round_timeout: int,
    mission_id: str,
    no_reviewer: bool,
    logger: logging.Logger,
    verifier: "InContainerVerifier | None" = None,
    on_skill_lesson: Any = None,
) -> Any:
    """Construct and run a MissionLoopEngine instance against *environment*."""
    # Imports are lazy so the module stays importable even without
    # codex_autoloop installed (e.g. in CI for unit tests).
    from argus_skill.adapters.codex_backend import CodexRunnerBackend  # noqa: F401  # ensure package importable
    from argus_skill.mission.engine import MissionLoopConfig, MissionLoopEngine
    from argus_skill.mission.reviewer import MissionReviewer
    from argus_skill.runners.container import (
        ContainerCodexRunner,
        ContainerCodexRunnerConfig,
        ContainerReviewerBackend,
    )
    from codex_autoloop.core.state_store import LoopStateStore
    from codex_autoloop.models import CodexRunResult

    # Reuse harbor_adapter's JSONL parsers (same codex output format).
    from benchmarks.harbor_adapter import (
        _AUGMENTED_MAX_CHARS,
        _extract_thread_id_from_jsonl,
        _parse_agent_messages_from_jsonl,
    )

    loop = asyncio.get_running_loop()

    def event_sink(event: dict) -> None:
        try:
            et = event.get("type", "?")
            if et == "engineer.progress":
                rnd = event.get("round")
                kind = event.get("kind") or "message"
                text = (event.get("text") or "").strip()
                if not text:
                    return
                head = text.splitlines()[0]
                if len(head) > 200:
                    head = head[:200].rstrip() + "…"
                logger.info("[engineer.r%s.%s] %s", rnd, kind, head)
            else:
                logger.info(
                    "[engine] %s %s",
                    et,
                    {k: v for k, v in event.items() if k != "type"},
                )
        except Exception:  # noqa: BLE001
            pass

    runner_cfg = ContainerCodexRunnerConfig(
        model=engineer_model,
        cli_flags_arg=_build_engineer_cli_flags(engineer_model, engineer_effort),
        skill_text=skill_text,
        skill_name=skill_name,
        round_timeout=round_timeout,
        output_filename=OUTPUT_FILENAME,
        agent_dir_posix=AGENT_DIR,
        verify_cmd="",  # SWE-Bench-Pro evaluates externally; no in-container tests.
        verify_timeout=0,
        tests_src_dir="",
        verify_advisory=False,
        augmented_max_chars=_AUGMENTED_MAX_CHARS,
    )
    runner = ContainerCodexRunner(
        environment=environment,
        env_vars={},
        config=runner_cfg,
        loop=loop,
        codex_run_result_cls=CodexRunResult,
        agent_message_parser=_parse_agent_messages_from_jsonl,
        thread_id_extractor=_extract_thread_id_from_jsonl,
        logger=logger,
        event_sink=event_sink,
    )

    reviewer_cfg = ContainerCodexRunnerConfig(
        model=reviewer_model,
        cli_flags_arg=f"-c model_reasoning_effort={reviewer_effort}",
        skill_text="",
        skill_name=None,
        round_timeout=round_timeout,
        output_filename=OUTPUT_FILENAME,
        agent_dir_posix=AGENT_DIR,
        verify_cmd="",
        verify_timeout=0,
        state_probe_cmd="",
        tests_src_dir="",
        verify_advisory=False,
        augmented_max_chars=_AUGMENTED_MAX_CHARS,
    )
    reviewer_backend = ContainerReviewerBackend(
        environment=environment,
        env_vars={},
        config=reviewer_cfg,
        loop=loop,
        codex_run_result_cls=CodexRunResult,
        agent_message_parser=_parse_agent_messages_from_jsonl,
        thread_id_extractor=_extract_thread_id_from_jsonl,
        logger=logger,
        event_sink=event_sink,
    )
    reviewer = MissionReviewer(runner=reviewer_backend)

    if verifier is not None:
        # Each round, run the official SWE-Bench-Pro test harness inside the
        # container and surface the *actually still-failing* tests to the
        # reviewer. This is ground truth the main agent cannot fabricate.
        _inner_reviewer = reviewer
        _verifier = verifier

        class _VerifierInjectingReviewer:
            def evaluate(self, **kwargs):
                ctx = dict(kwargs.get("verification_context") or {})
                try:
                    fut = asyncio.run_coroutine_threadsafe(
                        _verifier.run_and_get_failing(), loop
                    )
                    failing = fut.result(timeout=_verifier.timeout_sec + 60)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[verifier] error: %s", exc)
                    failing = None
                if failing is None:
                    ctx["acceptance_tests"] = (
                        list(_verifier.expected_to_pass)
                        if _verifier.expected_to_pass
                        else []
                    )
                    ctx["notes"] = (
                        (ctx.get("notes") or "")
                        + " in-container verifier did not run; "
                        "treating expected acceptance set as unverified."
                    ).strip()
                else:
                    ctx["acceptance_tests"] = failing
                    if not failing:
                        ctx["notes"] = (
                            (ctx.get("notes") or "")
                            + " in-container verifier reports ALL acceptance tests PASS."
                        ).strip()
                kwargs["verification_context"] = ctx
                return _inner_reviewer.evaluate(**kwargs)

        reviewer = _VerifierInjectingReviewer()  # type: ignore[assignment]

    if no_reviewer:
        class _NoReviewer:
            def evaluate(self, **_kwargs):
                from argus_skill.core.models import ReviewDecision
                return ReviewDecision(
                    status="done", confidence=1.0, reason="no_reviewer ablation"
                )
        reviewer = _NoReviewer()  # type: ignore[assignment]

    from argus_skill.skills.lessons import default_pending_lessons_dir
    pending_dir = default_pending_lessons_dir(
        os.environ.get("ARGUS_SKILL_HARBOR_SKILLS_DIR")
    )

    engine_cfg = MissionLoopConfig(
        objective=instruction,
        max_rounds=max_rounds,
        check_commands=[],
        main_model=engineer_model,
        reviewer_model=reviewer_model,
        reviewer_reasoning_effort=reviewer_effort,
        plan_mode="off",
        allow_follow_up_phase=False,
        pending_lessons_dir=str(pending_dir),
        mission_id=mission_id,
        on_skill_lesson=on_skill_lesson,
    )
    state_store = LoopStateStore(objective=instruction)
    engine = MissionLoopEngine(
        runner=runner,
        reviewer=reviewer,  # type: ignore[arg-type]
        planner=None,
        config=engine_cfg,
        state_store=state_store,
        event_sink=event_sink,
    )
    # Engine.run is sync; runner posts back via run_coroutine_threadsafe.
    return await asyncio.to_thread(engine.run)


# ----------------------------------------------------------------------------
# Public per-task entry point
# ----------------------------------------------------------------------------


def _build_instruction(task: Task) -> str:
    """Format the SWE-Bench-Pro task as an Argus-Skill mission objective."""
    return (
        f"You are working in repository {task.repo} pre-cloned at {DEFAULT_WORKDIR}.\n"
        f"The repo is checked out at base commit {task.base_commit}.\n\n"
        f"Resolve the following GitHub issue by editing the source code.\n"
        f"Do *not* modify the test suite. Make the failing tests pass while "
        f"keeping the existing passing tests green.\n\n"
        f"--- Issue ---\n{task.problem_statement.strip()}\n"
    )


async def run_one_task(
    task: Task,
    *,
    namespace: str = "jefzda",
    engineer_model: str = _DEFAULT_ENGINEER_MODEL,
    engineer_effort: str = _DEFAULT_ENGINEER_EFFORT,
    reviewer_model: str = _DEFAULT_REVIEWER_MODEL,
    reviewer_effort: str = _DEFAULT_REVIEWER_EFFORT,
    max_rounds: int = _DEFAULT_MAX_ROUNDS,
    round_timeout: int = _DEFAULT_ROUND_TIMEOUT,
    no_reviewer: bool = False,
    logger: logging.Logger | None = None,
) -> TaskResult:
    """Run argus-skill on one SWE-Bench-Pro task.

    On any error (docker, distill, engine), the patch is left empty and the
    error string is populated. Always returns a TaskResult.
    """
    logger = logger or log
    res = TaskResult(
        instance_id=task.instance_id,
        repo=task.repo,
        docker_image=task.docker_image(namespace),
    )
    t0 = time.time()

    # ---- Phase 1: host-side skill cache prep ----
    matched_skill_obj = None
    skill_store_obj = None
    distiller_obj = None
    scientist_model_str = ""
    try:
        from benchmarks.harbor_adapter import _do_host_prep
        prep = await asyncio.to_thread(_do_host_prep, _build_instruction(task))
        res.skill_used = prep.skill_used
        res.skill_match_names = list(prep.match_names)
        res.fallback_reason = prep.fallback_reason
        res.scientist_tokens = prep.scientist_tokens
        res.match_tokens = prep.match_tokens
        skill_text = prep.skill_text
        skill_name = prep.match_names[0] if prep.match_names else None
        matched_skill_obj = prep.matched_skill
        skill_store_obj = prep.skill_store
        distiller_obj = prep.distiller
        scientist_model_str = prep.scientist_model
    except Exception as exc:  # noqa: BLE001
        logger.warning("[%s] host prep failed: %s", task.instance_id, exc)
        skill_text = ""
        skill_name = None
        res.fallback_reason = f"prep_exception:{type(exc).__name__}"

    # Parse public fail_to_pass test names so the reviewer (and only the
    # reviewer) gets a ground-truth acceptance list. ``fail_to_pass`` on
    # the Task dataclass is stored as a JSON string list per task_loader.
    acceptance_tests = _parse_str_list(task.fail_to_pass)
    pass_to_pass = _parse_str_list(task.pass_to_pass)
    selected_test_files = _parse_str_list(task.selected_test_files_to_run)

    # Find per-instance harness directory on host (cloned on first eval run).
    host_run_scripts_dir: Path | None = None
    candidate = Path(DEFAULT_RUN_SCRIPTS_HOST_DIR) / task.instance_id
    if candidate.is_dir() and (candidate / "run_script.sh").is_file():
        host_run_scripts_dir = candidate
    elif acceptance_tests or selected_test_files:
        logger.warning(
            "[%s] in-container verifier disabled: %s missing",
            task.instance_id,
            candidate,
        )

    # ---- Phase 2: docker container + engine ----
    instruction = _build_instruction(task)
    try:
        async with docker_container(
            res.docker_image,
            base_commit=task.base_commit,
            repo_path=DEFAULT_WORKDIR,
            idle_seconds=max_rounds * round_timeout + 1800,
        ) as env:
            # /agent for codex scratch (prompt files, stdout, etc.)
            await env.exec(f"mkdir -p {AGENT_DIR}", timeout_sec=30)

            verifier: InContainerVerifier | None = None
            if host_run_scripts_dir is not None and selected_test_files:
                verifier = InContainerVerifier(
                    environment=env,
                    run_script_host_dir=host_run_scripts_dir,
                    selected_test_files=selected_test_files,
                    expected_to_pass=acceptance_tests + pass_to_pass,
                    before_repo_set_cmd=task.before_repo_set_cmd,
                    repo_path=DEFAULT_WORKDIR,
                    timeout_sec=round_timeout,
                    logger=logger,
                )
                try:
                    await verifier.install()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[%s] verifier install failed: %s", task.instance_id, exc
                    )
                    verifier = None

            engine_result = await _run_mission_engine_in_container(
                instruction=instruction,
                environment=env,
                engineer_model=engineer_model,
                engineer_effort=engineer_effort,
                reviewer_model=reviewer_model,
                reviewer_effort=reviewer_effort,
                skill_text=skill_text,
                skill_name=skill_name,
                max_rounds=max_rounds,
                round_timeout=round_timeout,
                mission_id=f"swebpro-{task.instance_id}",
                no_reviewer=no_reviewer,
                logger=logger,
                verifier=verifier,
                on_skill_lesson=_make_lesson_promoter(
                    matched=matched_skill_obj,
                    store=skill_store_obj,
                    distiller=distiller_obj,
                    scientist_model=scientist_model_str,
                    objective=instruction,
                    logger=logger,
                ),
            )

            res.rounds = _summarise_engine_rounds(engine_result)
            if res.rounds:
                res.final_review_status = res.rounds[-1].get("review_status")

            # Patch extraction.
            res.patch = await env.diff_repo(repo_path=DEFAULT_WORKDIR)
            if not res.patch:
                logger.info("[%s] empty diff after %d rounds",
                            task.instance_id, len(res.rounds))

            # ---- Phase-2 reviewer→skill loop: success writeback-revise.
            # Only fires when env flag is set, the mission ended with the
            # reviewer's last verdict ``done``, and a matched skill exists.
            if (
                _bool_writeback_env()
                and matched_skill_obj is not None
                and skill_store_obj is not None
                and distiller_obj is not None
                and res.final_review_status == "done"
            ):
                try:
                    await asyncio.to_thread(
                        skill_store_obj.writeback_from_trajectory,
                        skill=matched_skill_obj,
                        task_description=instruction,
                        successful_trajectory=_summarise_trajectory_for_writeback(res),
                        distiller=distiller_obj,
                        scientist_model=scientist_model_str,
                        revise=True,
                    )
                    logger.info(
                        "[%s] skill writeback-revise → %s v%s",
                        task.instance_id,
                        matched_skill_obj.name,
                        matched_skill_obj.version,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[%s] skill writeback-revise failed: %s",
                        task.instance_id, exc,
                    )
    except Exception as exc:  # noqa: BLE001
        logger.exception("[%s] task failed", task.instance_id)
        res.error = f"{type(exc).__name__}: {exc}"[:500]

    res.elapsed_s = round(time.time() - t0, 1)
    logger.info(
        "[%s] %s patch=%d rounds=%d elapsed=%.0fs",
        task.instance_id,
        "OK" if res.patch and not res.error else "EMPTY/ERR",
        len(res.patch),
        len(res.rounds),
        res.elapsed_s,
    )
    return res
