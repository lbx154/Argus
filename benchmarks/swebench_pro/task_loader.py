"""SWE-Bench-Pro task loader.

Reads tasks from a local JSONL file when present, otherwise falls back
to the HuggingFace ``datasets`` library.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass
class Task:
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    docker_tag: str = ""
    before_repo_set_cmd: str = ""
    selected_test_files_to_run: str = ""
    fail_to_pass: str = ""
    pass_to_pass: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        get = lambda k: d.get(k, "") if d.get(k) is not None else ""
        return cls(
            instance_id=str(get("instance_id")),
            repo=str(get("repo")),
            base_commit=str(get("base_commit")),
            problem_statement=str(get("problem_statement")),
            docker_tag=str(get("dockerhub_tag") or get("docker_image") or get("docker_tag") or ""),
            before_repo_set_cmd=str(get("before_repo_set_cmd")),
            selected_test_files_to_run=json.dumps(
                d.get("selected_test_files_to_run") or []
            ) if not isinstance(get("selected_test_files_to_run"), str)
            else get("selected_test_files_to_run"),
            fail_to_pass=json.dumps(d.get("fail_to_pass") or [])
            if not isinstance(get("fail_to_pass"), str) else get("fail_to_pass"),
            pass_to_pass=json.dumps(d.get("pass_to_pass") or [])
            if not isinstance(get("pass_to_pass"), str) else get("pass_to_pass"),
        )

    def docker_image(self, namespace: str = "jefzda") -> str:
        if self.docker_tag.startswith(("docker.io/", namespace + "/")) or "/" in self.docker_tag:
            return self.docker_tag
        if self.docker_tag:
            return f"{namespace}/sweap-images:{self.docker_tag}"
        # fallback: derive from repo + commit prefix
        slug = self.repo.replace("/", "_1776_").lower()
        return f"{namespace}/sweap-images:{slug}_{self.base_commit[:7]}"


def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _load_huggingface(dataset: str, split: str) -> list[dict]:
    from datasets import load_dataset  # lazy import
    ds = load_dataset(dataset, split=split)
    return [dict(r) for r in ds]


def load_tasks(
    *,
    dataset: str = "ScaleAI/SWE-bench_Pro",
    split: str = "test",
    local_jsonl: str | os.PathLike | None = None,
    repos: Iterable[str] | None = None,
    max_tasks_per_repo: int | None = None,
    instance_ids: Iterable[str] | None = None,
) -> list[Task]:
    """Return SWE-Bench-Pro tasks filtered by repo / instance.

    Preference order:
      1. ``local_jsonl`` if provided and exists
      2. HuggingFace ``datasets`` library
    """
    rows: list[dict] | None = None
    if local_jsonl:
        p = Path(local_jsonl)
        if p.is_file():
            rows = _load_jsonl(p)
    if rows is None:
        rows = _load_huggingface(dataset, split)

    tasks = [Task.from_dict(r) for r in rows]

    if instance_ids is not None:
        wanted = set(instance_ids)
        tasks = [t for t in tasks if t.instance_id in wanted]

    if repos is not None:
        wanted_repos = set(repos)
        tasks = [t for t in tasks if t.repo in wanted_repos]

    if max_tasks_per_repo is not None and max_tasks_per_repo > 0:
        seen: dict[str, int] = defaultdict(int)
        kept: list[Task] = []
        for t in tasks:
            if seen[t.repo] < max_tasks_per_repo:
                kept.append(t)
                seen[t.repo] += 1
        tasks = kept

    return tasks
