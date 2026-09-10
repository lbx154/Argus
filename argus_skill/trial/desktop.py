"""Private stdio protocol between the native desktop shell and trial setup."""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys

from . import TRIAL_URL
from .client import setup_trial


def main() -> int:
    output = sys.stdout

    def emit(**event):
        print(json.dumps(event, ensure_ascii=False), file=output, flush=True)

    key = ""
    try:
        data = json.loads(sys.stdin.readline(4096))
        if not isinstance(data, dict) or set(data) != {"api_key"} or not isinstance(data["api_key"], str):
            raise ValueError("请输入有效的内部测试 Key。")
        key = data["api_key"].strip()
        emit(event="progress", message="正在验证内部测试 Key…")
        # The shell receives only this protocol, never CLI output or credentials.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = setup_trial(TRIAL_URL, non_interactive=True, api_key=key, desktop=True,
                               progress=lambda message: emit(event="progress", message=message))
        if code:
            raise ValueError("试用连接验证未通过，请检查网络后重试。原有设置已保留。")
        from ..core.knob_store import read_persisted_knobs

        executable = read_persisted_knobs()["ARGUS_SKILL_RUNNER_BIN"]
        emit(event="complete", runner_bin=executable)
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        message = str(exc).replace(key, "[隐藏]") if key else str(exc)
        emit(event="error", message=message)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
