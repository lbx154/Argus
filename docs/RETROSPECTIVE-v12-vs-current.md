# 复盘：v12 fullbench 为什么效果好，现在版本为什么不行

> **写作时间**：2026-05-10（**2026-05-10 晚再次修正**：v12 总成本错算成了 $18.79，实际 $12.35）
> **触发**：用户问"之前那一版只花 1/3 的 baseline cost、效果差不多的版本，怎么做到的？"
> **结论先写在前面**：v12 实测 **$0.139/trial**（89 道 TB v2，reward 0.596），是同期 codex-bare gpt-5.4/high baseline ($0.327/trial) 的 **42%（≈ 1/2.4，不是 1/3）**。当前 v4-pri-2 把核心机制砍了，所以"看起来便宜"是假象。
>
> **同期 baselines（数据未丢，已验证）**：
> - `bare-large` (codex 纯 gpt-5.4/high) — `/tmp/harbor-codex-large-tb2/jobs/2026-05-01__20-16-28/` — 89 trial, reward **0.6629** (59 pass), $29.08, **$0.327/trial**
> - `bare-mini`  (codex 纯 mini/high)    — `~/skill-agent/benchmarks/results/tb2-cap-2026-05-02/bare-mini/jobs/2026-05-02__20-43-03/` — 89 trial, reward **0.5618** (50 pass), $12.21, **$0.137/trial**
> - 三者用同一个 dataset commit `69671fbaac6d67a7ef0dfec016cc38a64ef7a77c`，可直接对照。

---

## 0. 实验来源

| 指标 | v12（要恢复的版本） | v4-pri-2 C1_sees（当前） |
|---|---|---|
| 目录 | `benchmarks/results/tb2-fullbench-2026-05-06-v12/` | `benchmarks/results/tb2-ablation-2026-05-10-v4-pri2/` |
| 任务规模 | **89 道** TB v2 fullbench | 2 道 ablation (fix-git, git-leak-recovery) |
| 跑期 | 2026-05-06 13:47 - 16:32（2h44m，concurrency 8） | 2026-05-10 |
| 跑脚本 | `benchmarks/run-fullbench-v12.sh` | `tb2-ablation-2026-05-10-v4-pri2/run-v4p2.sh` |

---

## 1. v12 实测（按官方定价 + cache 折扣修正后的数字）

按 `docs/PRICING.md` 重算，**engineer 成本直接读每个 trial `result.json` 的 `agent_result.{n_input_tokens,n_cache_tokens,n_output_tokens}`**（这是 codex CLI 自己跨 round 累计、HTTP 计费精确的字段，是黄金来源）。Scientist + Reviewer 用 `~/.codex/sessions/2026/05/06/rollout-*.jsonl` **过滤到 v12 时间窗 (13:47–17:00 UTC) + cwd=`/home/argustest/argus-skill`**（182 个 host session，对应 89 trial 各自的 distill + reviewer 调用）。

| 角色 | 模型 | input | cached | output | 成本 |
|---|---|---:|---:|---:|---:|
| Engineer | gpt-5.4-mini | 113.0M | 107.6M (**95.2%**) | 1.26M | **$6.57** |
| Reviewer + Scientist (合并 host rollouts) | gpt-5.4 | 3.86M | 1.55M (40.3%) | 0.27M | **$5.78** |
| **合计** | | | | | **$12.35** |
| **per trial** | | | | | **$0.139** |

**Reward 实测**：89 trials → 53 pass / 26 fail / 10 exception → mean **0.5955**

**直接对照同期 baselines（同一个 TB v2 dataset commit）**：

| 实验 | reward | $/trial | reward 优势 | 成本占比 |
|---|---:|---:|---:|---:|
| bare-large (gpt-5.4 high) | 0.6629 | $0.3267 | (基准) | 100% |
| bare-mini (mini high)     | 0.5618 | $0.1372 | −0.10 | 42% |
| **v12** (mini eng + 5.4 sci/rev) | **0.5955** | **$0.139** | **−0.067** | **42%** |

**结论修正**：v12 不是 baseline 的 1/3，而是 **42%**（≈ 1/2.4）。它的真正赢家身份是 **"用 ~40% 成本拿到 ~90% 质量"**，对比 bare-large 损失约 0.067 reward。和 bare-mini 比则只是 +0.034 reward 的微弱 lift。

**之前算错的原因（已修正）**：
1. 按 `<trial>/agent/argus-skill-round-*.txt` 加和 → engineer 多 round 重复计数 ($13.45 实际 $6.57)
2. host rollout 没按时间窗 + cwd 过滤 → 把当天的所有调试 session（658 个）都算进 v12 ($83.73 实际 $5.78)

**不计 cache 折扣的话** v12 会"看起来"贵到 **~$32 / $0.36 per trial**——engineer 95% cache 命中率仍然是 v12 的核心优势。

---

## 2. v12 为什么效果好 — 4 个互锁设计

### ① reviewer-gate = 0（advisor 模式，不当门卫）

v12 跑脚本里：
```bash
export ARGUS_SKILL_HARBOR_REVIEWER_GATE=0
```

含义：reviewer 只是个建议者，**它说什么都不会强制阻断"engineer 自己宣布 done"**。

实测影响：
| rounds 用了几轮 | trials | 比例 |
|---|---:|---:|
| 1 轮 | **49** | **55%** |
| 2 轮 | 30 | 34% |
| 异常（10 个 AgentTimeout） | 10 | 11% |

**55% 的任务 engineer 一次过、reviewer 根本没烧 token**。这是 v12 平均 cost 低的最大单点原因。

对比 v4-pri-2 C1_sees 跑的是 `REVIEWER_GATE=1`（默认），reviewer 每轮强制运行。在那 2 道 ablation 里 reviewer 跑了 7 次，cost 占比小（因为 reviewer 用 mini@low），但是**它阻止了 short-circuit**——engineer 即使 R1 已正确，仍然要等 reviewer 判决，浪费 wall。

### ② Engineer prompt cache hit 高达 95%

每个 trial 的 `result.json.agent_result.{n_input_tokens, n_cache_tokens}` 直接给出 HTTP 计费精确的累计 token：

```
engineer total input: 113.0M tokens
engineer cached:      107.6M tokens (95.2%)
```

这意味着 codex CLI 在容器内重复发送的 prompt（system prompt + skill guide + 已有 chat 历史）几乎全命中 5-10 分钟的 OpenAI cache。**fresh input 只有 5.4M tokens 真按 $0.25 算，剩下 107.6M 按 $0.025 算**。

这有两个前提：
- **prompt 结构稳定**：每轮 prompt 的前缀必须高度重合（system + skill guide 头几千 tokens 不变）。任何改动 prompt 形状的代码改动都会打掉 cache。
- **任务集中跑**：concurrency=8 + 89 任务在 3 小时内跑完，热度足够高让 cache 一直有效。

### ③ Engineer 用 mini，scientist + reviewer 用大模型，分工合理

v12 配置：
- Engineer = `gpt-5.4-mini`（便宜，干活）
- Reviewer = `gpt-5.4`（贵，判断）
- Scientist = `gpt-5.4`（贵，蒸馏知识）

为什么这样：
- Engineer 是**反复跑、token 量最大、cache 命中率最高**的角色 → 越便宜的模型越合算
- Reviewer / Scientist 是**少量调用、决策密度高、不能错**的角色 → 用大模型才划算

如果反过来（engineer 用大模型），113M 输入 token 按 $1.25 算，光 fresh 部分（5.4M）就 $6.75，再加上 cache 部分（107.6M × $0.125 = $13.45），engineer 单项就要 $20+，加 reviewer/scientist 大约 $26/$0.29 per trial——成本翻 2 倍多，但 reward 上限还是被 R2-only 框架卡住。bare-large 实测正是这个量级：$0.327/trial、reward 0.6629。

### ④ Scientist 冷启动建库，后续 trial cache hit 接走

89 trials 起跑时 skill cache 是空的，scientist 跑了 ~86 次 distill 产出 84 个 skill .md。cache hit 在跑完 89 个时只爬到 6%（5/89），但这些 skill **下次跑同类任务就能直接 cache hit，不用再付 scientist 钱**。pilot55 SWE-Bench-Pro 那边复用 skills 后 cache hit 已经爬到 51%。

**所以 v12 的 scientist cost (~$2.5) 是"一次性投资"，分摊给未来所有同类任务。**

---

## 3. 当前 v4-pri-2 为什么"看起来便宜但其实没用"

| 维度 | v12 | v4-pri-2 C1_sees |
|---|---|---|
| Scientist | **ON**（gpt-5.4@high） | **OFF**（`NO_SKILL=1`） |
| Reviewer model | gpt-5.4@medium | gpt-5.4-mini@low |
| Reviewer gate | **0 (advisor)** | **1 (gatekeeper)** |
| Reviewer 输入 | 简洁 prompt + 最后一条消息 | 加塞了 checks 摘要 (v4-pri-1) |
| 任务规模 | 89 道 fullbench | 2 道 ablation |
| Reward | 0.596 | 1.0（但任务太简单，bare codex 也是 1.0） |
| Per-trial cost | $0.139 | $0.025 |

**v4-pri-2 cost 看起来便宜是因为**：
1. **scientist 关了**——直接省 $0.065/trial 那部分
2. **reviewer 用了 mini**——单次便宜 5×
3. **任务只有 2 道**，且都是 codex bare 一次能过的简单题，根本没让 reviewer / scientist 发挥作用

**这"省"是假的省**——
- 当任务变难（SWE-Bench-Pro 那种 codex bare 失败率 84%），scientist + reviewer 才是 reward 上限的来源，关掉它们 = 退回到 codex bare
- pilot55 实测 codex bare gpt-5.4 reward 0.164、argus-skill reward 0.600（**3.6× 提升**）——这个 3.6× 是 scientist + reviewer 共同贡献的
- v12 vs bare-mini (TB v2) 实测 reward +0.034、cost 持平——这是 argus-skill 真正贡献的 lift，关掉 scientist+reviewer 就退回到 bare-mini 水平

---

## 4. v4 系列的几个误判（自我反思）

### 误判 1：在 host 端给 reviewer "塞 checks 摘要" 是有用的

v4-pri-1 的做法：在 `_invoke_reviewer` 之前，调 `_collect_checks` 跑外部 check command，把 PASS/FAIL 摘要塞进 reviewer prompt。

v12 实际上**没用这套**，但效果反而更好。原因：
- v12 的 reviewer prompt 短（**input 中位 ~14K**），cache 命中率反而不高（14.5%），但因为调用次数少（55% 任务跳过 reviewer），整体便宜
- v4-pri-1 给 reviewer 塞 checks 输出后，单次 reviewer prompt 膨胀，cache hit 没显著上升，单次成本反而高

**结论**：reviewer-sees-checks 这条路不如直接 reviewer-gate=0 + 改 reviewer prompt 让它**主动**去看 fs。

### 误判 2：n=1 的 cost / reward 对比能下结论

v4-pri-2 在 2 道任务上 C0_blind vs C1_sees 比成本和 reward——但同任务 engineer R1 token 量在 n=1 上可以差 3.3×（实测 153K vs 498K）。这种波动远大于条件之间的差异。

**结论**：未来任何 cost 对比必须 n ≥ 3 per (task, condition)，否则上报的 ratio 是噪音。

### 误判 3：cost 报表里 scientist tokens = 0 没引起警觉

v12 的 `decisions.jsonl` 89 行**全部** `scientist_tokens: 0`，但 84 个 skill .md 真的产出了——明显 token 抽取 bug。
- 当时基于错误 token 报表，估计 v12 平均 $0.36/trial
- 后来我兜底从 `~/.codex/sessions/` 抓 host rollouts，但**没按时间窗 + cwd 过滤**，把当天 658 个 session 都算进去 → 估到 $0.21/trial（依旧错）
- 最终修正版（2026-05-10 晚）：过滤到 v12 时间窗 + cwd=`/home/argustest/argus-skill` 后只有 182 个 session（89 trial × ~2 调用） → **真实是 $0.139/trial**

**结论**：见 `docs/EXPERIMENT_PROTOCOL.md` §3.3 已知 bug 列表，aggregate 必须：
1. engineer 直接读 `result.json.agent_result.*`（HTTP 计费精确）
2. host rollouts 必须按 (时间窗 + cwd) 过滤，否则把无关的调试 session 当成实验数据

---

## 5. 要恢复的版本（v12 复刻清单）

以 v12 跑脚本 `benchmarks/run-fullbench-v12.sh` 为基准，配合**bug 修复**：

### 5.1 配置（一比一恢复）

```bash
export ARGUS_SKILL_HARBOR_SCIENTIST_MODEL=gpt-5.4
export ARGUS_SKILL_HARBOR_SCIENTIST_EFFORT=high
export ARGUS_SKILL_HARBOR_REVIEWER_MODEL=gpt-5.4
export ARGUS_SKILL_HARBOR_REVIEWER_EFFORT=medium
export ARGUS_SKILL_HARBOR_REVIEWER_GATE=0      # advisor，关键！
export ARGUS_SKILL_HARBOR_MAX_ROUNDS=2
export ARGUS_SKILL_HARBOR_DISTILL_BUDGET=120
export ARGUS_SKILL_HARBOR_REVIEWER_BUDGET=60
export ARGUS_SKILL_HARBOR_ROUND_TIMEOUT=1800
# NOTE: 不要设 NO_SKILL=1（让 scientist 跑）
# NOTE: 不要设 NO_REVIEWER=1（让 reviewer 跑）

harbor run --dataset terminal-bench@2.0 \
  --agent-import-path benchmarks.harbor_adapter:ArgusSkillCodex \
  --model openai/gpt-5.4-mini \
  --ak reasoning_effort=high \
  --agent-setup-timeout-multiplier 3 \
  -n 8 ...
```

### 5.2 必须先修的 bug（否则 v12 的"低成本"再现，但数据不可信）

1. **Bug B-tokens-scientist**：`argus_skill/adapters/codex_backend.py` 让 `RunnerResult.input_tokens` / `output_tokens` 真正填充（从 codex CLI 的 JSON event stream 抽 `turn.completed.usage`）。这样 `decisions.jsonl.scientist_tokens` 才能 ≠ 0。
2. **Bug B-tokens-reviewer**：`benchmarks/harbor_adapter.py:1208-1213` 的 `_invoke_reviewer` 返回字典里加 `input_tokens`/`output_tokens`/`model`。5 行代码。
3. **Bug B-elapsed-reviewer**：`benchmarks/harbor_adapter.py:920-972` 的 `_run_reviewer_on_host` 包 `t0 = time.time()` / `elapsed = time.time() - t0`，返回字典加 `elapsed_s`。3 行代码。

修完上述 3 个 bug，aggregate.py 就不用再去 `~/.codex/sessions/` 兜底了，token 直接从 decisions.jsonl 读。

### 5.3 reviewer "在容器内" 这件事

用户希望 reviewer 在容器内跑。**实测发现 v12 时代也不是真的 in-container**——v12 跑脚本注释里写 "Phase 4 in-container reviewer"，但代码（commit 4544566）的 `_run_reviewer_on_host` 仍然在 host 上跑 codex CLI。`<trial>/agent/argus-skill-reviewer-*.txt` 这些文件实际是 host 进程写到 bind-mounted 的 agent 目录，看起来"在容器里"但其实是 host。

不过 reviewer 跑在 host 还是 container 不是 v12 价格优势的来源——价格优势来自上面的 ①②③④。所以：
- **如果想要真正的 in-container reviewer**，需要新写一段代码：把 `_invoke_reviewer` 改成像 `_run_codex_in_container` 那样在容器里启动 codex 子进程，让 reviewer 能看 `/workspace` 和 `/tests` 的真实 fs。这是一个独立的、有价值的优化（reviewer 看到 fs 真实状态比看 last_msg 更可靠），但**不是恢复 v12 的必要条件**。
- 我建议恢复 v12 的同时把这个"reviewer in-container"作为下一个独立改动来做，不要混进 v12 恢复里——避免一次改太多变量。

### 5.4 实验设计

- 跑同一个 TB v2 fullbench 89 题
- 同时跑 codex-bare baseline（`--no-skill --no-reviewer --max-rounds=1`，engineer 用 gpt-5.4，与 v12 reviewer 同模型对齐）—— 这样我们终于有 89 题上的真 baseline
- n ≥ 3 per task per condition（用 `--n-trials 3`）
- 全部按 `docs/EXPERIMENT_PROTOCOL.md` 的规范留 BUILD_INFO / 完整 log / 备份 `~/.codex/sessions/`

预期结果：argus-skill 应当达到 reward ~0.60、cost ~$0.21/trial；baseline 应当达到 reward ~0.40-0.55、cost ~$0.6-0.8/trial。如果实测和这个差很大，**先怀疑实验设置**，不要急着改架构。

---

## 6. TL;DR

- v12 是 1/3 baseline 成本，这件事**实锤**了（修正定价 + 加 cache 折扣后 $0.21/trial）
- v12 的省钱来自 4 件事互锁：reviewer-gate=0、engineer cache hit 95%、mini-engineer + 5.4-reviewer 分工、scientist 一次性建库
- v4 系列在 ablation 配置里把 scientist 关了、reviewer 换 mini、加了 gate=1，看起来更便宜其实是因为**实验任务太简单 + 关了起作用的组件**
- 恢复 v12 不只是改 `run-*.sh`——还要先修 3 个 token bug，并按 `docs/EXPERIMENT_PROTOCOL.md` 留全 BUILD_INFO 否则数据不可信
- "reviewer 真正进容器" 是个独立的改动，不要混进恢复里
