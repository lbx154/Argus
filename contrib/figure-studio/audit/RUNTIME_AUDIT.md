# Research vertical 论文生产代码审计

审计对象：`/data/v-boxiuli/argus-runtime-latest`，HEAD `443876007943f4979f9537c95a0e0456a5941033`。  
方法：静态代码/提交审计与离线测试；未启动 daemon、未联网、未修改仓库。以下均为 HEAD 行号。

## 结论摘要

这还不是 production-grade 的论文生产流水线。它已有原子状态写入、机械引文/结构门槛、稿件快照和并行 Reviewer 调度，但 Paper 的“生产”主要靠通用 Engineer 阅读 Markdown 后自行操作；Build/Experiment 的硬门只验证 `HANDOFF.md` 标题，Review 也不能证明 Reviewer 真看过 PDF 像素。多个旧工具仍存在，却已被新 playbook 绕开，形成两套相互不完全一致的契约。

## 1. 五阶段状态机与推进条件

- 正式顺序为 `idea -> build -> experiment -> paper -> review`（`argus_skill/verticals/research/stages.py:23-29`）；别名只在解析/迁移边界使用（同文件 `31-41`）。Review 是终态，不存在单独的 `done` stage；done 是把 `review.status` 标为 done、`current_stage` 仍为 review（`argus_skill/skills/stage_machine.py:613-626,703-713`）。
- 普通 mission 先经 Reviewer，再由 runtime 把最终 verdict 交给 Manager（`argus_skill/apps/_runtime_stage_transition.py:35-56`）。预规划的 bounded node 只有 `stage_closing=true` 才可触发阶段写入（`argus_skill/apps/_runtime_helpers.py:169-228`）。干净、无冲突的 Reviewer `done` 可跳过第二次 Manager LLM 投票，但仍跑确定性 validator（`argus_skill/manager/_stage_ops.py:928-988`）；其他情形由 Manager LLM 在 ADVANCE/HOLD/COMPLETE 间判断（同文件 `995-1076`）。
- Manager 解析器允许 ADVANCE 到任意更晚阶段，而非只准相邻阶段（`argus_skill/manager/stage_decider.py:270-295`）；`_set_stage` 会把中间阶段记为 skipped（`argus_skill/skills/stage_machine.py:371-389`）。干净 Reviewer 的确定性路径则只走下一阶段（`argus_skill/manager/_stage_ops.py:915-980`）。
- Idea→Build：若 staged + publishable/doctoral + 非 locked，必须有 12 条 route、12 个独立 review、一次 selector 和有效 `selected_idea`；具体选择门见 `argus_skill/verticals/research/idea_portfolio.py:45-54,473-506,1223-1274`。随后只再检查 `HANDOFF.md` 首个非空行严格等于 `# HANDOFF — IDEA`（`stages.py:293-311,425-444`）。locked、direct 或 exploratory 路径不要求 portfolio；这时硬门甚至不要求 state 中存在 `selected_idea`。
- Build→Experiment：语义 checklist 要求真实方法/强基线/配置/正控/调用链一致（`stages.py:75-119`），但确定性门实际只有 `# HANDOFF — BUILD`（`stages.py:445-446`）。Reviewer/Manager 的 LLM 判断是唯一语义门。
- Experiment→Paper：文字要求 wins 明显多于 losses、headline/primary comparison 获胜、击败最强同信息基线且正控通过（`stages.py:120-161`），但硬门仍只有 `# HANDOFF — EXPERIMENT`（`stages.py:445-446`）；`experiment_audit_gate.py` 没有 runtime call site，且只被自身 CLI 调用（`argus_skill/verticals/research/experiment_audit_gate.py:85-194,197-218`）。
- Paper→Review：必须有 `paper/main.tex`；有 `main.pdf` 或 `main.html`，PDF 仅须 pypdf 可读且至少一页，HTML 仅须长度/标签像论文（`stages.py:314-351`）；再通过机械引文检查、结构下限及 `# HANDOFF — PAPER`（`stages.py:354-371,447-455`）。它不执行编译，也不验证 PDF 比 tex 新。
- Review→done：重新跑上述 paper/引文/结构门，并要求 `paper/REVIEW.md` 含 `**Verdict:** done`、四个固定 section、assessment 内字面量 `Scientific:/Visual:/Language:`，且 accept case 归一化字符串长度至少 40 个字符（不是 40 词；`stages.py:374-414,456-464`）。最终还须 Reviewer status=done、staged paper mission scope=`final_submission`（`argus_skill/manager/stage_decider.py:392-436,625-652`），以及合法 `RESEARCH_RESULT`：有 evidence、correctness=verified、无 failed fidelity；publishable/doctoral 还需 breakthrough class、verified_new novelty 与相应 significance（`argus_skill/core/research_contract.py:292-345`）。
- `.argus/PIPELINE_STATE.json` 的底层写入是原子 tmp+replace（`argus_skill/core/pipeline_state.py:49-66`）。初始 stage 由 `persist_vertical` seed（`argus_skill/skills/vertical_select.py:620-637`）；推进/完成通常由 Manager 调 `advance_stage/complete_final_stage`（`argus_skill/manager/_stage_ops.py:669-742`）；idea selector 写 `selected_idea`（`idea_portfolio.py:1009-1046`）；Reviewer 写 `current_verdict/next_action`（`argus_skill/reviewer/_core.py:271-327`）。所谓“Manager sole writer”不是权限边界：`advance_stage` 明说不认证调用者，`advanced_by` 是自由文本（`stage_machine.py:479-501`）；Supervisor 的 Planner request 也可直接调它（`argus_skill/life/supervisor/_planning_cycle_enqueue.py:151-205`）。`NEXT_OWNER` 只控制轮次 handoff，不决定 stage。
- agent 在 `current_stage=="build"` 时可以写 `paper/main.tex`，不会有任何 stage-aware 文件写入拦截或负向检查；Build validator 根本不检查 paper（`stages.py:445-446`）。提示只要求“执行当前 playbook/不要改 stage state”（`argus_skill/verticals/research/prompt_policy.py:103-116`），甚至泛称 manuscript source 是可用 work product（`106-109`）。
- 没有检查论文主题仍对应 `selected_idea.route_id`。全仓 research 的 route_id 消费仅在 `idea_portfolio.py`；后续 `stages.py` 的 paper/review validator 不读 `selected_idea`（`stages.py:354-464`）。稿件快照只能证明被审版本未变，不能证明选题一致（`argus_skill/manager/_stage_ops.py:885-909`）。

## 2. Paper 阶段实际做什么

- 核心机制是 prompt orchestration：角色提示要求先打开唯一 Paper playbook（`prompt_policy.py:23-34,103-116`）；playbook 再让 Engineer 读 handoff/venue/author kit、写整稿/图表/引文并编译（`argus_skill/verticals/research/skills/research-paper-playbook.md:21-54`）。runtime 没有 `produce_paper()` 或固定步骤执行器。
- Outline：`draft_outline.py` 只有 schema、parser、validator/cross-check，没有生成器（`argus_skill/verticals/research/draft_outline.py:1-19,216-273,281-381`）；除 re-export/测试外无生产 call site。结构门明确允许没有 `DRAFT_OUTLINE.md` 的完整稿通过（测试钉死于 `tests/skills/test_paper_structural_minimums.py:54-67`）。
- LaTeX template：`venue_profiles.py` 只是静态/本地 profile 数据与 resolver；profile 记录 documentclass、style URL/files 等（`argus_skill/verticals/research/venue_profiles.py:90-138,316-445`），按 env→本地 JSON→state 解析，缺 venue 会报错（`485-546`）。没有代码自动下载/复制/实例化模板；Engineer Skill 被告知读取 current official author kit（`skills/engineer/venue-paper-drafting.md:8-31`）。
- Compile：没有自动编译器。Skill 仅建议 Engineer 自行运行 `latexmk ... paper/main.tex`（`skills/engineer/venue-format-preflight.md:25-34`）；离场门只读取已存在的 PDF/HTML（`stages.py:314-351`）。因此旧 PDF、空白但一页的 PDF、或与 venue 不相干的 HTML 都可能越过这一层。
- Figures：Visualization Router 要 Engineer 按语义选 Matplotlib/PPT/FigureSpec/Draw.io/HTML 等，image-2 仅用于非 claim-bearing illustrative asset（`skills/engineer/research-visualization-router.md:18-30`）。没有 daemon 自动生图。
- `figure_tool.py` 本身只有 v2 prompt、review、cache/freeze/sync 等 subcommand，没有 generate subcommand（`argus_skill/verticals/research/figure_tool.py:1166-1269`）；真正请求 `/images/generations` 的是通用 `tools.image_api.generate_image`（`argus_skill/tools/image_api.py:431-455`），由 Engineer 在选择该 route 后显式调用。`gpt-image-2` 来自 capability route/default，而非 Paper 自动步骤（`argus_skill/life/research_profile.py:20-21,166-200`）。
- 新 v2 prompt 确实存在并由 `figure_tool paper-prompt` 使用（`figure_tool.py:54-55,89-146,210-228,1272-1313`），但没有生产 call site自动调用；当前精简 `paper-illustration-image2.md` 直接让 Engineer“写 prompt→调用通用 image_api generate”，未引用 v2 template（该 Skill `8-23`）。所以答案是“可手动用，默认不保证用”。
- Citations：Paper/Review 离场自动跑确定性 `check_citations`（`stages.py:354-370`），检查引用键解析、重复 key、author/title/year、placeholder（`integrity_gate.py:164-253`）；代码明确不验证 BibTeX 是否真是那篇论文或是否支持句中主张（`integrity_gate.py:17-21,170-175`）。语义真实性只靠 LLM Reviewer。
- Language：旧 `generate_academic_language_review` 是 deterministic heuristic + reviewer-model advisory，并可写三个独立 review/history 文件（`academic_language_review.py:249-385`）；全仓无自动 call site。现行流程是三并行中的 Language LLM pass，再由 integrated LLM Reviewer裁决。
- Layout：`generate_layout_review` 可确定性调用 pdftoppm/mutool、pdftotext，再把页 PNG 发给 vision route（`paper_layout_review.py:92-203,260-307,529-542,948-1006`），但同样只有自身 CLI call site，不在 Paper/Review runtime 路径。现行自动硬门只用 pypdf 数页。
- 分类：状态/文件存在、BibTeX 和结构 regex、pypdf 可读性是确定性代码；写稿、模板取得、编译、生图、引文语义核对及语言/视觉质量是 Engineer/Reviewer LLM 根据 prompts 自主操作。

## 3. Review：三并行 + 一权威 Reviewer

- commit `4a72b03ac` 的实现位于 `_parallel_final_review_passes`：仅在 research + review 且 `current_verdict` 仍为空/in_progress/mapped 时启用（`argus_skill/reviewer/_core.py:31-53`）；fork 三个独立 backend，以 `ThreadPoolExecutor(max_workers=3)` 同时发 Scientific/Visual/Language read-only prompt（`55-166`）。任一 backend/空输出失败即 blocked；全部有文本也固定返回 `continue`，把三份文本拼进 reason（`197-268`）。
- `SupervisedEngineer.run` 在 Engineer round 之前调用该 helper、把组合发现覆盖写入唯一 `paper/REVIEW.md`，并把 next_action 给 Engineer（`argus_skill/engineer/runner.py:205-283`）；Engineer 修复后，普通 `Reviewer.evaluate` 作为 integrated authoritative reviewer 被调用（`runner.py:284-401`；`argus_skill/engineer/round_reviewer.py:99-206`）。其 verdict 再覆盖同一 REVIEW.md（`reviewer/_core.py:305-327,643-651`）。
- 并行调用确实是不同 runner、read-only sandbox；共享实例会被拒绝（`reviewer/_core.py:84-126,128-166`）。但没有保存三 pass 的独立结构化 verdict、稿件 hash 或“已实际打开 PDF”的证据；最终硬门只做 REVIEW.md 字符串形状检查，手写同形文本也能通过。
- PDF 能力：三个 prompt 明说读 rendered output（`reviewer/_core.py:55-80`），backend 在 read-only workspace 中理论上可自行跑 shell；然而 runtime 不调用 pdftotext/pdftoppm、不附加页图，也不检查 tool trace。真正实现这两命令+vision 输入的是未接线的 `paper_layout_review.py`。因此科学/语言 pass 很可能读 tex；视觉 pass“能选择打开”但系统不能证明它看过任何 PDF 页面。
- commit `443876007` 的 Review playbook 要求 preliminary pass 各自只打开对应 specialist Skill（`skills/research-review-playbook.md:58-74`），但 `_parallel_final_review_passes` 的 `RunnerOptions` 没有 `skill_paths`，prompt 也未给 Skill 路径（`reviewer/_core.py:55-82,128-144`）。只有后续普通 Reviewer 注入 native skill paths（`reviewer/_core.py:431-438,503-519`）。这是新 playbook 与实现的落差。

## 4. 矛盾、冗余与 HEAD 现状

- 已消失：HEAD `stages.py` 不再要求“摘要首句先报数字”，只要求整稿由贡献/最强结果主导（`stages.py:164-183`）；旧 `emnlp/aaai-academic-language-review.md` 已删除，当前 `venue-academic-language-review.md` 也没有“禁止数字开头”规则（该文件 `8-30`）。所以题述的 stages.py↔Markdown 直接冲突在 HEAD 不再成立。
- 仍残留：旧 Python language heuristic 仍把“问题/gap 前先给 numeric/result”列为 major issue（`academic_language_review.py:1468-1478`），而且 prompt 仍重复这条（`930-933`）。它现在是未自动调用的 advisory，不是 stage hard gate。
- 仍冲突：全局 `MIN_REVIEW_ABSTRACT_WORDS=170`（`academic_language_review.py:40-47`）被无条件用于所有 venue（`1436-1449`）；但 AAAI 与 Frontiers profile 都是 150 且 soft/advisory（`venue_profiles.py:371-374,443-445`），model prompt又说非 hard venue 不按固定最小长度判断（`academic_language_review.py:871-893`）。手动运行该工具时，同一 payload 同时带相反信号。
- 固定下限仍是硬门：1 个可解析外部图、8 个正文 cite key、8 个被引用 bib entry、Related Work 800 chars（`paper_structural_minimums.py:41-46,427-539`）；还强制一个被关键词识别的 Figure 1 overview（`460-475`）和 appendix（`552-571`）。这些对理论/短文/非典型 venue 不做类型适配。
- 3ccfba162/443876007 宣称单一 playbook、按需 specialist、不要额外流程报告；但旧 outline/experiment-audit validators 与 method-freeze/figure-provenance/language-layout writers 仍保留。新 Figure Studio 甚至明确“不要 provenance/review files”（`skills/engineer/paper-framework-figure-studio.md:35-37`），而 `figure_tool.sync_paper_metadata` 仍要求并写 provenance/manifest（`figure_tool.py:927-960,1118-1158`）。这是冗余的两代工作流。
- Paper playbook 的 Work 第 2 步无条件要求下载 exemplars（`skills/research-paper-playbook.md:23-29`），但 443876007 新增的 progressive table 又说仅在结构/视觉需要校准时打开 exemplar Skill（同文件 `62-76`）；是否必做不一致。
- Reviewer 通用提示说 done 不要求 artifact completeness（`argus_skill/roles/prompts/reviewer.py:630-638`），但 Manager 的 Paper/Review hard gate要求固定图/引用/Related Work/appendix/固定 REVIEW 字符串。最终裁决实际上仍由隐藏在 Reviewer 之后的 host quota 覆盖。

## 5. 测试

- 原建议命令直接运行不可用：环境没有 pytest-timeout，`--timeout 600` 是 unknown argument；而对整个 `tests` 做 `-k research` 会在过滤前导入三个无关 Quant 文件，并因缺 `scipy` collection error。
- 可执行命令：`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests -k research -q -o addopts='' -p no:cacheprovider --ignore-glob='tests/test_quant*.py' --disable-warnings`。
- 结果：`313 passed, 6 failed, 6276 deselected, 1 warning in 16.01s`；墙钟 17.06s。
- 6 个失败：`tests/domains/test_chemistry_domain.py:173` 仍期望旧 stage `research`；`tests/test_codex_live_search.py:124,181` 仍期望旧 stage/live-search contract；`tests/test_planner_prompt_budget.py:209,315` 仍期望已删 Final-submission/Research-plan prompt block；`tests/test_research_reviewer_engineering_contract.py:38` 仍期望 3ccfba162 精简掉的原句。没有修复任何失败。

## 6. Verdict（不超过 8 点）

- 否：当前更像“LLM 自主作者 + 离场审计器”，不是可重复、可观测、逐步执行的论文生产 pipeline。
- 优点：状态落盘原子、研究流程 forward-only、Reviewer 三路并发真实存在、稿件版本可绑定，BibTeX 的机械错误也会 fail closed。
- 风险 1：Build/Experiment 的硬门仅验证可任意编写的 HANDOFF 标题；论文可提前写，且选题、代码、实验、paper 没有 route/hash 贯穿绑定。
- 风险 2：compile、template materialization、outline、figure creation、language/layout review均无统一执行器；已有功能模块多数未接线或与新 playbook 冲突。
- 风险 3：视觉 Reviewer 没有强制 PDF→page images→vision 输入；done 只需 LLM 文本和可伪造的 REVIEW.md 形状，不能证明 page-by-page inspection。
- 修复 1（P0）：在 `argus_skill/verticals/research/stages.py` 为 Build/Experiment 增加真实 artifact validators，并把 `selected_idea.route_id` + selected thesis digest 写入/核对实现配置、实验结果与 `paper/main.tex` metadata；不要再以 HANDOFF 标题代替证据。
- 修复 2（P0）：在 `argus_skill/engineer/runner.py`/`argus_skill/reviewer/_core.py` 接入受控 Paper/Review driver：实际执行 venue compile、检查 tex/PDF freshness，调用 `paper_layout_review.py` 渲染全部页面并把 PNG 直接附给 Visual 与 integrated Reviewer，同时把三 pass 绑定到同一 manuscript snapshot。
- 修复 3（P1）：以 `venue_profiles.py` 为单一约束源，删除/改成 claim/venue-aware 的 `paper_structural_minimums.py` 固定配额和 `academic_language_review.py` 全局 170 词规则；删除或正式接线旧 outline/audit/freeze/provenance/review modules，并同步修复上述 6 个测试。
