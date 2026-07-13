# 近体诗审阅 · Prosody, Conception & Anti-AI Review

The classical_poetry reviewer checklist. It reuses the framework Reviewer role
(no new agent) and separates what is **machine-decidable** from what is
**live-reviewer judgement** — never faking the latter as the former.

## 一 · 格律（机检层 · BLOCKING · 由 `checks.py prosody` 强制）

These are decided reproducibly by the prosody engine against the 平水韵 table;
they are recorded as `blocking` findings and the poem cannot pass with any of them
standing. The reviewer confirms the machine report, it does not re-judge by ear:

- **rhyme（押韵）**：近体诗须押平声韵、一韵到底；韵脚出韵为 blocking。
- **meter（平仄谱）**：二四(六)分明，粘对合律；分明位失替为 blocking。
- **hard_fault（硬伤）**：三平尾、孤平为 blocking；三仄尾为 note。
- **parallelism（对仗·机检部分）**：律诗中二联平仄相对为 blocking；同位重字为 note。
  （词性、结构、忌合掌等**语义对仗**属下面的 live 层，非机检。）

多音字按两可、生僻字标「?」——不误判、不隐瞒。

## 二 · 立意与技法（live-reviewer 层 · 非机检 · NON-blocking heuristic）

These require reading and judgement; they are **live-reviewer findings**, marked
non-blocking, and are NEVER mechanized or given a fake numeric score:

- **conception（命意）**：是否有一处中心意象/字在结尾被轻轻翻面？没有则「格律再对也只是韵文」。
- **imagery（意象）**：意象是否具体、彼此关联，而非陈词堆砌？
- **diction（炼字）**：每联是否有一个经得起推敲的「诗眼」（多为动/形词）？
- **allusion（典故）**：用典是否贴切、不生硬、不掉书袋？
- **tone（语调）**：情景关系是否自然，避免直接抒情说明过满？

## 三 · 反 AI 味（live 自查 · NON-blocking）

- **anti_ai**：结尾忌口号式升华；忌合掌；忌同质化意象堆砌；忌空泛哲理句。

## 输出

Emit `poetry/review.json` as `{verdict, findings[]}` per the shared literary
review contract. Each finding `{id, type, severity, blocking, location, evidence,
suggested_action, must_not_break[]}`. `type` ∈ the poetry vocabulary
(rhyme/meter/hard_fault/parallelism/conception/imagery/diction/allusion/tone/
anti_ai). Any prosody finding is `blocking` → `verdict="revise"`. Craft findings
are non-blocking observations.
