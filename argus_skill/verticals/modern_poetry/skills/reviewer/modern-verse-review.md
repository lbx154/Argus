---
name: "Modern Free-Verse Review"
description: "Review free verse against declared constraints and live poetic craft."
---

# 现代诗审阅 · Modern Free-Verse Review

Reuses the framework Reviewer role. Free verse has NO metrical machine layer, so
this checklist is honest about the split: a THIN machine layer (declared hard
constraints) and a LIVE-reviewer craft layer that is never mechanized.

## 一 · 硬约束（机检层 · BLOCKING · 由阶段完成钩子调用 `form.check_form` 强制）

Decided mechanically against the declared `form_spec` only — these catch declared
violations, not poetic quality:

- **language**：诗是否为声明的语言（zh Han / en Latin）。
- **line_count**：若 brief 指定了行数/上下限，实际行数须满足。
- **banned_word**：命中禁用陈词清单（仅清单内，非通用陈词检测）。
- **empty_line**：无空白必需行。

## 二 · 技法（live-reviewer 层 · 非机检 · NON-blocking heuristic）

These require reading and judgement; recorded as NON-blocking live findings, never
mechanized or scored:

- **imagery（意象）**：意象是否具体、彼此关联，而非罗列。
- **lineation（断行）**：断行/分节是否服务节奏与语义，避免无意义断行。
- **tone（语调）**：语调是否统一，避免抽象解释过满。
- **cliche（陈词）**：清单之外的陈词滥调、机械排比（人工判）。
- **coherence（连贯）**：整首是否有一个中心张力/落点。
- **reference_fidelity**：若给了参考文本，是否尊重其约束而非漂移。

## 输出

Emit `poetry/review.json` as `{verdict, findings[]}` per the shared literary
review contract. `type` ∈ the modern vocabulary (language/line_count/banned_word/
empty_line + imagery/lineation/tone/cliche/coherence/reference_fidelity). Hard-
constraint findings are `blocking`; craft findings are non-blocking judgements.
Never fake a numeric aesthetic score.
