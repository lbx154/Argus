# Argus 论文画图 A/B 测试总结

日期：2026-09-03  目录：`/data/v-boxiuli/argus_figure_test/`

## 看图入口

| 场景 | A 基线 | B 优化 | A|B 并排 |
|---|---|---|---|
| 1 MARL 架构图 (AAAI) | `baseline/scenario_1_marl_architecture.png` | `optimized/scenario_1_marl_architecture.png` | `comparison/scenario_1_marl_architecture_AB.png` |
| 2 多头注意力流程图 (NeurIPS) | `baseline/scenario_2_attention_flow.png` | `optimized/scenario_2_attention_flow.png` | `comparison/scenario_2_attention_flow_AB.png` |
| 3 联邦学习时序图 (ICML) | `baseline/scenario_3_federated_protocol.png` | `optimized/scenario_3_federated_protocol.png` | `comparison/scenario_3_federated_protocol_AB.png` |
| 4 NAS 搜索空间 (CVPR) | `baseline/scenario_4_nas_search_space.png` | `optimized/scenario_4_nas_search_space.png` | `comparison/scenario_4_nas_search_space_AB.png` |
| 5 消融实验柱状图 (AAAI) | `baseline/scenario_5_ablation_results.png` | `optimized/scenario_5_ablation_results.png` | `comparison/scenario_5_ablation_results_AB.png` |

每张同目录下有 `.svg`（矢量源）；1–4 基线有 `.spec.json`。

## A 组怎么画的（Argus 现状）

- 1–4：Argus 自带 `figure_spec_scripts/figure_renderer.py`（JSON → SVG），规格由我按场景手写
- 5：matplotlib 脚本 `baseline/scenario_5_ablation.py`

## B 组怎么画的（优化后）

- 1–4：新写的 `optimized/paper_figure_renderer.py build-all`
- 5：改写的 `optimized/scenario_5_ablation.py`
- 全部过 `optimized/figure_quality_gate.py`（确定性检查）

## 优化了什么

| 维度 | A 基线 | B 优化 |
|---|---|---|
| 边标签 | 11px `#777777` 浅灰，缩到单栏不可读 | 13px `#3F3F3F` 白底垫；同源多边合并为一个标签 |
| 连线 | 节点中心到中心的斜线 | 正交折线，箭头精确落在节点边界端口 |
| 分组 | 无 | 虚线分组框 + 组标题（Decentralized Actors / Multi-Head Processing / Layer 1–4） |
| 标题 | 只是 metadata 不渲染 | 渲染在顶部 |
| 时序图 | 用普通节点图冒充，无生命线 | 真时序图：生命线、时间轴、编号消息、激活框、自消息 |
| NAS | 3 层（场景要 4 层）、每层候选数不一致、灰线杂乱 | 4 层 × 5 候选，非最优边淡化，最优路径加粗 |
| 配色 | 各场景各一套 | Okabe–Ito 色盲友好统一调色板 |
| 消融图 | 图例遮住最高柱、无数值、粗体标题 | 图例移出绘图区、逐柱数值、caption 式说明、hatch 兼顾灰度打印 |
| 质量检查 | 无 | gate：字号 <12px、节点/文本重叠、文本压节点、越界、边穿透非端点节点 |

## 质量 gate 结果（机械指标）

| | 通过 | 错误数 |
|---|---|---|
| A 基线 | 1/5 | 26（全部是 11px 字号；scenario 4 意外通过是因为它没有边标签） |
| B 优化 | 5/5 | 0 |

## 人工目检（我自己看 PNG 发现、gate 没抓到的）

B 组第一版有 4 处问题，已修复并重建：
1. scenario 2 "context vector" 标签压 Concat/Linear Output 边框 → 移到连线上方
2. scenario 1 "Decentralized Actors" 组标题压虚线 → 移入框内加白底
3. scenario 3 "Local Training" 被画成 Client1→Client3 消息（语义错）→ 改为激活框旁标注；"Broadcast/Upload" 只在末端有箭头 → 改为 3 条独立箭头；"Secure Aggregate" 与场景要求的 "Aggregate" 不一致 → 改回
4. gate 缺 text-on-node 检查 → 新增 `text_overlaps_node`，在修复前的 svg 上能报出 2 处

B 组终版仍可挑的地方（未改，留给你判断）：
- scenario 2 "context vector" 标签离它的箭头约 70px，关联感偏弱
- scenario 1 组标题的白底垫在浅灰组底色上隐约可见
- scenario 5 y 轴仍从 0 起（消融图这样最诚实，但 70–90 区间差异被压缩）
- scenario 2 画布上下留白偏多

## 关于"自动化程度"的如实说明

- B 组 1–4 的**布局（节点坐标、via 折点、标签位置）是手写规格**，写在 `paper_figure_renderer.py` 的 build-all 里，不是算法自动排的
- Codex 另写了 `optimized/auto_figurespec_generator.py`（自动分层布局），能跑通 scenario 1（8 节点 4 边），但 **B 组图没有用它**——它的输出质量没经过验证
- 真正提升的自动化是：一条命令 build-all 出 4 张 SVG → cairosvg 出 PNG → gate 出报告；以及正交连线、分组框、时序图这些原语不用再手画
- gate 是机械检查，证明不了"好看"——这次 4 处真问题全是我看图发现的，gate 只抓到字号

## 复现

```bash
cd /data/v-boxiuli/argus_figure_test
python3 optimized/paper_figure_renderer.py build-all --output-dir optimized
python3 optimized/scenario_5_ablation.py
for s in optimized/scenario_*.svg; do /home/v-boxiuli/.local/bin/cairosvg "$s" -o "${s%.svg}.png" -W 1536; done
python3 optimized/figure_quality_gate.py compare --baseline-dir baseline --optimized-dir optimized --output optimized/quality_report.json
python3 make_comparison.py
```
