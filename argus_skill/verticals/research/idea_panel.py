"""Ask several independently-trained models for ideas, then let them argue.

One model asked once returns six candidates that share one model's taste and
one model's blind spots. Two models from different labs disagree about which
idea is worth a campaign, and the disagreement is the useful part: an objection
a GPT-family model cannot see is often obvious to a Gemini- or Claude-family
one, and a candidate that survives cross-examination by a stranger is a better
bet than one nobody argued with.

Availability decides the panel. Whatever CLIs are installed are the panel;
anything missing is skipped without comment, and a box with one usable backend
behaves exactly as it did before this module existed.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PANEL_KNOB = "ARGUS_SKILL_IDEA_PANEL"

# Backends whose CLIs front genuinely different model families. Ordered by how
# much a panel gains from adding them, not by preference.
_CROSS_VENDOR_BACKENDS = ("codex", "claude", "copilot", "grok")


def _resolve_bin(backend: str) -> str | None:
    """Resolve a seat's CLI, or nothing. A name Argus does not support and a
    CLI that is not installed are the same answer here: no seat."""
    from ...agent_cli.runner_backend import SUPPORTED_BACKENDS, resolve_runner_bin

    if backend not in SUPPORTED_BACKENDS:
        return None
    try:
        resolved = resolve_runner_bin(backend)
    except Exception:  # noqa: BLE001 — an unresolvable backend is unavailable
        return None
    return resolved if resolved and Path(resolved).exists() else None


_usable: dict[str, bool] = {}


def _is_usable(backend: str) -> bool:
    """Answer whether this box can actually buy tokens from a backend.

    An installed CLI is not a subscription. Someone who pays for one vendor
    still has the other launchers on PATH, and seating them would spend an
    ideation round discovering they cannot log in. Ask the readiness check
    Argus already owns, once per process, and treat anything it cannot
    confirm as no seat.
    """
    if backend in _usable:
        return _usable[backend]
    from ...core.backend_readiness import check_backend_readiness

    try:
        ok = bool(check_backend_readiness(backend, probe_auth=True, timeout_s=15).ok)
    except Exception:  # noqa: BLE001 — unverifiable is not usable
        ok = False
    if not ok:
        log.debug("idea-panel: %s is installed but not usable here", backend)
    _usable[backend] = ok
    return ok


def parse_panel(raw: str | None) -> list[tuple[str, str]]:
    """Read ``backend:model`` entries, or bare backends, from operator config."""
    seats: list[tuple[str, str]] = []
    for entry in str(raw or "").replace("\n", ",").split(","):
        entry = entry.strip()
        if not entry:
            continue
        backend, _, model = entry.partition(":")
        seat = (backend.strip().lower(), model.strip())
        if seat not in seats:
            seats.append(seat)
    return seats


def available_panel(configured: str | None = None) -> list[tuple[str, str]]:
    """Return the seats this machine can actually fill.

    An operator list is honoured as written, minus seats whose CLI is missing.
    With no list, every installed cross-vendor CLI takes a seat on its own
    default model.
    """
    seats = parse_panel(configured if configured is not None else os.environ.get(PANEL_KNOB))
    if not seats:
        seats = [(backend, "") for backend in _CROSS_VENDOR_BACKENDS]
    return [
        seat
        for seat in seats
        if _resolve_bin(seat[0]) and _is_usable(seat[0])
    ]


def _runner_for(backend: str, agent_bin: str) -> Any | None:
    from ...agent_cli.agent_cli_runner import AgentCliRunner

    try:
        return AgentCliRunner(agent_bin=agent_bin, backend=backend)
    except Exception:  # noqa: BLE001 — a seat that will not build is just empty
        log.debug("idea-panel: cannot build a runner for %s", backend, exc_info=True)
        return None


def _ask(seat: tuple[str, str], prompt: str, workdir: str, label: str) -> str:
    from ...core.models import RunnerOptions
    from ...core.run_gateway import run_exec as gateway_run_exec
    from .idea_search import _extract_message

    backend, model = seat
    agent_bin = _resolve_bin(backend)
    if not agent_bin:
        return ""
    runner = _runner_for(backend, agent_bin)
    if runner is None:
        return ""
    options = RunnerOptions(
        reasoning_effort="high",
        working_dir=workdir,
        skip_git_repo_check=True,
        full_auto=True,
        live_search=True,
    )
    if model:
        options = options.__class__(**{**vars(options), "model": model})
    try:
        return _extract_message(
            gateway_run_exec(runner, prompt=prompt, options=options, run_label=label)
        ).strip()
    except Exception:  # noqa: BLE001 — one silent panellist must not end the panel
        log.debug("idea-panel: %s produced nothing", backend, exc_info=True)
        return ""


def _cross_examination_prompt(direction: str, others: str) -> str:
    return (
        "You are on a panel choosing which research idea deserves a full "
        f"campaign in this direction:\n\n{direction}\n\n"
        "Below are candidates proposed by other models, trained by other labs. "
        "You did not write them and you are not required to be kind to them.\n\n"
        f"{others}\n\n"
        "For each candidate, give the single strongest reason it would fail — "
        "the objection you would raise if you had to referee the resulting "
        "paper, not a list of caveats. Say plainly where the proposer is "
        "assuming something the literature does not support, where the "
        "baseline is weaker than it looks, and where the win would be too "
        "small to matter even if everything worked.\n\n"
        "Then name the one candidate you would actually bet a month of GPUs "
        "on, and say what you would have to see in the first week to keep "
        "believing it. If none of them are worth a campaign, say that and say "
        "what question you would attack instead.\n\n"
        "Write as `### Panel review — <candidate id>` blocks, then a final "
        "`### Panel bet` block."
    )


def run_panel(
    workdir: Any,
    *,
    direction: str,
    proposal_prompt: str,
    configured: str | None = None,
) -> str:
    """Collect independent proposals, then have each seat cross-examine the rest.

    Returns markdown to append to the candidate file, or ``""`` when this box
    cannot seat a real panel. Never raises.
    """
    try:
        seats = available_panel(configured)
        if len(seats) < 2:
            return ""
        cwd = str(workdir)
        log.info(
            "idea-panel: seating %d cross-vendor models (%s)",
            len(seats),
            ", ".join(b for b, _ in seats),
        )

        proposals: list[tuple[str, str]] = []
        for seat in seats:
            body = _ask(seat, proposal_prompt, cwd, "idea-panel-propose")
            if "## Candidate" in body:
                proposals.append((seat[0], body))
        if len(proposals) < 2:
            return ""

        sections = [
            f"\n## Candidates from `{backend}`\n\n{body}\n" for backend, body in proposals
        ]

        for backend, _ in proposals:
            others = "\n\n".join(
                f"### From {other}\n\n{body}"
                for other, body in proposals
                if other != backend
            )
            review = _ask(
                (backend, dict(seats).get(backend, "")),
                _cross_examination_prompt(direction, others),
                cwd,
                "idea-panel-review",
            )
            if review:
                sections.append(f"\n## Cross-examination by `{backend}`\n\n{review}\n")

        log.info("idea-panel: %d proposal set(s) and their reviews", len(proposals))
        return "".join(sections)
    except Exception:  # noqa: BLE001 — ideation must never break the campaign
        log.debug("idea-panel failed", exc_info=True)
        return ""


__all__ = ["PANEL_KNOB", "available_panel", "parse_panel", "run_panel"]
