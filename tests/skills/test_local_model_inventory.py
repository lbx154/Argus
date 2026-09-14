"""The research stage context lists the checkpoints already on this machine."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.verticals.research import prompt_policy


def _hub_with(root: Path, *repos: tuple[str, int]) -> Path:
    hub = root / "hub"
    for name, size in repos:
        blobs = hub / name / "blobs"
        blobs.mkdir(parents=True)
        # Inventory uses metadata only; sparse fixtures avoid allocating and
        # writing gigabytes just to test human-readable size formatting.
        with (blobs / "weights.safetensors").open("wb") as stream:
            stream.truncate(size)
        snapshots = hub / name / "snapshots" / "abc"
        snapshots.mkdir(parents=True)
        (snapshots / "config.json").write_text('{"model_type": "gpt2"}')
        (snapshots / "model.safetensors").symlink_to(blobs / "weights.safetensors")
    return hub


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    prompt_policy._model_inventory_cache.clear()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    for key in ("HF_HUB_CACHE", "HF_HOME", "TRANSFORMERS_CACHE", "ARGUS_SKILL_MODEL_CACHE_DIRS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        "argus.core.knob_store.read_persisted_knobs", lambda *a, **k: {}
    )


def test_lists_cached_weights_largest_first(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    hub = _hub_with(
        tmp_path,
        ("models--org--small", 4 * 1024 * 1024),
        ("models--org--big", 3 * 1024 ** 3),
    )
    (hub / "datasets--team--bench").mkdir()
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))

    block = prompt_policy.local_model_inventory_block(None)

    assert "## Model weights already on this machine" in block
    assert block.index("org/big") < block.index("org/small")
    assert "(3.0 GB)" in block
    assert "(4 MB)" in block
    assert "`team/bench`" in block
    assert str(hub) in block


def test_project_local_cache_is_found_and_symlinks_are_not_double_counted(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _hub_with(project / "outputs" / "model_cache", ("models--acme--net", 2 * 1024 ** 3))

    block = prompt_policy.local_model_inventory_block(project)

    assert "`acme/net` (2.0 GB)" in block
    assert str(project / "outputs" / "model_cache" / "hub") in block


def test_operator_cache_dirs_knob_is_honoured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    hub = _hub_with(tmp_path / "shared", ("models--lab--model", 1024 ** 3))
    monkeypatch.setenv("ARGUS_SKILL_MODEL_CACHE_DIRS", str(hub))

    assert "`lab/model`" in prompt_policy.local_model_inventory_block(None)


def test_no_weights_means_no_block(tmp_path: Path) -> None:
    assert prompt_policy.local_model_inventory_block(tmp_path / "empty-project") == ""


def test_compute_stages_carry_the_inventory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    hub = _hub_with(tmp_path, ("models--org--model", 1024 ** 3))
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))
    monkeypatch.setattr(prompt_policy, "local_hardware_block", lambda: "")

    for role in ("planner", "engineer"):
        fragment = prompt_policy.render_role_prompt_fragment(
            role=role, operation="develop", stage="experiment", scope="bounded", project_root=None
        )
        assert "`org/model`" in fragment, role
    reviewer_policy = prompt_policy.render_role_prompt_fragment(
        role="reviewer", operation="evaluate", stage="experiment", scope="bounded", project_root=None
    )
    assert "org/model" not in reviewer_policy
    assert "`org/model`" in prompt_policy.research_runtime_context("experiment")
    paper = prompt_policy.render_role_prompt_fragment(
        role="engineer", operation="author_draft", stage="paper", scope="bounded", project_root=None
    )
    assert "org/model" not in paper


def test_config_only_and_orphan_blobs_are_not_checkpoints(monkeypatch, tmp_path):
    hub = tmp_path / "hub"
    snapshot = hub / "models--org--metadata" / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text('{"model_type": "gpt2"}')
    blobs = hub / "models--org--orphan" / "blobs"
    blobs.mkdir(parents=True)
    (blobs / "weights.safetensors").write_bytes(b"unreferenced blob")
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))
    assert prompt_policy.local_model_inventory_block() == ""


@pytest.mark.parametrize("filename", ["model.safetensors", "pytorch_model.bin"])
def test_index_requires_every_shard_and_reports_snapshot(monkeypatch, tmp_path, filename):
    hub = tmp_path / "hub"
    snapshot = hub / "models--org--sharded" / "snapshots" / "revision"
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text('{"model_type": "gpt2"}')
    extension = Path(filename).suffix
    shards = [f"part-{n}{extension}" for n in (1, 2)]
    (snapshot / f"{filename}.index.json").write_text(json.dumps({
        "weight_map": {"a": shards[0], "b": shards[1], "c": shards[0]},
    }))
    (snapshot / shards[0]).write_bytes(b"first shard")
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))
    assert prompt_policy._query_local_models(None)[0] == []
    (snapshot / shards[1]).write_bytes(b"second shard")
    assert prompt_policy._query_local_models(None)[0] == [
        ("org/sharded", len(b"first shardsecond shard"), snapshot)
    ]
    block = prompt_policy.local_model_inventory_block()
    assert str(snapshot) in block
    assert "load without a download" not in block


@pytest.mark.parametrize("broken", ["missing", "incomplete", "empty"])
def test_broken_or_incomplete_weight_links_are_excluded(monkeypatch, tmp_path, broken):
    hub = _hub_with(tmp_path, ("models--org--net", 8))
    blob = hub / "models--org--net" / "blobs" / "weights.safetensors"
    snapshot = hub / "models--org--net" / "snapshots" / "abc"
    if broken == "missing":
        blob.unlink()
    elif broken == "incomplete":
        partial = blob.with_suffix(".incomplete")
        blob.rename(partial)
        (snapshot / "model.safetensors").unlink()
        (snapshot / "model.safetensors").symlink_to(partial)
    else:
        blob.write_bytes(b"")
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))
    assert prompt_policy._query_local_models(None)[0] == []


def test_duplicate_hub_does_not_prefer_large_incomplete_cache(monkeypatch, tmp_path):
    complete = _hub_with(tmp_path / "complete", ("models--org--net", 8))
    partial = tmp_path / "partial" / "hub"
    blobs = partial / "models--org--net" / "blobs"
    blobs.mkdir(parents=True)
    (blobs / "weights.incomplete").write_bytes(b"partial bytes" * 100)
    monkeypatch.setattr(prompt_policy, "_hub_cache_dirs", lambda _: [partial, complete])
    assert prompt_policy._query_local_models(None)[0] == [
        ("org/net", 8, complete / "models--org--net" / "snapshots" / "abc")
    ]


def test_inventory_cache_avoids_repeated_snapshot_checks(monkeypatch, tmp_path):
    hub = _hub_with(tmp_path, ("models--org--net", 8))
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))
    calls = []
    original = prompt_policy._snapshot_weight_bytes

    def check(snapshot):
        calls.append(snapshot)
        return original(snapshot)

    monkeypatch.setattr(prompt_policy, "_snapshot_weight_bytes", check)
    first = prompt_policy.local_model_inventory_block(tmp_path)
    assert prompt_policy.local_model_inventory_block(tmp_path) == first
    assert len(calls) == 1
