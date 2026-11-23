# 版式常量

## 宽度：先量 `\textwidth`

图按版心宽度画，放进 LaTeX 时 `width=\textwidth`，不再缩放，字号才是设定的 8.5pt。在 main.tex 的
`\begin{document}` 后临时加一行，编译后从日志读出来：

```latex
\typeout{TEXTWIDTH=\the\textwidth}      % 双栏期刊再加 \typeout{COLUMNWIDTH=\the\columnwidth}
```

```bash
grep -o 'TEXTWIDTH=[0-9.]*pt' paper/main.log
```

把这个数填进 spec 的 `figure.width_pt`。下面是本仓库期刊模板实测的值（模板选项、`\geometry` 改了就要重量）：

| 模板 | 期刊举例 | `\textwidth` | 约合 |
|---|---|---|---|
| cn（ctexart） | 管理世界、经济研究、会计研究、金融研究 | 455.24 pt | 16.0 cm |
| elsevier（elsarticle） | JFE、JAE、JCF、Research Policy | 390 pt | 13.7 cm |
| tandf（interact） | European Accounting Review、ABR | 408 pt | 14.3 cm |
| springer（sn-jnl） | RAST、JIBS、JBE | 372 pt | 13.1 cm |
| informs（informs3/4） | Management Science、Organization Science、ISR | 469.75 pt | 16.5 cm |
| generic（article） | TAR、JAR、JF、RFS 等投稿版 | 469.75 pt | 16.5 cm |

## 高度

高 ÷ 宽：事件研究 0.45，趋势 0.42，分布 0.45，分箱散点 0.62，框架图 0.42，系数图按行数自动。
多面板横排时整体比例不变；竖排时按行数加高。一张图在版面上一般不超过半页高。

## 字体与字号

- 中文稿：中文 FandolSong（ctex 模板默认宋体），西文和数字 TeX Gyre Termes，数学 TeX Gyre Termes Math。
- 英文稿：TeX Gyre Termes + TeX Gyre Termes Math（与 Times 模板一致）。
- 全图 8.5pt，一个字号；不用粗体。审计：小于 6.5pt FAIL，小于 7.5pt WARN，大于 10.5pt FAIL。
- 模板用了别的正文字体时，在 spec 里加 `figure.preamble` 覆盖（例如 `\setmainfont{...}`）。

## 颜色与线

颜色不限。默认配色按系列顺序取：深蓝 `#1F3A68`、砖红 `#B2432F`、墨绿 `#3A7D44`、灰 `#6E6E6E`、赭石 `#C08A2B`。
spec 里写 `figure.colors: [...]` 可以换成任何颜色（十六进制或 matplotlib 颜色名）。同一个概念在全文的图里用同一种颜色
（例如处理组始终深蓝、对照组始终砖红）。中文期刊多为黑白印刷：引擎让每个系列同时有不同的标记形状和线型，印成黑白也分得清。

| 元素 | 值 |
|---|---|
| 系列 | 按配色顺序；标记依次为实心圆、空心方块、实心三角、空心菱形；线型依次为实线、虚线、点线、点划线 |
| 坐标轴、文字 | 黑 |
| 参考线（零线、事件时点、全样本估计）、次要标签 | 灰 `#6E6E6E`，0.6pt 虚线或点线 |
| 直方图 | 第一个颜色的淡色；实际估计值用第二个颜色的竖线 |
| 区间带 | 该系列颜色的淡色 |
| 分隔线 | 浅灰 `#BDBDBD` |
| 数据线 / 误差线 | 0.8pt；坐标轴 0.6pt |
| 标记 | 直径 3.6pt |

去掉上、右边框；不画网格（确实需要时只画极浅的水平线）；刻度朝外，4～6 个；图例无边框，放在不挡数据的空白处，
只有一个系列时不要图例。
