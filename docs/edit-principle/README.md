# edit-principle — 修改方法论

这个目录（`edit-principle`）记录在 argus-skill 仓库做修改时遵循的工作流程。每次修改前先读这里的 skill，确保流程一致。（提交身份仍用 `nssmd`，见 `skills/03-push-after.md`。）

## 核心原则

0. **每次改之前先对照 [`README.md` 的"设计哲学"章节](../../README.md) 和 [`skills/04-harness-vs-agent-boundary.md`](skills/04-harness-vs-agent-boundary.md)**。这是 hard 原则，不是建议：harness 不做科研判断、不替 agent 决定品味好坏。任何 `if reward > 0.02` / `if families >= 3` / `if days > 21` 这类阈值出现在 harness 里都是错的。
1. **永远先 pull 再改**。上游可能在你思考时已经动过。
2. **测试驱动加新模块**：先写测试，再写实现，至少一个端到端测试证明集成路径走通。
3. **找现有钩子，不要重构**：90% 的"集成"问题是找对接入点。`grep` 找已有的同类模式，挂上去；不要新建并行架构。
4. **改完立刻 push**：本地不留长时间未推的状态，避免和上游分叉。
5. **不带 Claude / HAPI 字样**：commit 用 `Co-Authored-By: nssmd <nssmd@noreply.local>`。

## 工作流（每次改动顺序执行）

1. **Step 0 · 对照哲学** — 读 README 的"设计哲学"段 + skill 04，问自己"我要加的这段代码是科研判断还是笨管道？"。是判断 → 不写代码，写 checklist 给 reviewer。是管道 → 继续。
2. **Step 0.5 · 读当前设计 + 相关历史** — 按 [`skills/05-read-design-history-first.md`](skills/05-read-design-history-first.md)。当前契约从 `DESIGN_AUTHORITY`/`ARCHITECTURE` 读；旧裁决和事故从 Git 历史按需读取。
3. **Step 1 · pull** — 按 [`skills/00-pull-first.md`](skills/00-pull-first.md)。
4. **Step 2 · 写测试 + 实现** — 按 [`skills/01-test-driven-additions.md`](skills/01-test-driven-additions.md)。
5. **Step 3 · 接入现有钩子** — 按 [`skills/02-integration-via-existing-hooks.md`](skills/02-integration-via-existing-hooks.md)。绝不新建并行 daemon / supervisor。
6. **Step 4 · push** — 按 [`skills/03-push-after.md`](skills/03-push-after.md)。

## Skill 索引

| # | Skill | 何时用 |
|---|---|---|
| 00 | [`pull-first`](skills/00-pull-first.md) | 任何写操作前 |
| 01 | [`test-driven-additions`](skills/01-test-driven-additions.md) | 加新模块时 |
| 02 | [`integration-via-existing-hooks`](skills/02-integration-via-existing-hooks.md) | 把新模块接进 runtime 时 |
| 03 | [`push-after`](skills/03-push-after.md) | 一块工作完成后 |
| **04** | [`harness-vs-agent-boundary`](skills/04-harness-vs-agent-boundary.md) | **Step 0 必读** — 决定一段逻辑该在 harness 还是 agent prompt 里 |
| **05** | [`read-design-history-first`](skills/05-read-design-history-first.md) | **Step 0.5 必读** — 先读当前权威，再用 `git log -S` 检查同一机制的历史 |
| **06** | [`keep-files-small`](skills/06-keep-files-small.md) | **Step 2/3 必读** — 加 50+ 行新逻辑前先问"该不该新建模块"，>1000 行的文件追加必须在 commit 里解释 |

## 例外

- **只读探索**（grep / read / 测试运行）不需要 pull-first，但有写操作时必须。
- **紧急 hotfix**（生产坏了）允许跳过测试驱动，但必须在 PR 描述里写明。
- **没有任何例外可以跳过 Step 0**。即使是 hotfix，也得确认修的是管道层。

## 记账：本目录的过往违规

| 日期 | commit | 违规 | 修复 commit |
|---|---|---|---|
| 2026-06-01 | `c6b11d3` | F3 anti_mediocrity 把 `min_delta=0.02` / `min_families=3` 当 hard gate 计入 stage_check 退出码 | 见后续 commit |
