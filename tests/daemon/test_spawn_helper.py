from __future__ import annotations

import io
import json

from argus.daemon import spawn_helper


def test_spawn_helper_keeps_failure_output_enabled(monkeypatch, tmp_path) -> None:
    observed: dict[str, object] = {}

    def fake_spawn(config, *, quiet: bool):
        observed["life_dir"] = config.life_dir
        observed["quiet"] = quiet
        return 2

    monkeypatch.setattr(
        spawn_helper.sys,
        "stdin",
        io.StringIO(json.dumps({"life_dir": str(tmp_path / "life")})),
    )
    monkeypatch.setattr(spawn_helper, "spawn_detached_daemon", fake_spawn)

    assert spawn_helper.main() == 2
    assert observed == {"life_dir": tmp_path / "life", "quiet": False}
