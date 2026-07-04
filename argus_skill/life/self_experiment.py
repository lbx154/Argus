"""Self-experiment: flow-conservation probe (approach C · Phase-1 OBSERVE).

自省第一步 —— **流量守恒探针**。这是"深而广的自我改进"骨架的第一块可交付
增量:纯确定性、无 LLM、fail-soft。它只做一件事,把它做到不可造假:

    对每条"信号流"不变式(producer 信号 → consumer 信号),数一数两端各出现
    了多少次。如果 producer 出现了足够多次(≥ ``min_producer``)、而 consumer
    **一次都没出现**,那这条线就"疑似断了"—— 一个 dead wire。

这正是 argus 之前**发现不了**的那一类"结构性深 bug":
``self_evolve.process_lesson`` 被产出了 7 次,但 ``skill.created`` 是 0 —— 蒸馏
线断了,却没有任何单点报错。守恒律把这类"负空间"的缺失变成一个可数、可复现、
不可编造的结构信号。

设计边界(遵循 CLAUDE.md 的 *harness 没 agent 聪明*):

* 本模块只做**领域无关的笨计数**。它绝不判断"这个 gap 值不值得修"、"根因是
  什么"、"怎么修"—— 那些是 reviewer / planner(agent)的科研判断。探针只把
  结构信号写进 journal(``self_experiment.gap_suspected``),让 agent 自己去
  接触、去判断、去修。
* **诚实的局限**(不过度声称):这是"consumer 整类缺席"的检测,不是逐 producer
  的因果归因。如果 ``skill.created`` 是由**别的** producer(如 reviewer 的
  skill_ops)产生的,本探针会把 process_lesson→skill 这条线也算作"活着"(假
  阴性)。宁可漏报、绝不编造 —— 逐 producer 因果探针是 v2 的事。
* 加新不变式只需往 :data:`INVARIANTS` 里加一行;两端信号必须是**可靠持久化**
  的(journal kind / event type 都落 canonical events.jsonl)。

Public surface:

* :class:`FlowInvariant` — 一条守恒律的声明(producer/consumer 各自的 kind + 来源)。
* :data:`INVARIANTS` — 已证实的两条 dead-wire 候选(flagship = process_lesson→skill)。
* :class:`GapFinding` — 一次扫描命中的结构缺口。
* :class:`ConservationProbe` — 纯计数扫描器(``scan`` 无副作用、无 LLM)。
* :func:`read_events_jsonl` / :func:`run_probe` — 从 memory 落盘处读流并扫描。
* :func:`maybe_journal_gap_advisory` — 把命中写成 journal 建议(按 recent 窗口去重)。

供 supervisor 通过一个 epoch-gated 的一行 fail-soft delegate 调用(见
``argus_skill/life/supervisor/_core.py`` tick())。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .memory import JournalEntry, LifeMemory

log = logging.getLogger(__name__)

# Journal kind written for each suspected flow-conservation gap. Advisory
# only — the agent (reviewer/planner) decides whether to act.
GAP_KIND = "self_experiment.gap_suspected"

# Recent-journal window used to dedup re-surfacing the same invariant across
# ticks. Matches the advisor's convention (self_evolve_advisor.py).
DEFAULT_RECENT_WINDOW = 200

# Signal sources understood by the probe. "journal" = journal.entry events
# counted by ``.kind``; "events" = rows in events.jsonl counted by ``["type"]``.
_JOURNAL = "journal"
_EVENTS = "events"


@dataclass(frozen=True)
class FlowInvariant:
    """One flow-conservation law: a ``producer`` signal that is *supposed* to
    lead to a ``consumer`` signal downstream.

    Counting is by exact kind/type equality — deliberately dumb and
    non-fabricable. Both endpoints must be reliably persisted in canonical
    events.jsonl (journal kinds as journal.entry events, event types as events).
    """

    name: str
    producer_kind: str
    producer_source: str  # _JOURNAL | _EVENTS
    consumer_kind: str
    consumer_source: str  # _JOURNAL | _EVENTS
    # Noise floor: don't cry "dead wire" until the producer has fired enough
    # times that a zero consumer is meaningful rather than early-run jitter.
    min_producer: int = 3


# Seed registry — two proven dead-wire candidates. Both endpoints are reliably
# persisted, both are real conservation laws in the self-evolve subsystem.
#
#   process_lesson_to_skill  ── FLAGSHIP, reproduces the real observed bug:
#       7 self_evolve.process_lesson produced → 0 skill.created consumed.
#   missing_tool_to_skill    ── a surfaced missing-tool advisory should
#       eventually mint a skill; a channel that never does is dead.
#
# 加新不变式:再加一个 FlowInvariant(...) 即可。两端务必是可靠持久化的信号。
INVARIANTS: tuple[FlowInvariant, ...] = (
    FlowInvariant(
        name="process_lesson_to_skill",
        producer_kind="self_evolve.process_lesson",
        producer_source=_JOURNAL,
        consumer_kind="skill.created",
        consumer_source=_EVENTS,
        min_producer=3,
    ),
    FlowInvariant(
        name="missing_tool_to_skill",
        producer_kind="self_evolve.missing_tool_advisory",
        producer_source=_JOURNAL,
        consumer_kind="skill.created",
        consumer_source=_EVENTS,
        min_producer=3,
    ),
)


@dataclass
class GapFinding:
    """One suspected dead wire surfaced by a scan."""

    invariant_name: str
    producer_count: int
    consumer_count: int
    # A few producer identifiers so the agent can go read the actual sites.
    sample_sites: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Stream extraction (fail-soft: a bad line / missing file never raises)
# --------------------------------------------------------------------------
def _journal_kind(entry: Any) -> str:
    """Best-effort ``kind`` of a journal entry (dataclass or dict)."""
    kind = getattr(entry, "kind", None)
    if kind is None and isinstance(entry, dict):
        kind = entry.get("kind")
    return str(kind or "")


def _journal_label(entry: Any) -> str:
    """Short identifier for a journal entry, for ``sample_sites``."""
    ident = getattr(entry, "id", None)
    if ident is None and isinstance(entry, dict):
        ident = entry.get("id")
    return f"journal:{_journal_kind(entry)}:{ident or '?'}"


def _event_type(event: Any) -> str:
    """Best-effort ``type`` of an events.jsonl row (dict or object)."""
    if isinstance(event, dict):
        return str(event.get("type") or "")
    return str(getattr(event, "type", "") or "")


def _event_label(event: Any) -> str:
    ts = event.get("ts") if isinstance(event, dict) else getattr(event, "ts", None)
    return f"event:{_event_type(event)}:{ts if ts is not None else '?'}"


def read_events_jsonl(root: Any) -> list[dict[str, Any]]:
    """Read ``<root>/events.jsonl`` into a list of dicts. Fail-soft: returns
    ``[]`` on any missing-file / I/O / parse problem, and silently skips
    individual malformed lines."""
    out: list[dict[str, Any]] = []
    try:
        if root is None:
            return out
        path = Path(root) / "events.jsonl"
        if not path.exists():
            return out
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                out.append(row)
    except Exception:  # noqa: BLE001 — probe must never break the caller
        return out
    return out


def resolve_events_dir(memory: Any) -> Any:
    """Directory that actually holds ``events.jsonl``.

    CRITICAL correctness note: in daemon (split-memory) mode ``memory`` is a
    ``MemoryBundle`` whose ``.root`` is the GLOBAL root, but ``events.jsonl`` is
    written to the PROJECT root (``mem.project.root`` == the daemon's
    ``runtime_root``) — same place ``memory.journal`` writes. Reading
    ``memory.root/events.jsonl`` there would miss every ``skill.created`` event
    and FALSELY flag a live wire as dead. So prefer ``project_root``; only fall
    back to ``root`` for the legacy single-rooted ``LifeMemory`` (tests / ``life
    run``), where ``events.jsonl`` and the journal share one root.
    """
    return getattr(memory, "project_root", None) or getattr(memory, "root", None)


# --------------------------------------------------------------------------
# The probe (pure counting; no LLM, no judgment, no side effects)
# --------------------------------------------------------------------------
class ConservationProbe:
    """Counts producer/consumer signals per invariant and emits a
    :class:`GapFinding` whenever a producer fired ``>= min_producer`` times but
    its consumer never fired. Strict zero-consumer keeps v1 non-fabricable and
    threshold-free ("consumer << producer" partial-starvation is a v2 refinement
    that would need a tuned ratio — deliberately omitted here)."""

    def __init__(self, invariants: Iterable[FlowInvariant] = INVARIANTS) -> None:
        self.invariants = tuple(invariants)

    @staticmethod
    def _collect(
        source: str,
        kind: str,
        journal_entries: list[Any],
        events: list[Any],
    ) -> list[str]:
        """Return labels of every entry matching ``kind`` in the given source."""
        if source == _JOURNAL:
            return [
                _journal_label(e)
                for e in journal_entries
                if _journal_kind(e) == kind
            ]
        if source == _EVENTS:
            return [_event_label(e) for e in events if _event_type(e) == kind]
        return []

    def scan(
        self,
        journal_entries: Iterable[Any] | None,
        events: Iterable[Any] | None,
    ) -> list[GapFinding]:
        """Pure scan. ``journal_entries`` = objects/dicts with ``kind``;
        ``events`` = dicts with ``type``. Returns one finding per dead wire."""
        j = list(journal_entries or [])
        ev = list(events or [])
        findings: list[GapFinding] = []
        for inv in self.invariants:
            producers = self._collect(inv.producer_source, inv.producer_kind, j, ev)
            consumers = self._collect(inv.consumer_source, inv.consumer_kind, j, ev)
            if len(producers) >= inv.min_producer and len(consumers) == 0:
                findings.append(
                    GapFinding(
                        invariant_name=inv.name,
                        producer_count=len(producers),
                        consumer_count=0,
                        sample_sites=producers[:3],
                    )
                )
        return findings


def run_probe(
    memory: LifeMemory,
    *,
    events: Iterable[dict[str, Any]] | None = None,
    invariants: Iterable[FlowInvariant] = INVARIANTS,
) -> list[GapFinding]:
    """Convenience: read journal from ``memory`` + events from disk (or the
    ``events`` override, for tests) and scan. Fail-soft on journal read."""
    try:
        entries = list(memory.journal.all())
    except Exception:  # noqa: BLE001
        entries = []
    ev = list(events) if events is not None else read_events_jsonl(
        resolve_events_dir(memory)
    )
    return ConservationProbe(invariants).scan(entries, ev)


# --------------------------------------------------------------------------
# Surfacing (writes advisory journal entries; dedup by recent window)
# --------------------------------------------------------------------------
def _invariant_tag(name: str) -> str:
    return f"invariant:{name}"


def _recent_gap_invariants(memory: LifeMemory, window: int) -> set[str]:
    """Invariant names already surfaced within the recent journal window, so a
    standing gap isn't re-journaled every epoch."""
    seen: set[str] = set()
    try:
        tail = getattr(memory.journal, "tail", None)
        entries = list(tail(window)) if callable(tail) else list(memory.journal.all())[-window:]
    except Exception:  # noqa: BLE001
        return seen
    for entry in entries:
        if _journal_kind(entry) != GAP_KIND:
            continue
        for tag in (getattr(entry, "tags", None) or []):
            if isinstance(tag, str) and tag.startswith("invariant:"):
                seen.add(tag.split(":", 1)[1])
    return seen


def maybe_journal_gap_advisory(
    memory: LifeMemory,
    findings: Iterable[GapFinding],
    *,
    recent_window: int = DEFAULT_RECENT_WINDOW,
    on_cost: Any = None,
) -> list[str]:
    """Write each *new* :class:`GapFinding` to the journal as a
    ``self_experiment.gap_suspected`` entry. Skips any invariant already
    surfaced in the recent window. Returns the invariant names newly written.

    Fail-soft: the caller (supervisor) wraps this in try/except — a self-
    experiment hiccup must never block the main tick. Enqueues NO backlog item:
    the agent decides what to do with the signal, per *harness 没 agent 聪明*.
    """
    findings = list(findings or [])
    if not findings:
        return []
    already = _recent_gap_invariants(memory, recent_window)
    written: list[str] = []
    for f in findings:
        if f.invariant_name in already:
            continue
        summary = (
            f"Flow-conservation gap: {f.producer_count} producer signal(s) but "
            f"{f.consumer_count} consumer — the '{f.invariant_name}' wire appears "
            f"dead. Structural signal only; investigate whether the consumer is "
            f"disconnected and decide if it is worth repairing."
        )
        entry = JournalEntry.new(
            kind=GAP_KIND,
            title=f"suspected dead wire: {f.invariant_name}",
            summary=summary,
            tags=["self-experiment", _invariant_tag(f.invariant_name)],
            extra={
                "invariant": f.invariant_name,
                "producer_count": f.producer_count,
                "consumer_count": f.consumer_count,
                "sample_sites": list(f.sample_sites),
            },
        )
        try:
            memory.journal.append(entry)
        except Exception:  # noqa: BLE001
            continue
        if callable(on_cost):
            try:
                on_cost(entry)
            except Exception:  # noqa: BLE001
                pass
        written.append(f.invariant_name)
    return written


# --------------------------------------------------------------------------
# Live surfacing to the planner (C Phase-2 · close-the-loop)
# --------------------------------------------------------------------------
def render_open_gaps_block(
    findings: Iterable[GapFinding],
    invariants: Iterable[FlowInvariant] = INVARIANTS,
) -> str:
    """Render the CURRENTLY-open dead wires as a planner-prompt block.

    把"当前还开着的" dead wire 渲染成 planner prompt 里的一个专属区块。这是 C 的
    闭环:探针只是**发现** gap(OBSERVE);这个区块每个规划周期把 gap 实时端到
    planner 面前,让 agent(planner L4)决定"要不要开一个查根因的 mission、还是写
    一行理由驳回"—— **消费决策归 agent**,harness 只负责如实呈现结构信号。

    Pure + deterministic: no journaling, no LLM, no side effects. Returns ``""``
    when there are no open gaps, so on the HEALTHY path the planner prompt is
    byte-for-byte unchanged. It is COUNT-framed ("producer fired Nx, consumer
    fired 0x") and explicitly NOT a verdict — the harness stays a dumb counter.

    HONEST FRAMING: this is a **dormant smoke detector**. The two seed invariants
    (process_lesson→skill, missing_tool→skill) may currently be ALIVE in the
    running daemon (skill.created fires from the distillation wire + reviewer
    skill_ops), so this block is EXPECTED to be empty in production — it only
    fires when a genuinely dead wire exists. Shipping reliable surfacing infra,
    not a near-term behavior change.
    """
    findings = list(findings or [])
    if not findings:
        return ""
    inv_by_name = {inv.name: inv for inv in invariants}
    rows: list[str] = []
    for f in findings:
        inv = inv_by_name.get(f.invariant_name)
        wire = (
            f" ['{inv.producer_kind}' -> '{inv.consumer_kind}']" if inv is not None else ""
        )
        samples = ", ".join(list(f.sample_sites)[:3]) or "(none)"
        rows.append(
            f"- {f.invariant_name}{wire}: producer fired {f.producer_count}x, "
            f"consumer fired {f.consumer_count}x. sample producers: {samples}"
        )
    header = (
        "\n## Open structural dead-wires (self-experiment · flow-conservation)\n"
        "The harness COUNTS producer->consumer signal pairs across the WHOLE run. "
        "A wire below fired its producer many times while its consumer NEVER fired "
        "— a silent structural gap with no single-point error. This is a COUNT "
        "ONLY, not a verdict, and it grants NO budget/scope.\n"
        "MANDATE — for EACH wire: either queue ONE root-cause investigation/repair "
        "mission that opens the sample producer sites and finds why the consumer is "
        "disconnected, OR explicitly defer/reject it with a one-line reason — do "
        "NOT silently drop it. YOU decide whether each is worth repairing, the root "
        "cause, and how (or why not). This does NOT override rule 0 / the stage "
        "gate: if the current stage checklist forbids this work now, defer with "
        "that as the reason. A wire drops off this list automatically once its "
        "consumer fires.\n"
    )
    return header + "\n".join(rows)


__all__ = [
    "GAP_KIND",
    "DEFAULT_RECENT_WINDOW",
    "FlowInvariant",
    "INVARIANTS",
    "GapFinding",
    "ConservationProbe",
    "read_events_jsonl",
    "resolve_events_dir",
    "run_probe",
    "maybe_journal_gap_advisory",
    "render_open_gaps_block",
]
