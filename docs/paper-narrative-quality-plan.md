# Argus 论文“实验报告味”治理方案

状态：已在未提交工作树实现；新 semantic-loss / cold-read 信号默认只做 shadow calibration，不改变既有阻断标准。

## 1. 问题定义

本方案不以“原论文不够好”为前提，也不把更短、更少数字或更弱的完整性要求当作改进。Argus 已经能较强地约束证据真实性、比较完整性、引用、格式和复现；要解决的是这些内部要求进入 reader-facing manuscript 后的表达失衡：所有实验像同等重要，所有数字像都应在每个位置重报，所有控制像审计项目逐项验收，论文因而带有实验报告或认证报告的语气。

问题不是证据太多，而是缺少三步转换：

1. 从全部证据中识别真正承担中心论证的证据；
2. 为其余证据分配机制解释、排除替代解释、限定 scope、复现或完整性角色；
3. 根据 Abstract、Introduction、Results、caption、Conclusion 的不同职责重新包装同一事实。

目标形态是完整的证据后端、完整的论文覆盖和有主次的读者前端。关键数字、五句摘要、170 字下限、numerical caption、完整方法与控制覆盖可以保留；需要改变的是“选什么进入当前叙事位置”和“如何解释它为什么在这里出现”。

## 2. 设计原则

### 2.1 不新增第一类角色

不在 Manager、Planner、Engineer、Reviewer 之外新增核心角色。`Narrative Editor` 是 Paper 阶段的一次 fresh-context Engineer mission，使用受限的编辑契约；Scientific Reviewer 仍是完整性裁决者。

### 2.2 不新增强制项目可见审计包

不创建新的 conformance JSON、问题清单或认证包。Experiment 到 Paper 继续只使用项目根目录的 `HANDOFF.md`；Review 的正常权威输出继续是 `paper/REVIEW.md`。必要的计数、语义映射和编辑前后快照属于内部 mission state 或测试输出，不成为论文项目的常驻负担。

### 2.3 保留现有写作硬要求

本次治理不通过取消下列要求来降低“报告味”：

- 摘要仍按既有 drafting contract 写成五句，并满足 170 字下限；
- Abstract、Introduction、Results、caption 和 Conclusion 仍可出现精确 headline 数字；
- figure/table caption 仍需给出 numerical takeaway；
- 方法、baseline、控制、adverse result、uncertainty 和 scope boundary 仍需被论文完整覆盖；
- 科学比较矩阵不能因为叙事包装而从论文中消失。

硬要求负责确保论文有料；证据选择和叙事包装负责避免这些内容以清单、流水账或审计报告的形式出现。

### 2.4 筛选不等于删除

“不进入当前 prose 段落”不等于“不进入论文”，更不等于“不保留证据”。一项内容可以由 Methods、表格、caption、Appendix 或直接交叉引用承担。会改变 headline claim、机制解释、主要替代解释、uncertainty 或适用范围的事实必须在读者需要它的位置进入 prose。

### 2.5 重复必须重新承担 section role

不设置“同一 headline 数字全篇最多出现两次”一类通用上限。中心数字可以在多个关键位置重复，caption 中的重复也可以是必要的。要求是每次出现都完成所在 section 的职责，而不是把同一个方法 × 数据集 × 指标矩阵原样复制到全文各处。

### 2.6 科学内容不可由叙事编辑器单方面删除

Narrative Editor 可以选择前景证据、合并重复解释、调整位置、表格化和改写；不能删除唯一承载某项科学事实的内容，不能修改数字、比较方向、claim scope 或 uncertainty。若认为唯一内容应退出主文，只能提出候选移动或省略建议，交 Scientific Reviewer 裁决。

## 3. 建议工作流

```text
Experiment evidence
      |
      v
Paper HANDOFF
  - thesis + reader-change arc
  - complete decisive evidence
  - evidence roles + proposed placement
      |
      v
Author draft
  - existing five-sentence / 170-word / numerical-caption rules retained
  - selected evidence packaged for each section role
      |
      v
Fresh-context Narrative Editor
  - select foreground evidence
  - package numbers, controls, matrices, and captions
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

Narrative Editor 不接收 Reviewer 的逐句措辞和历史 review log，只接收当前论文、Paper HANDOFF、必须覆盖的科学事实、已有 drafting contract 和位置职责。它的目标不是减少内容，而是把证据从“逐项汇报”转换为“围绕结论推进”。

## 4. Experiment 到 Paper 的交接内容

保留现有 `# HANDOFF — EXPERIMENT` 标记，在进入 Paper 前要求以下自然语言小节：

```markdown
## Paper story
- Prior belief: 读者进入论文前通常会相信什么？
- Surprising observation: 哪个结果改变了这个预期？
- Thesis: 论文最终建立什么？
- Reader change: 读完后，读者对该问题的判断应发生什么变化？
- Reveal sequence: 最多四个依次推进理解的论证节点。

## Evidence roles
- Headline evidence: 可以跨 Abstract、Introduction、Results、caption、Conclusion 重复的中心结果。
- Mechanism evidence: 解释结果为何发生的实验或分析。
- Disambiguating controls: 排除最可信替代解释的控制。
- Scope-changing evidence: adverse、null、uncertainty 或 boundary。
- Completeness evidence: 保证比较完整、复现和审计可信的其余矩阵内容。

每项至少说明：
- reasoning_role: 它改变哪一步推理；
- canonical_location: 最完整的承载位置；
- repeat_locations: 哪些位置需要再次出现以及各自作用；
- packaging: prose | table | caption | methods | appendix | cross-reference。

## Presentation risks
- 容易被无差别铺开的次要数字、方法和数据切片；
- 容易被写成 sanity-check 清单的控制实验；
- 容易泄漏到正文的审计、协议或 reviewer-response 语言；
- 容易只报数、不解释数字改变了什么判断的 caption 或 Results 段落。
```

HANDOFF 不是预先删减实验的工具。它先保证完整清点，再明确哪些证据是前景、哪些由表格或方法部分承担，避免 Author 在写作时把所有 artifact 当成同等重要的 prose 素材。

## 5. 证据选择与叙事包装

### 5.1 证据分层

| 层级 | 进入 prose 的条件 | 主要承载位置 |
|---|---|---|
| Headline | 直接建立中心 thesis | Abstract、Introduction、Results、主图 caption、Conclusion |
| Mechanism | 解释为什么有效或为何失败 | Results、Analysis、机制图/表 |
| Disambiguating control | 排除一个可信且会改变结论的替代解释 | 首次需要完成该推理的 Results/Analysis 位置 |
| Scope-changing | 改变 claim、uncertainty、population 或适用边界 | Results、Discussion/Limitations、必要时 Abstract |
| Completeness | 保证比较全面、方法可复现或审计可信，但不改变当前推理 | Methods、完整表格、Appendix、交叉引用 |

所有层级都属于论文证据。分层只决定叙事焦点和承载形式，不决定实验是否“值得保留”。

### 5.2 各位置仍保留数字，但承担不同作用

| 位置 | 精确数字的职责 | 包装重点 |
|---|---|---|
| Abstract | 用精选 headline 结果证明贡献成立 | 问题、洞察、结果和意义形成五句完整弧线 |
| Introduction | 让读者提前看到预期被怎样改变 | 数字服务动机和贡献，不展开完整结果矩阵 |
| Results | 给出完整条件、比较、uncertainty 和必要控制 | 从比较推出 inference，而不是逐行念表 |
| Caption | 让图表脱离正文仍能读懂 | 回答图表问题，给 numerical takeaway 并解释其含义 |
| Conclusion | 用中心结果支撑最终 takeaway | 解释数字改变了什么认识，不重新汇报全部实验 |

同一 headline 数字可以在这些位置重复。Reviewer 检查的不是机械次数，而是 Abstract 的“贡献证据”、Introduction 的“预期变化”、Results 的“完整判断”、caption 的“独立可读”和 Conclusion 的“科学含义”是否彼此不同。

### 5.3 五句摘要与 170 字的包装方式

继续要求五句和 170 字下限，但五句不是五项验收清单，建议分别承担：

1. 问题及读者原有认识；
2. 现有工作缺少的关键能力或解释；
3. 本文的核心洞察与方法；
4. 精选 headline 数字及最能确立解释的控制；
5. 该结果带来的科学含义或适用边界。

170 字用于容纳方法直觉、比较条件和结果意义之间的连接，不用于继续堆入次要 baseline、所有数据切片或完整审计矩阵。

### 5.4 Caption 的包装方式

figure/table caption 继续必须有 numerical takeaway。优先采用：

```text
这张图或表回答什么问题
→ 比较对象与必要条件
→ 最关键的精确数字
→ 该数字支持什么判断或排除什么解释
```

caption 可以重述 headline 数字，因为它需要独立可读；但不应把每个柱、每行方法、每项检查按 dashboard 顺序全部复述。完整矩阵由图表本身承担，caption 选择读者应先看到的结构和结论。

### 5.5 方法、控制和审计矩阵的包装方式

- Methods/Setup 保留方法和实验设置的规范定义及完整方法枚举；
- table 保留完整方法 × 数据集 × 指标矩阵，方法标签可重复；
- Results prose 选择能改变当前判断的行、列、差值和控制，并解释为什么；
- Appendix 保留次要切片、routine sanity checks 和完整补充矩阵；
- 主文必须保留会削弱 headline claim 的 adverse/null result、关键控制、主要 uncertainty 和 scope boundary；
- run ID、文件路径、validator 状态和内部 gate 名称继续留在证据后端，不以审计口吻进入 reader-facing prose。

“完整覆盖”不要求把表格每个单元再逐句念一遍；“叙事选择”也不允许把不利证据藏到读者无法发现的位置。

### 5.6 从报告句转换为论文句

优先把内容从 artifact/status 结构转换为 question/inference 结构：

- `我们运行了 A、B、C 三项检查且全部通过` → 说明这些控制共同排除了哪个替代解释；
- `表 2 报告全部指标` → 先指出矩阵中改变中心判断的模式，再定位完整结果；
- `结果经过 evidence-chain validation` → 直接陈述实验条件、比较和可复现信息；
- `A=..., B=..., C=...` → 保留必要精确数字，同时说明差异为何支持机制、scope 或结论。

## 6. Narrative Editor 契约

### 可以直接执行

- 为完整证据清单分配 headline、mechanism、control、scope 和 completeness 角色；
- 选择每个 section 应前景化的结果，并保留其他结果的表格、Methods 或 Appendix 承载位置；
- 合并表达同一 inference 的重复段落，但保留各 section 必需的数字锚点；
- 在 Methods 中保留完整方法定义，在其他位置先按科学类别组织，再点名与当前推理有关的方法；
- 将高密度逐项汇报改为“模式或问题 → 精选数字 → inference → 完整表格引用”；
- 改写防御性 `not X but Y`、验收式、gate 式和 reviewer-response 句型；
- 保留 caption numerical takeaway，同时使其回答图表问题而不是复述 dashboard；
- 调整 section/paragraph 顺序，使每一步改变读者理解。

### 不得直接执行

- 取消五句摘要、170 字下限或 numerical-caption 要求；
- 删除唯一的 adverse result、关键控制、主要 uncertainty 或 scope boundary；
- 为了减少重复而移除各关键 section 必需的精确 headline 数字；
- 改动任何数字、统计定义、比较方向或实验条件；
- 扩大或缩小科学 claim；
- 因为内容“打断故事”而隐藏会改变读者判断的证据；
- 把未解决的科学问题改写成语气问题。

### 需要 Reviewer 裁决的建议

Narrative Editor 若认为唯一科学内容应从主文移到附录或完全省略，应在内部 handoff 中给出：

```text
content: <事实或段落>
proposal: move_to_appendix | omit_from_manuscript
reader_benefit: <减少什么认知负担>
science_risk: <可能损失哪一步推理>
reasoning_role: <当前是否改变 claim、scope、替代解释或复现>
replacement_carrier: <table | methods | appendix | cross-reference | none>
```

Author 提出或接受该方案后，由 Scientific Reviewer 批准。此结果属于内部 mission state；不新增持久化 JSON。

## 7. Scientific loss check

Reviewer 比较编辑前后的科学含义与覆盖范围，而不是要求句子原样保留。至少检查：

1. 每个 headline claim 仍有直接、可定位的精确证据；
2. 五句摘要、170 字和 numerical caption 等既有写作要求仍满足；
3. 完整方法、baseline、控制和结果矩阵仍在论文某个合适位置可见；
4. 会改变 headline interpretation 的损失、null、反例和 uncertainty 仍然可见；
5. 关键控制在读者需要排除替代解释之前或当时出现；
6. Methods/Appendix 仍提供理解和复现结果所需的信息；
7. 包装没有把不同 population、metric、protocol 或 claim 混为一谈；
8. Narrative Editor 没有改变数字、比较方向或 scope。

Reviewer 反对包装或位置调整时必须指出丢失的推理环节或覆盖位置。`信息不完整`不是足够具体的否决理由。

Scientific loss check 需要编辑前快照。实现时在内部 mission storage 保存编辑前后的 manuscript source closure、rendered PDF 和哈希；不依赖 Git，也不在项目中新增可见审计包。

## 8. 冷读可读性评审

冷读 Reviewer 只读取当前 rendered PDF，不读取内部结果目录、HANDOFF、历史 review 或证据审计。它独立评价：

- **中心性**：第一页后能否用一句话说出唯一核心发现；
- **推进性**：每个主要 section 的数字和实验是否推进理解，而非重新播放同一清单；
- **层次性**：读者能否区分 headline、机制、控制、scope 和 completeness evidence；
- **解释性**：精确数字之后是否说明它改变了什么判断；
- **时机**：关键证据、控制和限制是否在需要时出现；
- **视觉叙事**：主图和 caption 是否共同回答清楚的问题，而不是充当 dashboard。

冷读不得以“数字很多”“摘要较长”或“控制完整”本身判差。它必须区分必要科学密度与无层次的逐项汇报，也必须同时指出内容太少、解释不足或连接缺失。

## 9. 科学完整性与可读性冲突

当 Narrative Editor 建议改变包装或位置，而 Scientific Reviewer 判断存在损失时，不直接回退到原文，也不默认删减。Author 应生成第三方案：

- 保留精确证据；
- 保留既有摘要、caption 和完整性要求；
- 将逐项汇报改为模式、对比问题或 inference；
- 改变承载形式或出现位置；
- 在表格保留完整覆盖，在 prose 解释最有推理作用的部分。

Scientific Reviewer 先排除造成结论或覆盖损失的版本，冷读 Reviewer 再比较剩余版本是否更像研究论文而非实验报告。

## 10. Phase 0.5：运行契约

在实施 prompt 改造前，先定义四个逻辑 operation，不增加 core role：

| operation | core role | 读取范围 | 输出位置 | 阻断权 |
|---|---|---|---|---|
| `author_draft` | Engineer | HANDOFF、完整科学证据、代码、venue contract | 正常论文文件 | 无认证权 |
| `narrative_edit` | Engineer | 当前论文、HANDOFF evidence roles、既有 drafting contract | 论文文件；内部编辑建议 | 无科学删除权 |
| `science_loss_check` | Reviewer | 编辑前后快照、decisive evidence、必要直接证据 | 内部 verdict；最终摘要可进入 REVIEW.md | 可阻断科学损失 |
| `cold_read` | Reviewer | 当前 rendered PDF 与冷读 rubric | 内部 readability verdict | 可阻断 reject-level 可读性问题 |

当前实现已将 operation 贯通到 role prompt catalog 和实际 round loop：Research Paper 使用 `author_draft`，Research Review 的 Engineer 使用 fresh-context `narrative_edit`，其后并行运行 `science_loss_check`、视觉检查和 `cold_read`，最后仍由普通 integrated Reviewer 裁决。非 Research vertical 继续使用 `mission` / `evaluate` 兼容路径。

编辑前 source/PDF closure 保存在 vertical state root 的 `.argus/internal/narrative-runtime/<mission>/`，按内容哈希保存编辑后版本；不依赖 Git。`cold_read` 每次获得一个临时 workspace，其中只有 `paper/main.pdf`，pass 结束即删除。新 pass 结果只进入当轮 integrated Reviewer 上下文，不单独写入项目。

正常路径中四个 operation 的临时诊断结果不生成新的项目可见包。最终 stage authority 仍由 Reviewer 和 `paper/REVIEW.md` 承担。

## 11. 代码改动面

### 11.1 `argus_skill/verticals/research/stages.py`

- 扩展 Experiment handoff，加入 reader-change arc、evidence roles、canonical placement 和 repeat role；
- 扩展 `paper.argument` / `paper.voice`，要求完整证据有主次地进入论文；
- 保留五句摘要、170 字、精确数字和 numerical caption 等既有要求；
- Review checklist 加入 semantic loss check 和 cold-reader PDF pass；
- 保持 `HANDOFF.md` 与 `paper/REVIEW.md` 为唯一正常持久化交接。

### 11.2 `argus_skill/verticals/research/prompt_policy.py`

- 使用 `operation` 区分 `author_draft`、`narrative_edit`、`science_loss_check` 和 `cold_read`；
- Narrative Editor fragment 强调证据选择与包装，而不是默认压缩或删数字；
- 不向 Narrative Editor 注入历史 Reviewer 原话和完整审计上下文；
- cold-read fragment 只接收 rendered PDF；
- Reviewer fragment 要求以“丢失的推理环节或覆盖位置”说明 veto。

### 11.3 venue drafting skills

- 保留五句摘要、170 字下限、精确 headline 数字和 numerical-caption 要求；
- 在这些要求之前加入 evidence-role selection；
- 为 Abstract、Introduction、Results、caption、Conclusion 定义不同的数字包装职责；
- 要求 Methods/table/Appendix 保持完整方法、控制和结果覆盖；
- 将 `全部都要覆盖` 与 `全部都要在每段 prose 逐项复述` 明确区分；
- 删除鼓励使用内部 gate、validator、evidence-chain 语言自证可信的残余指令；
- 保持 evidence honesty 和 adverse comparison visibility。

### 11.4 `academic_language_review.py`

- 保留五句摘要和 170 字的现行检查，不把它们迁移为通用 advisory；
- 保持 deterministic measurements 只作为 Reviewer 事实，不让 Host 判论文质量；
- 增加 headline/mechanism/control/scope/completeness placement 的候选诊断；
- 检测“逐行念表”、同一矩阵无角色复制、控制清单化和内部审计语言；
- 不对 headline 数字设通用重复次数上限；caption 重述被记录，但若承担独立可读职责不视为缺陷；
- Reviewer 同时判断解释不足与无层次堆叠。

### 11.5 Review 产物边界

`academic_language_review.py` 的正常 Python/CLI 路径现在默认 `write=False`，因此不会写 JSON、Markdown 和 history。兼容 CLI 只有显式传 `--write` 才生成旧产物：

1. 正常调度使用 `write=False`，诊断结果保存在内部 mission state，CLI 显式 `--write` 作为兼容入口；或
2. 明确这些文件是既有工具例外，并修改“唯一正常持久化输出”的表述。

当前实现采用第一种。迁移不自动删除用户项目中已有的 review 文件。

### 11.6 旧模块

- `draft_outline.py` 与 `argument_organization.py` 不在当前正常 Paper 调用路径时，正式兼容弃用，不为本次治理重新引入；
- `reviewer_simulation.py` 不作为现行 stage gate，移出推荐路径与公共导出，历史入口保留只读 warning；
- 本次不升级这些模块的 schema，也不让它们成为新的 mandatory artifact。

## 12. 测试方案

### 12.1 单元测试

- 五句摘要和 170 字检查继续生效；
- figure/table caption 仍要求 numerical takeaway；
- headline 数字可跨关键位置重复，不因固定次数被 Host 判差；
- caption 数字计入重复观测，但独立可读角色可解释该重复；
- 完整方法列表在 Methods 中存在，表格标签可重复；
- 完整矩阵仍在 paper/table/appendix 中可定位；
- evidence-role selection 能区分 headline、mechanism、control、scope 和 completeness；
- Narrative Editor prompt 不包含删除唯一科学内容或取消既有硬规则的权限；
- Reviewer prompt 要求指出具体 inference loss 或 coverage loss；
- cold-read 输入不包含源码、HANDOFF、REVIEW 或证据审计；
- `reviewer_simulation` 不再参与 stage completion。

### 12.2 回归样例

使用 run04 作为第一份负向 fixture，观察但不把表面次数写死为通用质量规则：

- headline 数字是否在不同 section 承担不同作用；
- 四方法完整列表是否由 Methods/table 规范承载，prose 是否解释比较结构；
- random-control 是否被包装为排除替代解释，而非重复 sanity-check 清单；
- `claim-bearing`、`evidence chain`、`powered validation` 等审计式表达是否退出 reader-facing prose；
- 主图和 caption 是否从 dashboard 汇报变成问题、数字和 inference。

至少加入两篇同领域、同 venue、贡献形态相近的优秀论文作为正向 calibration。不得仅靠 run04 训练 lexical blacklist，也不得把优秀论文更短或数字更少误当作目标。

### 12.3 端到端验收

对同一份证据生成 baseline 与新流程版本，盲评：

1. 五句摘要、170 字、numerical caption 和完整证据覆盖均未退化；
2. Scientific Reviewer 判断核心 claim、adverse evidence、controls、uncertainty 和 scope 等价保留；
3. 冷读 Reviewer 更容易区分 headline、机制、控制和补充证据；
4. 精确数字仍充分出现，但每次出现更清楚地服务所在 section；
5. 方法矩阵仍完整，但 prose 不再像逐行念表；
6. 不增加 unsupported claim、遗漏 adverse result 或错误合并不同实验条件。

只有科学等价和既有硬要求先通过，才比较是否减少实验报告味。

## 13. 分阶段实现

当前未提交实现状态：Phase 0 的 run04 基线和完整 shadow 改写已完成；Phase 0.5、Phase 1 及 Phase 2 的运行能力已实现；Phase 3 的候选诊断已实现，但仍保持 shadow。多篇正向 exemplar 校准和将新信号提升为 blocking 是后续观测决策，不应在缺少误报数据时自动开启。

### Phase 0：建立基线

- 冻结 run04 PDF/text 和两篇正向 exemplar；
- 记录现有五句摘要、170 字、caption、数字和完整性要求；
- 标注 headline、mechanism、control、scope 和 completeness evidence；
- 运行盲冷读，形成“实验报告味”的改造前基线。

### Phase 0.5：建立运行契约

- 明确四个 operation 的实际入口和顺序；
- 定义编辑前后内部快照、读取范围、输出位置和阻断权；
- 解决 academic-language review 项目可见产物与 REVIEW.md 唯一权威输出的冲突；
- 明确旧 outline/reviewer-simulation 模块的兼容弃用边界。

### Phase 1：低风险选择与包装改造

- 修改 Experiment → Paper HANDOFF，加入 evidence roles 和 placement；
- 在 AAAI/EMNLP drafting skills 中保留原硬要求并加入选择与包装规则；
- 修改 `paper.argument` / `paper.voice`；
- 对 run04 shadow-evaluate，不设置新的自动阻断。

### Phase 2：loss check 与冷读闭环

- 为 Narrative Editor 增加受限 mission fragment；
- 加入 Reviewer semantic/coverage loss checklist；
- 在 rendered PDF 上运行 fresh-context cold read；
- 实现 conflict resolution，不以删减或恢复原文作为唯一解。

默认 enforcement 为 `shadow`。只有显式设置
`ARGUS_SKILL_NARRATIVE_REVIEW_ENFORCEMENT=blocking` 才允许新的 semantic-loss
或 cold-read 发现单独阻断；启用前必须完成正向 exemplar 与 run04 误报校准。

### Phase 3：语义诊断与校准

- 增加 evidence-role、矩阵复述、控制清单化和审计语言候选检测；
- 使用多篇 venue exemplar 校准；
- 根据误报决定诊断保留为 advisory 还是进入 Reviewer checklist，不设通用数字重复硬上限。

## 14. 暂不做的事

- 不把当前论文定义为“不够好”再整体重写；
- 不取消五句摘要、170 字、精确数字或 numerical caption；
- 不减少实际运行的方法、控制、结果或论文中的完整覆盖；
- 不更换基础模型；
- 不通过降低 reasoning effort 假定性地改善文风；
- 不新增 Narrative Editor core role；
- 不让正则表达式直接裁决论文质量；
- 不让任何自动编辑器删除唯一科学内容；
- 不把优秀论文的句子或固定结构复制为模板；
- 不以“更短”“数字更少”作为可读性的代理指标。

## 15. 已确认的实施决策

1. 现有五句摘要、170 字、精确数字、numerical caption 和完整证据要求保留；
2. 新诊断先对 run04 shadow-evaluate，不直接阻断；
3. caption 中的 headline 数字计入重复观测，但独立可读是有效的不同职责，不因计入就要求删除；
4. Methods/Setup 取得完整方法规范枚举位置，表格方法标签豁免；其他 prose 按当前推理选择点名方法；
5. 不设 headline 数字全篇固定次数上限，改为检查每次重复的 section role；
6. Author 提出位置或省略例外，Scientific Reviewer 批准；
7. `draft_outline.py` / `argument_organization.py` 退出当前正常路径，不为本次治理升级；
8. `reviewer_simulation.py` 按兼容弃用处理，不形成 Paper/Review gate。
