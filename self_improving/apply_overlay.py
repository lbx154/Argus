#!/usr/bin/env python3
# argus self-improving build step 4 — OVERLAY EDITS with ASYMMETRIC SAFETY.
# The Meta-Critic's proposed edits route here. Add-only house-rules (can only ADD
# a directive, never weaken a guard, never touch framework source or the sealed
# verifier) are applied to the LIVE special_prompts overlay immediately. Anything
# that changes a contract / weakens a guard / edits framework source is REFUSED by
# the auto-path and queued PENDING for human/A-B promotion (step 5/6).
#
# usage:
#   apply_overlay.py propose <edit.json>      # classify + (add-only) apply or queue pending
#   apply_overlay.py rollback <slug>          # remove an applied overlay (reversible)
#   apply_overlay.py list                     # active overlays + pending queue
import json
import os
import re
import sys
import time
from pathlib import Path

META = Path.home() / ".argus-meta"
PENDING = META / "pending"
JOURNAL = META / "journal.jsonl"
SP_DIR = Path(os.environ.get("ARGUS_SKILL_SPECIAL_PROMPTS_DIR",
                             Path.home() / ".argus-skill" / "special_prompts"))
SEAL = Path.home() / ".argus-seal"
FRAMEWORK_SRC = re.compile(r"\b(reviewer|stages|runner|planner|loop|models|life_worker)\.py\b|argus_skill/")
WEAKEN = re.compile(r"\b(remove|delete|disable|lower|reduce|skip|weaken|bypass|loosen|relax|ignore|turn off|raise the threshold|stall_threshold|forward_progress\s*=|quarantine)\b", re.I)
FROZEN = re.compile(r"\b(eval_solution|analyze_sweep|seal_check|verifier|val[_ ]?loss|t-test|the gate|metric definition)\b", re.I)


def jrnl(rec):
    rec["ts"] = int(time.time())
    with open(JOURNAL, "a") as f:
        f.write(json.dumps(rec) + "\n")


def active_overlays():
    return sorted(SP_DIR.glob("3?-meta-*.md")) if SP_DIR.exists() else []


def classify(edit):
    """Return 'add_only' | 'contract_change' | 'refused'. Conservative: anything
    touching framework source or the frozen verifier, or weakening a guard, or of
    uncertain scope, is NEVER add-only."""
    blob = f"{edit.get('scope','')} {edit.get('target','')} {edit.get('text','')}"
    if FROZEN.search(blob) or "/.argus-seal" in blob:
        return "refused"                       # never auto-touch the sealed verifier
    if FRAMEWORK_SRC.search(blob) or WEAKEN.search(blob):
        return "contract_change"               # code/contract/guard edit -> pending
    if edit.get("scope") == "house_rule" and edit.get("text"):
        return "add_only"
    return "contract_change"                    # default: not provably add-only


def cmd_propose(edit_path):
    edit = json.loads(Path(edit_path).read_text())
    slug = re.sub(r"[^a-z0-9-]", "", edit.get("slug", "").lower())[:40] or "unnamed"
    kind = classify(edit)
    if kind == "refused":
        jrnl({"action": "refused", "slug": slug, "reason": "touches the sealed verifier / frozen metric"})
        print(f"REFUSED {slug}: an overlay may never touch the sealed verifier / metric.")
        return 2
    if kind == "contract_change":
        PENDING.mkdir(parents=True, exist_ok=True)
        (PENDING / f"{slug}.json").write_text(json.dumps(edit, indent=2))
        jrnl({"action": "queued_pending", "slug": slug, "target": edit.get("target", "")})
        print(f"PENDING {slug}: contract/guard/source edit — NOT auto-applied; queued for A/B promotion (step 5/6).")
        return 0
    # add_only: enforce one-in-flight, then write the live overlay
    if active_overlays():
        print(f"BLOCKED {slug}: one meta-overlay already in flight ({active_overlays()[0].name}); roll it back first.")
        return 1
    SP_DIR.mkdir(parents=True, exist_ok=True)
    path = SP_DIR / f"30-meta-{slug}.md"
    body = (f"# META house-rule (auto-applied add-only overlay) — {slug}\n\n"
            f"_Rationale: {edit.get('rationale','')}_\n\n{edit['text'].rstrip()}\n")
    path.write_text(body)
    os.chmod(path, 0o644)                        # satisfy special_prompts trust model
    jrnl({"action": "applied_add_only", "slug": slug, "path": str(path)})
    print(f"APPLIED {slug}: add-only house-rule live at {path} (reversible: rollback {slug}).")
    return 0


def cmd_rollback(slug):
    slug = re.sub(r"[^a-z0-9-]", "", slug.lower())
    path = SP_DIR / f"30-meta-{slug}.md"
    if path.exists():
        path.unlink()
        jrnl({"action": "rolled_back", "slug": slug})
        print(f"ROLLED BACK {slug}.")
        return 0
    print(f"no active overlay {slug}")
    return 1


def cmd_list():
    print("ACTIVE add-only overlays:")
    for p in active_overlays():
        print(f"  {p.name}")
    print("PENDING (contract changes awaiting A/B promotion):")
    if PENDING.exists():
        for p in sorted(PENDING.glob("*.json")):
            print(f"  {p.stem}")


def main():
    if len(sys.argv) < 2:
        print("usage: apply_overlay.py propose <edit.json> | rollback <slug> | list")
        sys.exit(2)
    cmd = sys.argv[1]
    if cmd == "propose":
        sys.exit(cmd_propose(sys.argv[2]))
    if cmd == "rollback":
        sys.exit(cmd_rollback(sys.argv[2]))
    if cmd == "list":
        cmd_list()
        sys.exit(0)
    print(f"unknown command {cmd}")
    sys.exit(2)


if __name__ == "__main__":
    main()
