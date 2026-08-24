# Team-conditioned ideation and dataset loop

Flywheel 的个性化单元不是“会议名称 -> 固定 Prompt”，而是：

```text
TeamProfile × venue/deadline × resource × source-snapshot binding × operator target
  -> immutable condition snapshot
  + connection binding and source provenance in the ideation-run ledger
  -> content-addressed Argus ideation objective
  -> Builder / Breaker / Arbiter candidate portfolio
  -> independent reviews + human scalar/pairwise labels
  -> consent/license/redaction-gated JSONL
  -> post-submission outcome and rebuttal feedback
```

同一个会议交给不同团队，应当得到不同的可行空间、候选 Idea 和 Prompt。仓库中的
290 个条目只是覆盖 58 个会议的 **seed coverage baseline**，用于浏览、回归测试和
冷启动讨论；它们不是“对所有团队都最合适的 290 个答案”，也不享有 novelty 推定。

这里的 TeamProfile 是可移植的条件化工作区画像，**不是 SaaS 多租户安全边界**。
当前 API 没有用户认证、RBAC、tenant 行级策略或 tenant-scoped secret isolation。
互不信任的团队必须使用独立部署、数据库、runtime 目录和凭据；不能依赖
`team_profile_id` 阻止另一位实例使用者读取或修改记录。

## 1. TeamProfile：先描述谁来做，再讨论做什么

`POST /api/team-profiles` 保存可修改的团队画像；历史语义由每个 run 自己冻结的条件
快照保留，而不是靠 TeamProfile 行版本。当前数据合同包括：

- `expertise`：团队真实掌握的领域知识、工程系统和理论能力；
- `methods`：能够可靠执行的方法、仪器、实验范式与统计工具；
- `data_access`：公开或已获授权的数据、任务、平台和 testbed；
- `constraints`：GPU/API/参与者/设备/人员/日历、不可使用的数据与其它硬边界；
- `goals`：目标贡献类型、学习目标、期望 information gain 和可接受终态；
- `policy`：伦理、隐私、双重用途、AI disclosure、署名与会议合规要求；
- `training_consent` 与 `license_basis`：只控制该画像衍生标注能否进入显式导出，
  不等于自动训练许可。

示例：

```http
POST /api/team-profiles
Content-Type: application/json

{
  "name": "compiler-systems-small-lab",
  "expertise": ["compiler runtime", "GPU profiling"],
  "methods": ["systems measurement", "causal profiling"],
  "data_access": ["public benchmark suites", "team-owned telemetry"],
  "constraints": {
    "people": 2,
    "gpu_count": 1,
    "no_human_subjects": true,
    "elapsed_days": 45
  },
  "goals": {
    "contribution": "mechanism plus reproducible systems evidence",
    "completion_target": "three defensible finalists for human selection"
  },
  "policy": {
    "public_or_authorized_data_only": true,
    "automatic_submission": false
  },
  "training_consent": false,
  "license_basis": ""
}
```

修改 TeamProfile 只影响未来的 ideation run。已经创建的 run 不回读最新画像，而是
保留创建当时的不可变 `condition_snapshot`，因此后来可以回答“这个候选是在什么
条件下产生的”。

## 2. 条件化 Ideation run

`POST /api/ideation/runs` 将画像与指定会议、deadline、资源、source binding 和可自定义
终止目标编译在一起，并在 run ledger 另行绑定 connection/provenance。
`source_snapshot_ref` 与 `source_snapshot_sha256` 必须同时提供或同时为空；后者必须是
恰好 64 个十六进制字符。condition schema v3 还冻结最初的团队条件原文与摘要；其 `source_context` 只保存
`operator_snapshot_bound`、对 ref 再做 SHA-256 得到的 `reference_sha256`、内容的
`content_sha256` 与 `fresh_discovery_required`，不会把 raw ref 复制进 condition 或训练
导出。为运行时定位，`IDEATION_OBJECTIVE.md` 本身会包含 JSON-encoded raw ref；因此 ref
不能包含 secret。Argus 使用前必须逐字节验证 content SHA-256，不匹配即 `BLOCKED`；
匹配后保留原 packet，并以另一个版本化 freshness delta 补充新发现，不能静默替换。
未提供 binding 时，Objective 必须先检索并冻结真实 `SOURCE_SNAPSHOT.json`，再进入候选
生成。`preflight_attestations` 也进入同一个 v2 condition hash。
`candidate_count` 当前为 3–20，`finalist_count` 不得超过前者：

```http
POST /api/ideation/runs
Content-Type: application/json

{
  "team_profile_id": "<profile-id>",
  "venue_key": "SOSP",
  "deadline_id": 123,
  "resource_id": "<resource-id>",
  "connection_id": "<connection-id>",
  "candidate_count": 8,
  "finalist_count": 5,
  "completion_target": "Return a falsifiable portfolio; NO_WINNER is acceptable.",
  "source_snapshot_ref": "artifact://reviewed-source-packet",
  "source_snapshot_sha256": "<exactly-64-hex-sha256>",
  "create_campaign": true,
  "preflight_attestations": {
    "compute_inventory_and_capacity_verified": true,
    "data_access_and_license_reviewed": true,
    "non_compute_prerequisites_reviewed": true
  }
}
```

后端把 canonical condition JSON 和 Argus objective 写到 Flywheel 自己的内容寻址目录：

```text
runtime/ideation-objectives/<objective-sha256>/
  CONDITION_SNAPSHOT.json
  IDEATION_OBJECTIVE.md
```

数据库的 `ideation_runs.condition_snapshot`、`objective_sha256`、source snapshot 引用和
可选 `campaign_id` 构成溯源链。若 `create_campaign=true`，只创建
`campaign_kind=conditioned_ideation` 的 **idle Campaign**；它不会因 run 创建而启动
Argus。真实 Start 仍需单独提交 `human_approved=true`、非空 `approval_reason` 和
`actor`，并重新核对 condition/objective 的冻结文件与哈希，再通过连接、release、
资源、并发、时钟与 preflight gate。普通 seed/manual/unbound Campaign 不具备生产执行资格。

## 3. Prompt 的“百变”来自条件，不来自随机改写

`backend/src/foundry/services/ideation.py` 生成的 Objective 把下列内容冻结在同一
condition hash 下：

1. 团队专长、方法、数据权限、约束、目标与政策；
2. 会议 scope 元数据及该轮 deadline 的证据状态；
3. 实际资源 profile 与 operator completion target；
4. Oral-level evidence aspiration，但明确 `positive_result_required=false`；
5. `automatic_submission_allowed=false`。

因此“百变”是可解释、可复现的条件变化：同一团队改了 GPU 数量、数据许可或用时，
应产生新的 condition hash；另一个团队即使面向同一会议，也不继承前一团队的候选。
禁止只靠 temperature 或随机措辞制造表面多样性。

Objective 要求先冻结 `SOURCE_SNAPSHOT.json`，严格区分官方事实、点快照、forecast 与
推断，并把静态 seed 仅作为 coverage probe 和反例。外部来源应优先使用会议官方页、
官方 proceedings/OpenReview、primary paper/arXiv 与 upstream GitHub commit；社交
媒体只能提供线索，不能单独支撑 novelty 或事实性主张。

## 4. Builder / Breaker / Arbiter 与独立多审稿人

条件化 Objective 定义三个工件轨道：

- `DEBATER_A_BUILDER` 从团队独特能力组合和可访问证据出发，提出带机制、falsifier、
  decisive experiment、最近工作与成本估计的候选；
- `DEBATER_B_BREAKER` 在看见 Builder 结论前独立重建可行空间，然后攻击 collision、
  headroom、数据权限、时间/算力、metric gaming、baseline、伦理与 venue mismatch，
  并可提出自己的候选；
- `ARBITER` 只在两边工件冻结后比较，保留分歧、允许 tie，并可返回 `NO_WINNER`。

这些是 Argus mission 内的隔离 reasoning tracks/artifacts。只有运行 telemetry 真能
证明时，平台才可声称它们是两个独立 OS daemon；默认文案不做这种推断。

Arbiter 之后要请求 fresh-context 的独立评审，至少覆盖 novelty/collision、
methods/statistics/falsifiability、resource/schedule、venue/compliance、integrity/ethics。
缺证据时应返回 `score: null`；veto 和审稿人分歧不能被一个均值抹掉。真实 Viewer
请求可通过 `POST /api/campaigns/{campaign_id}/review-panel` 一次冻结同一份证据快照，
再排队 2–5 个不同 `reviewer_kind`/rubric 的独立进程；单项复核仍可使用
`POST /api/campaigns/{campaign_id}/review`。Flywheel UI 的 Campaign 主操作默认请求五项
panel，保留每位 Reviewer 的结论而不生成虚假的录用概率。

## 5. 候选、标量标注与 pairwise preference

Argus 或人工必须先产出 `CANDIDATES.json`。每个候选包含固定字段：

```text
candidate_key, title, problem_gap, core_hypothesis, mechanism,
closest_work, differentiation_claim, public_or_authorized_data, method,
strongest_baselines, decisive_experiments, falsifier, estimated_resources,
elapsed_time_plan, venue_fit, risks, ethics_and_license,
expected_information_gain, terminal_recommendation, team_specific_advantage,
condition_fit_counterfactual, novelty_collision_test
```

`novelty_collision_test` 至少包含 dated `search_cutoff`、一手来源
`closest_source_ids` 和可反驳 differentiation 的 `falsifier`。导入必须同时提供
`CANDIDATES.json` 与 `flywheel.ideation-candidates/1` manifest，后者精确绑定
condition/objective/candidates SHA-256 与候选数。公开的
`POST /api/ideation/runs/{run_id}/candidates` 只接受 `human_entered`；只有 coordinator
经过 Argus allowlist、索引大小/SHA、下载 receipt、原始 candidate bytes 与 manifest
一致性验证的内部路径才可写入 `argus_artifact` provenance，浏览器不能自行声明。
候选一旦导入即不可覆盖，新的候选版本必须创建新的 ideation run。

选中候选后，`POST /api/ideation/candidates/{candidate_id}/campaign` 为该候选编译并冻结
独立 Objective/contract，只返回 `idle` 且 `launch_triggered=false`。不可变 binding receipt
覆盖 condition、父 Objective、候选 artifact/record/input/prompt SHA。后续 Research Episode
必须提供并验证这个真实 candidate Campaign；ideation Campaign 只能作为来源链，不能冒充执行。

标量标注写入 `POST /api/ideation/candidates/{candidate_id}/labels`，必须逐维提供
0–10 或 `null`：

```text
novelty_evidence, falsifiability, resource_fit, venue_fit,
methodological_soundness, integrity_risk, expected_information_gain
```

它还保存 `shortlist|revise|reject|abstain` 决策、匿名 labeler alias 和已脱敏理由。
`POST /api/ideation/runs/{run_id}/pairwise` 保存两候选的 `left|right|tie|abstain`
偏好。标量与 pairwise 同时保留，比把科研判断压成一个“创新分”更适合训练排序器、
偏好模型或校准工具。

## 6. JSONL 导出与 group-safe split

`GET /api/datasets/training-export`（与 `/api/outcomes/training-export` 同一导出）目前可
产生三类 JSONL schema：

- `argus-flywheel/conditioned-idea-label/v2`；
- `argus-flywheel/conditioned-idea-preference/v2`；
- `argus-flywheel/outcome-review/v2`。

候选标注只有在 **run 的 TeamProfile 同意并有 license basis**，该条
label/preference 同时具备显式 consent、非空 license basis 和 redaction confirmation，
并且重新验证 conditioned run、condition/objective 文件、candidate manifest、portfolio
artifact 与 candidate record SHA 后才可导出；因此未执行、被淘汰和负方向仍可成为真实
选择负样本，seed/manual 候选不可以。投稿 outcome 还必须冻结并重新验证真实 candidate
Campaign 的 condition、artifact/record/input/prompt 与 binding receipt，或为合法回根的
rebuttal，并满足 submission consent、review-use rights 与 pseudonymization/redaction。
alias 禁止邮箱、URL 和可直接识别身份的信息；secret、私有路径、未授权全文和真实
reviewer identity 都不得进入导出。

split 由 `group_id` 的 SHA-256 稳定映射为 80% train、10% validation、10% test：

- ideation 数据以整个 `ideation_run_id` 为组；
- outcome 数据以 source `campaign_id` 为组。

同一 run/campaign 的近重复候选、多个 label 或多位 reviewer 因而不会跨 split 泄漏。
响应头 `X-Automatic-Training: false` 是硬边界：导出只生成候选数据文件，不会启动
训练、上传语料或修改任何模型。实际训练前仍需独立的数据治理、许可审计、去重、污染
检查、质量抽检和人工批准。

## 7. 投稿后 outcome 与 rebuttal 闭环

`POST /api/outcomes/submissions` 记录人工提供、已匿名化的 paper version、decision、
reviewer score/confidence/recommendation、feedback 和 questions；它不是投稿接口。
`GET /api/outcomes/submissions` 返回版本、全部 reviewer 意见和训练导出资格原因。

人工可向 `POST /api/outcomes/submissions/{submission_id}/follow-up` 提交 actor 与非空
理由。后端冻结：

```text
runtime/rebuttal-objectives/<objective-sha256>/REBUTTAL_OBJECTIVE.md
```

并创建 `campaign_kind=rebuttal_follow_up` 的 **idle Campaign**。Objective 用
Response Advocate / Response Skeptic / Rebuttal Arbiter 和独立 panel 把每条意见映射到
证据或 human-input request；它不会自动 Start、联系 reviewer 或提交 rebuttal。重复的
同一 objective 是幂等的。

## 8. Viewer 证据边界

独立 Viewer 不信任浏览器提交的本地文件路径。`POST /api/campaigns/{id}/review` 与
`POST /api/campaigns/{id}/review-panel` 都从已连接 Argus project 的 artifact index 中
只选择 allowlisted `text|markdown|json|table`
类型，通过 Argus artifact API 读取 preview，并冻结 canonical：

```text
runtime/viewer/evidence-snapshots/<sha256>/EVIDENCE_SNAPSHOT.json
```

默认最多 24 个 artifact、每个 preview 64 KiB、总计 512 KiB；每段 preview 和整个
snapshot 都带 SHA-256。Viewer worker 在 evaluator 前重新校验内联 snapshot。没有合格
证据时状态为 `empty`、`score=null`，不会用路径猜测内容或制造分数。

## 9. 当前实现边界

- 已实现的是条件冻结、Objective/Campaign 创建、候选导入、人工标注、pairwise、
  outcome/rebuttal 记录和 gated JSONL 导出；这些 API 不等于已经调用真实 Argus。
- Flywheel 不自动抓取并训练模型，不自动提交论文或 rebuttal，不代表会议录用结果。
- seed 目录和静态 290 Prompt Packet 可做冷启动/覆盖回归；生产候选应走 TeamProfile
  条件化路径，并在当前 sources、资源和政策快照下重新生成；seed manifest 明确
  `personalization_state=seed_coverage_baseline`、`launch_ready=false` 和
  `requires_team_condition_snapshot=true`。
- 训练数据的“可导出”只说明产品内门槛齐全，不代替法律、伦理、会议政策或机构审查。
