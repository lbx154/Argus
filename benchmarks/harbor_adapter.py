"""Harbor adapter for argus-skill (A-lite design).

Registers a Harbor agent ``argus-skill-codex`` that drops the full
matcher → distiller → reviewer-loop in front of Harbor's stock Codex
launch. Mirrors skill-agent's ``benchmarks.harbor_adapter`` shape but
adds the multi-round engineer-reviewer loop.

A-lite design summary (post rubber-duck critique 2026-05-04):
  * matcher / distiller / reviewer all run **on the host** (cheap
    OPENAI_API_KEY calls). No need to install argus-skill inside every
    TB v2 image.
  * **engineer rounds run inside the Harbor container** via
    ``environment.exec`` calling the same ``codex exec`` CLI that
    Harbor's stock agent uses.
  * After each engineer round, parse the last agent_message from the
    JSON event stream, feed it to the host-side reviewer, get a
    ``done`` / ``continue`` / ``blocked`` decision plus next-action
    feedback, and (if not done) inject the feedback into the next
    round's prompt.

Time budget (TB v2 typical: 600s/task):
  * distill: ≤ 120s
  * each engineer round: ≤ 200s (inherits Harbor's per-exec timeout)
  * each reviewer call: ≤ 60s
  * default max_rounds = 2

Launch::

    OPENAI_API_KEY=... OPENAI_BASE_URL=... \\
    PYTHONPATH=$(pwd) \\
    harbor run \\
        --dataset terminal-bench@2.0 \\
        --agent-import-path benchmarks.harbor_adapter:ArgusSkillCodex \\
        --model openai/gpt-5.4-mini \\
        --ak reasoning_effort=high \\
        -n 4 -k 1 \\
        --jobs-dir runs/argus-skill-codex-tb2

Ablation env vars:
  ``ARGUS_SKILL_HARBOR_NO_SKILL=1``    — skip matcher+distill (reviewer-only)
  ``ARGUS_SKILL_HARBOR_NO_REVIEWER=1`` — skip reviewer entirely (max_rounds=1)
  ``ARGUS_SKILL_HARBOR_REVIEWER_GATE=1`` — restore legacy "reviewer says continue
                                          → fire R2" behaviour (default off:
                                          R2 fires only on objective R1 failure)
  Combine the first two to fall back to plain bare-mini behaviour (sanity).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shlex
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Harbor imports happen lazily so importing this file outside Harbor
# (e.g. for unit tests) doesn't blow up.
try:
    from harbor.agents.installed.codex import Codex as _HarborCodex
    from harbor.environments.base import BaseEnvironment
    from harbor.models.agent.context import AgentContext
    from harbor.models.trial.paths import EnvironmentPaths
    _HARBOR_OK = True
    _HARBOR_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover
    _HARBOR_OK = False
    _HARBOR_IMPORT_ERROR = exc
    _HarborCodex = object  # type: ignore[misc,assignment]
    BaseEnvironment = object  # type: ignore[misc,assignment]
    AgentContext = object  # type: ignore[misc,assignment]
    EnvironmentPaths = None  # type: ignore[assignment]


log = logging.getLogger(__name__)

# --- env-var-driven knobs --------------------------------------------------

_DEFAULT_DISTILL_BUDGET = 120.0          # seconds, host-side matcher+distill cap
_DEFAULT_REVIEWER_BUDGET = 60.0          # seconds, per reviewer call
_DEFAULT_ROUND_TIMEOUT = 600             # seconds, per in-container engineer call
_DEFAULT_MAX_ROUNDS = 2
_AUGMENTED_MAX_CHARS = 24 * 1024


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# --- structured per-trial decision log ------------------------------------

_DECISIONS_LOG_ENV = "ARGUS_SKILL_HARBOR_DECISIONS_LOG"


def _write_decision(record: dict) -> None:
    path = os.environ.get(_DECISIONS_LOG_ENV)
    if not path:
        return
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.warning("decisions log write failed: %s", exc)


# --- codex JSON event parsing (matches ArgusBot's _consume_codex_event) ---


def _parse_agent_messages_from_jsonl(text: str) -> list[str]:
    """Pull all agent_message texts from codex --json event stream.

    Codex emits events like:
        {"type":"thread.started","thread_id":"..."}
        {"type":"item.completed","item":{"type":"agent_message","text":"..."}}
        {"type":"turn.completed"}
    We collect every agent_message in order.
    """
    messages: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        if item.get("type") != "agent_message":
            continue
        msg = item.get("text")
        if isinstance(msg, str) and msg.strip():
            messages.append(msg)
    return messages


def _extract_thread_id_from_jsonl(text: str) -> str | None:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "thread.started":
            tid = event.get("thread_id")
            if isinstance(tid, str):
                return tid
    return None


# --- argus-skill imports (lazy) -------------------------------------------


def _import_argus_skill():
    from argus_skill.adapters.codex_backend import CodexRunnerBackend
    from argus_skill.core.models import CheckResult
    from argus_skill.engineer.reviewer import Reviewer, ReviewerConfig
    from argus_skill.scientist.distiller import Distiller, DistillerConfig
    from argus_skill.skills.store import Skill, SkillStore

    return {
        "CodexRunnerBackend": CodexRunnerBackend,
        "CheckResult": CheckResult,
        "Reviewer": Reviewer,
        "ReviewerConfig": ReviewerConfig,
        "Distiller": Distiller,
        "DistillerConfig": DistillerConfig,
        "Skill": Skill,
        "SkillStore": SkillStore,
    }


# --- the host-side prep step -----------------------------------------------


@dataclass
class _HostPrep:
    skill_text: str
    skill_used: bool
    matched: bool
    match_count: int
    match_names: list[str]
    scientist_tokens: int
    match_tokens: int
    fallback_reason: str | None
    elapsed_s: float
    # Phase-2 reviewer→skill loop: callers (e.g. swebench_pro runner)
    # need the live Skill object + SkillStore + Distiller to perform
    # post-mission writeback-revise and skill_gap lesson promotion.
    # Left as ``None`` on cache miss / no-match / errors.
    matched_skill: Any = None  # argus_skill.skills.store.Skill | None
    skill_store: Any = None    # argus_skill.skills.store.SkillStore | None
    distiller: Any = None      # argus_skill.scientist.distiller.Distiller | None
    scientist_model: str = ""


def _do_host_prep(instruction: str) -> _HostPrep:
    """Run matcher+distiller on the host. Cheap on cache hits, ≤120s on miss."""
    if _bool_env("ARGUS_SKILL_HARBOR_NO_SKILL"):
        return _HostPrep("", False, False, 0, [], 0, 0, "no_skill_ablation", 0.0)

    deps = _import_argus_skill()
    scientist_model = os.environ.get("ARGUS_SKILL_HARBOR_SCIENTIST_MODEL", "gpt-5.4")
    matcher_model = os.environ.get("ARGUS_SKILL_HARBOR_MATCHER_MODEL", scientist_model)
    matcher_effort = os.environ.get("ARGUS_SKILL_HARBOR_MATCHER_EFFORT", "high")
    skills_dir = Path(
        os.environ.get("ARGUS_SKILL_HARBOR_SKILLS_DIR")
        or (Path.cwd() / "benchmarks" / "results" / "argus-skill-harbor" / "skills")
    )
    skills_dir.mkdir(parents=True, exist_ok=True)

    backend = deps["CodexRunnerBackend"](backend="codex")
    store = deps["SkillStore"](
        skills_dir,
        runner=backend,
        matcher_model=matcher_model,
        matcher_reasoning_effort=matcher_effort,
    )

    t0 = time.time()
    matched_skills: list = []
    match_tokens = 0
    fallback_reason: str | None = None
    scientist_tokens = 0
    skill_text = ""
    distilled_skill: Any = None  # populated when we save_distilled below
    distiller_obj: Any = None    # reused for revise/promote_lesson hooks

    try:
        matched_skills_or_none, match_tokens = store.find_relevant(instruction)
        if matched_skills_or_none:
            matched_skills = matched_skills_or_none
            skill_text = "\n\n---\n\n".join(s.render() for s in matched_skills)
    except Exception as exc:
        fallback_reason = f"match_exception:{type(exc).__name__}"
        log.warning("matcher failed: %s", exc)

    if not skill_text and not _bool_env("ARGUS_SKILL_HARBOR_NO_DISTILL"):
        # No match — distill a new skill.
        try:
            distiller_obj = deps["Distiller"](backend)
            cfg = deps["DistillerConfig"](
                model=scientist_model,
                reasoning_effort=os.environ.get("ARGUS_SKILL_HARBOR_SCIENTIST_EFFORT", "high"),
                skip_git_repo_check=True,
                full_auto=True,
            )
            with tempfile.TemporaryDirectory(prefix="argus-skill-harbor-"):
                result = distiller_obj.distill(
                    task_description=instruction,
                    config=cfg,
                )
            scientist_tokens = result.input_tokens + result.output_tokens
            try:
                distilled_skill = store.save_distilled(
                    task_description=instruction,
                    raw_distill_output=result.last_agent_message,
                    scientist_model=scientist_model,
                )
                skill_text = distilled_skill.render()
            except Exception as exc:
                # Parse failure — keep the raw distill output as the skill text.
                log.warning("save_distilled failed: %s", exc)
                fallback_reason = f"parse_failure:{type(exc).__name__}"
                skill_text = result.last_agent_message
        except Exception as exc:
            fallback_reason = f"distill_exception:{type(exc).__name__}"
            log.warning("distill failed: %s", exc)

    if distiller_obj is None:
        # Lazily build a distiller for matched-cache-hit case so the
        # caller can still hook revise/promote_lesson against it.
        try:
            distiller_obj = deps["Distiller"](backend)
        except Exception as exc:  # noqa: BLE001
            log.warning("post-match distiller construction failed: %s", exc)

    elapsed = round(time.time() - t0, 2)
    if skill_text and len(skill_text) > _AUGMENTED_MAX_CHARS:
        log.warning("skill text too large (%d chars); dropping.", len(skill_text))
        fallback_reason = fallback_reason or "skill_oversize"
        skill_text = ""

    matched_skill_obj = None
    if matched_skills:
        matched_skill_obj = matched_skills[0]
    elif distilled_skill is not None:
        matched_skill_obj = distilled_skill

    return _HostPrep(
        skill_text=skill_text,
        skill_used=bool(skill_text),
        matched=bool(matched_skills),
        match_count=len(matched_skills) if matched_skills else 0,
        match_names=[s.name for s in matched_skills] if matched_skills else [],
        scientist_tokens=scientist_tokens,
        match_tokens=match_tokens,
        fallback_reason=fallback_reason,
        elapsed_s=elapsed,
        matched_skill=matched_skill_obj,
        skill_store=store,
        distiller=distiller_obj,
        scientist_model=scientist_model,
    )


# --- the agent --------------------------------------------------------------


class ArgusSkillCodex(_HarborCodex):  # type: ignore[misc,valid-type]
    """Harbor's Codex agent + host-side skill cache + reviewer-gated round loop."""

    @staticmethod
    def name() -> str:  # type: ignore[override]
        return "argus-skill-codex"

    async def run(  # type: ignore[override]
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        if not _HARBOR_OK:  # pragma: no cover
            raise RuntimeError(
                "Harbor is not importable inside ArgusSkillCodex. "
                f"Underlying import error:\n{_HARBOR_IMPORT_ERROR}"
            )

        if not self.model_name:
            raise ValueError("Model name is required")
        model = self.model_name.split("/")[-1]
        cli_flags = self.build_cli_flags()
        cli_flags_arg = (cli_flags + " ") if cli_flags else ""

        # ---- Phase 1: container setup (mirror Harbor's stock Codex) ----
        env, setup_command = await self._prepare_container(environment)

        if setup_command.strip():
            await self.exec_as_agent(environment, command=setup_command, env=env)

        # ---- Phase 2: host-side matcher+distill ----
        distill_budget = _float_env("ARGUS_SKILL_HARBOR_DISTILL_BUDGET", _DEFAULT_DISTILL_BUDGET)
        try:
            prep = await asyncio.wait_for(
                asyncio.to_thread(_do_host_prep, instruction),
                timeout=distill_budget,
            )
        except asyncio.TimeoutError:
            self.logger.warning(
                "host prep exceeded %.0fs; falling back to engineer-only.", distill_budget
            )
            prep = _HostPrep("", False, False, 0, [], 0, 0, "distill_timeout", distill_budget)
        except Exception as exc:
            self.logger.warning("host prep raised: %s", exc)
            prep = _HostPrep(
                "", False, False, 0, [], 0, 0, f"prep_exception:{type(exc).__name__}", 0.0
            )

        self.logger.info(
            "argus-skill prep: matched=%s match_count=%d skill_used=%s elapsed=%.1fs",
            prep.matched, prep.match_count, prep.skill_used, prep.elapsed_s,
        )

        # ---- Phase 3: multi-round engineer (+ optional reviewer) loop ----
        no_reviewer = _bool_env("ARGUS_SKILL_HARBOR_NO_REVIEWER")
        # v7: Reviewer-as-gate is OFF by default. Reviewer now runs as a
        # diagnostic logger only; R2 fires on objective failure of R1
        # (timeout / non-zero exit / empty output), not on a reviewer
        # disagreement with the engineer's prose. Set
        # ARGUS_SKILL_HARBOR_REVIEWER_GATE=1 to restore old gating behavior.
        reviewer_gate = _bool_env("ARGUS_SKILL_HARBOR_REVIEWER_GATE")
        max_rounds = 1 if no_reviewer else _int_env(
            "ARGUS_SKILL_HARBOR_MAX_ROUNDS", _DEFAULT_MAX_ROUNDS
        )
        round_timeout = _int_env("ARGUS_SKILL_HARBOR_ROUND_TIMEOUT", _DEFAULT_ROUND_TIMEOUT)

        rounds_summary: list[dict] = []
        last_review_feedback: str | None = None
        last_round_summary: str | None = None
        last_round_failure: str | None = None
        last_thread_id: str | None = None
        run_error: str | None = None

        try:
            for round_idx in range(1, max_rounds + 1):
                round_prompt = self._build_round_prompt(
                    instruction=instruction,
                    skill_text=prep.skill_text,
                    review_feedback=last_review_feedback,
                    previous_round_summary=last_round_summary,
                    previous_round_failure=last_round_failure,
                    round_idx=round_idx,
                    total_rounds=max_rounds,
                )
                # Cap the prompt size, dropping skill on overflow rather than instruction.
                if len(round_prompt) > _AUGMENTED_MAX_CHARS:
                    self.logger.warning(
                        "round %d prompt too large (%d chars); dropping skill",
                        round_idx, len(round_prompt),
                    )
                    round_prompt = self._build_round_prompt(
                        instruction=instruction,
                        skill_text="",
                        review_feedback=last_review_feedback,
                        previous_round_summary=last_round_summary,
                        previous_round_failure=last_round_failure,
                        round_idx=round_idx,
                        total_rounds=max_rounds,
                    )

                # Engineer round (in container). We catch the docker-exec
                # RuntimeError so a per-round timeout does not abort the
                # trial — the verifier will still judge whatever the
                # engineer left on disk, exactly as bare-mini behaves
                # when its agent timeout fires.
                round_t0 = time.time()
                round_error: str | None = None
                stdout = ""
                exit_code = -1
                try:
                    stdout, exit_code = await self._run_codex_in_container(
                        environment=environment,
                        env=env,
                        cli_flags_arg=cli_flags_arg,
                        model=model,
                        prompt=round_prompt,
                        round_idx=round_idx,
                        round_timeout=round_timeout,
                        # v5: do NOT resume R1's codex session in R2. The
                        # filesystem is preserved (Harbor keeps the
                        # container), so R2 sees R1's files; but starting
                        # a fresh session means R2 doesn't inherit R1's
                        # (potentially wrong) self-belief that the work
                        # is done. R1's summary is fed via the prompt
                        # builder so R2 still has context to verify.
                        resume_session_id=None,
                    )
                except RuntimeError as exc:
                    # Harbor's docker exec raises RuntimeError on timeout
                    # (e.g. "Command timed out after 200 seconds").
                    round_error = f"engineer_runtime_error:{exc}"
                    self.logger.warning(
                        "round %d engineer raised RuntimeError: %s", round_idx, exc
                    )
                except Exception as exc:  # pragma: no cover - unexpected
                    round_error = f"engineer_exception:{type(exc).__name__}:{exc}"
                    self.logger.warning(
                        "round %d engineer raised %s: %s",
                        round_idx, type(exc).__name__, exc,
                    )
                round_elapsed = time.time() - round_t0

                agent_messages = _parse_agent_messages_from_jsonl(stdout)
                last_msg = agent_messages[-1] if agent_messages else ""
                last_thread_id = (
                    _extract_thread_id_from_jsonl(stdout) or last_thread_id
                )

                self.logger.info(
                    "round %d engineer: exit=%d, %d agent_messages, %.1fs%s",
                    round_idx, exit_code, len(agent_messages), round_elapsed,
                    f" error={round_error}" if round_error else "",
                )

                # v7 round-loop policy:
                #   * R1=clean (exit 0 + non-empty output) → break, trust the
                #     engineer like skill-cap-phaseA does (single-shot).
                #   * R1=objective failure (timeout, non-zero exit, empty
                #     output) → fire R2 with retry context. This is exactly
                #     where retries pay off; reviewer-disagreement does not
                #     correlate with verifier outcome.
                #   * Reviewer is OPTIONAL and DIAGNOSTIC only by default.
                #     Set ARGUS_SKILL_HARBOR_REVIEWER_GATE=1 to restore the
                #     old "reviewer says continue → fire R2" behavior.
                round_record: dict = {
                    "round": round_idx,
                    "engineer_exit": exit_code,
                    "agent_messages": len(agent_messages),
                    "elapsed_s": round_elapsed,
                }

                # Classify R1 outcome.
                if round_error:
                    failure_mode: str | None = round_error
                elif exit_code != 0:
                    failure_mode = (
                        f"engineer exit_code={exit_code}"
                        if exit_code != -1
                        else f"engineer round timed out after {round_timeout}s"
                    )
                elif not last_msg.strip():
                    failure_mode = "engineer produced no agent message"
                else:
                    failure_mode = None

                # Diagnostic reviewer pass: log the verdict so we can study
                # how often reviewer & verifier agree, but DO NOT gate on it
                # unless the user explicitly opted into the legacy gate.
                review_decision: dict | None = None
                run_reviewer = (
                    not no_reviewer
                    and last_msg.strip()
                    and not round_error
                )
                if run_reviewer:
                    try:
                        review_decision = await self._run_reviewer_on_host(
                            instruction=instruction,
                            last_msg=last_msg,
                            round_idx=round_idx,
                            thread_id=last_thread_id,
                            engineer_exit_code=exit_code,
                        )
                        round_record["review_status"] = review_decision.get("status")
                        round_record["review_confidence"] = review_decision.get(
                            "confidence"
                        )
                    except Exception as exc:  # pragma: no cover - defensive
                        self.logger.warning(
                            "round %d reviewer raised %s: %s — ignoring",
                            round_idx, type(exc).__name__, exc,
                        )
                        round_record["review_status"] = "reviewer_error"
                else:
                    round_record["review_status"] = "skipped"

                rounds_summary.append(round_record)

                # If engineer crashed outright (exception in container exec),
                # we have no useful state and another round is unlikely to
                # help.
                if round_error:
                    break

                # Decide whether to retry.
                if failure_mode is None:
                    # R1 completed cleanly. Trust it (sc-A parity).
                    if reviewer_gate and review_decision is not None:
                        # Legacy gate: reviewer disagreement still triggers
                        # another round.
                        status = review_decision.get("status")
                        if status == "continue" and round_idx < max_rounds:
                            last_review_feedback = (
                                review_decision.get("next_action")
                                or review_decision.get("reason")
                                or ""
                            )
                            last_round_summary = last_msg or last_round_summary
                            last_round_failure = None
                            continue
                    break

                # Failure path: only retry if we have rounds left.
                if round_idx == max_rounds:
                    break

                last_round_failure = failure_mode
                last_round_summary = last_msg or last_round_summary
                if review_decision is not None:
                    last_review_feedback = (
                        review_decision.get("next_action")
                        or review_decision.get("reason")
                        or ""
                    ) or last_review_feedback
                self.logger.info(
                    "round %d engineer failed (%s) — retrying in round %d",
                    round_idx, failure_mode, round_idx + 1,
                )
        except Exception as exc:  # pragma: no cover - unexpected outer error
            run_error = f"{type(exc).__name__}:{exc}"
            self.logger.exception("argus-skill round-loop raised — recording and re-raising")
            raise
        finally:
            with contextlib.suppress(Exception):
                await self._cleanup_container(environment, env)
            with contextlib.suppress(Exception):
                _write_decision({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "model": model,
                    "matched": prep.matched,
                    "match_count": prep.match_count,
                    "match_names": prep.match_names,
                    "skill_used": prep.skill_used,
                    "scientist_tokens": prep.scientist_tokens,
                    "match_tokens": prep.match_tokens,
                    "prep_elapsed_s": prep.elapsed_s,
                    "fallback_reason": prep.fallback_reason,
                    "max_rounds": max_rounds,
                    "rounds_executed": len(rounds_summary),
                    "rounds": rounds_summary,
                    "no_reviewer": no_reviewer,
                    "reviewer_gate": reviewer_gate,
                    "run_error": run_error,
                })

    # ----------------------------------------------------------------------
    # Phase helpers (mostly mirroring Harbor's stock Codex.run)
    # ----------------------------------------------------------------------

    async def _prepare_container(
        self, environment: BaseEnvironment
    ) -> tuple[dict[str, str], str]:
        """Recreate Harbor's Codex.run() preamble: mkdirs, auth, skills, mcp.

        Returns ``(env, setup_command)``. ``setup_command`` should be
        run via ``exec_as_agent`` once before any round.
        """
        remote_codex_home = self._REMOTE_CODEX_HOME.as_posix()
        remote_secrets_dir = self._REMOTE_CODEX_SECRETS_DIR.as_posix()
        remote_auth_path = (self._REMOTE_CODEX_SECRETS_DIR / "auth.json").as_posix()

        env: dict[str, str] = {"CODEX_HOME": remote_codex_home}

        # mkdirs
        await self.exec_as_agent(
            environment,
            command=(
                f'mkdir -p "$CODEX_HOME" {shlex.quote(remote_secrets_dir)} '
                f"{shlex.quote(EnvironmentPaths.agent_dir.as_posix())}"
            ),
            env=env,
        )

        auth_json_path = self._resolve_auth_json_path()
        if auth_json_path:
            self.logger.debug("Codex auth: using auth.json from %s", auth_json_path)
            await environment.upload_file(auth_json_path, remote_auth_path)
            if environment.default_user is not None:
                await self.exec_as_root(
                    environment,
                    command=f"chown {environment.default_user} {remote_auth_path}",
                )
            setup_command = (
                f'ln -sf {shlex.quote(remote_auth_path)} "$CODEX_HOME/auth.json"\n'
            )
        else:
            self.logger.debug("Codex auth: using OPENAI_API_KEY")
            env["OPENAI_API_KEY"] = self._get_env("OPENAI_API_KEY") or ""
            setup_command = (
                f"cat >{shlex.quote(remote_auth_path)} <<EOF\n"
                '{\n  "OPENAI_API_KEY": "${OPENAI_API_KEY}"\n}\nEOF\n'
                f"ln -sf {shlex.quote(remote_auth_path)} "
                '"$CODEX_HOME/auth.json"\n'
            )

        if openai_base_url := self._get_env("OPENAI_BASE_URL"):
            env["OPENAI_BASE_URL"] = openai_base_url
            setup_command += (
                '\ncat >>"$CODEX_HOME/config.toml" <<TOML\n'
                'openai_base_url = "${OPENAI_BASE_URL}"\n'
                "TOML"
            )

        skills_command = self._build_register_skills_command()
        if skills_command:
            setup_command += f"\n{skills_command}"

        mcp_command = self._build_register_mcp_servers_command()
        if mcp_command:
            setup_command += f"\n{mcp_command}"

        return env, setup_command

    async def _run_codex_in_container(
        self,
        *,
        environment: BaseEnvironment,
        env: dict[str, str],
        cli_flags_arg: str,
        model: str,
        prompt: str,
        round_idx: int,
        round_timeout: int,
        resume_session_id: str | None = None,
    ) -> tuple[str, int]:
        """Run a single ``codex exec`` round inside the container.

        Returns ``(stdout, exit_code)``. We do NOT raise on non-zero
        exit so the round-loop can decide whether to retry / continue.

        When ``resume_session_id`` is given we use ``codex exec resume
        <id> ...`` so the round inherits R1's conversation thread —
        otherwise R2 would start from scratch and could overwrite R1's
        on-disk progress without ever seeing R1's tool history.
        """
        escaped = shlex.quote(prompt)
        per_round_log = (
            EnvironmentPaths.agent_dir / f"argus-skill-round-{round_idx}.txt"
        )
        # Also tee to the standard codex.txt for compatibility with Harbor's
        # post-run capture (only the LAST round's content survives — that's
        # OK since the verifier looks at the final filesystem state, not
        # intermediate logs).
        if resume_session_id:
            # `codex exec resume` syntax: positional [SESSION_ID] [PROMPT]
            # come AFTER the `--` end-of-options marker.
            quoted_session = shlex.quote(resume_session_id)
            codex_invocation = (
                "codex exec resume "
                "--dangerously-bypass-approvals-and-sandbox "
                "--skip-git-repo-check "
                f"--model {model} "
                "--json "
                "--enable unified_exec "
                f"{cli_flags_arg}"
                "-- "
                f"{quoted_session} {escaped}"
            )
        else:
            codex_invocation = (
                "codex exec "
                "--dangerously-bypass-approvals-and-sandbox "
                "--skip-git-repo-check "
                f"--model {model} "
                "--json "
                "--enable unified_exec "
                f"{cli_flags_arg}"
                "-- "
                f"{escaped}"
            )
        cmd = (
            "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; "
            f"{codex_invocation} "
            f"2>&1 </dev/null | tee {per_round_log.as_posix()} "
            f"{(EnvironmentPaths.agent_dir / self._OUTPUT_FILENAME).as_posix()}"
        )
        result = await environment.exec(
            command=f"set -o pipefail; {cmd}",
            env=env,
            timeout_sec=round_timeout,
        )
        stdout = result.stdout or ""
        return stdout, int(result.return_code)

    async def _run_reviewer_on_host(
        self,
        *,
        instruction: str,
        last_msg: str,
        round_idx: int,
        thread_id: str | None,
        engineer_exit_code: int,
    ) -> dict:
        """Run argus-skill's reviewer on the host. Returns a JSON-friendly dict.

        On any failure we return ``status="continue"`` so the loop keeps
        going (better to do another engineer round than abort).

        ``engineer_exit_code`` is the in-container ``codex exec`` exit
        code from this round. We surface it to the reviewer as
        ``main_error`` so a non-zero exit becomes an explicit signal
        (otherwise the reviewer only sees the last agent message and
        cannot tell whether the engineer crashed mid-task).
        """
        budget = _float_env("ARGUS_SKILL_HARBOR_REVIEWER_BUDGET", _DEFAULT_REVIEWER_BUDGET)
        main_error = (
            None
            if engineer_exit_code == 0
            else f"engineer exit_code={engineer_exit_code} (non-zero — investigate before declaring done)"
        )
        try:
            decision = await asyncio.wait_for(
                asyncio.to_thread(
                    _invoke_reviewer,
                    instruction=instruction,
                    last_msg=last_msg,
                    round_idx=round_idx,
                    thread_id=thread_id,
                    main_error=main_error,
                ),
                timeout=budget,
            )
        except asyncio.TimeoutError:
            self.logger.warning("reviewer round %d exceeded %.0fs", round_idx, budget)
            return {"status": "continue", "reason": "reviewer timeout"}
        except Exception as exc:
            self.logger.warning("reviewer round %d raised: %s", round_idx, exc)
            return {"status": "continue", "reason": f"reviewer error: {exc}"}
        return decision

    async def _cleanup_container(
        self, environment: BaseEnvironment, env: dict[str, str]
    ) -> None:
        """Mirror Harbor's stock cleanup: copy sessions out, then rm secrets."""
        with contextlib.suppress(Exception):
            await self.exec_as_agent(
                environment,
                command=(
                    f"mkdir -p {EnvironmentPaths.agent_dir.as_posix()}\n"
                    'if [ -d "$CODEX_HOME/sessions" ]; then\n'
                    f"  rm -rf {(EnvironmentPaths.agent_dir / 'sessions').as_posix()}\n"
                    f'  cp -R "$CODEX_HOME/sessions" '
                    f"{(EnvironmentPaths.agent_dir / 'sessions').as_posix()}\n"
                    "fi"
                ),
                env=env,
            )
        with contextlib.suppress(Exception):
            remote_secrets_dir = self._REMOTE_CODEX_SECRETS_DIR.as_posix()
            await self.exec_as_agent(
                environment,
                command=f'rm -rf {shlex.quote(remote_secrets_dir)} "$CODEX_HOME"',
                env=env,
            )

    # ----------------------------------------------------------------------
    # Prompt builders
    # ----------------------------------------------------------------------

    @staticmethod
    def _build_round_prompt(
        *,
        instruction: str,
        skill_text: str,
        review_feedback: str | None,
        round_idx: int,
        total_rounds: int,
        previous_round_summary: str | None = None,
        previous_round_failure: str | None = None,
    ) -> str:
        # v7: Round 1 prompt mirrors skill-cap-phaseA's exact shape — bare
        # `guide intro + ## Skill guide + ## Task`. Verify-evidence and
        # round-X-of-Y reminders were removed: they cost the agent reasoning
        # time without delivering parity gains. Round 2+ adds a failure
        # context block (we only retry on objective R1 failure now).
        parts: list[str] = []
        if skill_text:
            parts.append(
                "You have been provided with a reusable skill guide for tasks of "
                "this type. Read it carefully, then solve the task below."
            )
            parts.append(f"## Skill guide\n{skill_text}")

        # Round 2+ context. A retry only fires when the previous round
        # objectively failed (timeout / non-zero exit / empty output), so
        # frame the context around finishing partial work — not around
        # rebutting a reviewer.
        if previous_round_summary or previous_round_failure:
            header = (
                f"## Previous attempt (round {round_idx - 1}) — RETRY CONTEXT\n"
                "Your previous attempt did not complete cleanly. The container "
                "filesystem still holds whatever was written. Inspect what is "
                "already on disk, then complete (or fix) the task. Do NOT "
                "restart from scratch unless the existing state is unusable."
            )
            blocks: list[str] = [header]
            if previous_round_failure:
                blocks.append(f"Failure mode: {previous_round_failure}")
            if previous_round_summary:
                trimmed = previous_round_summary.strip()
                if len(trimmed) > 4000:
                    trimmed = trimmed[:4000] + "\n\n[... truncated ...]"
                blocks.append(
                    "Last engineer summary (may be partial):\n"
                    f"```\n{trimmed}\n```"
                )
            parts.append("\n\n".join(blocks))

        if review_feedback:
            parts.append(
                f"## Reviewer hint (from round {round_idx - 1})\n"
                f"{review_feedback}"
            )

        parts.append(f"## Task\n{instruction}")

        return "\n\n".join(parts)


def _invoke_reviewer(
    *,
    instruction: str,
    last_msg: str,
    round_idx: int,
    thread_id: str | None,
    main_error: str | None = None,
) -> dict:
    """Pure-sync wrapper around Reviewer.evaluate. Called via to_thread."""
    deps = _import_argus_skill()
    backend = deps["CodexRunnerBackend"](backend="codex")
    reviewer = deps["Reviewer"](backend)
    cfg = deps["ReviewerConfig"](
        model=os.environ.get("ARGUS_SKILL_HARBOR_REVIEWER_MODEL", "gpt-5.4"),
        reasoning_effort=os.environ.get(
            "ARGUS_SKILL_HARBOR_REVIEWER_EFFORT", "medium"
        ),
        skip_git_repo_check=True,
        full_auto=True,
    )
    decision = reviewer.evaluate(
        objective=instruction,
        operator_messages=None,
        round_index=round_idx,
        session_id=thread_id,
        main_summary=last_msg,
        main_error=main_error,
        checks=[],
        config=cfg,
    )
    return {
        "status": decision.status,
        "confidence": decision.confidence,
        "reason": decision.reason,
        "next_action": decision.next_action,
    }


__all__ = ["ArgusSkillCodex"]
