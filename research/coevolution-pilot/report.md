本报告是 SkillsBench 的五任务、单次重复初版实验。状态：未见任务全部完成。

初版结论：运行中产生、验证并使用新工具的机制已经跑通，Skill/Wiki 也有真实更新；本轮没有显示原始验收通过数提升。联合组的模型调用从 33 次降到 23 次，但总 token 仅降低约 2.8%，费用估算仅降低约 0.9%，不能将少调用等同于显著成本收益。学习产物存在具体质量缺陷，不能直接自动推广。

公开来源：[SkillsBench](https://github.com/benchflow-ai/skillsbench/tree/9a1f4dd5f7659f75707435da3ce854b6e48321d1)，版本 `9a1f4dd5f7659f75707435da3ce854b6e48321d1`。使用原始任务、输入和验收断言，五道任务的官方参考解都通过验收。评测使用共同 Docker 环境和较短的固定预算，因此这些分数不是官方排行榜分数。

四个条件使用同一模型 gpt-5.6-sol/high：基线、仅学习得到的 Skill/Wiki、仅学习得到的 Pi 工具、两者联合。开发任务是 sales-pivot-analysis、manufacturing-codebook-normalization；下面三道任务不参与学习。各条件每题最多 20 次模型请求、240 秒，独立容器、相同输入；Skill/Wiki 和运行时工具在评测前冻结。所有结果保留，不挑选最佳运行。

| 条件 | 任务通过数/已完成数 | 模型事件数 | 总 token | Pi 费用估算 | 新检查工具调用 |
|---|---:|---:|---:|---:|---:|
| 基线 | 1/3 | 33 | 278,889 | $0.526 | 0 |
| Skill + Wiki | 1/3 | 30 | 387,531 | $0.630 | 0 |
| Pi 运行时工具 | 1/3 | 40 | 471,206 | $0.642 | 6 |
| 联合 | 1/3 | 23 | 271,022 | $0.521 | 7 |

| 未见任务 | 基线 | Skill + Wiki | Pi 工具 | 联合 |
|---|---|---|---|---|
| invoice-fraud-detection | 未通过 (1/2) | 未通过 (1/2) | 未通过 (1/2) | 未通过 (1/2) |
| pdf-excel-diff | 通过 (11/11) | 通过 (11/11) | 通过 (11/11) | 通过 (11/11) |
| xlsx-recover-data | 未通过 (7/8) | 未通过 (7/8) | 未通过 (7/8) | 未通过 (7/8) |

主要结果是原始验收整体通过的任务数。括号内为非跳过测试的通过项；不能把格式测试通过等同于业务答案正确。每条件只有三题、一次运行，没有统计显著性保证。

invoice-fraud-detection 的补充逐记录诊断保存在 results.json 的 invoice_diagnostics 中；它只解释原始验收为什么失败，不替换原始得分。已完成的结果中，欺诈页及原因全部识别正确，但 `PO-INVALID` 占位符被保留，参考验收要求这些位置为 null。这个输出契约边界使整体任务仍然判失败。

此外，xlsx-recover-data 存在可复核的参考口径不一致。四组都填入 Science 平均预算 7610.3，原始验收要求 7444.4。独立计算确认前者对应 2019–2024 六个年度，后者只取 2019–2023 五个年度；工作簿其他五个可直接核对的已知平均值均使用六个年度。详见 `benchmark-audit.json`。原始分数没有修改，这个 1/3 不应直接解释为真实业务正确率；此审计也不能证明任何进化条件提高了准确率。

实际进化内容：

- 2026-09-14T06:55:57.751Z，train-sales-pivot-analysis-joint-cycle2 激活 v1：Three useful inspections exposed two recurring formats: the workbook has a five-column Data sheet with 2,450 records, while the 74-page PDF stores one SA2 record as four vertically extracted lines. The prior reusable candidate was rejected because importing spreadsheet dependencies triggered oversized OpenBLAS thread creation. This revision retains the generic bounded readers, sets numerical-library thread limits before optional imports, uses pypdf rather than pdfplumber, and caps long strings as well as item count.

运行时源码逐字匹配原始模型工具调用的 source 字段：True。每个候选经过 CSV/XLSX/PDF、缺失文件错误、输入只读检查后才激活。激活和后续 inspect_data 调用位于同一 Pi 运行轨迹中。此次进化对象是实际执行的工具层 Python 代码，未训练模型权重，也未自动修改生产 Pi 内核。

学习产物：

- skill：[Product-codebook text normalization](learning/frozen/skills/engineer/codebook-text-normalization.md)；格式通过，正文 1626 字符。
- skill：[Native pivot workbooks with openpyxl](learning/frozen/skills/engineer/native-pivot-workbooks-with-openpyxl.md)；格式通过，正文 1318 字符。
- skill：[Evidence-bounded development artifact review](learning/frozen/skills/manager/evidence-bounded-development-review.md)；格式有问题，正文 2350 字符。
- wiki：[Argus-Pi bounded data inspection runtime](learning/frozen/wiki/pages/argus-pi-bounded-data-inspection-runtime.md)；格式通过，正文 4937 字符。
- wiki：[Australian SA2 demographic source compatibility](learning/frozen/wiki/pages/australian-sa2-demographic-source-compatibility.md)；格式通过，正文 981 字符。
- wiki：[Test-center codebook support limits](learning/frozen/wiki/pages/test-center-codebook-support-limits.md)；格式通过，正文 1055 字符。

Manager 的文档审阅保存在 `learning/frozen/quality-review.json`（尚未冻结时位于 current）。其主观评分来自同模型的新会话，不作为准确率证明。最终仍应结合原始文档、来源证据、工具契约和未见任务成绩判断质量。

冻结后只读反例检查另存于 `quality-probes.json`，没有反馈给评测代理或改变验收分数。检查发现：CSV 含多行引号字段时，检查器的行号是逻辑记录序号，Wiki 把它写成物理行号过强；重复 CSV 列名会覆盖值，Wiki 只明确说明了 XLSX 的同类限制。XLSX 未缓存公式返回 null、全文件行数上限会省略后续工作表，这两点与 Wiki 描述一致。Manager Skill 的 content 自带头部，写入器又生成了头部，留下重复 frontmatter，说明模型输出与持久化接口还需要更严格的契约检查。原始冻结文档未被人工润色。

开发过程：

| 开发运行 | 非跳过测试通过项 | 整体通过 | Pi 费用估算 |
|---|---:|---:|---:|
| train-manufacturing-codebook-normalization-joint | 15/16 | 0 | $1.086 |
| train-manufacturing-codebook-normalization-joint-cycle2 | 16/16 | 1 | $0.872 |
| train-sales-pivot-analysis-joint | 5/9 | 0 | $0.681 |
| train-sales-pivot-analysis-joint-cycle2 | 9/9 | 1 | $0.739 |

开发阶段保留了失败和修订。最早一次控制器错误地使用 continuation 提示、没有传入原始任务，已按基础设施错误隔离在 setup-invalid-runs；随后修正并加入原始任务与知识确实进入提示的断言。发布器对带签名工具 ID 的匹配、对验证失败证据的接纳也由实验实现者修正；这些人工桥接修复不算模型自进化成果。最初的工具式 Manager 审阅触及请求上限，随后用同一开发证据进行了一次无工具的收尾审阅，内容由模型生成，再由原生 SkillStore/WikiStore 持久化。模型生成的检查器源代码、其线程限制调整及 Skill/Wiki 修改有独立原始轨迹。

实验网关共记录 242 次模型请求，累计费用估算 $7.239，包括开发、评审和基础设施调试；上表仅为评测条件的 Pi 使用量估算。助手对话本身不在该网关内。请求上限 360、估算费用上限 $20。账户实际计费以 GitHub 为准。

适用边界：本实验复用 Argus 的 Engineer/Manager 提示协议、SkillStore、Wiki 初始化和格式约定，通过实验控制器驱动 Argus-Pi；未将完整生产 daemon 的所有调度、传播路径纳入评测。知识条件把冻结的全部 Skill/Wiki 文本注入提示，没有测试生产中的选择性检索或按需加载，也不能单独归因 Wiki 的收益。公共 benchmark 可能存在模型预训练污染；固定任务顺序和缓存也可能影响一次运行的费用。未使用任何上游预制 Skill 或 oracle 给代理解题。费用按原始 usage 中的普通输入、缓存读取、缓存写入和输出分别重新核算，旧网关日志保留；所有实验代理费用均远低于 $20 上限。

原始结果见 `results.json`、`scores.csv`、每个 runs 子目录内的 trajectory.jsonl.gz、agent/runtime-events.jsonl 和 grading/pytest.txt。发布版轨迹经无损 gzip 压缩，原始字节哈希在 archive-manifest.json；源代码、协议和参考解验收均已保留。基准输入和验收源码由 prepare.py 按固定版本下载，复跑环境与发布调整见 README.md。学习产物未写入生产全局 Skill/Wiki。
