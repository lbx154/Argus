---
name: read-design-history-first
description: Before changing Argus, read the current design authority and inspect Git history for prior decisions on the same mechanism. Current contracts live on main; dated reviews and incidents live in history.
when_to_invoke: At the start of a code-changing session, after pulling, and before reintroducing a previously removed mechanism.
---

# Read Current Design and Relevant History First

## 流程

```bash
# 1. 先读当前权威，不从历史计划猜现状
sed -n '1,240p' docs/DESIGN_AUTHORITY.md
sed -n '1,260p' docs/ARCHITECTURE.md
sed -n '1,240p' AGENTS.md

# 2. 查目标符号/机制最近如何演变
git log --oneline --all -- <relevant-path>
git log -S'<symbol-or-config-name>' --oneline --all

# 3. 需要旧裁决/事故时直接读 Git 历史，不把快照重新提交到 main
git log --all --name-only -- 'docs/reviews/*' 'docs/incidents/*'
git show <commit>:<historical-path>
```

## 为什么这是 Step 0.5

当前设计契约和历史裁决承担不同职责：

- `docs/DESIGN_AUTHORITY.md`、`docs/ARCHITECTURE.md`、`AGENTS.md` 描述当前实现；
- Git 历史解释某个机制为什么被加入、删除或重写；
- `technical_report/` 是绑定具体版本的正式报告，不是当前 runtime 的覆盖层。

如果只读当前文件，容易重复已经证伪的方案；如果只读历史 review，又会把已删除的结构当成
现行设计。两者必须按这个顺序使用。

## 已固定的高风险反模式

| 反模式 | 当前替代方案 |
| --- | --- |
| Harness 用 magic number 判断科研价值 | 只抽取事实，Reviewer 根据 checklist 判断 |
| 把 advisory 质量信号计入结构性退出码 | 只有完整性/安全/反造假问题可以机械 block |
| 从 objective prose 用关键词猜 vertical、scope 或 completion | 使用 Manager/Planner 的结构化字段 |
| 新建第二套 daemon/supervisor/prompt 组装链 | 挂到 `docs/orchestration-modules.md` 指定的现有 owner |
| 看到旧配置名就恢复旧机制 | 先用 `git log -S` 确认它为何被删除，并核对当前测试 |

## 反模式

- 只看日期化计划，不看当前实现。
- 把历史 review 复制回 main 充当当前法律。
- 用旧文件中的行号定位当前代码。
- 因为 Git 历史里出现过某机制，就假设它仍然存在。
