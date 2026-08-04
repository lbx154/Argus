---
name: "Literary Editing Review"
description: "Review literary edits for mechanical discipline and live craft quality."
---

# 文学编辑审阅 · Literary Editing Review

Reuses the framework Reviewer role — the editor does NOT add a new agent. The
split is honest: a machine EDIT-DISCIPLINE layer (mechanically decidable) and a
live-reviewer craft layer that judges whether the edit is actually good.

## 一 · 编辑纪律（机检层 · BLOCKING · 由 `checks.py edit-check` 强制）

Decided mechanically by comparing the edited text to the source under the task's
mode + must-keep list:

- **must_not_break**：operator/诊断标记为必须保留的原句段，编辑后须逐字仍在。
- **mode_discipline**：`critique` 只诊断、不得改动原文；改了即违规。
- **over_edit**：`proofread` 只改错、不得整段重写；与原文相似度过低即违规。
- **no_expansion**：`expand` 必须实际增补，编辑后不得不比原文长。
- **empty**：编辑产物不得为空（critique 除外，其编辑即原文）。

这些只判"纪律"，不判编辑好坏。

## 二 · 编辑质量（live-reviewer 层 · 非机检 · NON-blocking heuristic）

Recorded as NON-blocking live findings, never mechanized or scored:

- **edit_quality（质量）**：润色/改写是否真的更清晰、更有力？
- **fact_fidelity（事实忠实）**：polish/proofread 是否**擅自新增了事实**？——最需警惕。
- **coherence（连贯）**：编辑后整体是否仍连贯，未引入新矛盾？
- **over_reach（越权）**：是否超出 brief 允许的改动范围？

## 输出

Emit `editor/review.json` as `{verdict, findings[]}` per the shared literary
review contract. `type` ∈ the editor vocabulary (must_not_break/mode_discipline/
over_edit/no_expansion/empty + edit_quality/fact_fidelity/coherence/over_reach).
Edit-discipline findings are `blocking`; craft findings are non-blocking. Never
fake a numeric quality score, and never let an invented fact pass in a "polish".
