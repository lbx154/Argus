# Argus 交接(2026-09-05 收盘)

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
