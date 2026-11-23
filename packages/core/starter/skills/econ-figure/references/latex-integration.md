# 放进论文

## 中文期刊（《管理世界》《经济研究》……）

图题在图下方，写成“图 1 标题”；图题下面另起一段小字“注：”。

```latex
\begin{figure}[htbp]
  \centering
  \includegraphics[width=\textwidth]{figures/fig_event.pdf}
  \caption{数字化转型对企业 ESG 表现的动态效应}
  \label{fig:event}
  \begin{minipage}{\textwidth}\footnotesize
    注：样本为 2011—2022 年 A 股上市公司。图中报告式（3）中各期交互项的系数及其 95\% 置信区间，
    标准误聚类到企业层面；以试点前一年（$k=-1$）为基期。
  \end{minipage}
\end{figure}
```

ctex 模板默认把“图”和编号排成“图 1”；若模板用了 `\captionsetup{labelsep=space}` 之类的设置，照模板不动。

## 英文期刊（JFE、TAR、Management Science……）

图题在图下方；说明写在图题里或紧随其后的一段 `\footnotesize` 文字。很多期刊要求图放文末（模板里有 endfloat）：
保留模板的设置，figure 环境照常写。

```latex
\begin{figure}[htbp]
  \centering
  \includegraphics[width=\textwidth]{figures/fig_event.pdf}
  \caption{Dynamic effects of digital transformation on ESG ratings}
  \label{fig:event}
  \begin{minipage}{\textwidth}\footnotesize
    This figure plots the coefficients on the interactions in Equation~(3) with 95\% confidence intervals.
    Standard errors are clustered by firm. The year before the pilot ($k=-1$) is the omitted category.
  \end{minipage}
\end{figure}
```

## 图题和注怎么写

- 图题是名词短语，说“这是什么图”，不写结论句，不写“本文”“我们发现”。
- 注写清：样本、被解释变量、点和区间分别是什么、置信水平、标准误聚类层级、基期、派生数字怎么算。
- 图里有理想数据时，按论文的标注规则在图题末尾加一个 †，注里不再解释。
- 正文用 `\ref{fig:event}` 引用每一张图。

## 交付清单

- PDF 放进 `paper/figures/`，spec 和 CSV 放在 `paper/figures/src/`，能一条命令重画；
- `\includegraphics` 宽度是 `\textwidth`，没有 `scale` 或别的宽度；
- 重新编译，确认没有 Overfull、图没有跑到奇怪的位置。
