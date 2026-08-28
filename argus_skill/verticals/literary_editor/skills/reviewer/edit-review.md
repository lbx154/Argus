---
name: "Literary Editing Review"
description: "Review literary edits for mechanical discipline and live craft quality."
---

# 文学编辑审阅 · Literary Editing Review

Reuses the framework Reviewer role — the editor does NOT add a new agent. The
split is honest: a machine EDIT-DISCIPLINE layer (mechanically decidable) and a
live-reviewer craft layer that judges whether the edit is actually good.

## 一 · 编辑纪律（机检层 · BLOCKING · 由阶段完成钩子调用 `edit_ops.check_edit` 强制）

Machine checks cover only explicit facts:

- **must_not_break**：operator/诊断标记为必须保留的原句段，编辑后须逐字仍在。
- **empty**：编辑产物不得为空。

Character similarity and text length do not decide whether an edit stayed in scope.

## 二 · 编辑质量（live-reviewer 层 · 非机检 · NON-blocking heuristic）

Recorded as NON-blocking live findings, never mechanized or scored:

- **edit_quality（质量）**：润色/改写是否真的更清晰、更有力？
- **fact_fidelity（事实忠实）**：polish/proofread 是否**擅自新增了事实**？——最需警惕。
- **coherence（连贯）**：编辑后整体是否仍连贯，未引入新矛盾？
- **over_reach（越权）**：Read the source, brief, mode, goal, and semantic changes.
  Did the edit exceed its mandate? Explain which change crossed or respected that
  boundary; do not infer the answer from character similarity or length.

## 输出

Emit `editor/review.json` as `{verdict, findings[]}` per the shared literary
review contract. `type` ∈ the editor vocabulary (must_not_break/empty +
edit_quality/fact_fidelity/coherence/over_reach).
Edit-discipline findings are `blocking`; craft findings are non-blocking. Never
fake a numeric quality score, and never let an invented fact pass in a "polish".
