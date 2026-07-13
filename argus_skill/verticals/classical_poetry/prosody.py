"""classical_poetry PROSODY engine — the vertical's machine-decidable reliability
layer, adapted from the classical-poetry-prototype's ``check_prosody.py``.

This is the crown asset: it does NOT "remember" whether a poem is metrical, it
looks every character up in the 平水韵 table (:file:`data/pingshui.json`) and
checks it against the standard 近体诗 patterns — reproducibly, char by char.

What it decides MECHANICALLY (each a real, reproducible finding):

* **rhyme** (押韵) — the rhyme feet of the even lines must share ONE 平声 rhyme
  category (一韵到底, 押平声韵); an off-rhyme or a 仄声 foot is a finding.
* **meter** (平仄谱) — against the four standard 律句, the 二/四/(六) positions
  must match (分明); a mismatch there is 失替.
* **hard_fault** (硬伤) — 三平尾 / 孤平 (and a soft 三仄尾 note).
* **parallelism** (对仗) — for 律诗 middle couplets, ONLY 平仄相对 + 同位重字 are
  machine-checked; semantic parallelism (词性/结构/合掌) is explicitly left to a
  human/live reviewer.

Honesty (inherited from the prototype): a 多音字 (multi-tone) is treated as 两可
and never false-flagged; a character not in the table is reported ``?`` and does
NOT count as an error. So a "compliant" verdict is a machine fact, and an
"undecidable" character is stated, not hidden.

``machine_findings`` / ``analyze`` are the structured API the runtime gate and the
poetry review contract consume; ``main`` is a CLI for the STAGE_CHECK.

Adapted from: classical-poetry-prototype/classical-chinese-poetry/scripts/check_prosody.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

_DATA = Path(__file__).resolve().parent / "data" / "pingshui.json"

# 四种基本律句（五言核心），"平"/"仄"
_FIVE = {
    "a": "仄仄平平仄",   # 仄收 · 二字仄
    "b": "平平仄仄平",   # 平收(韵) · 二字平
    "c": "平平平仄仄",   # 仄收 · 二字平
    "d": "仄仄仄平平",   # 平收(韵) · 二字仄
}
_BASE = {"仄": ["a", "b", "c", "d"], "平": ["c", "d", "a", "b"]}
_RUYUN_SWAP = {"a": "d", "c": "b"}


class ProsodyError(ValueError):
    """Raised when the rhyme table is missing or a poem cannot be read."""


def _load_map() -> dict[str, Any]:
    if not _DATA.is_file():
        raise ProsodyError(f"rhyme table not found: {_DATA}")
    with _DATA.open(encoding="utf-8") as fh:
        return json.load(fh)


_CMAP: dict[str, Any] | None = None


def _cmap() -> dict[str, Any]:
    global _CMAP
    if _CMAP is None:
        _CMAP = _load_map()
    return _CMAP


def _readings(ch: str) -> list[dict[str, Any]]:
    return _cmap().get(ch, [])


def _tone(ch: str) -> str:
    """'平' / '仄' / '两'(两可) / '?'(未知)."""
    rs = _readings(ch)
    if not rs:
        return "?"
    tones = {r["tone"] for r in rs}
    if tones == {"平"}:
        return "平"
    if tones == {"仄"}:
        return "仄"
    return "两"


def _ping_groups(ch: str) -> set[str]:
    return {r["rhyme"] for r in _readings(ch) if r["tone"] == "平"}


def parse_poem(text: str) -> list[str]:
    """Extract pure-Hanzi lines, splitting on CJK/ASCII punctuation and newlines.
    A standalone title line 《...》 is dropped."""
    lines: list[str] = []
    for raw in re.split(r"[\n\r]+", text):
        if re.fullmatch(r"\s*《.*》\s*", raw):
            continue
        for part in re.split(r"[，。！？、；,.!?;]+", raw):
            hz = re.sub(r"[^一-鿿]", "", part)
            if hz:
                lines.append(hz)
    return lines


def _build_expected(qi: str, ruyun: bool, n: int, yan: int) -> list[str]:
    seq = []
    base = _BASE[qi]
    for i in range(n):
        key = base[i % 4]
        if i == 0 and ruyun and key in _RUYUN_SWAP:
            key = _RUYUN_SWAP[key]
        core = _FIVE[key]
        if yan == 7:
            head = "平平" if core[0] == "仄" else "仄仄"
            seq.append(head + core)
        else:
            seq.append(core)
    return seq


def _tones_of(line: str) -> list[str]:
    return [_tone(ch) for ch in line]


def _conflict(actual: str, expect: str) -> bool:
    return actual in ("平", "仄") and actual != expect


def _finding(ftype: str, severity: str, detail: str, line: int | None = None) -> dict[str, Any]:
    return {"type": ftype, "severity": severity, "line": line, "detail": detail}


def _check_rhyme(lines: list[str]) -> tuple[str | None, bool, list[dict[str, Any]]]:
    n = len(lines)
    even_idx = list(range(1, n, 2))
    tally: dict[str, int] = {}
    for i in even_idx:
        for g in _ping_groups(lines[i][-1]):
            tally[g] = tally.get(g, 0) + 1
    findings: list[dict[str, Any]] = []
    if not tally:
        findings.append(_finding(
            "rhyme", "blocking",
            "韵脚均非平声（近体诗须押平声韵），或均为生僻字无法判定"))
        return None, False, findings
    main = max(tally, key=lambda k: tally[k])
    for i in even_idx:
        ch = lines[i][-1]
        groups = _ping_groups(ch)
        t = _tone(ch)
        if t == "?":
            findings.append(_finding(
                "rhyme", "note", f"韵脚「{ch}」不在字表，无法判定", i + 1))
        elif main in groups:
            continue
        elif not groups:
            findings.append(_finding(
                "rhyme", "blocking", f"韵脚「{ch}」为仄声，出韵", i + 1))
        else:
            findings.append(_finding(
                "rhyme", "blocking",
                f"韵脚「{ch}」属【{'/'.join(sorted(groups))}】，与主韵【{main}】不同 → 出韵",
                i + 1))
    ruyun = main in _ping_groups(lines[0][-1])
    return main, ruyun, findings


def _pick_qi(lines: list[str], ruyun: bool, yan: int) -> tuple[str, list[str]]:
    n = len(lines)
    key_pos = [1, 3] if yan == 5 else [1, 3, 5]
    best: tuple[int, str, list[str]] | None = None
    for qi in ("仄", "平"):
        exp = _build_expected(qi, ruyun, n, yan)
        bad = 0
        for li in range(n):
            at = _tones_of(lines[li])
            for p in key_pos:
                if p < len(at) and _conflict(at[p], exp[li][p]):
                    bad += 1
        if best is None or bad < best[0]:
            best = (bad, qi, exp)
    assert best is not None
    return best[1], best[2]


def _check_meter(lines: list[str], expected: list[str], yan: int) -> tuple[bool, list[dict[str, Any]]]:
    key_pos = [1, 3] if yan == 5 else [1, 3, 5]
    findings: list[dict[str, Any]] = []
    ok = True
    for li, line in enumerate(lines):
        at = _tones_of(line)
        exp = expected[li]
        bad_here = []
        for p in range(min(len(line), len(exp))):
            if p in key_pos and _conflict(at[p], exp[p]):
                bad_here.append(p + 1)
        if bad_here:
            ok = False
            findings.append(_finding(
                "meter", "blocking",
                f"第{'、'.join(map(str, bad_here))}字失替（分明位应作 "
                f"{'/'.join(exp[p - 1] for p in bad_here)}）", li + 1))
    return ok, findings


def _check_faults(lines: list[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for li, line in enumerate(lines):
        at = _tones_of(line)
        last3 = at[-3:]
        if all(t == "平" for t in last3):
            findings.append(_finding(
                "hard_fault", "blocking", f"「{line[-3:]}」三平尾（三平调），近体诗大忌", li + 1))
        if all(t == "仄" for t in last3):
            findings.append(_finding(
                "hard_fault", "note", f"「{line[-3:]}」三仄尾，出句可容、韵句应避", li + 1))
        if at[-1] == "平" and at[-2] == "仄":
            ping_cnt = sum(1 for t in at[:-1] if t == "平")
            if ping_cnt <= 1:
                save = "三" if len(line) == 5 else "五"
                findings.append(_finding(
                    "hard_fault", "blocking",
                    f"疑犯孤平（韵脚外仅余孤立平声），可自救：改第{save}字为平", li + 1))
    return findings


def _check_duizhang(lines: list[str], yan: int) -> list[dict[str, Any]]:
    """律诗中二联：机检平仄相对 + 同位重字；语义对仗留给人工/live reviewer。"""
    if len(lines) != 8:
        return []
    key_pos = [1, 3] if yan == 5 else [1, 3, 5]
    findings: list[dict[str, Any]] = []
    for name, (o, d) in [("颔联", (2, 3)), ("颈联", (4, 5))]:
        ao, ad = _tones_of(lines[o]), _tones_of(lines[d])
        bad = [p + 1 for p in key_pos
               if ao[p] in ("平", "仄") and ad[p] in ("平", "仄") and ao[p] == ad[p]]
        if bad:
            findings.append(_finding(
                "parallelism", "blocking",
                f"{name}第{'、'.join(map(str, bad))}字平仄未相对", o + 1))
        dup = [k + 1 for k in range(min(len(lines[o]), len(lines[d])))
               if lines[o][k] == lines[d][k]]
        if dup:
            findings.append(_finding(
                "parallelism", "note",
                f"{name}同位重字：第{'、'.join(map(str, dup))}字", o + 1))
    return findings


#: The finding types this vertical's prosody layer can decide mechanically.
PROSODY_FINDING_TYPES: frozenset[str] = frozenset({"rhyme", "meter", "hard_fault", "parallelism"})

#: 近体诗 shapes the meter/hard-fault gate applies to (yan chars, line count).
_JINTI_YAN = (5, 7)
_JINTI_N = (4, 8)


def analyze(text: str) -> dict[str, Any]:
    """Analyze ``text`` and return a structured, reproducible prosody report.

    Returns ``{form, yan, n, is_jinti, rhyme_group, compliant, findings,
    undecidable}``. ``compliant`` is the machine verdict: True iff there is NO
    blocking rhyme/meter/hard_fault/parallelism finding. For a shape that is not a
    5/7-yan 4/8-line 近体 poem, only rhyme is decidable, so meter/faults are not
    gated and ``is_jinti`` is False (compliant then reflects rhyme only).
    """
    lines = parse_poem(text)
    n = len(lines)
    if n == 0:
        raise ProsodyError("no Chinese poem lines recognized in input")
    lens = {len(x) for x in lines}
    yan = next(iter(lens)) if len(lens) == 1 else None
    is_jinti = (yan in _JINTI_YAN) and (n in _JINTI_N)

    findings: list[dict[str, Any]] = []
    main, ruyun, rhyme_findings = _check_rhyme(lines)
    findings.extend(rhyme_findings)

    if is_jinti:
        assert yan is not None
        qi, expected = _pick_qi(lines, ruyun, yan)
        _, meter_findings = _check_meter(lines, expected, yan)
        findings.extend(meter_findings)
        findings.extend(_check_faults(lines))
        findings.extend(_check_duizhang(lines, yan))
        ti = {4: "绝句", 8: "律诗"}[n]
        form = f"{yan}言{ti}"
    else:
        form = "非近体（仅押韵可判）" if yan is None else f"{yan}言{n}句（非绝句/律诗）"

    undecidable = sorted({ch for l in lines for ch in l if _tone(ch) == "?"})
    compliant = not any(f["severity"] == "blocking" for f in findings)
    return {
        "form": form, "yan": yan, "n": n, "is_jinti": is_jinti,
        "rhyme_group": main, "compliant": compliant, "findings": findings,
        "undecidable": undecidable,
    }


def machine_findings(text: str) -> list[dict[str, Any]]:
    """The structured prosody findings (rhyme/meter/hard_fault/parallelism)."""
    return analyze(text)["findings"]


def render_report(result: dict[str, Any]) -> str:
    lines = [f"体裁：{result['form']}（{result['n']} 句）",
             f"主韵部：{result['rhyme_group']}"]
    if result["undecidable"]:
        lines.append(f"未判定字（不在字表）：{' '.join(result['undecidable'])}")
    if not result["findings"]:
        lines.append("✓ 无机检可判的格律问题（意境/对仗工整度须人工评鉴）")
    for f in result["findings"]:
        mark = "✗" if f["severity"] == "blocking" else "·"
        loc = f"第{f['line']}句 " if f["line"] else ""
        lines.append(f"  {mark} [{f['type']}] {loc}{f['detail']}")
    lines.append(f"机检结论：{'基本合律' if result['compliant'] else '存在出律/出韵/硬伤，见上'}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: prosody.py <poem.txt|->", file=sys.stderr)
        return 2
    src = argv[0]
    try:
        text = sys.stdin.read() if src == "-" else Path(src).read_text(encoding="utf-8")
        result = analyze(text)
    except (ProsodyError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(render_report(result))
    return 0 if result["compliant"] else 1


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "ProsodyError",
    "PROSODY_FINDING_TYPES",
    "analyze",
    "machine_findings",
    "render_report",
    "parse_poem",
]
