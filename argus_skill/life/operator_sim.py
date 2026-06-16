"""Long-tailed simulated human operator.

A real human operator is the thing that breaks an autonomous run out of a
rut. Deterministic gates get satisfied minimally or routed around; a person
does not. Crucially, a person's interventions are **long-tailed**: mostly
small grounded nudges, occasionally a sharp out-of-distribution redirect
("throw the whole recipe out"). This module automates exactly that.

Design goals:

* **实事求是 / grounded.** Every message is phrased from the ACTUAL run
  state — the live objective, the current pipeline stage, recent
  results/journal/trace, and whether the team has established
  ``research/GROUND_TRUTH.md`` yet. The operator refers to what is really
  happening, never to a fabricated number.
* **Long-tailed.** Intervention *styles* are drawn from a weighted table:
  HEAD (~70%) grounded nudges, MID (~25%) redirect / demand-for-evidence,
  TAIL (~5%) bold out-of-distribution interventions.
* **Deterministic for tests.** Sampling uses :mod:`random` with an
  injectable seed / ``Random`` instance.
* **General / task-agnostic.** Nothing here knows about any specific
  benchmark, metric, model, or hardware. It reasons purely from whatever
  state it is handed. Fallback strings contain no task literals.
* **Never raises.** Phrasing via the LLM runner is best-effort; on a
  missing/erroring runner it falls back to a deterministic per-style
  string. A failure here must never break the engineer loop.

Public surface:

* :class:`OperatorStyle` — one intervention style (band + weight + phrasing
  instruction + deterministic fallbacks).
* :data:`STYLE_TABLE` / :func:`sample_style` — the long-tailed distribution.
* :class:`RunState` / :func:`gather_run_state` — grounding snapshot.
* :class:`SimulatedOperator` — samples a style and builds one message.
* :func:`make_operator_guidance_provider` — the callable the daemon wires
  into ``SkillLoop(extra_guidance_provider=...)`` to inject one operator
  message per engineer round.
"""
from __future__ import annotations

import os
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..skills.ground_truth import GROUND_TRUTH_RELPATH

# ---------------------------------------------------------------------------
# Intervention styles — the long-tailed distribution
# ---------------------------------------------------------------------------

#: The three weight bands. HEAD dominates (common small nudges); TAIL is the
#: rare, sharp, out-of-distribution intervention that breaks ruts.
BAND_HEAD = "head"
BAND_MID = "mid"
BAND_TAIL = "tail"


@dataclass(frozen=True)
class OperatorStyle:
    """One intervention style.

    ``weight`` is an *absolute* sampling weight; the table is balanced so the
    per-band totals are ~0.70 / ~0.25 / ~0.05 (HEAD / MID / TAIL). ``phrasing``
    instructs the LLM how to voice this style. ``fallbacks`` are deterministic,
    task-agnostic strings used verbatim when no runner is available or the LLM
    call fails — they must never raise and never name a specific task.
    """

    name: str
    band: str
    weight: float
    phrasing: str
    fallbacks: tuple[str, ...]


#: The weighted style table. Per-band totals: HEAD 0.70, MID 0.25, TAIL 0.05.
STYLE_TABLE: tuple[OperatorStyle, ...] = (
    # ---- HEAD (~70%): grounded nudge / pointed question --------------------
    OperatorStyle(
        name="nudge_continue",
        band=BAND_HEAD,
        weight=0.25,
        phrasing=(
            "An encouraging, grounded nudge to keep pushing on the CURRENT "
            "line of work — acknowledge what just happened and say to keep "
            "going."
        ),
        fallbacks=(
            "Looks like that moved things a little — keep going on this line "
            "and tell me what the next concrete step is.",
            "Reasonable progress. Stay on this thread and push it one step "
            "further before you change direction.",
        ),
    ),
    OperatorStyle(
        name="pointed_question",
        band=BAND_HEAD,
        weight=0.25,
        phrasing=(
            "A short pointed question about the REAL bottleneck or the actual "
            "cause of the latest result — make the engineer name it."
        ),
        fallbacks=(
            "That improved a little — but what's the actual bottleneck here? "
            "Name it.",
            "Okay, but why did it move? Tell me the real cause, not a guess.",
        ),
    ),
    OperatorStyle(
        name="small_observation",
        band=BAND_HEAD,
        weight=0.20,
        phrasing=(
            "A small grounded observation about the current state plus a "
            "gentle steer on what to look at next."
        ),
        fallbacks=(
            "I'm watching this — looks incremental so far. What's the one "
            "thing you'd look at next?",
            "Noted where things stand. Pick the highest-leverage next move and "
            "go.",
        ),
    ),
    # ---- MID (~25%): redirect / demand evidence / why ----------------------
    OperatorStyle(
        name="demand_evidence",
        band=BAND_MID,
        weight=0.10,
        phrasing=(
            "A demand for EVIDENCE: did they actually measure/profile this? "
            "Ask to see the real numbers, not a narrative."
        ),
        fallbacks=(
            "You keep tuning — did you ever actually profile the run? Show me "
            "the numbers.",
            "Stop telling me, show me. Where are the measured numbers behind "
            "that claim?",
        ),
    ),
    OperatorStyle(
        name="redirect",
        band=BAND_MID,
        weight=0.08,
        phrasing=(
            "A redirect: tell them to stop the current motion and look at the "
            "real signal (the curve, the log, the profile) before continuing."
        ),
        fallbacks=(
            "Stop for a second — what does the actual curve/log say? Look "
            "before you touch anything else.",
            "Pause the tweaking. Go read the real signal first, then decide.",
        ),
    ),
    OperatorStyle(
        name="why_this",
        band=BAND_MID,
        weight=0.07,
        phrasing=(
            "Challenge the chosen approach: ask WHY this approach over an "
            "obvious alternative, and make them justify it."
        ),
        fallbacks=(
            "Why this approach over the obvious alternative? Justify the "
            "choice before you sink more time in.",
            "Convince me this is the right path and not just the convenient "
            "one. Why this and not something else?",
        ),
    ),
    # ---- TAIL (~5%): bold / sharp / out-of-distribution --------------------
    OperatorStyle(
        name="throw_out",
        band=BAND_TAIL,
        weight=0.0125,
        phrasing=(
            "A bold, sharp redirect: tell them to throw out the current "
            "recipe/approach entirely and try a FUNDAMENTALLY different one."
        ),
        fallbacks=(
            "Throw out the recipe. Stop refining this and try a fundamentally "
            "different approach.",
            "This whole direction is a dead end — scrap it and attack the "
            "problem a completely different way.",
        ),
    ),
    OperatorStyle(
        name="be_ambitious",
        band=BAND_TAIL,
        weight=0.0125,
        phrasing=(
            "Call out timid, incremental work and demand ambition: a "
            "method-level or structural change, not another small nibble."
        ),
        fallbacks=(
            "This is incremental and boring — be ambitious. Make a structural "
            "change, not another tiny nibble.",
            "You're playing it safe. I want a bold, method-level move, not "
            "more fine-tuning around the edges.",
        ),
    ),
    OperatorStyle(
        name="reverify",
        band=BAND_TAIL,
        weight=0.0125,
        phrasing=(
            "Express sharp distrust of a reported number and demand they "
            "re-verify it themselves, from scratch, with their own eyes."
        ),
        fallbacks=(
            "I don't believe that number. Re-verify it yourself, from "
            "scratch, before you build on it.",
            "That result smells wrong. Reproduce it independently and prove "
            "it to me before going further.",
        ),
    ),
    OperatorStyle(
        name="call_lazy",
        band=BAND_TAIL,
        weight=0.0125,
        phrasing=(
            "A blunt callout that they've been grinding the same thing too "
            "long with little to show — that it's lazy, and to change tack."
        ),
        fallbacks=(
            "You've been grinding the same thing for a while now — that's "
            "lazy. Change tack and do something that actually matters.",
            "We're going in circles. This is the easy path, not the right "
            "one — break the pattern.",
        ),
    ),
)


def sample_style(rng: random.Random) -> OperatorStyle:
    """Draw one :class:`OperatorStyle` from the long-tailed distribution.

    Uses ``rng`` (a :class:`random.Random`) so a fixed seed yields a
    deterministic sequence — required by the distribution test.
    """
    return rng.choices(STYLE_TABLE, weights=[s.weight for s in STYLE_TABLE], k=1)[0]


def band_weights() -> dict[str, float]:
    """Return the total sampling weight per band (for tests / introspection)."""
    out: dict[str, float] = {BAND_HEAD: 0.0, BAND_MID: 0.0, BAND_TAIL: 0.0}
    for s in STYLE_TABLE:
        out[s.band] = out.get(s.band, 0.0) + s.weight
    return out


# ---------------------------------------------------------------------------
# Grounding snapshot
# ---------------------------------------------------------------------------


@dataclass
class RunState:
    """A compact, grounded snapshot of what is actually happening.

    Everything is optional / best-effort — the operator phrases from whatever
    is present and stays silent about what is not. No field is task-specific;
    it is whatever the caller managed to read off the live run.
    """

    objective: str = ""
    stage: str | None = None
    has_ground_truth: bool = False
    has_research_dir: bool = False
    journal_tail: str = ""
    recent_scores: str = ""
    trace_tail: str = ""
    # Curated working-memory grounding (from the per-round reviewer checkpoint).
    # These let the operator refer to what the team has actually TRIED, what it
    # says it will do NEXT, and the blocker it named — so a nudge can call out a
    # diagnosis/action mismatch instead of speaking in generic platitudes.
    recent_attempts: str = ""
    next_step: str = ""
    open_blocker: str = ""

    def summarize(self, *, max_chars: int = 1600) -> str:
        """Render the state as a short plaintext block for the LLM prompt."""
        lines: list[str] = []
        obj = (self.objective or "").strip()
        if obj:
            if len(obj) > 400:
                obj = obj[:400].rstrip() + " …"
            lines.append(f"- Objective: {obj}")
        if self.stage:
            lines.append(f"- Current stage: {self.stage}")
        lines.append(
            f"- {GROUND_TRUTH_RELPATH} established: "
            f"{'yes' if self.has_ground_truth else 'NO (not yet written)'}"
        )
        if self.has_research_dir:
            lines.append("- research/ directory present: yes")
        if self.recent_scores.strip():
            lines.append("- Recent results/scores:\n" + _indent(self.recent_scores))
        if self.recent_attempts.strip():
            lines.append(
                "- Recently tried & FAILED (do not just re-suggest these):\n"
                + _indent(self.recent_attempts)
            )
        if self.open_blocker.strip():
            lines.append("- Open blocker (their words): " + self.open_blocker.strip())
        if self.next_step.strip():
            lines.append("- Their stated next step: " + self.next_step.strip())
        if self.journal_tail.strip():
            lines.append("- Recent journal/notes:\n" + _indent(self.journal_tail))
        if self.trace_tail.strip():
            lines.append("- Recent trace:\n" + _indent(self.trace_tail))
        text = "\n".join(lines).strip()
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + " …"
        return text


def _indent(text: str, *, prefix: str = "    ", max_lines: int = 12) -> str:
    rows = [r for r in (text or "").splitlines() if r.strip()]
    rows = rows[-max_lines:]
    return "\n".join(prefix + r.strip() for r in rows)


def _read_tail(path: Path, *, max_lines: int = 12, max_chars: int = 1200) -> str:
    try:
        if not path.is_file():
            return ""
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    rows = [r for r in text.splitlines() if r.strip()]
    tail = "\n".join(rows[-max_lines:])
    if len(tail) > max_chars:
        tail = tail[-max_chars:]
    return tail.strip()


#: A GENERAL "score-like" signal: an UPPER_SNAKE metric token (e.g. a validation
#: metric the run prints to its trace) followed by a number. Nothing here knows
#: any specific metric name — it captures whatever uppercase metric the run
#: itself emits, so the operator can refer to the real recent numbers when no
#: RESULTS.md exists. Requires at least one underscore so generic words / JSON
#: keys don't match.
_METRIC_RE = re.compile(r"([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\s*[:=]\s*([-+]?\d+(?:\.\d+)?)")


def _scores_from_trace(trace_text: str, *, max_items: int = 5) -> str:
    """Mine the last few ``METRIC: value`` pairs out of a raw trace blob.

    Task-agnostic: it matches any UPPER_SNAKE metric token, so a run that
    prints e.g. ``MEAN_VAL_BPB=0.84`` to its event log surfaces here without
    this module ever naming that metric. Returns ``""`` on no match. Defensive.
    """
    if not trace_text:
        return ""
    hits: list[str] = []
    try:
        for match in _METRIC_RE.finditer(trace_text):
            hits.append(f"{match.group(1)}: {match.group(2)}")
    except Exception:  # noqa: BLE001 — mining is best-effort
        return ""
    if not hits:
        return ""
    return "\n".join(hits[-max_items:])


def _load_checkpoint_state(checkpoint_path: Path | str | None) -> Any | None:
    """Load the curated working-memory checkpoint, fail-soft to ``None``.

    Returns ``None`` when the path is absent, the module is unavailable, the
    file is missing/garbled, or the checkpoint carries no curated state. Never
    raises — grounding must never break the engineer loop.
    """
    if checkpoint_path is None:
        return None
    try:
        from ..engineer.checkpoint import load_checkpoint
    except Exception:  # noqa: BLE001 — optional dependency
        return None
    try:
        cp = load_checkpoint(Path(checkpoint_path))
    except Exception:  # noqa: BLE001 — load_checkpoint is fail-soft, belt-and-braces
        return None
    if cp is None or cp.is_empty():
        return None
    return cp


def gather_run_state(
    project_root: Path | str,
    objective: str = "",
    *,
    trace_path: Path | str | None = None,
    checkpoint_path: Path | str | None = None,
) -> RunState:
    """Read a best-effort grounding snapshot from the live run.

    Defensive: every read is guarded, so a missing/garbled file degrades to an
    empty field rather than raising. Nothing here is task-specific — it reads
    the framework's general state files (pipeline stage, ground-truth gate,
    a couple of conventional result/trace artifacts).

    The run's real telemetry does NOT live in the git work-tree: the daemon
    fans events out to ``<life_dir>/events.jsonl`` (next to the per-round
    reviewer checkpoint), so ``trace_path`` / ``checkpoint_path`` let the caller
    point the operator at the ACTUAL progress. When given, the trace tail is
    read from ``trace_path`` (falling back to ``project_root/events.jsonl``),
    recent scores are mined from the trace when no ``RESULTS.md`` exists, and
    the checkpoint's ``tried_and_failed`` / ``next_step`` / ``open_blocker`` are
    surfaced so a nudge can reference what is really happening.
    """
    root = Path(project_root)
    state = RunState(objective=(objective or "").strip())

    # Pipeline stage (general framework state machine).
    try:
        from ..skills.stage_checklists import current_stage

        state.stage = current_stage(root)
    except Exception:  # noqa: BLE001 — never let grounding break the loop
        state.stage = None

    research_dir = root / "research"
    state.has_research_dir = research_dir.is_dir()
    state.has_ground_truth = (root / GROUND_TRUTH_RELPATH).is_file()

    # Recent results — conventional, general artifact names only.
    for cand in ("RESULTS.md", "research/RESULTS.md"):
        scores = _read_tail(root / cand)
        if scores:
            state.recent_scores = scores
            break

    # Recent journal-ish notes.
    for cand in ("research/PROGRESS.md", "PROGRESS.md", "research/JOURNAL.md"):
        notes = _read_tail(root / cand)
        if notes:
            state.journal_tail = notes
            break

    # Recent trace (event log). Prefer the real daemon life-dir trace; fall
    # back to a workdir-local events.jsonl if that's all the caller has.
    trace_tail = ""
    if trace_path is not None:
        trace_tail = _read_tail(Path(trace_path), max_lines=8)
    if not trace_tail:
        trace_tail = _read_tail(root / "events.jsonl", max_lines=8)
    state.trace_tail = trace_tail

    # No RESULTS.md? Mine recent score-like numbers straight out of the trace
    # so the operator can still refer to real movement. Reads a wider window.
    if not state.recent_scores.strip():
        trace_blob = ""
        if trace_path is not None:
            trace_blob = _read_tail(Path(trace_path), max_lines=400, max_chars=8000)
        if not trace_blob:
            trace_blob = _read_tail(
                root / "events.jsonl", max_lines=400, max_chars=8000
            )
        mined = _scores_from_trace(trace_blob)
        if mined:
            state.recent_scores = mined

    # Curated working-memory checkpoint (what was tried/failed, the named
    # blocker, the stated next step) — the richest grounding the run has.
    cp = _load_checkpoint_state(checkpoint_path)
    if cp is not None:
        tried = getattr(cp, "tried_and_failed", None)
        if tried:
            state.recent_attempts = "\n".join(str(t) for t in tried if str(t).strip())
        next_step = getattr(cp, "next_step", "") or ""
        if next_step.strip():
            state.next_step = next_step.strip()
        blocker = getattr(cp, "open_blocker", "") or ""
        if blocker.strip():
            state.open_blocker = blocker.strip()
        # Borrow the checkpoint goal as objective only if we weren't handed one.
        goal = getattr(cp, "goal", "") or ""
        if not state.objective and goal.strip():
            state.objective = goal.strip()

    return state


# ---------------------------------------------------------------------------
# The simulated operator
# ---------------------------------------------------------------------------

_UNSET: Any = object()


def _clean_message(text: str, *, max_sentences: int = 3, max_chars: int = 360) -> str:
    """Normalize an LLM-produced message into a short, plain operator line."""
    msg = (text or "").strip()
    if not msg:
        return ""
    # Drop a leading markdown header / label line if present.
    lines = [ln for ln in msg.splitlines() if ln.strip()]
    lines = [ln for ln in lines if not ln.lstrip().startswith("#")]
    msg = " ".join(lines).strip()
    # Strip wrapping quotes the model sometimes adds.
    if len(msg) >= 2 and msg[0] in "\"'“”" and msg[-1] in "\"'“”":
        msg = msg[1:-1].strip()
    # Clamp to a few sentences.
    out: list[str] = []
    count = 0
    buf = ""
    for ch in msg:
        buf += ch
        if ch in ".!?":
            out.append(buf)
            buf = ""
            count += 1
            if count >= max_sentences:
                break
    if buf.strip() and count < max_sentences:
        out.append(buf)
    msg = "".join(out).strip() or msg
    if len(msg) > max_chars:
        msg = msg[:max_chars].rstrip() + " …"
    return msg


class SimulatedOperator:
    """Produces ONE short, grounded, long-tailed operator message per cycle.

    Parameters
    ----------
    runner:
        An object exposing ``run_exec(prompt=..., options=..., run_label=...,
        resume_thread_id=...)`` (the framework ``RunnerBackend``). When ``None``
        (or when the call errors) the operator falls back to a deterministic
        per-style string. Never required.
    rng / seed:
        Injectable randomness for deterministic tests. Pass an explicit
        :class:`random.Random` via ``rng``, or an int ``seed``; otherwise a
        fresh non-deterministic ``Random`` is used.
    model / reasoning_effort:
        Forwarded to the runner options when phrasing via the LLM.
    """

    def __init__(
        self,
        *,
        runner: Any | None = None,
        rng: random.Random | None = None,
        seed: int | None = None,
        model: str | None = None,
        reasoning_effort: str = "low",
        working_dir: str | None = None,
    ) -> None:
        self._runner = runner
        if rng is not None:
            self._rng = rng
        else:
            self._rng = random.Random(seed)
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._working_dir = working_dir

    # -- sampling ----------------------------------------------------------
    def sample_style(self) -> OperatorStyle:
        return sample_style(self._rng)

    # -- one message per cycle --------------------------------------------
    def next_message(self, state: RunState) -> str:
        """Sample a style and return one grounded operator message."""
        message, _style = self.next_message_with_style(state)
        return message

    def next_message_with_style(
        self, state: RunState
    ) -> tuple[str, OperatorStyle]:
        """Sample a style and return ``(message, style)``.

        Exposes the chosen :class:`OperatorStyle` so the wiring layer can log
        the style/band alongside the emitted message (observability). The
        message itself is identical to :meth:`next_message`.
        """
        style = self.sample_style()
        message = self.build_message(state, style, self._runner)
        return message, style

    def build_message(
        self,
        state: RunState,
        style: OperatorStyle,
        runner: Any | None = _UNSET,
    ) -> str:
        """Phrase ONE short (1-3 sentence) message in ``style`` from ``state``.

        Uses the LLM ``runner`` to ground the phrasing in the real run state.
        Falls back to a deterministic per-style string when ``runner`` is
        ``None`` or the call fails/returns empty. NEVER raises.
        """
        active_runner = self._runner if runner is _UNSET else runner
        if active_runner is not None:
            try:
                phrased = self._phrase_with_runner(state, style, active_runner)
                cleaned = _clean_message(phrased)
                if cleaned:
                    return cleaned
            except Exception:  # noqa: BLE001 — phrasing is best-effort
                pass
        return self._fallback(style)

    # -- internals ---------------------------------------------------------
    def _fallback(self, style: OperatorStyle) -> str:
        if not style.fallbacks:
            return ""
        return self._rng.choice(list(style.fallbacks))

    def _phrase_with_runner(
        self, state: RunState, style: OperatorStyle, runner: Any
    ) -> str:
        from ..core.models import RunnerOptions

        prompt = self._build_prompt(state, style)
        options = RunnerOptions(
            model=self._model,
            reasoning_effort=self._reasoning_effort,
            skip_git_repo_check=True,
            working_dir=self._working_dir,
        )
        result = runner.run_exec(
            prompt=prompt,
            options=options,
            run_label="operator-sim",
            resume_thread_id=None,
        )
        return getattr(result, "last_agent_message", "") or ""

    @staticmethod
    def _build_prompt(state: RunState, style: OperatorStyle) -> str:
        return (
            "You are the HUMAN OPERATOR of an autonomous engineering run, "
            "checking in between work rounds. Write ONE short message (1-3 "
            "sentences, plain text, first person, no markdown, no preamble) to "
            "the engineer, IN THIS STYLE:\n\n"
            f"  {style.phrasing}\n\n"
            "Ground it in what is ACTUALLY happening right now (state below): "
            "refer to the real objective / stage / recent results. If they have "
            "TRIED & FAILED things, acknowledge those by name and push for "
            "something different rather than re-suggesting them. If they wrote "
            "down a diagnosis (e.g. an established GROUND_TRUTH) but their stated "
            "next step ignores it, call out that mismatch and tell them to act "
            "on the diagnosis. Do NOT invent numbers or facts you cannot see in "
            "the state. Speak like a terse, direct human operator.\n\n"
            "## Current run state\n"
            f"{state.summarize()}\n\n"
            "Reply with ONLY the message text."
        )


# ---------------------------------------------------------------------------
# Daemon wiring helper
# ---------------------------------------------------------------------------


#: Event type emitted (when an ``on_event`` sink is wired) each time the
#: operator returns a non-empty message. Makes the otherwise-invisible
#: intervention observable in ``events.jsonl`` so a run can be audited for
#: whether/when/how the operator fired (incl. the rare long-tail redirects).
OPERATOR_EVENT_TYPE = "life.operator_sim"


def make_operator_guidance_provider(
    *,
    project_root: Path | str,
    objective: str = "",
    runner: Any | None = None,
    seed: int | None = None,
    rng: random.Random | None = None,
    model: str | None = None,
    reasoning_effort: str = "low",
    trace_path: Path | str | None = None,
    checkpoint_path: Path | str | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> Callable[[], list[str]]:
    """Return a zero-arg provider for ``SkillLoop(extra_guidance_provider=...)``.

    The daemon calls the returned callable at the start of each engineer round;
    it gathers a fresh grounding snapshot (from the live run's real ``trace_path``
    / ``checkpoint_path`` when supplied), samples a long-tailed style, and
    returns ``[message]`` (the loop appends it under "## Operator guidance").
    Returns ``[]`` (silent) on any failure — it must never break the round.

    When ``on_event`` is supplied, each non-empty message is also surfaced as a
    ``life.operator_sim`` marker event (``style`` / ``band`` / ``message`` /
    ``agent_layer='operator'``) so the intervention is observable. The emit is
    guarded and never raises into the round loop.
    """
    operator = SimulatedOperator(
        runner=runner,
        seed=seed,
        rng=rng,
        model=model,
        reasoning_effort=reasoning_effort,
        working_dir=str(Path(project_root)),
    )

    def _provider() -> list[str]:
        try:
            state = gather_run_state(
                project_root,
                objective,
                trace_path=trace_path,
                checkpoint_path=checkpoint_path,
            )
            message, style = operator.next_message_with_style(state)
        except Exception:  # noqa: BLE001 — never break the engineer loop
            return []
        message = (message or "").strip()
        if not message:
            return []
        if on_event is not None:
            try:
                on_event(
                    {
                        "type": OPERATOR_EVENT_TYPE,
                        "style": style.name,
                        "band": style.band,
                        "message": message,
                        "agent_layer": "operator",
                    }
                )
            except Exception:  # noqa: BLE001 — observability must never break the loop
                pass
        return [message]

    return _provider


#: Opt-in env flag. Default OFF so existing behaviour/tests are unchanged
#: unless an operator explicitly enables the simulated operator.
ENABLE_ENV_VAR = "ARGUS_SKILL_SIMULATED_OPERATOR"
SEED_ENV_VAR = "ARGUS_SKILL_SIMULATED_OPERATOR_SEED"


def simulated_operator_enabled() -> bool:
    """True iff ``ARGUS_SKILL_SIMULATED_OPERATOR`` is set truthy (default OFF)."""
    raw = os.environ.get(ENABLE_ENV_VAR)
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def operator_guidance_provider_from_env(
    *,
    project_root: Path | str,
    objective: str = "",
    runner: Any | None = None,
    model: str | None = None,
    reasoning_effort: str = "low",
    trace_path: Path | str | None = None,
    checkpoint_path: Path | str | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> Callable[[], list[str]] | None:
    """Return a provider only when the opt-in env flag is set, else ``None``.

    This is the single gate the daemon calls: when the flag is OFF it returns
    ``None`` and ``SkillLoop`` is constructed exactly as before (no behaviour
    change). When ON it wires a seeded :class:`SimulatedOperator`, threading the
    live run's ``trace_path`` / ``checkpoint_path`` (grounding) and an
    ``on_event`` sink (observability) through to each call. Never raises.
    """
    if not simulated_operator_enabled():
        return None
    try:
        seed_raw = os.environ.get(SEED_ENV_VAR)
        seed = int(seed_raw) if seed_raw and seed_raw.strip() else None
    except ValueError:
        seed = None
    try:
        return make_operator_guidance_provider(
            project_root=project_root,
            objective=objective,
            runner=runner,
            seed=seed,
            model=model,
            reasoning_effort=reasoning_effort,
            trace_path=trace_path,
            checkpoint_path=checkpoint_path,
            on_event=on_event,
        )
    except Exception:  # noqa: BLE001 — wiring must never break a mission
        return None


__all__ = [
    "BAND_HEAD",
    "BAND_MID",
    "BAND_TAIL",
    "OperatorStyle",
    "STYLE_TABLE",
    "OPERATOR_EVENT_TYPE",
    "sample_style",
    "band_weights",
    "RunState",
    "gather_run_state",
    "SimulatedOperator",
    "make_operator_guidance_provider",
    "operator_guidance_provider_from_env",
    "simulated_operator_enabled",
    "ENABLE_ENV_VAR",
    "SEED_ENV_VAR",
]
