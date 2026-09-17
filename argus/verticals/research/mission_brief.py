"""The task brief the research vertical prepends to every Experiment mission.

Why it exists. A project failed because the task the Engineer received was a
few Planner sentences on top of fifteen hundred words of policy: the claim
was defined in passing inside the notes, the environment was not described,
and after a provider failure the benchmark was quietly narrowed. Benchmarks
with crisp prompts and prepared environments get strong implementations from
the same models. This module gives an Argus Experiment mission the same shape
at zero model cost: what the claim is (fixed, verbatim), where each component
stands and where its code is, what the environment actually contains, what
this task must achieve, and what changed since the last round.

Nothing here gates anything. The brief is evidence and orientation for the
Engineer; roles read it and decide. Everything is fail-soft: ``prepare_mission``
never raises and returns ``""`` when there is nothing to say or when anything
goes wrong. Nothing is cached; every call recomputes from the project tree
with cheap, time-limited subprocesses (``git``, the project interpreter's
``--version``).

The hook is forwarded by keyword through ``VerticalContract.prepare_mission``
(see ``argus/core/vertical_contract.py``); the parameter names are the
contract.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import method_card
from .notes import read_research_notes

log = logging.getLogger(__name__)

#: Hard cap on the block ``prepare_mission`` returns.
MAX_BRIEF_LINES = 70
HEADER = "## Task brief"
FIXED_CLAIM_SENTENCE = (
    "Only the operator changes this claim. The code is made to agree with it, "
    "never the claim with the code; when the implementation falls short, "
    "iterate on the implementation."
)
DEFINITION_OF_DONE = (
    "Definition of done: the checks above pass and the host-run tests/spec are "
    "green for the components this task names."
)

INTERPRETER_TIMEOUT_S = 5.0
TORCH_PROBE_TIMEOUT_S = 15.0
TORCH_CANDIDATES = 5
_TORCH_PROBE_CACHE: dict[str, str] = {}
COMPONENT_LINES = 12
DATA_DIRS = ("data", "datasets")
DATA_ENTRIES = 5
DATA_WALK_FILE_CAP = 20_000
THIRD_PARTY_CLONES = 5
CHANGED_PATHS = 10
TEXT_LIMIT = 1200

#: Stages the brief is built for; ``review`` only when the mission is a repair.
BRIEF_STAGES = frozenset({"experiment", "review"})
_REPAIR_WORDS = ("repair", "revision", "revise", "experiment")

_ROUTE_ID = re.compile(r"\broute-\d{1,3}\b", re.IGNORECASE)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _one_line(value: object, limit: int = TEXT_LIMIT) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _attribute(mission: object, name: str) -> Any:
    """``mission.name`` or ``mission[name]``; ``None`` when neither exists."""
    if mission is None:
        return None
    value = getattr(mission, name, None)
    if value is None and isinstance(mission, Mapping):
        value = mission.get(name)
    return value


def _human_size(size: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _normalized_stage(stage: object) -> str:
    return str(stage or "").strip().lower()


def _is_repair_mission(mission: object) -> bool:
    """A Review-stage mission that revises method or experiment, by its tags/scope."""
    words: list[str] = []
    tags = _attribute(mission, "tags")
    if isinstance(tags, (list, tuple, set)):
        words.extend(str(tag or "").lower() for tag in tags)
    decision = _attribute(mission, "manager_decision")
    if isinstance(decision, Mapping):
        words.append(str(decision.get("scope") or "").lower())
    scope = _attribute(mission, "scope")
    if isinstance(scope, str):
        words.append(scope.lower())
    text = " ".join(words)
    return any(word in text for word in _REPAIR_WORDS)


def _wants_brief(stage: str, mission: object) -> bool:
    if stage == "experiment":
        return True
    return stage == "review" and _is_repair_mission(mission)


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------


def _selected_idea(project_root: Path, state_root: Path | None) -> dict[str, Any]:
    from ...core.pipeline_state import read_pipeline_state

    for root in (project_root, state_root):
        if root is None:
            continue
        try:
            payload = read_pipeline_state(root)
        except Exception:  # noqa: BLE001 - a broken state file is not a brief failure
            continue
        selected = payload.get("selected_idea") if isinstance(payload, dict) else None
        if isinstance(selected, dict) and selected.get("route_id"):
            return selected
    return {}


def _claim_from_notes(project_root: Path) -> str:
    """The selected route named in RESEARCH_NOTES.md, with the line that names it."""
    try:
        notes = read_research_notes(project_root)
    except Exception:  # noqa: BLE001
        return ""
    for line in notes.splitlines():
        if _ROUTE_ID.search(line) and re.search(r"select|chosen|route", line, re.IGNORECASE):
            return _one_line(line.strip().lstrip("-*# "))
    return ""


_ROUTE_STATEMENT_HEADINGS = re.compile(
    r"^#{1,4}\s*(?:\d+[.)]?\s*)?(?:(?:central |core |main )?(?:claim|hypothesis|thesis|"
    r"mechanism)|executive summary|summary)\b",
    re.IGNORECASE,
)
ROUTE_STATEMENT_CHARS = 700


def _route_statement(
    project_root: Path, state_root: Path | None, selected: dict[str, Any]
) -> tuple[str, str, str]:
    """(title, statement, path) quoted from the selected route's own text.

    Before METHOD.md exists the only fixed text is the route the selector
    chose. Its first heading names the method and the first paragraph under a
    claim-like heading (or, failing that, the first paragraph at all) states
    it. The selector's rationale is *why it won*, which is not a claim, and one
    brief opened with it: "Every route in the regenerated portfolio was
    rejected by its reviewer...", forty words about the other routes.
    """
    candidates: list[str] = []
    for key in ("route_artifact",):
        value = selected.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())
    evidence = selected.get("evidence_considered")
    if isinstance(evidence, (list, tuple)):
        candidates.extend(
            str(item).strip()
            for item in evidence
            if str(item).strip().lower().endswith(".md")
        )
    for rel in candidates:
        for root in (project_root, state_root):
            if root is None:
                continue
            path = Path(root) / rel
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            title = ""
            paragraphs: list[tuple[str, str]] = []  # (heading, paragraph)
            heading = ""
            buffer: list[str] = []
            for raw in text.splitlines() + [""]:
                line = raw.strip()
                if line.startswith("#"):
                    if buffer:
                        paragraphs.append((heading, " ".join(buffer)))
                        buffer = []
                    if not title and line.lstrip("#").strip():
                        title = line.lstrip("#").strip()
                    else:
                        heading = line
                    continue
                if not line or line.startswith(("|", "```", "---")):
                    if buffer:
                        paragraphs.append((heading, " ".join(buffer)))
                        buffer = []
                    continue
                buffer.append(line)
            statement = ""
            for head, paragraph in paragraphs:
                if head and _ROUTE_STATEMENT_HEADINGS.match(head):
                    statement = paragraph
                    break
            if not statement and paragraphs:
                statement = paragraphs[0][1]
            if statement:
                return (
                    _one_line(title, 200),
                    _one_line(statement, ROUTE_STATEMENT_CHARS),
                    rel,
                )
    return "", "", ""


def _claim_section(
    card: dict[str, Any], project_root: Path, state_root: Path | None
) -> list[str]:
    statement = _one_line(card.get("statement"))
    title = _one_line(card.get("title"), 200)
    lines: list[str] = []
    if statement:
        lines.append("### Claim (fixed)" + (f" — {title}" if title else ""))
        lines.append(statement + " (METHOD.md, verbatim)")
    else:
        selected = _selected_idea(project_root, state_root)
        route_id = _one_line(selected.get("route_id"), 80)
        rationale = _one_line(selected.get("rationale"))
        route_title, route_statement, route_path = _route_statement(
            project_root, state_root, selected
        )
        if route_statement:
            lines.append(
                "### Claim (fixed)"
                + (f" — {route_title}" if route_title else f" — {route_id}")
            )
            lines.append(
                f"{route_statement} ({route_path}; the selected route, verbatim — "
                "write METHOD.md from it before method code)"
            )
        elif route_id:
            lines.append("### Claim (fixed)")
            lines.append(
                f"Selected idea {route_id}" + (f": {rationale}" if rationale else "")
                + " (from the Idea selection; write METHOD.md from the route before method code)"
            )
        else:
            from_notes = _claim_from_notes(project_root)
            if from_notes:
                lines.append("### Claim (fixed)")
                lines.append(from_notes + " (RESEARCH_NOTES.md; write METHOD.md from the route)")
    if lines:
        lines.append(FIXED_CLAIM_SENTENCE)
    return lines


def _attainment_section(card: dict[str, Any]) -> list[str]:
    from .method_card import render_claim_attainment

    lines = render_claim_attainment(card)
    return ["### Claim attainment (last statement)", *lines[1:]] if lines else []


def _reality_section(card: dict[str, Any]) -> list[str]:
    """Stand-ins and the results footprint, so the Engineer sees what the Reviewer will."""
    lines = method_card.render_run_reality(card, limit=5)
    return ["### Run reality", *lines] if lines else []


def _components_section(card: dict[str, Any]) -> list[str]:
    components = card.get("components") or []
    if not components:
        return []
    lines = ["### Components now"]
    for entry in components[:COMPONENT_LINES]:
        where = method_card.anchor_location(entry)
        failing = method_card.failing_test_ids(entry)
        line = f"- {entry['component']}: {entry['status']}"
        line += f" — {where}" if where else " — no `# @component` anchor in the code"
        if failing:
            line += "; failing: " + ", ".join(failing[:3])
        lines.append(line)
    if len(components) > COMPONENT_LINES:
        lines.append(f"- ... {len(components) - COMPONENT_LINES} more in METHOD.md")
    return lines


def _interpreter_line(project_root: Path) -> str:
    python = project_root / ".venv" / "bin" / "python"
    if not python.exists():
        return "- Interpreter: no .venv/bin/python in the project (create one before the first run)"
    version = ""
    try:
        proc = subprocess.run(
            [str(python), "--version"],
            capture_output=True,
            text=True,
            timeout=INTERPRETER_TIMEOUT_S,
            check=False,
        )
        version = (proc.stdout or proc.stderr or "").strip().splitlines()[0] if (proc.stdout or proc.stderr) else ""
    except (OSError, subprocess.SubprocessError):
        version = ""
    # Name the invocation, not just the file: a review-stage Engineer ran a
    # bare `python3 -m pytest`, got nothing, and spent a round on
    # `find / -name pytest` over the whole disk.
    return (
        f"- Interpreter: .venv/bin/python ({version or 'version unknown'}); "
        "run tests as `.venv/bin/python -m pytest tests/spec`"
    )


def _torch_probe(python: Path) -> str:
    """``torch 2.9.0 (CUDA yes)``, ``no torch``, or '' when the interpreter cannot be run.

    Cached for the process: torch installs do not change between rounds and
    the import alone takes seconds.
    """
    key = str(python)
    if key in _TORCH_PROBE_CACHE:
        return _TORCH_PROBE_CACHE[key]
    try:
        proc = subprocess.run(
            [str(python), "-c", "import torch; print(torch.__version__, torch.cuda.is_available())"],
            capture_output=True,
            text=True,
            timeout=TORCH_PROBE_TIMEOUT_S,
            check=False,
        )
        out = (proc.stdout or "").strip().split()
        if proc.returncode == 0 and len(out) >= 2:
            result = f"torch {out[0]} (CUDA {'yes' if out[1] == 'True' else 'no'})"
        else:
            result = "no torch"
    except (OSError, subprocess.SubprocessError):
        result = ""
    _TORCH_PROBE_CACHE[key] = result
    return result


def _host_interpreters(project_root: Path) -> list[tuple[str, Path]]:
    """Interpreters worth asking about torch: the project venv, the host runtime, PATH, conda."""
    found: list[tuple[str, Path]] = []
    seen: set[str] = set()

    def add(label: str, raw: object) -> None:
        if not raw or len(found) >= TORCH_CANDIDATES:
            return
        path = Path(str(raw))
        try:
            real = str(path.resolve())
        except OSError:
            return
        if real in seen or not path.exists():
            return
        seen.add(real)
        found.append((label, path))

    add(".venv/bin/python", project_root / ".venv" / "bin" / "python")
    add("host runtime (read-only; never install into it)", sys.executable)
    add("python3 on PATH", shutil.which("python3"))
    add("system python3", "/usr/bin/python3")
    add("python on PATH", shutil.which("python"))
    conda_prefix = os.environ.get("CONDA_PREFIX", "")
    if conda_prefix:
        add("conda env", Path(conda_prefix) / "bin" / "python")
    for envs_root in (Path.home() / ".conda" / "envs", Path.home() / "miniconda3" / "envs", Path.home() / "anaconda3" / "envs"):
        if envs_root.is_dir():
            for env in sorted(envs_root.iterdir())[:3]:
                add(f"conda env {env.name}", env / "bin" / "python")
    return found


def _torch_line(project_root: Path) -> str:
    """Where torch already is on this host, so nobody searches the disk for it.

    The v5 control project's Engineer, told only "no .venv in the project",
    tried the system python, found no torch and ran ``find / -name torch``
    for eight minutes. The host can answer that question in seconds.
    """
    probes = [(label, path, _torch_probe(path)) for label, path in _host_interpreters(project_root)]
    probes = [(label, path, result) for label, path, result in probes if result]
    if not probes:
        return ""
    parts = [f"{path} [{label}]: {result}" for label, path, result in probes]
    return (
        "- Torch on this host: " + "; ".join(parts) + ". Create .venv and pip install torch into it "
        "(CUDA wheels matching the driver above); do not scan the disk for packages (`find /`): "
        "the host has already listed what exists."
    )


TOOLS_OF_INTEREST = ("uv", "pip", "git", "latexmk", "pdflatex", "nvidia-smi", "conda", "node", "npx", "gh")


def _tools_line() -> str:
    """Which command-line tools are on PATH, so absence is a fact and not a search."""
    present = [name for name in TOOLS_OF_INTEREST if shutil.which(name)]
    missing = [name for name in TOOLS_OF_INTEREST if name not in present]
    line = "- Tools on PATH: " + (", ".join(present) if present else "none of the usual ones")
    if missing:
        line += f" (absent: {', '.join(missing)}; absent here means absent, do not `find /` for a tool)"
    return line


def _installed_distributions(project_root: Path) -> set[str]:
    """Normalised distribution names under the project venv's site-packages."""
    names: set[str] = set()
    venv = project_root / ".venv" / "lib"
    if not venv.is_dir():
        return names
    try:
        for site in venv.glob("python*/site-packages"):
            for entry in site.iterdir():
                if entry.name.endswith((".dist-info", ".egg-info")):
                    names.add(entry.name.split("-", 1)[0].lower().replace("_", "-"))
    except OSError:
        pass
    return names


def _packages_line(project_root: Path, card: dict[str, Any]) -> str:
    imported = [
        entry for entry in (card.get("reused_code") or []) if entry.get("kind") == "package"
    ]
    installed = _installed_distributions(project_root)
    parts: list[str] = []
    if installed:
        parts.append(f"{len(installed)} distributions installed in .venv")
    if imported:
        present, missing = [], []
        for entry in imported:
            name = str(entry.get("name") or "")
            key = name.lower().replace("_", "-")
            if (installed and key in installed) or (not installed and entry.get("revision_or_version")):
                present.append(name)
            else:
                missing.append(name)
        if present:
            parts.append("imported packages present: " + ", ".join(present[:10]))
        if missing:
            parts.append("imported but not found: " + ", ".join(missing[:10]))
    return "- Packages: " + "; ".join(parts) if parts else ""


def _clones_line(project_root: Path, card: dict[str, Any]) -> str:
    third_party = project_root / "third_party"
    if not third_party.is_dir():
        return ""
    by_name = {
        str(entry.get("name")): entry
        for entry in (card.get("reused_code") or [])
        if entry.get("kind") == "third_party"
    }
    items: list[str] = []
    try:
        clones = sorted(child for child in third_party.iterdir() if child.is_dir())
    except OSError:
        return ""
    for clone in clones[:THIRD_PARTY_CLONES]:
        known = by_name.get(clone.name) or {}
        revision = str(known.get("revision_or_version") or "") or method_card._git(
            ["rev-parse", "--short", "HEAD"], clone
        )
        items.append(f"{clone.name} @ {revision or 'no git revision'}")
    if len(clones) > THIRD_PARTY_CLONES:
        items.append(f"... {len(clones) - THIRD_PARTY_CLONES} more")
    return "- third_party clones: " + "; ".join(items) if items else ""


def _dir_size(path: Path) -> tuple[int, bool]:
    total, count = 0, 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for filename in filenames:
            try:
                total += os.stat(os.path.join(dirpath, filename)).st_size
            except OSError:
                continue
            count += 1
            if count >= DATA_WALK_FILE_CAP:
                return total, True
    return total, False


def _data_lines(project_root: Path) -> list[str]:
    lines: list[str] = []
    for name in DATA_DIRS:
        directory = project_root / name
        if not directory.is_dir():
            continue
        try:
            children = sorted(directory.iterdir())
        except OSError:
            continue
        size, capped = _dir_size(directory)
        entries = ", ".join(child.name + ("/" if child.is_dir() else "") for child in children[:DATA_ENTRIES])
        if len(children) > DATA_ENTRIES:
            entries += f", ... {len(children) - DATA_ENTRIES} more"
        lines.append(
            f"- {name}/: {_human_size(size)}{'+' if capped else ''}"
            + (f" ({entries})" if entries else " (empty)")
        )
    return lines


def _gpu_line() -> str:
    try:
        from .prompt_policy import _query_local_gpus

        gpus = _query_local_gpus()
    except Exception:  # noqa: BLE001
        return ""
    if not gpus:
        return ""
    return "- GPUs: " + "; ".join(
        f"{index} {name} {total:.0f} GB ({used:.0f} GB used)" for index, name, total, used in gpus
    )


def _checks_line(project_root: Path, card: dict[str, Any]) -> str:
    checks = card.get("checks")
    if not isinstance(checks, dict):
        return "- Last host-run tests/spec: none recorded yet"
    counts = checks.get("counts") or {}
    count_text = ", ".join(f"{n} {outcome.lower()}" for outcome, n in sorted(counts.items())) or "no tests"
    contradicted = [
        entry["component"]
        for entry in (card.get("components") or [])
        if str(entry.get("status") or "").startswith("contradicted")
    ]
    line = (
        f"- Last host-run tests/spec: round {checks.get('round_index')}, "
        f"exit {checks.get('exit_code')}, {count_text}"
    )
    if contradicted:
        line += "; contradicted: " + ", ".join(contradicted[:6])
    return line


MODEL_CACHE_ENTRIES = 8


def _model_cache_line() -> str:
    """Local model and dataset caches, so nobody searches the disk for them.

    Two Engineers ran `find /` over the whole machine looking for weights or a
    package (66 minutes in one case); a third, finding nothing quickly, wrote a
    mock model. The caches are where the hub library keeps them: the
    configured HF_HUB_CACHE / HF_HOME, or the default under the home directory.
    """
    roots: list[Path] = []
    for env in ("HF_HUB_CACHE", "HF_HOME"):
        value = os.environ.get(env, "").strip()
        if value:
            path = Path(value).expanduser()
            roots.append(path if env == "HF_HUB_CACHE" else path / "hub")
    roots.append(Path.home() / ".cache" / "huggingface" / "hub")
    seen: set[Path] = set()
    parts: list[str] = []
    for root in roots:
        try:
            root = root.resolve()
        except OSError:
            continue
        if root in seen or not root.is_dir():
            continue
        seen.add(root)
        try:
            entries = sorted(p.name for p in root.iterdir() if p.is_dir() and p.name.startswith(("models--", "datasets--")))
        except OSError:
            continue
        if not entries:
            continue
        names = [e.split("--", 1)[1].replace("--", "/") for e in entries]
        models = [n for e, n in zip(entries, names) if e.startswith("models--")]
        datasets = [n for e, n in zip(entries, names) if e.startswith("datasets--")]
        shown = models[:MODEL_CACHE_ENTRIES]
        more = f" (+{len(models) - len(shown)} more)" if len(models) > len(shown) else ""
        parts.append(
            f"{root}: {len(models)} models" + (f" [{', '.join(shown)}{more}]" if shown else "")
            + (f", {len(datasets)} datasets" if datasets else "")
        )
    if not parts:
        return ""
    return "- Model caches (hub layout; use them before downloading or substituting): " + "; ".join(parts)


def _environment_section(project_root: Path, card: dict[str, Any]) -> list[str]:
    lines = ["### Environment now", _interpreter_line(project_root)]
    for producer in (
        lambda: _torch_line(project_root),
        _tools_line,
        lambda: _packages_line(project_root, card),
        lambda: _clones_line(project_root, card),
        _gpu_line,
        _model_cache_line,
        lambda: _checks_line(project_root, card),
    ):
        try:
            line = producer()
        except Exception:  # noqa: BLE001
            line = ""
        if line:
            lines.append(line)
    try:
        data_lines = _data_lines(project_root)
    except Exception:  # noqa: BLE001
        data_lines = []
    lines.extend(data_lines)
    return lines


def _mission_contract(mission: object, state_root: Path | None) -> dict[str, Any]:
    """acceptance_check / decision_rule / non_goals from the mission or its context file."""
    contract: dict[str, Any] = {}
    for name in ("acceptance_check", "decision_rule", "non_goals"):
        value = _attribute(mission, name)
        if value:
            contract[name] = value
    if len(contract) == 3 or state_root is None:
        return contract
    mission_id = _one_line(_attribute(mission, "id"), 200)
    if not mission_id:
        return contract
    path = Path(state_root) / "handoffs" / mission_id / "mission.json"
    payload = method_card._load_json(path)
    if payload:
        for name in ("acceptance_check", "decision_rule", "non_goals"):
            if not contract.get(name) and payload.get(name):
                contract[name] = payload[name]
    return contract


def _task_section(mission: object, state_root: Path | None) -> list[str]:
    contract = _mission_contract(mission, state_root)
    lines = ["### This task"]
    acceptance = _one_line(contract.get("acceptance_check"))
    if acceptance:
        lines.append(f"- Acceptance check: {acceptance}")
    decision = _one_line(contract.get("decision_rule"))
    if decision:
        lines.append(f"- Decision rule: {decision}")
    non_goals = contract.get("non_goals")
    if isinstance(non_goals, str):
        non_goals = [non_goals]
    if isinstance(non_goals, (list, tuple)):
        items = [_one_line(item, 300) for item in non_goals if _one_line(item)]
        if items:
            lines.append("- Non-goals: " + "; ".join(items[:8]))
    lines.append(DEFINITION_OF_DONE)
    return lines


def _since_section(project_root: Path, card: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    changed = method_card.changed_files(project_root)
    if changed:
        untracked = sum(1 for entry in changed if entry["status"] == "??")
        modified = len(changed) - untracked
        paths = ", ".join(entry["path"] for entry in changed[:CHANGED_PATHS])
        if len(changed) > CHANGED_PATHS:
            paths += f", ... {len(changed) - CHANGED_PATHS} more"
        lines.append(
            f"- Uncommitted: {modified} modified/staged, {untracked} untracked ({paths})"
        )
    change_log = card.get("change_log") or []
    if change_log:
        last = change_log[0]
        files = last.get("files") or []
        files_text = ", ".join(files[:5]) + (f", ... {len(files) - 5} more" if len(files) > 5 else "")
        lines.append(
            f"- Last change: {last.get('when', '')} {_one_line(last.get('summary'), 200)}"
            + (f" ({files_text})" if files_text else "")
        )
    return ["### Since last time", *lines] if lines else []


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def render_task_brief(
    *,
    stage: str,
    project_root: Path,
    state_root: Path | None,
    mission: object,
) -> str:
    """The brief, or '' when the stage does not take one or the project is empty.

    Raises only on programming errors; ``prepare_mission`` is the guarded
    entry point the framework calls.
    """
    normalized = _normalized_stage(stage)
    if normalized not in BRIEF_STAGES or not _wants_brief(normalized, mission):
        return ""
    root = Path(project_root)
    if not root.is_dir():
        return ""
    try:
        card = method_card.derive_method_card(root)
    except Exception:  # noqa: BLE001
        log.debug("task brief: method card derivation failed", exc_info=True)
        card = {}
    state = Path(state_root) if state_root else None

    sections: list[list[str]] = []
    for build in (
        lambda: _claim_section(card, root, state),
        lambda: _components_section(card),
        lambda: _attainment_section(card),
        lambda: _environment_section(root, card),
        lambda: _reality_section(card),
        lambda: _task_section(mission, state),
        lambda: _since_section(root, card),
    ):
        try:
            section = build()
        except Exception:  # noqa: BLE001
            log.debug("task brief: a section failed", exc_info=True)
            section = []
        if section:
            sections.append(section)
    claim_present = any(section[0].startswith("### Claim") for section in sections)
    if not claim_present and not card.get("exists"):
        # Nothing fixed to work against and no card: the brief would be noise.
        return ""
    lines = [HEADER]
    for section in sections:
        lines.extend(section)
    return "\n".join(lines[:MAX_BRIEF_LINES])


def prepare_mission(
    *,
    stage: str,
    project_root: Path,
    state_root: Path,
    mission: object,
) -> str:
    """Vertical hook: the task brief for an Experiment (or repair) mission.

    Keyword-only because the framework forwards this hook by keyword; the
    parameter names are the contract. Never raises; '' on any failure.
    """
    try:
        return render_task_brief(
            stage=stage,
            project_root=project_root,
            state_root=state_root,
            mission=mission,
        )
    except Exception:  # noqa: BLE001 - a brief must never take a mission down
        log.debug("task brief failed", exc_info=True)
        return ""


__all__ = [
    "DEFINITION_OF_DONE",
    "FIXED_CLAIM_SENTENCE",
    "HEADER",
    "MAX_BRIEF_LINES",
    "prepare_mission",
    "render_task_brief",
]
