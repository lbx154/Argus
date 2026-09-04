# Argus 论文叙事质量改造方案

状态：讨论稿，不改变当前运行逻辑。

## 1. 问题定义

Argus 已经能较强地约束证据真实性、比较完整性、引用、格式和复现，但这些要求比“论文是否好读”更具体、更容易被 Reviewer 检查。结果是：证据审计语言、完整方法枚举、控制实验说明和同一组关键数字会从内部工作流泄漏到摘要、引言、结果、图注和结论，使论文像验收报告。

本方案不以“减少证据”为目标。它区分两件事：

1. 科学内容是否必须被保留；
2. 内容应该在何处、以多大篇幅、以何种形式出现。

目标形态是完整的证据后端和有层次的读者前端。科学判断决定内容是否保留，叙事判断决定内容如何呈现。

## 2. 设计原则

### 2.1 不新增第一类角色

不在 Manager、Planner、Engineer、Reviewer 之外新增核心角色。`Narrative Editor` 是 Paper 阶段的一次 fresh-context Engineer mission，使用受限的编辑契约；Scientific Reviewer 仍是完整性裁决者。

### 2.2 不新增强制项目可见审计包

不创建新的 conformance JSON、问题清单或认证包。Experiment 到 Paper 继续只使用项目根目录的 `HANDOFF.md`；Review 继续只维护 `paper/REVIEW.md`。必要的计数和语义映射作为运行时 Reviewer 输入或测试输出，不成为论文项目的常驻负担。

### 2.3 呈现预算不等于证据预算

不得限制实际运行的方法、控制实验或保存的结果数量。预算只约束 reader-facing manuscript 中的信息密度、完整枚举次数和重复次数。

### 2.4 科学内容不可由叙事编辑器单方面删除

Narrative Editor 可以合并、压缩、后移、表格化和改写，也可以删除重复副本；不能删除唯一承载某项科学事实的内容，不能修改数字、比较方向、claim scope 或 uncertainty。若认为唯一内容应退出正文，只能提出候选移动/省略建议，交 Scientific Reviewer 裁决。

### 2.5 例外必须基于推理作用

硬预算允许例外，但理由必须是该额外内容改变结论、排除关键替代解释、限定必要 scope 或使结果可解释/可复现。`为了完整`、`审稿人可能会问`、`已经做过该实验`不能单独构成例外理由。

## 3. 建议工作流

```text
Experiment evidence
      |
      v
Paper HANDOFF: thesis + reader-change arc + decisive evidence
      |
      v
Author draft (full scientific access, presentation budget active)
      |
      v
Fresh-context Narrative Editor
  - merge / compress / move / rewrite
  - no unilateral scientific deletion
      |
      v
Scientific loss check by Reviewer
      |
      v
Cold-reader readability review on rendered PDF only
      |
      v
Author resolves any science/readability conflict
```

Narrative Editor 不接收 Reviewer 的逐句措辞和历史 review log，只接收：当前论文、Paper HANDOFF、必须保留的科学事实以及呈现预算。这样避免把 reviewer objection 原样改写进论文。

## 4. Experiment 到 Paper 的交接内容

保留现有 `# HANDOFF — EXPERIMENT` 标记，在进入 Paper 前要求以下自然语言小节：

```markdown
## Paper story
- Prior belief: 读者进入论文前通常会相信什么？
- Surprising observation: 哪个结果改变了这个预期？
- Thesis: 论文最终建立什么？
- Reader change: 读完后，读者对该问题的判断应发生什么变化？
- Reveal sequence: 最多四个依次推进理解的论证节点。

## Decisive evidence
- 结论成立不可缺少的结果、反例和控制。
- 每项说明它改变哪一步推理，而不是只列 artifact。

## Presentation risks
- 容易被重复的关键数字或方法清单。
- 容易泄漏到正文的审计、协议或 reviewer-response 语言。
```

这里不要求预先决定所有内容留在正文还是附录。Author 提出放置方案，Reviewer 根据科学作用复核。

## 5. Reader-facing 呈现预算

以下是初始默认值，用 run04 和目标 venue 的优秀论文校准后再稳定。表格中的“硬”表示违反时必须修复或获得 Reviewer 的逐项例外，不表示 Host 用正则自行判断论文质量。

| 位置 | 数字组 | 完整方法枚举 | 控制实验说明 |
|---|---:|---:|---:|
| Abstract | 最多 2 组 | 最多 1 次，且最多点名 3 个方法；更多用方法类别概括 | 最多点名 1 个改变核心解释的控制 |
| Introduction | 最多 3 组 | 最多 1 次 | 不列控制清单；最多解释 1 个关键控制 |
| 单个 Results prose 段落 | 最多 2 组 | 围绕一个比较问题 | 最多完整解释 1 个控制的逻辑作用 |
| Conclusion | 最多 1 组 | 0 次 | 0 次完整控制清单 |

定义：

- 一个“数字组”是服务同一命题的连续比较，例如 `0.925 -> 0.708` 算一组；不按字符中的数字个数机械计数。
- 表格单元、坐标刻度、参考文献年份和公式不计入 prose 数字预算；caption 中重新陈述 headline 数字仍计入重复检查。
- “完整方法枚举”指把实验矩阵中的方法逐个点名；按科学类别概括不算完整枚举。
- “控制实验说明”指解释一个控制为何排除某种替代解释；只写交叉引用不算新的完整说明。

全篇默认硬约束：

1. 同一组精确 headline 数字在 reader-facing prose 中最多出现两处；其余位置使用定性结论或交叉引用。
2. 同一完整方法列表最多出现一次；Methods/Setup 中的规范定义优先取得该位置。
3. 同一控制实验的完整逻辑说明最多出现一次；后续只能承担新的推理作用或简短引用。
4. 同一限制或 caveat 完整陈述一次；其他位置只有在 section role 不同且确实改变解释时才可保留。

这些是默认预算，不是不可推翻的科学定律。Reviewer 可以批准例外，并在 `paper/REVIEW.md` 中用一句话说明额外出现承担的不同推理作用。

## 6. Narrative Editor 契约

### 可以直接执行

- 合并表达同一命题的段落；
- 删除重复副本，同时保留唯一、最合适的承载位置；
- 将完整方法枚举改为类别概括；
- 将高密度 prose 改为清晰表格或将二级细节后移；
- 调整 section/paragraph 顺序，使每一步改变读者理解；
- 改写防御性 `not X but Y`、验收式和 reviewer-response 句型；
- 缩短 caption 中已由正文或表格承担的重复解释。

### 不得直接执行

- 删除唯一的 adverse result、关键控制、主要 uncertainty 或 scope boundary；
- 改动任何数字、统计定义、比较方向或实验条件；
- 扩大或缩小科学 claim；
- 因为内容“打断故事”而隐藏会改变读者判断的证据；
- 把未解决的科学问题改写成语气问题。

### 需要 Reviewer 裁决的建议

Narrative Editor 若认为唯一科学内容应从主文移到附录或完全省略，应在 handoff 中给出：

```text
content: <事实或段落>
proposal: move_to_appendix | omit_from_manuscript
reader_benefit: <减少什么认知负担>
science_risk: <可能损失哪一步推理>
reasoning_role: <当前是否改变 claim、scope、替代解释或复现>
```

这只是本轮 handoff，不生成新的持久化 JSON。

## 7. Scientific loss check

Reviewer 比较编辑前后的科学含义，而不是要求句子原样保留。至少检查：

1. 每个 headline claim 仍有直接、可定位的证据；
2. 会改变 headline interpretation 的损失、null、反例和 uncertainty 仍然可见；
3. 关键控制在读者需要排除替代解释之前或当时出现；
4. Methods/Appendix 仍提供理解和复现结果所需的信息；
5. 合并没有把不同 population、metric、protocol 或 claim 混为一谈；
6. Narrative Editor 没有改变数字、比较方向或 scope。

Reviewer 反对压缩时必须指出丢失的推理环节。`信息不完整`不是足够具体的否决理由。

## 8. 冷读可读性评审

冷读 Reviewer 只读取当前 rendered PDF，不读取内部结果目录、HANDOFF、历史 review 或证据审计。它独立评价：

- **中心性**：第一页后能否用一句话说出唯一核心发现；
- **推进性**：每个主要 section 是否改变读者理解，而非再次认证同一结论；
- **密度**：数字、缩写、专有名词和控制说明是否超过局部阅读容量；
- **重复性**：再次出现的事实是否承担新的推理作用；
- **时机**：关键证据、控制和限制是否在需要时出现；
- **视觉叙事**：每张主图是否回答一个清楚的问题，而不是充当 dashboard。

评审必须同时指出“内容太多”和“内容太少”。缩短不是默认正确答案：缺少必要解释、连接句、方法直觉或证据也应被判为可读性缺陷。

## 9. 科学完整性与可读性冲突

当 Narrative Editor 建议压缩，而 Scientific Reviewer 判断存在损失时，不直接回退到原文。Author 应生成一个第三方案：

- 保留该证据；
- 减少它的表述成本；
- 改变承载形式或出现位置；
- 避免在其他 section 再次完整解释。

只有在无法用更低成本保留推理作用时，才在“完整正文版本”和“后移版本”之间裁决。Scientific Reviewer 先排除造成结论损失的版本，冷读 Reviewer 再比较剩余版本的可读性。

## 10. 代码改动面

### 10.1 `argus_skill/verticals/research/stages.py`

- 扩展 Experiment handoff 要求，加入 reader-change arc 和 decisive-evidence role；
- 扩展 `paper.argument` / `paper.voice`，明确呈现预算和 fresh-context narrative pass；
- Review checklist 加入 semantic loss check 和 cold-reader PDF pass；
- 保持 `HANDOFF.md` 与 `paper/REVIEW.md` 为唯一正常持久化交接。

### 10.2 `argus_skill/verticals/research/prompt_policy.py`

- 为 Paper 阶段渲染 Author 与 Narrative Editor 两种 mission fragment；
- Narrative Editor fragment 明确权限和禁止项；
- 不向 Narrative Editor 注入历史 Reviewer 原话和完整审计上下文；
- Reviewer fragment 要求以“丢失的推理环节”说明 veto。

实现时优先使用 mission scope 或 skill selection 区分 Author/Narrative Editor，不增加新的 core role enum。

### 10.3 venue drafting skills

- 在 AAAI/EMNLP drafting skill 中加入默认呈现预算；
- 明确预算只作用于 reader-facing presentation；
- 删除鼓励把完整审计矩阵重新叙述进 prose 的残余指令；
- 保持 evidence honesty 和 adverse comparison visibility。

### 10.4 `academic_language_review.py`

- 保持 deterministic measurements 只作为 Reviewer 事实，不让 Host 判论文质量；
- 增加语义数字组、完整方法枚举、控制说明和重复命题的候选检测；
- 将固定 `170 words` / `five-sentence abstract` 倾向迁移为 venue-specific advisory，而非通用质量代理；
- Reviewer 同时判断 over-compression 和 overloading；
- 输出每个预算例外是否具有不同推理作用。

### 10.5 `draft_outline.py` 与 `argument_organization.py`

- 若这两个旧结构仍在实际 Paper 路径使用，升级 outline section role：`reader_before`、`question`、`reveal`、`reader_after`；
- 不以字段长度或文件存在作为叙事质量证明；
- 若当前简化路径已不使用它们，则弃用而不是重新引入为 mandatory artifact。

### 10.6 `reviewer_simulation.py`

- 从正常研究路径、公共导出和推荐 skill 中正式弃用；
- 删除“至少十个 hostile questions”及“每个问题必须写进正文/limitations”的测试契约；
- 历史兼容入口可以保留只读 warning，但不能再形成 Paper/Review gate。

## 11. 测试方案

### 11.1 单元测试

- 数字组识别：连续比较算一组，年份/引用/公式不误计；
- 方法枚举识别：完整列表与类别概括区分；
- 重复命题识别：同义改写能够形成 Reviewer candidate，而非由 Host 自动删除；
- Narrative Editor prompt 不包含删除唯一科学内容的权限；
- Reviewer prompt 要求指出具体 inference loss；
- venue-specific budget 能覆盖默认值；
- `reviewer_simulation` 不再参与 stage completion。

### 11.2 回归样例

使用 run04 作为第一份负向 fixture，记录但不把以下数值直接写死为通用规则：

- headline 数字在多个 section 的重复；
- 四方法完整列表的重复；
- random-control 逻辑的多次完整解释；
- `claim-bearing`、`evidence chain`、`powered validation` 等审计式表达；
- dashboard 式主图和 caption 重复。

至少加入两篇同领域、同 venue、贡献形态相近的优秀论文作为正向 calibration。不得仅靠 run04 训练出针对单篇文章的 lexical blacklist。

### 11.3 端到端验收

对同一份证据生成 baseline 与新流程版本，盲评：

1. Scientific Reviewer 判断核心 claim、adverse evidence、controls、uncertainty 和 scope 是否等价保留；
2. 冷读 Reviewer 在不知道版本来源的情况下判断哪一版更像完整研究论文而非实验报告；
3. 第一页单句 thesis 恢复率提高；
4. headline 数字、方法清单和控制逻辑的无功能重复下降；
5. 不增加 unsupported claim、遗漏 adverse result 或错误压缩不同实验条件。

只有科学等价先通过，才比较可读性。

## 12. 分阶段实现

### Phase 0：建立基线

- 冻结 run04 PDF/text 和两篇正向 exemplar；
- 记录数字组、方法枚举、控制说明和重复命题；
- 运行盲冷读，形成改造前基线。

### Phase 1：低风险 prompt 与交接改造

- 修改 Experiment -> Paper HANDOFF；
- 增加 presentation budget；
- 为 Narrative Editor 增加受限 mission fragment；
- 弃用 reviewer-simulation gate。

### Phase 2：loss check 与冷读闭环

- 加入 Reviewer semantic loss checklist；
- 在 rendered PDF 上运行 fresh-context cold read；
- 实现 conflict resolution，不以恢复原文作为唯一解。

### Phase 3：语义诊断与校准

- 增加数字组、枚举和重复命题的候选检测；
- 使用多篇 venue exemplar 校准默认预算；
- 根据误报决定哪些规则保持硬预算、哪些降为 advisory。

## 13. 暂不做的事

- 不更换基础模型；
- 不通过降低 reasoning effort 假定性地改善文风；
- 不新增 Narrative Editor core role；
- 不让正则表达式直接裁决论文质量；
- 不让任何自动编辑器删除唯一科学内容；
- 不把优秀论文的句子或固定结构复制为模板；
- 不以“更短”作为可读性的代理指标。

## 14. 待确认决策

1. 初始预算是否采用本方案数值，还是先只对 run04 shadow-evaluate 后再阻断；
2. headline 数字两次上限是否包含 caption；本方案建议包含；
3. Methods 中完整方法列表是否豁免全篇一次上限；本方案建议它取得唯一完整枚举位置，表格可重复标签但 prose 不重复列表；
4. budget exception 由最终 Scientific Reviewer 单独决定，还是要求 Author 给出方案后 Reviewer 批准；本方案建议后者；
5. `draft_outline.py` / `argument_organization.py` 是升级还是正式退出当前简化路径，应在实现前先确认实际调用图。

