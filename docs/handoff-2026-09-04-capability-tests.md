# 交接:能力测试与框架修复(2026-09-03 下午 至 09-04 凌晨)

本文档交接这一轮"作为真实用户使用 Argus"的能力批量测试结果、随之落地的框架
修改,以及仍在运行的任务和待办事项。所有改动已随本次提交推到 main。

## 一、这次提交里有什么

本次提交是多个工作会话的累计成果,核心线索是:审计 Argus 的论文生产、图表、
实验、想法、写作五个环节,删掉机器味的验证关卡,让系统像一个真实的研究者
那样工作。

### 1. 删除机器验证关卡(多个会话累计)

删除的模块(连同各自的测试):`integrity_gate.py`、`integrity_check.py`、
`experiment_audit_gate.py`、`reviewer_simulation.py`、`run_evidence_health.py`、
`paper_structural_minimums.py`、`format_facts.py`、`exemplar_grounding.py`、
`contamination_check.py`、`argument_organization.py`、`draft_outline.py`、
`publication_scale.py` 等。取而代之的是各阶段 playbook 里的自然语言判断标准,
由 Reviewer 独立行使判断,而不是脚本化的通过/不通过检查。

### 2. venue 去硬编码

`venue_profiles.py` / `venue_research.py` 重构:不再内置 AAAI、EMNLP 等
写死的会议档案,目标会议的格式与惯例由任务运行时调研得到
(`tests/skills/researched_venues.py`、`tests/test_researched_venue_e2e.py`)。

### 3. 四阶段流水线(design 并入 experiment)

`CANONICAL_STAGE_ORDER = ("idea", "experiment", "paper", "review")`,
旧的 build/plan/run/analysis 阶段名通过 `STAGE_ALIASES` 归入 experiment。
实验设计是活的对象,随证据修订,不再有独立的 design 阶段和冻结的全局计划。

### 4. 实验 playbook 的三条新规则(本轮新增)

`verticals/research/skills/research-experiment-playbook.md`:

- **复用成熟框架**:RL 后训练、DPO、分布式训练、推理服务一律走维护中的
  框架(veRL、OpenRLHF、TRL、LLaMA-Factory、vLLM 或基线自带的栈),
  手搓训练循环只在基础设施本身就是研究对象时才允许。
  `builtin_skills/engineer/training-infrastructure-guide.md` 同步。
- **参考同领域最新开源代码**:实现前调研同领域近期论文放出的代码,
  把高质量代码库当参考实现读。
- **不搞预注册仪式**(2026-09-04 凌晨,应操作员要求):不在数据出现之前
  声明成功阈值、决策程序、扩展判据;不把设计表当不可变对象。像真正的
  实验科学家那样在语境里评判证据。idea playbook 的 handoff 部分同步:
  交接只描述决定性实验的方向,数值阈值和决策程序留给 experiment 阶段
  在接触真实证据后再定。

### 5. 框架图能力重写(2026-09-03 下午)

`verticals/research/skills/engineer/paper-framework-figure-studio.md` 全文
重写,基于对强论文 Figure 1 的一轮系统调研(6 agent workflow):六种构图
原型(pipeline strip / contrast diptych / lineage / overview+zoom /
results-first teaser / coverage map,各配范例论文)、做减法凸显新颖性、
一色一概念、按图类型的文字预算、几何排版规则、生产路线表(禁止一次性
栅格出图,必须可编辑源 + 渲染检查循环)。

### 6. review 结项修复(2026-09-04 凌晨,本轮唯一的 .py 行为修复)

`verticals/research/stages.py`:research vertical 的 review 结项校验原本
硬性要求 `paper/main.tex` + 渲染 PDF。当任务目标不含论文、Manager 已合法
把 paper 阶段记录为 skipped 时,结项永远无法通过,任务卡死在重试循环。
修复:paper 记录为 skipped 时,review 结项只要求 `paper/REVIEW.md`;
正常流程(paper 未跳过)仍严格要求稿件。此 bug 由 exp-01 任务内的 agent
自行诊断出根因并停下等操作员批准——判断链条正确,值得记录。

### 7. 测试套件修复(2026-09-04 凌晨,推送前收尾)

上述改动(四阶段合并、venue 去硬编码、关卡删除)让约 30 个存量测试过期,
其中 11 个在改动之前就已在 HEAD 上失败。本轮把全部测试修到与现实一致,
过程中顺带修了两处真实的源代码问题:

- **chemistry domain overlay 重键**(`argus_skill/domains/chemistry/overlay.py`):
  检查项仍按旧六阶段(research/plan/benchmark/run/analysis)分键,四阶段
  流水线下这些领域检查项永远挂不上,等于整个化学领域的科学底线静默失效。
  已重键到 idea / experiment / review,item id 前缀同步改名。
- **分层违规修复**(`skills/vertical_select.py` 原 327 行):框架层直接
  `import verticals.research.idea_portfolio` 做旧状态迁移,违反"框架不
  依赖具名领域"的架构不变量(HEAD 上就存在)。已改为 `VerticalContract`
  新增可选 `import_legacy_state` 钩子:research vertical 在
  `verticals/research/stages.py` 里自己实现迁移,框架泛型分发,不再点名。

其余为纯测试更新:五阶段断言改四阶段、旧阶段别名映射到 experiment、
`research/PIPELINE_STATE.json` 旧路径改 `.argus/`、硬编码 EMNLP/AAAI 改用
调研型 venue fixture(`tests/skills/researched_venues.py`)、webapi 前门
测试对齐新的"第 2 轮起带上下文走 Manager triage"契约等,共 15+ 个文件。

## 二、能力测试结果

### 批量测试 1:实验能力 — 通过(exp-01)

- 项目:`/data/v-boxiuli/argus-capability-tests/exp-01`,
  warmup 对照研究,19,227,136 参数 pre-LN GPT,WikiText-2(pinned revision)。
- 全流程无人工干预:203 行设计 → 干净项目级 venv(锁定依赖)→ CUDA 预检
  → 参数量核对 → 正向对照 + 精确恢复检查 → 两个 arm 各 8000 步真 GPU 训练
  (各约 21 分钟,自动用空闲 A6000)→ 报告 → 独立审稿 verdict done。
- 科学结果:预注册终点上 warmup 显著更好(val loss 10.4911 vs 10.6297,
  困惑度 −12.9%),但最优 checkpoint 打平(第 1600 步),两个 arm 都无
  不稳定;报告诚实指出差距大部分可由累计学习率暴露解释,结论范围收敛准确。
- 暴露问题:结项账目 bug(见上文修复);全程约 4 小时,其中纯训练仅 42
  分钟——推理档位是主要开销(已调整,见下)。

### 批量测试 2:框架图能力 — 通过(fig-01)

- 项目:`/data/v-boxiuli/argus-capability-tests/fig-01`,以 run-08 的
  diagnostic-fingerprint 方法为素材,产出 Figure 1。
- 约 7 分钟出全套交付物:可编辑 SVG 源、矢量 PDF、完整 caption、设计说明
  (`figures/` 下)。
- 质量:三区 pipeline strip、一色一概念、信息边界画成红色虚线防火墙、
  具体例子贯穿、右下角与单一全局预测器的归纳偏置对比。可迭代点:画布文字
  超预算、数学记号是伪下标(`A_i` 而非真下标)、caption 未逐一解码颜色。

### run-08 想法新颖性审计(workflow,10 agent)

- 结论:incremental-but-defensible。三个对抗性反驳全部失败,组合确实无人
  占据。最近先验:He et al. (arXiv:2511.10688)、QueRE (arXiv:2501.01558,
  应作为必须基线)、BHM-ESC、PromptEval。
- run-08 自己的 novelty map 有缺口:漏 PredictaBoard、QueRE、PromptEval、
  PromptSET;对 He et al. 的定性不准。
- **待操作员决定**:是否把审计发现注入 run-08 的 inputs/。

## 三、运行中的任务(交接时状态)

| 任务 | 会话 | 状态 |
|---|---|---|
| exp-01(能力测试 1) | s-d1a03355 | **已完整闭环**:重启后 Manager 用修复代码把 review 记为 done(paper 合法跳过),守护进程 09-04 02:51 干净退出(uptime 4144s,6 个 mission)——review 结项修复经实战验证 |
| run-08(真实 ICLR 任务) | s-72fa9517 | experiment 阶段进行中;守护进程已重启以应用提速配置 |
| fig-01(能力测试 2) | s-d7db8157 | 已完成,守护进程干净退出 |

GPU0 上 29GB 的 vLLM 引擎为常驻服务(已运行 3 天),与守护进程重启无关。

## 四、配置变更(不在仓库内)

`~/.argus-skill/config.json`(2026-09-04 凌晨,应操作员"动作快点"的要求):
除 Reviewer 保持 xhigh 外,其余角色推理档位从 xhigh 降到 high
(Engineer、Planner、Manager、Supervisor、Curator、前门分类、bounded DAG、
rewrite、plan preview、self,共 11 项)。档位经 knob 层解析
(env > config.json > 默认),每轮读取。

## 五、待办与下一步

1. **批量测试 3(写作)**:拿 exp-01 的真实结果让 Argus 写论文章节。未开始。
2. **批量测试 4(想法生成)**:开放方向出 idea,用同一套 workflow 审计机器
   查新颖性。未开始。
3. **run-08 审计注入**:等操作员决定。
4. 审计遗留(按需处理):contrib/figure-studio 构建流水线未接线;
   `method_freeze.py` 的 research_review_prompt_block;paper playbook 的
   exemplar 下载冲突。
5. `reviewer/kill-argument.md` 文件名仍是机器味词汇(内容已约束为审稿内
   自然语言);如要改名需同步 idea playbook 与 ideation ATTRIBUTION 的引用。

## 六、操作备忘

- 启动:`argus --daemon --new --continuous --bounded --mission-width 1
  --project-root <PATH> --objective "..."`(tui_launcher 不转发
  --objective-file,目标要内联)。
- playbook 的 .md 每轮从磁盘读取,改动即刻生效;.py 改动的生效路径见下一条——
  **只重启守护进程不会加载新代码**。
- **部署机制(2026-09-04 补,吃过一次亏)**:守护进程经
  `~/.local/bin/argus` 启动,其 shebang 指向
  `/data/v-boxiuli/argus-runtime-latest/.venv/bin/python`,该 venv 以
  editable 方式(`_editable_impl_argus_skill.pth`)指向
  `/data/v-boxiuli/argus-runtime-latest` 这个独立的 detached git checkout,
  **而不是** `/data/v-boxiuli/Argus` 开发仓库。所以让 .py 改动生效的完整
  流程是:在开发仓库 commit 并 push 到 main → 在 argus-runtime-latest 里
  `git fetch origin && git checkout --detach <新 rev>`(先 kill 守护进程;
  依赖未变时 venv 直接复用)→ 从各任务的项目目录重启
  (`cd <workdir> && setsid nohup argus --daemon --backend copilot
  --resume <session> --resume-continuous &`)→ 用
  `daemon.status.json` 的 `runtime.revision` 字段核对确实换到了新 rev。
  之前一轮"重启换新代码"因为漏了中间的 checkout 更新而实际无效。
- 验证导入版本时不要在开发仓库目录里跑 `python -c "import argus_skill"`——
  cwd 会先于 .pth 命中,打印出误导的路径;换到无关目录再验。
- 给运行中的任务发操作员消息:`argus_skill.core.transcript.append_turn(
  life_dir, "operator", text, message_id=...)`,life_dir 为
  `~/.argus-skill/projects/<session>/`。

## 七、追加(2026-09-04 上午):规划器对后台任务的依赖

**问题(run-08 实测发现)**:规划器把一个 durable 后台任务的 job ID
(`route09-confirm-finalize-methodset-v2`)写进了任务的 `deps`。这类任务
登记在项目目录的 `.argus_subagents/` 注册表里,不在 backlog 里,依赖解析
认不出这个键,于是整个 verdict 被拒,规划器每个周期重复同一个
`planner_error`,空转烧 token(GPU 上的实际工作不受影响)。

**修复(7bba00906)**:`_planning_cycle_enqueue.py` 的依赖解析在遇到
backlog 解析不了的键时查询 external-work 注册表;查到的 job 不算 backlog
依赖——从 `deps` 里放行,任务照常入队,由 mission 里的 engineer 走既有的
external-work 协议直接和后台任务协调(等待、健康检查、恢复都是现成的、
经实战验证的机制)。查不到的键仍拒绝整批;注册表不可用时保守回退为全部
未知。放行的 job ID 记入 task_added 事件的 `external_work_deps` 字段留痕。

设计取舍:第一版实现让规划器自己"挂起等待",经 10 agent 对抗评审确认了
四个缺陷(修订挂起永不重放、烧重规划计数导致节点被误判无进展、每次唤醒
都全量调用规划器模型、静默丢弃子树),遂改为上述"放行 + mission 侧等待"
方案——规划器不等任何东西,等待交给已有机制。

## 八、追加(2026-09-04 上午):过滤反馈被文件抖动冲掉导致的重规划空转

**问题(上一节修复部署后 run-08 实测发现)**:依赖放行修复生效后,裁决
任务正常入队,但规划器仍每 ~100 秒做一次完整模型调用。链条:规划器每轮
重新提出同一个裁决任务 → 去重过滤器丢弃("与现有待办重复")→ 全部被过滤
时留存反馈(diagnostic 为 planner_tasks_filtered)并进入空闲退避 → 下一轮
intake 用"证据签名"校验反馈,而该签名摘要整个项目文件树——GPU 上的三个
worker 和 finalizer 不停写文件,签名每周期都变 → 反馈 45 秒即被判失效清除,
退避同时归零 → 规划器以 15 秒的底线间隔无限盲目重规划。反馈的重复上限
(MANAGER_FEEDBACK_REPLAN_LIMIT=3,达到即零成本终止空闲)因此永远够不着。

**修复**:planner_tasks_filtered 这一类反馈的"证据"改为 backlog 自身状态
的摘要(各条目 id+status),不再看项目文件树——后台任务写文件不构成
"重新规划会有不同结果"的新证据;此外该类反馈的连续性判定不再要求 reason
文本逐字相同(规划器每轮会换措辞复述被过滤的标题,逐字比对使计数永远
停在 1)。效果:backlog 不变时尝试计数正常累积,三次后进入终止空闲,
此后每个巡检周期零模型调用;一旦 backlog 变化(暂停任务恢复、条目完成),
intake 的签名比对立即清除反馈并唤醒规划器。安全性核对过三点:守护进程
的空闲自动退出默认关闭(ARGUS_SKILL_DAEMON_IDLE_EXIT_MIN 未设,cap≤0
即禁用),终止空闲后外层循环照常重入、每轮巡检开头仍会自动恢复已结算的
外部等待任务,intake 的签名检查先于次数上限检查(backlog 一变就能把
规划器从终止空闲里叫醒)。改动集中在 `_planning_context.py`
(`_backlog_planning_signature` / `_manager_feedback_signature_for`)、
`_planning_cycle_intake.py`(按 diagnostic 选签名)、`_constants.py`
(共享 diagnostic 常量);配一个回归测试
`tests/life/test_filtered_feedback_survives_file_churn.py`。

**同族第三处(部署上一条修复后暴露)**:过滤反馈的路走通后,规划器改为
发布事件等待契约(waiting contract,设计上契约生效期间完全跳过规划器、
零模型开销),但契约的 `watched_paths` 里有一条指向活跃 finalizer 的日志
目录(`.argus_subagents/<job>_logs`)。`artifact_revision` 唤醒源对该目录
做 stat 摘要,作业活着就周期性写日志 → 修订值不断变化 → 契约每 ~40-100
秒被"证据变化"唤醒一次,每次唤醒都是一次完整规划器调用。修复
(`_planner_waiting_observed_revision`):落在外部作业注册表目录内的
watched path 不再做文件摘要——作业自身的簿记(日志、心跳)只要作业在跑
就会一直变,不构成证据;改为纳入注册表的作业状态视图(work_id/run_id/
state,与 `subagent_state` 唤醒源同一实现,抽成 `_external_work_state_rows`
复用),状态恰好只在启动/完成/失败这类真实转变时移动。注册表外的
watched path(如 workers 的结果记录目录)行为不变。已在 run-08 的真实
项目上验证:finalizer 持续写日志的同时,新算法两次间隔计算的修订值一致。

## 九、追加(2026-09-04 上午):过滤反馈与等待判定互锁导致的死锁空转

**问题(部署第八节两处修复后 run-08 实测发现)**:签名与契约修复生效后,
又暴露出同族第四处——这次是一个真正的死锁。链条:规划器某轮提出的裁决
任务与待办重复,全部被过滤,留下 planner_tasks_filtered 反馈(attempts=1);
下一轮规划器给出**正确答案**——发布等待判定("等 finalizer 到终态再规划",
带完整的事件等待契约字段),但完成路径上有一条老规则:"存在未解决的
Manager 反馈时,等待判定一律拒绝"(视为规划器回避反馈),于是正确答案
被打回,记 PLAN_ERROR 进退避;再下一轮规划器换个花样,把"等待"包装成
一个 vertical 为 waiting 的假任务提交,入队侧以 unknown_task_vertical 跳过
——但这个跳过点**不调用 `_record_filtered_task`**,导致过滤反馈渲染为空、
不留存、attempts 冻结在 1,三次止损(MANAGER_FEEDBACK_REPLAN_LIMIT)
永远够不着。两条腿互相锁死:等待判定过不了反馈关,垃圾任务轮次又不给
反馈计数,烧钱稳定在每 ~5 分钟一次完整规划器调用,无限期持续。

**修复**:两处。(1)完成路径(`_planning_cycle_completion.py`
`_pc_handle_waiting`):当活跃反馈的 diagnostic 是 planner_tasks_filtered
时,等待判定不再视为"回避反馈",而是视为**对反馈的正面回答**——"你提的
任务全都和在跑的工作重复"的正确回应恰恰是"那我等在跑的工作"——清除
反馈、照常安装等待契约;其他 diagnostic(如阶段闸门未过)维持原拒绝
逻辑,因为那些反馈确实要求规划器给出修订任务而非等待。(2)入队路径
(`_planning_cycle_enqueue.py`):unknown_task_vertical 与
vertical_task_policy 两个跳过点补上 `_record_filtered_task`,让"全部任务
都是垃圾"的轮次也正常留存反馈、累积尝试计数,三次后进入终止空闲。

**安全性核对**:担心过"清除反馈 + 接受等待"会不会开出新的振荡(重复任务
→过滤留反馈→等待清反馈→再重复任务……)。不会:等待判定被接受后契约
即生效,契约存续期间完全跳过规划器,只有真实唤醒(配合第八节修复,
注册表状态转变才算)才会再触发规划;届时 backlog 多半已变,即便再次
全过滤,每一轮都对应一次真实的作业状态转变,成本有自然上界。

回归测试并入 `tests/life/test_filtered_feedback_survives_file_churn.py`
(等待判定解除过滤反馈;其他 diagnostic 仍拒绝)。tests/life 803 通过。

**部署后观察**:run-08 在修复部署前已自行走出这次死锁(规划器换提了三个
不重复的新任务,反馈随任务入队被清除,05:40 的等待判定被正常接受)——
但触发条件(全过滤 + 等待)在长运行里必然复现,修复堵的是结构性的洞。

## 十、追加(2026-09-05 上午):清理硬编码的"神奇超参数"

**做法**:十路并行审计扫过整个 `argus_skill/`(605 个文件),共记录 442 处
硬编码数字,每处标注它做什么、触发时的后果、以及建议(删除 / 改为自适应 /
承重保留)。分诊原则沿用操作者的判断:会杀死正常长任务的墙钟超时、把主观
判断编码成数字的验收门槛、静默截断操作者或智能体沟通内容的上限,是最坏的
一类,尽量删;防止模型无限烧钱的止损器(重规划次数上限、退避封顶、探测
冷却)是承重的,保留。结果:285 处承重保留,129 处建议改自适应(留作后续,清单见 docs/audits/),
28 处本次直接删除(实施中又顺手删了第 29 处:选题组合精简交接的
9000 字符硬上限——去掉截断后它会让理由较长的合法交接直接崩溃)。

**本次删除的 29 处,按性质分四类**:

1. *静默吞掉沟通内容的长度门(最严重)*。前门路由器对超过 1600 字符的
   Manager 快速回复和操作者转向指令直接不投递、只记一条诊断——操作者以为
   转向了,其实什么都没发生;现改为无论长短一律投递(聊天传输层本就会分片)。
   同类:监督者关切文本截到 600 字(那正是告诉工程师"哪里错了"的唯一消息)、
   讨论记录每条截到 3000 字(理由写的是 PIPE_BUF,但落盘走的是 flock 下的
   普通文件,理由不成立)、操作者精确约束每条截 400 字且最多 12 条(函数自己
   的 docstring 说"必须原样传递")、预览计划最多 8 步(操作者批准的不是模型
   实际产出的计划)、改写简报截 4000 字、澄清问题最多 6 条、选题组合的理由/
   证据/风险各种截断。全部移除。
2. *把主观判断编码成数字的验收门槛*。学术语言评审的 4.0 分下限、摘要必须
   恰好 5 句且不少于 170 词、版式评审 3.5 分下限、基础设施评审 4.0 分下限、
   图评审提示词里"评分≥4 才保留"、化学 playground 章节"至少 24 个字母数字
   才算有内容"、监督者关切"长于 40 字才算真实"、数据领域阶段数上限 10。
   评审智能体本身负责判断,这些数字是它之前时代的残留。全部移除;结构性的
   检查(阶段数≥2、占位符模式、overfull box、空白页)保留。
3. *用墙钟猜测线程死活的看门狗*。`_life_worker_run.py` 原来在角色心跳安静
   超过 30 秒时把运行中的任务标记为失败("executor exited without completing
   the task")——这是拿时间去代替"线程还活着吗"这个可以直接回答的问题。
   现在每次监督者调用都登记执行线程,看门狗只在该线程确实 `is_alive()==False`
   时才失败任务;活着的执行器永远不会因为时间流逝被判死。
4. *同一策略数字的多份拷贝*。F6 家族失败熔断(72 小时窗口、连续 3 次)原来
   在配置默认值、函数默认参数、编排层回退字面量三处各写一份;现在函数参数
   改为必填,回退读配置类的默认值,单一来源。同类:生命周期心跳 1800 秒的
   私有拷贝改为导入共享常量;规划器近期历史窗口 20 的重复定义改为再导出;
   自动填充任务的伪造 impact_score=5 去掉(规划器从未给过这个分);GPU 租约
   启动后固定 sleep(2.0) 去掉(只为让 pid 列表看起来完整,状态本就会实时重算);
   一个 1 亿字符、永远触发不了的"上限"删除。

**副作用与风险(审阅时重点看)**:语言评审与基础设施评审的输入哈希不再包含
threshold 字段,历史记录与新记录的哈希不可比;两个评审模块的 `--threshold`
CLI 参数移除(仓库内无调用者)。单监督者模式下执行线程就是主循环线程,
一直存活,所以"任务返回后仍处于 running"这种情况不再由看门狗兜底——
监督者自身的错误路径清理和启动时的孤儿回收仍然覆盖崩溃场景。

**测试**:改了 10 个既有测试(把"断言被截断/被丢弃"翻转为"断言完整送达"),
没有新增测试。改动涉及的测试目录(life / daemon / manager / tools / skills
及两个单文件)全部通过;`tests/apps/test_cli_ask.py` 里有一个顺序相关的
预存失败,在未改动的 checkout 上同样失败,与本次无关。

**留作后续的 129 处"改自适应"**:典型如重规划连击的日志窗口 100 条(应按
条目 id 直接计数而非依赖日志密度)、空闲退避封顶被复用为无关的操作者等待
再授权节奏(复用是问题,数值不是)。完整清单在审计输出里,按文件和类别
可检索。

## 十一、交接清单(2026-09-05 上午,本人工作到此为止,后续由他人接手)

### 当前状态
- GitHub main = `0f85d2c41`;四个守护进程(run-08 s-72fa9517、FuseHead s-3e28f79c、
  write-01 s-80c507d6、idea-01 s-0b1c7fa1)均运行该版本,源码根
  `/data/v-boxiuli/argus-runtime-latest`。
- 审计原始数据:`docs/audits/magic-hyperparameters-2026-09-05.json`(442 条);
  待办清单:`docs/audits/magic-hyperparameters-adaptive-followups.md`(129 条)。

### 未完成的事(按重要性排序)

1. **Argus 的自我维护机制会劫持启动器(最紧急,部署完整性风险)**。今天 01:39
   一个维护任务(`~/.argus-skill/maintenance/pending/4211b892264d.json`)基于
   b11101ab0 建了 worktree,01:44 执行 `pip install -e --user`,把
   `~/.local/bin/argus` 重写为 `#!/usr/bin/python3.11` 并指向该 worktree。任何人
   之后用 `~/.local/bin/argus` 重启守护进程,都会加载**旧代码**(我第一次重启就
   中招)。该 worktree 里还有 6 个 `life/supervisor` 文件的未提交改动和一个新测试
   `test_final_submission_cert_recovery.py`,像是在做"最终提交证书恢复"——我没动
   它。需要决定:(a) 这份 WIP 合并还是丢弃;(b) 维护机制以后不许改写用户级
   启动器,或者部署脚本固定使用 `argus-runtime-latest/.venv/bin/argus`;
   (c) 它的测试在 HOME 被换成临时目录时找不到 portalocker,子进程立即失败但
   `tests/test_math_lean_async.py:213` 的等待只查结果文件不查进程退出,每次白等
   60 秒——这是它自己 venv 的问题,也是等待逻辑的问题。
2. **29 处删除只跑了相关测试目录,没有完整全量**。全量跑被失控测试(见第十节
   修复)和预存失败打断。接手人最好在一个依赖齐全的环境里跑一遍完整 `tests/`,
   已知预存失败:`tests/apps/test_cli_ask.py` 两条(顺序相关,未改动版本同样失败)、
   quant 系列(venv 缺依赖)、`tests/webapi/test_pairing.py`、`tests/test_role_library.py`。
3. **看门狗语义变化需要在长运行中观察**(`_life_worker_run.py`)。单监督者模式下
   执行线程就是主循环线程、永远存活,所以"任务返回后仍处于 running"不再由看门狗
   兜底,只靠监督者错误路径清理和启动时孤儿回收。如果看到 backlog 里有长期
   `running` 但没人在跑的条目,就是这里。
4. **前门长消息现在一律投递**(`router.py`)。依据是聊天传输层 `chunk_html` 会分片;
   如果某个传输通道不分片,超长 Manager 回复会在那儿失败——之前是静默丢弃,现在
   会看到错误,这是有意的,但要盯一下。
5. **监督者关切判定现在只看信号词**(`_normalize.py`)。措辞平静、不含任何信号词的
   真实异常若跟在"无异常"前缀后会被清空,依赖 LLM 二次确认兜底。若发现该杀的
   run 没被杀,检查 `_CONCERN_SIGNAL_TOKENS`。
6. **评审输入哈希变了**(语言评审、基础设施评审去掉 threshold 字段),历史记录的
   哈希不再可比;两个模块的 `--threshold` CLI 参数已删,仓库外脚本若用到会报错。
7. **`daemon/config.py` 里仍有 3 / 72.0 两个字面量**作为它自己 dataclass 的默认值,
   与 `LifeSupervisorConfig` 的默认值重复,本次未统一。
8. **129 处"改自适应"**未动。优先级最高的几条:重规划连击的日志窗口 100(应按
   条目 id 计数);空闲退避封顶被复用为操作者等待再授权节奏(拆开);各处"最近 N
   条"窗口随日志密度失真。
9. **烧钱家族四处修复的长运行验证**。单元测试和一次健康观察通过,但要看几天:
   `events.jsonl` 里若再出现 `planner returned waiting instead of revision tasks`
   或 `planner.start` 间隔稳定在 1-5 分钟且无 task_added,就是又冒头了。规划器的
   等待输出目前不带 `wait_mode`(解析默认 poll)、`wake_on` 填的是作业名——若契约
   轮询过密,考虑规范化。
10. **run-08 的两件搁置事项**:把新颖性审计发现(PredictaBoard、QueRE、PromptEval、
    PromptSET)注入 `inputs/` 需操作者确认;"Adjudicate" 任务目标引用了已删的
    validator 模块——run-08 现已推进到 TICC/量化深度状态的新阶段,可能已无关。

### 我的疑虑(诚实版)
- 29 处删除由 12 个并行 agent 各用几分钟完成,我逐条审了 diff,但对每处的
  下游消费者只做了 grep 级别的核实,没有逐一运行真实场景。风险最大的是第 3、4、5 条。
- 我对 Argus 维护机制的了解仅限今天的取证,不知道它还会做什么(是否会在下次
  重启时再把守护进程切回旧代码)。在弄清之前,每次重启后务必核对
  `daemon.status.json` 的 `runtime.source_root` 和 `runtime.revision`。
- 维护 worktree 基于 b11101ab0,若它完成后自行安装/部署,会把 0f85d2c41 的改动
  盖掉。
- 本文档第八至十节的修复思路都是从 run-08 一个项目的现场推出来的;其他项目的
  规划器行为模式可能不同。

## 十二、追加(2026-09-05 上午):启动器劫持——接手实施

承接第十一节第 1 条。本节先更正两处事实,再记录已落地的修复。

### 两处事实更正

- **启动器并没有"修回"**。第十一节的口径给人的印象是启动器已恢复,不实:
  `~/.local/bin/argus` 至今未动(mtime 09-05 01:44,shebang
  `#!/usr/bin/python3.11`),经 user-site 的
  `~/.local/lib/python3.11/site-packages/_editable_impl_argus_skill.pth`
  仍指向旧 worktree
  `~/.argus-skill/maintenance/worktrees/4211b892264d`。当时的验证被 cwd
  误导(在开发仓库目录里 import,cwd 先于 .pth 命中——正是第六节备忘里
  提醒过的那个陷阱)。在启动器修好之前,**不要用 `~/.local/bin/argus`
  重启任何守护进程**,用 `argus-runtime-latest/.venv/bin/argus`。
- **肇事命令不是部署步骤,也不是显式 `--user`**。取证:维护任务的
  Engineer 执行的是 `python3.11 -m pip install -e .`(没有 `--user`),
  系统 site-packages 不可写,pip 静默回退 "Defaulting to user
  installation" 落进 user-site 并重写 `~/.local/bin` 下的启动器。第十一节
  写的 `pip install -e --user` 口径不准——正因为回退是静默的,单靠"别加
  --user"教育不了它,要在环境层面封死。

### 本次落地的四件事

1. **源码根预检 knob `ARGUS_SKILL_SOURCE_ROOT`**(`core/runtime_identity.py`
   的 `configured_source_root()` / `source_root_preflight_error()`,
   `core/knobs.py` 注册为 path knob)。解析顺序:进程 env > 持久化 knob
   (`~/.argus-skill/config.json`)> 未配置(严格 no-op,行为与现在一致)。
   一旦配置,daemon 启动预检发现加载的包 resolve 到别的 checkout 即拒启
   (rc=2,在 knob-store 门之后、backend probe 之前,
   `daemon/_life_worker_run.py`),webapi `serve()` 同样拒绝
   (RuntimeError,`webapi/server.py`)。两侧路径都 resolve 后再比较,
   经 symlink 到达同一部署根不误伤;配置的目录不存在也接受——预检
   fail-close,写错只会把启动拒掉,不会放行错误的 checkout。被劫持的
   启动器从此**起不来**,而不是安静地跑旧代码。
2. **sandbox 加固**(`core/sandbox.py`)。`forbidden_write_roots()` 新增
   `~/.local/lib`(封死**所有** python 版本的 user-site——肇事 pip 跑在
   系统 3.11 下而非本 venv 的解释器;`~/.local/state`、`~/.local/share`
   是兄弟目录不受影响)、`~/.local/bin`(启动器本体),并保留
   per-interpreter 的 `_user_site_packages`(覆盖 PYTHONUSERBASE 指到
   home 之外的情形)。`sandboxed_child_env()` 与
   `configure_framework_python_env()`(`core/runtime_env.py`)都钉
   `PIP_USER=0`,后者接在 daemon boot(`_rf_bootstrap_environment`)和
   CLI `main()` 入口的 `os.environ` 上——默认的 dangerous_yolo 子进程
   原样继承 `os.environ`,只有进程级钉扎能到达它。两处接线各有真实调用
   bootstrap 入口的测试(`tests/daemon/test_life_worker.py`、
   `tests/apps/test_cli_parser.py`),删掉任一处调用即红。
3. **维护 playbook 明令**(engineer 执行第 6-8 条、reviewer 检查项)。
   禁止对框架做任何 pip install(`-e .`、`--user`、任何写 user-site 或
   `~/.local/bin` 的形式);测试的正确姿势是 worktree 根下
   `"${ARGUS_SKILL_PYTHON:-python3}" -m pytest <target> -q`(默认 cwd
   就能遮住部署包;若环境设了 PYTHONSAFEPATH——safe-mode 沙箱会设——
   前缀 `PYTHONPATH="$PWD"`);确需第三方依赖时进一次性 venv 或
   `pip install --target`。
4. **`tests/test_math_lean_async.py` 判死 fast-fail**。`_settle` /
   `_wait_until` 增加 doomed 探针:worker 死了(锁不在、submit 记录的
   pid 也不在)立即失败并引用 worker.log 尾部,不再把 60 秒 deadline
   白等完——第十一节第 1 条 (c) 的 14×60 秒消除;附一条复现测试
   (PYTHONPATH 影子 portalocker,断言快速失败且报根因)。

### 启用防护(需操作员执行一次,knob 默认未配置即不设防)

- 持久化(推荐,重启不丢):
  `/data/v-boxiuli/argus-runtime-latest/.venv/bin/python -c "from
  argus_skill.core.knob_store import write_persisted_knob;
  write_persisted_knob('ARGUS_SKILL_SOURCE_ROOT',
  '/data/v-boxiuli/argus-runtime-latest')"`
- 或按进程 env:启动命令前
  `export ARGUS_SKILL_SOURCE_ROOT=/data/v-boxiuli/argus-runtime-latest`
  (显式 env 优先于持久化值,与其余 knob 的先例一致)。

### 运维注意

- knob 配置后,从 dev checkout(`/data/v-boxiuli/Argus`)起 daemon /
  webapi 会被拒(这是预期的)。要在 dev checkout 上调试,
  `export ARGUS_SKILL_SOURCE_ROOT=/data/v-boxiuli/Argus` 或用独立的
  `ARGUS_SKILL_HOME`。
- canary / 多根部署:按进程 env 覆盖各自的根即可,持久化值只作缺省。
- 部署交接(handoff)自带 env 覆盖,不受持久化 knob 影响(评审时已验证)。

### 残余风险(诚实)

- **显式 `pip install --user` 在默认 yolo 模式下技术上仍拦不住**:
  `PIP_USER=0` 只封"静默回退",pip 的 CLI 旗标优先于 env。现有防线是
  playbook 禁令 + reviewer 检查项,加上源码根预检兜底——真装了,损害
  止于下一次启动被拒,而不是安静跑旧代码。
- safe-mode 沙箱设 PYTHONSAFEPATH,worktree cwd 不再自动遮住部署包;
  正确的测试姿势(`PYTHONPATH="$PWD"`)已写进 playbook,但跑错了的
  症状是"测的部署包而非 worktree 代码",不易察觉。
- `config.json` 损坏时:诊断路径(`runtime_identity()`,供 webapi
  /api/meta、daemon status 消费,它们只 catch OSError)宽容回退为
  "未配置";预检保持严格,KnobStoreCorruptError 本身就是拒启理由。

### 测试

定向合跑 9 个受影响文件(engineer_sandbox / runtime_identity /
runtime_env / knob_store / life_worker / server_m0 / maintenance
vertical / config_help / cli_parser):318 passed,0 failed(18.5s,
`.venv/bin/python -m pytest`)。全量 `tests/`:3 failed 全部为已知预存
(`tests/apps/test_cli_ask.py` 两条顺序相关、`tests/test_role_library.py`),
quant 系列缺依赖已排除;第十一节列的 `tests/webapi/test_pairing.py`
本次通过。

### 未代答、未动的事

- 两张 `paused_operator` 的维护决策卡未代答,留给操作员:
  `690fb430f6b6`(终稿认证消费修复本体,即那份 WIP 对应的任务)、
  `6062621ef4e9`(CI 基线恢复)。
- worktree `4211b892264d` 的 WIP 已完整保存为提交 `385ad3c1a`
  (在 argus-runtime-latest 的对象库里),worktree 本体未动。
- 维护积压共 14 个 pending(含一个指向 `/data/chenxi/argus-skill` 的,
  未动)。

启动器本体的修复与重新部署记录见下一提交追加。

### 部署记录(2026-09-05,`12ba2a8b7` 上线)

- **启动器隔离**:被劫持的 user-site 安装整套 mv 进
  `~/.local/share/argus-quarantine-2026-09-05/`(未 rm)。按 dist-info
  RECORD 移的不止交办清单三样,而是该次安装拥有的全部文件:
  `~/.local/bin/{argus,argus-doctor,argus-plugin-server,argus-skill}`、
  site-packages 下的 `_editable_impl_argus_skill.pth`(指向
  `~/.argus-skill/maintenance/worktrees/4211b892264d`)、
  `argus_skill-0.1.1.dist-info/`、`argus_skill/`(仅 `_frontend` 资产,
  但作为 namespace package 仍可被 import,不移则劫持不解)、
  `argus_doctor.py` 及其 pyc。验证:`/usr/bin/python3.11 -c "import
  argus_skill"` 报 `ModuleNotFoundError`。
- **启动器重建**:`~/.local/bin/argus` 重写为两行 shim
  (`#!/bin/sh` + `exec /data/v-boxiuli/argus-runtime-latest/.venv/bin/argus "$@"`),
  `argus --help` 正常出 usage。
- **knob 落盘**:`config.json` 先备份为
  `~/.argus-skill/config.json.bak-2026-09-05`,再经
  `normalize_cockpit_knob_value` + `write_persisted_knob` 写入
  `ARGUS_SKILL_SOURCE_ROOT=/data/v-boxiuli/argus-runtime-latest`;
  与备份 diff 仅多此一键(外加文件尾换行),既有键一字未动。
- **runtime 切换**:`/data/v-boxiuli/argus-runtime-latest` fetch 后
  detach 到 `12ba2a8b7`(自 `0f85d2c41`),依赖未变,venv 复用。
- **四守护进程滚动重启**(逐个 kill → 原参数 `--daemon --backend
  copilot --resume <sid> --resume-continuous` 从各自 project_workdir
  重起 → 轮询 status 核对),四个全部
  `revision=12ba2a8b7b0c`、`source_root=configured_source_root=
  /data/v-boxiuli/argus-runtime-latest`、`source_root_matches_config=true`:
  - `s-72fa9517`(argus-iclr-observation-v2/run-08):74995 → 222557
  - `s-3e28f79c`(ai-research-open-20260902):77837 → 261014
  - `s-80c507d6`(argus-capability-tests/write-01):83390 → 269163
  - `s-0b1c7fa1`(argus-capability-tests/idea-01):92077 → 280295
- 未触碰:pid 3554795 / 3658737(第 5/6 个守护进程,含持有待批
  operator 决策卡者)与 objective 守护进程 409548,事后 ps 确认三者
  仍存活;两张 `paused_operator` 决策卡、maintenance worktrees 均未动。
