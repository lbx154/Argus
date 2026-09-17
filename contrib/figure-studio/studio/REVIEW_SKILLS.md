# Studio × Argus 论文图技能集成审计

> 结论：**部分集成**。语义路由、真实 SciencePlots helper、真实 PPT Master checker/converter/round-trip 均已接通；但上游的证据→Design Spec→spec_lock 投影、SVG root contract、场景 5 正式合同和视觉迭代尚未闭环。以下 warning 以指定 receipt/gate 快照为证，不依赖并发编辑中的 renderer/gate 当前文本。

## 1. 技能要求矩阵

缩写：`RVR`=research-visualization-router.md；`PFFS`=paper-framework-figure-studio.md；`PCS`=paper-chart-styling.md；`PMA`=presentation-master.md；`SSC`=PPT Master references/shared-standards-core.md；`SSVG`=references/semantic-svg.md。

| 技能 | 规范强制/明确要求（短引文） | Studio 证据 | 状态 |
|---|---|---|---|
| Research Visualization Router | ① data/metric/uncertainty/ablation → Matplotlib/SciencePlots（RVR:22）<br>② conceptual/method/architecture → Framework Studio，通常 native PPTX（:23）<br>③ exact load-bearing topology → FigureSpec/Draw.io/Graphviz/browser SVG（:24）<br>④ 先写 one-sentence takeaway、逐项证据一致（:34–35）<br>⑤ 保留 editable/executable source、CVD-safe、出版字号（:36–40） | `build_figure.py:224,232–243` 按 `kind=data-chart` 分流，其余到 PPT；contracts 均有 `takeaway`、nodes/edges；`CONVENTIONS.md:20–43` 固化白底/字号/拓扑 gate。缺点：router 只有二分法，无 exact-topology override；`contracts/` 没有场景 5 合同，故 `build-all` 实际只发现 1–4。 | 部分 |
| Paper Framework Figure Studio | ① takeaway（PFFS:15）<br>② exact modules/labels/connections，含 source/target/direction/**boundary port/meaning**（:16–17）<br>③ 单一阅读方向、紧凑且暴露机制（:18–19）<br>④ 默认 PPT Master native objects，精确拓扑可换路（:20–21）<br>⑤ vector export + substantive caption/body reference（:22–23） | 合同有 takeaway、node label、edge from/to/kind，schema 限定 layout direction；PPTX/PDF/SVG 均在 receipt。缺 `source_port/target_port/meaning`；无 caption/body-reference 字段；`build_figure.py` 不管论文嵌入。技能还说“only editable source + final export / no provenance records”（:10–11,35–37），Studio 反而生成 receipt/gate/roundtrip，属于工具链扩展而非原样执行。 | 部分 |
| Paper Chart Styling + helper | ① 必须 SciencePlots/helper，缺依赖不得 plain-matplotlib fallback（PCS:32–39,115–119）<br>② ablation/per-component 用 single-column `figure_size`（:68–74,122–125）<br>③ colour + marker/linestyle 冗余、突出 Ours（:76–83）<br>④ 禁 jet、细 spine/grid；按 estimand、需要时显示 uncertainty（:85–93）<br>⑤ PDF/SVG/high-DPI、fonttype 42、终稿尺寸检查（:95–98,110–113） | `scenario_5...py:_load_style_helper/render` 真载入指定 helper；`:71–98` 用 colorblind、hatch、`highlight_ours`、std error bars；`:135–169` 同源导出/记录元数据。违例：`:71,74` 明确 `column="double"`；数据由 `generate_simulated_results()` 随机模拟而非读取权威结果；无终稿尺寸视觉检查记录。 | 部分 |
| FigureSpec | ① 仅 exact/editable topology 时用（FigureSpec:11–20）<br>② same spec byte-identical、spec 是 source of truth（:22–29,108–109）<br>③ `validate` 后 render（:90–98）<br>④ 检查 node penetration/CVD/print size（:100–107,131–135）<br>⑤ spec 与最终 SVG 并存（:148–152） | 本地 JSON contract + deterministic renderer + contract gate复刻了思想；但 `build_figure.py:24–27,244` 调的是自有 schema/`figma_figure_renderer.py`，未调用 Argus `figure_renderer.py validate/render`，也没有路由字段选择 FigureSpec。 | 部分（理念复刻，非技能调用） |
| Argus PPT Master adapter | ① conceptual figure 用 PPT Master，data chart 不用（PMA:12–25）<br>② pinned toolkit/注入解释器/绝对脚本路径（:27–65）<br>③ native DrawingML，禁整页 screenshot（:82–84）<br>④ paper 默认 `#ffffff`（:85–87）<br>⑤ 保留 artifacts，成功还须 fresh visual inspection（:88–89,109–114） | `pptmaster_bridge.py:25–30,357–453` 调真实 checker、SVG→PPTX、PPTX→SVG；receipt:103 为 pinned commit；PPTX inventory/gate 支持 native editability；tokens 白底。缺 fresh rendered visual-inspection/repair 证据。 | 部分 |
| Upstream PPT Master contract | ① Generate 必须先 audited `design_spec.md`，再投影 `spec_lock.md`（generate:155,203,227–243）<br>② flat root `<svg>` 恰有一个 canonical page-role（executor-base:20；SSVG:9,19）<br>③ 每个 visible direct root `<g>` 有 root-coordinate bounds（SSC:582–586）<br>④ logical Slide-local units 用 descriptive top-level `<g id>`，目标 3–8（SSC:586,590–614）<br>⑤ checker 0 errors；warning 非阻塞但可作质量建议（generate:395–410） | `pptmaster_bridge.ensure_project` 创建两 spec，build 做 checker/export/roundtrip；receipt 全步骤通过且 0 error。可是 spec 是每次硬编码：takeaway/节点/边未投影，且锁称 `icons: none` 而 SVG 实用 Tabler；指定 gate 仍有 4 类 PPT Master warning。 | 部分 |

## 2. 专项回答

### (a) PPT Master SVG / spec contract 与四类 warning

- **要求，但放置位置要纠正**：`data-pptx-page-role` 在 flat 页的根 `<svg>`，合法值仅 `cover|toc|section|content|ending`；本项目一页论文框架图应是 `data-pptx-page-role="content"`（SSVG:9,19；executor-base:20）。它不写在根 `<g>`。
- 每个“可见的直接根 `<g>`”必须写 `data-pptx-bounds="x y width height"`，四数均为 root/viewBox 坐标且 width/height 为正（SSC:584）。全画布 layer wrapper 可廉价用 `0 0 1280 720`，更佳是内容 tight union；nested `<g>` 不需要。指定 gate:111–125 的缺失与规范一致。
- ungrouped top-level Slide-local 内容应按逻辑单元包成 `<g id="descriptive-id" data-pptx-bounds="...">`；目标 3–8 个普通组。canvas background/装饰可留 root primitive（SSC:586），图例的线/字应合成一个 `legend` 组。
- `spec_lock.md` 对 Generate route 是 mandatory machine projection，不是可选 sidecar；Studio 虽有该文件，但 `pptmaster_bridge.py:_design_spec/_spec_lock` 是泛化常量，未从每幅合同投影。
- 四类 warning 都便宜：前三类在 `figma_figure_renderer.py` 的 root assembly/grouping 修；color drift 应按所有“实际使用的 color roles”更新 Design Spec 后投影到 `pptmaster_bridge.py:_spec_lock`（spec_lock_reference:21–32）。该快照具体缺 `#CBD5E1`、`#F0FAEE`（pptmaster_check.json:61–69）；不要只压 warning，也要把 `icons` 改为 `library: tabler-outline`、真实 inventory、`stroke_width: 2`。
- 注意：upstream 明定 warning advisory/non-blocking（generate:407），故当前 pass 判定合法；但 Hard rule 尚未满足，不能把“0 errors”表述成“完整 contract compliance”。

### (b) Router 路由是否匹配

- 是：Router 明确“所有 data/metric/result，包括 uncertainty/ablation”走 PCS（RVR:22），conceptual/method/architecture/teaser 走 PFFS，通常 PPT Master（:23）。因此场景 1–4 → PPT Master、场景 5 → Paper Chart Styling 的**语义选择正确**。
- 但执行闭环不匹配：`build_one` 只有 `data-chart`/默认 PPT 二分（build:224），没有 exact-topology route；且 `contracts/*.json` 没有场景 5，`build-all` 不会触发已写好的 data-chart 分支。

### (c) figure-contract / takeaway / label-count discipline

- PFFS 没定义名为 `figure-contract` 的 JSON schema，也没有数值化 “label-count” 规则；它定义的是更强的**内容清单纪律**：takeaway + exact modules/labels/connections + source/target/direction/port/meaning（PFFS:15–19）。
- Studio contracts 满足 takeaway、模块、标签、from/to、方向（由 from→to）的大部；指定 gate:38–52 还能验证重复标签计数（如 observation×4），这是 Studio 的有益加严。
- 仍缺 port 与 per-edge meaning，`kind` 只能粗略代替；场景 5 无正式 contract/takeaway，所以只能评为“部分遵循”。

### (d) 场景 5 逐项核对

| 项目 | 判定 | 证据/修复 |
|---|---|---|
| Font sizes | 部分 | AAAI double helper 给 font/axis≈9pt、ticks/legend≈8pt，合规；手写 bar value=7pt 仅踩最低线，低于 helper 所称 8–9pt print scale，建议 8pt。 |
| Palette | 满足 | `colorblind` + Ours 蓝、baselines 灰；无 jet/rainbow（scenario5:71,89,98）。 |
| Figure width | **违反** | ablation 应 `figure_size("single", venue="AAAI")`=约 3.3in；当前显式 double=6.9in（PCS:71；helper:105–109；scenario5:71,74）。若塞单栏会缩字约一半。 |
| Hatch/redundancy | 满足 | 四组 `"", "///", "\\\\", "xx"` 与颜色共同编码，符合 grayscale/CVD 冗余精神。 |
| Legend placement | 满足/条件风险 | 四列 legend 在 axes 上方且不遮数据，属 small legend；但 paper caption 已标识时应删 in-plot title（PCS:92–93），当前 title+legend 上方占高。 |
| Error bars | 满足 | 5-run sample std 以 `yerr`+caps 呈现（scenario5:41–61,87,93）；但模拟 RNG 不是论文权威实验输入。 |

## 3. Top 5 可执行缺口（按 CCF-A 终稿视觉影响）

1. **场景 5 尺寸语义错误**：单栏缩放会把 8–9pt 字压至约 4pt；改 `column="single"`，重排/短化 legend，bar value→8pt，caption 存在时去 title。文件：`studio/scenario_5_ablation_studio.py`。
2. **图 1 字号只过最低线**：receipt 有 6 个 7.10–7.49pt warning；将 typography 的出版目标改为 ≥8pt（178mm/1280 下至少 21px），再让布局吸收尺寸。文件：`studio/figma_tokens.py`。
3. **没有 exemplar→多方案→渲染批评→bounded repair**：固定一个 `layout.variant` 容易产生模板化/死空间；在渲染前生成 2–3 个布局候选并记录选择，导出后加 final-size visual review 回修 source。文件：`studio/build_figure.py`。
4. **连接合同不足以约束精确拓扑**：增加 `source_port`、`target_port`、`meaning`（及可选 topology route），避免共享 bus/反馈箭头语义靠 renderer 猜。文件：`studio/figure_contract.schema.json`。
5. **SVG/锁仍有可见一致性与编辑分组债务**：root `<svg>` 加 `page-role=content`、direct root groups 加 bounds、图例成组；Design Spec/spec_lock 声明全部实际颜色与 Tabler icon。文件：`studio/figma_figure_renderer.py`（metadata/grouping）与 `studio/pptmaster_bridge.py`（lock projection）。

## 4. 最终判断

Studio 已真实复用 Argus 的路由思想、Paper Chart helper 与 PPT Master 工具链，不是“名义集成”。但当前更像一个可靠的本地 deterministic renderer + exporter：上游技能要求的 evidence/confirmation/spec projection 和视觉选择/复修没有被编码成闭环。优先修 Top 1–2 可直接保护终稿可读性；Top 3–5 才能把“工具可用”提升为“技能契约真正落地”。
