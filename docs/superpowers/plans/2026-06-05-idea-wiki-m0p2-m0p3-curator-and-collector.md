# Wiki M0.2 + M0.3 — fix curator + add integrated collection mission

> **Executor:** Codex via tmux + `codex --yolo` + `/goal <this file>` in the
> existing `wiki-m0-codex` session.
> The architect (Claude) is handing this to you; he verifies.

## Why this exists

Empirical observation from 18h of unify_RL_argus running ($114 spent, 36
done + 2 failed missions, see `events.jsonl`):

1. **wiki-curator was NEVER loaded**. `Matched reviewer skill` log line
   count = 0. The skill was registered as adaptive, and the reviewer's
   `ReviewerMission.match(objective)` matcher rated "synthesize wiki cards"
   as irrelevant to "Root-cause Bagel RL failure". So Step 0 backfill,
   Step 1 synthesis, Step 4 promotion — none of it ran.

2. **Engineer wrote freeform notes to `sources/` root** because the schema
   only had `papers/`, `repos/`, `runs/` buckets and operational state
   notes ("stage_check terminal external blocker") fit none of them.
   These 6 orphan files are invisible to `WikiStore.iter_pages()` etc.
   (This is M0.4 work, NOT in this plan; mentioned for context.)

3. **Engineer did not naturally invoke the 4 ingestion skills** during
   internal code-analysis missions. Wiki only grew from the M0.1 backfill
   of `paper/refs.bib`. So the parasitic model is insufficient — we need
   an **active collection mission** that argus's own planner schedules
   when the project is idle.

This plan ships M0.2 (curator always-loaded + liberalized threshold) and
M0.3 (integrated wiki-collector mission). It is one combined deliverable
because they share the same end goal: **wiki actually grows during normal
argus operation**.

---

## File structure

**New files:**
| Path | Purpose |
|---|---|
| `argus_skill/builtin_skills/engineer/wiki-collector.md` | Engineer skill: autonomously derive 5-10 queries from project state, run arxiv+semantic-scholar+github searches, write to sources/* |
| `argus_skill/wiki/bot_state.py` | Persistent state: last_collected timestamp + cooldown logic |
| `tests/test_wiki_bot_state.py` | Cooldown + persistence tests |
| `tests/test_wiki_curator_fixed_loading.py` | Confirm reviewer.py always loads wiki-curator when wiki exists |
| `tests/test_planner_wiki_collect_enqueue.py` | Planner enqueues wiki_collect when conditions met |

**Modified files:**
| Path | Change |
|---|---|
| `argus_skill/engineer/reviewer.py` | Add `_load_wiki_curator_skill()` alongside the other 3 fixed reviewer skills; load only when `.autors/<project>/wiki/` exists |
| `argus_skill/builtin_skills/reviewer/wiki-curator.md` | Loosen Step 2 — every new source gets a scratch page; only candidate/stable require real judgment |
| `argus_skill/builtin_skills/engineer/argus-engineer-role.md` | Add "consult the wiki query_pack and queries/ early in any non-trivial mission" instruction |
| `argus_skill/planner/planner.py` | When `_build_planner_prompt` detects (a) wiki exists, (b) backlog empty / has been empty for ≥10 min via telemetry, (c) bot_state cooldown elapsed → inject a "consider scheduling a wiki_collect mission" suggestion into planner prompt (planner still decides; harness does not schedule) |

---

## M0.2 — wiki-curator fixes

### Task 1: F1 — make wiki-curator a fixed reviewer skill (loaded when wiki exists)

**Files:**
- Modify: `argus_skill/engineer/reviewer.py`
- Create: `tests/test_wiki_curator_fixed_loading.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_wiki_curator_fixed_loading.py`:
```python
from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.engineer.reviewer import _load_wiki_curator_skill_if_present


def test_returns_skill_text_when_wiki_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".autors" / "demo" / "wiki" / "queries").mkdir(parents=True)
    (tmp_path / ".autors" / "demo" / "wiki" / "query_pack.md").write_text("# pack")
    text = _load_wiki_curator_skill_if_present()
    assert text is not None
    assert "wiki-curator" in text.lower() or "Wiki Curator" in text


def test_returns_none_when_no_wiki(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    text = _load_wiki_curator_skill_if_present()
    assert text is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_wiki_curator_fixed_loading.py -v
```
Expected: ImportError on the helper.

- [ ] **Step 3: Implement**

In `argus_skill/engineer/reviewer.py`, find the existing pattern that
loads `_REVIEWER_ROLE_SKILL` and `_REVIEWER_ENGINEER_HANDOFF_SKILL` (look
for `_load_reviewer_engineer_handoff_skill`). Add a sibling:

```python
_WIKI_CURATOR_SKILL = "wiki-curator.md"


def _load_wiki_curator_skill_if_present() -> str | None:
    """Return the wiki-curator skill text when the project has a wiki.

    The wiki-curator MUST run on every mission close when a wiki exists,
    because the adaptive ReviewerMission matcher does not reliably
    surface it for diagnostic / debugging objectives (verified
    empirically: 0 matches across 36 missions in
    /home/yifanyang/.argus-skill/projects/59ec632ebc50/events.jsonl).
    """
    from pathlib import Path
    autors = Path.cwd() / ".autors"
    if not autors.exists():
        return None
    # Any .autors/*/wiki/ counts as "wiki present".
    if not any((p / "wiki").is_dir() for p in autors.iterdir() if p.is_dir()):
        return None
    return load_builtin_skill_text(f"reviewer/{_WIKI_CURATOR_SKILL}")
```

Then in `Reviewer._compose_prompt` (or wherever `_load_reviewer_engineer_handoff_skill()`
is concatenated into the final prompt — locate it in this file), append
the wiki-curator block AFTER handoff but BEFORE adaptive `matched_review_skill_block`:

```python
wiki_curator_text = _load_wiki_curator_skill_if_present()
if wiki_curator_text:
    prompt_parts.append(
        "## Wiki curator (fixed when a wiki exists — run as part of this verdict)\n\n"
        f"{wiki_curator_text}\n\n"
    )
```

Adjust to the exact prompt-assembly pattern already used by the
neighbors. The key invariant: when `.autors/<x>/wiki/` exists, this
skill text MUST appear in every reviewer prompt, independent of objective.

- [ ] **Step 4: Run tests until green**

```bash
pytest tests/test_wiki_curator_fixed_loading.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add argus_skill/engineer/reviewer.py tests/test_wiki_curator_fixed_loading.py
git commit -m "wiki: load wiki-curator as fixed reviewer skill when a wiki exists"
```

---

### Task 2: F2 — loosen curator Step 2 threshold

**Files:**
- Modify: `argus_skill/builtin_skills/reviewer/wiki-curator.md`

- [ ] **Step 1: Replace Step 2 entirely**

In `argus_skill/builtin_skills/reviewer/wiki-curator.md`, find the section
`### Step 2 — decide whether judgment is needed`. Replace EVERYTHING from
that heading up to (but NOT including) the next heading `### Step 3 — write or update pages`
with:

```markdown
### Step 2 — mechanical scratch lift + selective candidate promotion

The wiki is NOT a journal, but the scratch tier exists exactly so the
wiki can grow without overcommitting to judgment. Be liberal with
scratch creation; conservative with candidate/stable promotion.

**Mechanical (always do this)**:

For each NEWLY added source this mission (from Step 1):

- `sources/papers/<key>.md` → create or refresh
  `pages/techniques/<key>.md` with:
    - `status: scratch`
    - `title`: copied from the source title
    - `sources: ["papers/<key>.md"]`
    - `tags`: best-effort 1-3 tags from controlled vocab
      (`.autors/<project>/wiki/data/tags.yaml`); empty list if unclear
    - `reviewer_note`: the `relevance:` line from the source body
      (M0.1 ingest_lit_matrix appends it), or empty
    - `confidence: low`
    - `created_at: <today>`, `last_reviewed_at: <today>`
- `sources/runs/<run-id>.md` with `outcome=failure` and a non-empty
  `failure_signature` that matches the signature of a prior run in
  this project → create or refresh `pages/patterns/<signature>.md`
  with `status: scratch`, `related_runs` listing both run IDs.

If a scratch page for this source already exists, leave it alone
(scratch is the agent's first guess; don't overwrite it mechanically
on every mission).

**Judgment-required (do only when justified)**:

- `scratch → candidate` promotion: this mission found additional
  evidence (a second source supporting the same technique, a run that
  exercises it, etc). Write the `Why now?` reasoning into
  `reviewer_note` so a future reviewer can re-evaluate.
- `candidate → stable` promotion: ≥2 independent sources OR ≥1 run
  with measurable benefit; reviewer is willing to certify that the
  planner may act on this card.
- `pages/conflicts/<slug>.md` creation: this mission encountered two
  sources whose claims are inverted on the same variable (e.g.
  engineer's `WIKI-HANDOFF: conflict candidate` note).
- Demotion (candidate → scratch, stable → candidate): new evidence
  undermines the card.

If none of the judgment cases apply this mission, do not force a
candidate/stable; the mechanical scratch lift above is enough.
```

(The existing Step 3 / Step 4 / etc. continue unchanged.)

- [ ] **Step 2: Commit**

```bash
git add argus_skill/builtin_skills/reviewer/wiki-curator.md
git commit -m "wiki: curator step 2 — mechanical scratch lift for every new source"
```

---

### Task 3: Engineer role — instruct to consult the wiki early

**Files:**
- Modify: `argus_skill/builtin_skills/engineer/argus-engineer-role.md`

- [ ] **Step 1: Append a "Wiki consultation" section**

Find the existing "Mission-close RunCard" section at the bottom of
`argus-engineer-role.md` (added in M0 Task 11). Insert a NEW section
just ABOVE it:

```markdown
## Consult the project wiki before non-trivial work

If `.autors/<project>/wiki/` exists, BEFORE doing any non-trivial work,
read these files (they are short):

- `.autors/<project>/wiki/query_pack.md` — entry-point summary
- `.autors/<project>/wiki/queries/by-status.md` — what's already known
- `.autors/<project>/wiki/queries/by-tag.md` — find related techniques
- `.autors/<project>/wiki/queries/open-contradictions.md` — known
  unresolved disagreements
- `.autors/<project>/wiki/queries/stale-watchlist.md` — what hasn't
  been revisited in a while

The wiki is the project's accumulated memory of techniques worth
watching, contradictions noticed across sources, and cross-mission
patterns. If a technique-to-watch card is directly relevant to your
mission, cite it in your output (`see pages/techniques/<id>.md`).

If your mission ends up discovering a new technique / conflict /
pattern, drop a one-paragraph note for the reviewer in your final
summary (the reviewer's wiki-curator will turn it into a page).
```

- [ ] **Step 2: Commit**

```bash
git add argus_skill/builtin_skills/engineer/argus-engineer-role.md
git commit -m "wiki: engineer role consults wiki early; flags new findings for curator"
```

---

## M0.3 — integrated wiki-collector mission

### Task 4: bot_state.py — cooldown tracking (TDD)

**Files:**
- Create: `argus_skill/wiki/bot_state.py`
- Create: `tests/test_wiki_bot_state.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_wiki_bot_state.py`:
```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from argus_skill.wiki.bot_state import (
    BotState,
    cooldown_elapsed,
    load_bot_state,
    save_bot_state,
)


def test_load_returns_default_when_file_missing(tmp_path: Path):
    state = load_bot_state(tmp_path / "bot_state.json")
    assert state.last_collected_at is None
    assert state.last_query_seed is None
    assert state.consecutive_failures == 0


def test_save_and_load_roundtrip(tmp_path: Path):
    path = tmp_path / "bot_state.json"
    now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
    save_bot_state(path, BotState(
        last_collected_at=now,
        last_query_seed="grpo,visual editing,reward hacking",
        consecutive_failures=2,
    ))
    state = load_bot_state(path)
    assert state.last_collected_at == now
    assert state.last_query_seed == "grpo,visual editing,reward hacking"
    assert state.consecutive_failures == 2


def test_cooldown_elapsed_false_when_recent():
    now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
    last = now - timedelta(hours=6)
    assert cooldown_elapsed(last_collected_at=last, now=now, hours=12) is False


def test_cooldown_elapsed_true_when_old():
    now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
    last = now - timedelta(hours=24)
    assert cooldown_elapsed(last_collected_at=last, now=now, hours=12) is True


def test_cooldown_elapsed_true_when_never_collected():
    now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
    assert cooldown_elapsed(last_collected_at=None, now=now, hours=12) is True
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_wiki_bot_state.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement**

Create `argus_skill/wiki/bot_state.py`:
```python
"""Persistent state for the wiki-collector cooldown.

Lives at .autors/<project>/wiki/data/bot_state.json. Tiny JSON file:
no migrations, no schema enforcement beyond what dataclass field names
provide.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class BotState:
    last_collected_at: datetime | None = None
    last_query_seed: str | None = None
    consecutive_failures: int = 0
    notes: str = ""


def load_bot_state(path: Path) -> BotState:
    if not path.exists():
        return BotState()
    data = json.loads(path.read_text(encoding="utf-8"))
    lc = data.get("last_collected_at")
    return BotState(
        last_collected_at=(
            datetime.fromisoformat(lc).astimezone(timezone.utc) if lc else None
        ),
        last_query_seed=data.get("last_query_seed"),
        consecutive_failures=int(data.get("consecutive_failures", 0)),
        notes=data.get("notes", ""),
    )


def save_bot_state(path: Path, state: BotState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(state)
    if state.last_collected_at is not None:
        data["last_collected_at"] = state.last_collected_at.astimezone(
            timezone.utc
        ).isoformat()
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def cooldown_elapsed(
    *, last_collected_at: datetime | None, now: datetime, hours: float
) -> bool:
    if last_collected_at is None:
        return True
    return (now - last_collected_at).total_seconds() >= hours * 3600
```

- [ ] **Step 4: Run tests until green**

```bash
pytest tests/test_wiki_bot_state.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add argus_skill/wiki/bot_state.py tests/test_wiki_bot_state.py
git commit -m "wiki: bot_state cooldown tracking for integrated collector"
```

---

### Task 5: wiki-collector.md engineer skill

**Files:**
- Create: `argus_skill/builtin_skills/engineer/wiki-collector.md`

- [ ] **Step 1: Write the skill**

Create `argus_skill/builtin_skills/engineer/wiki-collector.md`:
```markdown
---
name: Wiki Collector
description: Autonomously refresh the project wiki by deriving 5-10 search queries from project state, running them against arxiv/semantic-scholar/github, and writing new findings to sources/papers/ and sources/repos/. Run only when planner has explicitly scheduled a wiki_collect mission. Engineer-only; reviewer's wiki-curator handles promotion.
category: wiki
version: 1
scientist_model: gpt-5.5
created_at: 2026-06-05T00:00:00+00:00
---

# Wiki Collector — derive queries from project state, ingest sources

## When to invoke

The planner schedules a `wiki_collect` mission when:
- `.autors/<project>/wiki/` exists
- Backlog has been empty for a non-trivial time
- The bot_state cooldown (default 12h) has elapsed since the last collect

Do NOT invoke this skill outside a planner-scheduled wiki_collect mission.

## Workflow

### Step 1 — derive 5-10 search queries from project state

Read (in this order, each is short):
- `project.md`
- `AGENTS.md` and any top-level `*goal*.md`
- The matched special prompts at `$ARGUS_SKILL_SPECIAL_PROMPTS_DIR` (or `~/.argus-skill/special_prompts/`)
- `research/PIPELINE_STATE.json` (if exists)
- The current `--objective` (in the mission context)
- `.autors/<project>/wiki/data/tags.yaml` for the controlled vocab
- `.autors/<project>/wiki/queries/by-tag.md` to see what's already covered

From these, derive **5-10 short search queries** that:
- Are concrete enough to return useful arxiv / repo hits (NOT
  "improve LLM reasoning"; YES "asymmetric clipping GRPO visual editing")
- Cross-product methods × failure modes you've seen in `reports/` and
  `diagnosis/`
- Tilt toward 2025-2026 work
- Avoid topics already heavily covered in `queries/by-tag.md`

Write the derived queries to mission scratch (so the reviewer can see
what you searched for).

### Step 2 — run the searches and ingest

For each query, use whichever of these tools is available — codex
native web search via `--search` if enabled, or the existing
`arxiv-paper-search` / `semantic-scholar-search` skill behavior:
- arxiv (last 18 months, ML/CL/AI categories)
- semantic-scholar (citation graph traversal from any matching paper)
- GitHub (search repos by topic, sort by stars * recency)

For each hit, write an immutable source via the wiki helpers:

```python
from datetime import date
from pathlib import Path
from argus_skill.wiki.store import WikiStore
from argus_skill.wiki.schema import SourcePaper, SourceRepo

store = WikiStore(Path(".autors/<project>/wiki"))

# Paper hit
src = SourcePaper(
    id=f"papers/{arxiv_id}",
    url=arxiv_url,
    title=paper_title,
    ingested_at=date.today(),
    ingested_by=f"wiki-collector@mission-{mission_id}",
    checksum=f"sha256:{abstract_sha256}",
    body=abstract_text,
)
try:
    store.write_source(src)
except FileExistsError:
    pass  # already ingested; sources are immutable

# Repo hit
repo_src = SourceRepo(
    id=f"repos/{owner}__{repo}",
    url=repo_url,
    title=f"{owner}/{repo} — {short_description}",
    ingested_at=date.today(),
    ingested_by=f"wiki-collector@mission-{mission_id}",
    checksum=f"sha256:{readme_sha256_or_url_hash}",
    body=readme_excerpt_or_url,  # SHORT — keep under 2 KB
)
try:
    store.write_source(repo_src)
except FileExistsError:
    pass
```

### Step 3 — update bot_state

At the end of the mission, regardless of outcome, update the cooldown
state:

```python
from datetime import datetime, timezone
from pathlib import Path
from argus_skill.wiki.bot_state import BotState, load_bot_state, save_bot_state

path = Path(".autors/<project>/wiki/data/bot_state.json")
state = load_bot_state(path)
state.last_collected_at = datetime.now(timezone.utc)
state.last_query_seed = "; ".join(queries)  # for next-time diversity
if hit_count == 0:
    state.consecutive_failures += 1
else:
    state.consecutive_failures = 0
save_bot_state(path, state)
```

### Step 4 — short reviewer-facing summary

Output a short note in your final mission summary:
- queries used
- N new papers ingested / M skipped as duplicates
- K new repos ingested
- any noteworthy hits (in 1-2 sentences each) the reviewer might want
  to consider promoting to candidate

Do NOT write any `pages/*` cards yourself — promotion is the
reviewer's wiki-curator's job (Step 2 mechanical lift will turn each
new source into a scratch page on this same mission's reviewer pass).

## Hard rules

- Stay under the per-mission token budget. If a paper's abstract is
  large, truncate to ~2 KB before storing in source body.
- Do NOT fabricate arxiv IDs or URLs. If a search returns no results,
  record that in the summary and move on.
- Sources are immutable: re-ingestion silently skips. Never overwrite.
- Cooldown: this skill should not be invoked more than once per
  12 hours by the planner.
```

- [ ] **Step 2: Commit**

```bash
git add argus_skill/builtin_skills/engineer/wiki-collector.md
git commit -m "wiki: add wiki-collector engineer skill (autonomous query + ingest)"
```

---

### Task 6: Planner enqueues wiki_collect at idle (TDD)

**Files:**
- Modify: `argus_skill/planner/planner.py` (`_build_planner_prompt`)
- Create: `tests/test_planner_wiki_collect_enqueue.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_planner_wiki_collect_enqueue.py`:
```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from argus_skill.planner.planner import Planner
from argus_skill.wiki.bot_state import BotState, save_bot_state


def _make_wiki(tmp_path: Path, project: str = "demo") -> Path:
    wiki = tmp_path / ".autors" / project / "wiki"
    (wiki / "queries").mkdir(parents=True)
    (wiki / "data").mkdir(parents=True)
    (wiki / "query_pack.md").write_text("# pack")
    return wiki


def test_planner_suggests_wiki_collect_when_cooldown_elapsed_and_backlog_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.chdir(tmp_path)
    wiki = _make_wiki(tmp_path)
    save_bot_state(
        wiki / "data" / "bot_state.json",
        BotState(last_collected_at=datetime(
            2026, 6, 4, 0, 0, tzinfo=timezone.utc
        )),
    )
    prompt = Planner._build_planner_prompt(
        continuous_objective="research X",
        journal_tail="",
        budget_remaining_usd=10.0,
        planning_cycle=0,
        runtime_change_summary="",
        mission=None,
    )
    assert "wiki_collect" in prompt
    assert "cooldown" in prompt.lower()


def test_planner_does_not_suggest_wiki_collect_when_cooldown_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.chdir(tmp_path)
    wiki = _make_wiki(tmp_path)
    # Just collected 1 hour ago — cooldown still active.
    save_bot_state(
        wiki / "data" / "bot_state.json",
        BotState(last_collected_at=datetime.now(timezone.utc) - timedelta(hours=1)),
    )
    prompt = Planner._build_planner_prompt(
        continuous_objective="research X",
        journal_tail="",
        budget_remaining_usd=10.0,
        planning_cycle=0,
        runtime_change_summary="",
        mission=None,
    )
    assert "wiki_collect" not in prompt
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_planner_wiki_collect_enqueue.py -v
```
Expected: both fail because planner doesn't yet inject the suggestion.

- [ ] **Step 3: Implement — extend the existing wiki block in `_build_planner_prompt`**

In `argus_skill/planner/planner.py`, locate the existing wiki block
(the `wiki_candidates` loop you added in M0). At the END of that block
(just before `wiki_block = "".join(parts)`), append cooldown-based
suggestion logic:

```python
            # M0.3: suggest a wiki_collect mission when cooldown has elapsed.
            # This is a SUGGESTION in the planner prompt, not a harness-enforced
            # action — the planner still decides.
            from datetime import datetime, timezone
            from ..wiki.bot_state import cooldown_elapsed, load_bot_state

            COLLECT_COOLDOWN_HOURS = 12.0
            for wiki_root in wiki_candidates:
                bot_state_path = wiki_root / "data" / "bot_state.json"
                state = load_bot_state(bot_state_path)
                if cooldown_elapsed(
                    last_collected_at=state.last_collected_at,
                    now=datetime.now(timezone.utc),
                    hours=COLLECT_COOLDOWN_HOURS,
                ):
                    parts.append(
                        f"### wiki_collect suggestion ({wiki_root.parent.name})\n"
                        f"The wiki's collector cooldown of {COLLECT_COOLDOWN_HOURS:.0f}h "
                        f"has elapsed since the last collect "
                        f"(last_collected_at={state.last_collected_at}). "
                        f"If the active backlog has space, consider enqueueing one "
                        f"`wiki_collect` mission with the `wiki-collector` engineer "
                        f"skill. It is a small, train-free background mission that "
                        f"derives 5-10 queries from project state and ingests new "
                        f"arxiv / github hits into sources/*. The reviewer's "
                        f"wiki-curator handles promotion on the same mission's "
                        f"reviewer pass.\n"
                    )
```

- [ ] **Step 4: Run tests until green**

```bash
pytest tests/test_planner_wiki_collect_enqueue.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add argus_skill/planner/planner.py tests/test_planner_wiki_collect_enqueue.py
git commit -m "wiki: planner suggests wiki_collect when cooldown elapsed and wiki present"
```

---

### Task 7: Full wiki suite green

```bash
pytest tests/test_wiki_*.py tests/test_planner_wiki_collect_enqueue.py tests/test_wiki_curator_fixed_loading.py -v
```
Expected: all pass. The M0 + M0.1 + M0.2 + M0.3 tests together should be
around 35-40. No regressions allowed.

If anything regresses, fix and re-commit. Don't move on red.

---

### Task 8: Restart argus on unify_RL_argus with the same objective

Now that wiki-curator always loads AND planner can schedule wiki_collect,
restarting argus should:
- Make wiki-curator run on every mission close (visible in events.jsonl as
  `Matched reviewer skill` for `wiki-curator` OR as a `wiki curator (fixed)`
  block in the reviewer prompt)
- Create scratch pages for each of the 15 existing sources/papers/ files
  (M0.1 backfill) AND any new sources from this run
- Optionally schedule a wiki_collect mission once backlog goes empty

- [ ] **Step 1: Confirm previous daemon stopped**

```bash
cd /data/yijia/unify_RL_argus
ARGUS_SKILL_SPECIAL_PROMPTS_DIR=$PWD/.argus_special_prompts argus-skill --status | grep daemon
```
Expected: `daemon : not running`.

- [ ] **Step 2: Restart with the same objective**

```bash
OBJECTIVE='Root-cause where the current Bagel RL training pipeline is failing: is the regression in (a) SFT initialization, (b) data construction (training set / prompts / reward labels), or (c) RL itself (reward shaping / GRPO dynamics / policy update)? Use reports/ (especially the *_sft_rl_*, *_sft_vs_*, reward_* subdirs), extracted_remote_results/, and the code in src/ and unify_rl/src/. Produce a written diagnosis with: (1) most likely root cause, (2) supporting evidence with specific report paths + numbers, (3) a falsifiable 1-2 step verification experiment for each hypothesis, (4) ranked recommendations for the next training run. Train-free: no GPU needed.'

tmux send-keys -t argus-unify-rl 'cd /data/yijia/unify_RL_argus' Enter
tmux send-keys -t argus-unify-rl "ARGUS_SKILL_SPECIAL_PROMPTS_DIR=/data/yijia/unify_RL_argus/.argus_special_prompts argus-skill --daemon-fg --continuous --bounded --objective \"$OBJECTIVE\"" Enter
```

- [ ] **Step 3: Verify**

Wait 30 seconds, then:
```bash
sleep 30
tmux capture-pane -t argus-unify-rl -p | tail -20
ls /data/yijia/unify_RL_argus/.autors/unify_RL_argus/wiki/sources/papers/ | wc -l
# expect: 15 (carried over from M0.1)
ls /data/yijia/unify_RL_argus/.autors/unify_RL_argus/wiki/pages/techniques/ 2>/dev/null | wc -l
# expect: 0 initially — will become ~15 after first reviewer pass with the new fixed wiki-curator
```

- [ ] **Step 4: Report back**

When done, print a short summary to stdout:
- Full test count and pass status
- Did Task 8's argus restart produce a `daemon: ready` log?
- (Optional) After ~3-5 minutes if you want to wait, did `pages/techniques/`
  start filling? If not, do not block on this — the architect monitors that.

Do not babysit beyond Task 8 Step 3. The architect takes over.

---

## Definition of done

- `tests/test_wiki_curator_fixed_loading.py` passes (2 tests)
- `tests/test_wiki_bot_state.py` passes (5 tests)
- `tests/test_planner_wiki_collect_enqueue.py` passes (2 tests)
- Full wiki test sweep passes — no regressions
- `wiki-curator.md` Step 2 says "mechanical scratch lift" not "silence is correct"
- `argus-engineer-role.md` has a "Consult the project wiki" section
- `wiki-collector.md` engineer skill exists under `builtin_skills/engineer/`
- `planner.py` injects wiki_collect suggestion when cooldown elapsed
- Argus is restarted on unify_RL_argus, daemon ready

## Non-goals (M0.4+ work, do NOT do)

- Source type "note" for orphan sources/*.md files (engineer drops at root) — fix in M0.4
- LIT_MATRIX-driven auto-technique-card creation at ingest time — covered by Step 2 mechanical lift now
- Cross-project pattern detection (M1)
- Bot rate limiting or independent budget — user chose shared budget
- bot_config.yaml — cooldown is hard-coded to 12h; tune later if needed
