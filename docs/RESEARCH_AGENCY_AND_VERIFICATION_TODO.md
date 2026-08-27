# Argus 框架优化 TODO：研究主动性、Kernel Frontier 与分级 Verification

> **性质：实现 backlog，不是当前运行契约。** 未勾选条目不代表现有 Argus 已具备对应
> 能力。当前事实仍以代码、行为测试、`AGENTS.md`、`docs/ARCHITECTURE.md` 和各 vertical
> checklist 为准。
>
> 目标不是把 Reviewer 变“松”，而是同时做到：**探索更大胆、调研更深、失败归因更准、
> 最终认证仍然严格**。

## 0. 用户反馈与问题定义

本轮反馈集中在三个相互关联的问题：

1. **Agent 过于保守，缺少研究好奇心。**
   - 倾向选择最容易通过当前 checklist 的路线，而不是最高潜在价值路线；
   - 看到已有 PR、缺少工具或环境红项时，容易缩小范围，而不是继续调查、安装、修复或寻找
     不重叠的高价值切口；
   - scope/checklist 一通过就停止，完成理由更像“流程闭环”，而不是“发现了什么、做成了什么”。
2. **Kernel 调研虽然已有框架，但仍不够鲜活、可执行。**
   - 应主动查看最新工具、官方 release、近期论文、作者实现和目标仓库最新 PR/issues；
   - 不只列工具名，还要知道如何发现已安装实例、如何安装、版本如何兼容、怎样 smoke test、
     怎样用于 profiling/实现/验证；
   - 不应因 PATH 未配置而把已经安装的 CUDA Toolkit、`ptxas`、Compute Sanitizer 判为缺失。
3. **Paper/idea verification bar 过早、过强。**
   - 当前 `research_target_level=publishable` 容易被模型误读为“每个早期 idea/每轮 pilot 都必须
     已经达到投稿级”，导致大量 idea 在形成前被 kill；
   - AAAI/EMNLP 的官方格式差异已有 profile，但“科学成熟度”“当前阶段”“最终投稿认证”仍
     混在一起；
   - 早期探索、论文开发和最终 submission 应使用不同 verification 强度，同时共享不可降低的
     真实性/正确性底线。

---

## 1. 当前实现诊断（改动前先承认已有能力）

### 1.1 已经存在、应复用的正确基础

- `docs/PRINCIPLES.zh-CN.md` 已明确：诚信约束声明，探索追求上限；负结果更新策略；
  Programme 的最终结果优先于把局部 profiling/文档工作填满 backlog。
- `kernel_engineering` vertical 已有：
  - `frontier_watch.py` 与 event-driven frontier protocol；
  - `specialized_tool_registry.json`（约 90 个工具条目）；
  - environment → baseline → optimize → validate → report stage；
  - IDGL、experiment-budget ladder、leverage gate、attempt outcome taxonomy；
  - Triton、TileLang、CUDA/CUTLASS/CuTe、Nsight、Blackwell 技术参考。
- `research` vertical 已有 `exploratory | publishable | doctoral` target，且 bounded mission 理论上
  不应等同 project completion。
- Reviewer prompt 已采用 trust-first、按需打开 raw evidence 的方向；final paper peer review 只应在
  `review` / `submission` / `final_submission` 生效。

### 1.2 原则与实际行为的断点

1. **价值原则没有变成可观测、可测试的控制行为。**
   `PRINCIPLES.zh-CN.md` 明确不应整段注入 prompt，这是对的；但当前也缺少短策略、行为
   指标和回归测试来保证 Planner 真的探索高价值路线。
2. **`research_target_level` 混合了“项目最终目标”和“当前轮验证强度”。**
   Manager 对“做投稿级论文/持续研究”通常选择 `publishable`；该字段随后进入每轮 Planner 和
   Reviewer prompt。虽然文案写了“project-level completion”，模型仍可能在 research/pilot 阶段
   过早套用最终发表标准。
3. **Scope 会受当前可用工具偏置。**
   Kernel 的专业工具 shortlist 位于 `environment` stage，但 scope stage 已要求确定实现路径；这
   允许 Agent 先按“当前能 import 什么”选题，再把缺失工具写成排除理由。
4. **环境审计偏向 PATH 探测。**
   `collect_tools()` 当前主要使用 `shutil.which()`；未系统检查 `CUDA_HOME`、`CUDA_PATH`、
   `/usr/local/cuda*`、alternatives、PyTorch/Triton 自带工具位置，容易产生 false negative。
5. **工具 registry 还是“目录”，不是完整操作手册。**
   多数条目有 URL、可执行文件和 `use_when`，但缺少兼容矩阵、安装策略、smoke test、最小使用
   示例、升级风险和 operator approval 边界。
6. **Research checklist 与 AAAI skills 存在冲突。**
   - `verticals/research/stages.py` 已写“引用、任务、模型和重复次数由 claim scope 决定，不设
     universal quota”；
   - 但 `VenueProfile` 仍默认 `min_verified_bib_entries=35`、`min_cited_keys=30`；
   - `aaai-format-preflight.md`、`aaai-academic-language-review.md` 和
     `academic-paper-peer-review-benchmark.md` 仍把 35/30、接近满 7 页、模型分数等写成硬门槛；
   - AAAI 官方的 7 页是**上限**，不是“必须填满到 Conclusion 第 6–7 页”的科学质量证明。
7. **Idea 证据判断太容易折叠为 kill。**
   应复用现有 `untested | inconclusive | supported | refuted` 四态；环境、实现、预算、prior-art
   重叠和 scope 变化属于原因或调度动作，不应膨胀成更多 idea 状态。
8. **Stage authority 可能造成无价值等待。**
   Reviewer 已认证 scope 后，Planner 仍可能因 Manager 尚未推进 `current_stage` 而 WAITING；这
   是合法权威边界，但缺少“认证后立即触发 Manager 决策”的 liveness SLO。

---

## 2. 设计原则（所有 TODO 的共同约束）

### 2.1 两条轴必须分开

- **探索姿态（Exploration Posture）**：愿意花多少预算探索非显然、高风险、高收益路线。
- **验证强度（Verification Profile）**：当前 artifact/claim 需要什么证据才能完成当前 mission。

不能用“严格 Reviewer”代替研究方向选择，也不能用“更有好奇心”降低真实性底线。

### 2.2 永不放宽的 Integrity Floor

任何 profile 下都必须阻断：

- 伪造、重标、复制或无法追溯的证据；
- stub evaluator、常量 scorer、未执行却声称执行；
- 不真实的引用、错误来源映射、泄露凭据；
- benchmark 路径没有进入被修改代码却声称加速；
- 通过放宽测试、容差、benchmark/scorer 制造成功；
- 把环境/权限/工具失败解释成科学或算法反证；
- 把 N=1、单 shape、单机器结果冒充普适结论。

### 2.3 可调整的是“完成什么”，不是“事实是否真实”

- Explore 可以用可信的小样本决定下一步，但不能宣称投稿完成；
- Develop 可以接受尚未完整覆盖的候选实现，但必须准确标注证据等级；
- Certify 才要求完整 claim coverage、venue compliance 和独立 final review。

### 2.4 Checklist 是 rubric，不是目标函数

完成理由必须引用**结果和决策价值**，不能只引用“文件存在/检查通过”。结构化 checklist 只负责
把当前阶段的重要问题带给 Reviewer，不替 Reviewer 判断科学价值。

### 2.5 好奇心不是盲目安装或无限试错

- 先发现、调查、估算信息增益，再安装；
- 项目级/隔离环境依赖可自主处理；
- driver、系统 CUDA 切换、破坏性全局升级继续要求 operator 确认；
- 高风险尝试必须受预算、停止条件和真实测量约束。

### 2.6 避免 Prompt 膨胀

不把本 TODO 或 `PRINCIPLES.zh-CN.md` 整段复制进四角色 prompt。优先：

1. 一个短的结构化 effective-policy block；
2. vertical-owned checklist metadata；
3. Agent 按需读取的 Skill/reference；
4. 行为测试与可观测指标。

---

## 3. P0 — 先修“误杀”和策略不可见问题

### TODO P0-01：建立 Verification Policy 双轴模型

- [ ] 新增项目级 `ExplorationPosture`：
  - `conservative`：明确预算紧/风险低的工程任务；
  - `balanced`：默认；
  - `frontier`：持续研究、kernel 优化、探索型 paper campaign。
- [ ] 新增 `VerificationProfile`：
  - `explore`：验证 premise、方向和信息增益；
  - `develop`：验证实现、比较和可复现候选；
  - `certify`：验证完整 claim、venue 与 submission readiness；
  - `adaptive`：默认，按 stage/scope 自动解析以上三档。
- [ ] `research_target_level` 只表示项目最终成功目标，不再承担每轮 verification bar。
- [ ] effective profile 建议解析顺序：
  1. `scope=final_submission` 或 submission completion gate → 强制 `certify`；
  2. operator 明确的 project override；
  3. `adaptive` 的 stage mapping；
  4. 未解析时 fail-visible，不静默回落到最严或最松。
- [ ] 建议的 research stage mapping：
  - `research`, `plan` → `explore`；
  - `benchmark`, `run`, `analysis`, `draft` → `develop`；
  - `review`, `submission` → `certify`。
- [ ] Kernel mapping：
  - `scope`, `environment` → `explore`；
  - `baseline`, `optimize` → `develop`；
  - `validate`, `report` → `certify`（仅对所声明的硬件/shape/claim 范围）。
- [ ] 将 operator 选择持久化在 state root 的 Manager-owned contract/config 中；降低最终 bar 属于
  改变“done”含义，必须走 operator confirmation，不能由 Engineer/Reviewer 自改。
- [ ] 四角色 prompt 只注入一段短信息：最终 target、当前 profile、不可降低的 integrity floor、
  当前 mission 的 completion scope。

**建议代码触点**

- 新模块：`argus_skill/core/verification_policy.py`；
- `argus_skill/core/project_contract.py`：持久化/确认边界；
- `argus_skill/manager/front_door.py`、`manager/_vertical_ops.py`、
  `roles/prompts/manager.py`：选择和修改策略；
- `argus_skill/roles/prompts/registry.py` 与 prompt types：统一解析 effective policy；
- Planner/Engineer/Reviewer prompt 只消费解析结果，不自行推断。

**验收标准**

- publishable AAAI 项目在 `research` stage 的 bounded idea-probe 可以 `done`，但不会认证 project
  publishable/completed；
- 同一项目进入 `final_submission` 时自动使用 `certify`；
- `explore` 仍拒绝伪造、stub evaluator 和假引用；
- policy 的来源和 effective result 在 cockpit/event 中可见。

### TODO P0-02：给 ChecklistItem 增加“适用成熟度”，不增加机器科学裁决

- [ ] 扩展 vertical checklist item 元数据，至少区分：
  - `integrity_floor`：所有 profile 都显示；
  - `stage_exit`：当前 stage 结束需要；
  - `certification`：仅 final/certify；
  - `advisory`：建议，不得单独阻断。
- [ ] `format_stage_checklist()` 按 effective profile 渲染相关 rubric；仍由 Reviewer 判断通过与否。
- [ ] full-pipeline checklist 固定包含所有 certification item。
- [ ] bounded mission 明确只认证该 mission 的交付，不因项目最终 target 尚未达到而保持无限 continue。
- [ ] Reviewer reason 必须区分：
  - current mission gap；
  - project-to-target gap；
  - final submission blocker。

**不做**

- 不恢复已删除的 automated gate router；
- 不用 harness 根据 issue code 自动判 done/kill；
- 不把所有 advisory 变成新 artifact。

**建议代码触点**

- `argus_skill/skills/stage_machine.py`；
- `argus_skill/verticals/*/stages.py`；
- `argus_skill/roles/prompts/registry.py`；
- `tests/skills/test_stage_checklists.py` 及新增 profile matrix tests。

### TODO P0-03：分离 Mission Verdict 与四态 Idea Evidence

- [ ] 保留 Reviewer 的 `done | continue | blocked | replan_requested` 作为 mission 控制状态。
- [ ] 复用 Kernel attempt outcome 已有的四态，不再新增第二套复杂状态机：
  - `untested`：binding premise 没有被有效执行；
  - `inconclusive`：执行过，但证据不足以支持或反驳；
  - `supported`：证据在明确声明范围内支持 premise；
  - `refuted`：有效、忠实、path-covered 的证据反驳了精确 premise。
- [ ] 其他概念放到正交字段，不进入 `idea_status`：
  - 环境、工具、实现、数值、测量问题 → 现有 `failure_class` / reason；
  - `promising` → `inconclusive` 状态下的一条正信号描述，不是状态；
  - `park` → Planner/backlog 调度动作；
  - prior-art 覆盖 → replan reason；
  - scope 调整 → `replan_requested` 的目标变化。
- [ ] “kill/refuted” 只允许 faithful、path-covered、measurement-valid 的实验反驳 binding premise。
  Prior art 完整覆盖可以停止 novelty 路线，但应写进 replan reason，不能伪装成实验反证。
- [ ] 环境、编译、权限、安装、toolchain、无 GPU、无网络、runner 崩溃均只能得到
  `untested` 或 `inconclusive`。
- [ ] `supported` 只表示当前 premise 在声明范围内得到支持，不自动等于 publishable 或 project done。
- [ ] `replan_requested` 表示需要新 scope/plan，不自动等于 idea/project 失败。
- [ ] Idea history 保留 premise 版本；重新定义 premise 后可重新测试，避免旧结论污染新 premise。

**验收标准**

- 一次 TileLang import 失败得到 `untested`，reason/failure_class 指向 environment；
- 一个正确但 N=1/noisy 的 pilot 默认是 `inconclusive`，不是 `refuted`；
- 上游 PR 只覆盖部分 edit surface 时，idea status 不变，Planner 用 replan reason 指出剩余切口；
- Reviewer 可结束一个正确完成的 bounded probe，同时把项目 gap 留给 Planner。

### TODO P0-04：清理 AAAI/通用 Paper 规则冲突

- [ ] 以 `verticals/research/stages.py` 的“claim-proportional、无 universal quota”原则为上位规则，
  审计并统一以下文件：
  - `verticals/research/venue_profiles.py`；
  - `verticals/research/skills/engineer/aaai-format-preflight.md`；
  - `verticals/research/skills/reviewer/aaai-academic-language-review.md`；
  - `verticals/research/skills/reviewer/academic-paper-peer-review-benchmark.md`；
  - EMNLP 对应 drafting/preflight/reviewer skills；
  - `verticals/research/paper_structural_minimums.py`、academic/language/layout review prompts。
- [ ] 删除或降级为 advisory 的通用硬阈值：
  - 35 verified BibTeX entries；
  - 30 unique cited keys；
  - References 必须占两页；
  - Conclusion 不得早于第 6/7 页；
  - “必须填满 7 页”作为科学质量判断；
  - 单一 model-review score（如 4/5）作为独占完成门。
- [ ] 保留 AAAI 真正官方/结构性硬约束：
  - 当前官方 author kit、匿名模式和 page **上限**；
  - style/preamble/PDF requirements；
  - 引用必须真实且 claim-complete；
  - Reproducibility Checklist 的官方位置；
  - 无未解析 citation/reference、无明显 PDF 破损。
- [ ] 将“篇幅较短”改为 Reviewer 的诊断信号：只有当重要方法、证据或论证功能缺失时才阻断；
  不能仅按页码阻断。
- [ ] Academic language/layout/infrastructure review JSON 保持 advisory evidence；Reviewer 读实际论文，
  不为刷模型分数反复改写自然段。
- [ ] Venue profile 分离：
  - **format facts**：官方模板、页数上限、匿名、end matter；
  - **scientific review calibration**：当前 CFP/track 的 contribution expectations；
  - 不再把 house-style bibliography 数量放进 format profile。
- [ ] AAAI 与 EMNLP 科学 review 不只换 persona 名称：从当年官方 CFP/author kit/track 页面刷新
  venue-specific rubric，并记录 primary source/date。

**验收标准**

- 一篇引用覆盖充分但只有 20 篇参考文献的 AAAI paper 不会仅因数量失败；
- 一篇 6 页、论证完整的 AAAI paper 不会仅因未“填满 7 页”失败；
- 一篇 7 页但没有真实贡献/证据的 paper 仍不能通过；
- fabricated citation、超页、错误模板、缺失 public evidence 在所有 profile 下仍失败。

### TODO P0-05：修复认证后 Stage 推进的 liveness

- [ ] Reviewer `done` 且 stage checklist 被当前 mission 覆盖后，立即触发一次 Manager stage decision；
  不等待一个不相关的 operator 消息或下一次空 backlog 周期。
- [ ] 保持 Manager 是唯一语义 stage authority；Supervisor 只负责可靠触发和超时可见性。
- [ ] 定义 SLO：stage certification 后若无外部 blocker，N 秒/一个 planning tick 内必须得到
  `advance | stay(reason) | rollback(reason)`。
- [ ] `stay` 必须给出仍缺的**当前 stage**价值工作，不能只说“等待 Manager”。
- [ ] cockpit 显示“已认证，等待 Manager stage decision”及等待时长。

**验收标准**

- Kernel scope 认证后自动进入 environment 或得到明确 stay reason；
- 不再出现 Planner 因合法 stage lock 无限 WAITING、同时 Manager 无自动决策的状态。

---

## 4. P1 — 把“好奇心”变成规划与实验机制

### TODO P1-01：Research Exploration Portfolio

- [ ] `frontier`/`balanced` posture 下，每次 scope/replan 至少维护三类候选：
  1. **incumbent**：最可能稳定推进的路线；
  2. **adjacent bet**：中风险、机制明显不同；
  3. **frontier/moonshot**：高风险、高潜在收益、可被廉价 probe 证伪。
- [ ] 候选按多目标排序，不使用单一“可行性分数”：
  - operator value / potential upside；
  - novelty/非重叠性；
  - expected information gain；
  - tractability 与资源；
  - strongest falsifier；
  - toolchain expansion cost。
- [ ] 至少提出一个 counterfactual：
  “如果当前环境/实现限制不存在，最可能改变结论的方案是什么？”
- [ ] 对“已有 PR”做 edit-surface/shape/hardware/forward-backward 级 overlap 分析，而不是 op-name 级
  全量回避。
- [ ] 每轮至少有一个能改变当前认知的实验；重复相同 gate、改名任务、纯文档补齐不计 research
  exploration。
- [ ] 负结果必须产生下列之一：新机理、边界、下一实验、降级 claim 或明确 park reason。
- [ ] 支持 operator 配置探索预算比例，建议 frontier 默认：
  - 50% incumbent；
  - 30% adjacent；
  - 20% moonshot。
  比例是 planning budget，不是机械 GPU 配额。

**实现位置**

- Planner short contract：`roles/prompts/planner.py`；
- 研究方法放 research/kernel vertical skills，不放通用长 prompt；
- 四态 `idea_status` 和 portfolio 进入 CHECKPOINT/项目知识，不制造多份重复 ledger；
- 增加行为回归，不能只测试 prompt 包含某句话。

### TODO P1-02：用 Expected Information Gain 替代“最容易验收”

- [ ] Planner 每个 proposed node 回答：
  - 这个任务会改变哪个决策？
  - 哪个结果会使路线 A/B 的排序翻转？
  - 最便宜的 faithful probe 是什么？
  - 成功/失败后分别做什么？
- [ ] Reviewer 对负结果先检查“它是否真正减少了不确定性”，再决定 continue/replan。
- [ ] 避免因 final publication bar 未达到就否定一个高信息量早期实验。
- [ ] 对 paper idea 使用“孵化状态”，而不是 research stage 内直接进行 final peer-review simulation。
- [ ] `kill-argument` 只用于已成熟 idea/draft 的 adversarial stress test；不得作为所有早期 idea 的
  默认筛选器。

### TODO P1-03：增加“研究主动性”可观测指标

新增事件/投影时保持语义化，不从 prose 关键词猜：

- [ ] 每个 planning cycle 的候选路线数与机制去重后数量；
- [ ] incumbent/adjacent/moonshot 的实际预算分配；
- [ ] 四态 `idea_status` 变化、failure/replan reason 及证据基础；
- [ ] 因环境缺失被 park 的路线中，尝试修复/安装的比例；
- [ ] frontier source freshness、primary-source coverage 和 decision impact；
- [ ] Reviewer rejection taxonomy：integrity / current-scope / maturity / publication-value / external；
- [ ] early-stage idea kill rate；
- [ ] `continue` 同 blocker 重复率与 replan latency；
- [ ] stage-certified-to-manager-decision latency；
- [ ] paper review loop 中纯措辞改写次数 vs 新证据/结构改动次数。

这些指标用于行为评估和 shadow rollout，不直接变成 hard gate。

---

## 5. P1 — Kernel 调研与工具链自治升级

### TODO K-01：修复 Tool Discovery，先找再装

- [ ] `environment_audit.collect_tools()` 不再只依赖 PATH；按以下来源发现：
  - `CUDA_HOME`、`CUDA_PATH`；
  - `/usr/local/cuda`、`/usr/local/cuda-*`；
  - `/etc/alternatives/cuda*`；
  - `torch.utils.cpp_extension.CUDA_HOME`；
  - Triton/backend 自带 compiler/binary 路径；
  - conda/venv prefix、project tool directories；
  - registry entry 声明的 known install roots。
- [ ] 报告区分：`installed_and_on_path`、`installed_not_on_path`、`not_installed`、
  `installed_incompatible`、`probe_failed`。
- [ ] 对 `installed_not_on_path` 生成最小 environment repair proposal，而不是安装同一工具第二份。
- [ ] audit 记录 target Python、effective PATH/CUDA_HOME、真实 executable path 和 smoke result。
- [ ] 环境变化后自动 refresh audit；未变化不重复跑昂贵 probe。

**回归场景**

- `/usr/local/cuda-13.1/bin/nvcc` 存在但 PATH 缺失时，报告“已安装未暴露”；
- 加入 `/usr/local/cuda/bin` 后 `cuda_cpp` 与 `sanitizer` capability 变绿；
- 不得把 clean `pip check` 的 “No broken requirements found” 当 warning（已有在途修复，保留测试）。

### TODO K-02：把 Specialized Tool Registry 升级为可执行知识库

为每个条目逐步增加：

- [ ] `primary_sources`：official docs/repo/releases；
- [ ] `last_verified_at` 与最新稳定/兼容版本，不把缓存当实时真相；
- [ ] `detect`：import、executable、env、known roots；
- [ ] `compatibility`：GPU arch、driver/CUDA、Python、Torch/Triton 约束；
- [ ] `install_profiles`：repo extra、wheel、conda/container、source/pinned commit；
- [ ] `smoke_test`：最小 import/compile/run；
- [ ] `usage_recipe`：如何用于目标 bottleneck；
- [ ] `profiling_recipe`：关键命令/指标与何时使用；
- [ ] `risk`：会否替换 Torch/Triton、是否系统级、磁盘/编译成本；
- [ ] `approval`：project-local 自动、system change 需确认；
- [ ] `fallback`：不支持当前架构时的可信替代。

不要把任意 shell 安装命令直接当可信执行代码。Registry 先生成计划，执行层必须经过参数化 adapter、
sandbox 和 approval policy。

### TODO K-03：Latest Tool/Technique Frontier Refresh

- [ ] scope、toolchain 变化、连续机制失败、准备 PR/report 时进行真实在线 refresh；
- [ ] 检查面至少包括：
  - target repo main/releases/open+merged PR/issues/CI；
  - CUDA/Blackwell docs 与 release notes；
  - PyTorch/Triton/Gluon、TileLang、CUTLASS/CuTe DSL、cuTile 等官方更新；
  - profiler/sanitizer；
  - FlashAttention/FlashInfer/xFormers/Transformer Engine 等相邻实现；
  - 最近论文、OpenReview/arXiv 和作者代码；
  - 与 op、shape、dtype、SM100/B200 直接相关的实现。
- [ ] 搜索结果必须回答：
  - 新工具解决什么瓶颈？
  - 是否支持当前 stack/B200？
  - 如何安装和最小使用？
  - 相比当前路径多出什么控制能力？
  - 是否已有上游工作重叠？
- [ ] 静态 reference 的“last refreshed”只能是 discovery anchor；最终决策必须引用本轮 primary
  source，避免最新工具调查退化为读旧 Markdown。
- [ ] `no_material_update=true` 保留，但必须有真实 queries/source 和 decision impact。

### TODO K-04：Toolchain Repair/Install Planner

- [ ] 新增只生成计划的命令，例如：
  `environment_audit plan-repair --require tilelang,cuda_cpp,sanitizer`；
- [ ] 修复顺序：
  1. 暴露已安装工具（PATH/env）；
  2. 使用 target repo extra/lockfile/container；
  3. 使用隔离 venv/conda/container；
  4. 使用官方 compatible wheel；
  5. 必要时 pinned source/nightly；
  6. 系统 CUDA/driver 变化前请求 operator。
- [ ] 安装前生成 dependency diff，防止 pip 静默替换自定义 NVIDIA Torch/Triton；
- [ ] 安装后依次运行：import → compile → one real kernel → profiler/sanitizer smoke → audit refresh；
- [ ] 安装失败归类 environment failure，idea 保持 untested/inconclusive；
- [ ] 保留回滚/删除新环境的命令，不污染 Argus framework venv。

### TODO K-05：从“工具列表”升级为“优化机制地图”

- [ ] 按 measured bottleneck 维护机制卡：
  - launch/CPU overhead；
  - HBM traffic / memory-bound；
  - tensor-core/compute-bound；
  - occupancy/latency/register pressure；
  - synchronization/producer-consumer boundary；
  - compilation/autotune；
  - communication/distributed；
  - numerical precision/stability。
- [ ] 每张卡包含：诊断指标、候选机制、适合的 DSL/library、B200-specific 机会、最小实验、
  correctness risk、失败解释。
- [ ] B200/SM100 调研至少覆盖并按需验证：TMA、Tensor Memory、warp specialization、persistent
  CTA、cluster、async pipeline、布局/向量化、online reduction/softmax、fusion/recompute-vs-store。
- [ ] 不因“有趣 counter”直接改 kernel；继续执行 timeline → leverage → focused profile → one
  source change → micro A/B 的梯子。
- [ ] 每轮 optimize 至少维护三条机制不同的 hypothesis；参数 sweep 不算三条机制。
- [ ] 鼓励检查生成的 PTX/SASS、launch topology 和实际 dispatch，而不是只看 Python/Triton 源码。

### TODO K-06：Kernel E2E 行为测试

- [ ] **Hidden CUDA install**：工具不在 PATH，但 common root 可发现；Agent 修 PATH 而不是回避
  CUDA C++。
- [ ] **Missing TileLang**：Agent读取 repo extra，生成隔离安装计划并 smoke；失败不 refute idea。
- [ ] **Partial upstream overlap**：一个 KDA PR 只覆盖 backward/某 shape，Planner 仍能提出不重叠
  forward/SM100/dispatch 切口。
- [ ] **Current frontier**：缓存 reference 过期时必须 live refresh；离线时不假装 current。
- [ ] **Three-hypothesis budget**：首个 candidate slower 时，剩余轮次必须尝试机制不同路线或给出
  evidence-backed exhaustion。
- [ ] **Real path evidence**：benchmark 未进入 changed kernel 时 speedup claim 必须失败。
- [ ] **No checklist-only completion**：scope/report done reason 必须包含 substantive decision/result。

---

## 6. P1 — Paper/Idea 孵化与 AAAI 差异化

### TODO P-01：Idea Incubation 复用四态，不做“一轮 kill”

- [ ] research idea 只使用 `untested | inconclusive | supported | refuted` 四态；seed、grounding、
  probe 和 development 是工作阶段/artifact，不再建一套候选状态枚举。
- [ ] early probe 的 Reviewer 问题是“学到了什么、下一 probe 值不值”，不是“现在能否过 AAAI”；
- [ ] publishable target 约束最终 destination，但不要求 seed/pilot 已是 publishable result；
- [ ] Planner 可以 park 一个 `untested`/`inconclusive` candidate，或因 prior art 请求 replan；这些是
  调度决定，不改写成 idea 的 epistemic 状态。
- [ ] 一个 candidate 被 `refuted` 或暂时 park 后，portfolio 中仍有 adjacent/moonshot 候选，避免
  project 与单 idea 同生共死；
- [ ] novelty 风险与 evidence maturity 分开：可以是“高 novelty bet + inconclusive evidence”，
  而不是直接失败。

### TODO P-02：分阶段 Paper Verification Rubric

#### Explore

- [ ] 文献 gap 是否真实；
- [ ] idea 是否可证伪；
- [ ] 最便宜 faithful probe 是否存在；
- [ ] 当前证据只决定下一步，不要求完整 baseline/引用/page/layout。

#### Develop

- [ ] evaluator/implementation faithful；
- [ ] strongest feasible baseline 与 public source；
- [ ] claim 与证据范围一致；
- [ ] paper 可以有 placeholder scaffold，但不能伪造结果；
- [ ] Reviewer 给少数最高杠杆修改，不输出无限 submission checklist。

#### Certify

- [ ] 完整 claim-critical evidence、uncertainty、comparisons；
- [ ] 当前 venue 官方格式；
- [ ] citation correctness/completeness；
- [ ] PDF、figures、anonymity、reproducibility；
- [ ] 独立 Reviewer 给 strongest accept/reject argument；
- [ ] 只有此档可以认证 final submission。

### TODO P-03：AAAI Scientific Profile（与 Format Profile 分离）

- [ ] 每个 AAAI cycle/track 从官方 CFP 刷新：scope、review criteria、reproducibility、special track
  expectations；
- [ ] 格式 profile 只存稳定可验证事实；科学 profile 存当年/track-specific calibration；
- [ ] AAAI review 强调当前 track 的 technical contribution/relevance/evidence，而不是复用 EMNLP
  的语言与 end-matter 偏好；
- [ ] 引用数量、页数使用率、图数量、模型 review score均不得代替 contribution judgment；
- [ ] 对 negative/boundary/diagnostic paper，先判断是否有独立的 surprising、decision-relevant insight；
  没有则 pivot，但不要因结果符号直接 kill。

### TODO P-04：Reviewer 输出“最强 accept case + 最强 reject case”

- [ ] 在 develop/certify paper review 中要求先写 strongest accept argument，再写 strongest reject
  argument；
- [ ] `continue` 只给 1–3 个最高杠杆 gap，避免每轮生成完整 laundry list；
- [ ] 明确 gap 类型：new evidence / implementation adequacy / claim scope / writing / format；
- [ ] writing/format gap 不得被升级成 idea refutation；
- [ ] certification reject 不等于 research idea permanently killed。

### TODO P-05：Paper 回归集

建立匿名化 fixture/replay，至少覆盖：

- [ ] 早期但有潜力的 idea：Explore 通过进入下一 probe，Certify 不通过；
- [ ] under-engineered negative：不得变成科学反证；
- [ ] 高质量窄 claim：少量但完整文献/benchmark 可以通过 Develop；
- [ ] 20 篇高相关引用的完整 AAAI paper：不因引用数量单独失败；
- [ ] 6 页完整 AAAI paper：不因 underfill 单独失败；
- [ ] 7 页无贡献 paper：Certify 失败；
- [ ] fabricated/stub evidence：所有 profile 失败；
- [ ] final paper 格式错误：Explore/Develop 可继续，Certify 阻断；
- [ ] mature negative/boundary insight：允许形成 paper，而不是正结果偏置。

---

## 7. P2 — Operator 控制与 Cockpit 可见性

### TODO UI-01：项目级策略控制

- [ ] Cockpit 显示：
  - final research target；
  - exploration posture；
  - configured verification profile；
  - effective current profile；
  - profile 来源（operator/stage/final scope）。
- [ ] 支持 Manager 自然语言修改，例如：
  - “这个项目用 frontier 探索模式”；
  - “当前只做 exploratory verification”；
  - “进入投稿前切回 certify”；
- [ ] 降低最终 completion bar 时显示影响并要求确认；提高 bar 可直接确认写入。
- [ ] 当前 profile 不得只靠进程 env；必须项目持久化、可恢复、可审计。

### TODO UI-02：展示“为什么没继续探索”

- [ ] 显示四态 `idea_status` 的证据基础，并单独显示 park/replan reason；
- [ ] 对 toolchain 排除显示：未安装、已安装未暴露、不兼容、成本高、operator 拒绝；
- [ ] 对 Reviewer reject 显示 gap 类型，不只显示红色 rejected；
- [ ] 对 stage WAITING 显示 authority/blocker/recheck 条件；
- [ ] 展示最近一个 moonshot 和为何执行/未执行，防止 frontier posture 只是标签。

---

## 8. P2 — 评估、Shadow Rollout 与安全上线

### TODO E-01：先记录基线

- [ ] 从近期 Kernel 与 Paper 项目抽取匿名化行为样本；
- [ ] 统计 early kill、相同 blocker 循环、环境 false negative、纯 checklist completion、
  paper wording loop、stage waiting；
- [ ] 标注人工认为的 false reject / correct reject / false accept 风险。

### TODO E-02：Shadow Reviewer/Planner

- [ ] 同一 artifacts 同时运行当前策略和新 profile，但 shadow 不改变状态；
- [ ] 比较：verdict、四态 `idea_status`、failure/replan reason、next action、证据打开量、token/cost；
- [ ] 重点审查“少杀 idea”是否伴随 integrity false accept；
- [ ] 只有在 fabrication/stub/citation 错误回归保持 100% 阻断后，才激活新策略。

### TODO E-03：上线成功指标

建议目标，不直接写成永久硬阈值：

- [ ] 环境工具 false-negative 显著下降；
- [ ] early-stage idea 因“尚未投稿级”被 kill 的比例下降；
- [ ] Kernel scope 后至少出现一个非 incumbent hypothesis；
- [ ] 环境 blocker 被主动调查/修复的比例上升；
- [ ] 相同 Reviewer blocker 的重复轮次下降；
- [ ] Paper 纯措辞/刷分循环下降；
- [ ] final submission 的 fabricated/stub/unsupported evidence 检出率不下降；
- [ ] token 与 wall-clock 增长在 operator 选定 exploration budget 内。

---

## 9. 建议实施顺序与依赖

| 顺序 | ID | 工作 | 依赖 | 完成定义 |
|---:|---|---|---|---|
| 1 | P0-01 | 双轴 policy + effective resolver | 无 | profile 可持久化、可解释、进入四角色短 context |
| 2 | P0-02 | checklist maturity metadata | P0-01 | stage/profile 渲染正确，integrity floor 永远存在 |
| 3 | P0-03 | 四态 idea evidence | P0-01 | mission done 与 idea refuted 不再混淆 |
| 4 | P0-04 | AAAI/通用 paper 冲突清理 | P0-01/02 | 无引用/page house quota 硬误杀 |
| 5 | P0-05 | stage certification liveness | 无 | Reviewer done 后 Manager 及时决定 stage |
| 6 | K-01 | hidden tool discovery | 无 | CUDA PATH 案例通过 |
| 7 | K-02/K-04 | registry 操作化 + install planner | K-01 | 能给安全安装/使用/smoke 方案 |
| 8 | P1-01/P1-02 | exploration portfolio/EIG | P0-03 | 每轮存在机制不同候选与决策实验 |
| 9 | K-03/K-05 | live frontier + mechanism map | P1-01/K-02 | 最新工具真正改变优化计划 |
| 10 | P-01..P-04 | paper incubation 与 venue calibration | P0-01..04 | Explore/Develop/Certify 行为分离 |
| 11 | UI-01/UI-02 | Operator 控制和可见性 | policy/disposition | 可调、可解释、可恢复 |
| 12 | E-01..E-03 | shadow + rollout | 上述能力 | 不牺牲 integrity 的前提下降低误杀 |

---

## 10. 最小可交付切片（建议第一个 PR）

为了避免一次改完整个框架，首个 PR 只做以下闭环：

1. [ ] 新增 `adaptive | explore | develop | certify` verification profile resolver；
2. [ ] publishable research target 在 research stage 自动得到 `explore`，final_submission 强制
   `certify`；
3. [ ] Reviewer prompt 明确“current mission bar”与“project final target”分离；
4. [ ] 删除 AAAI 的 35/30 与满 7 页 hard blocker，保留官方 format/integrity gate；
5. [ ] environment audit 发现 `/usr/local/cuda*` 下的 hidden tools；
6. [ ] 添加五个核心回归：
   - publishable target + explore bounded probe；
   - final submission 强制 certify；
   - fabricated evidence 全 profile 失败；
   - AAAI 无引用数量单独误杀；
   - CUDA installed-not-on-PATH 被正确识别。
7. [ ] 增加 effective verification profile 的事件/状态展示。

首个 PR **不**实现自动安装、不新增大段 prompt、不降低 final submission Reviewer 权威。

---

## 11. 明确不做的方案

- 不增加一个全局 `LENIENT_REVIEWER=1` 开关；它会把真实性与成熟度一起放松。
- 不把 Reviewer 关闭；当前 Engineer → independent Reviewer 不变。
- 不让低 profile 认证 final submission。
- 不因为“好奇心”自动升级 driver、替换系统 CUDA 或污染共享 Python。
- 不强制安装所有 DSL/library；必须由 bottleneck 和信息增益驱动。
- 不增加固定 paper 数、引用数、idea 数、实验数作为新的替代目标。
- 不把 frontier search 变成每 stage 重复的文档仪式；继续 event-driven。
- 不把一个模型 review score 当作科学真相。
- 不通过降低 correctness/benchmark/anti-fabrication 标准来提高 idea survival。
- 不只改 Skill 文案而没有 resolver、状态、行为测试和 rollout 指标。

---

## 12. 完成后的目标行为示例

### Kernel

> Agent 发现 TileLang import 失败，但先发现 `/usr/local/cuda-13.1/bin` 已有 nvcc/ptxas/
> sanitizer，只是 PATH 未暴露；修复隔离环境并 smoke。它同时保留 Triton incumbent、Gluon
> adjacent 和 TileLang/CuTe frontier 三条路线，用 B200 timeline/leverage/profile 决定先做哪条。
> 某条编译失败时 idea 仍是 untested；只有真实 path-covered A/B 才能 refute 精确机制。

### Paper idea

> 项目最终目标是 publishable AAAI paper，但 research stage 使用 explore profile。Reviewer
> 不问“现在能不能接收”，而问“gap 是否真实、premise 是否可测、这个 probe 是否改变决策”。
> Pilot 为负时先审计实现充分性；若 faithful，记录 boundary/refuted premise 并比较 portfolio 中
> 下一候选。只有 review/submission 才加载完整 AAAI certification bar。

### Final submission

> 无论前期探索姿态多激进，final_submission 自动进入 certify：真实 public evidence、claim
> coverage、引用、官方模板、页数上限、匿名、PDF、图表和独立 venue review 全部检查。探索更
> 大胆不意味着最终声明更宽松。
