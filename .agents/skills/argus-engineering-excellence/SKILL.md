---
name: argus-engineering-excellence
description: Continuously audit and improve the Argus agent-native research harness. Use for Argus 全项目治理, token efficiency, Claude Code dynamic-workflow research, Math/Lean 4 verticals, dead-code and test cleanup, WebUI rendering, source-level budget accounting, architecture refactoring, or persistent Skill/Wiki evolution. Do not use for unrelated repositories or as a generic coding checklist.
---

# Argus Engineering Excellence

把 Argus 建成 agent-native 科研软件工程的标杆，而不是功能堆砌的 demo。
交付真实、简洁、可靠、高效、可审计、可持续进化的系统，让代码和运行结果
本身证明工程质量。

## 核心约束

- 正确性、科研质量、完整性和反作弊优先；如实报告失败、波动和不确定性。
- harness 只做领域无关的预算、调度、持久化、结构化 I/O 与反造假护栏。
  科研和架构判断归 agent，任务完成与否归独立 reviewer。
- 修根因，不靠 prompt nudge、关键词规则、重复重启、表面指标或兼容补丁掩盖问题。
- 优先复用、简化和删除；每项能力保持一个事实来源。
- 一次只完成一个最高价值的完整增量，不同时铺开所有长期方向。
- 保护共享工作区中的既有修改；不得覆盖、回滚或提交不属于本次工作的改动。

## 每次调用

1. 定位 Argus checkout。确认存在 `AGENTS.md`、`pyproject.toml` 和
   `argus_skill/`；使用用户给定路径，否则使用当前 checkout。
2. 阅读适用范围内的 `AGENTS.md`，检查工作树和当前实现。先理解现有路径，
   不假设目标能力尚不存在，也不复制已有 helper。
3. 查看真实代码、调用关系、测试、运行 trace 和历史实现，选出当前最高价值、
   能独立闭环的一项工作。
4. 首次进入项目或涉及角色权威、完成语义时，阅读
   [operating-contract.md](references/operating-contract.md)。仅加载当前工作流
   对应的其他 reference，不把整套长期目标全部塞进上下文。
5. 建立现状证据；涉及 token、成本、性能或测试速度时，先记录可比基线。
6. 实现完整根因修复，接通所有相关入口，并删除被替代的旧路径。
7. 运行仓库已有的最小针对性测试。只有必要时才扩大验证范围，不新增无关工具。
8. 展示真实前后变化与 caveat。完成后只提交本次文件，并按项目授权 push 到
   `origin/main`；若权限或远端状态阻塞，明确报告，不伪造完成。

## 按需读取

| 当前工作 | 读取 |
| --- | --- |
| 项目愿景、角色边界、交付标准 | [operating-contract.md](references/operating-contract.md) |
| Token 效率或 Claude Code dynamic workflow | [token-and-dynamic-workflow.md](references/token-and-dynamic-workflow.md) |
| 数学研究、自然语言证明、Lean 4 或数学种子 | [math-vertical.md](references/math-vertical.md) |
| 死代码、测试精简、全项目架构治理 | [code-health.md](references/code-health.md) |
| WebUI 动态渲染或统一 budget | [webui-and-budget.md](references/webui-and-budget.md) |
| Skill/Wiki、跨任务记忆或能力前后对比 | [persistent-evolution.md](references/persistent-evolution.md) |

## 完成判断

每个增量应有现状证据、根因、完整实现、旧代码清理、针对性测试和可复现的
前后对比。前后数据是 reviewer 的判断证据，不是 harness 的机械分数门槛。
不得把随机抖动、评测污染、硬编码、作弊、未完成工作或不可复现结果算作成果。

