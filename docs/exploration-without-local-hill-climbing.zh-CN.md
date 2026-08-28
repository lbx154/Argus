# 避免局部爬山：把探索与结果认证分开

Argus 不能把工程纪律误解成思想保守。本文件记录一次真实失败模式：问题如何出现、第一次修复为什么仍然不对，以及 `kernel_engineering` vertical 最终采用的策略。

英文版：[exploration-without-local-hill-climbing.md](exploration-without-local-hill-climbing.md)

## 观察到的问题

在一次 8 张 AMD MI300X 上持续优化满血 BF16 GLM-5.2 serving 的任务中，Argus 初期进行了大量在线调研，检查了官方模型、vLLM、SGLang、ROCm、AITER、公开 benchmark、论文、issue 和 PR。

执行日志显示，早期研究窗口内共有 1,851 次与 GLM/ROCm 相关的 Web 与 GitHub 工具调用。满血 BF16 baseline 建立后，只剩两次相关 upstream source 读取；H2D 被确认成主要瓶颈后，相关联网调用为 0。

但当项目确认主要瓶颈是 routed expert 的 H2D 传输后，好奇心消失了。后续工作集中在相邻机制：

- packed 与 multi-stream copy；
- routed expert cache；
- explicit index map；
- pointer table；
- mapped-host fallback；
- recurrent prefetch；
- device-side controller 与 async-SDMA。

这些实验本身并非错误，但搜索空间变成了局部邻域。Argus 在知道 H2D 瓶颈后没有继续检查外部 frontier，因此错过了与当前问题高度相关的 FluxMoE/PagedTensor、FineMoE、MoE-Infinity、Fiddler 和 HIP virtual-memory remapping。

问题不在于没有工具，而在于策略。

## 为什么策略会制造局部爬山器

若干单独看似合理的规则叠加后，形成了错误目标：

1. **研究被视为一次性阶段。** 即使 precision 目标和瓶颈已经变化，旧 grounding 仍被视为完成。
2. **实现比探索更容易获得奖励。** 代码、benchmark，甚至一次具体 build failure 都算进展；纯研究报告却容易被认为“没有执行”。
3. **防磨洋工规则矫枉过正。** `smallest relevant surface`、`cheapest falsification check`、每轮必须测量等要求压制了高上限调查。
4. **联网能力受狭窄路由影响。** 显式 discovery 工作有 live search，但含研究内容的工程任务可能没有。
5. **最终认证要求泄漏到探索阶段。** 多 seed、多重复、可复现、置信区间和 safe fallback 在机制尚未证明值得投入前就成为默认门槛。
6. **缓存 Skill 保留旧偏好。** 只改顶层 Prompt 不够，项目或 shared Skill 仍可能写着 `smallest unimplemented mechanism`、`smallest fail-closed repair`。

在这种奖励下，最理性的行为就是不断修改最近的已知设计。Argus 实际优化的是“产出一个容易被接受的小增量”的概率，而不是“发现最佳机制”的概率。

## 第一次修复为什么仍然不对

最初提出的修法是：连续失败或约束变化后强制触发 search reset，并要求研究最终产出 executable gate 或 patch。

它比永不重开研究更好，但仍然把好奇心变成了需要许可的例外：

- 必须先失败足够多次，才允许搜索；
- 只有报告、没有实现，仍然容易被判为不完整；
- 立即可验证的想法仍然拥有结构性优势。

操作者否定了这个设计。研究不应等待失败；高质量报告本身可以是完整交付。

## 最终策略：探索与声明分层

最终策略把行为分成三种姿态：

| 姿态 | 合法产出 | 默认实验成本 | 风险偏好 |
| --- | --- | --- | --- |
| 探索 | 有来源的报告、机制组合、假设、开放问题、可选 prototype | 可以不运行 | 广泛、激进、高上限 |
| 筛选 | 一次干净运行或一次 inconclusive attempt | 单次运行，不做多 seed campaign | 最大化信息增益 |
| 声明或保留 | 正确实现与可比目标硬件证据 | 只按声明需要做重复和方差 | 严格证据 |

核心规则是：

> 探索可以是推测性的、未实现的、未验证的、尚不可复现的；性能声明不可以。

### Planner

Kernel Planner 现在：

- 不等待失败，主动读取当前 primary sources；
- 可以安排只交报告的 source analysis；
- 维护多个真正不同的机制方向；
- 长 benchmark 期间可以并行研究独立机制家族；
- 按预期上限和信息增益，而不是低执行风险排序；
- 不用探索槽位反复跑 seed、control 或未变化 benchmark；
- 不偏好 smallest patch 或最容易立即验证的方案。

### Engineer

Kernel Engineer 现在：

- 可以沿论文、runtime、kernel、memory system 和相邻 stack 的意外线索深入；
- 研究任务可以只交报告；
- 可以把激进、未实现、尚不可复现的想法明确标为 hypothesis；
- 普通探索默认一次干净运行即可；
- 只有候选准备被声明或保留时，才做更多重复与更完整正确性工作。

### Reviewer

Kernel Reviewer 现在：

- 按来源质量、事实准确性、综合能力、覆盖面和决策价值审查研究报告；
- 不要求探索任务必须有代码、gate、多 seed 或立即可复现；
- 不会仅因高风险和不确定性否定想法；
- 仍会拒绝把无证据假设写成确定结果；
- 仍要求性能声明在保留前通过严格正确性与测量。

## 运行时与 Skill 修改

策略落在：

- `argus_skill/verticals/kernel_engineering/stages.py`
- `argus_skill/verticals/kernel_engineering/skills/engineer/kernel-environment-first-engineering.md`
- `argus_skill/verticals/kernel_engineering/skills/reviewer/kernel-engineering-review.md`
- `argus_skill/verticals/kernel_engineering/references/frontier-search-protocol.md`
- `argus_skill/verticals/kernel_engineering/references/idgl-loop.md`
- `argus_skill/verticals/kernel_engineering/skills/engineer/kernel-benchmark-measurement-integrity.md`

所有 kernel mission 都可使用 live search。测试固定了角色契约、联网能力、纯报告研究、高上限偏好、单次筛选，以及探索与认证的分离。

公开实现历史：

- `dd073f0d`：启用主动 kernel 调研；
- `c784f958`：强化广泛 kernel 探索；
- `173a2af0`：优先高上限 kernel 探索。

私有镜像同步了等价修改。

## 仍然不可放宽的边界

放开探索不等于允许不诚实声明。Argus 仍必须拒绝：

- 伪造执行或测量；
- benchmark 没有进入修改代码却声称加速；
- 损坏、重标或不可追溯的证据；
- 通过放宽正确性阈值制造“优化”；
- 把 hypothesis 描述成测量事实。

好奇心决定 Argus 愿意调查什么；证据决定 Argus 可以声称什么。
