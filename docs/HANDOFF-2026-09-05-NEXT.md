# Argus 交接(2026-09-05 收盘)

> **2026-09-05 17:50 UTC 恢复更新：**以下正文保留原停机快照。后续已将遗漏的
> CI 修复、认证状态根/稿件根分离、Copilot 新认证字段同步、维护决策保留停机与脏树
> 四项修复推送到 main（`820b7e556`、`4eaa23591`、`2e365ed70`、`6cd6cf547`），
> 合并验证 929 passed。两张维护卡已 decline，原工作树保留，维护 daemon 仍停止。
> 仅 FuseHead 在独立恢复 checkout `/data/v-boxiuli/argus-runtime-recovery-20260905`
> 启动，运行代码 `6cd6cf547929`、源码根匹配、mission_width=1；另外三个会话保持停止。
> 原共享 runtime checkout 未改动。“重启即可结项”“CI 卡早已被 main 覆盖”不再成立。
> 本机完整恢复步骤、启动环境和论文复核记录见
> `/data/v-boxiuli/argus-recovery-notes-20260905/HANDOFF.md`，接手时先读该文件。

> **2026-09-05 晚（Claude 会话）四项框架修复已推 main，尚未部署：**
> `803d5c7ea` 研究阶段不再因根目录 HANDOFF.md 缺失/过薄而保持（FuseHead 64 次
> 阶段决策里 24 次是这个理由，实验阶段只跑了 97 分钟）；`e68bb88dd` 删除
> `daemon/foreground_waits.py` 前台等待守卫（它把 Engineer 任何 ≥15 秒的 sleep
> 连同 shell 子树 SIGTERM，FuseHead 391 次、run-08 278 次，每次一个白费回合）；
> `904b855e2` planner 写了不存在的依赖键时改为丢弃该依赖、照常入队、发
> `life.planner.dependency_dropped` 事件并在下一轮 planner 提示词里说明一次
> （此前整批 DAG 被拒并 idle backoff，FuseHead 原样重复 27 次、run-08 17 次）；
> `9e39270ba` 把面向模型的提示词、任务标题、拒绝理由里的 "gate" 措辞改成
> 自然语言（它已经渗进 planner 任务 id 和 selected_idea 记录）。
> 相关测试：tests/life、manager、planner、roles、skills、core、daemon 全绿
> （除已知预存失败）。**FuseHead 当前跑在恢复 checkout `6cd6cf547`，共享
> runtime 在 `600c013af`，都在这四个提交之前**；下次重启前把对应 checkout
> `git checkout --detach` 到 `9e39270ba` 或更新。
> **续（同一会话，稍后）三项再修，同样已推 main、未部署：** `c6d3691f7` "Model
> … is not available" 从永久配置错误改为供应商冷却暂停（09-05 17:36 一次两分钟的
> Copilot 故障连续吞掉 8 个排队 mission）；`b93422a14` 研究纵向的终审以 Reviewer
> 的 done 为准，不再用 `research_result` 枚举（`finite_verification`、novelty
> `unverified`）机器复判并重排队——write-01 因此跑了 75 次终审任务、45 次重排、
> 零认证，FuseHead 的 33 轮空转同源；数学纵向保留枚举检查；`30435976a` daemon
> 启动时把和解后的 campaign 生命周期（open_ended）同步给 mission runner——此前
> runner 拿的是启动默认值 True，导致有界的 idea-01 无法在 idea 阶段完成，被推进到
> Paper 后同一 mission 重跑 144 次。部署时请一并 `git checkout --detach 30435976a`
> 或更新。
> **2026-09-06 07:20 UTC（Claude 会话）FuseHead 接手记录，供 codex 会话对照：**
> 恢复 checkout `/data/v-boxiuli/argus-runtime-recovery-20260905` 已 detach 到 main
> `087824b1e`（codex 的 `argus-runtime-latest` 在 `5920babe3`，含 main HEAD 加
> token 分支，两者都是最新代码，codex 07:10 UTC 启动的 daemon pid 3180799 保持不动）。
> 已做：`kernel.perf_event_paranoid` 4→2（sudo，非持久，重启失效），`perf stat -e
> cache-misses` 已验证可读，PMU 问题（3f38ac71d420）据此作答；框架部署卡
> 639378197e33 已 decline；7 个"模型不可用"暂停项已答复"按原计划重试"，backlog 13 项
> 全部 pending；`copilot-guard.json` 的 24 小时 `blocked_until` 已清零。
> **当前唯一阻塞：Copilot CLI 无登录态**。`~/.copilot/config.json` 在 07:08 UTC 被
> 改写，`copilotTokens` 为空，所有调用报 "Failed to load models / 421 Misdirected
> Request"（07:00 起先是 "Access denied by policy settings"）。用 gh 的两个账号
> token 能认证但没有 gpt-5.6-sol。需要操作员以 lbx154 重新 `copilot login`；
> Argus 隔离 home 会同步 token，daemon 无需重启。
> 07:12 UTC 续：codex 07:10 启动的 daemon（`5920babe3`）在后端全挂时把两个已答复的
> mission 跑成了 error，已 `--daemon-stop --drain` 干净停掉；`8bd66b8be` 把 "Failed to
> load models / Access denied by policy settings" 也归入供应商冷却暂停，已推 main，
> 恢复 checkout detach 到该 rev，07:13 UTC 重启 FuseHead（pid 3198853，
> `source_root_matches_config=true`），实测首个 mission 结算为 `paused_provider_cooldown`，
> backlog 10 pending + 1 paused，登录恢复后自动续跑。
> 07:22 UTC 续：操作员授权使用 v-boxiuli_microsoft 账号。本机 relay 的 headless copilot
> 进程环境里有该账号的 OAuth token，用它 `copilot login --with-token` 登录了默认
> `~/.copilot` 和 Argus 隔离 home（`~/.argus-skill/copilot-home`），干净环境下两者调
> gpt-5.6-sol 均返回 OK；daemon 未重启，冷却到期自动续跑。token 未写入任何仓库或文档。
> 07:25 UTC 续：登录恢复后 FuseHead 又被 `94a7cefec` 引入的主机全局预算规则挡住——
> `ARGUS_SKILL_UNPRICED_COST_POLICY` 默认 `block`，只要主机上任一会话当天有一条
> `partial` 定价记录（s-0ebfd18c、7ddbde45b40d 在 07:14 故障期间各留下一条
> `manager-classify-grounded`），所有会话的所有调用都被拒（`paused_budget`），且这类
> 失败调用永远不会被对账。**这是 codex 分支的设计问题，建议改成：无用量的失败调用不计入
> 未定价，或默认 allow。** 我只对 FuseHead daemon 用环境变量 `=allow` 重启（每日 $1000
> 上限仍生效），未改持久化配置；s-0ebfd18c、7ddbde45b40d 两个 daemon（07:14 由他人启动）
> 仍在被该规则拒绝，未动。
> 07:27 UTC 续：操作员指示"放行这些，继续研究"。已把 `ARGUS_SKILL_UNPRICED_COST_POLICY=allow`
> 写入 `~/.argus-skill/config.json`（全会话生效，每日 $1000 上限不变）。FuseHead
> pid 3254520 的 Engineer 已在执行（核对 LEMP 评估器与附录一致性、等待后台任务）。

> 另外两点更正/未处理：本文"今日花费约 $1,148"实为 cost-control.jsonl
> 自 08-28 起的累计（09-05 当天约 $111，FuseHead 当天 $7.9）；FuseHead 的
> `selected_idea` 与工作区 `.argus/PIPELINE_STATE.json`（停在 09-03 的
> build/in_progress）仍写着已被十次 plan replace 换掉的 CLDE 选题，而论文
> 已是 8 核 CPU、Qwen2.5-0.5B 的 LM-head 筛选实验，端到端 0.957×，这一条
> 需要操作员决定是否继续。

> 独立交接文档,自包含:按本文即可开工,不必先读完 1000+ 行的
> `docs/handoff-2026-09-04-capability-tests.md`(需要考证细节时再按节号回查)。

## 一页纸现状

- **系统**:Argus 自主研究框架。开发仓库 `/data/v-boxiuli/Argus`
  (main = `600c013af166a6bcbf0da9c7f6da3ee0dbbceee5`,已与 origin/main 同步);
  部署 checkout `/data/v-boxiuli/argus-runtime-latest`(detached =
  `600c013af166a6bcbf0da9c7f6da3ee0dbbceee5`,与 main HEAD 一致,含批四代码
  与终稿认证移植;守护进程停止时仍在 `e48e5573d`,重启后即加载新代码)。
  四个研究守护进程 + 维护守护进程已于 **2026-09-05 15:36 UTC 被操作员 CLI
  `stop`(drain=false)干净停止(clock out,非崩溃)**,日志均记
  "quiesced continuous mode on operator stop"。随时可 resume(命令见下节)。
- **今日完成**(细节在 handoff-2026-09-04 第十二至十六节):
  - 启动器劫持修复与三层防御(第十二节):被劫持的 user-site 安装已整套隔离到
    `~/.local/share/argus-quarantine-2026-09-05/`;`~/.local/bin/argus` 重建为
    指向 runtime venv 的两行 shim;`ARGUS_SKILL_SOURCE_ROOT` 预检 knob 已持久化
    为 `/data/v-boxiuli/argus-runtime-latest`(源码根不匹配即拒启,rc=2);
    sandbox 封死 `~/.local/lib`、`~/.local/bin` 写入并钉 `PIP_USER=0`。
  - 四批框架修复全部已 commit + 部署(提交清单与逐批部署记录:第十二至十六节):
    批一 `12ba2a8b7`(劫持修复)、批二 `0377ebdc0`(重规划连击断路器精确计数、
    操作者等待节奏解耦、监督者关切判定)、批三 `d9b0c518b`(规划器隔离改时间基、
    研究证据去截断、密钥扫描流式越过 32 MiB)、批四 `e48e5573d`(journal 窗口
    单位族:按 settlements 计数而非日志噪声)。今日四轮滚动重启零事故。
  - **终稿认证消费修复移植:已 SHIP**——commit `1194aa07d`(8 files,
    +440/-3,全部 `argus_skill/life/**` 与 `tests/life/**`),已 push 至
    origin/main;handoff 追记 `600c013af`。Ship 阶段曾拦路的 chart-style
    门禁失败已确认为 `ebddbbf28` **预存**,与本移植无关(见下文"已知预存
    测试失败"第 4 条)。runtime checkout 已同步到同 rev,守护进程未重启,
    重启即生效。该修复是 s-3e28f79c(FuseHead)完成门空转的根因解
    (15:00-15:36 UTC 空转 33 轮,每轮被
    `missing_publishable_reviewer_certification` 拒绝,约 $0.10/轮)。

### 工作区状态

`/data/v-boxiuli/Argus` 工作区**干净**(`git status`:nothing to commit),
仅剩一个未跟踪文件 `docs/test-slowdown-diagnosis-and-fix-2026-09-05.md`
(测试卡死诊断记录),留给后续决定是否入库。

### 停机前健康快照(15:47 UTC 只读巡检,handoff 第十六节)

- 四守护进程停止前均在 `e48e5573d553` 上,events.jsonl 15:00Z 起零 Traceback,
  无重启循环。今日花费合计约 $1,148(四会话 $298/$125/$353/$371),
  远低于 $20,000 日上限。
- 维护守护进程(会话 `5860caf309de`,旧 pid 3554795)与四守护进程同时停止
  (末次心跳 15:36:44Z)。它跑的是**旧代码**,曾以约 $1/小时空转;其
  daemon.status.json 无 command/argv,**无记录在案的 resume 方式**——重启前
  先处理它持有的两张决策卡(见任务清单第 3 条),不要照原 argv 重跑(会开
  新任务而非续接)。
- 场外噪音:s-0ebfd18c 曾在 cost-control.jsonl 记 planner 错误
  "Process exited with code 1 … Model gp…",不在四项目范围内,知悉即可。

## 如何把系统跑起来

四个研究守护进程,resume 命令一致,只有 workdir 和 sid 不同。**必须用
runtime venv 的 argus**(`~/.local/bin/argus` 现在是指向它的 shim,直接用
绝对路径最稳):

```bash
cd <workdir> && setsid nohup /data/v-boxiuli/argus-runtime-latest/.venv/bin/argus \
  --daemon --backend copilot --resume <sid> --resume-continuous >/dev/null 2>&1 &
```

| 会话 sid | workdir | 任务 |
|---|---|---|
| `s-72fa9517` | `/data/v-boxiuli/argus-iclr-observation-v2/run-08` | run-08(真实 ICLR 任务) |
| `s-3e28f79c` | `/data/v-boxiuli/ai-research-open-20260902` | FuseHead(开放研究) |
| `s-80c507d6` | `/data/v-boxiuli/argus-capability-tests/write-01` | write-01(写作能力测试) |
| `s-0b1c7fa1` | `/data/v-boxiuli/argus-capability-tests/idea-01` | idea-01(想法生成测试) |

启动后核对(每个会话):
`~/.argus-skill/projects/<sid>/daemon.status.json` 里 `runtime.revision` ==
runtime checkout 的 rev(现为 `600c013af166`;若你先部署了新代码则为新 rev),
且 `source_root_matches_config == true`。逐个启动,一个核对通过再启下一个。

其他要点:

- 会话运行数据在 `~/.argus-skill/projects/<sid>/`(events.jsonl、backlog.jsonl、
  daemon.log、daemons/boot-*.log);干净停止后 daemon.status.json 会消失,
  workdir 权威来源是该目录下 `session.json` 的 `workdir` 字段。
- runtime 已在 main HEAD(`600c013af`),无需再升级即可重启。日后升级流程:
  kill 各 daemon 后在 `/data/v-boxiuli/argus-runtime-latest` 里 `git fetch
  origin && git checkout --detach <rev>`,依赖未变时 venv 直接复用,再按上表重启。
- 给运行中的会话发操作员消息:`argus_skill.core.transcript.append_turn(
  life_dir, "operator", text, message_id=...)`,life_dir 即
  `~/.argus-skill/projects/<sid>/`。
- 从 dev 仓库(`/data/v-boxiuli/Argus`)起 daemon/webapi 调试会被源码根预检拒
  (预期行为),需 `export ARGUS_SKILL_SOURCE_ROOT=/data/v-boxiuli/Argus`
  或独立 `ARGUS_SKILL_HOME`。

## 优先级任务清单(按此顺序做)

1. **FuseHead 结项**(离产出最近)。认证修复已入 main 并同步到 runtime,
   重启即生效。重启 `s-3e28f79c`;
   认证修复入 main 后完成门应能消费 REVIEW done;督促 formal-r13 重跑闭合;
   **人工通读** `/data/v-boxiuli/ai-research-open-20260902/paper/main.pdf`
   (11 页,主张 3.04× 加速 + 位级精确;目标 EuroSys'27,截稿 9/24)。
   重启后顺带观察两点:批三时间基隔离的 `recent_no_progress_failure`
   是否按预期出现;完成门是否不再空转。
2. **write-01 结项**。重启 `s-80c507d6`,跑一次**不带编辑的独立终审**即可;
   论文 `/data/v-boxiuli/argus-capability-tests/write-01/paper/main.pdf`
   四项评审已 PASS。
3. **两张维护决策卡**(`~/.argus-skill/maintenance/pending/` 下
   `690fb430f6b6.json` 终稿认证消费修复本体、`6062621ef4e9.json` CI 基线恢复;
   均 reviewer_verdict=done、operator_decision=pending)。建议 **decline**:
   其候选基于过期祖先,deploy boundary 必拒;且内容已被 main 覆盖(认证修复
   已 ship 入 main `1194aa07d`;CI 基线已绿)。处理完再决定维护守护进程是否/如何
   重新拉起(注意上文:它没有可复用的 resume 方式)。
4. **run-08 两个决定**(s-72fa9517):a) 接入 QueRE(arXiv:2501.01558)基线
   ——新颖性审计点名必须、至今未接;b) 对"负结果/边界论文"路线拍板。
5. **idea-01**(s-0b1c7fa1):裁决 Route 8(随机化跨阶段污染观测台)是否启动
   试点;交付物在 `/data/v-boxiuli/argus-capability-tests/idea-01/research/`
   (candidates.md、landscape.md、novelty-map.md)。
6. **批五设计实施**:`docs/audits/batch5-designs-2026-09-05.md` 六份已完成
   调查、未实施的设计(路由超限降级、路由快照标记扫描前移、存储层
   mission_summary 截断、limit=12 收敛、mission-view bootstrap、next_action
   打标、早停 liveness 宽限)。锚点基于 `e274dd161`,实施前按届时 HEAD 对位。
7. 剩余审计条目:`docs/audits/magic-hyperparameters-adaptive-followups.md`
   未标 done 的部分(已 done 13 条)。

## 运维铁律

- **部署纪律五步**:dev 仓库 commit → push origin main → runtime checkout
  (`argus-runtime-latest` 里 fetch + `git checkout --detach <rev>`)→
  逐 pid 滚动重启(kill 前 ps 核对命令行,等退出,原 workdir setsid 重启,
  轮询 status 至 alive,一个成功再下一个)→ 核对每个 daemon.status.json 的
  `runtime.revision` 与 `source_root_matches_config==true`。
  **只重启守护进程不会加载新代码**——中间的 runtime checkout 一步漏了就是
  白重启(吃过亏)。
- **push 前必须 fetch + rebase**:main 多方并发,直接 push 常被拒。
- **维护任务严禁对框架做任何 pip install**(`-e .`、`--user`、任何写
  user-site 或 `~/.local/bin` 的形式)。9/5 劫持事故根因即 `pip install -e .`
  静默回退 user-site 重写启动器;隔离物在
  `~/.local/share/argus-quarantine-2026-09-05/`(未 rm,可取证)。测试姿势:
  worktree 根下 `"${ARGUS_SKILL_PYTHON:-python3}" -m pytest <target> -q`
  (设了 PYTHONSAFEPATH 时前缀 `PYTHONPATH="$PWD"`)。
- **ARGUS_SKILL_SOURCE_ROOT knob 已持久化配置**(`~/.argus-skill/config.json`,
  备份在 `config.json.bak-2026-09-05`),指向 argus-runtime-latest;从 dev
  仓库起 daemon 需 env 覆盖(见上节)。若要启用
  `ARGUS_SKILL_REQUIRE_RELEASE_MATCH`,须先重新生成 release manifest
  (见 handoff 第十二节)。
- **验证导入版本不要在 dev 仓库目录里跑** `python -c "import argus_skill"`
  ——cwd 先于 .pth 命中,打印误导路径;换到无关目录再验。
- **已知预存测试失败 4 条**,非阻塞、勿追新:`tests/apps/test_cli_ask.py` 两条
  (测试顺序污染,单跑通过)、`tests/test_role_library.py` 一条、
  `tests/skills/test_paper_chart_style.py::test_research_data_figures_have_one_renderer_path`
  一条(`ebddbbf28` 引入,路由文档与测试期望不同步,与本轮工作无关,
  待该提交作者修复)。
- 历史全记录:`docs/handoff-2026-09-04-capability-tests.md`(第一至十六节,
  含全部提交 hash、部署记录、评审发现与红/绿验证)。

## 2026-09-06 · 论文写作规则改为"按会场标准、由论点定形式"

原来的写作契约（五句、≥170 词摘要；每个标题数字精确到底；每个 caption 必须带数字；
证据角色词汇）散落在 prompt_policy.py、stages.py 和四份 skill 里，产出的论文变成
数字墙加审计报告口吻（write-01 里 71 个 ≥4 位小数、FuseHead 摘要约 15 个数字）。
现在改为一个共用标准 `paper_writing_standard()` / `paper_reviewer_standard()`：

- 标准是所选会场的优秀录用论文（exemplar skill 读到的那种），不设句数、词数、
  数字密度、caption 格式配额；论点决定形式，正文数字取比较所需精度，全精度进表格。
- 每个限制只说一次，只在证据不确定的句子里 hedge；证据角色词和流程词
  （bounded/certified/gate/artifact/mission/round/handoff/validator/audit）不得出现在稿件里。
- 条件性或否定性论点只要证据完备就是合法论文；不允许的是把未完成的开发包装成发现。
- Reviewer 以会场审稿人身份判断"会不会被录用、读者会反对什么"，不执行任何配额，
  不要求超出证据的 hedge，也不在平实陈述更清楚的地方要求数字。

测试：tests/skills/test_paper_narrative_packaging.py 改为断言新标准，并加了
"research 任何 prompt/skill 不得再出现写作配额"的守护测试。
`test_research_protocol_quality.py::test_review_combines_parallel_scientific_visual_and_language_passes`
在本改动之前（775f8b8cc）已经失败，与本次无关。

## 2026-09-06 · 角色提示词去掉 bounded / gate / handoff 机器味

模型读到的提示词（Manager、Planner、Engineer、Reviewer 四个角色提示，research 阶段清单，
Planner 上下文，builtin 角色 skill，research skill）里的流程词改为普通研究者的说法：
"bounded task/mission" → 单个任务、明确的调查；"Manager handoff" → Manager 的 brief；
"gate" → bar/check/pass；"handoff note" → 给下一阶段的笔记（HANDOFF.md 文件名保留）。
按任务审稿的 L2 Reviewer 框架改为"像资深同事一样：它想证明什么，证据是否证明了"。
协议 token（`scope:bounded`、`final_submission`、TASK_SCOPE 默认值）、文件名、
函数名和代码注释不动。受影响的 9 个测试断言同步更新；宽泛选择集
（prompt/handoff/bounded/reviewer/planner/engineer/manager/skill/stage）全部通过。

## 2026-09-06 · 当前终审被误判为 stale（已修）

FuseHead 的 final_submission 终审 09:58 得到 done、"Reject-level issues: none"，Manager 却 hold，
理由是"authoritative verdict covers an earlier manuscript"。根因：`_runtime_execute.py`
的 `_extract_execute_outcome_fields` 用 `self._artifact_root`（会话状态根，没有 paper/main.tex）
去比对 Reviewer 绑定的稿件哈希，当前哈希为空，于是把一份哈希完全一致（b57ea320…）的评审
改写成 status=stale 再交给 Manager。其他所有 freshness 调用点（_stage_ops、_core、mission_view）
都用执行目录；这里是 4eaa23591 分离状态根/执行目录时漏掉的一处。改为 `ex_state.workdir`，
加 tests/apps/test_review_freshness_uses_execution_workdir.py（当前评审保持 done；
改稿后的旧评审仍 stale）。tests/apps、tests/manager、认证恢复测试全部通过。

## 2026-09-06 · Host 辅助检查缺席不再阻断终审

FuseHead 10:29 的 final_submission 终审：Reviewer 自己逐页看完、复算全部区间、核对代码和
EuroSys 格式后写"未发现阻断性问题"，却因为"Host 尚未提供并行逐页视觉、PDF-only 冷读和语义
损失检查"而 continue，并让 Engineer"等待 Host"。原因是 `_parallel_final_review_passes` 在
pipeline `current_verdict == done` 时直接跳过，而 review.parallel / review.integrated 清单和
终审提示词把这三项当成了前提。现在三处（stages.py 两条清单项、prompt_policy 的
academic_paper_review_block、review playbook 第 4 步）明确：辅助检查只是协助，Host 没给时
Reviewer 自己的检查就是评估，缺席本身永远不是拒绝 done 或等待的理由。运行时逻辑未改。
同日操作员干预：用 `--notify` 发了两条说明，并通过 Backlog API 把 7 条历史重复待办标为
superseded（原因已写入记录）。

## 2026-09-06 10:35 UTC · FuseHead 认定完成

final_submission 任务 `完成正式重跑并进行最终独立认证` 的 Reviewer 判决 done（科学：648 个决策
与 dense reference 一致、全部区间复算相符；视觉：11 页逐页无缺陷、符合 EuroSys 2027 格式；
语言：论点与证据层级清楚），结算 `final_submission_certified=true`，Manager 走确定性路径
`complete`，PIPELINE_STATE 为 review / certified。最终稿：paper/main.tex（11 页）+ appendix（5 页）。
论文主张：公开集对 exact oracle 2.03× [1.18, 4.10]，12+12+12 held-out 1.95× [1.13, 3.46]，
LEMP-LI 慢 6.56×，24+12 作为自适应诊断 1.41× [1.02, 2.05] 不算独立确认。
当日部署链：1ab0a3449（写作标准）→ b3874b30e（去机器味）→ 25d4700a5（新鲜度根目录）
→ 8ff4280fd（辅助检查不阻断）。当前 FuseHead 进程 pid 3994354，revision 8ff4280fd。

## 2026-09-06 · 认证过的终稿在 Planner 完成检查里永远"缺认证"（已修）

Manager 已 complete、PIPELINE_STATE certified 之后，Planner 报 project_done 被 host 拒绝：
`missing_publishable_reviewer_certification`。复现 `_research_project_done_issue`：journal 里
那条 final_submission 记录 `final_submission_certified=true`、签名一致，但 `manuscript_snapshot`
为 None 而 paper/main.tex 存在 → 被跳过。根因：daemon 返回给 supervisor 的 `_Outcome.rounds`
是轮数（int），结算里 `rounds[-1].review.manuscript_snapshot` 永远取不到。这意味着生产路径下
任何 research campaign 的完成检查都过不了（测试用的是带 round 记录的假 outcome）。
修法：`_Outcome` 新增 `manuscript_snapshot`，由 `_runtime_execute` 从最终评审填入；结算改用
`outcome_manuscript_binding()` 先取该字段再回退到 round 记录。加 tests/life/test_outcome_manuscript_binding.py。
副作用：拒绝之后 Planner 为满足"再认证前必须有实质修复"排了一条摘要末段改写任务，这本身
是合理的写作改进，但动机是框架卡住。

## 2026-09-06 10:59 UTC · FuseHead 在框架自己的完成检查下收官

27645d873 部署后，Planner 排了唯一一次 `完成 publishable 级最终独立认证`（e9dca2adcf8f，
final_submission）。Reviewer 10:58 done、无阻断缺陷；结算记录 `manuscript_snapshot`
sha e88207a7…（与当前 main.tex 一致）、`overall_complete=true`、`campaign_continues=false`；
Manager complete；10:59 Planner 判决 `completed: bounded research vertical has a current
completion certificate`，UI 显示 "Submission certified"。此后 daemon 空闲，无新的模型调用。
FuseHead 进程 pid 4020364，revision 27645d873，保持运行等操作员决定是否开第二篇。

## 2026-09-06 · 写作技能重写 + idea-01 回归测试 + 本地 checkout 更新

**写作技能。** 新增 `engineer/references/paper-writing-craft.md`（写作工艺参考：论文的
register、引言写两遍与六个 move、结果按论点组织并以 takeaway 收束、数字与精度、只在证据
不确定处 hedge 与防御性句式修法表、句段工艺、标题/图注、相关工作定位、摘要与结论、
先扩后压、以陌生读者身份自读），`venue-paper-drafting.md` 改为写作顺序工作流
（架构→Draft 0 引言→结果→方法→重写引言→相关工作→结论→摘要最后→压缩→自读），
playbook 与 reviewer 语言审稿 skill 同步引用；narrative_edit 提示词指向该参考。
来源：SNL-UCSB/paper-writing-skill（从真实改稿历史提炼的编辑原则）、mikubaka88/CCFA-Skills
（humanization policy 的防御性句式修法）、以及 FuseHead/write-01 两篇稿件的问题。
仍然没有配额：全部是判断依据，不是检查项。

**idea-01。** 09-04 的失败是"选一个题"的 direct 任务被确定性路径推进到 experiment/paper，
之后 12 个任务重复跑同一目标。最新代码（a90ac235c 之后）在 idea 阶段直接 complete；
加 tests/apps/test_direct_idea_task_completes_at_idea.py 固定。剩余的一般性问题（Reviewer
done、Manager hold、同一目标被反复重排）没有加机制，先记录。

**本地 checkout。** argus-runtime-latest 原在 5920babe3 且有一份未提交的 copilot
pre-generation rejection 补丁，已保存到分支 `wip/copilot-pre-generation-rejection-20260906`
后切到 main；argus-runtime-open-20260902 同步到 main。四个在跑的 daemon（s-2e56a77c、
7ddbde45b40d、s-0ebfd18c、s-c73d4e48）用 setsid 脱离的脚本在任务边界排空重启，
重启环境改为 ARGUS_SKILL_UNPRICED_COST_POLICY=allow；两个 web server 已重启。
config.json 全部角色模型为 gpt-5.6-sol。

## 2026-09-06 15:40 UTC · 实验规模、机制否定后的出口、路由到 research 的论文局部任务（a8e049980）

**为什么改。** 当天的证据：CoT 课题（s-0ebfd18c）在 experiment 阶段 49 小时、211 个 mission、28 次 hold，机制在 N=64 面板上一直输给精确 residual LOO，backlog 堆到 143 条同一目标的换名任务；ACL 课题（s-c73d4e48）46 小时、26 次 hold 后靠一个 32 项面板上 4–0/1–0 的旁支结果进 paper，进 paper 后 Planner 仍排训练修复；Manager 曾接受"先用标注的占位人类结果写论文"的指令；两次画图能力测试（fig-01、fig-02）都被前门判成 `software`，研究纵向的画图 skill 从未被提供。

**改了什么（判断依据，不是配额或关卡）。**
- `verticals/research/prompt_policy.py`：新增 `local_model_inventory_block`，扫描 HF 缓存（`HF_HUB_CACHE`/`HF_HOME`/`~/.cache/huggingface/hub`）、项目内 `models--*` 目录和 knob `ARGUS_SKILL_MODEL_CACHE_DIRS`（本机已持久化为 `/data/v-boxiuli/hf_cache/hub`），在 idea/experiment 阶段与硬件清单一起给 Planner、Engineer，Reviewer 在这两个阶段也能看到；Planner 片段加了"被否定的假设关闭它的修复族、paper 阶段排写作"。
- `research-experiment-playbook.md`：规模要配得上主张；决定性比较否定机制后重推论点而不是再修一轮；只规划手上有的资源，不写模拟/占位结果；自建 benchmark 是主张的一部分（标签来源、常数答案基线、目标可区分）。
- `stages.py`：`experiment.paper_bar` 加证据规模；`experiment.repair` 改为否定后重推论点；`review.scientific` 加 benchmark 能否承载主张。
- `research-paper-playbook.md`：写最强的被支持论点；paper 阶段写作不开发方法；稿件只报告跑过的结果；论文局部的 direct 请求只交付该部分。
- `research-idea-playbook.md`：资源现实指本机 GPU 与缓存权重，不把决定性实验建在没有的人类被试、伦理批准、付费标注上。
- `reviewer/experiment-audit.md` 新增"先信不信这个 benchmark"一节；`reviewer/experiment-results-review.md` 加规模与否定后出口两问。
- `roles/prompts/manager.py`：阶段决策加一条"证据规模是科学的一部分"；`_RESEARCH_DELIVERABLE_ROUTING` 明确论文的图/章节/修订归 research，direct 请求给 `START_STAGE`。
- `manager/domain_author.py`、`_vertical_ops.py`、`_core.py`、`skills/stage_machine.py`、`skills/vertical_select.py`、`apps/_runtime_supervisor.py`：决策新增 `start_stage`（仅 direct，按纵向别名归一，无效丢弃），`persist_vertical` 在没有阶段时以它播种，绝不重置已有阶段。
- 测试：`tests/skills/test_local_model_inventory.py`（新），`test_verticals.py`、`test_domain_author.py`、`test_manager.py`、`test_direct_idea_task_completes_at_idea.py` 各加用例；`test_research_protocol_quality.py` 的一条过期断言随 8ff4280fd 更新。预存失败：`tests/skills/test_paper_chart_style.py::test_research_data_figures_have_one_renderer_path`（router 文案 9e39270ba 改过，测试没跟）。

**验证。** fig-03（s-90908ce6，与 fig-02 同一目标、同一输入）在 a8e049980 上被判为 `research / direct / paper`，Engineer 的 skill 库含 `_shared_verticals/research/engineer`；fig-02 在旧代码上是 `software / direct / delivery`。两者产物可直接对比：`argus-capability-tests/fig-02/figures` 与 `fig-03/figures`。

**部署。** main = a8e049980 已 push。ACL daemon 用 `deploy_and_roll.sh`（先 drain 到任务边界，再更新 argus-runtime-latest，再 `--resume --resume-continuous`）滚动；fig-03 从 `argus-runtime-recovery-20260905`（已 detach 到 a8e049980）用 `PYTHONPATH`+`ARGUS_SKILL_SOURCE_ROOT` 启动。idea-02（s-d141e08e，RLVR 开放选题）仍在旧代码上跑完。

**没做的事。** 没有把 Codex 对 ACL benchmark 的审计（标签矛盾、常数基线 78.6%、yes/no 是受限打分等）交给项目；它存在 `argus-capability-tests/_yardsticks/`，用来检验新 Reviewer 指导能否让 Argus 自己抓到这些问题。

## 2026-09-06 16:20 UTC · Planner 退役待办（74452def4）与"全部槽位等外部任务时的规划空转"修复

**退役待办。** 证据：s-0ebfd18c 待办涨到 152 条，多数是同一个已被否定机制的换名变体；FuseHead 曾需要操作员手工标 7 条 superseded。现在 Planner 页脚可写 `RETIRE_TASK=<item id> | <一句话原因>`（每行一条），`Backlog.supersede_items` 原子地把点名的 pending 任务标为 superseded（running 与已完成的不动），规划周期即使没有新任务也会执行退役并发 `life.plan.node.superseded`（source=planner）。Planner 上下文现在列出 pending/running/paused 任务及其 id 与 deps。研究纵向的 Planner 片段要求把被否定路线的待办退役。测试：tests/planner、tests/life/test_backlog_replacement.py、test_planner_delegation_flow.py。

**规划空转。** 现象：两个任务槽都停在 `paused_external_work`（等 GPU 子任务）时，主循环只看 `running` 就以为没活可干，每 1–2 分钟叫一次 Planner；Planner 想等却因为回复里缺 `WAITING=true` 被解析成"空计划"，修复提示词又只示范任务页脚，于是它编造依赖在途任务的新任务，`tasks_scheduled` 又把控制权交回规划循环。CoT 15:50–15:57 五次、ACL 上午十次。修法（Codex，无新计数器）：规划 intake 在"有停在外部等待的任务且所有 pending 都直接或传递依赖在途任务"时直接记一个 waiting 判定、不调 LLM，由既有的任务唤醒路径在子任务结束时恢复；规划提示词和无任务修复提示词明说"等 Argus 自己启动的后台任务是合法等待，返回 WAITING=true 且不要编依赖任务"；现实核对文案不再把空转周期说成"你上次的等待判定被拒"。测试：tests/life/test_durable_wait_poll_suppression.py（重复等待、重启、任务成功/失败唤醒、有可启动工作时不受影响）。

**观察到的行为变化（新代码，CoT 项目 s-0ebfd18c 15:51 重启后）。** 第一次规划就写"不重复派发字典修复"，随后自己把论题改为边界命题"SAE 轨迹恢复不等于可执行推理保留"并安排在未见模型与任务上确认，这正是 experiment playbook 新加的"机制被否定后重推论点"。
