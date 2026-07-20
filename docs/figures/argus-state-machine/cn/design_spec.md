<!-- ppt-master-schema: design-spec/v1 -->
# argus_state_machine_cn - Design Spec

## I. Project Information

| Item | Value |
| --- | --- |
| Project Name | argus_state_machine_cn |
| Canvas Format | PPT 16:9 (1280×720) |
| Page Count | 2 |
| Target Audience | Argus 架构设计者、Agent 系统研究者、顶级会议论文审稿人及工程负责人 |
| Communication Intent | 以论文级系统图解释修复后的 Argus 状态转移，并证明此前识别的死锁、活锁和有界阻塞路径均获得明确退出边 |
| Desired Audience Outcome | 读者能在双栏缩放下辨认角色权限、正常转移、活性保护器及其触发后的安全终态 |
| Core Message / Ask / Action | 修复后的 Argus 对每类等待和失败闭环提供拒绝、唤醒、原子提交、有限重试或人工升级边 |
| Delivery Context | 顶级系统/机器学习会议论文插图；读者主导，兼容口头讲解 |
| Artifact Afterlife | 论文正文、补充材料、架构评审与后续修复设计基线 |
| Reading Mode | text |
| Content Strategy | 重组审计内容为两张独立论文图；保留所有状态语义、风险等级和已验证闭环，不增加未经审计的事实 |
| Design Style | SOSP/OSDI 式 data-journalism 系统图；彩色、克制、色盲友好、黑白打印仍可区分 |
| Created Date | 2026-07-20 |

## II. Canvas Specification

| Property | Value |
| --- | --- |
| Format | PPT 16:9 |
| Dimensions | 1280×720 |
| viewBox | `0 0 1280 720` |
| Margins | 40 px |
| Content Area | 1200×640 px |

## III. Visual Theme

### Theme Style

- **Mode**: instructional
- **Visual style**: data-journalism
- **Theme**: Academic systems architecture with semantic state colors
- **Tone**: Precise, analytical, evidence-led, publication-ready

### Color Scheme

| Role | HEX | Purpose |
| --- | --- | --- |
| Background | #FBFCFE | 论文白底，降低纯白眩光 |
| Secondary background | #F1F5F9 | 泳道、分组、说明区域 |
| Primary | #25364A | 标题、节点文字、边框 |
| Control flow blue | #4477AA | Manager/Planner 主控制链 |
| Normal progress green | #228833 | 正常、可继续、成功转移 |
| Recoverable wait amber | #CCBB44 | HOLD、pause、可恢复阻塞 |
| Critical loop red | #EE6677 | 死锁、活锁和失败闭环 |
| Operator arbitration purple | #AA3377 | 人工授权、Manager 仲裁 |
| Inactive gray | #BBBBBB | 非活动角色与背景连接 |
| Body text | #25364A | 正文与注释 |

颜色不是唯一编码：正常边使用实线，等待边使用虚线，死锁回路使用双线/回环箭头，人工边界使用点划线。

## IV. Typography System

### Font Plan

| Role | Chinese | English | Fallback tail |
| --- | --- | --- | --- |
| Title | Microsoft YaHei | Arial | sans-serif |
| Body | Microsoft YaHei | Arial | sans-serif |
| Emphasis | Microsoft YaHei | Arial | sans-serif |
| Code | Consolas | Consolas | monospace |

- Title: Microsoft YaHei Bold / Arial Bold
- Body: Microsoft YaHei / Arial
- Emphasis: Bold, never italic for Chinese
- Code: Consolas

### Font Size Hierarchy

| Purpose | Size |
| --- | --- |
| Body | 20 px |
| Page title | 36 px |
| Subtitle | 24 px |
| Annotation | 15 px |
| Footnote | 13 px |

## V. Layout Principles

### Page Structure

- **Header area**: 40–92 px，左对齐标题与一句结论；右侧放 Figure 编号和风险图例
- **Content area**: 92–674 px，使用不对称分区与精确网格，避免卡片墙
- **Footer area**: 674–704 px，短图注、状态符号说明和来源说明

### Spacing Specification

| Element | Current Project |
| --- | --- |
| Safe margin | 40 px |
| Content block gap | 16–22 px |
| Icon-text gap | N/A（不使用图标） |

## VI. Icon Usage Specification

不使用装饰性图标。状态语义仅由节点几何、线型、箭头和颜色表达。

## VII. Visualization Reference List

| Page | Template | Path | Summary-quote | Usage |
| --- | --- | --- | --- | --- |
| P01 | layered_architecture | templates/charts/layered_architecture.svg | "Pick for 3-4 horizontal architecture layers (presentation/service/data), 2-4 module cards per layer, each card = title + 1-line description (description required, even if source brief). Skip if no per-module descriptions (use icon_grid) or no horizontal layering (use module_composition)." | Adapt into control authority, execution loop, backlog reconciliation, and persistent-state wake layers |
| P02 | no-template-match | N/A | N/A | Custom liveness-safeguard atlas mapping every former risk to its guard and exit state |

Runners-up considered:
- process_flow | rejected for P01: cannot express Reviewer–Engineer cycles and stage rollback.
- circular_stages | rejected for P02: assumes one benign closed loop rather than multiple failure SCCs.
- matrix_2x2 | rejected for P02: risk classes are categorical, not two-axis quantitative observations.

## VIII. Image Resource List

No images. All visible content is authored as native SVG text, shapes, paths, and connectors.

## IX. Content Outline

### Part 1: Argus 修复后状态机与活性保护

#### Slide 01 - Argus 修复后状态机：每条等待路径都有退出边

- **Audience move**: 从理解角色权限进一步转向验证每个持久状态、重规划分支和阶段裁决都能到达安全后继状态
- **Layout**: 三层横向架构。顶部为 Operator/Manager/Planner 控制层；中部为 Backlog Reconciler 与 Engineer↔Reviewer round-loop；底部为 stage、pause、daemon upgrade 和 operator answer 持久状态。右侧放线型图例、模型成本口径与活性不变量。
- **Title**: Argus 修复后状态机：每条等待路径都有退出边
- **Core message**: 同范围修复仍留在 Engineer–Reviewer 内环；所有跨范围、等待和失败路径现在分别由版本化 CAS、自动唤醒、有限熔断或 Manager/Operator 仲裁终止。
- **Content**:
  - Operator → Manager：Manager 解释目标、授权与工作流；只有 Manager 能推进、保持或回滚 stage。
  - Planner → atomic DAG commit：整批写入前执行 cycle validation；普通计划和 replacement 都不会部分提交。
  - `next_pending` 与 `claim_next` 均先执行 backlog reconciliation：遗留 SCC、缺失依赖和失败依赖进入 `skipped`，不再伪装成空队列。
  - Engineer → Reviewer；`continue_same_scope` 进入下一轮 Engineer，并保留 Reviewer 证据。
  - `replan_requested` 携带 `plan_id + plan_version`；replacement 通过 compare-and-swap 原子提交，旧节点进入不可复活的 `superseded`。
  - Manager HOLD 不再重启同一 bounded item；已完成 workspace 接收新 daemon intent 时会重新打开 terminal stage，而不是直接宣布新任务完成。
  - `paused_budget/provider/daemon_shutdown` 在条件恢复后自动开始新 attempt；`paused_operator` 保持显式人工唤醒。
  - Operator answer 创建 continuation，并把所有活跃下游依赖从旧 failed ID 原子重接到 continuation。
  - UI 将费用明确标为 `MODEL SPEND`，只表示模型/API 调用，不包含 GPU 和基础设施成本。

#### Slide 02 - 活性保护图谱：13 个风险如何被关闭

- **Audience move**: 从“知道存在风险”转向逐项核验保护机制、退出状态和验证证据
- **Layout**: 左侧正常推进与安全终态主干；右侧按 P0/P1/P2 三列排列“原风险 → 新保护器 → 退出状态”。底部放验证带和统一活性条件。
- **Title**: 活性保护图谱：13 个风险如何被关闭
- **Core message**: 每一个原死锁或活锁分量现在至少具有一个确定退出机制；重复失败不会无限消耗预算。
- **Content**:
  - P0-1 DAG 环：新批次写入前拒绝；遗留 SCC 在读取时终结为 `skipped`。
  - P0-2 cleanup 不可达：`next_pending` 与 `claim_next` 共享 reconciliation，因此无 ready 节点时仍会清理。
  - P0-3 未版本化 replan：使用 `expected plan/version → CAS → version+1`。
  - P0-4 replacement 全过滤：持久化计数；第三次失败打开 circuit breaker，当前节点 fail-closed。
  - P0-5 recoverable pause：预算、provider 和 daemon pause 自动重启新 attempt；operator pause 保持人工控制。
  - P0-6 operator answer 断链：回答后原子 rewiring 所有活跃 downstream deps。
  - P1-1 stage lifecycle：HOLD 终结当前 bounded item；新 intent 自动重开已完成 stage。
  - P1-2 drain/upgrade：零阻塞 drain request + 5 秒定时复查；长实验继续到自然边界。
  - P1-3 unresolved cost：按 reservation 金额保守占位，但不再全机封锁。
  - P1-4 Manager feedback：相同证据最多三次；进入 terminal idle，Operator 或新 artifact revision 可唤醒。
  - P1-5 completed-task dedup：签名加入 acceptance、scope 与 context hash，新证据允许合法复跑。
  - P2-1 锁顺序：统一 `pipeline → session`。
  - P2-2 Planner failure：no-runner 和 exception 使用 15–300 秒指数退避。
  - 验证证据：life/manager/daemon 扩大测试通过；并发 claim、并发 answer 与 filtered replan 连续 20 轮压力测试通过；TUI 189 项、Web 107 项测试通过。

## X. Speaker Notes Requirements

- **Filename**: match each SVG filename under `notes/`
- **Content**: 每页 90–150 秒正式学术讲解；说明颜色与线型语义，区分已证实死锁、活锁和有意等待；不引入代码审计之外的新事实。
