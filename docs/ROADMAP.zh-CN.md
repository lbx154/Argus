# 路线图：角色交接

英文版：[ROADMAP.md](ROADMAP.md)

本路线图把[交接设计](role-handoff-design.md)拆成可以独立认领的工作项。每一项写明：由哪条原则推出、
涉及哪些代码、怎样算完成、依赖什么。状态只描述 `dev` 分支，不代表已经发布或部署。

规模：**S** 少于一天；**M** 一到三天；**L** 拆成若干子项，每个子项对应一个 PR。

## 总览

| 编号 | 优先级 | 规模 | 工作项 | 原则 | 依赖 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| H0 | — | — | Reviewer 交接审计修复 | 1、5、8 | — | 已完成（`ef216a16`） |
| H1 | P0 | S | 交接指标报告 | 9 | — | 待认领，适合新贡献者 |
| H2 | P0 | M | 候选未变化时不审阅 | 4、5 | — | 待认领 |
| H3 | P0 | M | Reviewer 升级通道与能力匹配 | 8 | — | 待认领 |
| H4 | P1 | L | 宿主快照、基线与候选 diff | 1、5 | — | 待认领 |
| H5 | P1 | L | 有作者的记录与宿主干预记录 | 1 | — | 待认领 |
| H6 | P1 | M | 以验收标准作为审阅标准 | 5、7 | — | 待认领 |
| H7 | P2 | M | 用预算取代进展启发式 | 1、8 | H1、H2、H4 | 待认领 |
| H8 | P2 | M | 在候选处审阅，而不是每轮审阅 | 5、8 | H2、H4、H7 | 待认领 |
| H9 | P2 | M | 投影由记录重建 | 1、4 | H5 | 待认领 |
| B1 | P1 | S | Reviewer 只写散文、不调用审阅动作 | 3 | — | 待认领，适合新贡献者 |
| B2 | P1 | M | Manager 等待不应丢弃已通过的判决 | 1 | — | 待认领 |
| B3 | P1 | S | 操作员是否可用只有一个来源 | 1 | — | 待认领，适合新贡献者 |
| B4 | P2 | S | checkpoint 相关指令保持一致 | 1、5 | — | 待认领，适合新贡献者 |
| B5 | P2 | S | 正确渲染 Engineer 的决定 | 3 | — | 待认领，适合新贡献者 |

H1、H2、H3、H4a、H5a、H6 和全部 B 项可以并行开始。

## 如何参与

1. 选定一项后，在 issue 或 draft PR 里写明编号，避免两个人做同一项。
2. 从 `dev` 拉分支（`fix/<编号>-<简述>` 或 `feat/<编号>-<简述>`），PR 目标为 `dev`，
   详见 [CONTRIBUTING.md](../CONTRIBUTING.md)。
3. 在 PR 描述中写明这项改动服务哪条原则。说不出原则的新机制不进入 Core
   （见 [PRINCIPLES.zh-CN.md](PRINCIPLES.zh-CN.md)）。
4. 增加一个在改动前失败的回归测试。运行该项列出的测试，以及完整的 Python 测试套件，
   `--basetemp` 放在系统盘之外。
5. 只改 Python 时，发行清单会过期，`tests/release_tools/test_check_artifacts.py` 会报告这一点，
   直到运行 `python -m argus.release_tools.build_release`。请在 PR 中说明是否运行过。
   改了前端源码必须同时提交重新构建的 bundle。
6. 一个 PR 只做一项。不要在同一个补丁里同时改 API、存储格式、阶段语义和部署方式。
7. 旧状态必须仍可读取。已经在运行的任务，按它开始时的规则完成。

## 各项说明

### H0 — 审计修复（已完成）

已在 `ef216a16` 合入：
- Copilot Reviewer 能读取提示词里提到的状态、checkpoint 和 playbook 路径。
- 未经审阅的 Engineer 回合及其证据，会交给下一次审阅。
- 宿主占位记录不再被当作 Reviewer 的判断。
- 后续修改被拒绝时，不再按更早的批准结算。
- 同一任务重跑时，各次运行保留各自的轮次记录。
- Reviewer 发给 Planner 的信号能到达 Planner 的会话记录；轮换后的会话不再续用旧线程。

这些是在现有结构内的补丁。H2 到 H8 要移除的，正是让这些补丁变得必要的结构。

### H1 — 交接指标报告（P0，S）

- **为什么：** 原则 9。后续每一项都需要一个基线来报告效果。
- **范围：** 在 `argus/tools/` 中、`cache_hit_report.py` 旁边，增加一个只读报告，读取项目的
  `events.jsonl` 和轮次交接文件。按每个被接受的任务报告：Reviewer 调用次数、在未变化候选上的
  调用占比、按理由分类的 `continue`、墙钟时间、token 和模型调用次数。不写任何状态。
- **完成标准：** 测试中一份固定的事件日志得出预期数字，并且本路线图写明运行它的命令。

### H2 — 候选未变化时不审阅（P0，M）

- **为什么：** 已记录的 59 次 `continue` 中有 30 次没有任何新东西可审。原则 4 说，对未变化的
  artifact 反复评审不是工作。
- **范围：** 在调用 Reviewer 之前（`engineer/round_reviewer.py` 的 `_invoke_reviewer_with_retry`），
  宿主检查自上次完成审阅以来是否有变化：工作区文件、新到达的外部结果，或新的操作员上下文版本。
  没有变化时，记录一条宿主事件，继续或等待，不调用 Reviewer。先用现有的改动文件窗口实现，
  H4 再把它换成快照。
- **完成标准：** 后台任务仍在运行时，测试中不发生 Reviewer 调用；结果到达后恰好调用一次。
  用 H1 回放日志，能看到未变化候选上的审阅占比下降。

### H3 — Reviewer 升级通道与能力匹配（P0，M）

- **为什么：** 59 次 `continue` 中有 17 次是 Reviewer 报告缺少访问权限或工具，然后把工作退回给
  无法修复的 Engineer。原则 8。
- **范围：**
  - 让审阅动作可以把障碍交给宿主（缺权限、缺工具）或契约所有者（契约冲突），复用现有的
    `manager_attention` 路径，不另建通道。对 Engineer 无法消除的障碍，不再重跑 Engineer。
  - 让 Vertical 声明某个阶段的审阅是否需要独立执行。需要时，启用现有的只读验证沙箱
    （`ARGUS_SKILL_REVIEWER_VALIDATION_IMAGE`，见
    [ROLE_SESSIONS_AND_SKILLS.md](ROLE_SESSIONS_AND_SKILLS.md)）；不需要时，从该阶段的 Reviewer
    指引中删除复跑要求。
- **涉及文件：** `reviewer/tools.py`、`roles/prompts/reviewer.py`、`engineer/round_settlement.py`、
  Vertical 契约、`verticals/research/prompt_policy.py`。
- **完成标准：** 测试中 Reviewer 报告访问障碍后，任务进入升级，不再有 Engineer 回合；需要执行的
  阶段，Reviewer 启动时带有沙箱。

### H4 — 宿主快照、基线与候选 diff（P1，L）

- **为什么：** 原则 1、5。Reviewer 应该直接阅读变化本身，`done` 应该覆盖整个任务，而不是最后一轮。
- **H4a，快照存储：** 在 Core 中增加一个模块，把工作目录快照到项目状态目录下的私有仓库，
  工作目录作为它的 work tree。
  - 排除生成的产物、虚拟环境和嵌套仓库；超过大小上限的文件只在清单中记录路径、大小和修改时间。
  - 快照以私有 ref 中的 `(mission, run, sequence)` 标识；绝不触碰用户的仓库、分支或 index。
  - 测试覆盖：非 git 工作目录、git 工作目录、嵌套 `.git`、大文件、被排除的目录，以及
    2,000 个文件的再次快照在一秒内完成。
- **H4b，审阅输入：** 任务开始时建立基线，每次请求审阅时建立候选。先呈现基线到候选的变化、
  有界的文本 diff、清单变化和已记录的命令，再呈现 Engineer 的叙述。替换
  `roles/prompts/reviewer.py` 中的 `_changed_artifact_block`。
- **H4c，绑定：** 只有通过时基线才前移。把候选编号写入 `record_reviewed_handoff`、阶段证书和
  阶段重放，使一次通过只对它审过的内容有效。
- **H4d，删除：** 删除 `skills/round_checkpoint.py`、`ARGUS_SKILL_ROUND_CHECKPOINT` 开关和
  基于修改时间的审阅窗口。

### H5 — 有作者的记录与宿主干预记录（P1，L）

- **为什么：** 原则 1。目前有 4 条宿主路径就地改写 Reviewer 的判决，来源默认是 `"reviewer"`，
  Engineer 的下一步指令有 5 个写者。
- **H5a：** 增加一种宿主干预记录，引用它所影响的判决。结算根据判决和干预计算有效结果，并记录
  这两部分输入。此步不改变行为。
- **H5b：** 改造 `core/venue_review.py` 中的 venue 验收改写。
- **H5c：** 改造后台任务保持（`hold_review_for_pending_background_run`）。
- **H5d：** 改造 `engineer/round_settlement.py` 中的操作员提问策略和终态提问剥离。
- **H5e：** 去掉 `review_source` 的 `"reviewer"` 默认值和回退，使未知作者明确报错；合并重叠的
  来源字段。
- **H5f：** 下一步 Engineer 指令由判决加上渲染后的干预构成，只有一个写者。
- **完成标准：** 架构测试禁止宿主代码替换 Reviewer 所写判决的字段；被 venue 规则保持的判决，
  封存时保留 Reviewer 原话，外加一条独立的宿主记录。

### H6 — 以验收标准作为审阅标准（P1，M）

- **为什么：** 原则 5、7。目前标准散落在 `review_objective`、原始请求、目标契约条款和阶段指引中，
  判决是自由散文。
- **范围：**
  - 对会产生声明的工作，Planner 必须写验收标准。
  - `approve_review` 和 `revise_review` 逐条给出每项标准的状态；只有全部满足、或由其所有者接受时
    才能通过。
  - 增加不阻断的后续事项字段，交给 Planner。触及真实性底线的问题仍然阻断。
  - Reviewer 通过引用读取任务契约，而不是拼接出来的 `review_objective`。
  - 重新合入 `fix/completion-objective-status` 中关于目标状态的工作，它从未进入 `dev`。
- **涉及文件：** `roles/prompts/planner.py`、`reviewer/tools.py`、
  `life/supervisor/_mission_execution_runtime.py`、`life/context_packet.py`。
- **完成标准：** 工具校验拒绝遗漏任何一条标准的批准；后续事项既不阻断任务，也不会丢失。

### H7 — 用预算取代进展启发式（P2，M）

- **为什么：** 原则 1。宿主执行委托人设定的预算，不估计进展。
- **范围：**
  - 把轮数、墙钟时间和成本上限表达为由 Manager 或操作员设定的任务预算。到达上限时宿主停止、
    记录这一事实，由 Manager 决定下一步。
  - 删除"消息不为空即有进展"的判断、`SAME_OBSTACLE_OVERLAP`、软/硬进展规则、H0 中加入的
    自我交回上限，以及拒绝适配器附加的 playbook。
  - 从 Reviewer 提示词中删除宿主机制说明。
- **完成标准：** 架构测试列出允许存在的宿主守卫；H1 显示在匹配任务上成功率和成本没有变差。

### H8 — 在候选处审阅，而不是每轮审阅（P2，M）

- **为什么：** 原则 5、8。进行中的工作不逐轮把关。
- **范围：** Engineer 完成可逆步骤时，每轮之后不调用 Reviewer。只有当 Engineer 宣布候选，或到达
  检查点预算时才请求审阅。`NEXT_OWNER` 变为"候选就绪"信号。
- **完成标准：** H1 显示每个被接受任务的 Reviewer 调用次数下降，同时匹配任务上的误判 `done`
  没有增加。

### H9 — 投影由记录重建（P2，M）

- **为什么：** 原则 1。摘要不能成为第二份事实来源。
- **范围：** 由记录派生 `latest.json`、frontier、任务简报和 Backlog 结果字段。延续
  [architecture-simplification-plan.md](architecture-simplification-plan.md) 中描述的单一来源
  `MISSION.md` 投影。
- **完成标准：** 测试中删除这些投影后由记录重建，得到完全相同的文件。

### B1 — Reviewer 只写散文、不调用审阅动作（P1，S）

Reviewer 用散文给出判断却没有调用审阅动作时，这次调用被当作后端故障，连续两次任务就以 error 结束。
重试时明确告诉它没有提交判决，并删除 `reviewer/_parsing.py` 中没有生产调用者的散文救援代码。

### B2 — Manager 等待不应丢弃已通过的判决（P1，M）

在 `engineer/round_settlement.py` 中，Reviewer 的 `done` 已经记录之后才检查 Manager 等待，
任务会变成暂停；恢复后 Engineer 从第 1 轮重新开始。应先结算已通过的判决，或从它恢复。

### B3 — 操作员是否可用只有一个来源（P1，S）

Reviewer 的工具说明读取环境开关，结算读取项目的提问策略。两者不一致时，Reviewer 被告知去问
操作员，而结算会拒绝这个问题。两处都应读取同一个生效策略。

### B4 — checkpoint 相关指令保持一致（P2，S）

Engineer 被要求把证据路径写进 `CHECKPOINT.md`，Reviewer 被要求不要读它，而会话轮换提示又让
Reviewer 参照记录在其中的批准。证据应在工作区里，H4 之后在候选 diff 中。让提示词保持一致，并删除
`shared_checkpoint_instructions` 中未被使用的 Reviewer 分支。

### B5 — 正确渲染 Engineer 的决定（P2，S）

`engineer/round_execution.py` 中的 `_engineer_decision_message` 把 `blocked` 渲染成 `continue`，
在路由默认交给 Reviewer 时却显示 `NEXT_OWNER=engineer`。`latest_role_decision` 返回一回合中的
第一个决定，而不是最后一个。这条路径目前没有启用，但启用时不应误导。
