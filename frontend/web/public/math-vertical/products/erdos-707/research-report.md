# #707 派生 A*/F4 项目 / L35 研究交付摘要

> 展示快照，冻结于 2026-07-16。原始 Erdős #707 的 prime-parameter 陈述已有反例；本报告只总结仍开放的 A*/F4 项目中已经完成独立审查的 L35 bounded result。

## 研究了什么

候选四点集 \(A_*=\{0,1,3,11\}\) 能否嵌入某个循环 perfect difference set？
L35 没有直接解决这个问题，而是审查一种更小、可精确判断的方法：
把真实点集投影到 \(C_7\) 商群后，Hall 自相关方程、乘数对称与
\(A_*+c\) 的逐纤维下界能否形成普遍 obstruction。

## 实际得到什么

- Proposition 15.1 给出 multiplier-coupled \(C_7\) 整数向量的精确参数化。
- Theorem 15.2 对每个完整平移 \(c\in C_v\) 分类实际 orbit closure 强制的逐纤维下界。
- Theorem 15.3 构造显式无界奇数族 \(q=(14r+9)^2\)，证明同一个抽象纤维向量对每个 \(c\) 都满足这套完整松弛。
- 因而，这套单一 \(C_7\) weighted relaxation 在该无界族上没有排除力。它是一条严格的方法边界。

## Reviewer 如何判定

- **数学推导：** 已通过自然语言独立复核。
- **是否为独立新结果：** 尚未认证。Hall 商群方程与 multiplier 输入是已知结果；精确参数化、碰撞表和 all-translate family 仍是 novelty-unverified。
- **研究意义：** 它阻止团队继续把同一松弛误当成普遍 obstruction，但不是 PDS 构造或不存在性定理。
- **bounded gate：** complete。
- **项目目标：** 仍开放。

## 还没有证明什么

- 没有构造循环 PDS。
- 没有证明 \(A_*\) 可嵌入或不可嵌入。
- 没有解决仍开放的四点命题 \(F_4\) 或固定 \(A_*\) 嵌入目标。
- 没有改写原始 Erdős Problem #707 已被反例否定的事实。
- 没有完成该结果的 Lean 形式化。
- 没有取得 verified-new 的新颖性裁决。

## 可复核文件

- `theorem-and-proof.md`：`SOLVE.md` §15 的冻结原文。
- `claim-ledger-l35.md`：L35 claim/evidence/status 原文。
- `reviewer-conclusion-l35.md`：`REVIEW.md` §10 的冻结原文。
- `audit_c7_quotient.py`：有限 sanity verifier。
- `c7-audit-output.txt`：本次重新运行的 literal output。

有限 verifier 在 \(q\le300\) 检查参数化与 full-lift lower bounds；只在
\(r=0\) 检查完整 all-\(c\) 支配，并在 \(0\le r\le20\) 检查公式同余和坐标下界。
它不参与无界定理的证明。
