---
name: PDF Chat
description: Read academic PDFs progressively (head → brief → section → page → full) instead of dumping the entire file into context. Use to inspect your own generated paper, read a related-work PDF, or do a reviewer-style re-read of main.pdf without burning the context window.
category: research
version: 1
author_model: gpt-5.5
created_at: 2026-06-03T00:00:00+00:00
source: inspired by ARIS skills/deepxiv (no SDK dependency — self-contained pdftotext/pypdf)
---

## Title
PDF Chat

## Description
Argus-native progressive PDF reader. Mirrors ARIS `deepxiv` step pattern (`brief` → `head` → `section` → full) but with zero external SDK dependency: extraction uses the system `pdftotext` CLI with a `pypdf` fallback. arXiv IDs are fetched directly from arxiv.org/pdf and cached.

## When to use
- The reviewer agent needs to re-read `paper/main.pdf` from a reader's perspective ("did I actually explain the method on page 3?").
- A planner needs the abstract + intro of a related paper before deciding whether to study it deeply.
- An author wants section-targeted text from a long PDF without loading the whole thing.

## When NOT to use
- The artifact is already in source form (`paper/main.tex`, `paper/sections/*.tex`) — read those directly; PDF extraction loses structure.
- You need image content from the PDF — this skill is text-only; use `paper_layout_review` (vision) instead.
- The "PDF" is actually HTML / a website — fetch with `WebFetch`.

## Tool surface
Single CLI: `python -m argus_skill.tools.pdf_chat <subcommand> <source> [options]`

`<source>` can be a local path or an arXiv id like `2509.12345`. arXiv PDFs are cached at `${ARGUS_SKILL_PDF_CACHE}` (default `~/.argus-skill/pdf_cache`).

| Subcommand | Output |
|---|---|
| `head <source>` | page count + detected section TOC + first-two-page preview (~4 KB) |
| `brief <source>` | abstract + ~600 chars of Introduction (~2.8 KB total) |
| `section <source> "<name>"` | one named section (case-insensitive substring match against the detected TOC) |
| `page <source> --start N [--end M]` | text of one page or page range |
| `full <source>` | entire concatenated text (truncated to 200 KB — use sparingly) |

All output is JSON on stdout (`source`, `text`, and shape-specific keys).

## How to solve
1. Always start with `head` to see the actual section map. Section detection is heuristic; the TOC tells you exactly which names will match the `section` subcommand.
2. Use `brief` next if the question is "is this paper relevant".
3. Drop into `section` for the targeted section(s).
4. `page` is for "what's on page 7 of my own draft" — useful when paired with `paper_layout_review` page-snapshot output.
5. `full` is a last resort — costs ~200 KB of context.

### Reviewer self-review pattern
After `paper/main.pdf` compiles, the reviewer agent should:
```
python -m argus_skill.tools.pdf_chat head paper/main.pdf
python -m argus_skill.tools.pdf_chat brief paper/main.pdf
python -m argus_skill.tools.pdf_chat section paper/main.pdf "Method"
python -m argus_skill.tools.pdf_chat section paper/main.pdf "Experiments"
```
Then write `paper/REVIEWER_QUESTIONS.json` (which the `reviewer_simulation` gate enforces) with the questions raised by reading the PDF as a reader would.

### Related-paper inspection pattern
```
python -m argus_skill.tools.pdf_chat brief 2509.12345
# decide whether to go deeper
python -m argus_skill.tools.pdf_chat section 2509.12345 "Related Work"
```

## Key rules
- Prefer `head`/`brief`/`section` over `full`; the whole point is to not load 30 KB of irrelevant prose.
- Section detection is heuristic — if a section name doesn't match, re-query with a different keyword or use `page`.
- Cached arXiv PDFs are immutable per id; force-refresh by deleting `${ARGUS_SKILL_PDF_CACHE}/<id>.pdf`.
- This tool reads only; it does NOT modify PDFs or write summaries to disk. Save summaries via `Write` if the agent wants them persisted.

## Response shape
- Return the subcommand's JSON output verbatim.
- If the PDF is missing or the arXiv fetch fails, surface the error and suggest the next step (fix path / retry / try `/arxiv`).

## Acknowledgements
Step pattern adapted from ARIS `deepxiv`. Extraction implementation is independent.
