"""Test-only literary EVALUATION MATRIX — the honest, checkable
capability ledger.

Every capability is classified into exactly one tier:

* **A — deterministic**: machine-decidable, reproducible, and NEGATIVE-tested (break
  the implementation and a test goes red). Its ``evidence`` names the test file.
* **B — fake-backend integration**: a runtime consumption chain proven with a fake
  backend / subprocess (the contract is enforced at the real stage gate, not only
  in a helper unit test). ``evidence`` names the runtime test file.
* **C — live-model**: requires model judgement (aesthetics, literariness). It is
  NEVER mechanized or given a fake numeric score. ``evidence`` explains why it is live.
* **GAP — not implemented**: a capability we deliberately did NOT mechanize and do
  NOT claim. ``evidence`` says so plainly.

The point of this module is that the classification is CHECKABLE, not decorative:
:mod:`tests.test_literary_eval_matrix` asserts every A/B capability's evidence file
exists, that no live/gap concept is smuggled into tier A, and that the gaps are
documented. The full regression proves the referenced tests actually pass.
"""
from __future__ import annotations

from dataclasses import dataclass

TIERS = ("A", "B", "C", "GAP")

#: Terms that name a LIVE-MODEL or GAP capability — they must never appear in a
#: tier-A row (the anti-fake-green invariant).
_LIVE_ONLY_TERMS = (
    "viewpoint", "tense drift", "literariness", "aesthetic", "conception",
    "quality score", "imagery quality", "beauty",
)


@dataclass(frozen=True)
class Capability:
    name: str
    tier: str
    domain: str
    evidence: str


CAPABILITIES: tuple[Capability, ...] = (
    # ---- A: deterministic, negative-tested -------------------------------- #
    Capability("classical prosody: rhyme/meter/hard-fault via 平水韵", "A",
               "classical_poetry", "tests/test_classical_poetry_prosody.py"),
    Capability("modern-verse hard constraints (language/line-count/banned)", "A",
               "modern_poetry", "tests/test_modern_poetry_form.py"),
    Capability("prose_state structure + hard constraints", "A",
               "prose", "tests/test_prose_structure.py"),
    Capability("edit discipline (mode + must-keep)", "A",
               "literary_editor", "tests/test_literary_editor_ops.py"),
    Capability("story_state patch atomicity (mid-apply rollback)", "A",
               "fiction_writing", "tests/test_fiction_writing_state_atomicity.py"),
    Capability("task envelope contract", "A",
               "shared", "tests/test_literary_task_envelope.py"),
    Capability("review contract (findings/verdict/plan coverage)", "A",
               "shared", "tests/test_literary_review_contract.py"),
    Capability("artifact manifest + lineage", "A",
               "shared", "tests/test_literary_artifact_manifest.py"),
    Capability("source registry rights + provenance ledger", "A",
               "shared", "tests/test_literary_source_registry.py"),
    Capability("shared stage-protocol conformance", "A",
               "shared", "tests/test_literary_stage_protocol.py"),
    Capability("genre profiles: validity + distinct rubric", "A",
               "fiction_writing", "tests/test_fiction_writing_profiles.py"),
    # ---- B: fake-backend runtime integration ------------------------------ #
    Capability("review -> revise enforced at the runtime stage gate", "B",
               "fiction_writing", "tests/test_fiction_writing_revise_runtime.py"),
    Capability("mandatory provenance ledger at runtime", "B",
               "fiction_writing", "tests/test_fiction_writing_sources.py"),
    Capability("prosody gate enforced at the runtime stage", "B",
               "classical_poetry", "tests/test_classical_poetry_runtime.py"),
    Capability("edit-discipline gate enforced at the runtime stage", "B",
               "literary_editor", "tests/test_literary_editor_runtime.py"),
    # ---- C: live-model, never mechanized ---------------------------------- #
    Capability("conception / imagery / diction (poetry craft)", "C",
               "classical_poetry+modern_poetry", "live-reviewer; never scored"),
    Capability("fact/memory boundary + fabrication guard", "C",
               "prose", "live-reviewer judgement"),
    Capability("edit quality / fact fidelity", "C",
               "literary_editor", "live-reviewer judgement"),
    Capability("character complexity / thematic depth", "C",
               "fiction_writing", "live-reviewer judgement"),
    # ---- GAP: not mechanized, not claimed --------------------------------- #
    Capability("viewpoint / tense drift detection", "GAP",
               "fiction_writing", "documented gap — heuristic only, not a machine check"),
    Capability("real corpus ingestion / author-style learning", "GAP",
               "shared", "sources registered but NOT ingested; no style learning yet"),
)


def by_tier() -> dict[str, list[Capability]]:
    return {t: [c for c in CAPABILITIES if c.tier == t] for t in TIERS}


def gaps() -> list[Capability]:
    return [c for c in CAPABILITIES if c.tier == "GAP"]


def render_matrix() -> str:
    lines = ["Literary Platform v0 — Capability Matrix", "=" * 42]
    labels = {"A": "A · 机检 (deterministic)", "B": "B · fake-backend integration",
              "C": "C · live-model", "GAP": "GAP · not implemented"}
    grouped = by_tier()
    for t in TIERS:
        lines.append(f"\n[{labels[t]}]  ({len(grouped[t])})")
        for c in grouped[t]:
            lines.append(f"  - {c.name}  [{c.domain}]")
            lines.append(f"      → {c.evidence}")
    return "\n".join(lines)


__all__ = [
    "TIERS", "Capability", "CAPABILITIES",
    "by_tier", "gaps", "render_matrix",
]
