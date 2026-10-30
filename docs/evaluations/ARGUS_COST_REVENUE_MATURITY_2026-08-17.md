# Argus 成本、收入与成熟度审计 — 2026-08-17

## 结论

- **没有证据支持一个可泛化的“Argus 总体节省比例”。**
- 在 2026-08-07 的同任务小样本中，mission 相对 fresh 的墙钟下降
  14.58%，但模型费用从每次 $0.028598 增至 $0.029934，增加 4.67%。
- 如果明确假设人工时薪为 $50，且人工实际投入为墙钟的 25%，人工与模型
  合计成本由 $0.674143 降至 $0.581337，**情景节省 13.77%**。这不是实测
  人工节省，因为实验没有记录人工工时。
- 最近六个完整自然月（2026-02 至 2026-07）没有可核验的收入凭证。六个月
  状态均为**未验证**，不是 0，不能绘制真实数值曲线，也不能用估值替代。
- 证据成熟度评分为功能 3/5、质量证据 3/5、成本证据 2/5、运维 2/5、
  商业数据 0/5。技术证据平均 2.5/5，适合受监督试用，不足以证明无人值守
  生产成熟或商业验证完成。

## 1. 成本

### 1.1 实测与日志推导

2026-08-07 的 controlled live experiment 使用 Pi 0.84.1、GitHub Copilot
和 `gpt-5-mini`，在两个小型 Python 仓库上每策略运行 4 次。成功要求
Reviewer `done` 且独立 held-out 检查通过。

| 指标 | fresh | mission | 变化 |
| --- | ---: | ---: | ---: |
| 平均墙钟 | 185.917 s | 158.804 s | -14.58% |
| 平均模型费用 | $0.028598 | $0.029934 | +4.67% |
| 平均显式 prompt | 13,434.25 tok | 8,934 tok | -33.50% |
| 平均重复仓库读取 | 7.75 | 4.50 | -41.94% |
| 联合成功 | 2/4 | 4/4 | +50 个百分点 |

可确认的是执行时间、显式 prompt 和重复读取下降；模型费用没有下降。
provider-reported input Token 反而增加 43.8%。墙钟不等于人工工时。

同一功能在 2026-08-08 real canary 中出现反向质量结果：fresh 为 1/2，
mission 为 0/2，尽管 mission 墙钟下降 29.37%、provider input 下降
46.54%。该 canary 没有模型费用，且 mission 两次 held-out 均失败，因此
不能把资源下降解释为有效成本节省，生产默认仍应保持 `fresh`。

### 1.2 人工与模型合计情景

公式：

```text
合计成本 = 模型费用 + 时薪 × 墙钟秒数 ÷ 3600 × 人工投入比例
节省率 = (fresh 合计成本 - mission 合计成本) ÷ fresh 合计成本
```

结果见 `ARGUS_COST_SCENARIOS_2026-08-17.csv`。

| 时薪 | 人工投入占墙钟 | 合计节省 |
| ---: | ---: | ---: |
| $25/h | 25% | 13.02% |
| $50/h | 25% | **13.77%** |
| $100/h | 25% | 14.17% |
| $25/h | 100% | 14.17% |
| $50/h | 100% | 14.37% |
| $100/h | 100% | 14.48% |
| 不计人工 | 0% | **-4.67%** |

这些是透明的情景测算，不是人工工时实测。以 $50/h 计算，只要人工投入超过
墙钟的约 0.355%，墙钟差额才足以覆盖 $0.001336 的模型费用增量；这个
break-even 仍依赖“人工投入随墙钟同比变化”的假设。

### 1.3 其他成本信号

- P1-03 的自然按需 Skill canary 为 10/10 held-out 通过，已知模型费用
  $0.035752/run；包含真实选择调用的 matcher+injection 基线为 4/4，
  $0.03964/run。局部方向性节省为 9.81%，但样本数不同，不能外推到整个
  Argus。
- 731-task SWE-Bench Pro 汇总称 Argus 约 78%、Direct Copilot 约 59%，
  即质量约高 19 个百分点；Argus 总 Token 约为 Direct 的 1.41 倍。按
  `Token/成功任务` 的近似代理计算，Argus 约高 6.65%，不支持算力节省。
- Argus 的 W19–22 mature 窗口相对 W1–6 start-up 窗口，solve input
  Token/任务下降 21.11%，active workflow seconds/任务下降 14.92%。
  这是不同任务窗口间的纵向信号，不是与 Direct 的因果对照，也不包含完整
  端到端时间或人工工时。
- Erdős 轨迹的 18 个 bounded missions 共记录 $36.7656135；direct
  工作占 56.14%，direct+support 占 89.68%。这是成本归因，不是节省率。

## 2. 六个月真实收入

月份采用当前日期之前的六个完整自然月。

| 月份 | 收入 | 状态 |
| --- | ---: | --- |
| 2026-02 | — | 未验证 |
| 2026-03 | — | 未验证 |
| 2026-04 | — | 未验证 |
| 2026-05 | — | 未验证 |
| 2026-06 | — | 未验证 |
| 2026-07 | — | 未验证 |

审查的仓库材料中没有发票、收款或会计记录。空值不能改写成 0，也不能用估值
填充。机器可读数据在 `ARGUS_REVENUE_2026-02_2026-07.csv`；SVG 明确显示
六个 N/A 点而不伪造折线：
`ARGUS_REVENUE_2026-02_2026-07.svg`。

要生成真实曲线，至少需要每月统一口径的已收现金或权责发生制收入、币种、
退款和税费处理规则，以及对应凭证。取得这些数据前不能计算增长率、累计收入
或趋势。

## 3. 成熟度

评分是审计判断，不是实测指标。

| 维度 | 评分 | 依据 |
| --- | ---: | --- |
| 功能 | 3/5 | 多角色、Reviewer、Skill、持久 daemon 和 bounded mission 均有真实运行；部分 session 策略仍只适合诊断。 |
| 质量证据 | 3/5 | 有 731-task 汇总和 held-out canary；缺任务级数据、区间和原始评分，且小样本结果反转。 |
| 成本证据 | 2/5 | 有真实 Token、费用、时间和读取汇总；没有人工工时与完整端到端成本，方向也不一致。 |
| 运维 | 2/5 | Doctor 与本次 daemon 运行成功；历史上有 stale-stage、重复 checkpoint 和 Reviewer 假阳性，生产仍使用保守默认。 |
| 商业数据 | 0/5 | 没有收入、客户、合同、留存或单位经济数据。0/5 表示证据缺失，不表示收入为 0。 |

总体定位：**受监督的技术试用 / beta**。可以继续做内部工程任务和受控评测，
不应据现有证据承诺稳定节省、无人值守交付或商业成熟。

## 4. 试运营与反馈数据

现有材料是内部 canary 和 benchmark，不是客户访谈或付费用户反馈。

- P1-02 controlled session policy：fresh、mission、rolling 各 n=4。
  mission 初次为 4/4 联合成功，fresh 为 2/4；rolling 为 1/4。
- rolling 修复 smoke：n=2，2/2 通过；尚未重跑完整矩阵。
- P1-02 real canary：fresh n=2，1/2；mission n=2，0/2。该结果否决了
  mission 成为生产默认。
- P1-03 自然 Skill canary：n=10，正确 Skill 打开 10/10，错误 Skill
  0/10，held-out 10/10。
- P1-03 matcher+injection：n=4，4/4 通过；单次费用高于自然按需路径。
- Reviewer 风险：一个 control 条件 Reviewer 4/4 `done` 但 held-out
  0/4；real canary 中 mission 也两次 Reviewer `done` 后 held-out 失败。
- SWE-Bench Pro：731 tasks，准确率为近似汇总，原始任务级结果未包含在
  本次材料中。

因此可用的“反馈”是：可用性和质量有积极信号，但 Reviewer 假阳性、策略
稳定性和成本口径仍是主要问题。没有客户满意度、留存、NPS、工单或付费转化
数据。

## 5. 本次 Argus 实验

本机使用 Argus 0.1.2、Pi 0.84.1 / GitHub Copilot backend，新建独立
workdir，只输入六个脱敏汇总文件。任务要求生成中文证据审计和可复算 CSV，
禁止访问其他仓库、网络、凭证或修改产品代码。

结果：

- `doctor --advisor none --verify`：全部阻塞检查通过；仅有非阻塞的
  path-memory 提醒。
- bounded campaign：约 321 秒，从启动至最终 Reviewer 完成并安全停止。
- history：2 个内部 mission 完成。
- 记录成本：$1.69。
- 输出：`outputs/evidence_audit.md` 和 `outputs/metrics.csv`。
- Reviewer：`done`；执行：`completed`。
- Needs you：0；内容决策人工介入：0。
- 最终安全 drain-stop 后 daemon 已停止，0 pending / 0 running / 0 paused。

这次运行证明本机从预检、daemon、Engineer、Reviewer、持久状态到安全停止的
链路可用。它没有无 Argus 基线，不能用于计算节省率。运行中没有复现出需要
修改产品代码的 bug，因此没有为本报告添加产品修复。

## 6. 复现

输入材料：

- `ARGUS_P1_02_P1_03_LIVE_EXPERIMENT_2026-08-07.md`
- `ARGUS_P1_02_P1_03_CANARY_2026-08-08.md`
- `technical_report/evidence/swebench_pro/README.md`
- `technical_report/evidence/swebench_pro/unified_experiment_summary.json`
- `technical_report/evidence/swebench_pro/argus_six_wave_windows.csv`
- `technical_report/evidence/erdos_trace/efficiency_audit.csv`

运行模板：

```bash
ARGUS_BIN=/absolute/path/to/argus-skill
MISSION=/absolute/path/to/isolated-mission

"$ARGUS_BIN" doctor --advisor none --verify
cd "$MISSION"
"$ARGUS_BIN" --status --life-dir "$MISSION/state" --project-root "$MISSION"
ARGUS_SKILL_SAFE_MODE=1 ARGUS_SKILL_REQUIRE_POST_TASK_LEARNING=0 \
  "$ARGUS_BIN" --daemon --continuous --bounded --new --backend pi \
  --objective-file "$MISSION/OBJECTIVE.md" \
  --project-root "$MISSION" --life-dir "$MISSION/state"
"$ARGUS_BIN" --status --life-dir "$MISSION/state" --project-root "$MISSION"
"$ARGUS_BIN" --daemon-stop --drain \
  --life-dir "$MISSION/state" --project-root "$MISSION"
```

完成条件是输出文件存在、数字可复算、Reviewer `done`、无 Needs you，并且
daemon 已安全停止；不能只依赖进度文字。

## 7. 限制

- 关键实验多为单后端、少任务、小样本；不能给稳定总体效应。
- 仓库只保留脱敏汇总，缺少原始 trace、任务级结果、方差和置信区间。
- SWE-Bench 的 Direct 逐 Wave Token/时间缺失。
- active workflow time 排除了等待、环境准备、外部验证、基础设施恢复和
  任务后维护。
- 没有实测人工工时，因此 13.77% 只能作为明确假设下的情景值。
- 没有真实收入和客户反馈数据，商业结论只能标记为未验证。

> 迁移去向（2026-10-30）：本文保留当时的数字、代码样例与路径；托管客户端、服务、管理页和桌面构建现由 [argus-hosting](https://github.com/lbx154/argus-hosting/blob/feat/trial/README.md) 维护。
