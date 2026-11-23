# ICML / ICLR 理论范例论文（2018–2026）

> 43 篇论文的 arXiv 编号与题目已于 2026-11-20 通过 arXiv API 核对一致；会议、年份和获奖/Oral 标签中标 ◻ 的条目未逐一核实，引用前打开原文确认。


说明：标 ✅ 的条目已在本次网络检索中核对（标题 + 会议 + 年份 + 链接）；标 ◻ 的条目依据已知的 arXiv 编号和会议信息列出，引用前建议再点开链接确认。奖项只在有把握时标注。

## A. 优化与过参数化

1. **ICLR 2018 (Best Paper)** — *On the Convergence of Adam and Beyond* — https://arxiv.org/abs/1904.09237 ◻
   - 问题：Adam 真的收敛吗？
   - 技巧：构造一个一维在线凸优化反例，让 Adam 收敛到错误的点；修复方法 AMSGrad 只需让二阶矩取 max。
   - 为何被接受：一个简单反例推翻了大家默认成立的结论，修复只有一行代码。
2. **ICLR 2019** — *Gradient Descent Provably Optimizes Over-parameterized Neural Networks* (Du et al.) — https://arxiv.org/abs/1810.02054 ✅
   - 问题：随机初始化的 GD 为什么能把两层 ReLU 网络训练到零损失？
   - 技巧：宽度足够时 Gram 矩阵在训练中几乎不变，于是动力学近似线性，损失按线性速率收敛（NTK 雏形）。
   - 为何被接受：证明核心只有“最小特征值 > 0 + 扰动界”，是此后整条 NTK 方向的起点。
3. **ICML 2019** — *A Convergence Theory for Deep Learning via Over-Parameterization* (Allen-Zhu, Li, Song) — https://arxiv.org/abs/1811.03962 ◻
   - 问题：深层网络（含 ResNet）在过参数化下能否被 SGD 全局优化？
   - 技巧：证明初始化附近满足半光滑性和梯度下界（类 PL 条件）。
   - 为何被接受：把两层网络的结论推广到多层，所用框架通用。
4. **ICML 2019** — *Fine-Grained Analysis of Optimization and Generalization for Overparameterized Two-Layer Neural Networks* (Arora et al.) — https://arxiv.org/abs/1901.08584 ◻
   - 问题：为什么真实标签学得快、随机标签学得慢，而且前者泛化好？
   - 技巧：把训练动力学投影到 NTK Gram 矩阵的特征方向上，得到依赖 y^T H^{-1} y 的泛化界。
   - 为何被接受：一个量同时解释了收敛速度和泛化，并用实验验证。
5. **ICML 2018** — *On the Optimization of Deep Networks: Implicit Acceleration by Overparameterization* (Arora, Cohen, Hazan) — https://arxiv.org/abs/1802.06509 ◻
   - 问题：加深线性网络会不会反而加速优化？
   - 技巧：深度线性网络的端到端矩阵动力学等价于带动量/自适应预条件的 GD。
   - 为何被接受：toy 模型简单，结论反直觉。
6. **ICLR 2021 (Oral；据记忆为 Outstanding Paper)** — *Optimal Rates for Averaged Stochastic Gradient Descent under Neural Tangent Kernel Regime* (Nitanda, Suzuki) — https://arxiv.org/abs/2006.12297 ✅（Oral 已确认）
   - 问题：NTK 区间内的平均 SGD 能否达到 minimax 最优速率？
   - 技巧：把网络学习化归为 RKHS 中的平均 SGD，借用核回归的经典最优速率分析。
   - 为何被接受：嫁接现成的经典理论，得到干净的最优结果。
7. **ICML 2023 (Outstanding Paper)** — *Learning-Rate-Free Learning by D-Adaptation* (Defazio, Mishchenko) — https://arxiv.org/abs/2301.07733 ✅（获奖已确认）
   - 问题：不调学习率，能否拿到最优的凸收敛速率？
   - 技巧：在线维护到最优点距离 D 的下界估计，并证明这个估计单调增、不会超过真实值。
   - 为何被接受：思路就是“估计 D”，同时有理论最优性和实际可用性。

## B. 隐式偏置（Implicit Bias）

8. **ICLR 2018** — *The Implicit Bias of Gradient Descent on Separable Data* (Soudry et al.) — https://arxiv.org/abs/1710.10345 ◻
   - 问题：在可分数据上，logistic 回归的 GD 最终收敛到哪个方向？
   - 技巧：分析权重范数以 log t 速度增长时的方向，证明它收敛到 hard-margin SVM 解，速率为 1/log t。
   - 为何被接受：线性模型加一个漂亮的结论，开启了隐式偏置研究。
9. **ICLR 2019** — *Gradient Descent Aligns the Layers of Deep Linear Networks* (Ji, Telgarsky) — https://arxiv.org/abs/1810.02032 ◻
   - 问题：深度线性网络在可分数据上，各层会怎样演化？
   - 技巧：利用层间平衡不变量 W_{i+1}^T W_{i+1} − W_i W_i^T 守恒，推出各层趋于秩一并相互对齐。
   - 为何被接受：用一个守恒量就推出了强结论。
10. **ICLR 2020 (Oral)** — *Gradient Descent Maximizes the Margin of Homogeneous Neural Networks* (Lyu, Li) — https://arxiv.org/abs/1906.05890 ◻
    - 问题：把“GD 收敛到最大间隔”从线性模型推广到齐次网络。
    - 技巧：构造平滑化的归一化 margin 作为单调递增的势函数，再结合 KKT 条件。
    - 为何被接受：势函数加齐次性就给出了一般性结论。
11. **ICLR 2022** — *What Happens after SGD Reaches Zero Loss? A Mathematical Framework* (Li, Wang, Arora) — https://arxiv.org/abs/2110.06914 ◻
    - 问题：SGD 在零损失流形上还会继续漂移到哪里？
    - 技巧：在流形附近用 Katzenberger 慢-快 SDE 极限，得到朝着降低 Hessian 迹方向的漂移。
    - 为何被接受：借来一个现成的随机分析工具，统一解释了 SGD 偏好平坦解。
12. **ICLR 2023** — *How Does Sharpness-Aware Minimization Minimize Sharpness?* (Wen, Ma, Li) — https://arxiv.org/abs/2211.05729 ◻
    - 问题：SAM 实际最小化的是哪种 sharpness？
    - 技巧：同样做零损失流形上的慢-快分解，证明 full-batch SAM 最小化最大特征值，而 1-SAM 最小化迹。
    - 为何被接受：用一个干净的区分回答了实践中的疑问。
13. **ICML 2019** — *On the Spectral Bias of Neural Networks* (Rahaman et al.) — https://arxiv.org/abs/1806.08734 ◻
    - 问题：神经网络为什么先学低频成分？
    - 技巧：对 ReLU 网络做傅里叶分析（分段线性函数的频谱衰减），再用合成实验验证。
    - 为何被接受：先给出风格化的分析，再用 toy 实验确认，结论直观。

## C. Edge of Stability / 大学习率

14. **ICLR 2021** — *Gradient Descent on Neural Networks Typically Occurs at the Edge of Stability* (Cohen et al.) — https://arxiv.org/abs/2103.00065 ✅
    - 问题：全批量 GD 的 sharpness 会怎样变化？
    - 技巧：以实证为主；在各种设置下测量 Hessian 最大特征值，发现它稳定在 2/η 附近。
    - 为何被接受：一个可复现的普适现象，直接挑战经典的 L-光滑假设，属于“现象型理论”论文。
15. **ICML 2022** — *Understanding Gradient Descent on Edge of Stability in Deep Learning* (Arora, Li, Panigrahi) — https://arxiv.org/abs/2205.09745 ◻
    - 问题：EoS 区间内，GD 隐式地在做什么？
    - 技巧：证明 GD 近似于在 sharpness 上做投影梯度流（对归一化 GD 或 √loss 分析）。
    - 为何被接受：把现象变成了可证明的隐式正则化。
16. **ICLR 2023** — *Self-Stabilization: The Implicit Bias of Gradient Descent at the Edge of Stability* (Damian, Nichani, Lee) — https://arxiv.org/abs/2209.15594 ◻
    - 问题：GD 为什么在 sharpness 超过 2/η 后不会发散？
    - 技巧：沿顶部特征向量做三阶 Taylor 展开，振荡通过 ∇S 产生负反馈，把 sharpness 拉回来。
    - 为何被接受：一个三阶展开就解释了整个机制。
17. **ICLR 2023** — *Understanding Edge-of-Stability Training Dynamics with a Minimalist Example* (Zhu et al.) — https://arxiv.org/abs/2210.03294 ✅
    - 问题：能否找到一个最小的可严格分析的 EoS 模型？
    - 技巧：用 4 层标量网络（带耦合项）做 toy 模型，在大局部区域内严格刻画动力学。
    - 为何被接受：典型的“最简 toy 模型能复现现象”式论文。

## D. 泛化 / 良性过拟合 / 特征学习

18. **ICML 2018** — *Stronger Generalization Bounds for Deep Nets via a Compression Approach* (Arora, Ge, Neyshabur, Zhang) — https://arxiv.org/abs/1802.05296 ◻
    - 问题：如何得到不随参数量爆炸的泛化界？
    - 技巧：证明训练好的网络对噪声稳定、因而可以被压缩，再对压缩后的网络使用计数界。
    - 为何被接受：“可压缩即可泛化”这个思路非常简单。
19. **ICML 2021** — *Tensor Programs IV: Feature Learning in Infinite-Width Neural Networks* (Yang, Hu) — https://arxiv.org/abs/2011.14522 ◻
    - 问题：NTK 极限下没有特征学习，什么样的参数化能在无限宽时保留特征学习？
    - 技巧：分析参数化的 abc 尺度空间，指出 μP（最大更新参数化）这个唯一的极值点。
    - 为何被接受：结论是一条可操作的缩放规则（后来用于 μTransfer）。
20. **ICLR 2023 (Notable top 5%)** — *Towards Understanding Ensemble, Knowledge Distillation and Self-Distillation in Deep Learning* (Allen-Zhu, Li) — https://arxiv.org/abs/2012.09816 ◻
    - 问题：集成和蒸馏为什么在深度学习中有效？
    - 技巧：构造“multi-view”数据分布，在两层 CNN 上分析不同随机种子分别学到哪些特征。
    - 为何被接受：用一个风格化的数据假设解释了一个流行的实践。
21. **ICLR 2025 (Outstanding Paper)** — *Learning Dynamics of LLM Finetuning* (Ren, Sutherland) — https://arxiv.org/abs/2407.10490 ✅（获奖已确认）
    - 问题：微调一个样本时，其他回答的概率会怎样变化？
    - 技巧：对单步更新做 eNTK 式的一阶分解（AKG 分解），解释 DPO 中的“squeezing effect”。
    - 为何被接受：只用一步线性化就解释了大模型的现象，并用实验确认。

## E. Grokking

22. **ICLR 2023 (Oral)** — *Progress Measures for Grokking via Mechanistic Interpretability* (Nanda et al.) — https://arxiv.org/abs/2301.05217 ◻
    - 问题：grokking 过程中网络内部发生了什么？
    - 技巧：在单层 transformer 上做模加法，逆向出离散傅里叶加三角恒等式的算法，并定义连续的进度指标。
    - 为何被接受：toy 任务加完全的机制解释。
23. **ICLR 2023** — *Omnigrok: Grokking Beyond Algorithmic Data* (Liu, Michaud, Tegmark) — https://arxiv.org/abs/2210.01117 ◻
    - 问题：grokking 只出现在算法数据上吗？
    - 技巧：用“LU 机制”解释：损失随权重范数呈 L 形（训练）和 U 形（测试），大初始化再加上权重衰减就会诱发 grokking。
    - 为何被接受：一张示意图就把机制讲清楚。
24. **ICLR 2024** — *Dichotomy of Early and Late Phase Implicit Biases Can Provably Induce Grokking* (Lyu et al.) — https://arxiv.org/abs/2311.18817 ✅
    - 问题：能否严格证明 grokking 的存在？
    - 技巧：齐次网络、大初始化加小权重衰减：早期处于 kernel 区间，晚期转向 min-norm/max-margin 隐式偏置，两者的切换就是 grokking。
    - 为何被接受：把已有的隐式偏置结果拼接起来就得到了证明。
25. **ICLR 2024** — *Grokking as the Transition from Lazy to Rich Training Dynamics* (Kumar et al.) — https://arxiv.org/abs/2310.06110 ◻
    - 问题：grokking 和 lazy/rich 训练区间有什么关系？
    - 技巧：在多项式回归 toy 模型上，用初始化尺度控制 lazy→rich 的转变。
    - 为何被接受：一个旋钮（初始化尺度）就复现了现象。

## F. In-Context Learning 理论

26. **ICLR 2022** — *An Explanation of In-context Learning as Implicit Bayesian Inference* (Xie et al.) — https://arxiv.org/abs/2111.02080 ◻
    - 问题：预训练为什么会涌现出 ICL？
    - 技巧：假设预训练数据由 HMM 混合生成，ICL 就等价于对隐概念做后验推断，并构造 GINC 合成数据集验证。
    - 为何被接受：用贝叶斯视角加一个风格化的数据假设给出解释。
27. **ICLR 2023 (Oral)** — *What Learning Algorithm Is In-Context Learning? Investigations with Linear Models* (Akyürek et al.) — https://arxiv.org/abs/2211.15661 ◻
    - 问题：transformer 在上下文中实际执行的是哪种算法？
    - 技巧：显式构造 transformer 权重来实现 GD 或闭式岭回归，再用探针实验比对。
    - 为何被接受：把问题化归为线性回归，并给出构造性证明。
28. **ICML 2023** — *Transformers Learn In-Context by Gradient Descent* (von Oswald et al.) — https://arxiv.org/abs/2212.07677 ✅
    - 问题：ICL 是不是在前向传播中做梯度下降？
    - 技巧：构造线性自注意力的权重，使其等价于一步 GD，并证明训练后的权重确实收敛到这个构造。
    - 为何被接受：一个等式（LSA = 一步 GD）就给出了结论。

## G. Transformer 表达能力

29. **ICLR 2019** — *On the Turing Completeness of Modern Neural Network Architectures* (Pérez, Marinković, Barceló) — https://arxiv.org/abs/1901.03429 ◻
    - 问题：Transformer 是否图灵完备？
    - 技巧：在任意精度、hard attention 的条件下，显式构造权重来模拟图灵机。
    - 为何被接受：用构造性证明回答了一个基本问题。
30. **ICLR 2020** — *Are Transformers Universal Approximators of Sequence-to-Sequence Functions?* (Yun et al.) — https://arxiv.org/abs/1912.10077 ◻
    - 问题：Transformer 能否逼近任意连续的序列到序列函数？
    - 技巧：量化输入后，用自注意力实现“上下文映射”（给每个 token 一个唯一 ID），再用 FFN 查表。
    - 为何被接受：经典万能逼近套路搬到 Transformer 上，证明步骤清楚。
31. **ICLR 2023 (Oral)** — *Transformers Learn Shortcuts to Automata* (Liu et al.) — https://arxiv.org/abs/2210.10749 ◻
    - 问题：浅层 transformer 如何模拟长度为 T 的自动机？
    - 技巧：利用 Krohn–Rhodes 分解和半群并行前缀，证明 O(log T) 层就够，可解群的情况下甚至只需常数层。
    - 为何被接受：借用代数自动机理论，结论简单而深刻。
32. **ICLR 2024** — *The Expressive Power of Transformers with Chain of Thought* (Merrill, Sabharwal) — https://arxiv.org/abs/2310.07923 ◻
    - 问题：CoT 步数如何影响 transformer 的计算能力？
    - 技巧：电路复杂度刻画：log 步仍不够强，线性步可识别正则语言，多项式步等于 P。
    - 为何被接受：只用一个参数（步数）就给出了完整的复杂度刻画。
33. **ICLR 2024** — *Chain of Thought Empowers Transformers to Solve Inherently Serial Problems* (Li, Liu, Zhou, Ma) — https://arxiv.org/abs/2402.12875 ◻
    - 问题：CoT 为什么能提升表达能力？
    - 技巧：常数深度 transformer 不带 CoT 时属于 AC0/TC0，带 T 步 CoT 后可模拟大小为 T 的电路。
    - 为何被接受：用电路下界加构造，论证非常直接。
34. **ICLR 2026 (Outstanding Paper)** — *Transformers are Inherently Succinct* (Bergsträßer, Cotterell, Lin) — https://arxiv.org/abs/2510.19315 ✅（获奖已确认）
    - 问题：transformer 的优势是否在于描述的简洁性，而不只是表达能力？
    - 技巧：构造一类语言族，证明 transformer 的描述比 LTL/RNN 指数级更短、比自动机双指数级更短，进而推出验证问题是 EXPSPACE 完全的。
    - 为何被接受：换了一个衡量维度（succinctness），纯理论文章也拿到了 outstanding。
35. **ICLR 2023 (Outstanding Paper)** — *Rethinking the Expressive Power of GNNs via Graph Biconnectivity* (Zhang et al.) — https://arxiv.org/abs/2301.09505 ◻
    - 问题：WL 层级之外，该用什么度量 GNN 的表达能力？
    - 技巧：以双连通性为判据，证明大多数 GNN 都无法判断割点/割边，并提出 GD-WL。
    - 为何被接受：选用一个具体、可检验的图性质作为度量。

## H. 扩散模型理论

36. **ICLR 2023 (Notable top 5%)** — *Sampling is as easy as learning the score: theory for diffusion models with minimal data assumptions* (Chen et al.) — https://arxiv.org/abs/2209.11215 ◻
    - 问题：只要 score 估计得准，扩散采样就能保证质量吗？
    - 技巧：用 Girsanov 定理比较两条 SDE 路径的 KL，得到对维度多项式依赖的 TV 界。
    - 为何被接受：用一个标准工具，在最少的假设下得到端到端的保证。
37. **ICLR 2024** — *Nearly d-Linear Convergence Bounds for Diffusion Models via Stochastic Localization* (Benton et al.) — https://arxiv.org/abs/2308.03686 ◻
    - 问题：扩散模型收敛界对维度 d 的依赖能否降到近线性？
    - 技巧：借用随机局部化（stochastic localization）的视角来控制误差项。
    - 为何被接受：借来一个新工具，直接把界改进了。
38. **ICLR 2024 (Outstanding Paper)** — *Generalization in diffusion models arises from geometry-adaptive harmonic representations* (Kadkhodaie et al.) — https://arxiv.org/abs/2310.02557 ◻
    - 问题：扩散模型是在记忆训练数据，还是在泛化？
    - 技巧：在不相交的训练子集上训练两个模型，比较它们生成的样本；再把去噪器做局部线性化，分析其特征基（GAHB）。
    - 为何被接受：实验设计极简（两个子集、两个模型）、结论清楚。
39. **ICML 2025 (Outstanding Paper)** — *Train for the Worst, Plan for the Best: Understanding Token Ordering in Masked Diffusions* (Kim et al.) — https://arxiv.org/abs/2502.06768 ✅（获奖已确认）
    - 问题：masked diffusion 的训练难度和解码顺序有什么关系？
    - 技巧：构造困难实例，证明某些子问题在计算上不可解；推理时自适应选择解码顺序就能绕开它们，并用 Sudoku 验证。
    - 为何被接受：困难实例的下界加一个简单的推理改进，带来了很大的实际收益。

## I. 强化学习理论

40. **ICLR 2020** — *Is a Good Representation Sufficient for Sample Efficient Reinforcement Learning?* (Du, Kakade, Wang, Yang) — https://arxiv.org/abs/1910.03016 ◻
    - 问题：近似线性的表示是否足以保证样本高效？
    - 技巧：构造困难实例（大量近似正交的向量），得到指数级的样本下界。
    - 为何被接受：一个困难实例就否定了一个普遍的直觉。
41. **ICLR 2021** — *What are the Statistical Limits of Offline RL with Linear Function Approximation?* (Wang, Foster, Kakade) — https://arxiv.org/abs/2010.11895 ◻
    - 问题：离线 RL 在线性可实现、数据覆盖良好的条件下，能否做到样本高效？
    - 技巧：构造困难 MDP 族，证明需要指数于 horizon 的样本。
    - 为何被接受：用下界否定了一个看似合理的期望。
42. **ICML 2020** — *Reward-Free Exploration for Reinforcement Learning* (Jin, Krishnamurthy, Simchowitz, Yu) — https://arxiv.org/abs/2002.02794 ◻
    - 问题：先在没有奖励的情况下探索，之后能否对任意奖励给出近优策略？
    - 技巧：对每个状态单独当作奖励去探索，然后在收集到的数据上做规划，上下界匹配。
    - 为何被接受：新问题设定、算法简单。

## J. Scaling Laws 理论

43. **ICML 2024** — *A Dynamical Model of Neural Scaling Laws* (Bordelon, Atanasov, Pehlevan) — https://arxiv.org/abs/2402.01092 ✅
    - 问题：时间、模型规模和数据量的 scaling 指数从何而来？
    - 技巧：分析 GD 训练的随机特征模型加幂律谱数据，用 DMFT 求解动力学，得到不同的指数和计算最优分配。
    - 为何被接受：化归为可解的线性模型，就复现了大量经验规律。

---

### 共性 trick 速查

- 化归为线性模型 / 核方法（NTK、随机特征、线性 attention）：2, 4, 6, 28, 43
- 最简 toy 模型复现现象，再做实验确认：14, 17, 22, 23, 25, 38
- 守恒量 / 势函数：9, 10
- 局部 Taylor 展开或慢-快分解：11, 12, 16
- 显式构造权重：27, 28, 29, 30, 33
- 困难实例或反例给出下界：1, 39, 40, 41
- 借用其他领域的现成工具（Krohn–Rhodes、Girsanov、随机局部化、DMFT）：31, 36, 37, 43
