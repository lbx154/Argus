# Argus Skill Agent 修改地图

这份说明给后续接手的 agent 看。目标不是替代代码阅读，而是让你先知道“哪里管哪里”，避免在 EMNLP 论文 pipeline 或 7x24 harness 里乱改错层。

## 一句话架构

`argus-skill` 是一个长期运行的 agent harness：外层 `LifeSupervisor` 管 backlog、预算、daemon、L4 planner（forward scheduling）；内层 `SkillLoop` 管单个任务的 skill 匹配、必要时蒸馏 skill、L1 engineer 执行、L2 reviewer 验收。历史上独立的 L3 critic 逐轮打磨循环已经移除——验收完全交给 L2 reviewer。EMNLP 论文生成 pipeline 是 built-in skill + per-stage reviewer 检查（stage checklists，reviewer 对照 artifact 裁决）+ planner fallback 共同实现的，不是单独一个 `make_paper.py`。`pipeline_contracts.py` 现在只负责 manifest/freshness/validation-priority 这套 artifact 构建-修复工具，不再是质量 gate。

主链路：

```text
argus-skill / python -m argus_skill
  -> argus_skill/apps/cli.py
  -> argus_skill/apps/_life_repl.py 或 argus_skill/daemon/life_worker.py
  -> argus_skill/life/supervisor.py        # backlog / budget / L4 planner
  -> _CodexSkillLoopRunner.execute(...)
  -> argus_skill/loop.py                   # matcher -> distiller -> engineer -> reviewer
  -> argus_skill/engineer/runner.py        # L1 round loop
  -> argus_skill/engineer/reviewer.py      # L2 structured verdict
```

## Agent 层级

| 层 | 角色 | 主要文件 | 改什么时看这里 |
| --- | --- | --- | --- |
| L0 | CLI / daemon / cockpit | `argus_skill/apps/cli.py`, `argus_skill/apps/_life_repl.py`, `argus_skill/daemon/life_worker.py`, `argus_skill/apps/_watch.py` | 命令行参数、REPL、daemon 启停、`--status`、`--follow`、Telegram/事件展示 |
| L1 | Engineer | `argus_skill/loop.py`, `argus_skill/engineer/runner.py` | 单轮执行 prompt、失败重试、session 续接、acceptance check、进度 watchdog |
| L2 | Reviewer | `argus_skill/engineer/reviewer.py`, `argus_skill/engineer/reviewer_schema.json` | done/continue/blocked 判断、reviewer JSON schema、论文任务的 peer-review gate |
| L4 | Planner | `argus_skill/planner/planner.py`, `argus_skill/life/supervisor.py` | continuous mode 自动排新任务、EMNLP final gate 失败后的自动分流。历史的 L3 critic 逐轮打磨层已移除（见 `planner/planner.py` 顶部说明），验收只由 L2 reviewer 负责 |
| Skill | 横向能力复用 | `argus_skill/skills/store.py`, `argus_skill/scientist/distiller.py`, `argus_skill/builtin_skills/` | skill 匹配、miss 后蒸馏（distiller 复用 engineer backend，不是独立 agent）、writeback、内置论文/research playbook |
| Contracts | 论文 artifact 工具 + 状态机 | `argus_skill/skills/pipeline_contracts.py`, `argus_skill/skills/pipeline_policy.py`, `argus_skill/skills/stage_checklists.py` | manifest/freshness/validation-priority 构建-修复（pipeline_contracts）；质量 gate 走 stage checklist（stage_checklists） |

## 入口和运行面

- `pyproject.toml`: console script 是 `argus-skill = "argus_skill.__main__:main"`。
- `argus_skill/__main__.py`: 只 re-export `apps.cli.main`。
- `argus_skill/apps/cli.py`: 所有顶层 CLI flag 都在这里注册。这里没有 subcommand 模型，`--daemon`、`--status`、`--watch`、`--follow`、`--continuous`、`--objective`、`--bounded`、skill admin 都是 top-level flag。
  - **入口硬门禁（`_lifetime_entry_error`）**：默认进 cockpit / 启动 daemon 时，必须同时满足 (1) 配了 mission objective（`--objective`，或当前 project 已持久化的 `continuous.json`），(2) 至少有一个受信任的 special prompt（`life/special_prompts.py`）。任一缺失就 `exit 2` 并打印可操作的指引——不再让 agent 在没有目标 / 没有机器规则的情况下空跑。只读 / admin flag（`--status`、`--watch`、`--skill-stats`…）不受门禁限制。
  - **`--bounded`**：把 mission 当成有界一次性目标，planner 认证 `project_done` 后硬停。默认（不加）是 open-ended——`project_done` 后继续生成新工作（7×24 永续）。这取代了过去从 objective 文本里猜 `7×24`/`ongoing`/`perpetual` 等关键词的做法（见 `LifeSupervisorConfig.open_ended`）。
- `argus_skill/apps/_life_repl.py`: 交互 cockpit，也包含真实 mission runner `_CodexSkillLoopRunner` 和 memory backend runner。单个 backlog item 最终就是从这里进 `SkillLoop`。
- `argus_skill/daemon/life_worker.py`: detached daemon 版本的同一套逻辑。这里管 `continuous.json` 热加载、pid lock、blue/green handoff、daemon status、预算环境变量。
- `argus_skill/life/memory.py`: 磁盘状态。global root 默认 `~/.argus-skill/`，project state 默认 `~/.argus-skill/projects/<fingerprint>/`。

常见状态文件：

```text
~/.argus-skill/identity.md
~/.argus-skill/skills/
~/.argus-skill/projects/<fingerprint>/project.md
~/.argus-skill/projects/<fingerprint>/backlog.jsonl
~/.argus-skill/projects/<fingerprint>/memory.jsonl   # 本项目日志（每个项目独立，无全局 journal）
~/.argus-skill/projects/<fingerprint>/events.jsonl
~/.argus-skill/projects/<fingerprint>/continuous.json
```

## 单任务 SkillLoop

`argus_skill/loop.py` 是单个 mission 的核心胶水。

关键对象：

- `SkillLoopConfig`: scientist/engineer/reviewer/matcher model、max rounds、check commands、writeback、distill-on-miss、runner flags、`paper_mission`。
- `SkillLoop.run(...)`: 主流程。
- `_build_engineer_prompt(..., paper_mission)`: 拼 L1 engineer prompt。长 horizon 论文 contract 仅在 `paper_mission=True` 时注入。
- 论文任务的识别**不再用关键词猜 objective 文本**，改由显式信号决定：`SkillLoopConfig.paper_mission`（默认 False；life 执行路径 `_CodexSkillLoopRunner` 显式置 True）。已删除旧的 `argus_skill/core/paper_objective.py` 与 `_looks_like_paper_objective`。

主流程：

```text
objective_for_skill -> SkillStore.find_relevant(...)
  miss -> Distiller.distill(...) -> SkillStore.save_distilled(...)
skill_text + task -> SupervisedEngineer.run(...)
  round k: engineer -> checks -> Reviewer.evaluate(...)
  done -> SkillStore.writeback_from_trajectory(...)
  continue -> next_action 注入下一轮
  blocked/max_rounds -> 返回 outcome
```

改 prompt 时注意：

- 普通任务的 L1 prompt 在 `SkillLoop._build_engineer_prompt`。
- 论文任务额外 contract 也在这个函数里，包含整链 EMNLP gate（reviewer 的 full-pipeline checklist）、不要把 daemon/route/cache/path 写进论文 prose、正文页数等约束。
- `objective_for_skill` 是干净用户目标；不要把 memory prelude 写进 skill history。`SkillStore.append_task_history` 已经在防这个坑。

## Engineer / Reviewer

L1 engineer round loop 在 `argus_skill/engineer/runner.py`。

这里管：

- 每轮调用 backend runner。
- 运行 acceptance checks：`argus_skill/engineer/checks.py`。
- backend failure / auth failure / context poisoned / effective progress timeout。
- 是否清掉 carried Codex thread id。
- **Curated-memory checkpoint + 结构化 session roll**（见下）。
- `round.main.completed`、`round.review.completed`、`session.roll` 等事件。

### Curated working-memory checkpoint（上下文管理 / 反 amnesia loop）

背景：一个 mission 的 Codex session 会被逐轮 `resume`，长 horizon 任务里它会
涨到几亿 token、被 codex 自动有损压缩上百次，每次压缩丢失工作记忆 → 模型反复
重读同一批 skill 文档空转（amnesia loop）。修复哲学：**不靠看门狗**，而是让
session 结构上短命 + 跨 session 边界只交接「经过筛选的有价值记忆」。

实现（`argus_skill/engineer/checkpoint.py` + `runner.py` + `reviewer.py`）：

- `CheckpointState`：小而**硬上限**的工作记忆（goal / done[] / tried_and_failed[]
  / open_blocker / next_step）。上限在 Python 里强制（不只在 prompt/schema），
  上限本身就是强制「遗忘/筛选」的机制——删除是解毒，不是丢失（地面真相在磁盘
  artifact 里，可重新召回）。
- **作者 = reviewer（记忆审计员）**：reviewer schema 增加 `checkpoint` 对象。
  engineer 在 turn 末尾按 prompt 输出一段 `HANDOFF:` 提案；reviewer 校验它
  （对照 checks/artifacts）并 CRUD 出下一份 canonical checkpoint。engineer 提议、
  reviewer 验证落定。
- **消费**：runner 每轮把 checkpoint 渲染成「Curated working memory」块 prepend 到
  engineer prompt（同 failed-tool advisory 的拼接方式，`loop.py` 不动）。
- **Session roll**：`SupervisedConfig.shift_round_limit`（env
  `ARGUS_SKILL_SHIFT_ROUND_LIMIT`，默认 8，0=禁用）。一个 thread 活满 N 轮就主动
  drop，下一轮从 checkpoint 重新播种一个**全新 session**，per-session 上下文有界 →
  上百次压缩的 runaway 不可能发生。已有的 context-pressure / poisoned-session 清
  thread 路径现在也带着 checkpoint = 重生而非失忆。
- **无进展**复用已有的 `planner_report.forward_progress`（reviewer 对照前后
  checkpoint 整体判断），不新增看门狗。
- fail-soft：reviewer 漏写/写坏 checkpoint → runner 保留上一份，绝不清空记忆。
- 持久化：`SupervisedConfig.checkpoint_path`（None=mission 内内存）。当前 `loop.py`
  未传 path，所以是 mission 内内存级（已足够修复单 mission 内的 amnesia loop）；
  要跨 mission 续接，给它传一个 project-state 路径即可。
- 测试：`tests/test_checkpoint.py`、`tests/test_checkpoint_loop.py`。

L2 reviewer 在 `argus_skill/engineer/reviewer.py`。

这里管：

- reviewer prompt。
- `reviewer_schema.json` 结构化输出。
- `parse_decision_text` / JSON verdict。
- 对近完成论文任务自动注入 `academic-paper-peer-review-benchmark.md`。
- reviewer-to-engineer handoff skill：`reviewer-engineer-handoff.md`。

如果 reviewer 老是误判：

- 先看 `Reviewer._build_prompt` 和 fallback role skill。
- 再看 `argus_skill/builtin_skills/argus-reviewer-role.md`、`academic-paper-peer-review-benchmark.md`。
- 最后才改 schema；schema 改动会影响 tests 和所有 verdict parser。

## 外层 LifeSupervisor

`argus_skill/life/supervisor.py` 是长期 harness 的大脑。它不是单任务 runner，而是“一个任务接一个任务”的调度器。

它负责：

- 从 `backlog.jsonl` claim `pending -> running`。
- 注入 memory prelude，但保持原始 objective 不被污染。
- 调用 runner 的 `execute(...)`。
- 成本统计和 budget gate。
- 任务完成后写 journal。
- backlog 空时，L4 planner 自动生成下一批任务（历史的 L3 critic 逐轮打磨层已移除）。
- 论文类 continuous objective 在 planner 误报 `project_done` 时，强制改派一个 `final_submission` 认证任务，由 L2 reviewer 出整链裁决，而不是相信 planner 的早停。

重点函数：

- `LifeSupervisor.run()`: 主循环。
- `LifeSupervisor.tick()`: 处理一个 backlog item。
- `_plan_next_work(...)`: L4 planner，continuous mode 下 backlog 空了就调用；论文 objective 的 `final_submission` 改派也在这里（约 `supervisor.py:1776` 一带）。
- `LifeSupervisorConfig.paper_mission` / `full_emnlp_gate` / `open_ended`: 显式信号（前两个默认 True，因为 life supervisor 就是 EMNLP 自动研究驱动）。`paper_mission` 决定 planner 给 bounded item 的论文/通用指导语；`full_emnlp_gate` 决定 `project_done` 前是否必须拿到一次 reviewer 认证的 full-pipeline 通过；`open_ended` 决定 planner 认证 `project_done` 后是“硬停”还是“继续生成新工作”。**已删除**旧的关键词判断 `_objective_requires_full_emnlp_gate` / `_objective_is_paper_long_horizon` / `_objective_is_open_ended`，全部改用 config flag。`open_ended` dataclass 默认 False，但 daemon/REPL 入口路径默认置 True（除非 `--bounded`），并随 `LifeWorkerConfig.continuous_open_ended` 一起做 blue/green handoff 序列化（`_config_payload` / `_config_from_payload`）。
- `_is_emnlp_finalization_objective(...)`: 识别一个任务是否就是项目级 `final_submission` 认证任务（看 `scope: final_submission` 标记，不再看 retired 的 `validate-*` 命令）。
- `_journal_has_full_emnlp_gate_success(...)`: 从 journal 里查是否已有一次被 reviewer 认证（`final_submission_certified=True`）的 full-pipeline 通过记录——这才是项目完成的唯一判据。
- 标量：`_PLANNER_SCOPE_FINAL_SUBMISSION = "final_submission"`，`_FULL_EMNLP_GATE_DESCRIPTION` 现在就是“L2 reviewer 的 full pipeline checklist（research → submission）”这句话，不是任何 shell 命令。

> 注意：历史上的 `_EMNLP_*_CODES` issue-code 分组、`_select_emnlp_finalization_repair_task`、
> `_automatic_emnlp_finalization_task_for_current_gate`、`_build_emnlp_finalization_objective`、
> `_planner_tasks_need_emnlp_finalization_override` 这套“按 issue code 选 repair lane”的确定性
> 分流**已经移除**。现在 supervisor 不读 `pipeline_contracts` 的 validator 结果来决定派活，
> 完成与否只由 L2 reviewer 对 full-pipeline checklist 的 `final_submission` 认证决定。改完成判定
> 逻辑看上面这几个函数，不要再去找 issue-code 分组。

## Skill 系统

skill 是 markdown 文件，带 YAML-like frontmatter。

关键文件：

- `argus_skill/skills/store.py`: markdown skill store、frontmatter parse、matcher、save/writeback。
- `argus_skill/scientist/distiller.py`: miss 后让 scientist 生成新 skill。
- `argus_skill/scientist/prompts.py`: matcher/distill prompt。
- `argus_skill/skills/quality.py`: distilled skill 质量门。
- `argus_skill/skills/lifecycle.py`: reinforce/distill/revise/retire 决策。
- `argus_skill/skills/builtins.py`: packaged built-in skill seed/export。
- `argus_skill/builtin_skills/*.md`: 内置 skill 源文件。
- `argus_skill/builtin_skills/domains/**`: domain skill 包。

初始化时：

- `GlobalMemory.init()` 会把 `argus_skill/builtin_skills` seed 到 `~/.argus-skill/skills/`。
- 默认不覆盖用户已经编辑过的 skill。
- `argus-skill --export-builtin-skills [DIR]` 可以复制内置 skill 到项目目录，默认 `./argus_builtin_skills`。

改内置 skill 时：

- 修改 `argus_skill/builtin_skills/*.md` 是源码。
- 用户本地 `~/.argus-skill/skills/*.md` 不会自动覆盖，除非显式 export/overwrite。
- 论文任务常引用 `argus_builtin_skills/<name>.md`，这是导出后的项目内副本路径；源码仍在 package 内。

## EMNLP 论文 pipeline 总览

这个 pipeline 是一组状态契约，不是单个脚本。目标项目里一般会逐步产生：

```text
research/
  PIPELINE_STATE.json
  LITERATURE_GROUNDING.json
  IDEA_PROVENANCE.json
  CODE_REUSE_PLAN.json
  NARRATIVE_REPORT.md
experiments/
  BENCHMARK_PROVENANCE.json
  BENCHMARK_PROVENANCE.md
  **/manifest.json
  **/status.json
  **/progress.jsonl
  **/raw scored rows / logs / verifier outputs
paper/
  main.tex
  main.pdf
  main.log
  PAGE_BUDGET.md
  PAPER_DRAFT_REPORT.json
  RESULTS_REPORT.md
  CLAIM_GRAPH.json
  EVIDENCE_GAPS.json
  ARTIFACT_MANIFEST.json
  ARTIFACT_FRESHNESS.json
  VALIDATION_PRIORITY_POLICY.json
  FIGURE_TABLE_STYLE_GUIDE.json
  SUBMISSION_ASSURANCE.json
  ACADEMIC_LANGUAGE_REVIEW.json
  PAPER_INFRASTRUCTURE_REVIEW.json
  LAYOUT_REVIEW.json
  figures/IMAGE2_FIGURES.json
  style_ref/
```

主要 stage 和 ownership（下表第三列的 `validate-*` 是历史 CLI 名，现已是 `stage_checklists.py` 里的 stage checklist 检查项，由 L2 reviewer 对照 artifact 裁决，不再是可调用的 CLI 子命令或 `pipeline_contracts` 函数）：

| Stage | 主要 skill | 主要 artifact / 检查项 |
| --- | --- | --- |
| 选题/grounding | `research-brief-to-experiment-plan.md`, `auto-research-pipeline.md` | `validate-grounding`, `validate-idea-provenance`, `validate-code-reuse` |
| 实验/benchmark | `agent-research-benchmark-runner.md` | `validate-full-scale-evidence`, `experiments/**` |
| 结果到 claim | `research-results-analysis-and-figures.md`, `claims-evidence-audit.md`, `result-to-claim.md` | `validate-claim-graph`, `RESULTS_REPORT.md`, `result_to_claim.tsv` |
| 初稿/LaTeX | `emnlp-paper-drafting.md` | `validate-paper-contract`, `validate-paper-format`, `main.tex`, `main.pdf` |
| 格式预检 | `emnlp-format-preflight.md` | `validate-research-md-format`, `FORMAT_PREFLIGHT.md` |
| 图表/IMAGE2 | `research-results-analysis-and-figures.md`, `paper-illustration-image2.md`, `paper-framework-figure-studio-pro.md` | `validate-image2-figures`, `validate-figure-table-style` |
| 学术语言 | `emnlp-academic-language-review.md` | `academic_language_review --write`, `validate-academic-language-review` |
| 基建泄漏 | `emnlp-paper-infrastructure-review.md` | `paper_infrastructure_review --write`, `validate-paper-infrastructure-review` |
| 视觉布局 | `paper-review-revision-loop.md`, `emnlp-format-preflight.md` | `paper_layout_review --write`, `validate-layout-review` |
| 最终提交 | `research-submission-assurance-gate.md` | `submission` stage checklist + full-pipeline `final_submission` 认证（`SUBMISSION_ASSURANCE.json`、`validate_submission_readiness`） |

## EMNLP 论文检查（stage checklists + validator 函数）

质量校验由 L2 reviewer 读 stage checklist 完成（`argus_skill/skills/stage_checklists.py` 的 `format_stage_checklist` / `format_full_pipeline_checklist`），对照 artifact 做裁决。`pipeline_contracts.py` 现在只剩 **manifest / freshness / validation-priority** 这套 artifact 构建-修复子系统，不再托管成套的论文质量 `validate-*` gate。

```bash
# 完成判定由 reviewer 走 stage checklist：
python -c "from argus_skill.skills.stage_checklists import format_full_pipeline_checklist; print(format_full_pipeline_checklist(role='reviewer'))"
```

`pipeline_contracts.py` 现存的 CLI / 可 import 表面（全部是 artifact 构建-修复工具，**不是**质量 gate）：

```text
# CLI（skill 文案会让 agent 直接跑这几个）
python -m argus_skill.skills.pipeline_contracts refresh-manifest --project-root .
python -m argus_skill.skills.pipeline_contracts refresh-artifact-freshness --project-root .
python -m argus_skill.skills.pipeline_contracts write-validation-priority-policy --project-root .
python -m argus_skill.skills.pipeline_contracts repair-emnlp-contract-artifacts --project-root .

# 对应可 import 的函数
refresh_artifact_manifest      / validate_artifact_manifest
refresh_artifact_freshness     / validate_artifact_freshness
write_validation_priority_policy / validate_validation_priority_policy
repair_emnlp_contract_artifacts
```

> 历史上 `pipeline_contracts.py` 还有约 20 个成套的论文质量 `validate-*` 函数
> （`validate_emnlp_paper_contract` / `validate_claim_graph` / `validate_full_scale_experiment_evidence`
> / `validate_image2_figures` / `validate_layout_review` …）和一个把它们串起来的
> `validate_full_emnlp_readiness` 总 gate。完成判定改成 L2 reviewer 的 full-pipeline checklist
> 认证后，这些函数**没有任何运行时 / 测试 / skill 调用方**，已整体**删除**（文件从 ~11.5k 行降到 ~2.5k 行）。
> 要重新引入某项机器校验，优先在 `stage_checklists.py` 的 checklist 里加一条，由 reviewer 验证。

项目是否“EMNLP ready”由 L2 reviewer 的 `format_full_pipeline_checklist` 整链裁决决定
（evidence、claim graph、paper contract、format、image-2、review、manifest、freshness、
submission assurance 都在 checklist 里），不是看某个 validator 的返回值，也不只是看 PDF
存不存在。

改 validator 时注意：

- `ContractIssue(code, path, message)` 的 `code` 仍是有用的诊断标识，重命名前先 grep 引用（tests、reviewer 文案）。
- full-pipeline 完成判定现在走 L2 reviewer 的 `stage_checklists` 整链裁决，不再有 `supervisor.py` 里的 EMNLP issue-code 分组；新增检查项时改 `stage_checklists.py` 的 checklist，而不是去找已删除的 issue-code lane。
- 新增 artifact 后，通常还要更新 manifest/freshness/validation priority 相关逻辑。
- 先改 narrow validator，再考虑是否需要在 checklist 里新增一项。

## Review 工具

这些是 paper pipeline 的模型/视觉 review 工具：

- `argus_skill/skills/academic_language_review.py`
  - CLI: `python -m argus_skill.skills.academic_language_review --project-root . --review-mode model --write`
  - 输出：`paper/ACADEMIC_LANGUAGE_REVIEW.json` 和 `.md`
  - 校验：`validate-academic-language-review`

- `argus_skill/skills/paper_infrastructure_review.py`
  - CLI: `python -m argus_skill.skills.paper_infrastructure_review --project-root . --review-mode model --write`
  - 输出：`paper/PAPER_INFRASTRUCTURE_REVIEW.json` 和 `.md`
  - 用来抓 manuscript prose 里的本地路径、device/cache、Argus/Codex route/config 泄漏。

- `argus_skill/skills/paper_layout_review.py`
  - CLI: `python -m argus_skill.skills.paper_layout_review --project-root . --review-mode vision --write`
  - 输出：`paper/LAYOUT_REVIEW.json`、`.md`、`paper/layout_review/pages/`
  - 用 PDF page snapshots 做视觉布局审核。

这些 review JSON 是生成证据。不要为了让 gate 绿而手改成 PASS；应该改 `main.tex` / PDF / evidence 后重跑工具。

## IMAGE2 / 论文图

图像工具在 `argus_skill/tools/image_tool.py`。

常用命令：

```bash
python -m argus_skill.tools.image_tool paper-prompt --out paper/figures/overview.prompt.txt --force
python -m argus_skill.tools.image_tool generate --prompt-file paper/figures/overview.prompt.txt --out paper/figures/overview.png
python -m argus_skill.tools.image_tool inspect --image paper/figures/overview.png
python -m argus_skill.tools.image_tool review --image paper/figures/overview.png --prompt-file paper/figures/overview.prompt.txt
python -m argus_skill.tools.image_tool sync-paper-metadata --project-root . --image paper/figures/overview.png --figure-id overview
```

contract 要求非数据类 paper-facing figure 通过 image-2/codex-image2 路线产生，并保留 prompt、sidecar、inspect、review、provenance、manifest hash。不要用本地 matplotlib/TikZ/SVG 画一个概念图再伪装成 image-2。

## Planner 的 EMNLP 完成判定

当 `LifeSupervisorConfig.full_emnlp_gate=True`（默认）时，L4 planner 有额外保护：

- `full_emnlp_gate` 这个**显式 config flag**（不再从 objective 文本猜）决定：`project_done` 前必须有一次被 reviewer 认证的 full-pipeline gate 通过记录。非论文的 continuous mission 可把它置 False。
- `_journal_has_full_emnlp_gate_success(...)`: 在 journal 里查这条认证记录（`final_submission_certified=True`）。
- `_plan_next_work(...)`: 若 planner 在尚未认证时就报 `project_done`，这里把它的裁决**替换**成一个 `scope=final_submission` 的 “Prove final submission readiness” 任务，交回 L2 reviewer 做整链认证。

所以：

- 改“项目什么时候算完成 / 还差认证时下一步派什么”，改 `supervisor.py` 的 `_plan_next_work` 改派分支和 `_journal_has_full_emnlp_gate_success`。
- 改“full-pipeline checklist 里某一项该不该判过”，改 `skills/stage_checklists.py`（reviewer 实际读的 checklist）；manifest/freshness/policy 这类 artifact 的构建-修复仍在 `pipeline_contracts.py`。
- 改“agent 读到任务后应该怎么做”，改 `builtin_skills/*.md` 或 `SkillLoop._build_engineer_prompt`。

## Backend / runner

Backend 协议在 `argus_skill/core/ports.py`：

```text
RunnerBackend.run_exec(prompt, options, run_label, resume_thread_id=None) -> RunnerResult
```

实现：

- `argus_skill/adapters/codex_backend.py`: 包 vendored `codex_autoloop.codex_runner.CodexRunner`，真实 codex/claude/copilot CLI 都从这里走。
- `argus_skill/codex_autoloop/`: 旧 ArgusBot autoloop 整套已删除，只保留三个底层 CLI driver 模块 `codex_runner` / `runner_backend` / `models`（+ 薄 `__init__`、`LICENSE`、`_VENDORED.md`）。`__init__` 不再 eager import orchestrator/core，所以 `import argus_skill.codex_autoloop.codex_runner` 没有遗留副作用。历史的 orchestrator / telegram_daemon / feishu_adapter / 第二份 reviewer·planner·checks / dashboard 等 ~33 个模块（~14.9k 行）都已移除——它们早被 `argus_skill.life` / `engineer` / `planner` 取代（Telegram 远控走的是新的 `life/telegram_bot.py`）。
- `argus_skill/adapters/memory_backend.py`: deterministic 测试/smoke。
- `_CodexSkillLoopRunner` 在 `_life_repl.py` 里组装真实 backend，并把同一个 backend 传给 distiller(scientist)、engineer、reviewer、planner。

常见 env：

```text
ARGUS_SKILL_LIFE_BACKEND=codex|memory
ARGUS_SKILL_RUNNER_BACKEND=codex|claude|copilot
ARGUS_SKILL_RUNNER_BIN=/path/to/codex
ARGUS_SKILL_RUNNER_EXTRA_ARGS="..."
ARGUS_SKILL_SAFE_MODE=1
ARGUS_SKILL_DISTILL_ON_MISS=0|1
ARGUS_SKILL_SKILL_WRITEBACK=0|1
ARGUS_SKILL_PER_MISSION_CAP_USD=30
ARGUS_SKILL_DAILY_CAP_USD=180
```

## 事件和观测

事件 sink 协议在 `argus_skill/core/ports.py`。

常见事件：

- `life.mission.started`
- `loop.start`
- `skill.match.*`
- `scientist.start`
- `round.start`
- `round.main.completed`
- `round.review.started`
- `round.review.completed`
- `skill.outcome`
- `life.iteration.continued`
- `life.planner.start`
- `life.planner.verdict`
- `loop.done`

展示相关文件：

- `argus_skill/cli/event_format.py`
- `argus_skill/cli/render.py`
- `argus_skill/apps/_watch.py`
- `argus_skill/apps/cli.py` 的 `--follow` helpers
- `argus_skill/life/telegram_bot.py`
- `argus_skill/life/notify.py`

## Benchmarks 和论文证据

仓库自带一些 benchmark/evidence 目录，用于本项目论文或 regression：

- `benchmarks/`: TB2/SWEBenchPro/prompt-only runners、report、archive helpers。
- `benchmarks/evidence/`: 归档实验证据。
- `experiments/`: 本地实验输出。
- `paper/`: 这个仓库自己的 claim-to-evidence paper workspace，不等同于每个外部 research project 的 `paper/main.tex` pipeline，但命名会重叠。
- `paper/build_*_artifacts.py`: 从 repo-local evidence 生成 checked-in `paper/artifacts/*`。

改 benchmark runner 看 `benchmarks/*.py` 和 `tests/test_*benchmark*`。

## 测试入口

常用快速测试：

```bash
pytest tests/test_loop_smoke.py
pytest tests/test_architecture_docs_contract.py
pytest tests/skills/test_pipeline_contracts_cli.py
pytest tests/skills/test_paper_layout_review_prompt.py
pytest tests/skills/test_academic_language_review.py
pytest tests/skills/test_paper_infrastructure_review.py
pytest tests/tools/test_image_tool.py
```

全量：

```bash
pytest
```

只改文档通常不用全跑。改 `pipeline_contracts.py` 至少跑 pipeline/review/image 相关 tests。改 `supervisor.py` 至少跑 life/daemon/planner 相关 tests。

## 修改时的层级规则

1. CLI 行为改 `apps/cli.py` / `_life_repl.py` / `daemon/life_worker.py`。
2. 单任务 agent prompt 改 `loop.py`。
3. L1 执行可靠性改 `engineer/runner.py`。
4. L2 验收标准改 `engineer/reviewer.py` 和相关 role skill。
5. L4 调度策略改 `life/supervisor.py` / `planner/planner.py`。
6. Skill 匹配、蒸馏、writeback 改 `skills/store.py` / `scientist/*`。
7. EMNLP 质量是否合格改 `skills/stage_checklists.py` 的 checklist；manifest/freshness/policy artifact 构建-修复改 `skills/pipeline_contracts.py`。
8. EMNLP 项目何时算完成 / 还差认证时改派什么，改 `life/supervisor.py` 的 `_plan_next_work` 与 `_journal_has_full_emnlp_gate_success`。
9. Agent 读到 paper 任务后的操作手册改 `argus_skill/builtin_skills/*.md`。
10. 生成 evidence/review JSON 的工具改 `skills/*_review.py` 或 `tools/image_tool.py`，不要只改 validator 放宽。

## 常见坑

- 不要把 runtime prelude、daemon 路径、Codex route、capability vault、local cache/device 写进论文正文。
- 不要手改 review JSON、manifest、freshness、submission assurance 来制造 PASS。优先修源 artifact 后重跑生成器。
- 不要假设还存在 `supervisor.py` 里的 EMNLP issue-code 分组 / `_select_emnlp_finalization_repair_task`：它们已删除，完成判定走 L2 reviewer 的 full-pipeline checklist 认证。
- 不要只在 built-in skill 文案里改规则，却忘了 reviewer 的 stage checklist（底层 `validate_*` 函数）仍然会判红。
- 不要只让单个 stage 的 paper-contract 检查过就说 EMNLP ready；最终看 `format_full_pipeline_checklist` 的整链裁决。
- 不要在 full-scale evidence gate 红的时候继续 polish `paper/main.tex`，先补实验/benchmark/source matrix。
- 不要把 pilot、synthetic、same-family-only evidence 写成 full EMNLP-ready result。
- 不要在 user 的 `~/.argus-skill/skills` 里直接覆盖本地编辑，源码改 `argus_skill/builtin_skills`，需要时再 export。

