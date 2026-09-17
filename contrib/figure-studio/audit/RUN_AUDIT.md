# Argus 端到端论文生产运行审计

审计对象：`/data/v-boxiuli/ai-research-open-20260902`；运行时：`/data/v-boxiuli/argus-runtime-latest`。  
证据快照：2026-09-03 18:45:17 PDT。项目目录及运行时均只读；未启动 GPU 作业、未终止进程。以下时间均为 PDT。由于现场仍有任务运行，结论只对应此快照。

## 结论先行

Argus 确实完成了从 12 路 Idea 评选、真实模型构建、CPU 实验、论文撰写到一次审查的“大部分实际工作”，并留下了丰富的配置、代码和逐次原始测量。可是流程控制没有完成同一件事：持久状态始终停在 `build/in_progress`，论文主题已从 CLDE 换成 FuseHead，当前原始结果又晚于论文/PDF。因而这是“有一篇可辨认的系统论文和若干真实实验”，不是由 Argus 自己五阶段契约认证完成的端到端运行。

## 1. 时间轴与阶段账本

### 1.1 关键时间轴

| 时间 | 可核验证据 | 阶段含义 |
|---|---|---|
| 09-02 08:50:28 | `OBJECTIVE.md` birth/mtime | 运行起点。 |
| 09-02 09:28:06 | 首个 `.argus_subagents/*.json` 完成 | Idea 子任务已在执行。 |
| 09-02 13:44:06 | Route-09 review mtime | CLDE 独立评审落盘。 |
| 09-02 14:18:02 | `selected_idea.selected_at`；`idea-provenance-selector-v4` done | selector 作出 Route-09/CLDE 选择。 |
| 09-02 14:41:25 | selection.json birth/mtime | 选择制品最终落盘。 |
| 09-02 14:45:32 | `PIPELINE_STATE` birth；history `idea→build` | Idea 正式结束、Build 开始；这是唯一一次持久阶段迁移。 |
| 09-02 14:53:54 | `clde-deps` 开始 | 首个 Build 执行任务。 |
| 09-02 15:00:42–15:01:01 | `clde-qwen25-reference`, 19.5 s | 首次真实 Qwen2.5-0.5B 执行；不是 toy。 |
| 09-02 15:25:14/15:26:29 | `runs/build/qwen25_05b_clde_sweep.json` birth/mtime | 首个持久 raw run；前 3 次同命令失败后第 4 次成功。 |
| 09-02 17:27:40 | `checkpoints/` 最早 mtime | 首批训练 checkpoint。 |
| 09-02 18:15:23 | `qwen25_05b_cap_adapt_v4_clde_gate.json` | CLDE gate=false；候选 PPL 相对损失 12.2811–15.2062，远高于 0.02 门槛。 |
| 09-02 21:31:47 | depth-22 suffix raw/ckpt | 最后一批深度恢复尝试仍 `recoverability_failed`。 |
| 09-02 21:49:40 | `clde/vocab_search.py` birth | 核心工作转向 exact-MIPS/词表头；主题转折最早硬证据。 |
| 09-02 22:03–22:26 | `build-certified-vocab-search` | 首个 centroid/HNSW 词表搜索实跑，先 error；22:36 的 eval 重跑成功。 |
| 09-03 00:54:13 | `CHECKPOINT.md`、`ARGUS_PORTFOLIO_REPORT.json` mtime | 明文称 progressive exact-MIPS 为替代/“mechanism-level systems repair”，仍要求留在 Build。 |
| 09-03 03:01:55 | progressive-MIPS raw mtime | 结果为 `replan_required`；随后连续开发 low-bit 变体。 |
| 09-03 09:22–09:30 | `fused-u8-admission-final` done；raw mtime 09:30 | `fused_batched_full_scan_u8_exact_selected`，FuseHead 机制成形。 |
| 09-03 09:53:47 | concurrent-request config birth | 开始论文主张所用的并发请求实验。 |
| 09-03 11:01:44 | current experiment raw birth | 首个 `runs/experiment/` 主结果制品开始生成；状态机仍是 Build。 |
| 09-03 11:05:38 | frozen confirmation raw mtime；`...-r4` done | 旧配置 confirmation 通过，两种 grouping 均 36/36。 |
| 09-03 11:51:44 | `paper/` 样式文件最早 mtime | Paper 工作实际开始。 |
| 09-03 11:57:05 | `paper/main.tex` birth | 主论文最迟于此时首次出现；当前 mtime 为 17:09:39。 |
| 09-03 12:49:18 | `PIPELINE_STATE` mtime、venue profile mtime | 状态文件被更新但仍为 `build/in_progress`。 |
| 09-03 13:20:48/52 | PDF CreationDate/filesystem mtime | 生成 7 页 `main.pdf`；appendix 为 4 页。 |
| 09-03 15:49–15:54 | Figure PDF/TeX mtimes | 图和图源在主 PDF 后继续变化。 |
| 09-03 16:13:11 | `paper/REVIEW.md` birth/mtime | 权威 verdict=`continue`，明确指出 raw/PDF 过期。 |
| 09-03 17:09:39 | `main.tex`、`appendix.tex` mtime | 论文源再次实质更新，但没有重编译。 |
| 09-03 17:57:42 | 当前 `HANDOFF.md` inode birth/mtime | 仍以 `# HANDOFF — IDEA` 开头，未形成 Build/Experiment/Paper handoff。 |
| 09-03 17:59:41 | concurrent config mtime；review-v4 启动 | 配置又晚于论文；新一轮审查实验开始。 |
| 09-03 18:39:28 | current raw mtime | 最新 raw 覆盖论文数值；confirmation 标记为 pending。 |
| 快照 18:45:17 | review-v4 `running` | 运行尚未结束。 |

补充文件范围：`checkpoints/` 317 文件，mtime 09-02 17:27:40–21:31:47；`runs/` 48 文件（46 build、2 experiment），mtime 09-02 15:26:29–09-03 18:39:28；`.argus_subagents/` 最早完成 09-02 09:28:06、最晚已完成 09-03 17:54:45，另有一项运行中。

### 1.2 子任务统计（按 task_id 的**字面前缀**）

| 前缀 | 总数 | done | error | timeout | running | 已记录 elapsed 合计 |
|---|---:|---:|---:|---:|---:|---:|
| `research-idea-*` | 8 | 5 | 1 | 2 | 0 | 6,840.9 s（1.90 h） |
| `build-*` | 40 | 27 | 13 | 0 | 0 | 11,445.3 s（3.18 h） |
| `experiment-*` | 0 | 0 | 0 | 0 | 0 | 0 |
| `paper-*` | 0 | 0 | 0 | 0 | 0 | 0 |
| `review-*` | 6 | 4 | 1 | 0 | 1 | 6,471.7 s（1.80 h） |
| 其他（大量 `clde-*`、`idea-*` 等） | 74 | 45 | 27 | 2 | 0 | 29,117.8 s（8.09 h） |
| **合计** | **128** | **81** | **42** | **4** | **1** | **53,875.7 s（14.97 task-hours）** |

这里的 elapsed 是任务墙钟秒之和，不是 GPU-hours/core-hours；任务有重叠，运行中任务没有 elapsed，故不能当项目墙钟或完整算力账。更重要的是，Experiment/Paper 实际发生却各有 0 个对应前缀任务，命名/阶段计量本身已失真。

## 2. 主题 pivot 与状态机是否发现

1. Route-09 的必要元素是：硬 residual cap、未算深层 tail radius、RMSNorm/全词表 margin certificate、FREE 式 causal catch-up，以及对 capped model 的永久深度省略。`PIPELINE_STATE.selected_idea` 和旧/当前 `HANDOFF.md` 均如此。
2. 09-02 18:15 的 CLDE gate 已否定核心可行性；后续 trajectory/prefix/suffix 分支到 21:31 仍失败。这个负证据被保留，做得正确。
3. 21:49 后的新代码改做输出头 exact MIPS；00:54 的 portfolio 将它描述成保持“exactness objective”的修复，却同时承认是 replacement，并要求先补 faithful published baseline。
4. 09-03 09:30 选中的 FuseHead 是 affine-u8 行包围、row-outer/query-panel-inner 融合、FP32 survivor completion；论文和 raw 使用的是 `.../uncapped/step-000120/model`。它没有 residual-capped decoder、lazy depth、tail certificate 或 causal catch-up。因此它不是 CLDE 的实现变体，而是另一个中心机制和论文命题。
5. 持久状态完全没发现这次科学主题替换：history 只有 `idea→build`，`current_stage=build`；没有 Build→Experiment→Paper→Review。`HANDOFF.md` 也没有随阶段覆盖，反而仍是 IDEA。

**对运行时规则的判定：明确违反。** 适应性开发本身并不违规：Experiment playbook 允许证据驱动地改 method/baseline/benchmark，也允许追随意外正证据。但本次运行违反的是约束链：

- `stages.py:23-29` 和交付说明规定单向 `Idea→Build→Experiment→Paper→Review`；这里跨过三个持久迁移直接写论文、做 Review。
- `stages.py:79-95`、`hypothesis-implementation-contract.md:20-28` 要求代码测试 selected mechanism；FuseHead 对 CLDE 应判 `MISMATCH`，未见 `ALIGNED` 评审。
- Build/Experiment/Paper 各要求对应 `# HANDOFF — ...`；当前首行是 IDEA。若调用 `stage_completion_issues(build)`，运行时自己的 `_handoff_issue` 会直接报 stale。
- `stages.py:132-140` 与 Experiment playbook 要求进入 Paper 前有真实强 published baseline；00:54 portfolio 明言此项仍缺，却仍在 11:57 生成论文。
- Paper 要求 tex、图源、输出相互一致并按当前 venue kit 编译；当前 PDF、两个图 PDF、tex、raw 均不同步。
- Review 要先对同一份当前论文做科学/视觉/语言三次独立只读 pass，再修复、重编译、综合验收；现有 review 前缀任务都是 exactness 实验/测试，不是这三类 pass，且 `REVIEW.md` verdict 是 `continue`。

## 3. 摘要 headline claim 对 raw evidence

| 论文主张 | raw 查找结果 | 判定 |
|---|---|---|
| 4.47× head speedup（0.505 s vs 2.260 s） | 当前 `runs/experiment/...concurrent_request_experiment.json` 为 candidate 0.570528 s、最小 exact total 2.341646 s、汇总 speedup 4.10435；现存 raw 没有 4.47 汇总。 | **未匹配/陈旧**。只残留在 tex、旧 PDF。 |
| 288/288 exact decisions | 当前 raw `fused_u8.parity={matches:288,tokens:288}`、zero fallback，public gates=true。 | **匹配，且 raw 新于论文**。 |
| bootstrap 95% lower bound 3.90× | 当前 paired bootstrap 为 point 3.84220、CI [3.61942, 4.04028]。 | **未匹配/陈旧**；方向仍显著为正，但 headline 数字已变。 |
| 1.10× prefix+head | 当前 raw 为 19.179178 s vs 20.968405 s，ratio 1.09329（两位小数应为 1.09，而非 1.10）。 | **陈旧/舍入口径不符**；“仍为正”匹配。 |
| 两组各 36/36 unopened；3.59–4.23× | 旧 `...frozen_confirmation.json`（11:05）精确匹配：36/36、3.58688 与 4.23088，CI 下界 3.51478/4.15694。 | **历史匹配但陈旧**：17:59 新 config 之后，当前主 raw 明示 `opened=false,pending=true`。 |

版本顺序是决定性证据：`main.pdf` CreationDate=13:20:48，`main.tex` mtime=17:09:39，current config=17:59:41，current raw=18:39:28。PDF 比 tex 旧 3:48:51；旧 PDF 内还写“20 rotated repetitions”，当前 tex/raw 写 28。故不能把 7 页 PDF 当当前论文，也不能用旧 confirmation 为新配置作 held-out 认证。`paper/REVIEW.md` 已正确识别这个阻断项。

## 4. baseline 公平性与披露

代码中存在的主要比较路径：

- `clde/vocab_search.py`：dense top-1、centroid/residual certified search、ANN-seeded progressive orthogonal/suffix search、`LempExactSearch`、`FexiproExactSearch`；`vocab_search_run.py` 另有 HNSW output-head 路径。
- `clde/lowbit_mips.py`：single affine-u8、dual-quant、batched dual-quant、FuseHead 的 batched full-scan-u8、oneDNN certified int8，以及 cone-block、separable-IVF、microprototype、directional-microcluster、query-subspace-tree 等开发分支。
- 论文实际 headline 比较：FP32 SGEMM、本地 query-parallel/serial affine-u8、MAXIMUS 官方仓库中的 BMM+本地 driver、oneDNN int8+本地 certificate、近似 HNSW。

LEMP/FEXIPRO **不是官方复现**。`PRIMARY_SOURCE_MAPPINGS`、`CHECKPOINT.md` 和 portfolio 都设 `official_reproduction=false`：LEMP 仅 LENGTH/norm-order 控制，缺 LEMP-LI 的 tuning、INCR、cache-aware、SIMD/C++；FEXIPRO 是本地 SVD/suffix-pruning 控制，缺官方完整 SVD/SIGMA、individual reorder 与优化 runtime。

`main.tex:349-370,538-545` 对此披露诚实，还明确说 official-repo BMM 没跑 MAXIMUS clustering/angular pruning，排除 LEMP/FEXIPRO 于 headline。问题不是文字冒充，而是证据门槛：query-parallel/serial u8 是本地同信息控制，BMM 也不是 published MAXIMUS 算法，所以当前包仍没有 runtime/portfolio 所要求的“faithful strongest published exact-MIPS baseline”。摘要的 “strongest same-information exact baseline” 可以按本地集合成立，但不能替代该 published-baseline 条件；且最新 raw 中最强 exact 方法已从旧 single-query 变成 official-repo BMM，论文表述也已陈旧。

## 5. 论文质量快照

- **篇幅/模板**：旧 `main.pdf` 7 页，Conclusion/AI disclosure 在第 6 页、References 从第 7 页开始；低于 EuroSys 12 页 technical-content 上限，形式上不超页。页数不足本身不是违规。当前 tex 尚未重编，真实当前页数未知。
- **author kit 一致性**：当前 tex 使用正确的匿名 `acmart` 声明；但 PDF metadata 显示 acmart v2.03 (2024-02-04)，而 `TEMPLATE_SOURCE.md` 声称用 v2.20 (2026-08-16)。当前渲染未兑现该声明。
- **图**：两幅外部 PDF 均有可编辑 TikZ 源。Figure 1 是蓝/青/橙、圆角框、粗箭头的扁平流程图；confirmation 是同色系、15 pt 字、手工硬编码柱和 CI 的简洁条形图。风格统一、标签较大，但数据图没有从 JSON 自动生成。
- **图的新鲜度**：`mechanism.pdf` 比其 tex 旧约 4 分钟；`confirmation.pdf` 比其 tex 旧约 2 小时 55 分；主 PDF 又早于当前两个图源/机制图导出。因此无法完成当前视觉验收。
- **figure skill 使用**：在 128 个 task JSON/日志、configs、runs 和论文源中找不到 `figure_tool.py`、FigureSpec、visualization-router、framework-figure-studio 或 Matplotlib/SciencePlots 调用；唯一证据是 AI disclosure 自述“TikZ figures”。结论是“**无可验证的 Argus figure skill/figure_tool 使用证据**”，不能证明从未在未记录的主 agent 上下文中使用。数据图用 TikZ 也偏离交付说明“数据图统一走 Matplotlib/SciencePlots”。
- **引用/占位符**：`references.bib` 19 条；main+appendix 共 19 个唯一 cite key，19/19 全部解析、无未引用条目。未发现 TODO/TBD/FIXME/placeholder/`??`。运行时 structural validator 通过：2 图、19 cite、Related Work 2021 chars、Conclusion 存在。
- **摘要**：运行时 heuristic 计为 172 词、5 句，覆盖问题/缺口、方法、保证、模型/benchmark/数字、意义；超过该动态 EuroSys profile 的 150 词软下限和通用 review 的 170 词阈值，无 reader-hostile/过度 caveat 触发。因此**结构规则通过**，但数值新鲜度不通过。
- **Review 文档**：格式段落齐全且 verdict=`continue` 是正确决定；但 Strongest accept case 与 Reject-level issues 基本逐字重复 assessment，没有展示三次独立 pass 的不同观察。

## 6. 浪费与效率

- 可严格确认“只等待、不改代码/结果”的 polling/barrier/settlement 任务 **12/128**，合计 10,882.5 task-s（3.02 h，占全部已记录 elapsed 的 20.2%）；其中 4 个 timeout 共 9,600 s。
- task ID 后缀 `-r2/-r3/-r4/-v2...` 有 **44** 项；含 `reviewed/diagnostic` 的有 **3** 项。与 wait 类去重后，用户所指的明显重试/诊断/等待集合为 **59/128**、30,516.1 s（8.48 task-h，56.6%）。这是“上界”：若中间修改过代码/config，成功重跑有必要价值。
- 更保守的制品浪费指标：21 组完全相同 command 涉及 66 项，首项之后有 **45** 次重复，20,780.5 s；多数写回同一路径，旧 raw 被覆盖，既消耗时间又破坏可审计版本链。
- error+timeout 共 **46/128**、19,152.9 s（5.32 task-h，35.5%）。失败帮助调试，但大量 `-v2/-v3/-v4` 与相同目标覆盖说明缺少“先做便宜 preflight、失败产物不可变命名”的机制。
- `checkpoints/` 有 317 文件、约 **362.0 GB (338 GiB)**；`runs/` 仅 46.7 MB。短适配任务按 step-10/20/40 密集保存模型+optimizer，是明显的存储/写入放大。
- 项目墙钟到快照为 33.91 h。按正式 Idea 迁移和 `main.tex` 首现切分：Idea 5.92 h（17.45%）；Build+事实上的 Experiment 21.19 h（62.49%）；Paper+事实上的 Review 6.80 h（20.06%）。这比 task elapsed 更适合表示阶段墙钟，但由于阶段状态失真，只能是文件事件估计。

## 7. 总体 verdict（8 点）

- 做得好：12 路 selection 有独立评审与 selector，负 CLDE 结果未被隐藏，并能快速转向一个有真实机制和可测收益的 CPU 系统方向。
- 做得好：raw 很细，包含逐 token parity、certificate、fallback、固定线程预算、28 次重复、bootstrap、prefix accounting 和 held-out grouping；最新 public 结果仍是显著正收益。
- 做得好：论文披露了 LEMP/FEXIPRO/MAXIMUS 的真实实现边界，引用全部解析，摘要结构和 EuroSys 页数上限通过。
- 已破坏：selected idea、执行机制、论文 thesis 三者不一致；FuseHead 是新课题，却被包装为 CLDE repair，缺少正式的 mismatch/重授权记录。
- 已破坏：阶段状态仅到 Build，Experiment/Paper/Review handoff 和迁移全缺；0 个 experiment/paper 前缀任务却有完整论文，说明阶段门被绕过。
- 已破坏：论文、图、PDF、配置、raw 是四个不同版本；4.47×、3.90×、1.10× 和当前 confirmation 均不能由最新证据链认证。
- **最高影响修复 1**：把 stage transition 做成写权限/任务调度硬门；每次 launch 前验证 `current_stage`、正确 HANDOFF marker、selected-thesis hash 和上一阶段 reviewer verdict，禁止 Build 中创建 Paper/Review 任务。
- **最高影响修复 2–3**：以不可变 run ID 保存 raw，并让表/图/tex 声明同一 evidence manifest hash，raw 更新即强制失效/重编 PDF；同时引入“scientific pivot”门（判定同一 Idea repair 还是新 Idea）和 faithful published-baseline gate，未通过不得进入 Paper。
