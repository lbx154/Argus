# 50 个历史 Argus PR 的回归分析实验

实验日期：2026-09-16。整理日期：2026-09-17。

## 1. 结论与阅读边界

50 个 Copilot 分析会话均正常结束，没有会话超时。按原始模型报告的结论统计：

| 原始模型结论 | PR 数 | 含义 |
|---|---:|---|
| `NO_REGRESSION_FOUND` | 12 | 在已分析范围内未发现回归，不代表全局安全 |
| `REGRESSION` | 11 | 报告给出了满足其适用条件的双版本回归见证 |
| `UNCERTAIN` | 27 | 仍有环境、模型行为或其他重要路径未覆盖 |
| 合计 | 50 | 三种结论互斥；未改写模型的原始判定 |

其中 4 份 `UNCERTAIN` 报告也包含局部回归见证。因此，按发现而非整份报告的结论统计，
共有 **15 个 PR 包含 21 条有配对证据的候选发现**。这不是 21 个经过独立裁决的缺陷，
也不能直接解释为 21 个独立根因。

本目录展开用户选择的四个混合判定案例，并补充已讨论过的 lbx154/Argus#53、lbx154/Argus#124：

| 案例 | 有证据的局部行为变化 | 整体判定 |
|---|---|---|
| [lbx154/Argus#17：事件重复回放](pr-017-event-replay.md) | 日志轮转期间，不同时刻的文件快照被拼接，重复返回同一个事件 | `UNCERTAIN` |
| [lbx154/Argus#29：完成回执被旧状态覆盖](pr-029-completion-receipt.md) | 同一次任务的旧 running 记录清空已完成视图中的摘要和完成时间 | `UNCERTAIN` |
| [lbx154/Argus#31：路径别名拆分锁身份](pr-031-session-lock-alias.md) | 同一状态目录的长路径与短别名选中不同锁文件，导致并发更新丢失 | `UNCERTAIN` |
| [lbx154/Argus#38：持久化文件名缺少迁移](pr-038-persistent-filename-migration.md) | 升级后重复分配已有任务，并丢失对子任务记录及退出证据的查找 | `UNCERTAIN` |
| [lbx154/Argus#53：引用示例变成控制指令](pr-053-quoted-control-markers.md) | Markdown 示例被误当作完成标记或当前操作选项；附 F1 的后续 main 复现 | `REGRESSION` |
| [lbx154/Argus#124：关键词误拦文献任务](pr-124-portfolio-keyword-admission.md) | Idea 阶段把研究主题或否定句中的 portfolio 当成另建方案集合，丢弃合法待入队任务 | `REGRESSION` |

这里的“有明确证据”指保存了触发路径、配对观测和适用性质，不等同于已完成独立语义裁决。
本次文档整理没有重跑案例，也没有检查它们是否仍存在于 2026-09-17 的最新版本。
案例主体均针对文档中列出的历史 merge commit。补充的 lbx154/Argus#53 文档还保存了
2026-09-16 的单独 main 追查；它不是对 2026-09-17 最新代码的新检查。

## 2. 实验问题与执行方式

研究问题是：**只使用 Copilot 和一份回归分析 Skill，能否用低成本静态分析或定向 probe，
发现历史 PR 引入的 harness 行为回归？**

本轮不是整体能力 benchmark，没有证明 Argus 某两个版本的任务成功率差距，也没有测量
真实模型生成触发输入的频率。

| 项目 | 配置 |
|---|---|
| 分析器 | 每个 PR 一个 GitHub Copilot CLI 会话 |
| CLI / 模型 | Copilot CLI 1.0.85，`gpt-6-astra`，reasoning effort `high` |
| Skill | `agent-harness-pr-regression` |
| Skill 冻结 SHA-256 | `987c6ed489b698f9c862fef554c903c89f22dc715f1b46b83c1c221de25b366d` |
| PR 对照 | 实际 merge 的第一父提交作为 base，merge commit 作为 candidate |
| 最大并发 | 5 个 Copilot worker |
| 每个 worker | 2 CPU、4 GiB RAM、256 个进程上限、20 分钟分析时限 |
| 每组配对 probe | 两个版本分别执行，每侧最多 120 秒；每 PR 最多 6 组 |
| 短路规则 | 影响可忽略且有消费者分析证据时，不要求生成测试 |
| 模型介导的变化 | 固定回复不能证明真实模型行为安全；证据不足保留不确定性 |

每个 worker 有独立容器、源码快照、HOME 和状态目录。容器不挂载真实 Argus 状态、
宿主 home 或 Docker socket；分析网络经受限代理访问 Copilot/GitHub 服务。
没有启动真实 Argus 任务/daemon，也没有使用其他 agent 或额外模型。分析容器使用预先安装的
依赖，worker 没有临时安装缺失依赖来补齐测试。
这里的“没有额外模型”不否认 Copilot 本身使用 LLM；指被测 Argus 没有实际调用模型。

资源隔离限制了对共享宿主机的影响，但不意味着宿主硬件完全独占。实验结束后，专用容器、
网络、镜像和工作源码副本已清理；报告、diff、probe、日志和源代码镜像保留。

相关本地实现（不包含在本次文档提交中）：
`integrations/agent-skills/agent-harness-pr-regression/SKILL.md`、
`experiments/pr_regression_50/README.md`。
精确复现原实验时应使用实验目录中的冻结副本，而不是后来维护过的 runner。

## 3. 抽样范围

采样时取得 `lbx154/Argus` 的 113 个历史 PR，69 个满足以下条件：

- 已合入 `main`，且 merge 可从采样时冻结的 main 到达。
- 存在可验证的双父 merge，第二父提交与记录的 PR head 一致。
- 能明确构造“合入前第一父提交 / 合入后 merge”的对照。

将这 69 个候选按合入时间排序，分为 5 个等数量分层，每层均匀随机抽取 10 个，
随机种子为 `20260916`。共得到 50 个 PR，合入时间覆盖
2026-08-07 至 2026-09-14。

采样时 main：`cc934eed184e32277a56b224a7785a2f28b7621f`。
分析工具所在工作树 HEAD：`c2f7b8b234f6cf002ff13c8cadcfdb894181ad15`。
工具工作树并不是所有 PR 的被测版本。

未合入、非 main 合入以及无法可靠还原的 squash/rebase 等 PR 不在本轮候选内。
因此，本实验不代表全部 PR 的无偏总体评估。PR title/body 是采样时 API 快照，
未证明是合入当时的文字，也没有作为验收真值。历史 CI 结论未作为判断 oracle。

## 4. 执行量与耗时

| 指标 | 结果 |
|---|---:|
| 正常退出的 Copilot 会话 | 50/50 |
| 会话级超时 | 0 |
| 实际短路 | 10 个 PR |
| 定向分析 | 40 个 PR |
| 配对 probe | 149 组 |
| 总墙钟时间 | 78.89 分钟 |
| 每 PR 中位耗时 | 7.74 分钟 |
| 每 PR 最大耗时 | 11.14 分钟 |

每 PR 耗时包括源码准备、Copilot 阅读/推理、工具操作和报告生成，不只是测试命令时间。
一次配对 probe 也不等于一个测试函数：它可能运行多个测试，或在缺依赖时停止。
149 组 probe 不能解释为 149 次成功的行为覆盖。

10 个短路均归入 `NO_REGRESSION_FOUND`；另外 2 个该结论来自定向分析。
例如品牌图片、未被运行时消费的说明文字，以及实际集成树无变化的修改可以短路。
模型会读取的 Skill Markdown、prompt 和配置没有仅因扩展名而被豁免。

## 5. 原始运行器与后处理统计为什么不同

原始冻结运行器接收了 41 份报告，拒收了 9 份；全部 50 个 Copilot 进程都退出为 0。
拒收是报告处理状态，不是 GitHub PR 被拒绝，也不是 Copilot 超时。

| 拒收原因 | PR |
|---|---|
| 整体 `UNCERTAIN` 与局部 `CONFIRMED_REGRESSION` 被过严的一致性规则视为冲突 | lbx154/Argus#17、lbx154/Argus#29、lbx154/Argus#31、lbx154/Argus#38 |
| 使用容器绝对路径 `/work/evidence/...`，运行器未映射到保存位置 | lbx154/Argus#49、lbx154/Argus#55、lbx154/Argus#73、lbx154/Argus#81、lbx154/Argus#116 |

| 统计口径 | 未发现回归 | `REGRESSION` | `UNCERTAIN` | 未接收 |
|---|---:|---:|---:|---:|
| 原始冻结运行器 | 12 | 9 | 20 | 9 |
| 单独的后处理报告汇总 | 12 | 11 | 27 | 0 |

后处理只允许受限的容器路径映射，并保留“局部发现 + 其他部分仍不确定”。
它检查原始报告结构、源版本身份、配对 receipt 和保存日志的哈希，没有改写模型结论，
没有重跑模型或 probe，也没有覆盖原始 `summary.json`。

**重要限制：原始拒收的 9 个案例未走到运行器后续的源码不可变性审计，后处理没有补做。
上述四个混合判定案例都属于这 9 个。** 分析器关于“未改动 tracked source”的自述不应冒充
这一步宿主侧审计已经完成。补充案例 lbx154/Argus#53、lbx154/Argus#124 原始运行器状态均为
`completed`，不属于这 9 份拒收报告。后处理可读性与证据一致性，也不是独立确认测试 oracle 正确。

## 6. 全部 50 个 PR 的结果

下表按冻结抽样的合入时间顺序排列。“候选条数”仅统计原报告中有配对证据的
`CONFIRMED_REGRESSION` 条目；不是所有提出过的假设，也不是独立缺陷数。
“接收/拒收”均指原始实验运行器对报告的处理状态。

| PR | 标题 | 原始模型结论 | 分析方式 | 候选条数 | 原始运行器 |
|---|---|---|---|---:|---|
| [lbx154/Argus#2](https://github.com/lbx154/Argus/pull/2) | docs: add agent-assisted installation guide | `UNCERTAIN` | 定向分析 | 0 | 接收 |
| [lbx154/Argus#3](https://github.com/lbx154/Argus/pull/3) | docs: highlight agent-assisted installation | `NO_REGRESSION_FOUND` | 短路 | 0 | 接收 |
| [lbx154/Argus#4](https://github.com/lbx154/Argus/pull/4) | feat(web): add Simplified Chinese localization | `UNCERTAIN` | 定向分析 | 0 | 接收 |
| [lbx154/Argus#5](https://github.com/lbx154/Argus/pull/5) | feat: add web attachment uploads | `UNCERTAIN` | 定向分析 | 0 | 接收 |
| [lbx154/Argus#6](https://github.com/lbx154/Argus/pull/6) | fix: install multipart runtime dependency | `UNCERTAIN` | 定向分析 | 0 | 接收 |
| [lbx154/Argus#9](https://github.com/lbx154/Argus/pull/9) | docs: use official Argus logo in README | `NO_REGRESSION_FOUND` | 短路 | 0 | 接收 |
| [lbx154/Argus#10](https://github.com/lbx154/Argus/pull/10) | docs: publish complete Argus brand asset kit | `NO_REGRESSION_FOUND` | 短路 | 0 | 接收 |
| [lbx154/Argus#11](https://github.com/lbx154/Argus/pull/11) | docs: remove legacy Argus mascot | `NO_REGRESSION_FOUND` | 短路 | 0 | 接收 |
| [lbx154/Argus#14](https://github.com/lbx154/Argus/pull/14) | feat: add copy actions to web conversations | `UNCERTAIN` | 定向分析 | 0 | 接收 |
| [lbx154/Argus#15](https://github.com/lbx154/Argus/pull/15) | docs: add white-background Argus logo set | `NO_REGRESSION_FOUND` | 短路 | 0 | 接收 |
| [lbx154/Argus#17](https://github.com/lbx154/Argus/pull/17) | Fix cross-platform runtime and event-stream reliability | `UNCERTAIN` | 定向分析 | 1 | 拒收 |
| [lbx154/Argus#21](https://github.com/lbx154/Argus/pull/21) | feat: add one-click Argus plugin and medical vertical | `UNCERTAIN` | 定向分析 | 0 | 接收 |
| [lbx154/Argus#25](https://github.com/lbx154/Argus/pull/25) | feat(brand): add iOS-style rounded mark | `NO_REGRESSION_FOUND` | 短路 | 0 | 接收 |
| [lbx154/Argus#27](https://github.com/lbx154/Argus/pull/27) | feat(desktop): add Windows Electron host | `UNCERTAIN` | 定向分析 | 0 | 接收 |
| [lbx154/Argus#31](https://github.com/lbx154/Argus/pull/31) | fix(windows): harden runtime lifecycle and recovery | `UNCERTAIN` | 定向分析 | 1 | 拒收 |
| [lbx154/Argus#32](https://github.com/lbx154/Argus/pull/32) | fix(windows): complete portable regression coverage | `UNCERTAIN` | 定向分析 | 0 | 接收 |
| [lbx154/Argus#36](https://github.com/lbx154/Argus/pull/36) | feat(backend): add DeepSeek Harness (dsh) as a native backend | `UNCERTAIN` | 定向分析 | 0 | 接收 |
| [lbx154/Argus#38](https://github.com/lbx154/Argus/pull/38) | fix(windows): merge startup and recovery hardening | `UNCERTAIN` | 定向分析 | 2 | 拒收 |
| [lbx154/Argus#40](https://github.com/lbx154/Argus/pull/40) | fix(manager): reuse prewarmed ACP scope | `UNCERTAIN` | 定向分析 | 0 | 接收 |
| [lbx154/Argus#41](https://github.com/lbx154/Argus/pull/41) | fix(manager): prewarm full SELF transport | `REGRESSION` | 定向分析 | 1 | 接收 |
| [lbx154/Argus#44](https://github.com/lbx154/Argus/pull/44) | fix(setup): configure and validate the backend model selector | `REGRESSION` | 定向分析 | 2 | 接收 |
| [lbx154/Argus#46](https://github.com/lbx154/Argus/pull/46) | install: simplify cross-platform setup | `REGRESSION` | 定向分析 | 2 | 接收 |
| [lbx154/Argus#47](https://github.com/lbx154/Argus/pull/47) | doctor: let installed agents repair Argus | `REGRESSION` | 定向分析 | 1 | 接收 |
| [lbx154/Argus#48](https://github.com/lbx154/Argus/pull/48) | install: make macOS and Linux setup instructions executable | `UNCERTAIN` | 定向分析 | 0 | 接收 |
| [lbx154/Argus#49](https://github.com/lbx154/Argus/pull/49) | runtime: sync the canonical private product tree | `REGRESSION` | 定向分析 | 1 | 拒收 |
| [lbx154/Argus#50](https://github.com/lbx154/Argus/pull/50) | docs: surface the WeChat community QR | `NO_REGRESSION_FOUND` | 短路 | 0 | 接收 |
| [lbx154/Argus#51](https://github.com/lbx154/Argus/pull/51) | setup: recommend Agent-assisted installation | `UNCERTAIN` | 定向分析 | 0 | 接收 |
| [lbx154/Argus#52](https://github.com/lbx154/Argus/pull/52) | runtime: fix Doctor backend and Manager identity stress findings | `UNCERTAIN` | 定向分析 | 0 | 接收 |
| [lbx154/Argus#53](https://github.com/lbx154/Argus/pull/53) | runtime: reduce repeated token context | `REGRESSION` | 定向分析 | 2 | 接收 |
| [lbx154/Argus#55](https://github.com/lbx154/Argus/pull/55) | fix: preserve paper audit integrity and restore green CI | `UNCERTAIN` | 定向分析 | 0 | 拒收 |
| [lbx154/Argus#57](https://github.com/lbx154/Argus/pull/57) | fix(manager): retry incomplete contextual routing | `NO_REGRESSION_FOUND` | 短路 | 0 | 接收 |
| [lbx154/Argus#73](https://github.com/lbx154/Argus/pull/73) | Fix Planner feedback loops and post-completion Manager reporting | `REGRESSION` | 定向分析 | 1 | 拒收 |
| [lbx154/Argus#74](https://github.com/lbx154/Argus/pull/74) | feat(desktop): replace Electron with Tauri desktop | `REGRESSION` | 定向分析 | 1 | 接收 |
| [lbx154/Argus#76](https://github.com/lbx154/Argus/pull/76) | fix(research): harden draft integrity checks | `REGRESSION` | 定向分析 | 3 | 接收 |
| [lbx154/Argus#81](https://github.com/lbx154/Argus/pull/81) | feat(research): standardize publication figure pipelines | `UNCERTAIN` | 定向分析 | 0 | 拒收 |
| [lbx154/Argus#83](https://github.com/lbx154/Argus/pull/83) | Redesign Argus Web with monochrome visual system | `UNCERTAIN` | 定向分析 | 0 | 接收 |
| [lbx154/Argus#85](https://github.com/lbx154/Argus/pull/85) | fix(daemon): avoid false stalled mission failures | `REGRESSION` | 定向分析 | 1 | 接收 |
| [lbx154/Argus#86](https://github.com/lbx154/Argus/pull/86) | Add complete dark-mode Argus brand system | `UNCERTAIN` | 定向分析 | 0 | 接收 |
| [lbx154/Argus#95](https://github.com/lbx154/Argus/pull/95) | Judge experiments by scientific value, not hard thresholds | `UNCERTAIN` | 定向分析 | 0 | 接收 |
| [lbx154/Argus#69](https://github.com/lbx154/Argus/pull/69) | feat: add configurable PR gate workflow | `NO_REGRESSION_FOUND` | 定向分析 | 0 | 接收 |
| [lbx154/Argus#97](https://github.com/lbx154/Argus/pull/97) | Reduce specialist review context without weakening integrated review | `UNCERTAIN` | 定向分析 | 0 | 接收 |
| [lbx154/Argus#65](https://github.com/lbx154/Argus/pull/65) | docs: explain Argus memory design | `NO_REGRESSION_FOUND` | 短路 | 0 | 接收 |
| [lbx154/Argus#66](https://github.com/lbx154/Argus/pull/66) | docs: define Argus core concepts | `NO_REGRESSION_FOUND` | 短路 | 0 | 接收 |
| [lbx154/Argus#98](https://github.com/lbx154/Argus/pull/98) | Repair current-main CI without weakening review or visualization contracts | `UNCERTAIN` | 定向分析 | 0 | 接收 |
| [lbx154/Argus#72](https://github.com/lbx154/Argus/pull/72) | Add live counterexample lab and isolated Jacobian bridge | `UNCERTAIN` | 定向分析 | 0 | 接收 |
| [lbx154/Argus#64](https://github.com/lbx154/Argus/pull/64) | Fix verified Windows, CI, and release regressions | `NO_REGRESSION_FOUND` | 定向分析 | 0 | 接收 |
| [lbx154/Argus#29](https://github.com/lbx154/Argus/pull/29) | Preserve full mission output in the web UI | `UNCERTAIN` | 定向分析 | 1 | 拒收 |
| [lbx154/Argus#116](https://github.com/lbx154/Argus/pull/116) | Require Planner scale assessment before research paper handoff | `UNCERTAIN` | 定向分析 | 0 | 拒收 |
| [lbx154/Argus#117](https://github.com/lbx154/Argus/pull/117) | Default concept figures to Method D with local-vector fallback | `UNCERTAIN` | 定向分析 | 0 | 接收 |
| [lbx154/Argus#124](https://github.com/lbx154/Argus/pull/124) | Prevent duplicate research idea portfolios | `REGRESSION` | 定向分析 | 1 | 接收 |

## 7. 证据位置与重新复现约定

以下是本机证据路径，不是已纳入本目录的附件：

```text
/home/chentianyu/argus-experiments/pr-regression-50-20260916/
```

| 相对实验根目录的路径 | 内容 |
|---|---|
| `manifest.json`、`population.json`、`selection.json` | 冻结环境、完整采样记录和 50 个样本 |
| `SKILL.md`、`runner/` | 实际运行的冻结 Skill 与程序 |
| `inputs/pr-N/input.json`、`change.patch`、`changed-paths.json` | PR 元数据、精确版本和集成差异 |
| `results/pr-N/REPORT.md`、`report.json` | 原始自然语言报告和结构化结论 |
| `results/pr-N/runner-result.json`、`copilot.jsonl`、`usage.json` | 运行器状态、真实工具轨迹和原始用量字段 |
| `results/pr-N/evidence/` | 配对 receipt、日志、输入和观测 |
| `results/pr-N/base-probes/`、`candidate-probes/` | 保存的新增 probe，保留了运行时文件名 |
| `summary.json` | 未覆盖的原始运行器汇总 |
| `posthoc-artifact-audit-v1/` | 单独后处理的汇总、逐例记录和审计代码副本 |

重新执行案例中的命令，需要先把对应历史版本还原到独立环境，再将保存的 probe
恢复到各自的 `tests/_regression_probe/`，使用该版本的隔离 fixture。
不要直接在当前工作树或正在运行的 Argus 项目里执行。部分 probe 有相邻文件依赖，
应保留原始目录布局；不能把缺模块、缺工具或基线也失败解释为候选回归。

## 8. 能支持与不能支持的结论

本轮支持“这种 Skill 可以产生可复核的局部反例，且确实能够短路低影响修改”。
四个混合判定案例集中暴露了并发快照、状态身份和跨版本持久化兼容的风险；
补充的 lbx154/Argus#53、lbx154/Argus#124 展示了把文本中的标记或关键词误当成控制意图的风险。

不支持把报告中的比例解释为真实缺陷率、precision/recall、Skill 优于其他方法，
或 Argus 版本整体退化的因果解释。27 个不确定判定中，有不少源于缺少 Node/npm、
原生 Windows 或真实模型行为证据；缺证据不等于存在缺陷。
模型自述、配对日志和独立语义裁决必须继续分开。
