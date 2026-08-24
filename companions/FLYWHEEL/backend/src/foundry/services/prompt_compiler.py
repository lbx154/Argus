"""Compile a research idea into an auditable Argus objective contract."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping


@dataclass(frozen=True)
class CompiledPrompt:
    phase: str
    prompt: str
    prompt_sha256: str
    generated_at: str
    manifest: Mapping[str, Any]


class PromptCompiler:
    VERSION = 2
    PROTOCOL_VERSION = "argus.flywheel/research-protocol-v2"
    PHASES = {"portfolio", "locked"}
    REQUIRED = {
        "venue": ("name", "edition", "track", "deadline", "scope"),
        "domain": ("name", "evidence_requirements"),
        "idea": ("title", "problem_gap", "mechanism_hypothesis", "kill_criterion"),
        "resources": ("gpu_count", "gpu_model", "gpu_hours", "wall_clock_deadline"),
    }
    LOCKED_REQUIRED = (
        "primary_claim", "primary_metric", "minimum_effect", "data_split",
        "confirmatory_seeds", "strongest_baselines",
    )

    def compile(
        self,
        *,
        venue: Mapping[str, Any],
        domain: Mapping[str, Any],
        idea: Mapping[str, Any],
        resources: Mapping[str, Any],
        phase: str = "portfolio",
        team: Mapping[str, Any] | None = None,
    ) -> CompiledPrompt:
        phase = phase.lower().strip()
        if phase not in self.PHASES:
            raise ValueError("phase must be 'portfolio' or 'locked'")
        for label, value in (("venue", venue), ("domain", domain), ("idea", idea), ("resources", resources)):
            missing = [key for key in self.REQUIRED[label] if value.get(key) in (None, "", [])]
            if missing:
                raise ValueError(f"{label} is missing required fields: {', '.join(missing)}")
        if phase == "locked":
            missing = [key for key in self.LOCKED_REQUIRED if idea.get(key) in (None, "", [])]
            if missing:
                raise ValueError(f"locked idea is missing frozen fields: {', '.join(missing)}")
        resolved_team = team or idea.get("team_conditions") or idea.get("team") or {}
        if not isinstance(resolved_team, Mapping):
            raise ValueError("team must be an object when supplied")
        condition_binding = self._condition_binding(idea.get("condition_binding"))
        completion_target = str(
            idea.get("completion_target")
            or "Produce an evidence-backed, reproducible research result for human review; "
            "NO_WINNER or a well-supported negative result is valid."
        ).strip()
        if not completion_target or len(completion_target) > 4_000:
            raise ValueError("idea.completion_target must contain 1 to 4000 characters")
        candidate_target = idea.get("candidate_target", 10)
        if (
            isinstance(candidate_target, bool)
            or not isinstance(candidate_target, int)
            or not 3 <= candidate_target <= 20
        ):
            raise ValueError("idea.candidate_target must be an integer between 3 and 20")
        finalist_limit = idea.get("finalist_limit", min(5, candidate_target))
        if (
            isinstance(finalist_limit, bool)
            or not isinstance(finalist_limit, int)
            or not 1 <= finalist_limit <= candidate_target
        ):
            raise ValueError("idea.finalist_limit must be between 1 and candidate_target")
        normalized = {
            "compiler_version": self.VERSION,
            "research_protocol_version": self.PROTOCOL_VERSION,
            "phase": phase,
            "team": dict(resolved_team),
            "venue": dict(venue),
            "domain": dict(domain),
            "idea": dict(idea),
            "resources": dict(resources),
            "completion_target": completion_target,
            "candidate_target": candidate_target,
            "finalist_limit": finalist_limit,
            "condition_binding": condition_binding,
        }
        goal_contract = self._compile_goal_contract(
            completion_target=completion_target,
            resources=resources,
            phase=phase,
        )
        normalized["goal_contract"] = goal_contract
        input_sha = hashlib.sha256(
            json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        body = self._render(normalized, input_sha)
        prompt_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
        generated_at = datetime.now(UTC).isoformat()
        return CompiledPrompt(
            phase=phase,
            prompt=body,
            prompt_sha256=prompt_sha,
            generated_at=generated_at,
            manifest={
                "compiler_version": self.VERSION,
                "research_protocol_version": self.PROTOCOL_VERSION,
                "phase": phase,
                "input_sha256": input_sha,
                "prompt_sha256": prompt_sha,
                "generated_at": generated_at,
                "venue": f"{venue['name']} {venue['edition']}",
                "idea_title": idea["title"],
                "condition_snapshot_bound": bool(resolved_team),
                "condition_binding": condition_binding,
                "personalization_state": (
                    "conditioned_ideation_candidate"
                    if resolved_team and condition_binding
                    else "seed_or_unbound_preview"
                ),
                "execution_eligible": bool(resolved_team and condition_binding),
                "execution_blockers": [
                    reason
                    for reason, blocked in (
                        ("team_condition_snapshot_missing", not bool(resolved_team)),
                        ("conditioned_candidate_binding_missing", not bool(condition_binding)),
                    )
                    if blocked
                ],
                "goal_contract": goal_contract,
                "oral_is_aspiration_only": True,
                "positive_result_required": False,
                "automatic_submission_allowed": False,
            },
        )

    def _render(self, data: Mapping[str, Any], input_sha: str) -> str:
        venue = data["venue"]
        domain = data["domain"]
        idea = data["idea"]
        resources = data["resources"]
        team = data["team"]
        condition_binding = data["condition_binding"]
        goal_contract = data["goal_contract"]
        candidate_target = data["candidate_target"]
        finalist_limit = data["finalist_limit"]
        phase = data["phase"]
        oral = bool(idea.get("oral_aspiration", True))
        source_lines = self._bullets(idea.get("source_requirements") or [
            "目标会议往届录用论文与官方 proceedings/OpenReview",
            "arXiv 一手预印本与作者发布版本",
            "作者或组织的官方代码仓库及固定 revision",
        ])
        baseline_lines = self._bullets(idea.get("baseline_candidates") or ["待检索并核验的最强可运行基线"])
        domain_evidence = self._bullets(domain["evidence_requirements"])
        policies = self._bullets(venue.get("policies") or ["匿名、伦理、引用和 artifact 规则必须从官方页面复核"])
        team_snapshot = json.dumps(team, ensure_ascii=False, sort_keys=True, indent=2)
        goal_snapshot = json.dumps(goal_contract, ensure_ascii=False, sort_keys=True, indent=2)
        if phase == "portfolio":
            phase_contract = f"""
## Portfolio 阶段合同

这是候选发现与廉价反证任务，不是假定 idea 正确的论文生产任务。

1. 截至 `{idea.get('search_cutoff', '任务启动日')}` 检索一手来源，按“机制、核心主张、setting、证据”建立 `NEAREST_WORK_MATRIX`，不能只做标题关键词匹配。
2. 以最多 {candidate_target} 条机制上真正不同的候选路线为探索目标；这不是最低配额。换数据集、prompt、backbone 或超参数不算机制创新，证据只支持更少路线时必须少报。
3. 每条路线写出最强反证、最便宜决定性 falsifier、所需真实 baseline、资源估计、伦理/许可证风险。
4. 探索集与确认集隔离。本阶段不得查看或优化最终 confirmatory test。
5. Builder 与 Breaker 的独立输出冻结后才交给 Arbiter；仅当 collision、重要性、可证伪性、headroom、资源和 hostile-review 均通过，才输出 `WINNER_PROPOSED` 与完整 `WINNER_CONTRACT`。
6. Arbiter 最多保留 {finalist_limit} 个 finalist；没有合格路线时以 `NO_WINNER_YET` 正常结束。不得为达到 {candidate_target} 条而填充弱 idea。
"""
        else:
            phase_contract = f"""
## Locked 阶段合同

本阶段不得静默改写已冻结合同：

- 唯一主张：{idea['primary_claim']}
- 主指标：{idea['primary_metric']}
- 最小实际效应：{idea['minimum_effect']}
- 冻结数据划分：{idea['data_split']}
- 确认性 seeds：{self._inline(idea['confirmatory_seeds'])}
- 最强 baseline：{self._inline(idea['strongest_baselines'])}

看过确认性结果后产生的新假设必须标记为 exploratory，并生成新版本，不得回写本合同。若最小效应、真实性或普适性失败，必须执行 kill、缩小 claim，或回到新 Portfolio；不能换指标挽救结论。
"""
        return f"""# ARGUS / FLYWHEEL · RESEARCH PROTOCOL v2 · {phase.upper()}

Protocol `{self.PROTOCOL_VERSION}` · compiler v{self.VERSION} · frozen input sha256 `{input_sha}`

## 身份与目标边界

你是 Argus 研究团队（Manager → Planner → Engineer → independent Reviewer）。目标是对一个重要、机制级、可证伪的研究候选进行诚实而有界的验证，并形成证据充分时才可供人工投稿的研究包。

目标会议：{venue['name']} {venue['edition']} · {venue['track']}
截止时间：{venue['deadline']}
会议范围：{venue['scope']}
领域：{domain['name']}
候选题目：{idea['title']}

`Oral / Best Paper` 是{'高标准愿景' if oral else '未启用的愿景'}，不是完成条件、内部自评结论或录用承诺。不得以获得正向结果为完成条件。

## 冻结条件与来源边界

团队条件、资源、会议、领域、idea、来源要求与自由目标共同构成本项目的冻结输入；运行中不得静默改变。若需要变化，必须生成新的父子版本与新 SHA，并再次经过人工批准。

团队条件快照：

```json
{team_snapshot}
```

{('团队条件已绑定；仍需 HUMAN_GATE_0 对该 input SHA 明确签核。' if team else '团队条件未绑定。本 Prompt 仅可用于冷启动/覆盖浏览；任何付费、GPU、数据或论文执行前必须返回 NEEDS_HUMAN_DECISION 并冻结真实团队条件。')}

{('候选已绑定到条件化 ideation run：' + json.dumps(condition_binding, ensure_ascii=False, sort_keys=True) if condition_binding else '候选没有 condition/objective/artifact 绑定；这是 seed/unbound preview，不具备执行资格，不得创建或启动真实研究 Campaign。')}

来源只允许使用有版本、获取时间、许可/访问状态和 SHA 的官方会议页面、官方 proceedings/OpenReview、一手论文/arXiv、上游 GitHub 与合同明确允许的数据。区分官方事实、预测、定点观察和推断；来源更新必须形成 freshness delta，不覆盖旧快照。

## 自由目标编译结果

操作者原始目标保持原文，不把“突破性”“Oral”等愿景伪装成可保证结果。以下 gates 只能由具备 artifact/SHA 的证据通过；budget 是硬上限；stop criteria 优先于愿景：

```json
{goal_snapshot}
```

## 候选问题

- 问题缺口：{idea['problem_gap']}
- 机制假设：{idea['mechanism_hypothesis']}
- 初始方法种子：{idea.get('method_seed', '由 Portfolio 在约束内探索')}
- 允许的公开数据/任务：{idea.get('public_data_or_tasks', '必须在执行前指定可追溯、许可允许的公开数据或任务')}
- 决定性实验设计：{idea.get('decisive_experiment', '由 Portfolio 提出最便宜、可证伪的实验并先行冻结')}
- 决定性 kill：{idea['kill_criterion']}
- 预期可观察模式：{idea.get('predicted_observation', '待提出；必须明确标记为预测而非结果')}

## 会议政策（启动时再次从官方来源核验）

{policies}

## 本领域的证据最低要求

{domain_evidence}

## 一手来源要求

{source_lines}

## Baseline 候选（全部是“待核验”，不是已运行事实）

{baseline_lines}

论文名 baseline 只有在实际运行官方实现或忠实机制重实现、固定代码 revision、配置与输出后才可使用该名称。代理启发式必须以 proxy 标注，不得冒充论文方法。

## 资源与执行边界

- GPU：{resources['gpu_count']} × {resources['gpu_model']}
- GPU-hour 上限：{resources['gpu_hours']}
- 墙钟截止：{resources['wall_clock_deadline']}
- 最大并发：{resources.get('max_parallel_jobs', 1)}
- API 预算：{resources.get('api_budget', '必须由操作者在启动前填写')}
- 项目只能写入专属 workdir/life root，不得 pull、reset 或复用正在运行的 Argus checkout。
- 超预算、外部联系、真人数据、双重用途、版本迁移和任何投稿动作均需人工批准。
- 若距保守截止日不足 90 天且没有已存在的 winner、真实 baseline 与 pilot 证据，不得从零承诺完整论文；只能做及时性分诊、已有工作的收敛或延期到下一周期。

{phase_contract}

## Builder / Breaker / Arbiter 对冲合同

- `BUILDER_ARGUS` 在不知道 Breaker 结论的上下文中，从冻结条件、来源与团队独特能力出发生成和修复候选，并输出带版本/SHA 的 `BUILDER_OUTPUT`。
- `BREAKER_ARGUS` 先独立重建可行空间，再审查 Builder artifact；重点攻击最近工作碰撞、伪创新、隐藏数据需求、baseline 真实性、统计功效、资源/时间、伦理许可证与 venue fit，并输出 `BREAKER_OUTPUT`。它可以杀死全部候选。
- `ARBITER` 只读取两份已经封存的输出及其来源证据，保留分歧和 veto，不以平均分掩盖阻断问题；输出 `ARBITER_DECISION`、至多 {finalist_limit} 个 finalist，或 `NO_WINNER`。

三者是独立上下文/工件角色。除非运行 telemetry 证明，不能声称它们是独立 OS 进程。任何候选进入实验或全文阶段前必须经过 `HUMAN_GATE_1_SELECTION`。

## 强制研究流水线与人工闸门

0. HUMAN GATE 0：人工签核冻结 input SHA、团队/资源/来源权限、目标解释、预算与启动权。
1. RESEARCH：来源快照、最近工作矩阵、Builder/Breaker/Arbiter、collision 与 venue fit。
2. HUMAN GATE 1：人工选择 finalist、要求补证，或接受 `NO_WINNER`/负结果；系统不自动升级为论文任务。
3. EVIDENCE + WRITE：只执行获批且预算内的实验，只写已有证据能够支持的内容。
4. INTEGRITY CHECK 1：从原始 artifact 审查引用、运行存在性、baseline 真实性、数据泄漏、统计、claim scope；失败最多修复 3 次，仍失败则隔离。
5. INDEPENDENT REVIEW 1：启动五位 fresh-context、只读证据的 Reviewer，分别负责：(a) novelty/最近工作碰撞；(b) 方法/统计/可证伪性；(c) 资源/进度可行性；(d) venue fit/当前政策；(e) 诚信/伦理/许可证。缺证据返回 `score:null`，不得猜分。
6. BOUNDED REVISION：逐项关联 reviewer 问题、修改和新证据，最多两轮实质修改；保留每版 diff 和旧 artifact。
7. INDEPENDENT REVIEW 2：重新实例化五位新的 fresh-context Reviewer；不得继承或复制第一轮评分与结论。
8. FINAL INTEGRITY CHECK（INTEGRITY CHECK 2）：从 primary/raw artifacts 自零重做最终诚信检查，必须零未解决阻断问题。
9. HUMAN GATE 2：人工审核主张、署名、AI 披露、venue 合规、最终稿与所有最终 SHA；系统不自动上传或投稿。

## 必须维护的可审计产物

`CONDITION_SNAPSHOT`、`SOURCE_SNAPSHOT`、`NEAREST_WORK_MATRIX`、`BUILDER_OUTPUT`、`BREAKER_OUTPUT`、`ARBITER_DECISION`、`BASELINE_PROVENANCE`、`RUN_LEDGER`、`EXCLUDED_RUNS`、`CLAIM_EVIDENCE_MATRIX`、`STATISTICAL_AUDIT`、`VENUE_COMPLIANCE`、两轮各五份独立 `REVIEW_CERTIFICATE`、revision diffs、两份 `INTEGRITY_REPORT`、`REPRODUCIBILITY_MANIFEST` 和最终 `PROCESS_SUMMARY`。

每项工件必须记录：协议/编译器版本、父版本、input SHA、Prompt SHA、condition/source SHA、Argus SHA、模型/provider、代码/数据版本、seed、配置、命令、环境、成本、时间、actor 与 artifact SHA。raw 证据和失败/负结果追加保存，不覆盖旧版本。只有 exact input/code/environment/command/seed 能重建同一输出 SHA 时，才可声明可复现。

## 诚信硬约束

- 不伪造引用、实验、数字、代码执行、人工评审或统计显著性。
- 不得把创建或发布新数据集作为主要贡献；只能使用合同中允许且许可可追溯的公开数据、公开任务或程序化验证实例。
- 不删除负结果或 excluded runs，不从多个指标、数据集、层、seed 中挑唯一正结果。
- 不把相关性写成因果性，不把进程存活写成研究进展。
- 不把 Reviewer 评分写成会议录用概率。
- 发现直接 novelty collision、无真实 baseline、无有效 headroom、资源不可行、伦理/许可证不满足或 kill criterion 触发时，必须停止或降级主张。

## 合法终态

`WINNER_PROPOSED`、`WINNER_LOCKED`、`NO_WINNER`、`NO_WINNER_YET`、`NEGATIVE_RESULT_RECORDED`、`NOVELTY_COLLISION`、`RESOURCE_INFEASIBLE`、`INSUFFICIENT_EVIDENCE`、`KILLED`、`DEFERRED`、`NEEDS_HUMAN_DECISION`、`BLOCKED`、`QUARANTINED`、`SUBMISSION_READY_FOR_HUMAN_REVIEW`。

只有最后一个状态代表材料可交给人审核；它不代表 Oral、不代表录用，也不授权自动投稿。
"""

    @staticmethod
    def _condition_binding(value: Any) -> dict[str, str]:
        if value in (None, {}):
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("idea.condition_binding must be an object")
        required = (
            "ideation_run_id",
            "candidate_id",
            "condition_sha256",
            "parent_objective_sha256",
            "candidate_artifact_sha256",
        )
        missing = [key for key in required if not str(value.get(key) or "").strip()]
        if missing:
            raise ValueError(
                "idea.condition_binding is missing required fields: " + ", ".join(missing)
            )
        for key in ("condition_sha256", "parent_objective_sha256", "candidate_artifact_sha256"):
            digest = str(value[key]).lower()
            if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
                raise ValueError(f"idea.condition_binding.{key} must be a SHA-256 digest")
        return {key: str(value[key]).strip() for key in required}

    @staticmethod
    def _compile_goal_contract(
        *, completion_target: str, resources: Mapping[str, Any], phase: str
    ) -> dict[str, Any]:
        """Wrap an arbitrary aspiration in objective, auditable boundaries."""

        return {
            "operator_aspiration": completion_target,
            "phase": phase,
            "measurable_gates": [
                {
                    "gate": "conditions_and_authority",
                    "pass_when": (
                        "named human approval references the exact frozen input SHA and "
                        "confirms data/source rights plus launch authority"
                    ),
                },
                {
                    "gate": "scientific_claim",
                    "pass_when": (
                        "each retained claim is falsifiable, scoped, and mapped to primary "
                        "evidence, uncertainty, authentic baselines, and a kill criterion"
                    ),
                },
                {
                    "gate": "reproducibility",
                    "pass_when": (
                        "exact code/data/environment/command/config/seed inputs and hashes "
                        "reconstruct every reported result or explicitly document variance"
                    ),
                },
                {
                    "gate": "independent_review",
                    "pass_when": (
                        "two rounds of five fresh-context reviewers are complete, every "
                        "veto is resolved or accepted as a stop, and revisions are traced"
                    ),
                },
                {
                    "gate": "final_integrity_and_release",
                    "pass_when": (
                        "from-scratch final integrity has zero blockers and a named human "
                        "approves exact final artifact hashes"
                    ),
                },
            ],
            "hard_budget": {
                "gpu_count": resources.get("gpu_count"),
                "gpu_model": resources.get("gpu_model"),
                "gpu_hours": resources.get("gpu_hours"),
                "api_budget": resources.get("api_budget", "UNBOUND"),
                "max_parallel_jobs": resources.get("max_parallel_jobs", 1),
                "wall_clock_deadline": resources.get("wall_clock_deadline"),
                "expansion_requires_new_version_and_human_approval": True,
            },
            "stop_criteria": [
                "novelty collision cannot be repaired without a new claim/version",
                "predeclared kill criterion is met or confirmatory effect is below threshold",
                "no authentic runnable baseline or meaningful headroom",
                "budget/deadline/resource ceiling is reached",
                "data rights, ethics, privacy, safety, or license gate fails",
                "integrity blocker persists after bounded repair",
                "human pauses, rejects, or declines the next gate",
            ],
            "valid_non_positive_outcomes": [
                "NO_WINNER",
                "NEGATIVE_RESULT_RECORDED",
                "NOVELTY_COLLISION",
                "RESOURCE_INFEASIBLE",
                "INSUFFICIENT_EVIDENCE",
                "KILLED",
            ],
            "completion_rule": (
                "all applicable gates pass with immutable evidence, or a valid non-positive "
                "terminal outcome is sealed; aspiration alone never completes the project"
            ),
        }

    @staticmethod
    def _bullets(values: Any) -> str:
        if isinstance(values, str):
            values = [values]
        return "\n".join(f"- {value}" for value in values)

    @staticmethod
    def _inline(values: Any) -> str:
        if isinstance(values, (list, tuple)):
            return ", ".join(str(value) for value in values)
        return str(values)


def compile_prompt(
    idea: Mapping[str, Any],
    venue: Mapping[str, Any],
    resources: Mapping[str, Any],
    phase: str = "portfolio",
    domain: Mapping[str, Any] | None = None,
    team: Mapping[str, Any] | None = None,
) -> CompiledPrompt:
    """Route-friendly facade; ``domain`` may also be embedded in ``idea``."""
    resolved_domain = domain or idea.get("domain")
    if not isinstance(resolved_domain, Mapping):
        raise ValueError("domain mapping is required (argument or idea.domain)")
    return PromptCompiler().compile(
        idea=idea,
        venue=venue,
        resources=resources,
        phase=phase,
        domain=resolved_domain,
        team=team,
    )
