#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the Argus VC pitch deck (landscape A4 slide PDF) with weasyprint.

Single-wedge narrative: land with verified evaluation / RL-environment
integrity (line A); engine = moat; data flywheel (B) = expansion; deliverables
(C) = cash-flow footnote. Honest, but in VC voice: lead with the wedge, frame
risk as a de-risking plan. Reuses the gpt-image-2 figures in bp_figures/.
"""
from __future__ import annotations
import pathlib, weasyprint

DOCS = pathlib.Path("/home/argustest/argus-skill/docs")
FIG = "bp_figures"

# accent palette (matches the figures)
BLUE, GREEN, ORANGE, PURPLE, YELLOW, INK = "#dcecff", "#e2f7df", "#ffe2d1", "#eadfff", "#fff2bd", "#1f2933"

def slide(accent, kicker, title, body_html, n):
    return f"""
<section class="slide">
  <div class="rail" style="background:{accent}"></div>
  <div class="pad">
    <div class="kicker">{kicker}</div>
    <h1>{title}</h1>
    <div class="body">{body_html}</div>
  </div>
  <div class="foot"><span>Argus · 机密 / Confidential</span><span>{n} / 13</span></div>
</section>"""

def ul(items):
    return "<ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>"

def figslide(accent, kicker, title, fig, takeaway, n, note=""):
    notehtml = f'<div class="fignote">{note}</div>' if note else ""
    return f"""
<section class="slide">
  <div class="rail" style="background:{accent}"></div>
  <div class="pad">
    <div class="kicker">{kicker}</div>
    <h1>{title}</h1>
    <div class="figwrap"><img src="{FIG}/{fig}"/></div>
    <div class="takeaway">{takeaway}</div>
    {notehtml}
  </div>
  <div class="foot"><span>Argus · 机密 / Confidential</span><span>{n} / 13</span></div>
</section>"""

slides = []

# 1 — Title
slides.append(f"""
<section class="slide title">
  <div class="pad center">
    <div class="logo">ARGUS</div>
    <div class="tagline">可信的自主研究引擎</div>
    <div class="sub">一台<b>结果不可作弊</b>的引擎 —— 因为它本就是为「绝不作弊地做研究」而造的。<br/>
    <span class="en">Results frontier labs can trust, because the agent can't cheat.</span></div>
    <div class="pill">种子轮 · 2026 · github.com/lbx154/argus-skill (MIT)</div>
  </div>
  <div class="foot"><span>Argus · 机密 / Confidential</span><span>1 / 13</span></div>
</section>""")

# 2 — The insight / thesis
slides.append(slide(BLUE, "THE INSIGHT · 一个洞察", "前沿实验室最缺的不是算力,是<span class='hl'>可信的结果</span>",
    "<div class='lead'>自主 agent 会 reward-hack,benchmark 会被污染。谁能证明「这个分数没作弊、可复现」,谁就握住了下一代 AI 训练的采购前提。</div>"
    + ul([
      "Argus 为了诚实地做研究,被迫造出一套<b>结果不可作弊</b>的引擎:真实公开 benchmark、环境严格对齐、独立验证器重测、可复现审计链。",
      "我们把这套<b>「可信度」</b>作为产品 —— 从前沿实验室今天正在高价买的 <b>评测 / RL-environment 完整性</b> 切入。",
      "<b>引擎是护城河,完整性是楔子,数据飞轮是扩张。</b>",
    ]), 2))

# 3 — Why now
slides.append(slide(ORANGE, "WHY NOW · 为什么是现在",
    "「可信度」刚从加分项变成<span class='hl'>采购前提</span>",
    "<div class='stats'>"
    "<div class='stat'><div class='big'>&gt;$1B/yr</div><div class='cap'>Anthropic 据报讨论未来一年投入 RL environments <span class='conf'>[报道 / medium]</span></div></div>"
    "<div class='stat'><div class='big'>刷榜危机</div><div class='cap'>benchmark 污染 + LMArena「排行榜幻觉」成公开丑闻;LMArena 仍融 $150M A 轮 <span class='conf'>[high]</span></div></div>"
    "<div class='stat'><div class='big'>$70–200/hr</div><div class='cap'>数据墙逼前沿实验室高价抢专家 / agentic 轨迹 <span class='conf'>[medium–high]</span></div></div>"
    "</div>"
    "<div class='lead2'>钱已经在涌向「环境 + 评测 + 可信过程数据」。Argus 不去新造需求 —— 用「可复现、可审计、不可作弊」的招牌去接已经存在的强需求。</div>", 3))

# 4 — Problem
slides.append(slide(PURPLE, "THE PROBLEM · 痛点",
    "实验室花十亿建环境,却被<span class='hl'>作弊和污染</span>反噬",
    ul([
      "<b>Reward hacking:</b> 训练中的 agent 学会「把数刷好看」而不是「把事做对」—— 硬编码已知输入统计量假装更快、偷换 kernel、环境漂移。",
      "<b>Benchmark 污染 / 刷榜:</b> 测试集泄露、过拟合 holdout、私有集审计争议 —— 报出来的分越来越不可信。",
      "<b>后果:</b> 评测平台怕被刷、labs 怕拿污染数据训出废模型、采购方无法用分数做决策。",
      "<b>缺的那一层:</b> 一个「提交者无法作弊、报告真实可复现」的<b>完整性护栏</b> —— 大家都自建一点、没人当产品做透。",
    ]), 4))

# 5 — Product / wedge A (figure)
slides.append(figslide(GREEN, "THE WEDGE · 楔子 = 产品 A",
    "我们卖<span class='hl'>两层验证</span>:报出来的数,永远不是奖励",
    "fig3_two_layer.png",
    "工程师自报分 → 冻结 harness(哈希锁)→ <b>独立 L2 验证器重跑</b>测出真实分;「报告 ≠ 重测」即作弊信号。这是 Argus 真机在用的 <b>8 套机制</b>(每条都有 file:line),抽出来即「完整性即服务」。买家:评测平台 / RL-environment 厂商 / 前沿实验室。",
    5))

# 6 — Moat (figure)
slides.append(figslide(BLUE, "WHY WE WIN · 护城河 = 引擎",
    "真正难的不是做一次,是<span class='hl'>数天不退化地一直产出</span>",
    "fig4_engine_moat.png",
    "长跑数天不空转 / 不打滑 / 不卡死 + 自进化 —— 这是自主 agent 最稀缺的工程资产,不是一个 prompt 能抄走的。诚信哲学(harness 不比 agent 聪明)是文化产物;审计链随日历时间复利。<b>可防御窗口约 12–18 个月,我们在窗口内把领先换成客户锁定 + 数据独家。</b>",
    6))

# 7 — Land & expand (figure)
slides.append(figslide(PURPLE, "LAND & EXPAND · 切入即扩张",
    "一个引擎,一条 wedge 切入,<span class='hl'>三层变现</span>",
    "fig1_three_lines.png",
    "用 <b>A(完整性)</b> 切入并建立公信力 → 同一批 run 顺手沉淀 <b>B(带审计链的研究轨迹语料)</b> 做高议价扩张 → <b>C(论文 / kernel / 因子)</b> 由横向团队卖出去做现金流地板。<b>关键:三者共用同一台引擎,B 的边际成本 ≈ 存储。</b>",
    7))

# 8 — Expansion B (figure)
slides.append(figslide(ORANGE, "EXPANSION · 扩张面 B",
    "数据飞轮:同一批 run,<span class='hl'>顺手</span>就是高价训练语料",
    "fig2_data_flywheel.png",
    "每个 run 沉淀完整、原子写入、可复现、带证据链的 agentic / reasoning 轨迹。对标 Scale(~$870M)、Surge(&gt;$1B)、Mercor(~$500M ARR)正高价收的稀缺品类。<b>我们不拼人海标注 marketplace,拼机器自产 + 近零边际成本 + 可验证不作弊。</b>",
    8))

# 9 — Traction (honest but strong)
slides.append(slide(GREEN, "WHAT'S REAL TODAY · 今天已落地的",
    "不是 demo,是<span class='hl'>真机上可数的连续运行</span>",
    "<div class='stats'>"
    "<div class='stat'><div class='big'>3 类真机</div><div class='cap'>KernelBench/B200 · nanoGPT speedrun/8×H100 · nanochat/B200 连续在跑</div></div>"
    "<div class='stat'><div class='big'>24,025</div><div class='cap'>条 codex rollout 轨迹 + 404 条 mission 审计链(events.jsonl)落盘可核实</div></div>"
    "<div class='stat'><div class='big'>~10 GB</div><div class='cap'>实测可核实轨迹语料,满产年化爬向 TB 级</div></div>"
    "</div>"
    "<div class='honest'>种子轮目标很具体:把已在真机运行的引擎产品化,<b>拿下前 2 个付费验证</b> —— A 完整性 design-partner + B 数据 pilot。</div>", 9))

# 10 — Market (figure)
slides.append(figslide(YELLOW, "MARKET · 市场",
    "吃十亿级相邻盘里<span class='hl'>最难自建</span>的稀缺细分",
    "fig7_quadrant.png",
    "相邻盘均为十亿级、一手信号硬:评测/环境(Anthropic RL env &gt;$1B/yr)、训练数据(Scale ~$870M、Surge &gt;$1B、Mercor ~$500M ARR)。我们不正面刚人海 marketplace,只吃「机器自产 + 可复现审计链」这片它们结构上不擅长的稀缺细分。",
    10))

# 11 — Team + risk-as-plan
slides.append(slide(BLUE, "TEAM & DE-RISKING · 团队与去风险",
    "资深创始人 + 把最大风险<span class='hl'>写成里程碑</span>",
    "<div class='two'>"
    "<div><div class='subh'>谁来做</div>" + ul([
      "<b>创始人(技术):</b> 资深 ML systems —— GPU kernel/CUDA、reward hacking、benchmark 完整性门清;Argus 全部代码的作者,亲手在 B200/H100 复现基线、堵 reward hack、修框架根因。",
      "<b>本轮必招:</b> 商业化 / GTM 负责人(理想升联创)+ 数据工程 + 2–3 名横向销售。",
    ]) + "</div>"
    "<div><div class='subh'>最大风险 → 对应里程碑</div>" + ul([
      "单一技术创始人,无商业 track record → <b>本轮优先招商业化负责人</b>。",
      "PMF 未验证(0 付费)→ <b>6 个月内拿 2 个付费验证</b>(A design-partner + B 数据 pilot)。",
      "B 线 OpenAI-ToS/IP → <b>融资后首批法务工作项</b>。",
    ]) + "</div>"
    "</div>", 11))

# 12 — Milestones + Ask (figure)
slides.append(figslide(ORANGE, "ASK & MILESTONES · 融资与里程碑",
    "种子轮 <span class='hl'>$4–6M / 18 个月</span>:把领先换成前几个付费验证",
    "fig5_roadmap.png",
    "<b>用途:</b> 数据脱敏/schema 管线(25%)· harness API 解耦+第三方化(20%)· 商业化负责人+销售+BD(25%)· 合规/法务/IP(15%)· B200/H100 算力(15%)。<b>对标种子轮:</b> Standard Kernel $20M · Mechanize $9.1M · Prime Intellect $15M(均 [high/medium])—— 我们已有可运行引擎但 pre-revenue,故取区间下沿。",
    12))

# 13 — Close
slides.append(f"""
<section class="slide title close">
  <div class="pad center">
    <div class="closeline">可信的结果,<br/>因为它<b>不会作弊</b>。</div>
    <div class="sub">Argus 把「不可作弊」从一句口号,做成了真机上可数、可复现、可审计的引擎 —— 并从前沿实验室今天正在买的<b>评测/环境完整性</b>切入。</div>
    <div class="pill">种子轮 $4–6M · 详细尽调材料(含全部代码证据 file:line 与置信度标注)备索</div>
  </div>
  <div class="foot"><span>Argus · 机密 / Confidential</span><span>13 / 13</span></div>
</section>""")

CSS = """
@page { size: A4 landscape; margin: 0; }
* { box-sizing: border-box; margin:0; padding:0; }
body { font-family:"Noto Sans CJK SC",sans-serif; color:#1f2933; }
.slide { position:relative; width:297mm; height:209.6mm; background:#fbfaf7;
         page-break-after:always; overflow:hidden; }
.rail { position:absolute; left:0; top:0; width:11mm; height:100%; }
.pad { position:absolute; left:11mm; right:0; top:0; bottom:0; padding:15mm 16mm 14mm 16mm; }
.kicker { font-size:11.5pt; letter-spacing:1.5px; color:#5b6b7a; font-weight:700; margin-bottom:5mm; }
h1 { font-size:27pt; line-height:1.2; color:#10212e; font-weight:800; margin-bottom:7mm; }
.hl { background:linear-gradient(transparent 62%, #ffe08a 62%); padding:0 2px; }
.body { font-size:13pt; line-height:1.62; }
.lead { font-size:15pt; line-height:1.5; color:#243240; margin-bottom:6mm; font-weight:600; }
.lead2 { font-size:13.5pt; line-height:1.55; color:#243240; margin-top:7mm; font-weight:600; }
ul { list-style:none; }
li { position:relative; padding-left:9mm; margin:4.4mm 0; font-size:13pt; line-height:1.5; }
li:before { content:"▸"; position:absolute; left:0; color:#6b8cae; font-weight:800; }
b { color:#10212e; }
.stats { display:flex; gap:9mm; margin:3mm 0 0; }
.stat { flex:1; background:#fff; border:2px solid #1f2933; border-radius:12px; padding:7mm 6mm; }
.big { font-size:23pt; font-weight:800; color:#10212e; margin-bottom:3mm; line-height:1.1; }
.cap { font-size:11pt; line-height:1.45; color:#3a4754; }
.conf { color:#7a8694; font-size:9.5pt; }
.honest { margin-top:7mm; background:#eef3f8; border-left:5px solid #6b8cae; border-radius:0 8px 8px 0;
          padding:5mm 7mm; font-size:12.5pt; line-height:1.55; }
.figwrap { text-align:center; margin:1mm 0 0; }
.figwrap img { max-height:118mm; max-width:96%; border:1px solid #e0e3e6; border-radius:10px; }
.takeaway { margin-top:5mm; font-size:12.5pt; line-height:1.55; color:#243240; }
.fignote { margin-top:3.5mm; font-size:10.5pt; line-height:1.45; color:#6a3d2a;
           background:#fff1ea; border-radius:7px; padding:3mm 5mm; }
.two { display:flex; gap:11mm; }
.two > div { flex:1; }
.subh { font-size:12.5pt; font-weight:800; color:#10212e; margin-bottom:3mm;
        border-bottom:2px solid #c5cdd4; padding-bottom:2mm; }
.foot { position:absolute; left:11mm; right:0; bottom:0; height:11mm; padding:0 16mm;
        display:flex; align-items:center; justify-content:space-between;
        font-size:9pt; color:#8a96a3; border-top:1px solid #e3e7ea; }
/* title slides */
.title { background:#10212e; }
.title .pad, .center { position:absolute; inset:0; display:flex; flex-direction:column;
        justify-content:center; align-items:center; text-align:center; padding:0 26mm; }
.logo { font-size:58pt; font-weight:900; letter-spacing:6px; color:#fbfaf7; }
.tagline { font-size:23pt; color:#dcecff; margin-top:4mm; font-weight:700; }
.sub { font-size:14pt; color:#c4d0db; line-height:1.6; margin-top:9mm; max-width:200mm; }
.sub .en { color:#8aa0b5; font-size:12.5pt; }
.title b, .close b { color:#ffe08a; }
.pill { margin-top:12mm; font-size:11pt; color:#9fb0c0; border:1.5px solid #3a4b5c;
        border-radius:30px; padding:3.5mm 9mm; }
.title .foot { color:#5b6b7a; border-top:1px solid #243240; }
.close { background:#10212e; }
.closeline { font-size:40pt; font-weight:900; color:#fbfaf7; line-height:1.25; }
.closeline b { color:#ffd76a; }
"""

html = "<!doctype html><html><head><meta charset='utf-8'><style>" + CSS + "</style></head><body>" + "".join(slides) + "</body></html>"
(DOCS/"_deck.html").write_text(html, encoding="utf-8")
weasyprint.HTML(string=html, base_url=str(DOCS)).write_pdf(str(DOCS/"Argus_VC_Deck.pdf"))
import os
print("slides:", len(slides), "| PDF bytes:", os.path.getsize(DOCS/"Argus_VC_Deck.pdf"))
