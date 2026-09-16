"""Per-project self-evaluation of Argus's experiment capability.

One project leaves two trees behind: the *state dir*
(``events.jsonl``, ``usage.jsonl``, ``backlog*.json*``, ``mission-view.json``,
``skills/``) and the *workspace* (``METHOD.md``, ``src/``, ``tests/spec``,
``third_party/``, ``configs/``, ``experiments/``, ``paper/``,
``.argus/round-checks/latest.json``, ``.autors/*/wiki``).  This module reads
both read-only and condenses them into one JSON report so successive
projects (and successive Argus versions) can be compared on the same axes:

* how long each stage took and how many missions/rounds it consumed,
* what the Reviewer said and how long it looked before saying it,
* what the run cost in tokens and dollars, per role where the ledger says,
* whether the method card, spec checks, reference clones, seeds, datasets,
  figures, knowledge artifacts and the manuscript exist and in what shape.

Nothing here gates anything.  Every field is fail-soft: a missing file or an
unreadable line yields the empty value for that field and the rest of the
report is still produced.  The module makes no model calls.

Command line::

    python -m argus.verticals.research.capability_report \\
        --state-dir <dir> --workspace <dir> [--json|--markdown] \\
        [--baseline <other report json>]

``--baseline`` prints both reports side by side with deltas.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import statistics
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from .figure_lint import figure_lint_issues
from .method_card import derive_method_card

log = logging.getLogger(__name__)

REPORT_SCHEMA_VERSION = 1

#: Stage order used to sort the per-stage table; unknown stages follow in
#: order of first appearance.
STAGE_ORDER: tuple[str, ...] = ("idea", "experiment", "paper", "review")

#: Roles a ``run_label`` such as ``manager-frontdoor-classify`` may start with.
KNOWN_ROLES: tuple[str, ...] = ("manager", "planner", "engineer", "reviewer", "curator")

#: Reviews finishing faster than this are counted separately: a verdict in
#: under a minute rarely rests on having read the evidence.
QUICK_REVIEW_S = 60.0

#: Dataset names recognised in experiment JSON keys and the METHOD.md protocol.
DATASET_VOCABULARY: tuple[str, ...] = (
    "california housing", "california_housing", "boston", "diabetes", "wine",
    "iris", "digits", "breast cancer", "breast_cancer", "mnist", "fashion-mnist",
    "fashion_mnist", "kmnist", "emnist", "cifar-10", "cifar10", "cifar-100",
    "cifar100", "svhn", "stl10", "stl-10", "tiny-imagenet", "imagenet",
    "celeba", "coco", "pascal voc", "ade20k", "cityscapes", "kitti", "lsun",
    "shapenet", "modelnet", "scannet", "nyu depth", "sst-2", "sst2", "glue",
    "superglue", "squad", "mnli", "qnli", "wikitext", "wikitext-2",
    "wikitext-103", "penn treebank", "ptb", "c4", "the pile", "openwebtext",
    "imdb", "ag news", "ag_news", "yelp", "amazon reviews", "librispeech",
    "common voice", "timit", "esc-50", "urbansound8k", "audioset", "gsm8k",
    "math", "humaneval", "mbpp", "mmlu", "hellaswag", "arc", "winogrande",
    "truthfulqa", "big-bench", "bbh", "ogb", "cora", "citeseer", "pubmed",
    "qm9", "zinc", "molhiv", "molpcba", "moleculenet", "uci", "adult",
    "covertype", "covtype", "higgs", "susy", "year prediction", "yearpredictionmsd",
    "abalone", "energy", "concrete", "kin8nm", "protein", "power plant",
    "naval", "yacht", "airline", "elevators", "bike sharing", "bike_sharing",
    "electricity", "traffic", "ett", "weather", "m4", "m5", "ml-1m",
    "movielens", "netflix", "criteo", "avazu", "synthetic",
)

_INT_LIST_RE = re.compile(r"\bseeds?\b\s*[:=]\s*[\[\(]([^\]\)]*)[\]\)]", re.IGNORECASE)
_SEEDS_FLAG_RE = re.compile(r"--seeds?\b[\s=]+([\d\s,]+)")
_SEED_SCALAR_RE = re.compile(r"\bseed\b\s*[:=]\s*(\d+)\b", re.IGNORECASE)
_RANGE_RE = re.compile(r"\bseeds?\b\s*[:=]\s*(?:list\()?range\(\s*(\d+)\s*(?:,\s*(\d+))?\s*\)", re.IGNORECASE)
_INT_RE = re.compile(r"-?\d+")
_FRONTMATTER_DESCRIPTION_RE = re.compile(
    r"\A---\s*\n(?P<body>.*?)\n---", re.DOTALL
)
_DESCRIPTION_LINE_RE = re.compile(r"^description:\s*(?P<value>.*)$", re.MULTILINE)
_SURVEYED_RE = re.compile(r"^\s*[\"']?\s*Surveyed\b", re.IGNORECASE)
_PDF_COUNT_RE = re.compile(rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)", re.DOTALL)
_PDF_PAGE_RE = re.compile(rb"/Type\s*/Page(?![s/])")


# --------------------------------------------------------------------------
# Low-level readers (all fail-soft)
# --------------------------------------------------------------------------


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.is_file():
        return
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _event_type(event: dict[str, Any]) -> str:
    for key in ("type", "event", "kind"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _to_epoch(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        try:
            return float(value)
        except ValueError:
            pass
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


def _event_ts(event: dict[str, Any]) -> float | None:
    for key in ("ts", "timestamp", "time", "created_at", "at"):
        if key in event:
            stamp = _to_epoch(event.get(key))
            if stamp is not None:
                return stamp
    return None


def _iso(stamp: float | None) -> str | None:
    if stamp is None:
        return None
    try:
        return datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _hours(start: float | None, end: float | None) -> float | None:
    if start is None or end is None or end < start:
        return None
    return round((end - start) / 3600.0, 3)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _item_id(event: dict[str, Any]) -> str:
    for key in ("item_id", "mission_id", "task_id", "id"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _round_index(event: dict[str, Any]) -> int | None:
    for key in ("round_index", "round"):
        value = event.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


# --------------------------------------------------------------------------
# State dir: events, backlog, usage
# --------------------------------------------------------------------------


def load_events(state_dir: Path) -> list[dict[str, Any]]:
    """Events sorted by timestamp; rows without a timestamp keep file order at the end."""
    stamped: list[tuple[float, int, dict[str, Any]]] = []
    unstamped: list[dict[str, Any]] = []
    for order, event in enumerate(_iter_jsonl(state_dir / "events.jsonl")):
        stamp = _event_ts(event)
        if stamp is None:
            unstamped.append(event)
        else:
            stamped.append((stamp, order, event))
    stamped.sort(key=lambda entry: (entry[0], entry[1]))
    return [entry[2] for entry in stamped] + unstamped


def load_backlog_items(state_dir: Path) -> dict[str, dict[str, Any]]:
    """Backlog items by id from every ``backlog*.json*`` file; the latest row wins."""
    items: dict[str, dict[str, Any]] = {}
    try:
        candidates = sorted(state_dir.glob("backlog*.json*"))
    except OSError:
        return items
    for path in candidates:
        if path.suffix in {".lock", ".owner"} or path.name.endswith(".lock"):
            continue
        rows: Iterable[dict[str, Any]]
        if path.suffix == ".jsonl":
            rows = _iter_jsonl(path)
        else:
            payload = _load_json(path)
            if isinstance(payload, dict):
                inner = payload.get("items") or payload.get("tasks") or payload.get("backlog")
                rows = [row for row in (inner or []) if isinstance(row, dict)]
            elif isinstance(payload, list):
                rows = [row for row in payload if isinstance(row, dict)]
            else:
                rows = []
        for row in rows:
            item_id = str(row.get("id") or "")
            if item_id:
                items[item_id] = row
    return items


def mission_stage(item: dict[str, Any]) -> str:
    """The stage a backlog item ran in: ``stage:<name>`` tag, else ``stage`` field."""
    for tag in item.get("tags") or ():
        if isinstance(tag, str) and tag.startswith("stage:"):
            return tag.split(":", 1)[1]
    for key in ("stage", "pipeline_stage", "current_stage"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _stage_rank(stage: str, first_seen: dict[str, float]) -> tuple[int, float]:
    if stage in STAGE_ORDER:
        return (STAGE_ORDER.index(stage), 0.0)
    return (len(STAGE_ORDER), first_seen.get(stage, float("inf")))


def stage_timeline(
    events: list[dict[str, Any]], backlog: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Per-stage ``entered_at``/``left_at``/``hours`` from manager stage decisions.

    ``life.manager.stage_decision`` rows carry ``action`` (advance/hold/complete),
    ``current_stage`` and ``target_stage``.  When the log skips a transition
    (the project moved from experiment to paper without a decision row) the
    earliest mission tagged with the new stage dates the boundary.  Stages the
    decisions never mention but missions were tagged with are added from the
    missions alone.
    """
    stamps = [stamp for stamp in (_event_ts(event) for event in events) if stamp is not None]
    first_ts = stamps[0] if stamps else None
    last_ts = stamps[-1] if stamps else None

    mission_bounds: dict[str, list[float]] = {}
    for item in backlog.values():
        stage = mission_stage(item)
        if not stage:
            continue
        for key in ("started_ts", "ts", "finished_ts"):
            stamp = _to_epoch(item.get(key))
            if stamp is not None:
                mission_bounds.setdefault(stage, []).append(stamp)

    intervals: dict[str, dict[str, float | None]] = {}
    first_seen: dict[str, float] = {}
    open_stage: str | None = None

    def _open(stage: str, at: float | None) -> None:
        nonlocal open_stage
        if not stage:
            return
        entry = intervals.setdefault(stage, {"entered_at": None, "left_at": None})
        if entry["entered_at"] is None and at is not None:
            entry["entered_at"] = at
            first_seen.setdefault(stage, at)
        entry["left_at"] = None
        open_stage = stage

    def _close(stage: str | None, at: float | None) -> None:
        nonlocal open_stage
        if stage and stage in intervals and at is not None:
            entry = intervals[stage]
            if entry["left_at"] is None or entry["left_at"] < at:
                entry["left_at"] = at
        if open_stage == stage:
            open_stage = None

    saw_decision = False
    for event in events:
        if _event_type(event) != "life.manager.stage_decision":
            continue
        stamp = _event_ts(event)
        current = str(event.get("current_stage") or "")
        target = str(event.get("target_stage") or "")
        action = str(event.get("action") or "")
        saw_decision = True
        if current and current != open_stage:
            boundary = stamp
            mission_starts = mission_bounds.get(current)
            if mission_starts:
                boundary = min(mission_starts)
                if stamp is not None:
                    boundary = min(boundary, stamp)
            if open_stage is None and not intervals:
                boundary = first_ts if first_ts is not None else boundary
            _close(open_stage, boundary)
            _open(current, boundary)
        if action == "advance" and target and target != current:
            _close(current, stamp)
            _open(target, stamp)
        elif action == "complete":
            _close(current, stamp)

    if not saw_decision:
        for stage, bounds in mission_bounds.items():
            intervals[stage] = {"entered_at": min(bounds), "left_at": max(bounds)}
            first_seen.setdefault(stage, min(bounds))

    stages: dict[str, dict[str, Any]] = {}
    for stage in sorted(intervals, key=lambda name: _stage_rank(name, first_seen)):
        entered = intervals[stage]["entered_at"]
        left = intervals[stage]["left_at"]
        end = left if left is not None else last_ts
        stages[stage] = {
            "entered_at": _iso(entered),
            "left_at": _iso(left),
            "hours": _hours(entered, end),
            "open": left is None and stage == open_stage,
        }
    total = _hours(first_ts, last_ts)
    return {"stages": stages, "total_hours": total, "first_event_at": _iso(first_ts), "last_event_at": _iso(last_ts)}


def mission_summary(
    events: list[dict[str, Any]], backlog: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Distinct missions, start attempts, missions per stage, rounds per mission."""
    started: dict[str, int] = Counter()
    completed: Counter[str] = Counter()
    rounds: dict[str, int] = {}
    for event in events:
        kind = _event_type(event)
        item = _item_id(event)
        if kind == "life.mission.started" and item:
            started[item] += 1
        elif kind == "life.mission.completed" and item:
            completed[str(event.get("status") or "")] += 1
        elif kind in {"round.start", "round.main.completed"} and item:
            index = _round_index(event)
            if index is not None:
                rounds[item] = max(rounds.get(item, 0), index)
    mission_ids = set(started) | {item for item in rounds}
    for item_id, row in backlog.items():
        if str(row.get("status") or "") in {"done", "failed", "running", "cancelled"}:
            mission_ids.add(item_id)
    per_stage: Counter[str] = Counter()
    for item_id in mission_ids:
        stage = mission_stage(backlog.get(item_id, {})) or "unknown"
        per_stage[stage] += 1
    round_values = [rounds[item] for item in mission_ids if item in rounds]
    mean_rounds = round(statistics.fmean(round_values), 2) if round_values else None
    return {
        "count": len(mission_ids),
        "start_attempts": int(sum(started.values())),
        "per_stage": dict(sorted(per_stage.items())),
        "completed_status": dict(sorted(completed.items())),
        "mean_rounds": mean_rounds,
        "max_rounds": max(round_values) if round_values else None,
        "rounds_total": int(sum(round_values)),
    }


def reviewer_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Verdict counts and review durations from ``round.review.started/completed``."""
    verdicts: Counter[str] = Counter()
    durations: list[float] = []
    skipped = 0
    started_at: dict[tuple[str, int | None], float] = {}
    for event in events:
        kind = _event_type(event)
        if kind == "round.review.started":
            stamp = _event_ts(event)
            if stamp is not None:
                started_at[(_item_id(event), _round_index(event))] = stamp
        elif kind == "round.review.completed":
            if event.get("review_skipped"):
                skipped += 1
                continue
            status = str(event.get("status") or event.get("verdict") or "unknown")
            verdicts[status] += 1
            key = (_item_id(event), _round_index(event))
            stamp = _event_ts(event)
            start = started_at.pop(key, None)
            if start is None:
                start = started_at.pop((key[0], None), None)
            if start is not None and stamp is not None and stamp >= start:
                durations.append(stamp - start)
            elif event.get("duration_s") is not None:
                durations.append(_float(event.get("duration_s")))
    return {
        "verdicts": dict(sorted(verdicts.items())),
        "reviews": int(sum(verdicts.values())),
        "skipped": skipped,
        "median_duration_s": round(statistics.median(durations), 1) if durations else None,
        "under_60s": sum(1 for value in durations if value < QUICK_REVIEW_S),
        "timed": len(durations),
    }


def _role_of(row: dict[str, Any]) -> str:
    for key in ("role", "agent_layer"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value.lower()
    label = str(row.get("run_label") or row.get("label") or "").lower()
    for role in KNOWN_ROLES:
        if label == role or label.startswith(role + "-") or label.startswith(role + ":") or label.startswith(role + "_"):
            return role
    return "other"


def _usage_fields(row: dict[str, Any]) -> tuple[int, int, int, float]:
    nested = row.get("usage") if isinstance(row.get("usage"), dict) else {}
    pricing = row.get("pricing") if isinstance(row.get("pricing"), dict) else {}

    def pick(*keys: str) -> Any:
        for key in keys:
            if row.get(key) is not None:
                return row.get(key)
            if nested.get(key) is not None:
                return nested.get(key)
        return None

    cost = row.get("cost_usd")
    if cost is None:
        cost = pricing.get("cost_usd")
    return (
        _int(pick("input_tokens", "prompt_tokens")),
        _int(pick("output_tokens", "completion_tokens")),
        _int(pick("cached_input_tokens", "cached_tokens", "cache_read_tokens")),
        _float(cost),
    )


def token_summary(state_dir: Path, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Totals from ``usage.jsonl``; falls back to ``usage.recorded`` events."""
    rows = list(_iter_jsonl(state_dir / "usage.jsonl"))
    source = "usage.jsonl"
    if not rows:
        rows = [event for event in events if _event_type(event) == "usage.recorded"]
        source = "events.usage.recorded" if rows else "none"
    totals = {"input": 0, "output": 0, "cached": 0, "usd": 0.0, "calls": 0}
    per_role: dict[str, dict[str, Any]] = {}
    for row in rows:
        inp, out, cached, cost = _usage_fields(row)
        totals["input"] += inp
        totals["output"] += out
        totals["cached"] += cached
        totals["usd"] += cost
        totals["calls"] += 1
        bucket = per_role.setdefault(_role_of(row), {"input": 0, "output": 0, "cached": 0, "usd": 0.0, "calls": 0})
        bucket["input"] += inp
        bucket["output"] += out
        bucket["cached"] += cached
        bucket["usd"] += cost
        bucket["calls"] += 1
    totals["usd"] = round(totals["usd"], 4)
    for bucket in per_role.values():
        bucket["usd"] = round(bucket["usd"], 4)
    return {**totals, "source": source, "per_role": dict(sorted(per_role.items()))}


# --------------------------------------------------------------------------
# Workspace: method card, spec checks, references, protocol, figures, paper
# --------------------------------------------------------------------------


def method_card_summary(workspace: Path) -> dict[str, Any]:
    try:
        card = derive_method_card(workspace)
    except Exception:  # noqa: BLE001
        log.debug("capability report: method card derivation failed", exc_info=True)
        card = {"exists": False, "components": []}
    components = card.get("components") or []
    statuses: Counter[str] = Counter(str(entry.get("status") or "unknown") for entry in components)
    anchors = sum(
        1 for entry in components
        if any(mark in str(entry.get("prescribes") or "") for mark in ('"', "“", "”", "'"))
    )
    bound_tests = {
        str(test.get("id") or "")
        for entry in components
        for test in (entry.get("tests") or [])
        if isinstance(test, dict) and test.get("id")
    }
    return {
        "exists": bool(card.get("exists")),
        "title": str(card.get("title") or ""),
        "components": len(components),
        "status_counts": dict(sorted(statuses.items())),
        "anchors": anchors,
        "spec_tests_bound": len(bound_tests),
        "unlisted_components": len(card.get("unlisted_components") or []),
        "reused_code": len(card.get("reused_code") or []),
        "hyperparameters": len(card.get("hyperparameters") or []),
        "hyperparameters_with_why": sum(
            1 for entry in (card.get("hyperparameters") or []) if entry.get("why")
        ),
        "has_protocol": bool(str(card.get("protocol") or "").strip()),
        "has_falsifiers": bool(str(card.get("falsifiers") or "").strip()),
    }


def spec_checks_summary(workspace: Path) -> dict[str, Any]:
    spec_dir = workspace / "tests" / "spec"
    spec_files = 0
    if spec_dir.is_dir():
        try:
            spec_files = sum(1 for path in spec_dir.rglob("test_*.py"))
        except OSError:
            spec_files = 0
    latest = _load_json(workspace / ".argus" / "round-checks" / "latest.json")
    rounds = 0
    try:
        rounds = sum(1 for _ in (workspace / ".argus" / "round-checks").glob("round-*.json"))
    except OSError:
        rounds = 0
    summary: dict[str, Any] = {
        "spec_dir_exists": spec_dir.is_dir(),
        "spec_files": spec_files,
        "rounds_checked": rounds,
        "last_round_index": None,
        "last_exit_code": None,
        "counts": {},
        "contradicted_components": [],
    }
    if isinstance(latest, dict):
        summary["last_round_index"] = latest.get("round_index")
        summary["last_exit_code"] = latest.get("exit_code")
        counts = latest.get("counts")
        summary["counts"] = {k: _int(v) for k, v in counts.items()} if isinstance(counts, dict) else {}
        components = latest.get("components")
        if isinstance(components, dict):
            summary["contradicted_components"] = sorted(
                name for name, entry in components.items()
                if isinstance(entry, dict) and entry.get("status") == "contradicted"
            )
    return summary


def _git_revision(clone: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(clone), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    head = _read_text(clone / ".git" / "HEAD").strip()
    if head.startswith("ref:"):
        ref = head.split(None, 1)[1].strip()
        direct = _read_text(clone / ".git" / ref).strip()
        if direct:
            return direct[:12]
        for line in _read_text(clone / ".git" / "packed-refs").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref:
                return parts[0][:12]
        return ""
    return head[:12]


def reference_summary(workspace: Path) -> dict[str, Any]:
    root = workspace / "third_party"
    clones: list[dict[str, Any]] = []
    if root.is_dir():
        try:
            children = sorted(child for child in root.iterdir() if child.is_dir())
        except OSError:
            children = []
        for child in children:
            is_git = (child / ".git").exists()
            clones.append({
                "name": child.name,
                "git": is_git,
                "revision": _git_revision(child) if is_git else "",
            })
    return {"third_party_exists": root.is_dir(), "clones": clones, "count": len(clones)}


def _iter_text_files(roots: Iterable[Path], suffixes: tuple[str, ...], limit: int = 400) -> Iterator[Path]:
    seen = 0
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for path in sorted(root.rglob("*")):
                if seen >= limit:
                    return
                if path.is_file() and path.suffix.lower() in suffixes and ".venv" not in path.parts:
                    seen += 1
                    yield path
        except OSError:
            continue


def detect_seeds(workspace: Path) -> dict[str, Any]:
    """Distinct seed values named in configs/scripts/src and how they were named."""
    roots = [workspace / "configs", workspace / "config", workspace / "scripts", workspace / "src", workspace / "experiments"]
    suffixes = (".py", ".yaml", ".yml", ".json", ".toml", ".sh", ".cfg", ".ini", ".txt", ".md")
    seeds: set[int] = set()
    list_hits = 0
    flag_hits = 0
    scalar_hits = 0
    for path in _iter_text_files(roots, suffixes):
        text = _read_text(path)
        if "seed" not in text.lower():
            continue
        for match in _INT_LIST_RE.finditer(text):
            values = [int(v) for v in _INT_RE.findall(match.group(1))]
            if values:
                list_hits += 1
                seeds.update(values)
        for match in _RANGE_RE.finditer(text):
            lo, hi = match.group(1), match.group(2)
            start, stop = (0, int(lo)) if hi is None else (int(lo), int(hi))
            if 0 <= stop - start <= 1000:
                list_hits += 1
                seeds.update(range(start, stop))
        for match in _SEEDS_FLAG_RE.finditer(text):
            values = [int(v) for v in _INT_RE.findall(match.group(1))]
            if values:
                flag_hits += 1
                seeds.update(values)
        for match in _SEED_SCALAR_RE.finditer(text):
            scalar_hits += 1
            seeds.add(int(match.group(1)))
    return {
        "distinct": len(seeds),
        "values": sorted(seeds)[:32],
        "list_declarations": list_hits,
        "cli_flags": flag_hits,
        "scalar_defaults": scalar_hits,
    }


def _json_keys(value: Any, out: list[str], depth: int = 0) -> None:
    if depth > 6:
        return
    if isinstance(value, dict):
        for key, inner in value.items():
            out.append(str(key))
            _json_keys(inner, out, depth + 1)
    elif isinstance(value, list):
        for inner in value[:50]:
            _json_keys(inner, out, depth + 1)


def detect_datasets(workspace: Path, protocol: str = "") -> dict[str, Any]:
    """Dataset names seen in experiments/ JSON keys or the METHOD.md protocol."""
    found: dict[str, set[str]] = {}
    keys: list[str] = []
    for path in _iter_text_files([workspace / "experiments"], (".json",), limit=200):
        payload = _load_json(path)
        if payload is None:
            continue
        local: list[str] = []
        _json_keys(payload, local)
        keys.extend(local)
        if isinstance(payload, dict):
            for key, inner in payload.items():
                if "dataset" in str(key).lower() and isinstance(inner, dict):
                    for name in inner:
                        found.setdefault(str(name), set()).add("experiments")
                elif "dataset" in str(key).lower() and isinstance(inner, (str, list)):
                    for name in inner if isinstance(inner, list) else [inner]:
                        if isinstance(name, str):
                            found.setdefault(name, set()).add("experiments")
    haystacks = {"experiments": " \n".join(keys).lower(), "method_card": protocol.lower()}
    if not protocol:
        haystacks["method_card"] = _read_text(workspace / "METHOD.md").lower()
    for name in DATASET_VOCABULARY:
        pattern = re.compile(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])")
        for where, haystack in haystacks.items():
            if haystack and pattern.search(haystack):
                found.setdefault(name, set()).add(where)
    names = sorted(found)
    return {
        "count": len(names),
        "names": names[:32],
        "sources": {name: sorted(found[name]) for name in names[:32]},
    }


def figure_summary(workspace: Path) -> dict[str, Any]:
    try:
        issues = list(figure_lint_issues(workspace))
    except Exception:  # noqa: BLE001
        log.debug("capability report: figure lint failed", exc_info=True)
        issues = []
    figures_dir = workspace / "paper" / "figures"
    files = 0
    if figures_dir.is_dir():
        try:
            files = sum(1 for path in figures_dir.iterdir() if path.is_file())
        except OSError:
            files = 0
    return {"issues": len(issues), "issue_samples": issues[:5], "files": files}


def _skill_description(text: str) -> str:
    match = _FRONTMATTER_DESCRIPTION_RE.match(text)
    if not match:
        return ""
    line = _DESCRIPTION_LINE_RE.search(match.group("body"))
    return line.group("value").strip() if line else ""


def knowledge_summary(state_dir: Path, workspace: Path) -> dict[str, Any]:
    skills_root = state_dir / "skills"
    skills = 0
    decision_records = 0
    if skills_root.is_dir():
        try:
            for path in skills_root.rglob("*.md"):
                if not path.is_file():
                    continue
                skills += 1
                if _SURVEYED_RE.match(_skill_description(_read_text(path))):
                    decision_records += 1
        except OSError:
            pass
    wiki_pages = 0
    wikis = 0
    autors = workspace / ".autors"
    if autors.is_dir():
        try:
            for wiki in sorted(autors.glob("*/wiki")):
                if not wiki.is_dir():
                    continue
                wikis += 1
                pages = wiki / "pages"
                if pages.is_dir():
                    wiki_pages += sum(1 for path in pages.rglob("*.md") if path.is_file())
                else:
                    wiki_pages += sum(
                        1 for path in wiki.rglob("*.md")
                        if path.is_file() and path.name not in {"README.md", "INDEX.md"}
                    )
        except OSError:
            pass
    return {
        "project_skills": skills,
        "decision_records": decision_records,
        "wikis": wikis,
        "wiki_pages": wiki_pages,
    }


def pdf_page_count(path: Path) -> int | None:
    """Page count from the ``/Pages`` root, then page objects, then pypdf if present."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    counts = [int(match.group(1)) for match in _PDF_COUNT_RE.finditer(data)]
    if counts:
        return max(counts)
    pages = len(_PDF_PAGE_RE.findall(data))
    if pages:
        return pages
    try:
        import pypdf  # type: ignore[import-not-found]

        return len(pypdf.PdfReader(str(path)).pages)
    except Exception:  # noqa: BLE001
        return None


def paper_summary(workspace: Path) -> dict[str, Any]:
    pdf = workspace / "paper" / "main.pdf"
    tex = workspace / "paper" / "main.tex"
    exists = pdf.is_file()
    return {
        "main_tex_exists": tex.is_file(),
        "main_pdf_exists": exists,
        "pages": pdf_page_count(pdf) if exists else None,
        "review_md_exists": (workspace / "paper" / "REVIEW.md").is_file(),
    }


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------


def _project_id(state_dir: Path, workspace: Path) -> str:
    session = _load_json(state_dir / "session.json")
    if isinstance(session, dict) and session.get("id"):
        return str(session["id"])
    return state_dir.name or workspace.name


def _current_stage(state_dir: Path) -> str:
    view = _load_json(state_dir / "mission-view.json")
    if isinstance(view, dict):
        stage = view.get("stage")
        if isinstance(stage, dict) and stage.get("id"):
            return str(stage["id"])
        if isinstance(stage, str):
            return stage
    pipeline = _load_json(state_dir / ".argus" / "PIPELINE_STATE.json")
    if isinstance(pipeline, dict):
        for key in ("stage", "current_stage"):
            if isinstance(pipeline.get(key), str):
                return str(pipeline[key])
    return ""


def build_report(state_dir: Path | str, workspace: Path | str) -> dict[str, Any]:
    """Every capability axis for one project, JSON-ready and fail-soft."""
    state_dir = Path(state_dir)
    workspace = Path(workspace)
    events = load_events(state_dir)
    backlog = load_backlog_items(state_dir)
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "project_id": _project_id(state_dir, workspace),
        "state_dir": str(state_dir),
        "workspace": str(workspace),
        "current_stage": _current_stage(state_dir),
        "events": len(events),
    }
    sections: list[tuple[str, Any]] = [
        ("timeline", lambda: stage_timeline(events, backlog)),
        ("missions", lambda: mission_summary(events, backlog)),
        ("reviewer", lambda: reviewer_summary(events)),
        ("tokens", lambda: token_summary(state_dir, events)),
        ("method_card", lambda: method_card_summary(workspace)),
        ("spec_checks", lambda: spec_checks_summary(workspace)),
        ("reference", lambda: reference_summary(workspace)),
        ("figures", lambda: figure_summary(workspace)),
        ("knowledge", lambda: knowledge_summary(state_dir, workspace)),
        ("paper", lambda: paper_summary(workspace)),
    ]
    for name, derive in sections:
        try:
            report[name] = derive()
        except Exception:  # noqa: BLE001
            log.debug("capability report: %s failed", name, exc_info=True)
            report[name] = {}
    timeline = report.pop("timeline", {}) or {}
    report["stages"] = timeline.get("stages", {})
    report["total_hours"] = timeline.get("total_hours")
    report["first_event_at"] = timeline.get("first_event_at")
    report["last_event_at"] = timeline.get("last_event_at")
    try:
        protocol = ""
        if report["method_card"].get("exists"):
            protocol = str(derive_method_card(workspace).get("protocol") or "")
        report["protocol"] = {
            "seeds": detect_seeds(workspace),
            "datasets": detect_datasets(workspace, protocol),
        }
    except Exception:  # noqa: BLE001
        log.debug("capability report: protocol failed", exc_info=True)
        report["protocol"] = {}
    return report


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:,.2f}" if abs(value) >= 100 else f"{value:g}"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, dict):
        return ", ".join(f"{k}={_fmt(v)}" for k, v in value.items()) or "-"
    if isinstance(value, (list, tuple)):
        return ", ".join(_fmt(v) for v in value) or "-"
    return str(value)


def report_rows(report: dict[str, Any]) -> list[tuple[str, Any]]:
    """Flat ``(metric, value)`` rows in display order; the markdown table's spine."""
    rows: list[tuple[str, Any]] = []
    rows.append(("project", report.get("project_id")))
    rows.append(("current stage", report.get("current_stage") or None))
    rows.append(("total hours", report.get("total_hours")))
    for stage, entry in (report.get("stages") or {}).items():
        suffix = " (open)" if entry.get("open") else ""
        rows.append((f"stage {stage} hours{suffix}", entry.get("hours")))
    missions = report.get("missions") or {}
    rows.append(("missions", missions.get("count")))
    rows.append(("missions per stage", missions.get("per_stage") or None))
    rows.append(("mission start attempts", missions.get("start_attempts")))
    rows.append(("mean rounds per mission", missions.get("mean_rounds")))
    reviewer = report.get("reviewer") or {}
    rows.append(("reviews", reviewer.get("reviews")))
    rows.append(("review verdicts", reviewer.get("verdicts") or None))
    rows.append(("review median seconds", reviewer.get("median_duration_s")))
    rows.append(("reviews under 60 s", reviewer.get("under_60s")))
    rows.append(("reviews skipped", reviewer.get("skipped")))
    tokens = report.get("tokens") or {}
    rows.append(("model calls", tokens.get("calls")))
    rows.append(("input tokens", tokens.get("input")))
    rows.append(("output tokens", tokens.get("output")))
    rows.append(("cached tokens", tokens.get("cached")))
    rows.append(("cost usd", tokens.get("usd")))
    for role, bucket in (tokens.get("per_role") or {}).items():
        rows.append((f"cost usd {role}", bucket.get("usd")))
    card = report.get("method_card") or {}
    rows.append(("method card", card.get("exists")))
    rows.append(("method components", card.get("components")))
    rows.append(("component status", card.get("status_counts") or None))
    rows.append(("quoted anchors", card.get("anchors")))
    rows.append(("spec tests bound", card.get("spec_tests_bound")))
    rows.append(("hyperparameters with why", card.get("hyperparameters_with_why")))
    spec = report.get("spec_checks") or {}
    rows.append(("tests/spec", spec.get("spec_dir_exists")))
    rows.append(("spec files", spec.get("spec_files")))
    rows.append(("last check exit code", spec.get("last_exit_code")))
    rows.append(("last check counts", spec.get("counts") or None))
    rows.append(("contradicted components", spec.get("contradicted_components") or None))
    reference = report.get("reference") or {}
    rows.append(("reference clones", reference.get("count")))
    rows.append((
        "reference revisions",
        [f"{c.get('name')}@{c.get('revision') or '?'}" for c in reference.get("clones") or []] or None,
    ))
    protocol = report.get("protocol") or {}
    seeds = protocol.get("seeds") or {}
    datasets = protocol.get("datasets") or {}
    rows.append(("distinct seeds", seeds.get("distinct")))
    rows.append(("seed values", seeds.get("values") or None))
    rows.append(("datasets", datasets.get("count")))
    rows.append(("dataset names", datasets.get("names") or None))
    figures = report.get("figures") or {}
    rows.append(("figure files", figures.get("files")))
    rows.append(("figure lint issues", figures.get("issues")))
    knowledge = report.get("knowledge") or {}
    rows.append(("project skills", knowledge.get("project_skills")))
    rows.append(("decision records", knowledge.get("decision_records")))
    rows.append(("wiki pages", knowledge.get("wiki_pages")))
    paper = report.get("paper") or {}
    rows.append(("paper main.pdf", paper.get("main_pdf_exists")))
    rows.append(("paper pages", paper.get("pages")))
    return rows


def _delta(current: Any, baseline: Any) -> str:
    if isinstance(current, bool) or isinstance(baseline, bool):
        return "" if current == baseline else "changed"
    if isinstance(current, (int, float)) and isinstance(baseline, (int, float)):
        diff = current - baseline
        if diff == 0:
            return "0"
        return f"{diff:+,.2f}" if isinstance(diff, float) and diff != int(diff) else f"{int(diff):+,}"
    if current == baseline:
        return ""
    return "changed"


def render_markdown(report: dict[str, Any], baseline: dict[str, Any] | None = None) -> str:
    """A compact table; with ``baseline`` both columns side by side plus deltas."""
    title = f"## Argus capability report: {report.get('project_id') or 'project'}"
    lines = [title, ""]
    if baseline is None:
        lines.append("| Metric | Value |")
        lines.append("|---|---|")
        for metric, value in report_rows(report):
            lines.append(f"| {metric} | {_fmt(value)} |")
        return "\n".join(lines) + "\n"
    base_rows = dict(report_rows(baseline))
    base_label = str(baseline.get("project_id") or "baseline")
    this_label = str(report.get("project_id") or "this")
    lines.append(f"| Metric | {base_label} | {this_label} | Delta |")
    lines.append("|---|---|---|---|")
    seen: set[str] = set()
    for metric, value in report_rows(report):
        seen.add(metric)
        base_value = base_rows.get(metric)
        lines.append(f"| {metric} | {_fmt(base_value)} | {_fmt(value)} | {_delta(value, base_value)} |")
    for metric, base_value in base_rows.items():
        if metric not in seen:
            lines.append(f"| {metric} | {_fmt(base_value)} | - | {_delta(None, base_value)} |")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m argus.verticals.research.capability_report",
        description="Measure one Argus project's experiment capability (read-only).",
    )
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    fmt = parser.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true", help="print the JSON report (default)")
    fmt.add_argument("--markdown", action="store_true", help="print the markdown table")
    parser.add_argument("--baseline", type=Path, default=None, help="another report JSON to compare with")
    parser.add_argument("--out", type=Path, default=None, help="also write the JSON report here")
    args = parser.parse_args(argv)

    report = build_report(args.state_dir, args.workspace)
    if args.out is not None:
        try:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"could not write {args.out}: {exc}", file=sys.stderr)
    baseline = None
    if args.baseline is not None:
        loaded = _load_json(args.baseline)
        if isinstance(loaded, dict):
            baseline = loaded
        else:
            print(f"baseline unreadable, ignored: {args.baseline}", file=sys.stderr)
    if args.markdown or baseline is not None:
        sys.stdout.write(render_markdown(report, baseline))
    else:
        sys.stdout.write(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
