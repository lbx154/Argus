"""Explicit desktop readiness: one real reply plus a call-bound action, no project work."""
from __future__ import annotations

import argparse
import json
import os
import time
from tempfile import TemporaryDirectory
from uuid import uuid4


def probe(backend: str, executable: str) -> dict:
    from ..agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
    from ..core.desktop_selection import prepare_selection
    from ..core.knob_store import read_persisted_knobs
    from ..core.knobs import resolve_role_model
    from ..core.role_actions import ACTION_BACKENDS, role_actions
    from ..manager.form import manager_actions

    if backend not in ACTION_BACKENDS:
        return {"ok": False, "backend": backend, "error": "所选后端不支持 Argus 动作协议，请选择支持的 CLI。"}
    environment = dict(os.environ)
    try:
        os.environ["ARGUS_SKILL_RUNNER_BACKEND"] = backend
        os.environ["ARGUS_DESKTOP_LAUNCH_NONCE"] = "readiness-probe"
        prepare_selection(os.environ, read_persisted_knobs())
        model = resolve_role_model("manager", role_env="ARGUS_SKILL_MANAGER_MODEL", backend=backend)
        challenge = f"ARGUS_READY_{uuid4().hex[:12]}"
        actions = manager_actions("Readiness check only; apply no configuration.", active_mission=False)
        deadline = time.monotonic() + 120
        with TemporaryDirectory(prefix="argus-readiness-") as directory:
            # Pi session state and Argus runner diagnostics are probe-owned.
            # The CLI's own login/configuration roots remain unchanged.
            os.environ["ARGUS_SKILL_HOME"] = directory
            options = RunnerOptions(
                working_dir=directory, model=model or None, disable_tools=True,
                sandbox_mode="read-only", reasoning_effort="low",
                external_interrupt_reason_provider=lambda: "真实调用检查超时（120 秒）" if time.monotonic() >= deadline else None,
            )
            runner = AgentCliRunner(agent_bin=executable, backend=backend)
            with role_actions(runner, options, actions) as bound:
                result = runner.run_exec(
                    prompt=f"This is an explicit CLI readiness check, not project work. Call the Argus hand_off tool once with decision={{'to':'manager','summary':'{challenge}','assumptions':[]}}, then reply with exactly {challenge}. Omit all other fields: no ask, answer, control, config or workflow_setup. Do not use other tools or change files/settings.",
                    resume_thread_id=None, options=bound,
                )
            replied = challenge in str(result.last_agent_message or "")
            confirmed = actions.handoff is not None and actions.handoff.to == "manager" and actions.handoff.summary == challenge
            ok = result.exit_code == 0 and not result.fatal_error and replied and confirmed
            return {"ok": ok, "backend": backend, "model": getattr(result, "usage_model", None) or model,
                    "tool_confirmed": confirmed, "reply_confirmed": replied,
                    "error": "" if ok else (result.fatal_error or actions.last_error or "模型未完成回复与动作工具确认；请检查该 CLI 的登录和工具协议能力。")}
    finally:
        os.environ.clear()
        os.environ.update(environment)


def main() -> int:
    from ..core.secret_guard import known_secret_values, redact_secrets_record

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--executable", required=True)
    args = parser.parse_args()
    try:
        result = probe(args.backend, args.executable)
    except Exception as exc:
        result = {"ok": False, "backend": args.backend, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(redact_secrets_record(result, known_values=known_secret_values()), ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
