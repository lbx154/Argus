"""Recurring infrastructure-failure detector for the self-evolve loop
(Signal B · trajectory).

Scans a finished mission's observable surface for a **bounded, curated**
set of infrastructure failure CLASSES — the structural signal that the
*same kind of failure keeps recurring* and the harness may want a
distilled debugging / playbook skill for it.

Contrast with Signal A (``missing_tool_detector``):

* Signal A fires when a tool the agent needed **did not exist**
  (``command not found`` / ``No module named X``).
* Signal B fires on **misconfigurations of tools that DO exist** — the
  call was malformed / the environment was mis-budgeted — which Signal A
  explicitly skips. e.g. ``torch.OutOfMemoryError`` from too-high vLLM
  ``gpu_memory_utilization`` during colocated FSDP weight-sync.

Pure pattern detector. **No quality judgment, no I/O.** Per skill 04,
this module only does the structural half; whether a recurrence is worth
minting a skill for is the reviewer/planner agent's call. The advisor
(``recurring_failure_advisor``) layers cross-mission counting on top.

The signature set is intentionally SMALL and EXECUTION-MEASURABLE:
each class names a failure whose fix can be validated by re-running and
asserting the error is gone (so a minted skill stays inside the
mint-skill "execution-measurable only" safety rule).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class FailureSignature:
    """A factual observation that a known infra failure class appeared
    in a trajectory.

    ``signature`` is a stable slug (e.g. ``"cuda_oom"``) used as the
    cross-mission counting key. ``category`` groups related signatures
    for human reading. ``generic`` marks wrapper symptoms that should be
    suppressed when a more specific signature co-occurs in the same
    mission (e.g. a vLLM engine-init failure that was really caused by
    CUDA OOM).
    """

    signature: str
    category: str
    context: str
    generic: bool = False
    evidence: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "signature": self.signature,
            "category": self.category,
            "context": self.context,
            "generic": self.generic,
            "evidence": list(self.evidence),
        }


# ---------------------------------------------------------------------------
# Curated signature patterns. Order matters only for evidence; dedup is by
# signature slug. Keep this set bounded — a noisy detector mints vague
# skills. Each tuple: (signature, category, human_context, generic, regex).
# ---------------------------------------------------------------------------

_PATTERNS: tuple[tuple[str, str, str, bool, "re.Pattern[str]"], ...] = (
    (
        "cuda_oom",
        "gpu_memory",
        "CUDA out of memory (GPU memory budget exceeded)",
        False,
        re.compile(
            r"torch\.(?:cuda\.)?OutOfMemoryError"
            r"|CUDA (?:error: )?out of memory"
            r"|CUDA out of memory",
            re.IGNORECASE,
        ),
    ),
    (
        "cuda_device_assert",
        "cuda_kernel",
        "CUDA device-side assert (bad index / kernel precondition)",
        False,
        re.compile(r"device-side assert triggered", re.IGNORECASE),
    ),
    (
        "nccl_dist_error",
        "distributed",
        "NCCL / torch.distributed collective failure or timeout",
        False,
        re.compile(
            r"NCCL error"
            r"|ProcessGroupNCCL"
            r"|Watchdog caught collective operation timeout"
            r"|torch\.distributed\b.*\btimed?\s*out",
            re.IGNORECASE,
        ),
    ),
    (
        "hf_flash_attn_unavailable",
        "attention_backend",
        "FlashAttention2 toggled on but package unusable in this env",
        False,
        re.compile(
            r"FlashAttention2 has been toggled on, but it cannot be used",
            re.IGNORECASE,
        ),
    ),
    (
        "vllm_engine_init_failed",
        "rollout_engine",
        "vLLM engine core failed to initialize",
        True,  # generic wrapper — often a symptom of OOM / config below
        re.compile(
            r"Engine core initialization failed"
            r"|EngineCore.*failed to (?:start|initialize)",
            re.IGNORECASE,
        ),
    ),
    (
        "ray_worker_died",
        "distributed",
        "Ray worker/actor died unexpectedly",
        True,  # generic wrapper — root cause usually a more specific sig
        re.compile(
            r"RayActorError"
            r"|WorkerCrashedError"
            r"|The actor died unexpectedly",
            re.IGNORECASE,
        ),
    ),
    (
        "image_generation_model_unavailable",
        "external_capability",
        "Image generation route rejected configured model/deployment",
        False,
        re.compile(
            r"(?:/images/generations|image[_ -]?tool|image generation|"
            r"gpt-image-[\w.-]*).*?(?:unknown_model|Unknown model|"
            r"DeploymentNotFound|deployment[- ]?not[- ]?found|"
            r"Resource not found)"
            r"|(?:unknown_model|Unknown model|DeploymentNotFound|"
            r"deployment[- ]?not[- ]?found|Resource not found).*?"
            r"(?:/images/generations|image[_ -]?tool|image generation|"
            r"gpt-image-[\w.-]*)",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
)


def _scan_text(text: str, *, evidence_label: str) -> list[FailureSignature]:
    out: list[FailureSignature] = []
    if not text:
        return out
    for signature, category, context, generic, regex in _PATTERNS:
        m = regex.search(text)
        if m is None:
            continue
        # capture a short evidence excerpt around the match
        start = max(0, m.start() - 20)
        excerpt = text[start : m.start() + 120].strip().replace("\n", " ")
        out.append(
            FailureSignature(
                signature=signature,
                category=category,
                context=context,
                generic=generic,
                evidence=(evidence_label, excerpt[:200]),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Mission-level entry point
# ---------------------------------------------------------------------------


def scan_failure_signatures(
    *,
    agent_messages: Iterable[str] = (),
    check_output_tails: Iterable[str] = (),
    fatal_error: str | None = None,
    events: Iterable[dict[str, Any]] = (),
) -> list[FailureSignature]:
    """Scan a finished mission for recurring-infra-failure signatures.

    Sources mirror ``missing_tool_detector.scan_mission``. Returns the
    deduped (by ``signature``) list for this mission, with generic
    wrapper signatures suppressed when at least one specific signature is
    also present (so a vLLM engine-init failure caused by CUDA OOM is
    rooted at ``cuda_oom``, not the wrapper).

    Pure: no I/O. Each signature appears at most once.
    """
    bag: list[FailureSignature] = []
    for i, msg in enumerate(agent_messages):
        bag.extend(_scan_text(str(msg or ""), evidence_label=f"agent_messages[{i}]"))
    for i, tail in enumerate(check_output_tails):
        bag.extend(
            _scan_text(str(tail or ""), evidence_label=f"check_output_tails[{i}]")
        )
    if fatal_error:
        bag.extend(_scan_text(str(fatal_error), evidence_label="fatal_error"))
    for i, event in enumerate(events):
        for key in ("output_excerpt", "text"):
            val = event.get(key) if isinstance(event, dict) else None
            if val:
                bag.extend(
                    _scan_text(str(val), evidence_label=f"events[{i}].{key}")
                )

    # Dedup by signature slug, keeping first-seen evidence.
    deduped: dict[str, FailureSignature] = {}
    for sig in bag:
        if sig.signature not in deduped:
            deduped[sig.signature] = sig

    sigs = list(deduped.values())
    has_specific = any(not s.generic for s in sigs)
    if has_specific:
        # Suppress generic wrapper symptoms; the specific root cause is
        # the meaningful recurrence key.
        sigs = [s for s in sigs if not s.generic]
    return sigs


def scan_events_jsonl(path: Path) -> list[FailureSignature]:
    """Convenience: scan a complete events.jsonl file (CLI / offline)."""
    events: list[dict] = []
    if not path.exists():
        return []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return scan_failure_signatures(events=events)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    sigs = scan_failure_signatures(
        events=[
            json.loads(line)
            for line in args.events.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    )
    if args.json:
        print(json.dumps([s.to_dict() for s in sigs], indent=2))
    else:
        if not sigs:
            print(f"failure_signature_detector: no signals in {args.events}")
            return 0
        print(f"failure_signature_detector: {len(sigs)} signal(s)")
        for s in sigs:
            print(f"  [{s.category}] {s.signature}: {s.context}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
