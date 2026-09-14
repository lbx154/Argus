"""Stop automatic paid retries after a hosted-trial failure until user review."""
from __future__ import annotations

import hashlib
import json
import math
import os
import time

from .client import profile_path, trial_home
from .storage import write_private

MESSAGES = {
    "invalid_trial_key": "内测 Key 无效，请更换 Key。",
    "trial_quota_exceeded": "余额不足以预留本次请求；有剩余额度也可能无法执行较大的任务。",
    "trial_busy": "试用服务的 10 路并发已满，请至少等待 5 秒。",
    "trial_tpm_exceeded": "试用服务的 TPM 预留已满，请至少等待 60 秒。",
    "trial_not_ready": "试用服务的上游账号尚未就绪。",
    "provider_unavailable": "上游暂时无法完成请求。",
    "provider_connection_failed": "上游连接中断，用量可能已被预留。",
    "provider_usage_missing": "上游未返回有效用量，网关会保留预留扣费。",
    "provider_stream_incomplete": "模型流未完整结束，网关会保留预留扣费。",
    "provider_stream_failed": "模型流处理失败，请先检查剩余额度。",
    "provider_protocol_error": "上游返回了不完整或不支持的响应。",
}
REQUEST_FAILED_MESSAGE = (
    "试用调用未正常结束；已暂停后续请求，以避免用量不明时重复扣费。"
)


def _path():
    return trial_home() / "trial-attention.json"


def _fingerprint():
    config = json.loads(profile_path().read_text(encoding="utf-8"))
    return hashlib.sha256(str(config["api_key"]).encode()).hexdigest()


def state() -> dict:
    if os.environ.get("ARGUS_DESKTOP_TRIAL_PROFILE"):
        return {}  # Explicit onboarding has already checked the server balance.
    try:
        row = json.loads(_path().read_text(encoding="utf-8"))
        return row if row.get("profile") == _fingerprint() else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, KeyError):
        return {"message": "试用状态无法读取，请在设置中重新验证 Key。"}


def reason() -> str:
    row = state()
    if not row:
        return ""
    # Old generic pauses may contain misleading balance wording. Improve the
    # explanation without deleting the pause or inferring that an old ambiguous
    # provider request was safe to replay.
    message = REQUEST_FAILED_MESSAGE if row.get("code") == "trial_request_failed" else str(
        row.get("message") or REQUEST_FAILED_MESSAGE
    )
    return message + " 已停止自动重试；请在文件 → 设置中核对状态并手动恢复试用。"


def record_failure(diagnostic: str) -> str:
    code = next((code for code in MESSAGES if code in str(diagnostic).lower()), "trial_request_failed")
    message = MESSAGES.get(code, REQUEST_FAILED_MESSAGE)
    delay = 60 if code == "trial_tpm_exceeded" else 5 if code == "trial_busy" else 0
    if os.environ.get("ARGUS_DESKTOP_TRIAL_PROFILE"):
        return message + " 本次验证失败，原设置和正在使用的试用状态未更改。"
    try:
        write_private(_path(), json.dumps({"profile": _fingerprint(), "code": code,
            "message": message, "resume_after": time.time() + delay}).encode())
    except (OSError, ValueError, KeyError):
        pass
    return message + " 请先查看本次具体报错，并在设置中核对状态、手动恢复试用；不会自动重放此次请求。"


def resume() -> None:
    row = state()
    wait = float(row.get("resume_after", 0)) - time.time()
    if wait > 0:
        raise ValueError(f"服务仍在限流，请等待至少 {math.ceil(wait)} 秒再恢复。")
    _path().unlink(missing_ok=True)
