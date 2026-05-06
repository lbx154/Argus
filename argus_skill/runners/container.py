"""ContainerCodexRunner — runs ``codex exec`` inside a duck-typed environment.

The :class:`argus_skill.mission.engine.MissionLoopEngine` treats its
``runner`` attribute as a duck-typed object satisfying the
:class:`argus_skill.runners.RunBackend` protocol::

    run_exec(*, prompt, resume_thread_id, options, run_label) -> CodexRunResult

This module supplies that contract for any caller that has a
container-like environment exposing an async ``.exec(command, env,
timeout_sec) -> ExecResult``. The benchmark harness (Harbor) is the
primary consumer today; future REPL sandbox modes (see Phase 3) will
plug a host-side ``LocalDockerEnv`` into the same runner.

Sync-on-the-outside / async-on-the-inside: the engine is sync and
calls ``run_exec`` from a worker thread (see ``_do_run`` in the
benchmark adapter, which uses ``asyncio.to_thread`` to drive
``engine.run()``). Inside ``run_exec`` we hop back onto the original
asyncio loop using ``asyncio.run_coroutine_threadsafe`` to drive
``environment.exec``.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import shlex
import time
from dataclasses import dataclass
from typing import Any, Callable

# Harbor types are deliberately late-imported by harbor_adapter; we
# accept duck-typed objects here so this module stays import-clean
# even when Harbor isn't installed (matters for unit tests).

log = logging.getLogger(__name__)


# In-container launcher script. Installed once into the container's
# agent_dir (see ContainerCodexRunner._ensure_launcher_in_container)
# and invoked per round with positional args:
#
#   bash __argus-launch.sh <prompt_file> <log_file> <out_file> \
#                          <exit_file> <model> <resume_thread_or_empty> \
#                          [extra codex flags...]
#
# The launcher backgrounds codex in its own session (so we can kill
# the whole tree by negative-PID signal on timeout) and prints the
# group leader PID so the host poller can target it.
_LAUNCHER_SCRIPT = r"""#!/bin/bash
# Argus-Skill in-container codex launcher.
set -u

if [ "$#" -lt 6 ]; then
  echo "usage: $0 <prompt> <log> <out> <exit> <model> <resume_or_empty> [flags...]" >&2
  exit 64
fi

PROMPT_FILE="$1"; LOG_FILE="$2"; OUT_FILE="$3"
EXIT_FILE="$4"; MODEL="$5"; RESUME="$6"
shift 6
EXTRA_FLAGS=("$@")

: > "$LOG_FILE"
rm -f "$EXIT_FILE"

if [ -s "$HOME/.nvm/nvm.sh" ]; then . "$HOME/.nvm/nvm.sh"; fi

PROMPT="$(cat "$PROMPT_FILE")"

if [ -n "$RESUME" ]; then
  CODEX_CMD=(codex exec resume)
  POSITIONAL=("$RESUME" "$PROMPT")
else
  CODEX_CMD=(codex exec)
  POSITIONAL=("$PROMPT")
fi
CODEX_CMD+=(
  --dangerously-bypass-approvals-and-sandbox
  --skip-git-repo-check
  --model "$MODEL"
  --json
  --enable unified_exec
)
if [ "${#EXTRA_FLAGS[@]}" -gt 0 ]; then
  CODEX_CMD+=("${EXTRA_FLAGS[@]}")
fi

# Background a session leader so the host can kill the whole tree
# via ``kill -TERM -<pgid>`` on timeout. ``set -m`` enables job
# control, which gives each backgrounded pipeline its own process
# group (pgid == leader pid). Using a subshell ``(...)`` (rather
# than ``bash -c``) preserves access to the arrays we built above.
set -m
(
  set -o pipefail
  "${CODEX_CMD[@]}" -- "${POSITIONAL[@]}" 2>&1 </dev/null \
    | tee -a "$LOG_FILE" "$OUT_FILE" >/dev/null
  echo "${PIPESTATUS[0]}" > "$EXIT_FILE"
) </dev/null >/dev/null 2>&1 &
PID=$!
disown
echo "$PID"
"""


@dataclass
class ContainerCodexRunnerConfig:
    """All knobs ContainerCodexRunner needs.

    The Harbor-specific bits (``environment``, ``env_vars``) are kept
    out of this dataclass so it stays serialisable / inspectable in
    tests; pass them positionally to ``ContainerCodexRunner``.
    """
    model: str
    cli_flags_arg: str
    skill_text: str
    skill_name: str | None
    round_timeout: int
    output_filename: str  # e.g. "codex.txt"
    agent_dir_posix: str  # e.g. "/agent"
    verify_cmd: str = ""
    verify_timeout: int = 300
    # Phase 4: pre-run the official TB v2 verifier inside the container.
    # When non-empty, the runner uploads this host path to /tests/ on
    # first round and auto-sets ``verify_cmd = bash /tests/test.sh`` if
    # ``verify_cmd`` is empty. The verify result becomes advisory
    # evidence for the reviewer (NOT promoted to a fatal_error).
    tests_src_dir: str = ""
    # Container-side path where ``tests_src_dir`` is uploaded.
    tests_dir_posix: str = "/tests"
    # When True (Phase 4 default), a non-zero verify_cmd exit is NOT
    # promoted to fatal_error — instead the verify exit/stdout/stderr
    # are surfaced to the reviewer as evidence so the reviewer (which
    # also runs in-container with shell access) can decide what to do.
    # Set False to restore the legacy "verify failure aborts the round"
    # behaviour.
    verify_advisory: bool = True
    augmented_max_chars: int = 200_000
    # Live streaming (set to <=0 to disable polling and fall back to
    # one-shot exec). 2s strikes a balance between freshness and
    # docker-exec call cost.
    stream_poll_interval: float = 2.0
    # After the engineer round completes (success or failure), run this
    # short shell snippet inside the container to capture independent
    # runtime evidence (file listings, listening ports, recent processes)
    # and surface it to the reviewer via verification_context. This
    # closes the "reviewer trusts the engineer's self-report" gap. Set
    # to "" to disable.
    state_probe_cmd: str = (
        "echo '== /app contents =='; "
        "ls -la /app 2>/dev/null | head -60 || true; "
        "echo '== listening tcp ports =='; "
        "(ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null || true) | head -30; "
        "echo '== recent processes =='; "
        "ps -ef 2>/dev/null | tail -25 || true; "
        "echo '== /app output files =='; "
        "for f in /app/output.* /app/result.* /app/answer.* /app/*.toml /app/*.json; do "
        "  [ -f \"$f\" ] && { echo \"--- $f ---\"; head -c 800 \"$f\"; echo; }; "
        "done 2>/dev/null || true"
    )
    state_probe_timeout: int = 30
    state_probe_max_chars: int = 6000
    stream_progress_max_chars: int = 600


class ContainerCodexRunner:
    """Drive one engineer round inside a Harbor container.

    The engine calls ``run_exec`` once per round. We:
      1. Render ``skill_text`` (if any) above the engine-supplied prompt.
      2. ``codex exec`` (or ``codex exec resume <thread>``) inside the
         container via ``environment.exec`` proxied through the host
         asyncio loop.
      3. Optionally run ``verify_cmd`` to surface authentic exit codes
         beyond the engineer's prose claim.
      4. Parse the JSONL stream for ``thread_id`` + agent_messages and
         return a ``CodexRunResult``-shaped object so the engine can
         read ``last_agent_message``, ``exit_code``, ``thread_id``,
         ``turn_failed``, ``fatal_error`` unchanged.
    """

    def __init__(
        self,
        *,
        environment: Any,
        env_vars: dict[str, str],
        config: ContainerCodexRunnerConfig,
        loop: asyncio.AbstractEventLoop,
        codex_run_result_cls: Any,
        agent_message_parser: Any,
        thread_id_extractor: Any,
        logger: logging.Logger | None = None,
        event_sink: Callable[[dict], None] | None = None,
    ) -> None:
        self.environment = environment
        self.env_vars = env_vars
        self.config = config
        self._loop = loop
        self._codex_run_result_cls = codex_run_result_cls
        self._parse_agent_messages = agent_message_parser
        self._extract_thread_id = thread_id_extractor
        self._round_idx = 0
        self.logger = logger or log
        self._event_sink = event_sink
        # Per-round line buffer for streaming JSONL parsing.
        self._stream_buffer = ""
        # Set on first round; the launcher script is installed once
        # per container lifetime in ``_ensure_launcher_in_container``.
        self._launcher_written = False
        # Phase 4: tests/ uploaded once per container lifetime when
        # ``config.tests_src_dir`` is set.
        self._tests_uploaded = False
        # Latest advisory verifier outcome (set in run_exec after each
        # round). ``None`` until the first verify_cmd actually runs.
        self.last_verify_exit: int | None = None
        self.last_verify_stdout: str = ""
        self.last_verify_stderr: str = ""
        self.last_verify_cmd: str = ""

    # ------------------------------------------------------------------
    # Public — duck-typed CodexRunner.run_exec surface
    # ------------------------------------------------------------------

    def run_exec(
        self,
        *,
        prompt: str,
        resume_thread_id: str | None,
        options: Any,
        run_label: str | None = None,
    ):
        """Synchronously drive one engineer round inside the container."""
        if run_label != "main":
            return self._stub_result_for_non_main(run_label)

        self._round_idx += 1
        round_idx = self._round_idx

        full_prompt = self._compose_prompt(prompt)
        if len(full_prompt) > self.config.augmented_max_chars:
            self.logger.warning(
                "round %d prompt too large (%d chars); dropping skill text",
                round_idx, len(full_prompt),
            )
            full_prompt = prompt  # drop skill_text on overflow

        # Phase 4: lazily upload the official TB v2 verifier tests/
        # directory the first time we run. This makes /tests/test.sh
        # available to ``verify_cmd`` (auto-set below if user left it
        # blank) and to the in-container reviewer.
        if self.config.tests_src_dir and not self._tests_uploaded:
            try:
                self._await(self._upload_tests())
                self._tests_uploaded = True
            except Exception as exc:  # noqa: BLE001
                self.logger.warning(
                    "tests upload failed (%s); verify_cmd will likely fail",
                    exc,
                )

        round_t0 = time.time()
        stdout = ""
        exit_code = -1
        fatal_error: str | None = None
        try:
            stdout, exit_code = self._await(
                self._exec_codex_round(
                    prompt=full_prompt,
                    resume_thread_id=resume_thread_id,
                    round_idx=round_idx,
                )
            )
        except RuntimeError as exc:
            fatal_error = f"container_exec_runtime_error:{exc}"
            self.logger.warning(
                "round %d engineer raised RuntimeError: %s", round_idx, exc
            )
        except Exception as exc:  # pragma: no cover - defensive
            fatal_error = f"container_exec_exception:{type(exc).__name__}:{exc}"
            self.logger.warning(
                "round %d engineer raised %s: %s",
                round_idx, type(exc).__name__, exc,
            )
        elapsed = time.time() - round_t0

        agent_messages = self._parse_agent_messages(stdout) if stdout else []
        thread_id = (
            self._extract_thread_id(stdout) if stdout else None
        ) or resume_thread_id

        # Effective verify command: caller-supplied wins; otherwise we
        # auto-fill from the uploaded TB v2 test.sh.
        effective_verify_cmd = self.config.verify_cmd
        if not effective_verify_cmd and self._tests_uploaded:
            effective_verify_cmd = (
                f"bash {self.config.tests_dir_posix}/test.sh"
            )

        # Optional in-container self-verify. Run AFTER the engineer
        # round. In Phase 4 (verify_advisory=True) the result is
        # surfaced to the reviewer as evidence instead of being
        # promoted to fatal_error — the reviewer (also in-container)
        # decides what to do with it.
        verify_stdout = ""
        verify_stderr = ""
        verify_exit: int | None = None
        if (
            effective_verify_cmd
            and fatal_error is None
            and exit_code == 0
            and agent_messages
        ):
            try:
                verify_stdout, verify_stderr, verify_exit = self._await(
                    self._exec_verify(
                        round_idx=round_idx,
                        cmd_override=effective_verify_cmd,
                    )
                )
            except Exception as exc:
                self.logger.warning(
                    "round %d verify_cmd raised %s: %s — ignoring",
                    round_idx, type(exc).__name__, exc,
                )
            else:
                if verify_exit != 0 and not self.config.verify_advisory:
                    fatal_error = (
                        f"verify_cmd_failed exit={verify_exit}\n"
                        f"stderr_tail:\n{verify_stderr[-1500:]}\n"
                        f"stdout_tail:\n{verify_stdout[-1500:]}"
                    )

        # Independent runtime probe: capture container state regardless
        # of how the round ended, so the reviewer sees facts (running
        # services, files written, output values) instead of relying on
        # the engineer's prose. Best-effort; errors are swallowed.
        runtime_probe = ""
        if self.config.state_probe_cmd:
            try:
                runtime_probe = self._await(
                    self._exec_state_probe(round_idx=round_idx)
                )
            except Exception as exc:  # noqa: BLE001
                self.logger.warning(
                    "round %d state_probe raised %s: %s — ignoring",
                    round_idx, type(exc).__name__, exc,
                )

        self.logger.info(
            "round %d engineer: exit=%d, %d agent_messages, %.1fs%s%s%s",
            round_idx,
            exit_code,
            len(agent_messages),
            elapsed,
            f" verify_exit={verify_exit}" if verify_exit is not None else "",
            f" probe={len(runtime_probe)}B" if runtime_probe else "",
            f" fatal={fatal_error}" if fatal_error else "",
        )

        result = self._build_result(
            stdout=stdout,
            exit_code=exit_code,
            thread_id=thread_id,
            agent_messages=agent_messages,
            fatal_error=fatal_error,
        )
        # Side-channel for the engine: surfaced into verification_context
        # by MissionLoopEngine._build_verification_context.
        if runtime_probe:
            try:
                setattr(result, "runtime_probe", runtime_probe)
            except Exception:  # noqa: BLE001
                pass
        # Phase 4: attach verifier evidence (the official TB v2 verifier
        # exit/stdout/stderr) for the reviewer.
        if verify_exit is not None:
            try:
                setattr(result, "verify_exit", int(verify_exit))
                setattr(result, "verify_stdout_tail", (verify_stdout or "")[-3000:])
                setattr(result, "verify_stderr_tail", (verify_stderr or "")[-1500:])
                setattr(result, "verify_cmd", effective_verify_cmd)
            except Exception:  # noqa: BLE001
                pass
            # Side-channel for the Harbor adapter's monkey-patched
            # Verifier.verify: register THIS round's advisory result so
            # the host-side scorer can adopt it instead of re-running
            # /tests/test.sh on a mutated /app. Keyed on the
            # environment object's identity, which is shared between
            # the runner and Harbor's trial-level Verifier call.
            try:
                from argus_skill.runners._verify_cache import (
                    register_verify_result,
                )
                register_verify_result(
                    id(self.environment),
                    exit_code=int(verify_exit),
                    stdout=verify_stdout or "",
                    stderr=verify_stderr or "",
                    cmd=effective_verify_cmd,
                )
            except Exception:  # noqa: BLE001
                # Cache registration is best-effort; the adapter's
                # patch falls back to Harbor's verifier when no entry
                # is present.
                pass
            # Convenience attributes for callers / tests that prefer
            # to read off the runner directly.
            self.last_verify_exit = int(verify_exit)
            self.last_verify_stdout = verify_stdout or ""
            self.last_verify_stderr = verify_stderr or ""
            self.last_verify_cmd = effective_verify_cmd
        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _compose_prompt(self, engine_prompt: str) -> str:
        # Operational guidance every engineer round. Cheap (~150 chars),
        # high-leverage: stops daemons from being reaped when the
        # codex CLI exits at end-of-round, and forces the engineer to
        # produce verifiable evidence rather than self-reporting.
        ops_note = (
            "## Operational notes (read first)\n"
            "- If your solution requires a long-running background service (HTTP "
            "server, daemon, etc), launch it with "
            "`setsid nohup <cmd> >/tmp/svc.log 2>&1 </dev/null &` "
            "and verify it is actually listening (e.g. `curl localhost:<port>` "
            "or `ss -tlnp | grep <port>`) **before** reporting done — "
            "otherwise it may be reaped when this turn ends.\n"
            "- Before claiming success, run a concrete verification command "
            "(test, curl, diff against the spec) and quote its output verbatim "
            "in your final reply. Do not rely on self-assessment alone.\n"
        )
        skill = (self.config.skill_text or "").strip()
        if not skill:
            return f"{ops_note}\n{engine_prompt}"
        intro = (
            "You have been provided with a reusable skill guide for tasks of "
            "this type. Read it carefully, then solve the task below."
        )
        return (
            f"{ops_note}\n{intro}\n\n## Skill guide\n{skill}\n\n{engine_prompt}"
        )

    @dataclass
    class _RoundPaths:
        """In-container paths for one round's artifacts."""
        prompt: str
        log: str
        out: str
        exit: str

    def _round_paths(self, round_idx: int) -> "ContainerCodexRunner._RoundPaths":
        ad = self.config.agent_dir_posix
        return ContainerCodexRunner._RoundPaths(
            prompt=f"{ad}/argus-skill-round-{round_idx}.prompt",
            log=f"{ad}/argus-skill-round-{round_idx}.txt",
            out=f"{ad}/{self.config.output_filename}",
            exit=f"{ad}/argus-skill-round-{round_idx}.exit",
        )

    @property
    def _launcher_path(self) -> str:
        return f"{self.config.agent_dir_posix}/__argus-launch.sh"

    async def _exec_codex_round(
        self,
        *,
        prompt: str,
        resume_thread_id: str | None,
        round_idx: int,
    ) -> tuple[str, int]:
        """Launch ``codex exec`` in the background and stream its log.

        ``environment.exec`` only returns full stdout at command
        completion, so we can't get true streaming through it. Instead
        we install a tiny launcher script in the container once, then
        per round:

          1. Drop the prompt into a file (base64 → no shell escaping).
          2. Invoke the launcher, which backgrounds codex and prints
             the launcher PID. The launcher writes JSONL output to
             ``round-N.txt`` (and appends to ``codex.txt``) and writes
             the codex return code to ``round-N.exit`` on completion.
          3. Poll: each poll runs two trivial exec calls — read
             ``round-N.exit`` (status), read new bytes from
             ``round-N.txt`` since the last offset (live progress).
          4. Parse newly-arrived JSONL lines and emit
             ``engineer.progress`` events through the engine's sink.
          5. When the exit file appears, read the full log and return.

        Falls back to a single blocking exec when
        ``stream_poll_interval <= 0`` (debug-only).
        """
        cfg = self.config
        if cfg.stream_poll_interval <= 0:
            return await self._exec_codex_round_blocking(
                prompt=prompt,
                resume_thread_id=resume_thread_id,
                round_idx=round_idx,
            )

        paths = self._round_paths(round_idx)
        self._stream_buffer = ""

        await self._ensure_launcher_in_container()
        if not await self._write_prompt_file(prompt, paths.prompt):
            return "", -1

        pid = await self._launch_round(
            paths=paths,
            resume_thread_id=resume_thread_id,
        )
        if pid is None:
            return "", -1

        self.logger.info(
            "round %d engineer launched pid=%s; polling %s every %.1fs",
            round_idx, pid, paths.log, cfg.stream_poll_interval,
        )

        final_exit = await self._poll_until_done(
            paths=paths,
            pid=pid,
            round_idx=round_idx,
        )

        # Drain any final partial line.
        if self._stream_buffer.strip():
            self._emit_progress_lines("\n", round_idx=round_idx)

        full_stdout = await self._read_full_log(paths.log, round_idx=round_idx)
        return full_stdout, final_exit

    # --- one-time launcher install ------------------------------------

    async def _ensure_launcher_in_container(self) -> None:
        """Write ``__argus-launch.sh`` to the container's agent_dir once."""
        if self._launcher_written:
            return
        script = _LAUNCHER_SCRIPT
        b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
        cmd = (
            f"mkdir -p {self.config.agent_dir_posix} && "
            f"printf '%s' {shlex.quote(b64)} | base64 -d "
            f"> {self._launcher_path} && chmod +x {self._launcher_path}"
        )
        res = await self.environment.exec(
            command=cmd, env=self.env_vars, timeout_sec=60,
        )
        if int(res.return_code) != 0:
            raise RuntimeError(
                f"failed to install argus-skill launcher in container: "
                f"rc={res.return_code} stderr={getattr(res, 'stderr', '')}"
            )
        self._launcher_written = True

    # --- per-round helpers --------------------------------------------

    async def _write_prompt_file(self, prompt: str, prompt_path: str) -> bool:
        b64 = base64.b64encode(prompt.encode("utf-8")).decode("ascii")
        cmd = (
            f"printf '%s' {shlex.quote(b64)} | base64 -d > {prompt_path}"
        )
        res = await self.environment.exec(
            command=cmd, env=self.env_vars, timeout_sec=60,
        )
        if int(res.return_code) != 0:
            self.logger.warning(
                "failed to write prompt file (rc=%s): %s",
                res.return_code, getattr(res, "stderr", ""),
            )
            return False
        return True

    async def _launch_round(
        self,
        *,
        paths: "ContainerCodexRunner._RoundPaths",
        resume_thread_id: str | None,
    ) -> str | None:
        """Invoke the launcher; it backgrounds codex and echoes PID."""
        cfg = self.config
        # Position the launcher's positional arguments. The launcher
        # consumes them as: <prompt> <log> <out> <exit> <model> <resume>
        # then "$@" for extra cli flags.
        argv = [
            "bash", self._launcher_path,
            paths.prompt, paths.log, paths.out, paths.exit,
            cfg.model, (resume_thread_id or ""),
        ]
        # Append free-form extra cli flags from cli_flags_arg as
        # additional positional args. Splitting via shlex preserves
        # quoting semantics (e.g. ``-c reasoning_effort=high``).
        argv.extend(shlex.split(cfg.cli_flags_arg or ""))
        argv_str = " ".join(shlex.quote(a) for a in argv)
        res = await self.environment.exec(
            command=argv_str, env=self.env_vars, timeout_sec=60,
        )
        if int(res.return_code) != 0:
            self.logger.warning(
                "failed to launch codex in container (rc=%s, err=%s)",
                res.return_code, getattr(res, "stderr", ""),
            )
            return None
        # Launcher prints the bg group PID on its last stdout line.
        out = (res.stdout or "").strip().splitlines()
        return out[-1].strip() if out else None

    async def _poll_until_done(
        self,
        *,
        paths: "ContainerCodexRunner._RoundPaths",
        pid: str,
        round_idx: int,
    ) -> int:
        cfg = self.config
        offset = 0
        deadline = time.monotonic() + cfg.round_timeout

        while time.monotonic() < deadline:
            is_done, rc, new_text = await self._poll_state(
                exit_path=paths.exit,
                log_path=paths.log,
                offset=offset,
            )
            if new_text:
                offset += len(new_text.encode("utf-8"))
                self._emit_progress_lines(new_text, round_idx=round_idx)
            if is_done:
                return rc if rc is not None else -1
            await asyncio.sleep(cfg.stream_poll_interval)

        # Timeout — kill the process group and return the conventional
        # 124 (matches GNU ``timeout(1)``).
        self.logger.warning(
            "round %d engineer exceeded round_timeout=%ds; killing pid=%s",
            round_idx, cfg.round_timeout, pid,
        )
        with contextlib.suppress(Exception):
            await self.environment.exec(
                command=(
                    f"kill -TERM -{pid} 2>/dev/null; sleep 2; "
                    f"kill -KILL -{pid} 2>/dev/null; "
                    f"kill -TERM {pid} 2>/dev/null; "
                    f"kill -KILL {pid} 2>/dev/null; true"
                ),
                env=self.env_vars,
                timeout_sec=30,
            )
        return 124

    async def _poll_state(
        self,
        *,
        exit_path: str,
        log_path: str,
        offset: int,
    ) -> tuple[bool, int | None, str]:
        """Read status + new bytes via two short, focused exec calls.

        Returns (is_done, exit_code_or_None, new_text).
        """
        # Run them concurrently — saves ~one docker-exec roundtrip
        # of latency per poll without changing semantics.
        status_task = asyncio.create_task(
            self.environment.exec(
                command=f"cat {exit_path} 2>/dev/null || true",
                env=self.env_vars, timeout_sec=30,
            )
        )
        tail_task = asyncio.create_task(
            self.environment.exec(
                command=f"tail -c +{offset + 1} {log_path} 2>/dev/null || true",
                env=self.env_vars, timeout_sec=30,
            )
        )
        try:
            status_res, tail_res = await asyncio.gather(
                status_task, tail_task, return_exceptions=False,
            )
        except Exception as exc:
            self.logger.warning("poll exec failed: %s — retrying", exc)
            return False, None, ""

        new_text = tail_res.stdout or ""
        status = (status_res.stdout or "").strip()
        if not status:
            return False, None, new_text
        first = status.splitlines()[0].strip()
        try:
            return True, int(first), new_text
        except (ValueError, TypeError):
            return True, -1, new_text

    async def _read_full_log(self, log_path: str, *, round_idx: int) -> str:
        """Read the full per-round log (canonical source for parsers)."""
        try:
            res = await self.environment.exec(
                command=f"cat {log_path} 2>/dev/null || true",
                env=self.env_vars, timeout_sec=120,
            )
            return res.stdout or ""
        except Exception as exc:
            self.logger.warning(
                "round %d: failed to read final log: %s", round_idx, exc,
            )
            return ""

    async def _exec_codex_round_blocking(
        self,
        *,
        prompt: str,
        resume_thread_id: str | None,
        round_idx: int,
    ) -> tuple[str, int]:
        """Legacy one-shot exec path (no live streaming)."""
        cfg = self.config
        paths = self._round_paths(round_idx)
        escaped = shlex.quote(prompt)
        if resume_thread_id:
            quoted_session = shlex.quote(resume_thread_id)
            codex_invocation = (
                "codex exec resume "
                "--dangerously-bypass-approvals-and-sandbox "
                "--skip-git-repo-check "
                f"--model {cfg.model} --json --enable unified_exec "
                f"{cfg.cli_flags_arg}"
                f"-- {quoted_session} {escaped}"
            )
        else:
            codex_invocation = (
                "codex exec "
                "--dangerously-bypass-approvals-and-sandbox "
                "--skip-git-repo-check "
                f"--model {cfg.model} --json --enable unified_exec "
                f"{cfg.cli_flags_arg}"
                f"-- {escaped}"
            )
        cmd = (
            "set -o pipefail; "
            "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; "
            f"{codex_invocation} 2>&1 </dev/null | tee {paths.log} {paths.out}"
        )
        result = await self.environment.exec(
            command=cmd, env=self.env_vars, timeout_sec=cfg.round_timeout,
        )
        return (result.stdout or ""), int(result.return_code)

    # ------------------------------------------------------------------
    # Live progress: parse codex JSONL stream and forward to event_sink
    # ------------------------------------------------------------------

    def _emit_progress_lines(self, new_text: str, *, round_idx: int) -> None:
        """Parse newly-arrived stdout bytes and emit progress events.

        Codex emits one JSON event per line. We buffer partial lines
        across polls. Only ``item.completed`` items with extractable
        text become ``engineer.progress`` events; everything else is
        protocol noise (thread.started, turn.started, etc.).
        """
        if not self._event_sink or not new_text:
            return
        self._stream_buffer += new_text
        while "\n" in self._stream_buffer:
            line, self._stream_buffer = self._stream_buffer.split("\n", 1)
            line = line.strip()
            if not line or line[0] != "{":
                continue
            try:
                event = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(event, dict):
                continue
            if str(event.get("type") or "") != "item.completed":
                continue
            item = event.get("item") or {}
            if not isinstance(item, dict):
                continue
            kind = str(item.get("type") or "message")
            text = self._extract_progress_text(item)
            if not text:
                continue
            cap = self.config.stream_progress_max_chars
            if cap > 0 and len(text) > cap:
                text = text[: cap - 1].rstrip() + "…"
            payload = {
                "type": "engineer.progress",
                "kind": kind,
                "text": text,
                "round": round_idx,
            }
            try:
                self._event_sink(payload)
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _extract_progress_text(item: dict[str, Any]) -> str:
        """Best-effort text extraction from a codex item.completed payload."""
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
        content = item.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for piece in content:
                if isinstance(piece, dict):
                    t = piece.get("text")
                    if isinstance(t, str) and t.strip():
                        parts.append(t.strip())
            if parts:
                return "\n".join(parts).strip()
        if isinstance(content, str) and content.strip():
            return content.strip()
        cmd = item.get("command") or item.get("name")
        if isinstance(cmd, str) and cmd.strip():
            return cmd.strip()
        if isinstance(cmd, list):
            try:
                return " ".join(str(p) for p in cmd).strip()
            except Exception:  # noqa: BLE001
                return ""
        return ""

    async def _exec_verify(
        self,
        *,
        round_idx: int,
        cmd_override: str | None = None,
    ) -> tuple[str, str, int]:
        cfg = self.config
        cmd = (cmd_override or cfg.verify_cmd).strip()
        result = await self.environment.exec(
            command=f"set -o pipefail; {cmd}",
            env=self.env_vars,
            timeout_sec=cfg.verify_timeout,
        )
        stdout = result.stdout or ""
        stderr = getattr(result, "stderr", None) or ""
        exit_code = int(result.return_code)
        self.logger.info(
            "round %d verify_cmd: exit=%d stdout=%dB stderr=%dB cmd=%s",
            round_idx, exit_code, len(stdout), len(stderr), cmd[:80],
        )
        return stdout, stderr, exit_code

    async def _upload_tests(self) -> None:
        """Upload the host TB v2 tests/ directory into the container.

        Idempotent at the runner level (``_tests_uploaded`` flag in
        ``run_exec``). Uses ``environment.upload_dir`` which every
        Harbor environment backend supports. Target is
        ``config.tests_dir_posix`` (default ``/tests``).

        Harbor's verifier runs ``reset_dirs([tests_dir, verifier_dir])``
        before its own verification step, so anything we put here is
        cleaned up automatically — there's no risk of polluting the
        official verifier run.
        """
        cfg = self.config
        if not cfg.tests_src_dir:
            return
        target = cfg.tests_dir_posix
        # Ensure parent exists & dir is writable. mkdir -p is idempotent.
        await self.environment.exec(
            command=f"mkdir -p {target} && chmod 0777 {target} 2>/dev/null || true",
            env=self.env_vars, timeout_sec=30,
        )
        try:
            await self.environment.upload_dir(
                source_dir=cfg.tests_src_dir,
                target_dir=target,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(
                "upload_dir(%s -> %s) failed: %s",
                cfg.tests_src_dir, target, exc,
            )
            raise
        self.logger.info(
            "uploaded TB v2 tests %s -> %s", cfg.tests_src_dir, target,
        )

    async def _exec_state_probe(self, *, round_idx: int) -> str:
        """Run state_probe_cmd and return truncated stdout.

        Probe failures are non-fatal — we just return what we got. The
        probe runs *after* the engineer round so any services it
        launched are observable by the host.
        """
        cfg = self.config
        try:
            result = await self.environment.exec(
                command=f"set +e; {cfg.state_probe_cmd}",
                env=self.env_vars,
                timeout_sec=cfg.state_probe_timeout,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(
                "round %d state_probe exec failed: %s", round_idx, exc,
            )
            return ""
        text = (result.stdout or "")
        if cfg.state_probe_max_chars > 0 and len(text) > cfg.state_probe_max_chars:
            head = cfg.state_probe_max_chars // 2
            tail = cfg.state_probe_max_chars - head - 32
            text = text[:head] + "\n…[truncated]…\n" + text[-tail:]
        return text

    def _await(self, coro):
        """Run an async coroutine on the captured event loop and block.

        We're called from a thread (engine.run() lives in
        asyncio.to_thread). The host loop is still running and will
        actually execute the coroutine.
        """
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    def _build_result(
        self,
        *,
        stdout: str,
        exit_code: int,
        thread_id: str | None,
        agent_messages: list[str],
        fatal_error: str | None,
    ):
        cls = self._codex_run_result_cls
        kwargs: dict[str, Any] = dict(
            command=["codex", "exec", "(in container)"],
            exit_code=exit_code,
            thread_id=thread_id,
            agent_messages=list(agent_messages),
            json_events=[],
            stdout_lines=stdout.splitlines() if stdout else [],
            stderr_lines=[],
            turn_completed=(exit_code == 0 and fatal_error is None),
            turn_failed=(exit_code != 0 or fatal_error is not None),
            fatal_error=fatal_error,
        )
        return cls(**kwargs)

    def _stub_result_for_non_main(self, run_label: str | None):
        """Engine occasionally fires non-main rounds (final report etc.).

        We don't run those inside the Harbor container — return a no-op
        result. The mission engine doesn't issue these unless plan_mode
        is on; benchmark mode pins plan_mode=off.
        """
        cls = self._codex_run_result_cls
        return cls(
            command=["argus-skill", "container-runner", "noop", str(run_label)],
            exit_code=0,
            thread_id=None,
            agent_messages=[""],
            json_events=[],
            stdout_lines=[],
            stderr_lines=[],
            turn_completed=True,
            turn_failed=False,
            fatal_error=None,
        )


class ContainerReviewerBackend(ContainerCodexRunner):
    """Reviewer codex running inside the *same* container as the engineer.

    Phase 4: replaces the host-side ``CodexRunnerBackend`` for review.
    The reviewer now has shell access in the live container and can
    inspect the engineer's work directly (cat output files, curl
    services, etc.) instead of guessing from prose.

    Implementation: subclass of :class:`ContainerCodexRunner` that
      * uses a ``reviewer-N`` file-name prefix in the shared agent dir
        (so it doesn't collide with engineer rounds);
      * pass-through composes the prompt (no skill_text / ops_note —
        the caller's reviewer prompt is already self-contained);
      * skips post-round ``verify_cmd`` and ``state_probe_cmd`` (the
        reviewer is *itself* the verification step).
    """

    # The wrapper accepts the same kwargs ContainerCodexRunner needs.
    # Caller is expected to construct a ``ContainerCodexRunnerConfig``
    # tailored for reviewer use (empty ``skill_text`` / ``verify_cmd``
    # / ``state_probe_cmd`` / ``tests_src_dir``; reviewer model + effort
    # via ``model`` + ``cli_flags_arg``).

    # -- naming ------------------------------------------------------------

    def _round_paths(self, round_idx: int) -> "ContainerCodexRunner._RoundPaths":
        ad = self.config.agent_dir_posix
        return ContainerCodexRunner._RoundPaths(
            prompt=f"{ad}/argus-skill-reviewer-{round_idx}.prompt",
            log=f"{ad}/argus-skill-reviewer-{round_idx}.txt",
            out=f"{ad}/{self.config.output_filename}",
            exit=f"{ad}/argus-skill-reviewer-{round_idx}.exit",
        )

    # -- prompt: pass through (no ops_note/skill prefix) -------------------

    def _compose_prompt(self, engine_prompt: str) -> str:
        return engine_prompt

    # -- run_exec: drive a codex round, accept reviewer's run_label --------

    def run_exec(
        self,
        *,
        prompt: str,
        options: Any,
        run_label: str | None = None,
        resume_thread_id: str | None = None,
    ):
        # The engine always passes run_label="reviewer" for review
        # rounds; ContainerCodexRunner.run_exec gates on "main" only,
        # so we delegate to the parent with run_label forced to "main"
        # for the inner check (the reviewer-specific naming + prompt
        # pass-through above keep the artifacts separate).
        return super().run_exec(
            prompt=prompt,
            options=options,
            run_label="main",
            resume_thread_id=resume_thread_id,
        )


__all__ = [
    "ContainerCodexRunner",
    "ContainerCodexRunnerConfig",
    "ContainerReviewerBackend",
]
