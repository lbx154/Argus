# scipilot-figure-skill

> SciPilot Skills family. Scientific data **visualization advisor** — thinks first, plots second.
> SciPilot Skills 家族成员 — 科研数据**可视化顾问**，先思考后绘制。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB.svg)](#dependencies--依赖)
[![Status: v2.1.0](https://img.shields.io/badge/Status-v2.1.0-success.svg)](#)
[![Advisor Mode](https://img.shields.io/badge/Mode-Advisor%2BPlotter-c41e3a.svg)](#为什么这不只是个画图工具)
[![Stack](https://img.shields.io/badge/Stack-matplotlib%20%7C%20seaborn%20%7C%20plotly-orange.svg)](#)
[![Claude Code Skill](https://img.shields.io/badge/Claude%20Code-Skill-orange.svg)](https://claude.com/claude-code)

A [Claude Code](https://claude.com/claude-code) / [Codex](https://github.com/openai/codex) / Cursor Skill that does **two things in order**: (1) profiles your data and recommends the right chart for the argument you want to make, (2) renders it at publication grade for Nature / Science / IEEE / Elsevier / PNAS / Chinese journals. Built on **matplotlib + seaborn + SciencePlots** (static) and **plotly** (interactive), with **CJK font auto-configuration** so Chinese text never renders as boxes.

> [中文文档](#中文文档) | [English](#english)

---

## 中文文档

### 概览

科研工作者最大的画图痛点往往不是"不会用 matplotlib"，而是"手上一堆数据，不知道该用什么图把结论讲清楚"。`scipilot-figure-skill` 是 SciPilot Skills 家族的第二个成员，专做这件事——

**它的首要能力是【思考与判断】，其次才是【绘制】。**

### 为什么这不只是个画图工具

```
普通画图工具           scipilot-figure-skill
─────────────         ─────────────────────
你说 "画柱状图"  →   先 EDA：每列类型、样本量、分布、异常值、相关性
直接 plt.bar()        再问："你想论证什么？组间差异？关系？趋势？"
                     按数据特征 + 论证目标查决策框架推荐图型
                     n=5 想画均值柱时主动拦截 → 改用 stripplot
                     维度 > 12 时建议拆图，不硬塞
                     最后才进入绘制 → 期刊规范 → 自检 → 导出
```

### 核心工作流（8 步）

```
0. 理解任务   ── 这张图要论证什么？数据在哪？
   ↓
1. 剖析数据   ── profile_data.py：列类型/样本量/分布/异常/相关
   ↓
2. 选图       ── chart_selection.md：按数据形态+论证目标决策
   ↓ (主动拦截 → viz_pitfalls.md)
3. 查规范     ── journal_specs.md：栏宽/字号/DPI/字体
   ↓
4. 配环境     ── setup_style.py：期刊预设+CJK字体
   ↓
5. 绘制       ── plot_recipes.md：9 类图配方
   ↓
6. 自检闭环   ── 程序自检 visual_qa + AI 读图 visual_review.md（缺字/裁切/重叠/对齐→回改）
   ↓
7. 导出       ── export_figure.py：多格式+按最终尺寸+灰度预览
```

### v2.1 新增：出图后的视觉自检闭环

普通画图工具画完就结束——没人回看成图，于是中文方框、文字被裁、图例压数据、子图编号乱放，全留到投稿才暴露。v2.1 补上**出图后的闭环**：

```
绘制 → 渲染 PNG → 程序自检(缺字/裁切/重叠) + AI 读图(遮盖/子图对齐/灰度)
                            ↓ 发现问题
        回到对应步骤改图 → 重渲 → 再读，直到通过
```

- **程序抓确定性问题**：`visual_qa.audit_layout()` 同时盯 warning + logging 两条通道拦截缺字乱码，并查文字越界裁切、刻度标签重叠。
- **AI 读图抓感知性问题**：渲成 PNG 后用多模态读图能力核对图例是否压数据、子图 a/b/c 是否对齐、配色灰度可分——这些程序查不出。
- **子图标签一行对齐**：`layout_tools.add_panel_labels(fig)` 用统一 figure 坐标让 a/b/c/d 横竖成线，不再手摆易错位。
- **字体兜底**：`setup_style` 全模式默认修负号方框 + 自动配 CJK 字体，从源头少出乱码。

详见 [`references/visual_review.md`](references/visual_review.md)。

### 五条硬性原则

1. **按最终尺寸出图不二次缩放** — `figsize=(3.5, 2.625)` 直接定 Nature 单栏
2. **矢量优先** — 数据图走 PDF / SVG / EPS；照片才用 TIFF/PNG；**绝不 JPEG**
3. **配色对色盲友好** — 默认 Okabe-Ito，加冗余编码，出图前查灰度
4. **字号在最终尺寸下可读** — 7-9 pt，最小 6 pt
5. **误差必有交代** — 图注必须写清 SD/SEM/95%CI + n + 检验方法

### 主动拦截的画图错误（精选）

完整 15 条在 [`references/viz_pitfalls.md`](references/viz_pitfalls.md)。

- **P1**：n<10/组的均值柱掩盖分布 → 改箱线 + stripplot
- **P2**：双 Y 轴的"相关"是作图者捏造的 → 拆子图或标准化
- **P3**：饼图与 3D 图 → 横向柱状
- **P4**：Y 轴不当截断 → 从 0 起或断裂明示
- **P6**：x 是分类用折线连均值 → 散点 / 柱状
- **P14**：rainbow / jet 色图 → viridis / RdBu_r
- **P12**：一图多论点 → 拆图，一图一结论
- **P16**：中文/负号变方框 → setup_style 配 CJK + 关 unicode_minus，导出前 visual_qa 拦截
- **P18**：子图 a/b/c 乱放 → add_panel_labels 统一对齐

### 安装

```
请帮我安装这个 Skill：https://github.com/Haojae/scipilot-figure-skill.git
```

或手动：

```bash
git clone https://github.com/Haojae/scipilot-figure-skill.git \
          ~/.claude/skills/scipilot-figure-skill
pip install -r ~/.claude/skills/scipilot-figure-skill/requirements.txt
```

`SciencePlots` / `pypdf` / `kaleido` 是可选增强，缺失时不影响运行。

### 中文支持

`setup_style(lang='zh')` 按优先级查找：

```
Noto Sans CJK SC > Source Han Sans SC > SimHei > Microsoft YaHei
```

找不到任何 CJK 字体时抛出清晰的安装提示。中文期刊"宋体 + Times New Roman 数字"混排：传 `serif_for_zh=True`。

### 使用示例（4 个，**至少 2 个体现"思考"**）

**例 1：用户只丢一个 CSV**

```
我有这份 results.csv，帮我画成论文图。
```

→ Skill 不会直接画。会先：
1. 问"你这份数据主要想说服读者相信什么？"
2. 跑 `profile_data.py` 输出剖析报告
3. 给出推荐图型 + 理由 + 备选
4. 用户确认后才进入绘制流程

**例 2：用户指定的图型不合适**

```
帮我画 3 组各 5 个样本的均值柱状图。
```

→ Skill 主动拦截：

> n=5 太小，均值柱掩盖分布——审稿人很可能要求"show individual points"。
> 我建议改成 **箱线 + stripplot 叠加每个点**：5 个点直接可见。要按原方案画，还是改？

**例 3：多面板组合**

```
给我画 Figure 1：4 panel，分别是 PCA、loss、混淆矩阵、生存曲线。
Nature 双栏。
```

→ 各 panel 字号配色统一，子图标签 a/b/c/d 加粗左上，导出 PDF + 灰度预览。

**例 4：带显著性标注**

```
3 组数据画箱线 + 显著性。
```

→ 先确认 n、检验方法、是否多重校正；图注必须写清统计内容。

### 命令行直接调脚本

```bash
# 数据剖析（思考的起点）
python scripts/profile_data.py results.csv --group group --group condition

# 列可用 CJK 字体
python scripts/setup_style.py --list-fonts

# 演示导出
python scripts/export_figure.py demo --out ./test_demo

# 合规自检
python scripts/check_figure.py figs/*.pdf --min-dpi 300 --strict
```

### SciPilot Skills 家族

| Skill | 状态 | 功能 |
|---|---|---|
| scipilot-cite-skill | [v1.0.0](https://github.com/Haojae/scipilot-cite-skill) | 文献检索与引用插入 |
| **scipilot-figure-skill** | **v2.1.0 (本仓库)** | **可视化顾问 + 绘制 + 视觉自检闭环** |
| scipilot-writing-skill | [v1.0.0](https://github.com/Haojae/scipilot-writing-skill) | 学术写作与润色 |
| scipilot-review-skill | 规划中 | AI 模拟审稿 |
| scipilot-submit-skill | 规划中 | 投稿格式适配 |
| scipilot-read-skill | 规划中 | 论文阅读与翻译 |

### 许可证

[MIT](LICENSE) © 2026 Haojae

---

## English

### Overview

The hardest part of scientific plotting is rarely "I don't know matplotlib" — it's "I have data, I don't know which chart conveys my conclusion." `scipilot-figure-skill` is the second member of the SciPilot Skills family, built around this insight: **think first, plot second**.

### Why this isn't just a plotting tool

```
Generic plotter            scipilot-figure-skill
──────────────             ──────────────────────
"plot a bar chart"  →    Profiles data first: types, n, distribution, outliers
plt.bar()                Asks: "What argument do you want this figure to make?"
                         Decides chart type from data shape + intent
                         Refuses bad choices (n=5 mean bar → stripplot instead)
                         Suggests splitting figures when dimensions exceed 12
                         Only then renders → spec → audit → export
```

### 8-step core workflow

```
0. Understand   — what does this figure argue? where is the data?
   ↓
1. Profile      — profile_data.py: types / n / distribution / outliers / corr
   ↓
2. Select       — chart_selection.md: decision framework by shape + intent
   ↓ (active interception → viz_pitfalls.md)
3. Spec         — journal_specs.md: column width / font / DPI
   ↓
4. Style        — setup_style.py: journal preset + CJK font config
   ↓
5. Plot         — plot_recipes.md: 9 recipe families
   ↓
6. Self-check   — visual_qa (program) + AI reads the PNG (visual_review.md): glyphs/clipping/overlap/alignment
   ↓
7. Export       — export_figure.py: multi-format + final size + grayscale
```

### New in v2.1: a post-render visual self-check loop

Generic plotters stop at "saved" — nobody looks at the result, so CJK tofu boxes, clipped labels, legends covering data, and misaligned panel letters all survive to submission. v2.1 closes the loop **after rendering**:

```
plot → render PNG → program audit (glyphs/clipping/overlap) + AI reads image (occlusion/panel alignment/grayscale)
                              ↓ issues found
       go back, fix, re-render, re-read — until clean
```

- **Program catches deterministic issues**: `visual_qa.audit_layout()` traps missing-glyph warnings (both warnings and logging channels), text clipped off-canvas, and overlapping tick labels.
- **AI catches perceptual issues**: after rasterizing to PNG, the skill reads the image to check whether the legend covers data, whether panel letters a/b/c line up, and whether colors stay distinct in grayscale.
- **One-call panel alignment**: `layout_tools.add_panel_labels(fig)` places a/b/c/d in unified figure coordinates so they align both ways.
- **Font safety net**: `setup_style` fixes the minus-sign box in all modes and auto-configures CJK fonts.

See [`references/visual_review.md`](references/visual_review.md).

### Five hard rules

1. **Render at final size, never rescale** — set `figsize=(3.5, 2.625)` directly
2. **Vectors first** — PDF / SVG / EPS for data figures; **never JPEG**
3. **Colorblind-safe palette** — Okabe-Ito default + redundant encoding
4. **Readable type at final size** — 7-9 pt body, 6 pt minimum
5. **Errors must be explained** — captions state SD/SEM/CI + n + test type

### Actively intercepted mistakes

Full 15 in [`references/viz_pitfalls.md`](references/viz_pitfalls.md).

- **P1**: mean-only bar charts with n<10 hide distributions → use box + stripplot
- **P2**: dual-Y axis fabricates correlations → split panels or standardize
- **P3**: pie charts and 3D bars → horizontal bar
- **P14**: rainbow / jet colormaps → viridis / RdBu_r
- **P12**: one figure with five points → split, one figure per claim
- **P16**: CJK / minus-sign tofu boxes → setup_style configures CJK + disables unicode_minus; visual_qa blocks leftover missing glyphs
- **P18**: scattered panel letters → add_panel_labels aligns a/b/c in one call

### Installation

```
Please install this Skill for me: https://github.com/Haojae/scipilot-figure-skill.git
```

Manual:

```bash
git clone https://github.com/Haojae/scipilot-figure-skill.git \
          ~/.claude/skills/scipilot-figure-skill
pip install -r ~/.claude/skills/scipilot-figure-skill/requirements.txt
```

### Usage examples

**Example 1: bare CSV**

```
I have results.csv. Plot it for my paper.
```

The skill will: profile the data, ask what argument the figure should make, recommend a chart with rationale + alternatives, and only then plot.

**Example 2: bad request, gentle refusal**

```
Plot a bar chart of group means: 3 groups, 5 samples each.
```

The skill will reply:

> With n=5 per group a mean bar chart hides the distribution and reviewers
> typically ask for individual data points. I recommend **box plot +
> stripplot overlay** — all five points are visible and the distribution
> is clear. Stick with bars, or switch?

**Example 3: multi-panel composite**

```
Figure 1, 4 panels: PCA, loss curves, confusion matrix, survival.
Nature double column.
```

**Example 4: statistical comparison**

```
3 groups, box plot with significance.
```

### SciPilot Skills family

| Skill | Status | Purpose |
|---|---|---|
| scipilot-cite-skill | [v1.0.0](https://github.com/Haojae/scipilot-cite-skill) | Reference discovery & insertion |
| **scipilot-figure-skill** | **v2.1.0 (this repo)** | **Advisor + renderer + visual self-check loop** |
| scipilot-writing-skill | [v1.0.0](https://github.com/Haojae/scipilot-writing-skill) | Writing & polishing |
| scipilot-review-skill | Planned | AI peer-review simulation |
| scipilot-submit-skill | Planned | Submission formatting |
| scipilot-read-skill | Planned | Paper reading & translation |

### License

[MIT](LICENSE) © 2026 Haojae

### Dependencies / 依赖

```
matplotlib>=3.7
seaborn>=0.13
plotly>=5.18
pandas>=2.0
numpy>=1.24
scipy>=1.10
Pillow>=10.0
SciencePlots>=2.1   # optional
pypdf>=4.0          # optional
kaleido>=0.2.1      # optional
PyMuPDF>=1.23       # optional; only for visual_qa.render_preview() of saved PDFs
```

Python 3.9+ recommended.
