"""Canonical literature ledger validation and deterministic TSV projection.

Research agents author one evidence-bearing file:
``research/LITERATURE_GROUNDING.json``.  This module projects its paper rows to
``research/LIT_MATRIX.tsv`` without asking a model to rewrite the same metadata
in a second format.  It deliberately validates provenance shape, not paper
counts or scientific coverage; the Reviewer judges whether the selected sources
cover the actual claims and nearest prior work.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

LEDGER_PATH = Path("research/LITERATURE_GROUNDING.json")
MATRIX_PATH = Path("research/LIT_MATRIX.tsv")

MATRIX_COLUMNS = (
    "id",
    "key",
    "category",
    "title",
    "year",
    "venue",
    "url",
    "task",
    "method",
    "dataset",
    "baseline",
    "metric",
    "key_result",
    "limitation",
    "relevance",
    "retrieved_via",
    "raw_source",
)


@dataclass(frozen=True)
class LiteratureIssue:
    code: str
    path: str
    message: str


def _text(value: object) -> str:
    if isinstance(value, list):
        return "; ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def _paper_groups(payload: dict[str, Any]) -> Iterable[tuple[str, int, object]]:
    papers = payload.get("papers")
    if isinstance(papers, list):
        for index, paper in enumerate(papers):
            if isinstance(paper, dict) and paper.get("classic_anchor") is True:
                category = "classic"
            elif isinstance(paper, dict) and paper.get("recent_high_quality") is True:
                category = "recent"
            else:
                category = "other"
            yield category, index, paper
        return

    group_names = [
        "recent_high_quality_papers",
        "classic_papers",
        "benchmark_papers",
        "adjacent_papers",
        "other_papers",
    ]
    group_names.extend(
        sorted(
            key
            for key, value in payload.items()
            if key.endswith("_papers")
            and key not in group_names
            and isinstance(value, list)
        )
    )
    for key in group_names:
        category = {
            "recent_high_quality_papers": "recent",
            "classic_papers": "classic",
        }.get(key, key.removesuffix("_papers"))
        rows = payload.get(key, [])
        if not isinstance(rows, list):
            continue
        for index, paper in enumerate(rows):
            yield category, index, paper


def _display_key(paper: dict[str, Any]) -> str:
    return (
        _text(paper.get("key"))
        or _text(paper.get("paper_id"))
        or _text(paper.get("arxiv_id"))
        or _text(paper.get("doi"))
        or _text(paper.get("title")).casefold()
    )


def _source_identities(paper: dict[str, Any]) -> tuple[str, ...]:
    identities: list[str] = []
    doi = _text(paper.get("doi")).casefold()
    if doi:
        doi = doi.removeprefix("https://doi.org/").removeprefix("http://doi.org/")
        identities.append(f"doi:{doi}")
    arxiv_id = _text(paper.get("arxiv_id")).casefold()
    if arxiv_id:
        identities.append(f"arxiv:{arxiv_id.split('v', 1)[0]}")
    url = _text(paper.get("url") or paper.get("publication_url")).casefold()
    if url:
        identities.append(f"url:{url.rstrip('/')}")
    title = _text(paper.get("title")).casefold()
    if title:
        identities.append(f"title:{title}")
    return tuple(dict.fromkeys(identities))


def _source_identity(paper: dict[str, Any]) -> str:
    identities = _source_identities(paper)
    return identities[0] if identities else ""


def _raw_source(paper: dict[str, Any]) -> str:
    return _text(
        paper.get("raw_response_path")
        or paper.get("raw_response")
        or paper.get("source_file")
        or paper.get("source_artifact")
    )


def _project_relevance(paper: dict[str, Any]) -> str:
    return _text(
        paper.get("implication")
        or paper.get("implication_for_project")
        or paper.get("benchmark_or_baseline_implication")
        or paper.get("relevance")
    )


def _valid_http_url(value: object) -> bool:
    parsed = urlparse(_text(value))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_literature_ledger(payload: object) -> list[LiteratureIssue]:
    """Validate canonical identity and source provenance without quota gates."""
    if not isinstance(payload, dict):
        return [LiteratureIssue("root_invalid", "$", "ledger root must be an object")]

    issues: list[LiteratureIssue] = []
    seen: dict[str, str] = {}
    rows = list(_paper_groups(payload))
    if not rows:
        issues.append(
            LiteratureIssue(
                "papers_missing",
                "$",
                "ledger must contain papers or recent/classic paper arrays",
            )
        )
        return issues

    for category, index, raw in rows:
        path = f"$.{category}[{index}]"
        if not isinstance(raw, dict):
            issues.append(LiteratureIssue("paper_invalid", path, "paper must be an object"))
            continue
        title = _text(raw.get("title"))
        if not title:
            issues.append(LiteratureIssue("title_missing", f"{path}.title", "title is required"))
        url = raw.get("url") or raw.get("publication_url")
        if not _valid_http_url(url):
            issues.append(
                LiteratureIssue(
                    "url_invalid",
                    f"{path}.url",
                    "a primary http(s) source URL is required",
                )
            )
        if not _text(raw.get("retrieved_via")):
            issues.append(
                LiteratureIssue(
                    "provenance_missing",
                    f"{path}.retrieved_via",
                    "retrieval provenance is required",
                )
            )
        if not _raw_source(raw):
            issues.append(
                LiteratureIssue(
                    "raw_source_missing",
                    path,
                    "a cached raw source artifact is required",
                )
            )
        if not _project_relevance(raw):
            issues.append(
                LiteratureIssue(
                    "relevance_missing",
                    path,
                    "project relevance/implication is required",
                )
            )
        identities = _source_identities(raw)
        if not identities:
            issues.append(
                LiteratureIssue(
                    "identity_missing",
                    path,
                    "paper needs a key, paper_id, arxiv_id, DOI, or title",
                )
            )
        else:
            duplicate = next((identity for identity in identities if identity in seen), "")
            if duplicate:
                issues.append(
                    LiteratureIssue(
                        "paper_duplicate",
                        path,
                        f"duplicates {seen[duplicate]} via {duplicate}",
                    )
                )
                continue
            for identity in identities:
                seen[identity] = path
    return issues


def _matrix_row(category: str, paper: dict[str, Any]) -> dict[str, str]:
    display_key = _display_key(paper)
    return {
        "id": display_key,
        "key": display_key,
        "category": category,
        "title": _text(paper.get("title")),
        "year": _text(paper.get("year")),
        "venue": _text(paper.get("venue") or paper.get("venue_status")),
        "url": _text(paper.get("url") or paper.get("publication_url")),
        "task": _text(paper.get("task") or paper.get("topic")),
        "method": _text(paper.get("method") or paper.get("mechanism")),
        "dataset": _text(paper.get("dataset")),
        "baseline": _text(paper.get("baseline") or paper.get("baselines")),
        "metric": _text(paper.get("metric") or paper.get("metrics")),
        "key_result": _text(
            paper.get("key_result") or paper.get("paper_relevant_summary")
        ),
        "limitation": _text(paper.get("limitation")),
        "relevance": _project_relevance(paper),
        "retrieved_via": _text(paper.get("retrieved_via")),
        "raw_source": _raw_source(paper),
    }


def render_lit_matrix(payload: dict[str, Any]) -> str:
    """Render the deterministic matrix projection from the canonical ledger."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=MATRIX_COLUMNS,
        delimiter="\t",
        lineterminator="\n",
    )
    writer.writeheader()
    for category, _index, raw in _paper_groups(payload):
        if isinstance(raw, dict):
            writer.writerow(_matrix_row(category, raw))
    return output.getvalue()


def _write_if_changed(path: Path, text: str) -> bool:
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    return True


def load_ledger(project_root: Path) -> dict[str, Any]:
    path = project_root / LEDGER_PATH
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def sync_literature_ledger(project_root: Path) -> tuple[bool, list[LiteratureIssue]]:
    payload = load_ledger(project_root)
    issues = validate_literature_ledger(payload)
    if issues:
        return False, issues
    changed = _write_if_changed(project_root / MATRIX_PATH, render_lit_matrix(payload))
    return changed, []


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "sync"))
    parser.add_argument("--project-root", type=Path, default=Path("."))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    root = args.project_root.expanduser().resolve()
    try:
        payload = load_ledger(root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    issues = validate_literature_ledger(payload)
    if issues:
        print(
            json.dumps(
                {
                    "ok": False,
                    "issues": [issue.__dict__ for issue in issues],
                },
                ensure_ascii=False,
            )
        )
        return 1
    changed = False
    if args.command == "sync":
        changed = _write_if_changed(root / MATRIX_PATH, render_lit_matrix(payload))
    print(
        json.dumps(
            {
                "ok": True,
                "papers": sum(1 for _ in _paper_groups(payload)),
                "matrix": str(MATRIX_PATH),
                "changed": changed,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
