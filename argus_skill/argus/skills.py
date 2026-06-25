"""argus.skills — Skill library + Skill Selector.

Skills are distilled from Trace and live in a single folder. Lifecycle:
  Reviewer (CRUD) → Manager.audit (approve for use) → Agent self-selects →
  when there are many, a SkillSelector (small, NO session history) picks one.
"""
from __future__ import annotations
import json, re
from pathlib import Path
from typing import Callable, Optional
from .core import Skill


class SkillStore:
    """One folder of reusable, Trace-distilled skills. Provisional until proven."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._index = self.root / "_index.json"

    # ---- Reviewer CRUD ----
    def upsert(self, skill: Skill) -> Skill:
        (self.root / f"{skill.name}.md").write_text(skill.content, encoding="utf-8")
        idx = self._load()
        idx[skill.name] = {"family": skill.family, "provisional": skill.provisional,
                           "employed": skill.employed, "version": skill.version}
        self._index.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
        return skill

    def delete(self, name: str) -> None:
        (self.root / f"{name}.md").unlink(missing_ok=True)
        idx = self._load(); idx.pop(name, None)
        self._index.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")

    def get(self, name: str) -> Optional[Skill]:
        p = self.root / f"{name}.md"
        if not p.exists():
            return None
        m = self._load().get(name, {})
        return Skill(name=name, content=p.read_text(encoding="utf-8"),
                     family=m.get("family", ""), provisional=m.get("provisional", True),
                     employed=m.get("employed", False), version=m.get("version", 1))

    def list(self, *, employed_only: bool = False) -> list[Skill]:
        out = [self.get(n) for n in self._load()]
        out = [s for s in out if s]
        return [s for s in out if s.employed] if employed_only else out

    # ---- lifecycle ----
    def distill_from_trace(self, name: str, trace: list[str], family: str = "") -> Skill:
        """Stub: a real distiller would summarize the trace into a playbook."""
        content = f"# {name}\n\n_Distilled from {len(trace)} trace steps._\n"
        return self.upsert(Skill(name=name, content=content, family=family, provisional=True))

    def confirm(self, name: str) -> None:                # proven on a later loop
        s = self.get(name)
        if s: s.provisional = False; self.upsert(s)

    def employ(self, name: str) -> None:                 # Manager-approved for use
        s = self.get(name)
        if s: s.employed = True; self.upsert(s)

    def _load(self) -> dict:
        if self._index.exists():
            try: return json.loads(self._index.read_text(encoding="utf-8"))
            except Exception: return {}
        return {}


class SkillSelector:
    """Small, history-free chooser: given a task + the employed skills, pick one.
    No session context — just task text vs skill name/family keyword overlap (stub for
    a small model)."""

    def __init__(self, store: SkillStore, model_fn: Optional[Callable[[str], str]] = None):
        self.store = store
        self.model_fn = model_fn

    def select(self, task_text: str) -> Optional[Skill]:
        cand = self.store.list(employed_only=True)
        if not cand:
            return None
        if self.model_fn:                                # a real small model, no history
            name = (self.model_fn(task_text) or "").strip()
            return self.store.get(name)
        words = set(re.findall(r"[a-z]+", task_text.lower()))
        best, score = None, 0
        for s in cand:
            key = set(re.findall(r"[a-z]+", (s.name + " " + s.family).lower()))
            ov = len(words & key)
            if ov > score:
                best, score = s, ov
        return best or cand[0]
