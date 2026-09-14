# Proposal 时间预估与实验排期

每个 proposal 有具体完成时间、乐观到悲观区间、实验依赖及资源排布。
用户选择 idea、期限或资源后可重新计算；记录版本后可以追溯初版估计、
历次修改、实际延期与具体原因。模块位于 research vertical，不引入新 daemon。

## 直接使用

在源码根目录运行：

```bash
python -m argus_skill.verticals.research.timeline \
  --input docs/examples/research-timeline.json
```

[完整示例](examples/research-timeline.json)包括实现难度、idea 验证、主实验、
消融、held-out 确认、分析、写作与审阅，以及一个备选 proposal。
所有示例数字均为演示估计，不是该模型或研究方法的实测工期。

输出 Markdown；加 `--json` 输出结构化数据。`estimate(payload)` 也可直接导入。
Web API 已提供相同的只读预览接口，使用现有 Bearer 认证：

```text
POST /api/research/timeline/estimate
Content-Type: application/json
Authorization: Bearer <configured-token>

<与 CLI 输入相同的 JSON>
```

接口不启动模型调用、实验或 daemon。输入结构错误返回 422。
当前交付是计算模块、CLI、API 与研究 Skill 接入；尚无专门的前端编辑页面。
自然语言 proposal 由现有 Agent 按 `research-timeline` Skill 形成输入，不用关键词猜任务。

## 时间的含义

- 每个任务提供 `duration_hours: [下限, 最可能, 上限]`，点估计采用
  `(下限 + 4 × 最可能 + 上限) / 6`。模型难度写在 `difficulty` 与 `basis`，
  由任务作者解释它对工期的影响；代码不隐藏乘以某个难度系数。
- `resources` 声明可同时使用的槽位；任务声明占用量。GPU、CPU、研究人员等
  均为命名资源。按依赖拓扑顺序及输入同级顺序，放入最早可行的资源时间窗。
  支持多个资源同时占用及多 worker/GPU：以事先确认可稳定并行的 worker 槽建模。
- 工期是从同一项目起点开始的经过小时，`now_hours` 是当前进度时刻，
  `deadline_hours` 是期望完成时刻。资源假定连续可用；人工非工作时间、
  外部等待要计入估计，或拆成等待任务。模型不实现节假日历、抢占或全局最优排程。
- `finish_hours.expected` 为包含资源排队的具体完成时间，`remaining_hours`
  为距当前还需多久。下限/上限是三种输入情景排程的包络，不是概率置信区间；
  既不保证 idea 成功，也不保证未知返工落在区间内。
- 各 proposal 独立占用同一组假设资源作比较。它们是备选方案，不能把各自排期
  叠加当作实际并发容量；`selected_proposal_id` 是用户选择，不是自动选题。
- `defer_optional=true` 只在预期超期时，从依赖图末端延后明确可选且未开始的任务。
  必需任务依赖的可选节点仍被保留。排期结果列出延后项；若仍超期，保留真实
  `deadline_gap_hours`，不压缩时长、减少必需实验或改写 GoalContract。

## 记录与动态调整

把计划输入保存在目标项目 `.argus/timeline/proposal.json`，首次记录：

```bash
python -m argus_skill.verticals.research.timeline \
  --input /path/to/project/.argus/timeline/proposal.json \
  --project-root /path/to/project --expected-version 0 --reason '初版 proposal'
```

记录产生 `.argus/timeline/000001.json`，包含原输入、完整报告、修改理由和时间。
后续用 `--expected-version 1`、`2`……；版本冲突会失败，不覆盖另一位操作者的修改。
编号记录通过文件锁和原子发布保存；旧版不可由此接口改写。
这些文件是计划记录，不替代 `events.jsonl`，不授予实验成功或项目完成状态。

进展更新：

| 状态 | 字段 | 行为 |
| --- | --- | --- |
| `pending` | 原三点工期 | 从当前时间起，按依赖及资源重新排程 |
| `running` | `actual_start_hours`、`remaining_hours: [a,m,b]` | 保留开始时刻和占用资源，重新预测剩余工期 |
| `completed` | `actual_start_hours`、`actual_finish_hours` | 固定实际时段，后续不能重排执行 |
| `failed` | 实际开始/结束、`reason`，建议 `evidence` | 保留失败，不重试；必需失败或其后继未解决时不能给出完整交付日期 |
| `blocked` | `reason`，建议 `evidence` | 列出未解决阻碍和受影响后继 |

原来预计 2 小时的验证在 3 小时后失败，Planner 可以提出新的修复或验证任务。
保留失败任务；若它已退出交付路径，将它标为 `optional: true`，用新 ID 加入替代实验，
并明确修正后继依赖。选题仍遵守现有阶段政策；不因时间计算自动重开 idea portfolio。
更新 `now_hours` 并保存新版本后，恢复具体日期计算。成功、失败、运行中的历史
不得被删除或重置成 pending；若停止正在跑的任务，应先记录真实终态。

## 延期反馈

修订报告同时展示相对上一版和初版的总工期变化、选中的 proposal、旧期限与资源、
新增/修改/移除任务。`task_variances` 展示相对初版的逐任务完成时间偏差，区分
已经发生的 `observed` 延期和预测延期，附依赖、资源排队时长、原因和证据引用。

任务 `reason` 应说明可核对的具体原因，例如 evaluator 错误需修复、实测吞吐偏低、
provider 中断、额外控制实验或关键假设不成立；`evidence` 指向现有日志/结果。
这些是调用方报告的原因，不经工具自动认证。缺失原因显示“原因待确认”，
不从时间差推测科研失败。需要诊断时由 Agent 说明下一步查什么。

Engineer 在实验里程碑或用户修改约束后更新预测；Planner 使用结果调整现有 backlog。
模块本身不会定时监控进程、自动收集实际用时、自动派实验或发送延期通知。
采用的 Skill 已接入 Idea 与 Experiment playbook 的按需入口。

## 实现与验证

`timeline_models.py` 校验输入；`timeline_schedule.py` 计算依赖与资源排期；
`timeline.py` 提供报告与 CLI；`timeline_store.py` 负责版本记录和偏差对照。

```bash
python -m pytest tests/skills/test_research_timeline.py tests/webapi/test_research_timeline.py
```
