# Idea Wiki M0.1 — bib + LIT_MATRIX ingest (option C) + argus restart

> **Executor:** Codex via tmux + `codex --yolo` + `/goal <this file>`.
> The architect (Claude) is the one giving you this goal; he verifies.
> You implement and run experiments + restart argus at the end.

**Goal:** Close the wiki side-effect gap surfaced in the first unify_RL_argus
mission. Argus's engineer produced `paper/refs.bib` (15 entries) and
`research/LIT_MATRIX.tsv` via codex's native web search, completely bypassing
the 4 named ingestion skills (`paper-ingestion`, `arxiv-paper-search`,
`semantic-scholar-search`, `novelty-check`) that have the wiki-write hook.
Result: `sources/papers/` stayed at 0 despite 15 papers actually consulted.

**Fix:** add a reviewer-side ingestion path that, at curator time, reads
`paper/refs.bib` (and `research/LIT_MATRIX.tsv` if present) and converts each
entry into an immutable `sources/papers/*.md` card. The reviewer's existing
`wiki-curator.md` skill is told to do this as step 0 of its pass.

**Architecture:** new helper `argus_skill/wiki/ingest.py` with two pure
functions (`ingest_refs_bib`, `ingest_lit_matrix`). A new CLI subcommand
`argus-skill wiki ingest` lets the operator backfill an existing wiki
without waiting for a mission. The reviewer skill instructs the agent to
call these helpers from its python execution.

**Spec reference:** `docs/IDEA_WIKI_DESIGN.md` (commit `9c9bee2`). This
plan is a M0.1 patch; the M0 spec needs no change since the existing
"reviewer is the only promoter" principle is preserved — engineer keeps
writing refs.bib via its normal flow; reviewer ingests at mission close.

**Tech stack:** Python 3.11 stdlib only for bib parsing (regex). Pytest.
PyYAML already in deps.

---

## File structure

**New files:**
| Path | Purpose |
|---|---|
| `argus_skill/wiki/ingest.py` | bib + tsv parsers + ingest functions |
| `tests/test_wiki_ingest.py` | TDD coverage |

**Modified files:**
| Path | Change |
|---|---|
| `argus_skill/wiki/__init__.py` | Re-export `ingest_refs_bib`, `ingest_lit_matrix` |
| `argus_skill/builtin_skills/reviewer/wiki-curator.md` | Add "Step 0: bib + lit_matrix backfill" section before existing Step 1 |
| `argus_skill/apps/cli.py` | Add `argus-skill wiki ingest` subcommand |

---

## Task 1: ingest.py — bib + LIT_MATRIX parsers (TDD)

**Files:**
- Create: `argus_skill/wiki/ingest.py`
- Create: `tests/test_wiki_ingest.py`

### Step 1 — write the failing tests

Create `tests/test_wiki_ingest.py`:
```python
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from argus_skill.wiki.ingest import (
    parse_bib_entries,
    ingest_refs_bib,
    ingest_lit_matrix,
)
from argus_skill.wiki.schema import SourcePaper
from argus_skill.wiki.store import WikiStore


SAMPLE_BIB = """
@article{williams1992reinforce,
  title={Simple statistical gradient-following algorithms for connectionist reinforcement learning},
  author={Williams, Ronald J.},
  journal={Machine Learning},
  volume={8},
  pages={229--256},
  year={1992},
  doi={10.1007/BF00992696}
}

@inproceedings{schulman2017ppo,
  title={Proximal Policy Optimization Algorithms},
  author={Schulman, John and Wolski, Filip and Dhariwal, Prafulla and Radford, Alec and Klimov, Oleg},
  year={2017},
  url={https://arxiv.org/abs/1707.06347}
}

@article{xu2023imagereward,
  title={ImageReward: Learning and Evaluating Human Preferences for Text-to-Image Generation},
  author={Xu, Jiazheng and others},
  year={2023},
  url={https://arxiv.org/abs/2304.05977}
}
"""


SAMPLE_TSV = """id\tyear\ttype\tvenue\turl\trelevance_to_bagel_rl_diagnosis
williams1992reinforce\t1992\tclassic\tMachine Learning\thttps://doi.org/10.1007/BF00992696\tPolicy-gradient basis; zero group variance implies no useful advantage.
schulman2017ppo\t2017\tclassic\tarXiv\thttps://arxiv.org/abs/1707.06347\tKL/clipping/update stability anchor.
xu2023imagereward\t2023\trecent\tNeurIPS\thttps://arxiv.org/abs/2304.05977\tVisual reward-model reliability and preference correlation.
"""


@pytest.fixture
def wiki(tmp_path: Path) -> WikiStore:
    root = tmp_path / ".autors" / "demo" / "wiki"
    for sub in ("sources/papers", "sources/repos", "sources/runs",
                "pages/techniques", "pages/conflicts", "pages/patterns"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return WikiStore(root)


def test_parse_bib_entries_basic():
    entries = parse_bib_entries(SAMPLE_BIB)
    assert len(entries) == 3
    keys = [e["key"] for e in entries]
    assert keys == ["williams1992reinforce", "schulman2017ppo", "xu2023imagereward"]
    schulman = next(e for e in entries if e["key"] == "schulman2017ppo")
    assert schulman["title"] == "Proximal Policy Optimization Algorithms"
    assert schulman["year"] == "2017"
    assert schulman["url"] == "https://arxiv.org/abs/1707.06347"


def test_parse_bib_handles_missing_url_via_doi():
    entries = parse_bib_entries(SAMPLE_BIB)
    williams = next(e for e in entries if e["key"] == "williams1992reinforce")
    # No `url` field, but `doi` should fall back to https://doi.org/<doi>
    assert williams["url"].startswith("https://doi.org/10.1007/BF00992696")


def test_ingest_refs_bib_writes_one_source_per_entry(wiki: WikiStore, tmp_path: Path):
    bib = tmp_path / "refs.bib"
    bib.write_text(SAMPLE_BIB, encoding="utf-8")
    written = ingest_refs_bib(
        wiki,
        bib_path=bib,
        ingested_by="wiki-curator@test-mission",
    )
    assert len(written) == 3
    # Files exist on disk with the expected paths
    for path in written:
        assert path.exists()
    # Round-trip the first one
    src = wiki.read_source(SourcePaper, "papers/williams1992reinforce")
    assert src.title.startswith("Simple statistical")
    assert src.url.startswith("https://doi.org/")
    assert src.ingested_by == "wiki-curator@test-mission"


def test_ingest_refs_bib_is_idempotent(wiki: WikiStore, tmp_path: Path):
    bib = tmp_path / "refs.bib"
    bib.write_text(SAMPLE_BIB, encoding="utf-8")
    first = ingest_refs_bib(wiki, bib_path=bib, ingested_by="x")
    second = ingest_refs_bib(wiki, bib_path=bib, ingested_by="x")
    assert len(first) == 3
    assert len(second) == 0  # sources are immutable; second call skips all


def test_ingest_lit_matrix_appends_relevance_to_source_body(
    wiki: WikiStore, tmp_path: Path
):
    bib = tmp_path / "refs.bib"
    bib.write_text(SAMPLE_BIB, encoding="utf-8")
    ingest_refs_bib(wiki, bib_path=bib, ingested_by="x")

    tsv = tmp_path / "LIT_MATRIX.tsv"
    tsv.write_text(SAMPLE_TSV, encoding="utf-8")
    enriched_count = ingest_lit_matrix(wiki, tsv_path=tsv)
    assert enriched_count == 3
    src = wiki.read_source(SourcePaper, "papers/schulman2017ppo")
    assert "KL/clipping/update stability anchor." in src.body


def test_ingest_lit_matrix_skips_papers_not_in_sources(
    wiki: WikiStore, tmp_path: Path
):
    # No bib ingested -> sources/papers/ empty -> LIT_MATRIX rows have no
    # target -> enrichment should be a no-op, not an error.
    tsv = tmp_path / "LIT_MATRIX.tsv"
    tsv.write_text(SAMPLE_TSV, encoding="utf-8")
    enriched_count = ingest_lit_matrix(wiki, tsv_path=tsv)
    assert enriched_count == 0
```

### Step 2 — run to confirm failure

```bash
pytest tests/test_wiki_ingest.py -v
```
Expected: ImportError on `argus_skill.wiki.ingest`.

### Step 3 — implement ingest.py

Create `argus_skill/wiki/ingest.py`:
```python
"""Reviewer-side helpers for converting engineer-produced lit artifacts
(paper/refs.bib, research/LIT_MATRIX.tsv) into wiki sources.

These exist because, in practice, the argus engineer often uses codex's
native web search and writes refs.bib directly, bypassing the four named
ingestion skills (paper-ingestion / arxiv-paper-search /
semantic-scholar-search / novelty-check) that have the per-skill wiki
hook. Without this module the wiki would stay empty for any mission
that does its own search.

Pure file I/O + regex; no LLM calls.
"""
from __future__ import annotations

import csv
import hashlib
import re
from datetime import date
from pathlib import Path

from .schema import SourcePaper, parse_frontmatter, serialize_frontmatter
from .store import WikiStore


_BIB_ENTRY_RE = re.compile(
    r"@\w+\s*\{\s*([^,\s]+)\s*,\s*(.*?)\n\}",
    re.DOTALL,
)
_BIB_FIELD_RE = re.compile(
    r"(\w+)\s*=\s*[{\"](.+?)[}\"]\s*,?\s*$",
    re.MULTILINE | re.DOTALL,
)


def parse_bib_entries(text: str) -> list[dict[str, str]]:
    """Return a list of dicts: {key, type-erased fields...}.

    Tolerates @article / @inproceedings / @book / @misc and is forgiving
    about whitespace. URL falls back to https://doi.org/<doi> when only
    `doi` is present.
    """
    entries: list[dict[str, str]] = []
    for m in _BIB_ENTRY_RE.finditer(text):
        key = m.group(1).strip()
        body = m.group(2)
        fields: dict[str, str] = {"key": key}
        for fm in _BIB_FIELD_RE.finditer(body):
            name = fm.group(1).strip().lower()
            value = " ".join(fm.group(2).split())  # collapse whitespace
            fields[name] = value
        if "url" not in fields and "doi" in fields:
            fields["url"] = f"https://doi.org/{fields['doi']}"
        entries.append(fields)
    return entries


def _checksum(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def ingest_refs_bib(
    store: WikiStore,
    *,
    bib_path: Path,
    ingested_by: str,
    today: date | None = None,
) -> list[Path]:
    """Read refs.bib and write one immutable sources/papers/<key>.md per entry.

    Returns the list of NEWLY written paths. Already-present sources are
    silently skipped (sources are immutable). Best-effort: a malformed
    entry is skipped rather than aborting the whole batch.
    """
    today = today or date.today()
    text = bib_path.read_text(encoding="utf-8")
    written: list[Path] = []
    for entry in parse_bib_entries(text):
        key = entry.get("key", "").strip()
        if not key:
            continue
        title = entry.get("title", "").strip() or "(untitled)"
        url = entry.get("url", "").strip() or "about:blank"
        # Body: keep the verbatim original bib stanza so provenance is preserved.
        stanza = _reconstruct_stanza(entry)
        src = SourcePaper(
            id=f"papers/{key}",
            url=url,
            title=title,
            ingested_at=today,
            ingested_by=ingested_by,
            checksum=_checksum(stanza),
            body=stanza,
        )
        try:
            path = store.write_source(src)
        except FileExistsError:
            continue  # already ingested in a prior mission
        written.append(path)
    return written


def _reconstruct_stanza(entry: dict[str, str]) -> str:
    """Re-render a bib entry as text for the source body (provenance trail)."""
    key = entry.get("key", "?")
    fields = [(k, v) for k, v in entry.items() if k != "key"]
    lines = [f"@misc{{{key},"]
    for k, v in fields:
        lines.append(f"  {k} = {{{v}}},")
    lines.append("}")
    return "\n".join(lines)


def ingest_lit_matrix(
    store: WikiStore,
    *,
    tsv_path: Path,
) -> int:
    """Append a `relevance:` line to each existing source paper whose key
    appears in LIT_MATRIX.tsv.

    LIT_MATRIX columns (argus convention):
        id, year, type, venue, url, relevance_to_<topic>

    We look for any column whose name starts with `relevance` and append
    its value to the source's body. Returns the number of sources enriched.
    """
    enriched = 0
    with tsv_path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        relevance_col = next(
            (c for c in (reader.fieldnames or []) if c.startswith("relevance")),
            None,
        )
        if relevance_col is None:
            return 0
        for row in reader:
            key = (row.get("id") or "").strip()
            relevance = (row.get(relevance_col) or "").strip()
            if not key or not relevance:
                continue
            path = store.root / "sources" / "papers" / f"{key}.md"
            if not path.exists():
                continue
            # Append relevance to the body (LIT_MATRIX itself is mutable
            # metadata; mutating the source body is acceptable here because
            # we are appending provenance, not rewriting facts).
            src = parse_frontmatter(path.read_text(encoding="utf-8"), SourcePaper)
            if relevance in src.body:
                continue  # idempotent
            new_body = (src.body + f"\n\nrelevance: {relevance}").strip()
            updated = SourcePaper(**{**src.__dict__, "body": new_body})
            path.write_text(serialize_frontmatter(updated), encoding="utf-8")
            enriched += 1
    return enriched
```

### Step 4 — run tests until green

```bash
pytest tests/test_wiki_ingest.py -v
```
Expected: 6 passed. If parser regex misses an edge case in the sample bib,
fix the regex (do NOT lower the assertions).

### Step 5 — commit

```bash
git add argus_skill/wiki/ingest.py tests/test_wiki_ingest.py
git commit -m "wiki: bib + LIT_MATRIX ingest helpers for reviewer backfill"
```

---

## Task 2: Re-export from package + wiki-curator prompt update

**Files:**
- Modify: `argus_skill/wiki/__init__.py`
- Modify: `argus_skill/builtin_skills/reviewer/wiki-curator.md`

### Step 1 — re-export

Edit `argus_skill/wiki/__init__.py`. After the existing docstring, add:
```python
from .ingest import ingest_refs_bib, ingest_lit_matrix  # noqa: F401
```

### Step 2 — patch wiki-curator.md

Open `argus_skill/builtin_skills/reviewer/wiki-curator.md`. Find the
heading `### Step 1 — survey what changed`. Insert a new section BEFORE
it (so it becomes Step 0):

```markdown
### Step 0 — backfill from engineer-produced lit artifacts

In practice the engineer often uses codex's native web search and writes
`paper/refs.bib` (+ optional `research/LIT_MATRIX.tsv`) directly, without
invoking the four named ingestion skills that have the per-skill wiki
hook. The result is that `sources/papers/` stays at 0 even though the
engineer consulted real papers.

Before doing any synthesis, backfill the wiki from whatever lit artifacts
the engineer produced this mission:

```python
from pathlib import Path
from argus_skill.wiki.store import WikiStore
from argus_skill.wiki.ingest import ingest_refs_bib, ingest_lit_matrix

wiki_root = Path(".autors/<project>/wiki")
store = WikiStore(wiki_root)

refs_bib = Path("paper/refs.bib")
if refs_bib.exists():
    written = ingest_refs_bib(
        store,
        bib_path=refs_bib,
        ingested_by=f"wiki-curator@mission-{mission_id}",
    )
    # written is a list of newly created source paths; already-present
    # sources are silently skipped (immutable).

lit_matrix = Path("research/LIT_MATRIX.tsv")
if lit_matrix.exists():
    ingest_lit_matrix(store, tsv_path=lit_matrix)
```

This converts each bib entry into an immutable `sources/papers/<key>.md`
card with the verbatim bib stanza as body. The `relevance_to_*` column
of `LIT_MATRIX.tsv` (if present) is appended to the matching source body
as a provenance line. **No judgment is added at this step**; the bib
entry is a fact, not a claim about importance. Synthesis into
`pages/techniques/*` remains your job in Steps 1-2.

Skip Step 0 silently if neither file exists.
```

(The existing Step 1 / Step 2 / etc. then continue unchanged.)

### Step 3 — commit

```bash
git add argus_skill/wiki/__init__.py argus_skill/builtin_skills/reviewer/wiki-curator.md
git commit -m "wiki: curator step 0 backfills from refs.bib + LIT_MATRIX"
```

---

## Task 3: CLI subcommand for manual backfill

**Files:**
- Modify: `argus_skill/apps/cli.py` (the file you found in M0 Task 8 — adjust if it lives elsewhere)

### Step 1 — add `wiki ingest` subcommand

In the same `wiki` subparser block you created in M0 Task 8, add a
sibling to `init`:

```python
ingest_parser = wiki_sub.add_parser(
    "ingest", help="Backfill sources/papers/ from paper/refs.bib (+ optional LIT_MATRIX.tsv)"
)
ingest_parser.add_argument(
    "--wiki", type=Path, required=True,
    help="Path to .autors/<project>/wiki/",
)
ingest_parser.add_argument(
    "--refs", type=Path,
    help="Path to refs.bib (default: <wiki>/../../paper/refs.bib if it exists)",
)
ingest_parser.add_argument(
    "--lit-matrix", type=Path,
    help="Path to LIT_MATRIX.tsv (default: <wiki>/../../research/LIT_MATRIX.tsv if it exists)",
)
ingest_parser.add_argument(
    "--ingested-by", default="wiki-curator@manual-backfill",
    help="Provenance string for the ingested_by frontmatter field",
)
```

In the dispatch block:
```python
elif args.command == "wiki" and args.wiki_cmd == "ingest":
    from argus_skill.wiki.store import WikiStore
    from argus_skill.wiki.ingest import ingest_refs_bib, ingest_lit_matrix

    store = WikiStore(args.wiki)
    # Resolve default paths: wiki is .autors/<project>/wiki/, so project
    # root is wiki/../../
    project_root = args.wiki.parent.parent
    refs = args.refs or (project_root / "paper" / "refs.bib")
    lit = args.lit_matrix or (project_root / "research" / "LIT_MATRIX.tsv")

    written: list = []
    if refs.exists():
        written = ingest_refs_bib(
            store, bib_path=refs, ingested_by=args.ingested_by
        )
        print(f"ingested {len(written)} new source(s) from {refs}")
    else:
        print(f"no refs.bib at {refs}, skipping bib ingest")

    if lit.exists():
        enriched = ingest_lit_matrix(store, tsv_path=lit)
        print(f"enriched {enriched} source(s) from {lit}")
    else:
        print(f"no LIT_MATRIX.tsv at {lit}, skipping enrichment")

    return 0
```

### Step 2 — manual smoke test

```bash
argus-skill wiki ingest \
  --wiki /data/yijia/unify_RL_argus/.autors/unify_RL_argus/wiki
```
Expected:
- `ingested 15 new source(s) from /data/yijia/unify_RL_argus/paper/refs.bib`
- `enriched 15 source(s) from /data/yijia/unify_RL_argus/research/LIT_MATRIX.tsv`

Verify:
```bash
ls /data/yijia/unify_RL_argus/.autors/unify_RL_argus/wiki/sources/papers/ | wc -l
# expect: 15

head -20 /data/yijia/unify_RL_argus/.autors/unify_RL_argus/wiki/sources/papers/schulman2017ppo.md
# expect: frontmatter with url, title; body has the bib stanza + relevance line
```

Then regenerate indexes and validate:
```bash
python -c "
from pathlib import Path
from argus_skill.wiki.store import WikiStore
from argus_skill.wiki.index import rebuild_indexes
from argus_skill.wiki.validate import validate_wiki
s = WikiStore(Path('/data/yijia/unify_RL_argus/.autors/unify_RL_argus/wiki'))
rebuild_indexes(s); validate_wiki(s)
print('ok')
"
```

### Step 3 — commit

```bash
git add argus_skill/apps/cli.py
git commit -m "wiki: argus-skill wiki ingest subcommand for manual backfill"
```

---

## Task 4: Run the full wiki test suite

```bash
pytest tests/test_wiki_*.py -v
```
Expected: previous 48 still pass + new 6 from `test_wiki_ingest.py` = 54
passed.

If anything regresses, fix and re-commit. Do not move on with a red bar.

---

## Task 5: Restart argus on unify_RL_argus with the same objective

This is the final smoke test. Argus's mission 2 will run with the fixed
wiki-curator, the existing `paper/refs.bib` (15 entries from mission 1) is
still on disk, so the reviewer's curator pass should populate
`sources/papers/` with 15 cards.

### Step 1 — sanity check: previous daemon really stopped

```bash
cd /data/yijia/unify_RL_argus
ARGUS_SKILL_SPECIAL_PROMPTS_DIR=/data/yijia/unify_RL_argus/.argus_special_prompts \
  argus-skill --status | grep daemon
```
Expected: `daemon : not running`. If still alive, run `--daemon-stop`.

### Step 2 — restart with the same objective

In the existing `argus-unify-rl` tmux session (or create a new one if it
died):

```bash
tmux send-keys -t argus-unify-rl 'cd /data/yijia/unify_RL_argus' Enter

OBJECTIVE='Root-cause where the current Bagel RL training pipeline is failing: is the regression in (a) SFT initialization, (b) data construction (training set / prompts / reward labels), or (c) RL itself (reward shaping / GRPO dynamics / policy update)? Use reports/ (especially the *_sft_rl_*, *_sft_vs_*, reward_* subdirs), extracted_remote_results/, and the code in src/ and unify_rl/src/. Produce a written diagnosis with: (1) most likely root cause, (2) supporting evidence with specific report paths + numbers, (3) a falsifiable 1-2 step verification experiment for each hypothesis, (4) ranked recommendations for the next training run. Train-free: no GPU needed.'

tmux send-keys -t argus-unify-rl "ARGUS_SKILL_SPECIAL_PROMPTS_DIR=/data/yijia/unify_RL_argus/.argus_special_prompts argus-skill --daemon-fg --continuous --bounded --objective \"$OBJECTIVE\"" Enter
```

### Step 3 — verify it took off

Wait 30 seconds, then capture:
```bash
sleep 30
tmux capture-pane -t argus-unify-rl -p | tail -20
```
Expected: a `daemon: ready` log line and a planner cycle starting.

### Step 4 — report back

When you're done, write a short summary to stdout:
- Did Task 4 (full test suite) end green?
- After the backfill in Task 3, how many files in
  `.autors/unify_RL_argus/wiki/sources/papers/`?
- Did the restart in Task 5 produce a `daemon: ready` log?
- Final cost so far (`argus-skill --status | grep cost`).
- Anything weird?

Do not wait for mission 2 to complete; the architect monitors that.

---

## Definition of done

- `tests/test_wiki_ingest.py` passes (6 tests).
- Full `tests/test_wiki_*.py` suite passes (54 tests).
- `argus-skill wiki ingest --wiki ...` is wired in the CLI.
- Backfill on unify_RL_argus shows 15 source files appearing in
  `.autors/unify_RL_argus/wiki/sources/papers/`.
- Argus daemon is restarted in the existing `argus-unify-rl` tmux session
  with the same objective as before, daemon-ready log captured.
- A short summary printed to stdout for the architect.

## Non-goals

- Do not change the page-card schema or other wiki internals.
- Do not auto-promote any cards based on the bib ingest — promotion is
  still reviewer judgment in Steps 1-2 of wiki-curator.
- Do not write a backfill subcommand for sources/runs/ or sources/repos/
  — those are out of scope for this patch.
