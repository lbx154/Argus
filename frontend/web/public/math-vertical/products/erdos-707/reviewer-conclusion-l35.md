# L35 independent review — verbatim frozen excerpt

> Source: `research/REVIEW.md` §10, captured 2026-07-16 UTC.

## 10. L35：multiplier-coupled \(C_7\) 松弛的专项审计

本节只审查 `SOLVE.md` Proposition 15.1、Theorems 15.2--15.3 和
`CLAIM_LEDGER.md` L35。沿用 §9 已认证的 Hall 商群方程，并独立复核其与
multiplier orbit reduction、指定集合的 full-lift 碰撞以及显式无界族的
组合。审查日期为 2026-07-15。

### 10.1 原问题、\(F_4\) 与 §15 三个结论并排核对

| 命题 | 对象与量词 | 假设 | 结论 | 严格非等价边界 |
|---|---|---|---|---|
| Erdős 1980 原始 Problem #707 | 对每个有限正整数 \(B_2\) 序列，存在一个**素数** \(p\) 和模 \(p^2+p+1\) 的 \(p+1\) 个剩余类 | 输入整数本身必须作为不同剩余类出现，且全部非零有序差唯一 | 输入嵌入一个 prime-order-parameter perfect difference set | 量化所有有限输入且限制参数为素数；已由 Alexeev--Mixon Theorem 8 的另一四点集否定。它不是 §15 所研究的固定 \(A_*\)、任意整数 \(q\) 必要条件。 |
| 本项目的 \((F_4)\) | 对每个四元素整数 Sidon 集 \(A\)，存在某个整数 \(q\ge1\) 和某个循环 \((q^2+q+1,q+1,1)\) PDS \(D\) | \(\rho_v|_A\) 单射且 \(\rho_v(A)\subseteq D\)；不要求 \(q\) 为素数幂或 \(D\) 为 Singer | 四个**实际标号**嵌入一个真实循环 PDS | 这是存在真实点集及完整逐点差覆盖的命题；任何 quotient multiplicity 可行向量都不是其充分条件。 |
| Proposition 15.1 | 对每个 \(q\ge2\) 且 \(q\equiv2,4\pmod7\)，先考察每个**已存在**且零和规范化的循环 PDS；再分类所有满足同一 reduced Hall system 的 abstract integer vectors | \(7\mid v=q^2+q+1\)；真实 PDS 部分复用 classical multiplier 使规范化集合在乘 \(q\) 下不变 | 真实 PDS 的 \(C_7\) 纤维必为 \((x,y,y,z,y,z,z)\)；所有这种形状的 cardinality/full-shift 解与满足 (15.4)--(15.5) 的 \((a,d)\) 双向等价 | 前半是 PDS 的必要像，后半是加权向量的完整 iff；都不构造 PDS、不含指定点，也不排除或嵌入 \(A_*\)。 |
| Theorem 15.2 | 对上述每个 \(q\) 和**每个 full residue** \(c\in C_v\)，计算 \(A_*+c\) 的实际乘 \(q\) 轨道闭包 \(U_c\) | \(A_*=\{0,1,3,11\}\)；若有真实嵌入，则 \(c\) 必须是同一个零和规范化平移 | 精确给出 \(b(c)=(\epsilon,\beta,\beta,\gamma,\beta,\gamma,\gamma)\)，并分类所有 seed-orbit collisions 与 fixed points；嵌入只会推出 \(n\ge b(c)\) | 这是**必要 lower bound** 的 all-\(c\) 分类，不声称存在任何满足下界的 lift，也不声称某个 \(c\) 必为实际规范化平移。 |
| Theorem 15.3 | 对每个 \(r\ge0\)，令 \(q=(14r+9)^2\)；对该 \(q\) 的**每个** \(c\in C_v\) | 只要求 Proposition 15.1 的完整 \(C_7\) integer relaxation 和 Theorem 15.2 的 exact lower bounds | 同一个显式 vector 对所有 \(c\) 同时满足 Hall 方程、multiplier 对称和 \(n\ge b(c)\) | 证明的是该 relaxation 在一个无界奇数族上无排除力；不是 PDS 构造、不是 \(A_*\) 嵌入、不是 \(A_*\) 非嵌入，更不解决 \((F_4)\)。 |

量词方向尤其重要。真实嵌入若存在，会产生一个特定规范化平移 \(c\) 及一个
满足 §15 条件的纤维向量；Theorem 15.3 反过来对每个 \(c\) 都产生同一个
**abstract vector**，但没有在任一纤维内选择点。因此
\[
\text{真实嵌入}\Longrightarrow\text{\(C_7\) relaxation 可行},
\]
而逆向没有被证明，也通常不成立。all-\(c\) 可行只能关闭这一必要条件的
排除能力。

### 10.2 Proposition 15.1 的正反参数化

令 \(k=q+1\)、\(s=v/7\)。在 \(C_7\) 上，\(q\equiv2\) 或 \(4\) 的乘法轨道
恰为
\[
\{0\},\quad H=\{1,2,4\},\quad-H=\{3,5,6\}.
\]
零和规范化 PDS 在乘 \(q\) 下不变，故其纤维向量必须是
\[
n=(x,y,y,z,y,z,z). \tag{10.1}
\]
记周期自相关为 \(R(t)=\sum_jn_jn_{j-t}\)。乘 \(q\) 给
\(R(qt)=R(t)\)，而换序给 \(R(-t)=R(t)\)；两个三元非零轨道又被负号交换，
所以六个非零 shift 都有同一值 \(T\)。Hall 的零 shift 和 cardinality
方程为
\[
x+3y+3z=k,\qquad x^2+3y^2+3z^2=q+s. \tag{10.2}
\]
反之，只要 (10.2) 成立，
\[
k^2=R(0)+6T=q+s+6T,\qquad k^2=q+7s
\]
强制 \(T=s\)。因此 (10.2) 确实恢复**全部六个**非零 Hall shifts，而非只
保留平方和。

置
\[
u=7x-k,\qquad p=7y-k,\qquad r=7z-k.
\]
直接展开 (10.2) 得
\[
u+3p+3r=0,\qquad u^2+3p^2+3r^2=42q. \tag{10.3}
\]
消去 \(u=-3(p+r)\) 后为
\[
2p^2+3pr+2r^2=7q. \tag{10.4}
\]
又因 \(u,p,r\equiv-k\pmod7\)，可定义
\[
a=p+r\equiv-2k\pmod7,\qquad d=(p-r)/7\in\mathbb Z.
\]
恒等式
\[
4(2p^2+3pr+2r^2)=7(p+r)^2+(p-r)^2
\]
把 (10.4) 精确变成
\[
a^2+7d^2=4q,\qquad a\equiv-2(q+1)\pmod7. \tag{10.5}
\]
解回 \(u,p,r\) 给
\[
x=\frac{q+1-3a}{7},\quad
y=\frac{2(q+1)+a+7d}{14},\quad
z=\frac{2(q+1)+a-7d}{14}. \tag{10.6}
\]

反向也闭合。由 (10.5) 模 \(4\) 可知 \(a,d\) 同奇偶，于是
\[
p=(a+7d)/2,\quad r=(a-7d)/2,\quad u=-3a
\]
均为整数。(10.5) 的模 \(7\) 条件给
\(p,r,u\equiv-k\pmod7\)，所以 (10.6) 的三个分子分别可除以 \(7,14,14\)。
倒推上述恒等式恢复 (10.3)、(10.2)，再由 \(k^2=R(0)+6T\) 恢复全部
非零 shift。故这是标号 orbit values 的双向参数化；非负性和纤维容量
\(0\le x,y,z\le s\) 是 feasibility 的另加不等式，不是被遗漏的方程。

对 \(q\ge37\) 的自动边界也成立：(10.5) 给
\(|a|\le2\sqrt q\)，故
\[
x\ge(q+1-6\sqrt q)/7>0.
\]
由 (10.4) 配方得到 \(|p|,|r|\le2\sqrt{2q}\)，故
\[
y,z\ge(q+1-2\sqrt{2q})/7>0.
\]
而 \(s\ge q+1\)（\(q\ge7\)），再由 \(x+3y+3z=q+1\) 得每项不超过
\(q+1\le s\)。这只简化 abstract feasibility，仍不产生 lift。

### 10.3 Theorem 15.2：full-lift 碰撞、固定点和三 seed 情形

表 (15.15) 只按 \(\bar c=c\bmod7\) 将四个**整数 seed labels**
分入 \(\{0\},H,-H\)。在非零 quotient orbit 内，两个不同 seeds \(a,b\)
生成同一个 full-group \(q\)-orbit，当且仅当其中一个非平凡幂把一个
shifted seed 送到另一个。因 \(q^3\equiv1\pmod v\)，\(q^2\) 的情况反向后
已包含在 \(q\) 的两个方向中，故恰为
\[
\begin{aligned}
(q-1)c&\equiv b-qa\pmod v
&&\Longleftrightarrow q(a+c)=b+c,\\
(q-1)c&\equiv a-qb\pmod v
&&\Longleftrightarrow q(b+c)=a+c. \tag{10.7}
\end{aligned}
\]
这证明 (15.20) 没有把 full lift 偷换成 \(c\bmod7\)。逐对将
\(A_*=\{0,1,3,11\}\) 代入 (10.7) 并模 \(7\) 化简，得到 (15.21) 的十二个
方向 residue；其值也与 (15.15) 中“两个 seeds 位于同一 \(H\) 或 \(-H\)
cell”的行一致。独立代入的完整结果是
\[
\begin{array}{c|cc|cc}
\{a,b\}&\multicolumn{2}{c|}{q\equiv2\pmod7}
       &\multicolumn{2}{c}{q\equiv4\pmod7}\\
&q(a+c)=b+c&q(b+c)=a+c&q(a+c)=b+c&q(b+c)=a+c\\ \hline
\{0,1\}&1&5&5&1\\
\{0,3\}&3&1&1&3\\
\{0,11\}&4&6&6&4\\
\{1,3\}&1&2&2&1\\
\{1,11\}&2&0&0&2\\
\{3,11\}&5&2&2&5
\end{array} \tag{10.8}
\]

线性同余的解数由
\[
\gcd(q-1,v)=\gcd(q-1,q^2+q+1)=\gcd(q-1,3)=:g \tag{10.9}
\]
控制。若 \(g=1\)，每个方向恰有一个 full lift。若 \(g=3\)，则
\(q\equiv1\pmod3\)，右端 \(b-qa\) 可除以 \(3\) 当且仅当
\(b-a\equiv0\pmod3\)；在 \(A_*\) 的六对中只有 \(\{0,3\}\)，此时每个
方向有三个 lifts，其余方向无解。

两个方向不会共享解。若同一个 \(c\) 同时满足它们，写
\(A=a+c,B=b+c\)，则 \(qA=B\) 且 \(qB=A\)，从而 \(q^2A=A\)。
结合 \(q^3A=A\) 得 \(qA=A\)，于是 \(A=B\)，与 \(a\ne b\) 矛盾。
因此 (15.20) 的两个解集确实不重叠。

fixed subgroup 是 \((q-1)x=0\) 的解集，故由 (10.9)
\[
\operatorname{Fix}_{C_v}(q)=
\begin{cases}
\{0\},&q\not\equiv1\pmod3,\\
\{0,v/3,2v/3\},&q\equiv1\pmod3.
\end{cases} \tag{10.10}
\]
第二种情形中 \(3\mid v\)，而本节始终有 \(7\mid v\)，所以
\(v/3,2v/3\) 也都在零 quotient fiber。每行 (15.15) 至多一个 seed 落在
零类；其 orbit 若 fixed 就贡献 \(1\)，否则贡献 \(3\)。非零 cell 中每个
不同 full-group orbit 对三个相应 fibers 各贡献一点。因此
\[
\epsilon(c)\in\{0,1,3\},\qquad
\beta(c)=|S_H/{\sim_c}|,\qquad
\gamma(c)=|S_{-H}/{\sim_c}|,
\]
并无条件有 \(0\le\epsilon,\beta,\gamma\le3\)。

三 seed cell 也没有漏项。对所有 \(q\)，用 (10.7) 形成的 orbit 等价类
已经精确处理任意两两或三重重合。另在 \(q\ge12\) 时可排除三个不同 seeds
处于同一 orbit：若循环命名为 \(a,b,d\)，则必有
\[
d-b\equiv q(b-a)\pmod v.
\]
但
\[
|q(b-a)-(d-b)|\le11q+11<v
\]
迫使整数等式 \(d-b=q(b-a)\)，而
\(|d-b|\le11<q\le|q(b-a)|\)，矛盾。因此无界范围内三-label cell 只有
三类（无碰撞）或两类（单次 pair collision）；小参数仍由精确等价类定义
覆盖，不能靠这个简化越界。

### 10.4 Theorem 15.3：显式无界族及统一 all-\(c\) 支配

令 \(t=14r+9\)、\(q=t^2\)。则 \(t\equiv2\pmod7\)，所以
\(q\equiv4\pmod7\) 且 \(7\mid v\)。在 (10.5) 取
\[
a=2t,\qquad d=0.
\]
此时
\[
a^2+7d^2=4t^2=4q,\qquad
a+2(q+1)=2(t^2+t+1)\equiv0\pmod7,
\]
故 Proposition 15.1 的双向参数化直接给
\[
x=\frac{t^2-6t+1}{7},\qquad
y=z=\frac{t^2+t+1}{7}. \tag{10.11}
\]
这同时证明整数性、cardinality 和全部七个 Hall shifts，不调用素数分布、
PDS 存在性或有限搜索。

在最小值 \(t=9\) 时 \((x,y,z)=(4,13,13)\)。沿
\(t\mapsto t+14\)，三个分子的增量分别为
\[
28t+112>0,\qquad 28t+210>0,
\]
所以对每个 \(r\ge0\)，\(x,y,z\ge4\)。另一方面 §10.3 已对**每个 full
lift** \(c\in C_v\) 符号证明
\(\epsilon(c),\beta(c),\gamma(c)\le3\)。因此同一个 (10.11) 对所有 \(c\)
同时满足
\[
x\ge\epsilon(c),\qquad y\ge\beta(c),\qquad z\ge\gamma(c).
\]
由于每个 \(t=14r+9\) 为奇数且严格增大，这是一族无界奇数阶参数，不会被
独立的 even-order obstruction 吸收。结论严格止于：这套完整单一
\(C_7\) weighted relaxation 在该族上、对所有候选规范化平移均可行。

`research/code/audit_c7_quotient.py` 不参与上述证明。它只在
\(2\le q\le300\) 比较直接 triples 与参数 triples、在同一有限范围逐个
枚举 \(c\) 检查 lower bound，并只对 \(0\le r\le20\) 检查显式族。它是
公式和实现的 finite sanity check；即使全部断言通过，也不能证明可逆
参数化、全 lift 分类、无界性或 all-\(c\) theorem。

### 10.5 有边界的一手 novelty 审计

本轮只对三项候选贡献作定向审计：

1. exact orbit-reduced \(C_7\) 参数化 (15.4)--(15.5)；
2. 指定 \(A_*\) 的 collision-sensitive full-lift lower bounds
   (15.15)--(15.22)；
3. 同一个 vector 支配所有 translates 的无界族 (15.23)--(15.26)。

核读的一手原文及其覆盖边界如下。

- **Hall 1947, printed p. 1084, Corollary 4.2**
  （`research/sources/hall-1947-cyclic-projective-planes.txt`,
  OCR 341--387）：对任意 \(K\mid N\) 定义模 \(K\) 的纤维数 \(b_i\)，并
  逐系数给出 \(\sum b_i=n+1\)、零 shift 平方和及每个非零 shift 的
  \(\sum b_ib_{i+\nu}=s\)。这精确覆盖 §15 使用的全部 quotient
  autocorrelations，故该输入为 **known**。Corollary 4.3（同文件
  OCR 388--406）专门取 \(K=3\)，不是当前 \(C_7\) orbit reduction。
  本轮在 Hall 原文中未定位到 (15.4)--(15.5)、指定集合 full-lift
  collision table 或 all-translate feasibility family。

- **Gordon, “Some Restrictions on Orders of Abelian Planar Difference
  Sets,” author manuscript dated 1997-10-08, pp. 1--2**
  （`research/sources/gordon-planar-multipliers.txt`, OCR 23--38）：
  原文定义 multiplier 为把 \(D\) 送到一个 translate 的 automorphism，并
  明述 First Multiplier Theorem：planar abelian difference set 的 order
  \(n\) 的每个 divisor 都是 numerical multiplier。这认证 §15 的
  multiplier 输入为 **known**；其论文目标是用 multipliers 排除 orders，
  没有在核读段落中给出本节三项精确包装。

- **Ryser 1973, “Variants of Cyclic Difference Sets,” printed
  pp. 48--49, Theorem 3.1**
  （`research/sources/ryser-1973-variants-cyclic-difference-sets.txt`,
  OCR 135--200）：对象明确是 *near difference sets of type 2*，
  假设 \(v=3m\)，结论是三元二次方程
  \(x^2=ky^2-\lambda(m-1)z^2\) 有非平凡整数解。它既不是 planar
  difference set 的 \(C_7\) 全 shift 系统，也不含 prescribed subset 或
  all-\(c\) lower bounds，不能作为 §15 三项的先例。

- **Alexeev--Mixon, arXiv:2510.19804v2 (2026-01-16)**：
  Theorems 8--9（`research/sources/alexeev-mixon-2025.txt`, OCR
  178--211）分别处理 \(\{1,2,4,8\}\) 的 prime-parameter 非嵌入和
  五点集 \(\{1,2,4,8,13\}\) 的任意有限 PDS 非嵌入。原文自己在 OCR
  219--224 说明 Sections 4--5 是 projective-plane 与 polarity 路线；
  Section 4 的 Lemma 13（OCR 330--377）把 PDS translates 作为 lines，
  Section 5 从 polarity 开始（OCR 414--425）。它们没有给出网页检索代理
  所声称的 multiplier-orbit quotient fibers、collision-sensitive lower
  bounds 或 “Lemmas 14--18 all-translate orbit closure”。该网页归因与
  本地一手原文矛盾，已拒绝。原文 Future directions（OCR 760--766）仍问
  最小 forbidden Sidon set 的大小和分类，也没有认证当前 \(A_*\) 的
  \(C_7\) 包装。

因此 novelty 分类只能是：

| 内容 | novelty verdict |
|---|---|
| Hall 全部 cyclic-quotient autocorrelations；planar multiplier 输入 | **known** |
| (15.4)--(15.5) 的 exact labeled orbit-reduced \(C_7\) 参数化 | **novelty-unverified** |
| (15.15)--(15.22) 对固定 \(A_*\) 的 collision-sensitive full-lift lower bounds | **novelty-unverified** |
| (15.23)--(15.26) 的同一-vector all-translate 无界 feasibility family | **novelty-unverified** |
| 上述后三项中的任何一项 | **不是 verified-new**：有界阴性检索不能证明原创 |

这是一项有边界的一手审计，不是对全部设计论书籍、非数字化旧文献和所有
引用链的系统综述。“在所核来源中未定位到”不得改写成“文献中不存在”。

### 10.6 L35 五轴独立终表

| 轴 | 判定 |
|---|---|
| correctness | **verified natural-language**。§§10.2--10.4 独立核对全部 shift 的归约及恢复、\((a,d)\) 参数化正反方向、full-lift 两个碰撞同余、\(\gcd(q-1,v)\)、两方向不重合、fixed subgroup、三 seed 情形、显式无界族和同一 vector 的 all-\(c\) 支配。有限脚本只作 sanity check。 |
| novelty | **mixed**。Hall quotient equations 与 multiplier 输入为 **known**；精确 orbit-reduced \(C_7\) 参数化、collision-sensitive lower bounds 和 all-translate family 在本轮核读来源中未定位到先例，但只能保持 **novelty-unverified**，没有 `verified-new` 结论。 |
| significance | 给出完整 multiplier-coupled 单一 \(C_7\) integer relaxation 的严格终端方法边界：即使加入 \(A_*\) 的精确 same-translate orbit lower bounds，该 relaxation 在一个显式无界奇数族上仍对每个 \(c\) 可行。这能阻止继续把同一松弛误当成普遍 obstruction，但不是新的 PDS 存在或不存在结果。 |
| scope | Proposition 15.1 与 Theorem 15.2 覆盖每个整数 \(q\ge2\) 且 \(q\equiv2,4\pmod7\)、每个任意循环 PDS 的零和规范化必要像以及每个 \(c\in C_v\)；无 prime-power/Singer 假设。Theorem 15.3 的 terminal feasibility 只覆盖 \(q=(14r+9)^2\)、\(r\ge0\) 的显式无界族。 |
| limitations | 可行 weighted fibers 不选择原群点，也不保证每个非零有序差逐点恰出现一次或多个 quotients 可共同 lift。L35 不构造 PDS、不嵌入 \(A_*\)、不证明 \(A_*\) 非嵌入、不解决任意循环 PDS 中的真实 \(A_*\) 问题或 \((F_4)\)，未形式化，且 novelty 未认证；因此未达到 `publishable` 项目目标。 |

### 10.7 CLAIM_LEDGER 一致性与 bounded gate

一手核读没有实质改变 L35 的现有分类：其已把 Hall quotient equations 与
multiplier theorem 记作 known inputs，把精确参数化、碰撞表和 all-translate
方法边界记作 novelty-unverified，并明确不作 PDS 构造、\(A_*\) 非嵌入或
publishable 完成声明。因此本轮**不修改** `CLAIM_LEDGER.md` L35，避免把
阴性检索错误升级成 `verified-new`。

未发现 Proposition 15.1 或 Theorems 15.2--15.3 的实质数学错误，且
statement-fidelity、correctness、source/novelty、significance、scope 与
limitations 均已分别审计，故 L35 的 bounded review gate 标记
`complete`。这个 `complete` 只关闭当前 review item；真实 \(A_*\) lift、
\((F_4)\) 和项目 `publishable` 目标仍开放。下一步若继续研究，必须加入
跨 quotient 共同 lifting 或保留原群逐点有序差唯一性的机制，而不能把
本节的 weighted feasibility 改写成最终数学解答。
