---
name: econ-figure
description: "Create and audit empirical economics, finance and management journal figures (event study, coefplot, binscatter, trends, placebo distributions) from YAML + CSV."
---

# 经管期刊配图 —— 路由

引擎是确定的：你只写**画哪种图、数据在哪个文件、坐标轴和图例叫什么**，`scripts/render.py` 负责版式、字体和尺寸。
颜色不限：默认用一套克制的配色，想换就在 spec 里写 `figure.colors`。不要自己从零写 matplotlib 设置，也不要手调字号。

按下面的步骤从磁盘读取文件，不要凭记忆。

## 0. 这个技能对不对

| 需求 | 去向 |
|---|---|
| 事件研究 / 平行趋势、系数图、分箱散点、年度趋势、安慰剂分布、研究框架图 | 继续 |
| 经管论文里的其他数据图（柱状图、地图……） | 继续：用第 4 步的样式接口自己画，再过第 6 步审计 |
| 计算机会议论文的框架图、teaser | 不是这个技能，用 method-figure |
| 照片、示意插画 | 不是这个技能；经管期刊基本不用 |

## 1. 必读

1. [static/core/stance.md](static/core/stance.md) —— 不可妥协的规矩。
2. [static/core/contract.md](static/core/contract.md) —— 动手前要回答的问题。

再读 [manifest.yaml](manifest.yaml)，看这次还需要哪些参考文件。

## 2. 先回答契约问题

在动手前写出来（写进 paper/figures/FIGURE_PLAN.md 或回复里）：

1. 这张图要证明的一句话结论，以及它替代或补充了哪张表；
2. 图型（第 3 步的表）；
3. 数据文件：由论文自己的哪个脚本写出、对应哪张表的哪几列；
4. 目标期刊、模板和量出来的 `\textwidth`；
5. 图里出现的每个变量名、符号，和正文、表格里的写法一致；
6. 图里有没有理想数据（是就按论文的标注规则在图题末尾加 †）。

## 3. 选图型，从模板开始

读 [references/archetypes.md](references/archetypes.md)，把 `templates/` 里对应的文件复制到论文目录再改。不要从空白开始。

| 图型 | 用途 | 模板 | 范例 |
|---|---|---|---|
| `event-study` | DID 的动态效应、平行趋势检验 | `templates/event-study.yaml` | `assets/gallery/event-study.png` |
| `coefplot` | 异质性、分组、分维度、机制的系数和置信区间 | `templates/coefplot.yaml`（英文：`coefplot-en.yaml`） | `assets/gallery/coefplot.png` |
| `binscatter` | 控制固定效应后 Y 对 X 的关系（只用真实微观数据） | `templates/binscatter.yaml` | `assets/gallery/binscatter.png` |
| `trend` | 核心变量的年度均值或占比 | `templates/trend.yaml` | `assets/gallery/trend.png` |
| `distribution` | 安慰剂检验的估计值分布、分组密度 | `templates/distribution.yaml` | `assets/gallery/distribution.png` |
| `framework` | 研究框架、假设关系（H1、H2a……） | `templates/framework.yaml` | `assets/gallery/framework.png` |
| 多面板 | 两三张小图并排（如动态效应 + 安慰剂） | `templates/event-placebo.yaml` | `assets/gallery/event-placebo.png` |

动手前看一眼对应的范例 PNG，那就是要达到的样子。

## 4. 写 spec，数据从脚本来

- 数据写成 CSV，由论文自己的回归或数据脚本生成（和表格同一个来源），**不要把数字手抄进 spec**。列名见 archetypes.md。
- `figure.width_pt` 填量出来的 `\textwidth`（方法见 [references/house-style.md](references/house-style.md)）；`language` 填 zh 或 en；
  `colors` 可选，一组颜色（如 `['#1F3A68', '#B2432F']`），按系列顺序使用。
- 标签写变量名，不写整句；数学符号写成 `$k$`、`$\hat\beta_k$`，和正文一致。
- 图里不写标题、不写系数、星号、p 值：标题和说明写在 LaTeX 的 `\caption` 和“注：”里。
- 现成图型不够用时，自己写 matplotlib，但开头用同一套样式：

```python
import sys; sys.path.insert(0, '<技能目录>/scripts')
from econfig import style
style.prepare_environment()
plt = style.apply('zh')            # 之后 plt.figure(figsize=(宽cm/2.54, 高cm/2.54)) 按版心宽度画
```

## 5. 出图

```bash
python3 <技能目录>/scripts/render.py fig_event.yaml -o paper/figures/fig_event
```

输出 `fig_event.pdf`（放进论文的）和 `fig_event.png`（预览），并自动审计。文字由 XeLaTeX 排版，所以图里的中文宋体、Times 和数学符号与正文同一套字体。

## 6. 审计 —— 交付前必须做

```bash
python3 <技能目录>/scripts/audit.py paper/figures/*.pdf --width-cm 16.0
```

修掉每一个 `FAIL`，`WARN` 要么修、要么说明理由。审计查：图内标题、图上印了统计量、字号、粗体、Type 3 字体和未嵌入字体、宽度不等于版心。颜色不查。
然后**打开 PNG 用眼睛看**，按 [references/anti-checklist.md](references/anti-checklist.md) 逐条过：审计只管规则，管不了图有没有讲清楚一件事。

## 7. 交付

读 [references/latex-integration.md](references/latex-integration.md)，交回：

- figure 环境：`\includegraphics[width=\textwidth]{...}`，不缩放；
- 图题（名词短语）和“注：”（样本、被解释变量、置信区间、标准误聚类、基期、派生数字怎么算）；
- spec 和数据文件放在 PDF 旁边，以后改数字能一键重画。

## 修改已有的图

用户已经有一张图要改时：先用审计找出它违反了哪些规矩，再用对应模板重画；只改用户要求的地方，不要顺手改别的。
