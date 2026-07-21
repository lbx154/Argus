"""CapabilityRegistry — the single interface research gates use to read physics
research capabilities.

Layers, in increasing precedence (later augments/overrides earlier by
``capability_id``; the in-source base is ALWAYS present so gates run standalone):

1. **in-source base** — version-controlled JSON under
   ``argus_skill/verticals/physics/capabilities/*.json``. Guarantees the gates work
   in tests/CI with no external library.
2. **external distilled library** — READ-ONLY. The path comes from
   ``ARGUS_SKILL_PHYSICS_CAPABILITY_LIB`` (primary), else a best-effort relative
   discovery of a sibling ``PHYSICS_CAPABILITY_TRACE_V2.json`` /
   ``PHYSICS_CAPABILITY_SYNTHESIS_FROM_223.json``. **No absolute path literals live
   in this source.** A missing file → base only; a MALFORMED file → fail-open: keep
   the base, record a diagnostic (never crash, never silently swallow).
3. **per-project overlay** — optional ``research/CAPABILITY_OVERLAY.json`` for a run.

Provenance is preserved on every capability: ``source_path``, ``source_layer``,
``paper_evidence_refs`` and ``version``. The registry NEVER writes the external
library; new distilled capabilities are added by dropping base/overlay JSON or by
pointing the env var at a new external file — existing gates depend only on this
interface, never a path.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

#: Environment variable naming the external distilled capability library.
ENV_EXTERNAL_LIB = "ARGUS_SKILL_PHYSICS_CAPABILITY_LIB"

#: Distilled-library capability families -> the gate(s) that consume them.
FAMILY_GATES: dict[str, tuple[str, ...]] = {
    "I": ("literature",),   # Literature synthesis & positioning
    "D": ("theory",),       # Equation & model construction
    "E": ("theory",),       # Dimensional & scale reasoning
    "F": ("numerical",),    # Numerical research design
    "G": ("numerical",),    # Data & experimental analysis
    "L": ("novelty",),      # Reviewer hard gates
}

#: File names the best-effort discovery looks for (external distilled library).
_EXTERNAL_FILE_NAMES = (
    "PHYSICS_CAPABILITY_TRACE_V3.json",
    "PHYSICS_CAPABILITY_TRACE_V2.json",
    "PHYSICS_CAPABILITY_SYNTHESIS_FROM_223.json",
)

#: Gate names a V3 record's ``capability_type``/``family`` may name directly.
_V3_GATE_TYPES = frozenset({"literature", "theory", "numerical", "novelty"})

_UNSET = object()


@dataclass
class Capability:
    capability_id: str
    name: str = ""
    family: str = ""
    group: str = ""
    domains: tuple[str, ...] = ()
    is_generic: bool = True
    gates: tuple[str, ...] = ()
    applicability: str = ""
    basic_criteria: str = ""
    advanced_criteria: str = ""
    hard_fail: tuple[str, ...] = ()
    metric: str = ""
    pass_threshold: str = ""
    paper_evidence_refs: tuple[str, ...] = ()
    version: int = 1
    # provenance
    source_path: str = ""
    source_layer: str = "base"  # base | external | overlay

    def applies_to_gate(self, gate_id: str) -> bool:
        if gate_id in self.gates:
            return True
        return gate_id in FAMILY_GATES.get(self.family, ())


def _base_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "verticals" / "physics" / "capabilities"


def _repo_root() -> Path:
    # .../argus_skill/skills/capability_registry.py -> parents[2] == repo root
    return Path(__file__).resolve().parents[2]


def _discover_external() -> str | None:
    """Best-effort RELATIVE discovery of an external distilled library.

    Looks one level down inside the worktrees dir and the workspace root for a
    sibling capability file. No absolute path literals; returns ``None`` if none
    is found. Callers that must be hermetic (tests) pass ``external_path``
    explicitly or set the env var so discovery is never used.
    """
    repo = _repo_root()
    search_roots: list[Path] = []
    parent = repo.parent
    search_roots.append(parent)                 # e.g. .../worktrees
    if parent.name == "worktrees":
        search_roots.append(parent.parent)      # workspace root
    for base in search_roots:
        try:
            children = [c for c in base.iterdir() if c.is_dir()]
        except OSError:
            continue
        for child in children:
            for name in _EXTERNAL_FILE_NAMES:
                cand = child / name
                if cand.is_file():
                    return str(cand)
    return None


class CapabilityRegistry:
    def __init__(
        self,
        project_root: object = None,
        *,
        external_path: object = _UNSET,
        base_dir: Path | None = None,
    ) -> None:
        self._project_root = Path(str(project_root)) if project_root else None
        self._base_dir = base_dir or _base_dir()
        if external_path is _UNSET:
            self._external_path = self._resolve_external()
        else:
            # explicit override (str path, or None to disable). Hermetic for tests.
            self._external_path = str(external_path) if external_path else None
        self._caps: dict[str, Capability] = {}
        self._diagnostics: list[str] = []
        self._sources: list[str] = []
        self._loaded = False

    # ---- external path resolution (env > discovery) ---------------------- #
    @staticmethod
    def _resolve_external() -> str | None:
        env = os.environ.get(ENV_EXTERNAL_LIB)
        if env is not None:
            return env.strip() or None  # empty string explicitly disables external
        return _discover_external()

    # ---- loading --------------------------------------------------------- #
    def load(self) -> "CapabilityRegistry":
        if self._loaded:
            return self
        self._load_base()
        self._load_external()
        self._load_overlay()
        self._loaded = True
        return self

    def _add(self, cap: Capability) -> None:
        self._caps[cap.capability_id] = cap  # later layer overrides same id

    def _load_base(self) -> None:
        if not self._base_dir.is_dir():
            self._diagnostics.append(f"base capability dir missing: {self._base_dir}")
            return
        for path in sorted(self._base_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                self._diagnostics.append(f"base file unreadable {path.name}: {exc}")
                continue
            for rec in data.get("capabilities", []) if isinstance(data, dict) else []:
                cap = self._from_base_record(rec, str(path))
                if cap:
                    self._add(cap)
            self._sources.append(f"base:{path.name}")

    def _load_external(self) -> None:
        p = self._external_path
        if not p:
            return
        path = Path(p)
        if not path.is_file():
            self._diagnostics.append(f"external capability library not found: {p} (using base only)")
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # MALFORMED external library -> fail-open with a clear diagnostic.
            self._diagnostics.append(
                f"external capability library malformed, ignored (base kept): {p}: {exc}"
            )
            return
        records = data.get("capabilities") if isinstance(data, dict) else data
        if not isinstance(records, list):
            self._diagnostics.append(f"external capability library has no capabilities[] list: {p}")
            return
        normalize = self._pick_external_normalizer(data, records)
        n = 0
        for rec in records:
            cap = normalize(rec, str(path))
            if cap:
                self._add(cap)
                n += 1
        schema = str(data.get("schema", "")) if isinstance(data, dict) else ""
        tag = "v3" if normalize is self._from_v3_record else (schema or "distilled")
        self._sources.append(f"external:{path.name}[{tag}]({n})")

    @staticmethod
    def _pick_external_normalizer(data: object, records: list):
        """Choose the schema adapter for an external library.

        V3 (``physics_capability_trace_v3``) records carry ``capability_type`` naming
        the gate directly (theory/numerical/novelty); the older TRACE_V2/SYNTHESIS
        records carry a lettered ``capability_family``. Detection is by the top-level
        ``schema`` string, with a per-record fallback so a schema-less V3 export is
        still routed correctly. Never mutates the B-side library.
        """
        schema = str(data.get("schema", "")).lower() if isinstance(data, dict) else ""
        if schema.startswith("physics_capability_trace_v3"):
            return CapabilityRegistry._from_v3_record
        first = next((r for r in records if isinstance(r, dict)), None)
        if first is not None and not first.get("capability_family"):
            ctype = str(first.get("capability_type", "")).strip().lower()
            if ctype in _V3_GATE_TYPES or str(first.get("family", "")).strip().lower() in _V3_GATE_TYPES:
                return CapabilityRegistry._from_v3_record
        return CapabilityRegistry._from_external_record

    def _load_overlay(self) -> None:
        if not self._project_root:
            return
        path = self._project_root / "research" / "CAPABILITY_OVERLAY.json"
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self._diagnostics.append(f"project capability overlay malformed, ignored: {path}: {exc}")
            return
        for rec in data.get("capabilities", []) if isinstance(data, dict) else []:
            cap = self._from_base_record(rec, str(path), layer="overlay")
            if cap:
                self._add(cap)
        self._sources.append("overlay:CAPABILITY_OVERLAY.json")

    # ---- record normalisation ------------------------------------------- #
    @staticmethod
    def _from_base_record(rec: dict, path: str, *, layer: str = "base") -> Capability | None:
        if not isinstance(rec, dict) or not rec.get("capability_id"):
            return None
        # Accept both the compact base schema and the richer
        # THEORY_CAPABILITY_REQUIREMENT_SPEC schema (spec field names as fallbacks).
        def pick(*names: str) -> str:
            for n in names:
                v = rec.get(n)
                if v:
                    return v if isinstance(v, str) else str(v)
            return ""
        domain = rec.get("domains")
        if not domain and rec.get("domain"):
            domain = [rec["domain"]] if isinstance(rec["domain"], str) else rec["domain"]
        hard = rec.get("hard_fail") or rec.get("failure_codes") or []
        return Capability(
            capability_id=str(rec["capability_id"]),
            name=str(rec.get("capability_name", rec.get("name", ""))),
            family=str(rec.get("family", "")),
            group=str(rec.get("group", "")),
            domains=tuple(domain or []),
            is_generic=bool(rec.get("is_generic", True)),
            gates=tuple(rec.get("gates", []) or []),
            applicability=pick("applicability", "applicability_question"),
            basic_criteria=pick("basic_criteria", "basic_standard"),
            advanced_criteria=pick("advanced_criteria", "advanced_standard"),
            hard_fail=tuple(hard if isinstance(hard, list) else [hard]),
            metric=str(rec.get("metric", "")),
            pass_threshold=pick("pass_threshold", "publishable_standard"),
            paper_evidence_refs=tuple(rec.get("paper_evidence_refs", []) or []),
            version=int(rec.get("version", 1) or 1),
            source_path=path,
            source_layer=layer,
        )

    @staticmethod
    def _from_external_record(rec: dict, path: str) -> Capability | None:
        """Normalise a distilled-library (TRACE_V2 / SYNTHESIS) record -> Capability."""
        if not isinstance(rec, dict) or not rec.get("capability_id"):
            return None
        actions = rec.get("extracted_research_actions") or []
        basic = actions[0] if isinstance(actions, list) and actions else str(rec.get("metric", ""))
        evidence = rec.get("full_text_supporting_refs") or rec.get("source_refs") or []
        # the distilled library's family is like "I. Literature synthesis ..." — keep
        # the leading code (A..L) in `family`, the full label in `group`.
        fam_raw = str(rec.get("capability_family", ""))
        fam_m = re.match(r"\s*([A-Za-z0-9]+)", fam_raw)
        return Capability(
            capability_id=str(rec["capability_id"]),
            name=str(rec.get("capability_name", "")),
            family=fam_m.group(1) if fam_m else fam_raw,
            group=str(rec.get("capability_group", "")) or fam_raw,
            domains=tuple(rec.get("source_domains", []) or []),
            is_generic=True,
            gates=(),  # inferred from family via applies_to_gate
            applicability=str(rec.get("why_generalizable", "")),
            basic_criteria=str(basic),
            advanced_criteria="",
            hard_fail=tuple(rec.get("hard_fail_conditions", []) or []),
            metric=str(rec.get("metric") or ""),
            pass_threshold=str(rec.get("pass_threshold") or ""),
            paper_evidence_refs=tuple(str(r) for r in evidence),
            version=int(rec.get("version", 1) or 1),
            source_path=path,
            source_layer="external",
        )

    @staticmethod
    def _from_v3_record(rec: dict, path: str) -> Capability | None:
        """Normalise a ``physics_capability_trace_v3`` record -> Capability.

        V3 declares the consuming gate directly via ``capability_type`` (==``family``,
        one of theory/numerical/novelty), so ``gates`` is set explicitly and routing
        does NOT depend on the lettered ``FAMILY_GATES`` map. Domain routing uses
        ``domain_gate`` (``*`` == generic). Read-only: no field of ``rec`` is mutated.
        """
        if not isinstance(rec, dict) or not rec.get("capability_id"):
            return None
        ctype = str(rec.get("capability_type") or rec.get("family") or "").strip().lower()
        gates = (ctype,) if ctype in _V3_GATE_TYPES else ()

        domain_gate = str(rec.get("domain_gate", "") or "").strip()
        domain = str(rec.get("domain", "") or "").strip()
        domains: list[str] = []
        for d in (domain_gate, domain):
            if d and d not in domains:
                domains.append(d)

        # paper evidence: V3 `source_evidence` is a list of {source_id,title,url_or_doi,...}
        evidence: list[str] = []
        for e in rec.get("source_evidence") or []:
            if isinstance(e, dict):
                ref = e.get("source_id") or e.get("url_or_doi") or e.get("title")
                if ref:
                    evidence.append(str(ref))
            elif e:
                evidence.append(str(e))

        # hard-fail signals: prefer the plain indicator list, else the failure_codes.
        hard = rec.get("hard_fail_indicators")
        if not hard:
            hard = [c.get("code") for c in (rec.get("failure_codes") or [])
                    if isinstance(c, dict) and c.get("severity") == "hard_fail" and c.get("code")]

        return Capability(
            capability_id=str(rec["capability_id"]),
            name=str(rec.get("capability_name", "")),
            family=ctype,
            group=str(rec.get("domain_label", "") or domain_gate),
            domains=tuple(domains),
            is_generic=bool(rec.get("is_generic", False)),
            gates=gates,
            applicability=str(rec.get("applicability_question", "")),
            basic_criteria=str(rec.get("basic_standard", "")),
            advanced_criteria=str(rec.get("advanced_standard") or rec.get("strong_standard") or ""),
            hard_fail=tuple(str(h) for h in (hard if isinstance(hard, list) else [hard]) if h),
            metric=str(rec.get("minimum_artifact", "")),
            pass_threshold=str(rec.get("publishable_standard") or rec.get("strong_standard") or ""),
            paper_evidence_refs=tuple(evidence),
            version=int(rec.get("version", 1) or 1) if str(rec.get("version", 1)).isdigit() else 3,
            source_path=path,
            source_layer="external",
        )

    # ---- query interface ------------------------------------------------- #
    def all(self) -> list[Capability]:
        self.load()
        return list(self._caps.values())

    def get(self, capability_id: str) -> Capability | None:
        self.load()
        return self._caps.get(capability_id)

    def by_family(self, family: str) -> list[Capability]:
        return [c for c in self.all() if c.family == family]

    def by_domain(self, domain: str) -> list[Capability]:
        d = (domain or "").strip().lower()
        return [c for c in self.all()
                if "*" in c.domains or any(d in str(x).lower() for x in c.domains)]

    def for_gate(self, gate_id: str) -> list[Capability]:
        return [c for c in self.all() if c.applies_to_gate(gate_id)]

    def sources(self) -> list[str]:
        self.load()
        return list(self._sources)

    def diagnostics(self) -> list[str]:
        self.load()
        return list(self._diagnostics)


__all__ = [
    "ENV_EXTERNAL_LIB",
    "FAMILY_GATES",
    "Capability",
    "CapabilityRegistry",
]
