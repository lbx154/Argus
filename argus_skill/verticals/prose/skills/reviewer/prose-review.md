---
name: "Prose Review"
description: "Review prose structure, declared constraints, and live literary craft."
---

# 散文审阅 · Prose (Lyric / Narrative / Memoir) Review

Reuses the framework Reviewer role. Prose has no meter, so the split is honest: a
THIN machine layer (prose_state structure + declared hard constraints) and a
LIVE-reviewer craft layer that is never mechanized — and that is where prose is
actually judged.

## 一 · 结构与硬约束（机检层 · BLOCKING · 由阶段完成钩子调用 `structure` 校验器强制）

- **structure**：prose_state 必须声明 narrative_center / observation_subject /
  factual_anchors / memory_boundary / paragraph_movement / ending_strategy。
- **language / paragraph_count / banned_word / empty**：声明的语言、段数上下限、
  禁用陈词清单、非空。

这些只判"声明是否齐全、硬约束是否满足"，不判散文好坏。

## 二 · 技法（live-reviewer 层 · 非机检 · NON-blocking heuristic）

Recorded as NON-blocking live findings, never mechanized or scored:

- **observation（观察）**：观察是否具体，而非抽象抒情？
- **fact_memory（事实/回忆边界）**：正文是否混淆客观事实与回忆/推测？是否越过声明的
  memory_boundary？
- **fabrication（擅自补写）**：模型是否替用户"补全"了本不存在的事实/细节？——最需警惕。
- **movement（段落推进）**：段落之间是否真有推进，而非原地打转？
- **imagery（意象）**：意象是否承担结构作用，而非装饰？
- **ending（收束）**：结尾是否虚假升华/口号式？
- **template（模板化）**：是否出现模板化哲理句、套路化抒情？

## 输出

Emit `prose/review.json` as `{verdict, findings[]}` per the shared literary review
contract. `type` ∈ the prose vocabulary (structure/language/paragraph_count/
banned_word/empty + observation/fact_memory/fabrication/movement/imagery/ending/
template). Structure + hard-constraint findings are `blocking`; craft findings are
non-blocking judgements. Never fake a numeric aesthetic score, and never let an
invented fact pass as the operator's memory.
