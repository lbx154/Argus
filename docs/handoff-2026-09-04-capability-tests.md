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
- playbook 的 .md 每轮从磁盘读取,改动即刻生效;.py 改动需重启守护进程
  (`kill <pid>` 后 `argus --resume <session> --daemon --resume-continuous`)。
- 给运行中的任务发操作员消息:`argus_skill.core.transcript.append_turn(
  life_dir, "operator", text, message_id=...)`,life_dir 为
  `~/.argus-skill/projects/<session>/`。
