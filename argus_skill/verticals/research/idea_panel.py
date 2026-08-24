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

    Seating is opt-in. Across four directions and thirty-two blind-scored
    candidates a panel did not beat single-model ideation on the mean — it
    produced the best candidate in the batch and more than twice as many weak
    ones, so it buys spread rather than level. That is a trade an operator
    chooses, not one a campaign inherits from which CLIs happen to be
    installed. Named seats whose CLI is missing are dropped, not failed.
    """
    seats = parse_panel(configured if configured is not None else os.environ.get(PANEL_KNOB))
    if not seats:
        return []
    usable = [seat for seat in seats if _resolve_bin(seat[0]) and _is_usable(seat[0])]
    # Two seats on the same backend are two labs only when they name different
    # models. A backend that serves one model, asked twice, is one model
    # arguing with itself — which is worse than not seating a panel, because it
    # looks like one.
    if len({seat[1] for seat in usable}) == 1 and len({seat[0] for seat in usable}) == 1:
        return usable[:1]
    return usable


def _runner_for(backend: str, agent_bin: str) -> Any | None:
    from ...agent_cli.agent_cli_runner import AgentCliRunner

    try:
        return AgentCliRunner(agent_bin=agent_bin, backend=backend)
    except Exception:  # noqa: BLE001 — a seat that will not build is just empty
        log.debug("idea-panel: cannot build a runner for %s", backend, exc_info=True)
        return None


def _ask(seat: tuple[str, str], prompt: str, workdir: str, label: str) -> str:
    # The runner's own options type, not the core one: only this carries the
    # fields the per-backend command builders read.
    from ...agent_cli.agent_cli_runner import RunnerOptions
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
        model=model or None,
        reasoning_effort="high",
        working_dir=workdir,
        skip_git_repo_check=True,
        full_auto=True,
        live_search=True,
    )
    try:
        # Every seat argues from a clean session: a bare runner also wants the
        # thread stated, where the campaign's wrapper defaults it.
        return _extract_message(
            gateway_run_exec(
                runner,
                prompt=prompt,
                options=options,
                run_label=label,
                resume_thread_id=None,
            )
        ).strip()
    except Exception:  # noqa: BLE001 — one silent panellist must not end the panel
        log.debug("idea-panel: %s produced nothing", backend, exc_info=True)
        return ""


def _seat_id(seat: tuple[str, str]) -> str:
    """Name a seat by the model it speaks for, falling back to its CLI."""
    backend, model = seat
    return f"{backend}:{model}" if model else backend


def _in_parallel(seats, work):
    """Run one call per seat at the same time, keeping the seat order.

    A slow panellist should cost the panel its own latency, not everyone's, and
    one that raises should cost only its own seat.
    """
    if len(seats) < 2:
        return [(seat, work(seat)) for seat in seats]
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(
        max_workers=len(seats), thread_name_prefix="argus-panel"
    ) as pool:
        futures = [pool.submit(work, seat) for seat in seats]
        out = []
        for seat, future in zip(seats, futures):
            try:
                out.append((seat, future.result()))
            except Exception:  # noqa: BLE001 — one seat failing is one empty seat
                log.debug("idea-panel: %s raised", _seat_id(seat), exc_info=True)
                out.append((seat, ""))
    return out


def _verdict_prompt(direction: str, transcript: str) -> str:
    return (
        "The panel has proposed candidates and cross-examined each other on "
        f"this direction:\n\n{direction}\n\n"
        "Here is the full record — every candidate and every objection raised "
        f"against it:\n\n{transcript}\n\n"
        "One of these gets a campaign; the rest do not. Name the single "
        "candidate you would run, including one you did not propose and "
        "including one you attacked, if the argument moved you. Say what "
        "survived the objections against it, what is now the biggest risk you "
        "are knowingly accepting, and the first measurement that would tell "
        "you within a week that you picked wrong.\n\n"
        "Answer as:\n"
        "### Winner\n<candidate id and title>\n"
        "### Why it survived\n<the objection it answered>\n"
        "### Risk accepted\n<what could still kill it>\n"
        "### Week-one check\n<the measurement>"
    )


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
    """Propose in parallel, argue, then converge on one candidate to run.

    Returns markdown to append to the candidate file, or ``""`` when this box
    cannot seat a real panel. Never raises.
    """
    try:
        seats = available_panel(configured)
        if len(seats) < 2:
            return ""
        cwd = str(workdir)
        log.info(
            "idea-panel: seating %d models (%s)",
            len(seats),
            ", ".join(_seat_id(seat) for seat in seats),
        )

        # Seats do not read each other while proposing, so proposing is
        # embarrassingly parallel and a round costs one model's latency
        # rather than the sum of them.
        proposals = [
            (seat, body)
            for seat, body in _in_parallel(
                seats, lambda seat: _ask(seat, proposal_prompt, cwd, "idea-panel-propose")
            )
            if "## Candidate" in body
        ]
        if len(proposals) < 2:
            return ""

        sections = [
            f"\n## Candidates from `{_seat_id(seat)}`\n\n{body}\n" for seat, body in proposals
        ]

        def _others_for(seat: tuple[str, str]) -> str:
            # Identity is the seat, not the launcher: two models reached through
            # one CLI are still two labs, and each must be handed the other's
            # candidates rather than an empty page.
            return "\n\n".join(
                f"### From {_seat_id(other)}\n\n{body}"
                for other, body in proposals
                if other != seat
            )

        reviews = _in_parallel(
            [seat for seat, _ in proposals],
            lambda seat: _ask(
                seat,
                _cross_examination_prompt(direction, _others_for(seat)),
                cwd,
                "idea-panel-review",
            ),
        )
        for seat, review in reviews:
            if review:
                sections.append(
                    f"\n## Cross-examination by `{_seat_id(seat)}`\n\n{review}\n"
                )

        # The campaign runs one idea, so the panel has to land on one. Each seat
        # reads the whole argument — every candidate and every objection to it —
        # and names its winner. Agreement is a strong signal and disagreement is
        # information; neither is decided here.
        transcript = "".join(sections)
        verdicts = _in_parallel(
            [seat for seat, _ in proposals],
            lambda seat: _ask(seat, _verdict_prompt(direction, transcript), cwd, "idea-panel-verdict"),
        )
        for seat, verdict in verdicts:
            if verdict:
                sections.append(f"\n## Verdict from `{_seat_id(seat)}`\n\n{verdict}\n")

        log.info(
            "idea-panel: %d proposal set(s), %d review(s), %d verdict(s)",
            len(proposals),
            sum(1 for _, r in reviews if r),
            sum(1 for _, v in verdicts if v),
        )
        return "".join(sections)
    except Exception:  # noqa: BLE001 — ideation must never break the campaign
        log.debug("idea-panel failed", exc_info=True)
        return ""


__all__ = ["PANEL_KNOB", "available_panel", "parse_panel", "run_panel"]
