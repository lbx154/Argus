"""Private stdio desktop protocol. Preparation never switches the active profile.

The native shell commits settings and the candidate profile together only after
verification. Unlike CLI setup, this path never rewrites persistent model knobs.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import CLIENT_MODEL, TRIAL_URL
from .client import TRIAL_ENV, profile_path, query_status, trial_home, validate_role_overrides
from .storage import write_private


def prepare(api_key: str, progress=print, *, download_progress=None) -> tuple[str, dict]:
    from ..core.backend_readiness import check_backend_readiness
    from ..core.paths import global_root
    from ..tools.setup import _verify_setup_smoke
    from .native_cli import install_native_copilot

    validate_role_overrides()
    progress("正在验证内部测试 Key 和剩余额度…")
    balance = query_status(TRIAL_URL, api_key)
    if balance["tokens_remaining"] <= 0:
        raise ValueError("此内测 Key 的额度已用完，请更换 Key 或使用自己的账号。")
    progress("正在下载并校验 Copilot，首次准备可能需要几分钟…")

    def downloaded(received, total):
        size = f"{received / (1024 * 1024):.1f} MiB"
        if total:
            size += f" / {total / (1024 * 1024):.1f} MiB"
        progress(f"Copilot 下载已接收 {size}；完成后还会校验文件。")
        if download_progress is not None:
            download_progress(received, total)

    executable = install_native_copilot(progress=progress, download_progress=downloaded)
    root = global_root() / "runtime"
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="trial-check-", dir=root) as temporary:
        candidate = Path(temporary) / "profile.json"
        write_private(candidate, json.dumps({"base_url": TRIAL_URL + "/v1", "api_key": api_key}).encode())
        overrides = {
            TRIAL_ENV: "1", "ARGUS_SKILL_MODEL": CLIENT_MODEL,
            "ARGUS_SKILL_RUNNER_BACKEND": "copilot", "ARGUS_SKILL_LIFE_BACKEND": "copilot",
            "ARGUS_SKILL_RUNNER_BIN": executable,
            "ARGUS_SKILL_BACKEND_AUTH_MODE": "subscription_cli",
            "ARGUS_DESKTOP_TRIAL_PROFILE": str(candidate),
        }
        saved = {key: os.environ.get(key) for key in overrides}
        try:
            os.environ.update(overrides)
            progress("正在验证 Copilot 和一次真实模型回复…")
            # The actual smoke below proves authorization. Avoid launching a
            # second persistent ACP worker merely to check the same account.
            report = check_backend_readiness("copilot", runner_bin=executable, probe_auth=False)
            if not report.ok:
                raise ValueError("Copilot 运行检查未通过，请检查程序或科学角色配置后重试。")
            smoke_output = io.StringIO()
            with contextlib.redirect_stdout(smoke_output):
                verified = _verify_setup_smoke("copilot", model=CLIENT_MODEL)
            if not verified:
                detail = smoke_output.getvalue().replace(api_key, "[隐藏]").strip()
                raise ValueError(
                    "真实模型回复验证未通过；原设置未更改。\n" + detail
                )
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
    # Synchronize after the smoke because validation also consumes allowance.
    try:
        balance = query_status(TRIAL_URL, api_key)
    except ValueError:
        balance = {**balance, "stale": True}
    return executable, {**balance, "checked_at": int(time.time())}


def current_status(*, resume_trial: bool = False) -> dict:
    from . import attention

    cache = trial_home() / "trial-status.json"
    try:
        config = json.loads(profile_path().read_text(encoding="utf-8"))
        balance = query_status(str(config["base_url"]).removesuffix("/v1"), str(config["api_key"]))
        error = None
        if resume_trial:
            try:
                if balance["tokens_remaining"] <= 0:
                    raise ValueError("试用额度已用完，请更换 Key 或使用自己的账号。")
                attention.resume()
            except ValueError as exc:
                error = str(exc)
        reason = attention.reason()
        result = {**balance, "checked_at": int(time.time()), "stale": False,
                  "paused": bool(reason), "attention": reason, "error": error}
        write_private(cache, json.dumps(result).encode())
        return result
    except (OSError, ValueError, KeyError):
        previous = {}
        try:
            previous = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        allowed = {k: v for k, v in previous.items() if k in {
            "tokens_remaining", "token_limit", "tokens_used", "checked_at",
        } and type(v) is int and v >= 0}
        # Never echo file contents, an invalid profile or an upstream body.
        reason = attention.reason()
        return {**allowed, "stale": True, "paused": bool(reason), "attention": reason,
                "error": "余额暂未同步，请检查网络后重试。"}


def main() -> int:
    output = sys.stdout
    def emit(**event):
        print(json.dumps(event, ensure_ascii=True), file=output, flush=True)
    key = ""
    try:
        request = json.loads(sys.stdin.readline(4096))
        if not isinstance(request, dict):
            raise ValueError("无效的试用请求。")
        action = request.get("action", "prepare")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            if action in {"status", "resume"} and set(request) == {"action"}:
                emit(event="balance", **current_status(resume_trial=action == "resume"))
                return 0
            if action != "prepare" or set(request) - {"action", "api_key"} or not isinstance(request.get("api_key"), str):
                raise ValueError("请输入有效的内部测试 Key。")
            key = request["api_key"].strip()
            executable, balance = prepare(
                key, progress=lambda message: emit(event="progress", message=message),
                download_progress=lambda downloaded, total: emit(
                    event="download", downloaded_bytes=downloaded, total_bytes=total,
                ),
            )
            emit(event="complete", runner_bin=executable, balance=balance)
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        message = str(exc).replace(key, "[隐藏]") if key else str(exc)
        emit(event="error", message=message)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
