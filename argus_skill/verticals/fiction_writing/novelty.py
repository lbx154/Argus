"""fiction_writing anti-COPY / NOVELTY gate — the '不能抄' half of the corridor.

:mod:`.style_lint` asks "does this sound like a machine?"; this module asks "did
it copy the human?". The voice-card architecture is anti-plagiarism BY DESIGN
(abstract features + an explicit lexicon, never "imitate author X verbatim"), but
that principle had no teeth — nothing measured whether a *continuation* actually
lifted sentences from its source / canon. This IS that measurement, modelled 1:1
on :mod:`.style_lint` / :mod:`.temporal` so it plugs into the same deterministic
review gate.

Honesty contract (the same split style_lint uses):

* A long VERBATIM run shared between the draft and the reference text is a
  DETERMINISTIC FACT — the span literally appears in both — so a run at/over the
  block threshold is a BLOCKING ``verbatim_copy`` finding. Copying is categorically
  different from stylistic taste: there is a ground truth, so it earns real teeth.
* Comparison is punctuation- AND whitespace-insensitive (zh compares the stream of
  letters/ideographs/digits only), so swapping a comma for a period cannot split a
  verbatim run to evade the gate. en compares word tokens (punctuation already
  dropped).
* Besides a single long run, the AGGREGATE verbatim-overlap ratio blocks when it
  exceeds a conservative model-seed default (guarded by an absolute floor so a
  short legitimate quotation in a tiny excerpt never trips it) — this catches
  lightly-edited 洗稿 that reorders/rebreaks copied text into medium runs.
* The THRESHOLDS (run length, overlap ratio) are MODEL-SEED, pending corpus
  calibration, and set conservatively HIGH so legitimate short quotation/allusion
  passes as a note. An author may TIGHTEN via ``novelty_budget``.
* Semantic / paraphrase plagiarism is NOT machine-reliable, so it earns NO teeth
  here — that stays reviewer guidance. We never fake a similarity score.

Absent a reference text (an original, not a continuation) nothing fires.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from .style import novelty_budget

#: The finding type this gate emits (blocking or note per :func:`check_novelty`);
#: registered in ``FICTION_CONTINUITY_TYPES`` because — like temporal — it is
#: machine-provable, not a heuristic craft opinion.
NOVELTY_FINDING_TYPE = "verbatim_copy"

_CALIBRATION = "model-seed (BCC-pending)"

#: Model-seed run thresholds per language, in TOKENS (zh: letters/ideographs/digits
#: with punctuation stripped; en: words). Conservatively high: a 24-char / 12-word
#: contiguous verbatim run is a lifted sentence, not an idiom. The run LENGTH is a
#: fact; only these numbers are seeds.
_DEFAULTS: dict[str, dict[str, int]] = {
    "zh": {"note_run": 12, "block_run": 24},
    "en": {"note_run": 6, "block_run": 12},
}

#: Aggregate overlap ratio below which we do not even emit a note (avoid noise on
#: the unavoidable handful of short shared spans any continuation carries).
_RATIO_NOTE_FLOOR = 0.05

#: Model-seed DEFAULT: a draft whose verbatim overlap with the source exceeds this
#: fraction blocks even without an author-declared budget — but only once an
#: absolute amount of text has been copied (``_MIN_COVERED_FOR_RATIO_BLOCK``), so a
#: single short quotation in a tiny excerpt can never trip the ratio gate. Real
#: original continuations measure ≈0 here; near-verbatim 洗稿 measures ≫0.5.
_DEFAULT_OVERLAP_BLOCK = 0.5
_MIN_COVERED_FOR_RATIO_BLOCK = 40


def _tokens(text: str, language: str) -> tuple[list[str], list[int]]:
    """Return ``(tokens, original_char_index_per_token)`` for run detection.

    zh: each letter / ideograph / digit is a token — whitespace AND punctuation are
    dropped, so a punctuation-only edit cannot split a verbatim run. en: each
    lowercased WORD is a token (case + spacing + punctuation normalized). The char
    index lets a run map back to a source line for the finding.
    """
    tokens: list[str] = []
    idx: list[int] = []
    if language == "en":
        for m in re.finditer(r"[A-Za-z0-9']+", text):
            tokens.append(m.group(0).lower())
            idx.append(m.start())
    else:
        for i, ch in enumerate(text):
            if unicodedata.category(ch)[0] in ("L", "N"):
                tokens.append(ch)
                idx.append(i)
    return tokens, idx


def _ref_shingles(tokens: list[str], k: int) -> dict[tuple[str, ...], list[int]]:
    """Map every ``k``-token shingle of the reference to the positions it starts at."""
    out: dict[tuple[str, ...], list[int]] = {}
    for i in range(len(tokens) - k + 1):
        out.setdefault(tuple(tokens[i:i + k]), []).append(i)
    return out


def _extend(draft: list[str], di: int, ref: list[str], ri: int) -> int:
    """Length of the maximal token run matching ``draft[di:]`` against ``ref[ri:]``."""
    n = 0
    while di + n < len(draft) and ri + n < len(ref) and draft[di + n] == ref[ri + n]:
        n += 1
    return n


def _verbatim_runs(draft: list[str], ref: list[str], k: int) -> list[tuple[int, int]]:
    """Maximal, non-overlapping ``(draft_start, length)`` runs of ≥ ``k`` shared tokens."""
    if k <= 0 or len(draft) < k or len(ref) < k:
        return []
    shingles = _ref_shingles(ref, k)
    runs: list[tuple[int, int]] = []
    i = 0
    limit = len(draft) - k
    while i <= limit:
        starts = shingles.get(tuple(draft[i:i + k]))
        if not starts:
            i += 1
            continue
        best = max(_extend(draft, i, ref, ri) for ri in starts)
        runs.append((i, best))
        i += best  # non-overlapping: skip past the whole matched run
    return runs


def check_novelty(
    draft_text: str,
    reference_text: str,
    card: dict[str, Any] | None = None,
    language: str = "zh",
) -> list[dict[str, Any]]:
    """Return deterministic verbatim-overlap findings for a draft vs its source.

    A shared verbatim run at/over the block threshold is a BLOCKING
    ``verbatim_copy`` finding (the '不能抄' hard line — a run this long in both
    texts is a fact). Medium runs are non-blocking notes. The aggregate overlap
    ratio is a note unless the card declares ``novelty_budget.max_overlap_ratio``
    and it is exceeded. Thresholds are model-seed; absent a reference text nothing
    fires.
    """
    card = card or {}
    if not reference_text.strip() or not draft_text.strip():
        return []
    lang = "en" if language == "en" else "zh"
    defaults = _DEFAULTS[lang]
    budget = novelty_budget(card)
    block_run = int(budget.get("max_verbatim_run") or defaults["block_run"])
    note_run = min(defaults["note_run"], block_run)
    max_ratio = budget.get("max_overlap_ratio")
    unit = "char" if lang == "zh" else "word"
    join = "".join if lang == "zh" else " ".join

    d_tokens, d_idx = _tokens(draft_text, lang)
    r_tokens, _ = _tokens(reference_text, lang)
    runs = _verbatim_runs(d_tokens, r_tokens, note_run)

    findings: list[dict[str, Any]] = []
    covered = 0
    for start, length in runs:
        covered += length
        blocking = length >= block_run
        snippet = join(d_tokens[start:start + length])
        findings.append({
            "type": NOVELTY_FINDING_TYPE,
            "severity": "major" if blocking else "note",
            "blocking": blocking,
            "line": draft_text.count("\n", 0, d_idx[start]) + 1,
            "run_len": length,
            "detail": (
                f"{length}-{unit} verbatim run copied from the reference: "
                f"{snippet[:60]!r}" + ("" if blocking else " (below block threshold)")
            ),
            "calibration": _CALIBRATION,
        })

    if d_tokens:
        ratio = covered / len(d_tokens)
        if max_ratio is not None:
            blocks_ratio = ratio > float(max_ratio)
            thr = f"declared novelty_budget.max_overlap_ratio {max_ratio}"
        else:
            blocks_ratio = (
                ratio > _DEFAULT_OVERLAP_BLOCK and covered >= _MIN_COVERED_FOR_RATIO_BLOCK
            )
            thr = f"model-seed default {_DEFAULT_OVERLAP_BLOCK}"
        if blocks_ratio:
            findings.append({
                "type": NOVELTY_FINDING_TYPE,
                "severity": "major",
                "blocking": True,
                "line": None,
                "run_len": None,
                "detail": (
                    f"verbatim overlap ratio {ratio:.2f} ({covered} tokens) exceeds "
                    f"{thr}"
                ),
                "calibration": _CALIBRATION,
            })
        elif ratio > _RATIO_NOTE_FLOOR:
            findings.append({
                "type": NOVELTY_FINDING_TYPE,
                "severity": "note",
                "blocking": False,
                "line": None,
                "run_len": None,
                "detail": (
                    f"verbatim overlap ratio {ratio:.2f} of the draft matches the "
                    "reference (note; declare novelty_budget.max_overlap_ratio to gate)"
                ),
                "calibration": _CALIBRATION,
            })
    return findings


def is_original(
    draft_text: str,
    reference_text: str,
    card: dict[str, Any] | None = None,
    language: str = "zh",
) -> bool:
    """True iff the draft triggers NO blocking verbatim-copy finding."""
    return not any(
        f["blocking"] for f in check_novelty(draft_text, reference_text, card, language)
    )


__all__ = ["NOVELTY_FINDING_TYPE", "check_novelty", "is_original"]
