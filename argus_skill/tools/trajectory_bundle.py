"""Trajectory bundle — package one project's FULL trajectory into a
self-describing, exportable artifact (B-line CORE capability, 存全量).

EN: The B-line product is the sellable research trajectory. This gathers a
project's complete on-disk trajectory layers (plus any caller-supplied codex
rollout sessions — the finest-grained data) into ONE bundle directory with a
``manifest.json`` (schema version, provenance, per-file size / line count), so
the full-fidelity asset is a single coherent, copyable artifact instead of files
scattered across ``~/.argus-skill`` and ``~/.codex``. FULL fidelity — nothing is
stripped or redacted here (compliance / ToS-safe derivation is a deferred,
pre-release concern). Fail-soft per file: an unreadable layer is recorded as
``missing`` and never aborts the bundle.

中文：B 线产品是可售研究轨迹。这里把一个项目完整的落盘轨迹层（以及调用方提供的
codex rollout 会话——最细粒度数据）收进一个 bundle 目录 + ``manifest.json``
（schema 版本、来源、每文件大小/行数），让全量资产成为一个自洽可复制的制品，而不是
散在 ``~/.argus-skill`` 与 ``~/.codex`` 各处。全量、不剥离/不脱敏（合规/ToS-safe
留到发布前）。单文件失败即标 ``missing``，绝不中断打包。
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

_BUNDLE_SCHEMA_VERSION = 1

# Canonical argus project trajectory layers → their on-disk filename. Both the
# split ``memory.jsonl`` (MemoryBundle) and single-root ``journal.jsonl``
# (LifeMemory) are listed; whichever exists is bundled, the other is `missing`.
# 规范的 argus 项目轨迹层 → 落盘文件名；memory.jsonl 与 journal.jsonl 都列，存在
# 哪个收哪个，另一个记为 missing。
_LAYER_FILES: dict[str, str] = {
    "events": "events.jsonl",
    "decisions": "decisions.jsonl",
    "activity": "activity.log",
    "memory": "memory.jsonl",
    "journal": "journal.jsonl",
    "backlog": "backlog.jsonl",
    "telemetry": "telemetry.jsonl",
    "inbox": "inbox.jsonl",
}


@dataclass
class LayerFile:
    layer: str
    src: str          # absolute source path
    rel: str          # path inside the bundle
    bytes: int
    lines: int        # newline count for text layers; 0 if uncounted


@dataclass
class BundleManifest:
    schema_version: int
    project_label: str
    project_dir: str
    created_ts: float
    layers: list[LayerFile] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    total_bytes: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _count_lines(path: Path) -> int:
    """Best-effort newline count (streamed, never loads the whole file)."""
    try:
        n = 0
        with path.open("rb") as fh:
            for _ in fh:
                n += 1
        return n
    except Exception:  # noqa: BLE001
        return 0


def bundle_project(
    project_dir: str | Path,
    out_dir: str | Path,
    *,
    codex_session_paths: list[str | Path] | None = None,
    copy: bool = True,
    now: float | None = None,
) -> BundleManifest:
    """Bundle ``project_dir``'s full trajectory into ``out_dir`` + a manifest.

    EN: Copies each present argus layer to ``out_dir/<layer>/<file>`` and any
    caller-supplied codex rollout sessions to ``out_dir/codex/``. Writes
    ``out_dir/manifest.json``. ``copy=False`` computes the manifest without
    writing (dry run). ``now`` overrides the timestamp (for reproducible tests).
    Per-file fail-soft.
    中文：把每个存在的 argus 层复制到 ``out_dir/<layer>/<file>``，调用方给的 codex
    rollout 会话复制到 ``out_dir/codex/``，并写 ``manifest.json``。``copy=False``
    只算 manifest 不落盘；``now`` 覆盖时间戳（便于可复现测试）；单文件失败即跳过。
    """
    project_dir = Path(project_dir)
    out_dir = Path(out_dir)
    created = float(now) if now is not None else time.time()

    layers: list[LayerFile] = []
    missing: list[str] = []
    total = 0

    def _take(layer: str, src: Path, rel: str) -> None:
        nonlocal total
        try:
            size = src.stat().st_size
            lines = _count_lines(src)
            if copy:
                dst = out_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            layers.append(LayerFile(layer=layer, src=str(src), rel=rel, bytes=size, lines=lines))
            total += size
        except Exception:  # noqa: BLE001 — one bad file must not sink the bundle
            missing.append(layer)

    for layer, fname in _LAYER_FILES.items():
        src = project_dir / fname
        if src.exists() and src.is_file():
            _take(layer, src, f"{layer}/{fname}")
        else:
            missing.append(layer)

    for sess in codex_session_paths or []:
        sp = Path(sess)
        if sp.exists() and sp.is_file():
            _take("codex", sp, f"codex/{sp.name}")

    manifest = BundleManifest(
        schema_version=_BUNDLE_SCHEMA_VERSION,
        project_label=project_dir.name,
        project_dir=str(project_dir),
        created_ts=created,
        layers=layers,
        missing=missing,
        total_bytes=total,
    )
    if copy:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "manifest.json").write_text(
            json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    return manifest
