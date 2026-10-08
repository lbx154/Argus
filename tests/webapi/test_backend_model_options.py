"""The quick model picker follows the ACTIVE backend's own model list."""

from __future__ import annotations

import json
import stat
import sys
import time
from pathlib import Path

import pytest

from argus.webapi import mission_items

_FAKE_COPILOT = r'''#!{python}
import json, sys
calls = {calls!r}
with open(calls, "a") as fh:
    fh.write("start\n")
for line in sys.stdin:
    req = json.loads(line)
    m = req.get("method")
    if m == "initialize":
        res = {{"protocolVersion": 1, "agentCapabilities": {{}}}}
    elif m == "session/new":
        res = {{"sessionId": "s1", "models": {{
            "currentModelId": "fake-newest",
            "availableModels": [
                {{"modelId": "auto", "name": "Auto"}},
                {{"modelId": "zz-fake-newest-but-late-alphabet", "name": "Second"}},
                {{"modelId": "fake-newest", "name": "Newest"}},
                {{"modelId": "aa-fake-older", "name": "Older"}},
            ]}}}}
    else:
        res = {{}}
    if "id" in req:
        sys.stdout.write(json.dumps({{"jsonrpc": "2.0", "id": req["id"], "result": res}}) + "\n")
        sys.stdout.flush()
'''


@pytest.fixture
def copilot_home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    root.mkdir()
    calls = tmp_path / "calls.log"
    fake = tmp_path / "copilot"
    fake.write_text(_FAKE_COPILOT.format(python=sys.executable, calls=str(calls)))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    stale = tmp_path / "pi"
    stale.mkdir()
    (stale / "models.json").write_text(
        json.dumps({"providers": {"p": {"models": [{"id": "stale-catalog-model"}]}}})
    )
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(stale))
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "copilot")
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BIN", str(fake))
    monkeypatch.setattr(mission_items, "_BACKEND_MODELS_WAIT_S", 15.0)
    return root, calls


def test_copilot_options_come_from_the_account_with_the_default_marked(copilot_home) -> None:
    root, calls = copilot_home
    rows = mission_items.model_options(root)
    by_model = {row["model"]: row for row in rows}
    assert "stale-catalog-model" not in by_model
    assert by_model["fake-newest"]["source"] == "backend"
    assert by_model["fake-newest"].get("default") is True
    assert by_model["aa-fake-older"]["source"] == "backend" and not by_model["aa-fake-older"].get("default")
    assert rows[0]["model"] == "fake-newest"
    # "auto" means no explicit model, not a choice; the account order is kept.
    assert "auto" not in by_model
    names = [row["model"] for row in rows]
    assert names.index("zz-fake-newest-but-late-alphabet") < names.index("aa-fake-older")
    assert all("rank" not in row for row in rows)
    # A second request inside the TTL is answered from the cache, no new CLI.
    mission_items.model_options(root)
    assert calls.read_text().count("start") == 1
    assert (root / "cache" / "backend_models.json").is_file()


def test_seen_and_current_models_stay_alongside_the_backend_list(copilot_home, monkeypatch) -> None:
    root, _calls = copilot_home
    monkeypatch.setenv("ARGUS_SKILL_MODEL", "fake-pinned")
    usage = root / "projects" / "p1" / "usage.jsonl"
    usage.parent.mkdir(parents=True)
    usage.write_text(json.dumps({"model": "fake-seen", "completed_at": time.time() - 5, "output_tokens": 3}) + "\n")
    sources = {row["model"]: row["source"] for row in mission_items.model_options(root)}
    assert sources["fake-pinned"] == "current"
    assert sources["fake-seen"] == "seen"
    assert sources["fake-newest"] == "backend"


def test_a_slow_probe_does_not_block_and_falls_back_to_the_catalog(copilot_home, monkeypatch) -> None:
    root, _calls = copilot_home
    from argus.core import backend_readiness

    def _hang(_exe, _timeout):
        time.sleep(2)
        return ["fake-late"], "fake-late"

    monkeypatch.setattr(backend_readiness, "probe_copilot_models", _hang)
    monkeypatch.setattr(mission_items, "_BACKEND_MODELS_WAIT_S", 0.1)
    started = time.monotonic()
    rows = mission_items.model_options(root)
    assert time.monotonic() - started < 1.5
    assert {row["model"] for row in rows} >= {"stale-catalog-model"}


def test_non_copilot_backends_keep_the_harness_catalog(copilot_home, monkeypatch) -> None:
    root, calls = copilot_home
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "codex")
    sources = {row["model"]: row["source"] for row in mission_items.model_options(root)}
    assert sources == {"stale-catalog-model": "catalog"}
    assert not Path(calls).exists()
