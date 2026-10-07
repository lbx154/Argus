# lbx154/Argus#124：把研究主题或否定指令误当成创建 portfolio

[返回实验总览](README.md)

## 1. 一句话解释

系统原本想阻止 Agent 重复创建一套研究方案集合，于是新增了 portfolio 拦截规则。
但在受影响的任务入口，“研究投资组合优化”和“不要创建另一个 portfolio”也因包含
`portfolio` 一词而被拦住了。

**原始报告结论为 `REGRESSION`，局部发现为 `F1`。** 证据来自历史两个版本的真实
Planner admission 路径，不代表已测量真实模型的误拦频率、最终任务成功率或线上影响。
本文件是原 50-PR 实验的案例展开，不增加样本数，也没有重跑实验或检查当前最新 main。

## 2. PR 与精确对照

| 项目 | 值 |
|---|---|
| PR | [lbx154/Argus#124](https://github.com/lbx154/Argus/pull/124) |
| 标题 | Prevent duplicate research idea portfolios |
| 合入时间 | 2026-09-14 07:17:16 UTC |
| Base | `5b25300287296c40a8d6c58306a3923dd26c2937` |
| Candidate merge | `2b9cfc15aeedc13d02774217b7200580c3477588` |
| 集成差异 | 5 个路径，包含 Planner 策略与模型可见的 Engineer 提示 |
| 原始运行器状态 | `completed`，不属于后处理恢复的 9 份拒收报告 |
| 原报告候选发现 | `F1`；其他提示行为仍有未解决的不确定性 |

比较的是实际 merge 与其第一父提交。PR 希望由 runtime 管理唯一的 canonical Idea
portfolio，避免 Planner/Engineer 再创建一套 Team、路线和 selector。
**阻止真正的重复方案选择是合理目标，问题在于把合法的独立文献工作也当成重复创建。**

## 3. 可复现的上下文与两种输入

不是所有出现 `portfolio` 的任务都会被拦。到达该见证需要具体的阶段、路径和任务条件：

| 条件 | 见证中的值 |
|---|---|
| Vertical / stage | `research` / `idea` |
| Workflow / target | `staged` / `publishable` |
| 方向 | `locked`，不是要求重新选择研究方向 |
| 已选 idea | 无 |
| Task 的 Team 所有权路径 | `.argus/teams/citation-audit` |
| 工作内容 | 两项 source-only 审查，分别写 `source-a.md`、`source-b.md` |
| 约束 | 不执行代码、不做 idea selection，要求 independent review |
| 其他干扰项 | 没有已有 backlog 或历史失败 blocker |

两个独立变体保留上述合法工作，只改变文字：

**变体 A：研究主题中含有该词。**

```text
Audit two primary sources on portfolio optimization
```

意思是“审阅两篇关于投资组合优化的原始文献”，不是创建 Argus 的 idea portfolio。

**变体 B：明确禁止再创建一套 portfolio。**

```text
Do not create another idea portfolio or selector.
```

这句话追加到中性文献审查任务的目标中。它是在禁止重复创建，不是在提出创建请求。

## 4. 实际的新旧版本表现

| 真实 admission 边界的观测 | Base | Candidate |
|---|---|---|
| `portfolio_required(project)` | `false` | `false` |
| 不含该词的中性审查任务 | 1 个 pending item | 1 个 pending item |
| 主题含 `portfolio optimization` 的审查 | 1 个 pending item | **0 个 pending item** |
| 明确禁止另建 portfolio 的审查 | 1 个 pending item | **0 个 pending item** |
| 两个误拦变体的事件 | 无该拒绝事件 | `life.planner.task_skipped` / `vertical_task_policy` |

Candidate 的拒绝理由是：

```text
the research runtime owns the canonical Idea portfolio; omit all
`.argus/teams/...` paths and let the runtime-provided portfolio complete
```

这里的 pending item 是生产函数构造的**待入队条目**。没有运行真实 Team worker，
也没有把它扩大描述成已测量的持久队列提交、无限重规划或整个 campaign 失败。

## 5. 根因与独立的正确性依据

实际调用链：

```text
TaskSpec
-> _pc_build_pending_items
-> task/path normalization
-> load_vertical_contract
-> VerticalContract.planner_task_issues
-> research.stages.planner_task_issues
-> skip event，并省略 pending item
```

新增的
[研究阶段策略](https://github.com/lbx154/Argus/blob/2b9cfc15aeedc13d02774217b7200580c3477588/argus_skill/verticals/research/stages.py#L692-L735)
忽略 `project_root`，在满足阶段和 Team 路径条件后，扫描小写化的任务文字。
`portfolio` 的子串出现即可命中 `explicitly_portfolio`，未区分主题、否定与实际所有权意图。
[原有入队消费者](https://github.com/lbx154/Argus/blob/2b9cfc15aeedc13d02774217b7200580c3477588/argus_skill/life/supervisor/_planning_cycle_enqueue.py#L625-L670)
随后执行过滤，故这个错误不是仅存在于静态字符串分析。

保护的性质不是“旧版本接受就必须继续接受”，而是：
**合法的、有独立证据产物的 source-only 审查，不能仅因主题或否定句就被当成替代方案集合。**
它有未改变的项目契约支持：

- [Team Lead 契约](https://github.com/lbx154/Argus/blob/2b9cfc15aeedc13d02774217b7200580c3477588/argus_skill/builtin_skills/agent-team-lead.md#L9-L35)
  允许文件互不冲突的独立 Team 工作。
- [Idea playbook](https://github.com/lbx154/Argus/blob/2b9cfc15aeedc13d02774217b7200580c3477588/argus_skill/verticals/research/skills/research-idea-playbook.md#L21-L34)
  允许对已锁定方向进行有界、source-only 的研究定位。
- [Portfolio 的适用条件](https://github.com/lbx154/Argus/blob/2b9cfc15aeedc13d02774217b7200580c3477588/argus_skill/verticals/research/idea_portfolio.py#L40-L50)
  将 locked 方向等情形排除在强制 portfolio formation 之外。

两侧的任务和初始 pipeline-state 哈希一致，loader、状态解析、路径规范化、策略 hook
和过滤记录均实际执行。没有用手写的新旧分类器代替生产实现。

## 6. 正常对照与配对结果

正常对照帮助区分合理的新限制与误拦：

- 不含歧义词的相同审查在两边都被接纳。
- 真正请求另一套十二路线、十二审查和 selector 的任务在 candidate 被拒绝，这是预期改进。
- 同样的主题审查位于相邻 Experiment 阶段时，两边都被接纳。
- 准备和提示构造的正常路径两边保留了相应边界，但不因此声称真实模型行为相同。

| Probe | Base | Candidate | 每侧命令耗时 |
|---|---|---|---|
| `planner-policy` | 14 passed，exit 0 | 6 passed / 8 failed，exit 1 | 0.866s / 1.166s |
| `planner-enqueue` | 3 passed，exit 0 | 1 passed / 2 failed，exit 1 | 1.166s / 1.166s |
| `preparation-prompt` | 8 passed，exit 0 | 8 passed，exit 0 | 0.966s / 1.016s |

`planner-policy` 包含跨四种研究模式的边界矩阵；`planner-enqueue` 是上述 locked 场景的
实际待入队见证。多个失败用例是同一机制的重复或变体，不算多个独立缺陷。
既有 portfolio 测试为 base 9 条、candidate 14 条，集合不同，不作为同一 oracle 的对照。

完整 PR 分析约 7.41 分钟，共运行 4 组配对 probe，无超时。
上表秒数只是命令执行时间，不包含 Copilot 阅读、推理和构造案例的时间。

## 7. 保存证据与重新复现

本机原始证据根目录：

```text
/home/chentianyu/argus-experiments/pr-regression-50-20260916/results/pr-124/
```

| 相对路径 | 用途 |
|---|---|
| `REPORT.md`、`report.json`、`runner-result.json` | 原始分析、三个假设中的局部发现 F1、运行器状态 |
| `base-probes/test_portfolio_boundary.py`、`candidate-probes/test_portfolio_boundary.py` | 原始文件名下保存的同一测试 |
| `evidence/probe_source.py` | 新 probe 的独立保存副本 |
| `evidence/planner-enqueue/receipt.json`、`base.log`、`candidate.log` | 精确版本、命令、退出码、哈希和实际入队边界观测 |
| `evidence/planner-policy/receipt.json` | 边界矩阵与正常对照 |
| `evidence/preparation-prompt/receipt.json` | 准备和 prompt 构造证据，不是真实模型执行 |
| `evidence/comparison.json`、`source-evidence.json`、`observations-summary.json` | 输入身份、独立契约、源码片段和结果索引 |

新测试源文件的 SHA-256：

```text
3ee9774b936690dff468071ec80971af01a0904592a0cd06182b5644a863ff4e
```

将历史两侧源码与 probe 恢复到独立容器后，在各自源码根执行：

```bash
python -m pytest -q -s -p no:cacheprovider \
  tests/_regression_probe/test_portfolio_boundary.py -k enqueue_bounded_audit
```

环境为 Python 3.11.16、pytest 8.4.2、隔离 HOME/Argus 状态、每侧 120 秒时限。
不要直接在现有活动项目里执行。Probe 复用了已有 supervisor fixture，但只调用确定性
待入队方法；无关上下文、历史失败等辅助项按 fixture 隔离，未替换被测策略与过滤决策。
新 probe 的 backend 拒绝每次 `run_exec`，并禁止子进程和 socket；没有真实模型或 worker。

## 8. 与 lbx154/Argus#53 的相似点和结论限制

两者都把文本中的弱信号升级成了执行控制：

| 案例 | 弱信号 | 被错误执行的判断 |
|---|---|---|
| [lbx154/Argus#53](pr-053-quoted-control-markers.md) | 正文引用了 `done` | 当前任务已经完成 |
| lbx154/Argus#124 | 主题或否定句出现 `portfolio` | 当前任务要另建研究方案集合 |

合理的修复方向应保留对真正重复 portfolio 的约束，同时结合适用项目状态和实际所有权
冲突判断；不能把所有包含该词的任务都拒绝。本案例没有实施或验证该修复。

原始运行器接受了报告并执行其源码不可变性审计，但这仍不等于独立裁决 oracle 的语义。
真实模型如何理解新增的 canonical-Team 指令、是否改善重复方案创建，以及实际任务
误拦频率均未测量。本文只整理历史配对证据，不声称当前最新 main 仍有该问题。
