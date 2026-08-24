# ARGUS / FLYWHEEL

Argus Research Data Flywheel 是一个与 Argus 源码和运行目录完全隔离的研究活动控制平面。它把会议日历、团队条件、候选 Idea、结构化研究合同、算力与模型配置、Argus 实时状态、独立 Viewer 评审、人工标注和投稿后反馈放到同一套界面里。核心关系是“团队条件决定可行研究空间”：不同团队即使面向同一会议，也可以因专长、方法、数据权限、资源、时间、目标和政策不同而得到不同的 Idea 与 Prompt。

它物理上位于 `Argus/companions/FLYWHEEL`，逻辑上是围绕 Argus 的伴生产品：Argus 继续负责模型、角色、daemon、研究执行与工件；FLYWHEEL 负责会议与团队条件、Prompt/Protocol 编译、人工门、可观测性和不可变数据闭环。两者通过版本化 WebAPI 连接，FLYWHEEL 不直接 import 或复制 `argus_skill` 内部实现。

```text
Research team → FLYWHEEL control + data plane → versioned Argus WebAPI → Argus research engine
                    ↑                                      ↓
                    └──── immutable episodes ← evidence / artifacts / reviews
```

当前内置时间窗为 **2026-08-22 至 2027-08-22（含首尾）**，会议 universe 是种子文件明确列出的 **58 个 CCF-A 会议**，不是“全球所有学术会议”。它包含 **85 个 Full/Regular Paper deadline 事件、290 个 baseline seed Idea（每会 5 个）**：其中 28 个日期为 `official_confirmed`，57 个为 `forecast`。预测日期和预测区间不是官方事实，必须在锁定执行或投稿前回到会议官网复核。PPoPP、NDSS、SIGKDD 的预测点在窗内，但不确定区间上沿越过 2027-08-22；界面保留完整区间，不把未来实际日期伪装成已知。

这里的 **290 = 58 个 venue × 每会 5 个 seed 候选**。它是用于冷启动、界面覆盖和回归审计的 **seed coverage baseline**，不是适用于所有团队的最终推荐，更不是 290 个已经验证 novelty 的答案；manifest 明确 `launch_ready=false`。静态导出器为每个 venue 绑定其最早规划目标，并不是 85 个 deadline/round 各生成 5 个候选（不是 `85 × 5`）；指定轮次的真实 Campaign 必须另外绑定具体 `deadline_id`。生产 ideation 应先创建 TeamProfile，冻结团队/resource conditions 与可选 source snapshot digest/provenance；没有 source binding 时由 Argus 先生成和冻结真实 source snapshot，再生成个性化候选。

## 设计边界

- Flywheel 不修改 `Argus/` 或 `argus-skill/`，也不会对正在运行或有未提交修改的 checkout 执行 `pull/reset`。
- 远端检查只执行 `git ls-remote` 并原子记录 registry。候选 SHA 只有在操作者提供完整 SHA、显式确认且远端 ref 完全匹配时，才会 fetch 到 Flywheel 自己的内容寻址 staging 目录；不会修改现有 checkout。测试、canary 和采用仍是后续独立人工流程。
- `Oral / Best Paper` 是研究与评审标准的高目标，不是完成条件、录用概率或可由模型自我认证的结果。
- 负结果、`NO_WINNER_YET`、collision 与 kill criterion 都是合法、需要保留的科研结果。
- 实际投稿、署名、AI 使用披露、伦理批准、重大预算扩张和活动中版本迁移始终需要人。
- Flywheel 可以导出经过同意、许可和脱敏门控的标注 JSONL，但不会自动训练、上传数据、启动后续 Campaign 或提交论文/rebuttal。
- TeamProfile 是可移植的条件化工作区画像，不是 SaaS tenant 或安全主体。Flywheel API 目前不内置用户认证、RBAC、租户行级隔离或租户密钥隔离；互不信任团队必须使用独立部署、数据库、runtime 和凭据。若要跨主机访问，必须放在带 TLS、身份认证和访问控制的反向代理之后。

## 主要能力

- **Evidence Horizon**：会议时间轨、官方/预测 deadline、D−180/90/30/14/7/2 提醒与真实 `.ics` 日历。
- **Team-conditioned Ideation**：TeamProfile 记录专长、方法、数据权限、约束、目标与政策；每次 run 冻结不可变 condition snapshot，并编译 Builder/Breaker/Arbiter + 独立多审稿人的 Argus Objective。
- **Idea Radar**：按会议浏览 290 个 seed baseline，接入 arXiv、OpenReview API2 和 GitHub 后显示来源变化、最近邻与启发式差异；不会把 seed 或词面相似度伪装成 novelty 证明。
- **Prompt Factory**：将会议、领域、Idea 和实际资源编译为内容寻址的 Portfolio/Locked Argus 合同；`POST /api/campaigns/{id}/locked-contract` 只冻结人工批准的不可变合同，不会启动 Argus 或投稿。输入字段、版本与晋升语义见 [Argus Prompt 模板](docs/PROMPTING_ARGUS.md)。
- **Campaign Cockpit**：把 daemon 是否存活、是否有科学进展、证据状态、截止风险、完整事件和 artifact 分开显示。
- **独立 Viewer**：用单独 PID、fresh workdir 和 allowlisted、限长、内容寻址的只读证据快照执行目标会议 rubric；未配置 evaluator 或没有合格证据时明确等待，不制造分数。
- **Annotation & Preference loop**：保存逐维 0–10/`null` 标量标签、`shortlist/revise/reject/abstain` 决策和候选 pairwise preference；按 run/campaign 做 group-safe split。
- **Outcomes & Rebuttal**：记录人工提供且已脱敏的真实审稿意见、分数、问题和决定；可冻结 rebuttal Objective 并创建 idle follow-up Campaign，仍需另一次人工 Start。
- **Connections & Resources**：连接本机或服务器 Argus WebAPI；GPU 由 `nvidia-smi` 探测，也可添加集群、CPU 或 API-only 资源。
- **安全 Release channel**：只读远端 SHA 检查、原子 registry 与显式确认的隔离 exact-SHA staging；不会自动测试、canary、采用或迁移运行中的 Campaign。
- **Research Data Vault**：把条件、候选、Prompt、Argus 轨迹、论文、内部/外部评审与结果串成 `Research Episode`；每次人工封存只追加 SHA-256 父链 revision，旧版本不能覆盖。
- **Dataset snapshots**：先预检 consent、license、redaction、Head 当前性与完整性，再由人确认不可变的 episode-revision 成员快照；训练分组在未来训练审批中单独冻结，本产品不会自动训练、上传或启动下一轮研究。

## 可靠数据闭环

```text
一句话团队条件 → 人工确认画像 → 会议与来源 → 10 个条件化候选
→ 人工选择 → Research Protocol v2 → 独立批准 Start → Argus 执行
→ 证据 / 实验 / 论文 → 双阶段独立评审 → 投稿与 rebuttal
→ 外部评审确认 → Episode revision → Dataset snapshot
```

Builder Argus 与 Breaker Argus 在同一项目协议内对冲，Arbiter 只依据可审计证据选择；五位 Reviewer 使用 fresh context。`NO_WINNER`、novelty collision、资源不可行、负结果与不确定结果都是合法终态。用户可以自由填写追求，但 “Oral / Best Paper / 独一无二” 只会被编译成更严格的证据与评审目标，不会被系统伪装成保证。

界面作为 Argus 的独立副产品，参考并延伸 Argus 的品牌标志、蓝色主强调、Geist/中文字体回退、紧凑工作台和角色语义；Evidence Horizon、数据标注与审稿闭环是 Flywheel 自己的控制平面信息架构，不会修改上游 Argus UI。

## 从团队条件到“百变” Prompt

推荐的生产入口不是直接挑静态 seed，而是：

1. `POST /api/team-profiles` 记录团队真实专长、方法、数据权限、时间/算力限制、目标与合规政策；
2. `POST /api/ideation/runs` 绑定 venue/deadline/resource/connection、候选数和可自定义 completion target；可选 source ref 与恰好 64-hex SHA-256 必须成对。condition schema v3 冻结团队最初的一句话原文及其摘要，并只把 source reference/content digest 和 freshness 状态写入 condition；
3. Flywheel 在 `runtime/ideation-objectives/<objective-sha256>/` 冻结 `CONDITION_SNAPSHOT.json` 与 `IDEATION_OBJECTIVE.md`，可选地只创建一个 idle `conditioned_ideation` Campaign；
4. 人工用带 `human_approved=true`、非空 `approval_reason` 和 `actor` 的独立 Start 请求授权 Argus；
5. Argus 以 Builder、Breaker、Arbiter 轨道生成候选，独立 Viewer panel 逐证据评审；人工再提供逐维标签和 pairwise preference；
6. 人工选定一个已通过 manifest 绑定的候选后，`POST /api/ideation/candidates/{candidate_id}/campaign` 为它冻结独立 Prompt/contract 并只创建 `idle` Campaign；另一个候选、团队或目标都会得到不同哈希；
7. 研究完成后，Research Episode 必须绑定真正执行该候选的 Campaign，而不是前置 ideation Campaign；condition、candidate artifact/record/input/prompt 与 binding receipt 一并进入不可变谱系；
8. 训练资格还必须重新验证来源：Episode/outcome 只接受具有完整不可变 receipt 的 `conditioned_candidate_research`，或可回溯到它的合法 `rebuttal_follow_up`；seed、手工、无绑定和前置 ideation Campaign 可保留为档案，但永不进入训练。未执行或被淘汰的标量/成对候选判断仍可作为负样本，但必须重新验证 conditioned run、condition/objective 文件、candidate manifest/portfolio/record SHA，并同时满足 consent、license basis 与 redaction/pseudonymization gate。响应明确标记不会自动训练。

完整字段、数据谱系、JSONL schema、group-safe split 和投稿后闭环见 [个性化与数据集说明](docs/PERSONALIZATION_DATASET.md)。

## 本机快速启动（PowerShell）

要求 Python 3.11+、Node.js 22.12+。先按仓库根 README 安装并完成 Argus 自检，再安装 Flywheel：

```powershell
# Run from the Argus repository root.
Set-Location .\companions\FLYWHEEL
.\scripts\bootstrap.ps1
```

开发模式先启动 Argus 控制面，再启动 Flywheel：

```powershell
# 终端 1 · Argus（继续使用 Argus 自己的 provider/model/API 配置）
# Windows 按仓库根 README 使用 direct-pip Scripts entry point，不创建根目录 venv。
$Scripts = py -c "import sysconfig; print(sysconfig.get_path('scripts'))"
$Argus = Join-Path $Scripts "argus.exe"
if (-not (Test-Path -LiteralPath $Argus)) { throw "Install Argus from the repository README first." }
& $Argus --web

# 终端 2 · Flywheel API
Set-Location .\companions\FLYWHEEL
.\.venv\Scripts\python.exe -m uvicorn foundry.app:app --app-dir backend\src --host 127.0.0.1 --port 8743 --reload

# 终端 3 · Flywheel UI
npm --prefix frontend run dev -- --host 127.0.0.1 --port 5175
```

打开 `http://127.0.0.1:5175`。API 文档位于 `http://127.0.0.1:8743/docs`。

也可使用隔离容器运行（默认只绑定本机 `127.0.0.1:8080`）：

```powershell
docker compose up --build
```

运行完整本地检查：

```powershell
.\scripts\check.ps1
```

## 连接 Argus

1. 在本机或服务器启动 Argus WebAPI。默认本机地址是 `http://127.0.0.1:8799`。
2. Flywheel 启动时会根据 `FLYWHEEL_ARGUS_BASE_URL` 自动登记一个 managed connection，并用 `FLYWHEEL_ARGUS_TOKEN_ENV` 指向与 Argus 相同的 token 环境变量；不复制模型 API Key。需要多实例时再从 **Connections** 添加；远程 Bearer 连接必须使用 HTTPS。
3. API 只允许在服务端配置的**同一个 `FLYWHEEL_ARGUS_BASE_URL`** 上引用明确配置的 `FLYWHEEL_ARGUS_TOKEN_ENV`；变量名或 endpoint 任一不匹配都会在建立网络客户端之前失败，不能把 `GITHUB_TOKEN` 等任意进程环境变量、也不能把 Argus Token 发往其他地址。其他 endpoint 可提供一次性的 literal token；它只保存在当前 Flywheel 进程的内存 vault 中。两种方式互斥，均不写入 SQLite，也不返回浏览器。
4. Test 会同时校验协议、`daemon.command.v1`、snapshot schema 和只读 `/api/system/doctor` backend readiness。成功后，为 Campaign 选择该连接、已配置的资源与 Argus release。WebAPI 启动必须保持 `backend=connection-default`；若没有完整 SHA，manifest 会如实记录 `release_pinned=false`，不应将其当作可复现的生产执行。
5. 条件化 ideation 与候选 Campaign 在创建时已经冻结各自的 Objective/contract。Locked Contract 是额外的确认性实验版本工具，本身不会让 seed 或手工 Prompt 获得执行资格。生产 Start 只接受可重新验真的 `conditioned_ideation`、`conditioned_candidate_research`，以及来源为有效候选 Campaign 的 `rebuttal_follow_up`；随后 Flywheel 才会在 `runtime/campaigns/<id>/` 写入启动 Prompt/manifest。本机 connection 使用 Flywheel 专属 isolated workspace；remote connection 向目标 Argus 发送空 `workdir/launch_cwd`，让目标实例在自己的 `ARGUS_SKILL_HOME` 下分配 workspace，避免把 Flywheel 主机路径泄漏给远端。

Argus 本身支持 Pi、Copilot、Codex、Claude、OpenCode、Grok、Qoder 与 DSH backend，但当前 `CreateDaemonIn` 只接受 objective/name/workdir/launch_cwd 与命令收据字段，**不接受每次 launch 的 backend override**。Flywheel 因此不能在一次 Start 中把目标实例临时切成 Pi 或 Copilot；非 `connection-default` 的请求会被 409 拒绝。角色 backend 必须先在目标 Argus 上配置，Flywheel 再把后续真实 snapshot（包括其中实际报告的 daemon/role backend 信息）原样持久化。Pi 或 Copilot 也不适合作为 Flywheel 的状态内核：期限、幂等命令、证据账本、审批和恢复必须由确定性的控制平面负责。Viewer 应使用另一个 provider，或至少使用独立进程与 fresh context，避免 Campaign 自评。

Release monitor 明确区分 Microsoft 官方 release origin 与显式选择的 preview origin；不同 origin 或 SHA 绝不能因 README 声称同步就视为同一构建。仓库中的兼容性文字不是持续有效的远端事实、stable/canary 认证或 Campaign pin。每次 Connection Test 都会重新记录目标实例实际返回的 revision、protocol、snapshot schema 和 capabilities；目标实例的 live snapshot 才是 backend 真值，本机是否安装某个 CLI 与此无关。

## Locked Contract 与启动边界

`POST /api/campaigns/{campaign_id}/locked-contract` 是额外的确认性实验版本工具，要求关联的 Idea、deadline、资源合同、显式时区墙钟截止、全部 preflight attestations，以及人工批准的 primary claim/metric/minimum effect/data split/seeds/baselines。它不会把 seed/unbound Campaign 变成可执行任务；生产 Start 仍须通过上面的条件化来源校验。每一版写到：

```text
runtime/campaigns/<target-id>/contracts/locked-vN-<contract-sha256>/
  OBJECTIVE.md
  MANIFEST.json
```

- 从未启动且仍为 `idle` 的 Campaign：在原 Campaign 内生成 v1、v2……；相同请求幂等返回现有版本，不覆盖旧文件。
- 已有启动生命周期痕迹、但不处于 active 状态的 Portfolio：保留 source Campaign 和其收据/工作区不变，创建新的 `idle`、`hypothesis_locked` child Campaign，并记录 `promoted_from_campaign_id`。
- `starting`、`running` 或 `draining`：返回 409，必须先安全暂停，不能对 active Campaign 原地晋升。
- 冻结操作不会连接 Argus，不会触发 launch，也不会触发 submission。启动 child 是另一个显式动作；Start 必须包含 `human_approved=true`、非空 `approval_reason` 和 `actor`，并从不可变目录重读和校验路径边界、Campaign 绑定、版本、request/contract/prompt hash、资源合同、连接兼容性、并发与 preflight，然后才创建 daemon。授权正文和时间会冻结在 launch manifest，retry 必须完全复用。

真实 Start 还要求 `wall_clock_deadline` 是带显式 UTC offset 的 ISO-8601 时间，例如 `2027-07-01T18:00:00+08:00`；缺失或无 offset 会返回 409。对 `official_confirmed` 目标，其本地日期不得晚于官方 deadline date；对 `forecast` 目标，不得晚于 `forecast_window_start`，而不是预测点或区间末端。

## 独立 Viewer

Viewer worker 使用 JSON 文件队列：

```powershell
.\.venv\Scripts\python.exe -m foundry.workers.viewer_worker --queue-dir runtime\viewer --once
```

要得到真实评分，需配置一个遵循 [Viewer 协议](prompts/VIEWER_PROTOCOL.md) 的独立 evaluator 命令。Review API 先从目标 Argus 的 allowlisted artifact preview 冻结 `runtime/viewer/evidence-snapshots/<sha256>/EVIDENCE_SNAPSHOT.json`，而不信任客户端本地路径；默认上限是 24 个 artifact、单个 64 KiB、总计 512 KiB。凭据必须由 adapter 从环境读取，不能出现在命令行。没有 evaluator 或可读证据时，结果为 `awaiting_evaluator`/`score:null`，不会生成占位分数。

## 数据源

- arXiv：按查询每日缓存，并遵守连续请求间隔。
- OpenReview：使用 API2 的 `content.venueid` 查询目标会议论文。
- GitHub：支持 ETag/条件请求并暴露 rate-limit 状态。
- 离线或限流时可返回明确标注的 cache/stale/demo；绝不把 demo 当实时来源。

详细配置见 [集成说明](docs/INTEGRATIONS.md)，个性化与训练数据边界见 [个性化与数据集说明](docs/PERSONALIZATION_DATASET.md)，产品边界见 [产品规格](docs/PRODUCT_SPEC.md)，系统分层见 [架构](docs/ARCHITECTURE.md)，验收追踪见 [需求矩阵](docs/REQUIREMENTS.md)。

为全部 290 个候选导出资源绑定的 Prompt Packet：

```powershell
.\.venv\Scripts\python.exe scripts\export_prompts.py `
  --gpu-count 1 --gpu-model "Operator-verified GPU" --gpu-hours 24 `
  --wall-clock-deadline "2026-09-01T18:00:00+08:00" `
  --max-parallel-jobs 1 --api-budget "2M tokens hard cap" `
  --output runtime\prompt-catalog
```

以上数值只是命令格式示例，不是任何团队的资源承诺。执行前必须重新探测或由操作者核验实际 GPU 型号、GPU-hour、API hard cap、最大并发和墙钟截止；生产路径应以 TeamProfile、ResourcePool 和冻结的 condition snapshot 为准。对 CSCW 这类 rolling venue，墙钟时间只作为内部规划 cutoff，不会被伪装成官方投稿截止日。seed 中出现的算力文字只记录冷启动构思假设，绝不能覆盖运行时资源合同。

导出器会对每个会议分别选择最早的可用 Full/Regular Paper 目标；预测目标使用预测区间最早端。若传入的统一墙钟截止晚于该保守目标，单个 Packet 会自动收紧到该日的 UTC 起点并在 `CATALOG.json` 标记 `resource_deadline_clamped=true`，不会生成“截止日以后仍可运行”的假合同。

导出完成后可直接打开 `runtime/prompt-catalog/CATALOG.md` 浏览全部 290 个 seed，并进入各自的 Prompt、粗略想法和 manifest。`CATALOG.md` 是 Portfolio coverage baseline，不是 TeamProfile 条件化输出、290 篇已锁定论文、85 个轮次的展开表或 novelty/录用证明。

公开的产品目标与边界见 [产品规格](docs/PRODUCT_SPEC.md)。可验证验收和上线前人工门见 [验收清单](docs/ACCEPTANCE.md)，备份、恢复、安全和故障处置见 [运维手册](docs/OPERATIONS.md)。

## 目录

```text
companions/FLYWHEEL/
├── backend/                 FastAPI、SQLite、调度器与 adapters
├── frontend/                React/Vite Evidence Horizon UI
├── data/seeds/              独立会议与 Idea 快照
├── prompts/                 Prompt/Viewer 协议示例
├── docs/                    产品、个性化数据集、架构、集成与验收说明
├── scripts/                 安装、检查与导出工具
└── runtime/                 本地数据库、Campaign、缓存与 Viewer 队列（忽略提交）
```
