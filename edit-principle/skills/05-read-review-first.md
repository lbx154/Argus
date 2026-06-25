---
name: read-review-first
description: Before continuing any line of work that touches code in argus_skill/, read review/ for written feedback on prior commits — especially any review of commits you authored. Treat review verdicts as binding architectural rulings, not opinions.
when_to_invoke: At the start of every session and after every pull. Also: any time you're about to add a feature that resembles something you've shipped before (the previous version may have been reviewed and rejected).
---

# Read-Review-First

## 流程

```bash
# 1. 列出最近评审 (按修改时间)
ls -lt review/ 2>/dev/null | head -20

# 2. 找针对自己（或本会话工作）的 review
ls review/ | grep -i "nssmd\|<your-recent-commit-sha>" 2>/dev/null

# 3. 读最近 30 天内的 review，无论作者
find review/ -name "*.md" -mtime -30 2>/dev/null | xargs -I{} echo {}

# 4. 重点读 §结论 / §TL;DR / §裁决表 —— review 通常会用裁决表标出 KEEP / REJECT / REWORK
```

## 为什么这是 Step 0.5 而不是可选的

argus-skill 的 `review/` 目录是**架构裁决的累计法律**。仓库维护方在这里以书面形式记录哪些设计被否决、哪些被保留、为什么。这些裁决具有约束力：

- 已被 **REJECT** 的设计模式：不能再实现（即使换个名字）。
- 已被 **REWORK** 的设计：必须按 review 指出的方向改，不能换路径绕过。
- 已被 **KEEP** 的设计：可以延展，但延展时要不破坏 review 中提出的边界条件。

跳过 review，等于在不知道法律的情况下立法。

## 决策矩阵

| 你正在做的事 | 必须先读的 review |
|---|---|
| 加一个 `check_*` / `validate_*` / `gate_*` 函数 | `review/*research-factory-gates*` 系列（防止 F3-style 阈值复活） |
| 改 `argus_skill/life/supervisor.py` | 任何 `*supervisor*` review |
| 改 `argus_skill/engineer/reviewer.py` 或 reviewer schema | 任何 `*reviewer*` review |
| 加新 daemon 进程 / `_*.py` helper | 任何 `*daemon*` 或 `*architecture*` review |
| 加 prompt / skill markdown | 任何 `*skill*` review |
| 改 `edit-principle/skills/*` | 任何包含 `nssmd` 或 `methodology` 的 review |

## 在脑子里建一个"被否决清单"

把 review 中所有 **REJECT** 或 **REWORK** 项摘出来，列成 anti-pattern。下次写代码前问自己："我现在写的东西像不像清单上的某一条？" 如果像，停下，按 review 给的修复方向改。

当前**已知禁忌清单**（按 commit 倒序）：

| Review | 被否决的模式 | 替代方案 |
|---|---|---|
| `review/2026-06-01-research-factory-gates-c6b11d3.md` | harness 用 magic number 阈值（`min_delta=0.02` / `min_families=3`）替 agent 判断科研品味 | 用 advisory finding 列事实，verdict 交 reviewer |
| `review/2026-06-01-research-factory-gates-c6b11d3.md` | 把品味判断的 gate 失败计入 `stage_check` 退出码 | 区分 `structural` (可 block) vs `advisory` (永远不 block) |
| `review/2026-06-01-research-factory-gates-c6b11d3.md` | 时间常数（`incubating > 7d` 等）自动 quarantine 项目 | 改成 `advisory_time_signals` 给 agent 拉取 |

每次有新 review 加进来，**这张清单要扩展**，作为 skill 04 的延伸。

## 反模式

- ❌ "我没收到 review 提醒就当没有"——review 不会主动来找你，必须 `ls review/` 看
- ❌ "review 是某人的个人意见"——只要在 `review/` 目录里它就是裁决
- ❌ 看完 review 不更新这份 skill 的"已知禁忌清单"——下一轮就会再犯
- ❌ "只读跟我同名的 review"——其他 review 也包含通用原则（例如哲学 commit `6a90f55` "Remove harness keyword heuristics" 对所有人有约束）

## 与 skill 03 (push-after) 的衔接

push 后，按 [`skills/03-push-after.md`](03-push-after.md) 留意：维护方可能在新 commit 里加 review 文件。下一次 session 开头 `git log --oneline -10` 看看有没有 `Add review of <your-sha>` 之类的 commit，立刻读它。

## 与 skill 04 (boundary) 的衔接

skill 04 是**架构原则**（harness vs agent 边界）。本 skill 是**已被裁决的具体案例**。两者一起读才完整：04 教你识别原则、05 告诉你哪些具体决定已经做过。

## 实操：本目录的 review 索引

每条 review 进来后，在这一节登记：

| 日期 | review 文件 | 一句话核心 |
|---|---|---|
| 2026-06-01 | [`review/2026-06-01-research-factory-gates-c6b11d3.md`](../../review/2026-06-01-research-factory-gates-c6b11d3.md) | F3 hard gate 被拒；F4 保留；F5 时间超时改 advisory |

这张表也是 grep-able 入口，找特定主题 review 不用每次 `ls review/`。
