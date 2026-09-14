# Proposal 时间预估与实验排期

每个 proposal 有具体完成时间、乐观到悲观区间、实验依赖及资源排布。
用户选择 idea、期限或资源后可重新计算；记录版本后可以追溯初版估计、
历次修改、实际延期与具体原因。模块位于 research vertical，不引入新 daemon。

## 在网页版验证

功能开发在公开仓库 `lbx154/Argus` 的 `dev` 分支。入口：选择项目 → 顶部
**更多 → 工作台 → 研究排期**。也可在当前网页版地址后加
`?project=<项目ID>&view=workbench&module=timeline` 直接进入。

1. 点击 **加载论文示例**。显示两个候选 proposal、点估计与区间，以及实验时间条。
   默认示例第一个方案预计 **103.0 小时**，区间 **38–252 小时**。
2. 把 **期望完成时间（小时）** 从 `120` 改为 `60`，排期自动更新；第一个方案
   会重排为 **58.0 小时**，区间变为 **22–142 小时**。页面的“根据期限重排”
   列出复用实现、等价缓存、并行确认等替代做法，以及每项工期变化和适用条件。
   这是演示估计：点估计满足期限，悲观情景仍可能超期。
3. 切换 **选择 idea / proposal** 或修改可用资源；展开具体任务可编辑三点工期、
   实现难度、资源占用、依赖和进展状态。也可添加候选方案与实验任务。
4. 填写 **本次保存 / 调整原因**，点击 **保存计划版本**，然后刷新页面，确认
   原来的输入、排期和版本仍在。
5. 修改任务工期，在该任务填写 **变化 / 延期原因** 与 **证据引用**，保存下一版。
   结果底部显示 **版本对照与延期原因**，包括相对初版的变化和逐任务偏差。
6. 再把期限放宽到 `300` 小时，原做法与可选实验会恢复；预计 **183 小时**，
   区间 **62–420 小时**。改为 `20` 小时则保留当前最佳候选 **58 小时**及
   **38 小时缺口**，不会把估计机械缩放成 20 小时。

**根据期限自动重排** 开关控制任务顺序与执行做法的调整；
**超期时延后可选任务** 单独控制可选范围。两者都启用时，每次编辑期限都会从
原 proposal 重新计算，而不是在上一次删减后的结果上继续删减。旧版已保存的
proposal 不会被自动补造替代方案，可加载新版示例或在任务内“添加替代做法”。

未保存修改不会自动落盘。版本冲突时保留当前草稿：先导出 proposal，再点击
“重新载入已保存计划”取得最新版本。保存计划不会启动实验。
进展和失败原因仍需由实际实验结果或操作者更新，不是后台自动监控通知。

源码用户更新方式（已有运行服务需从更新后的源码重新启动）：

```bash
git switch dev
git pull --ff-only origin dev
python -m argus_skill --web
```

这里假设本机 `origin` 指向 `https://github.com/lbx154/Argus.git`。
分支包含构建后的网页资源；本地继续修改前端时再执行：

```bash
cd frontend/web
npm ci
PYTHONPATH=../.. npm run build
```

开发时也可用 `npm run dev`，默认网页 `http://localhost:5173`，API 代理到
`http://127.0.0.1:8799`。远程机器通过 SSH 转发实际端口后访问。
已安装的旧 App 或旧服务器不会因 GitHub 推送自动升级。

## CLI 与 API

在源码根目录运行：

```bash
python -m argus_skill.verticals.research.timeline \
  --input argus_skill/verticals/research/timeline_example.json
```

[完整示例](../argus_skill/verticals/research/timeline_example.json)包括实现难度、idea 验证、主实验、
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
网页版与 CLI 使用相同的估计器和版本存储。额外接口：

- `GET /api/research/timeline/example`：读取随包发布的演示输入。
- `GET /api/projects/{sid}/research/timeline`：返回 `{latest: null | 版本记录}`。
- `POST /api/projects/{sid}/research/timeline`：使用
  `{input, expected_version, reason}` 保存版本；版本冲突返回 409。

所有接口使用现有认证。保存路径由服务端绑定的项目工作目录解析，浏览器不能指定
任意磁盘路径；拒绝越出项目的 timeline 符号链接。预估与版本保存不改变 backlog。
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
- `adapt_to_deadline=true` 在预计超期时尝试关键路径优先排序，并为未开始的任务
  选择声明好的 `execution_options`。每个做法有自己的三点工期、资源、依据、
  取舍，以及 `preserves_acceptance` 声明。仅选择声明保留必需验收目标且资源
  可满足的做法；不通过缩放原工期制造“刚好按时”。采用贪心列表调度，
  不是全局最优搜索；无法满足期限时仍展示缺口。
- 替代做法的适用性和验收范围由任务作者 / Agent 判断，运行时只负责排程。
  同一任务可以声明多个做法，例如复用已有实现、更多 GPU 并行执行相同测试。
  可直接在网页展开任务后编辑，不需要写 JSON；没有可用做法时不会编造。

示例替代做法（任务的 `execution_options` 数组元素）：

```json
{"id":"parallel","title":"并行执行相同确认实验",
 "duration_hours":[2,6,18],"resources":{"gpu":2},
 "basis":"按可用双 GPU 的同规模吞吐估计，包含汇总检查时间",
 "tradeoff":"同时占用两个 GPU 槽，可能增加其他任务排队",
 "preserves_acceptance":true}
```

`adaptation` 返回重排前的区间、调整条目、依据与取舍。最终三种情景都使用同一组
选中做法重新排程，区间不会由 deadline 截断。任务一旦开始，不能因放宽期限切回
另一个实现；网页记录开始状态时会采用当前排期的实际做法与资源，并冻结身份。
CLI/API 调用者同样应把选中行的 `duration_hours`、`resources`、`execution_option_id`
写入已启动任务，再记录实际进展。

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
`timeline_adaptation.py` 根据期限选择已声明做法并重排；
`timeline.py` 提供报告与 CLI；`timeline_store.py` 负责版本记录和偏差对照。

```bash
python -m pytest tests/skills/test_research_timeline.py tests/skills/test_timeline_adaptation.py tests/webapi/test_research_timeline.py
cd frontend/web
npm test -- src/test/researchTimeline.test.tsx src/test/researchWorkbenchApi.test.ts
npm run typecheck
```
