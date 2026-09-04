# Argus 论文画图流程优化 — 最终报告（A | B | C 三组对比）

日期：2026-09-03

## 先说没做到 / 需要注意的

1. **G2 自动检查报"干净"之后，肉眼复查仍发现缺陷。** 场景 2 中 "Input Embedding"、"Scaled Dot-Product" 标题曾压到卡片边框、K 标签压边。根因是宽度估算器把 Helvetica Bold 低估最多 16%，且排版压力把卡片内边距压到 4px。已手工修复（粗体 AFM 字宽表 + 内边距下限（先 8px，后按 v1 token 提到 12px）+ 换行作为独立压力步骤），四张图重建、门禁与确定性全部复验通过。教训：门禁量的是几何盒子，不是渲染字形，字宽表必须贴近真实字体。
2. **场景 5（消融柱状图）曾有 88.9 数值标签被裁掉**，由我手工把 y 轴上限调到 98 修复；基线组柱状图仍为截断 y 轴（60% 起），在图注里已声明。
3. **场景 4 高亮路径是"合理但未指定"的**，场景描述没给算子序列，图上加了脚注说明。
4. **REVIEW_SKILLS.md 里列出的 Argus 画图 skill 之间的契约缺口**（router → studio → ppt-master 的字段命名、spec_lock 位置等）只做了本仓库内的桥接，没有改 Argus 本体的 skill 文档。
5. **"上下文调到 40 万"是 Claude Code 客户端配置**，会话内改不了，需要在客户端设置里调。

## C 组做了什么（整合 Argus 所有画图 skill）

`studio/build_figure.py build-all` 一条命令走完：

- **Research Visualization Router** 决定路由（示意图 → ppt-master；数据图 → paper_chart_style）。
- **Paper Framework Figure Studio** 的图契约 JSON（`studio/contracts/*.json`）：节点、分组、边、标签、方向。
- **Figma 风设计 token**（`studio/figma_tokens.py`、`studio/CONVENTIONS.md`，来源 = `figure_tool.py` 提示词模板 `argus-image2-paper-prompt-v1`）：暖白底 #fbfaf7、#1F2933 2px 描边、圆角 12/16px、卡片内边距 ≥12px、按角色的 v1 浅色填充（#ffe2d1/#fff2bd/#dcecff/#e2f7df/#eadfff/#fff1c9）、药丸标签、#D55E00 步骤徽章、虚线分组、少量 tabler 图标。
- **自研布局渲染器**（`studio/figma_figure_renderer.py`）：分层布局、正交走线、箭头 28px 净空、标签不压箭头、画布装不下时按"内边距 → 换行 → 间距 → 字号"逐级压缩，21px（8.28pt @ 178mm 双栏宽）字号硬下限。
- **PPT Master 桥接**（`studio/pptmaster_bridge.py`）：SVG 平铺页契约 → 原生可编辑 PPTX（0 张图片，全部是形状和文本框）。
- **质量门禁 v2**（`studio/figure_quality_gate_v2.py`）：字号、重叠、边穿过非端点节点、边穿过分组标签、箭头被裁、标签盖箭头等；输出 `studio/out/<id>/quality/{gate.json,build_receipt.json,render.log}`。
- **Paper Chart Styling**（场景 5）：`paper_chart_style.py` 出版尺寸 3.3×2.18in，字号 8–10pt。

## 复验结果（重建后）

| 项目 | S1 | S2 | S3 | S4 |
|---|---|---|---|---|
| 门禁 errors / warnings | 0/0 | 0/0 | 0/0 | 0/0 |
| 最小字号 | 21px / 8.28pt | 同 | 同 | 同 |
| 两次重建 SVG 字节一致 | 是 | 是 | 是 | 是 |
| PPTX 图片数 | 0 | 0 | 0 | 0 |
| PNG | 2560×1440 | 同 | 同 | 同 |
| 卡片横向内边距 | 17px | 12px（已到下限） | 非分层 | 非分层 |
| 卡片字号 / 内边距 / 层间距 | 23 / 17 / 28 | 22 / 12 / 28 | 24 / 18 / – | 24 / 18 / – |

门禁阈值未放松：MIN_FONT_SIZE 12、MIN_PHYSICAL_PT 7、PREFERRED 8、OVERLAP_TOLERANCE 1。

## 与 v1 Figma token 对齐（最后一轮）

用户指定风格源为 `argus_skill/verticals/research/figure_tool.py` 的 `argus-image2-paper-prompt-v1`（Argus 最新 runtime 已改成去掉 "Figma" 的 v2，本仓库按用户要求沿用 v1）。最后一轮把 C 组与 v1 token 逐项对齐：

- 背景由纯白改为暖白 #fbfaf7（SVG 背景矩形、药丸/遮罩填充、PPTX 原生幻灯片背景 FBFAF7）；全部 SVG 中 `#ffffff` 填充为 0 处。
- 卡片描边 1.5px → 2px；圆角 RADIUS_CARD 12 / RADIUS_PANEL 16 / 药丸 999。
- 内边距下限 8px → 12px（`padding_floor = max(12, round(font_floor*0.4))`）。
- 浅色块改为 v1 六色；S1 图标从每个节点都有削减到 4 个（Environment / Experience Buffer / Centralized Critic / Policy Network），契约 schema 允许 `icon: null`。
- S5 柱状图：底色 #fbfaf7、v1 浅色填充 + #1f2933 1px 描边、去掉原先的 `highlight_ours` 加粗描边；"Full Model" 现仅靠颜色 + 斜线纹理 + 图例区分。
- 代价：S2 因内边距提到 12px，卡片字号由 23px 压到 22px（仍高于 21px 下限）。
- 复验：S1–S4 门禁 0/0，最小字号 21px，PPTX 0 张图片，PNG 2560×1440，另开临时目录重建后 SVG 字节一致。S5 不走 `build-all`（没有契约 JSON，由 `scenario_5_ablation_studio.py` 单独渲染），因此不在确定性检查范围内。

## 看图的位置

- 三列对比图（A 基线 | B 第一轮优化 | C 整合 skill 的 studio）：`comparison/scenario_{1..5}_*_ABC.png`
- C 组成品：`studio/scenario_{1..4}_*.{svg,pptx,png,pdf}`，`studio/scenario_5_ablation_results.{svg,png,pdf}`
- 每图门禁与回执：`studio/out/<id>/quality/`
- 过程报告：`studio/REPORT_{A,B,D,E,F,G1,G2,S5}.md`、独立评审 `studio/REVIEW_C.md`、skill 审查 `studio/REVIEW_SKILLS.md`

## 已知残留

- S2 六个紧凑标签在 PPTX 中各导出为两个文本框（视觉无差，编辑时需注意）。
- S4 进入 Layer-4 Skip Connection 的路径从下方绕入，构图略绕。
- `project_manager.py validate` 仍有命名建议级提示。
