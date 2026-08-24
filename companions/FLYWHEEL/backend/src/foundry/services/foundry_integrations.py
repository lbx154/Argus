"""Narrow service functions intended for FastAPI route handlers."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..integrations.argus_cli import ArgusCliAdapter
from ..integrations.release_monitor import ReleaseMonitor, persist_release_inspection
from ..integrations.release_stager import ReleaseStager
from ..integrations.resource_probe import NvidiaSmiProbe
from ..integrations.sources import ArxivAdapter, GitHubAdapter, OpenReviewAdapter
from .prompt_compiler import CompiledPrompt
from .prompt_compiler import compile_prompt as _compile_prompt
from .viewer_queue import ViewerQueue


def compile_prompt(
    idea: Mapping[str, Any],
    venue: Mapping[str, Any],
    resources: Mapping[str, Any],
    phase: str = "portfolio",
    domain: Mapping[str, Any] | None = None,
) -> CompiledPrompt:
    return _compile_prompt(idea, venue, resources, phase, domain)


def probe_resources() -> dict[str, Any]:
    return asdict(NvidiaSmiProbe().probe())


def plan_local_argus(
    *,
    campaign_root: Path,
    objective_file: Path,
    backend: str,
    executable: str = "argus-skill",
    mission_width: int = 2,
) -> dict[str, Any]:
    """Return a dry-run argv; route code must make execution a separate approval."""
    plan = ArgusCliAdapter(executable).build_launch(
        campaign_root=campaign_root,
        objective_file=objective_file,
        backend=backend,
        mission_width=mission_width,
        dry_run=True,
    )
    return plan.redacted_dict()


def inspect_release(
    repository: str,
    *,
    ref: str = "refs/heads/main",
    reported_release: Mapping[str, Any] | None = None,
    release_registry: Path | None = None,
) -> dict[str, Any]:
    """Read-only remote SHA comparison; never pulls or adopts a release."""
    return asdict(ReleaseMonitor().inspect(
        repository,
        ref=ref,
        reported_release=reported_release,
        release_registry=release_registry,
    ))


def record_release_inspection(
    inspection: Mapping[str, Any],
    *,
    release_registry: Path,
) -> dict[str, Any]:
    """Atomically publish read-only inspection state while preserving adoption fields."""
    return persist_release_inspection(release_registry, inspection)


def stage_release(
    repository: str,
    *,
    ref: str,
    expected_sha: str,
    confirm_isolated_stage: bool,
    data_dir: Path,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Create an isolated candidate checkout; never test, adopt, or launch it."""
    return asdict(ReleaseStager(data_dir, timeout=timeout).stage(
        repository,
        ref=ref,
        expected_sha=expected_sha,
        confirm_isolated_stage=confirm_isolated_stage,
    ))


def sync_sources(
    requests: Sequence[Mapping[str, Any]],
    *,
    cache_dir: Path,
    github_token: str | None = None,
    demo_fixtures: Mapping[str, Path] | None = None,
) -> dict[str, Any]:
    """Refresh configured sources; every row reports fresh/cache/demo/error."""
    adapters = {
        "arxiv": ArxivAdapter(cache_dir=cache_dir),
        "openreview": OpenReviewAdapter(cache_dir=cache_dir),
        "github": GitHubAdapter(cache_dir=cache_dir, token=github_token),
    }
    updates: list[dict[str, Any]] = []
    fixtures = demo_fixtures or {}
    for request in requests:
        kind = str(request.get("kind") or "").lower()
        adapter = adapters.get(kind)
        if adapter is None:
            updates.append({"source": kind, "status": "error", "error": "unsupported source"})
            continue
        query = str(request.get("query") or "")
        try:
            if kind in fixtures:
                result = adapter.demo(query, fixtures[kind])
            elif kind == "arxiv":
                result = adapter.refresh(query, max_results=int(request.get("limit", 50)))
            elif kind == "openreview":
                result = adapter.refresh(query, limit=int(request.get("limit", 1000)))
            else:
                result = adapter.refresh(query, per_page=int(request.get("limit", 30)))
            updates.append(asdict(result))
        except (OSError, ValueError) as exc:
            updates.append({"source": kind, "query": query, "status": "error", "error": str(exc)})
    return {
        "updates": updates,
        "all_succeeded": all(row.get("status") not in {"error", "stale_cache"} for row in updates),
        "external_calls_are_not_implied_by_demo": True,
    }


def enqueue_viewer(request: Mapping[str, Any], *, queue_dir: Path) -> dict[str, Any]:
    return ViewerQueue(queue_dir).enqueue(request)
