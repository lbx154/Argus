# Figure Studio — Argus 论文画图流水线整合（v1 Figma token）

把 Argus 现有的画图 skill（Research Visualization Router、Paper Framework Figure
Studio、`figure_tool.py` 的 `argus-image2-paper-prompt-v1` Figma 风 token、PPT Master、
Paper Chart Styling）接成一条可自动跑的流水线，并附 5 个 CCF-A 场景的
A（Argus 原状）| B（第一轮优化）| C（本流水线）对比图，以及两份对 Argus
论文生产全流程的审计。

## 目录

- `studio/` — 流水线本体
  - `build_figure.py build-all` — 契约 JSON → SVG → 质量门禁 → 原生可编辑 PPTX/PNG/PDF
  - `figma_tokens.py` — v1 Figma token（#fbfaf7 暖白底、#1f2933 2px 描边、圆角 12/16、六色浅色块）
  - `figma_figure_renderer.py` — 分层布局 + 正交走线 + 压力式压缩（内边距→换行→间距→字号）
  - `figure_quality_gate_v2.py` — 字号/重叠/走线/裁切门禁（阈值不可放松）
  - `pptmaster_bridge.py` — SVG → PPT Master 原生 PPTX（0 张图片）
  - `scenario_5_ablation_studio.py` — 数据图路线（paper_chart_style + v1 token）
  - `contracts/*.json` — 4 个示意图契约；`figure_contract.schema.json`
  - `CONVENTIONS.md` — 风格与契约规范；`REPORT_FINAL.md` — 最终报告
  - `scenario_*.{svg,pptx,png}` — C 组成品；`out/<id>/quality/` — 门禁与回执
- `comparison/scenario_{1..5}_*_ABC.png` — 三列对比图
- `AB_TEST_SUMMARY.md` — A/B 阶段说明
- `audit/` — Argus 论文生产全流程审计
  - `RUN_AUDIT.md` — 对 `ai-research-open-20260902` 真实运行的证据审计
  - `RUNTIME_AUDIT.md` — 对 research vertical 代码（stages/paper/review）的审计
  - `PAPER_PRODUCTION_REVIEW_20260902.md` — 更早一轮的生产能力审查

## 运行

```bash
export PPT_MASTER_HOME=~/.argus-skill/tools/ppt-master/skills/ppt-master   # 默认值
export FIGURE_STUDIO_PYTHON=/path/to/python   # 需含 python-pptx、cairosvg、matplotlib、jsonschema、Pillow；默认当前解释器
cd contrib/figure-studio/studio
python build_figure.py build-all                # S1–S4，输出到 studio/out 与 studio/
python scenario_5_ablation_studio.py            # S5 数据图
python make_comparison_abc.py                   # 需 A/B 组 PNG（未随仓库提交）
```

`scenario_5_ablation_studio.py` 默认从仓库内
`argus_skill/verticals/research/skills/engineer/figure_spec_scripts/paper_chart_style.py`
读取样式，可用 `PAPER_CHART_STYLE` 覆盖。

## 已知限制

- 尚未接入 Argus runtime 的 Paper 阶段自动步骤；当前是独立可调用的工具链。
- S5 没有契约 JSON，不在 `build-all` 的确定性检查范围内。
- `make_comparison_abc.py` 依赖 A/B 组输出目录（本地 `argus_figure_test/{baseline,optimized}`），未提交。
