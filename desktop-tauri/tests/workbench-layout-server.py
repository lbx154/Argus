"""Serve an isolated, read-only project for real workbench layout tests."""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import uvicorn


def main():
    with tempfile.TemporaryDirectory(prefix="argus-layout-") as temporary:
        root = Path(temporary)
        os.environ["ARGUS_SKILL_HOME"] = str(root)
        from argus_skill.core.session import SessionMeta, write_session_meta
        from argus_skill.core.transcript import append_turn
        from argus_skill.life.memory import BacklogItem, LifeMemory
        from argus_skill.webapi.server import create_app

        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / "example.py").write_text('print("layout fixture")\n', encoding="utf-8")
        now = time.time()
        write_session_meta(root, SessionMeta(
            id="s-layout", created=now, last_active=now,
            display_name="工作台布局检查 · Desktop layout",
            workdir=str(workspace), cwd=str(workspace),
        ))
        life = root / "projects/s-layout"
        memory = LifeMemory.open(life)
        memory.backlog.add(BacklogItem(
            id="task-layout", ts=now, title="Inspect the workbench layout",
            objective="Verify the embedded workbench without a model call.", status="done",
        ))
        append_turn(life, "argus", "Read-only layout fixture; no model calls.")
        config = uvicorn.Config(
            create_app(global_root=root, auth_token="local-layout-test"),
            host="127.0.0.1", port=0, access_log=False, log_level="warning",
        )
        with config.bind_socket() as sock:
            print(json.dumps({"origin": f"http://127.0.0.1:{sock.getsockname()[1]}"}), flush=True)
            uvicorn.Server(config).run(sockets=[sock])


if __name__ == "__main__":
    main()
