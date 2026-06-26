#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Argus business plan — investor-grade, professional, but still plain-language
(no code / file:line / ML jargon). Adds TOC + At-a-Glance, quantified traction,
a revenue model, and a use-of-funds breakdown. Headlines the PROCESS-DATA insight
(the world keeps results, never how they were made — a blank market Argus fills).
"""
from __future__ import annotations
import pathlib, weasyprint

DOCS = pathlib.Path("/home/argustest/argus-skill/docs")
F = "bp_figures"

def section(n, zh, en, body):
    return f"""<section class="sec">
  <div class="sechead"><div class="secnum">{n}</div><div class="sectitles"><div class="seczh">{zh}</div><div class="secen">{en}</div></div></div>
  {body}
</section>"""

def fig(path, cap):
    return f'<figure class="fig"><img src="{F}/{path}"/><figcaption>{cap}</figcaption></figure>'

def callout(tag, html):
    return f'<div class="callout"><span class="ctag">{tag}</span>{html}</div>'

def table(headers, rows):
    th = "".join(f"<th>{h}</th>" for h in headers)
    trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>'

def stat3(items):
    return '<div class="statrow">' + "".join(
        f'<div class="stat"><div class="statbig">{b}</div><div class="statcap">{c}</div></div>' for b, c in items) + '</div>'

COVER = """
<section class="cover">
  <div class="coverinner">
    <div class="cv-logo">ARGUS</div>
    <div class="cv-rule"></div>
    <div class="cv-title">商业计划书</div>
    <div class="cv-sub">Business Plan · 种子轮</div>
    <div class="cv-purpose">一个能够 7×24 自主进行研究、且<b>结果可验证、不可作弊</b>的 AI 系统。<br/>它不仅产出高质量成果,更完整保留「成果如何一步步产生」的全过程 ——<br/>一类长期缺失的稀缺数据。</div>
    <div class="cv-meta"><div>2026 · 种子轮</div><div>机密 / Confidential</div></div>
  </div>
</section>"""

TOC = """
<section class="toc-page">
  <h2 class="page-h2">目录 <span>Contents</span></h2>
  <div class="toc-grid">
    <div class="toc-col">
      <div class="toc-item"><span>执行摘要</span><b>03</b></div>
      <div class="toc-item"><span>1 · 为什么是现在</span><b>04</b></div>
      <div class="toc-item"><span>2 · 痛点与机会</span><b>04</b></div>
      <div class="toc-item"><span>3 · 方案与已验证战绩</span><b>04</b></div>
      <div class="toc-item"><span>4 · 过程数据:一个空白市场</span><b>05</b></div>
      <div class="toc-item"><span>5 · 产品线</span><b>06</b></div>
    </div>
    <div class="toc-col">
      <div class="toc-item"><span>6 · 市场规模与客户</span><b>07</b></div>
      <div class="toc-item"><span>7 · 商业模式与单位经济</span><b>07</b></div>
      <div class="toc-item"><span>8 · 竞争与壁垒</span><b>08</b></div>
      <div class="toc-item"><span>9 · 团队</span><b>09</b></div>
      <div class="toc-item"><span>10 · 风险与应对</span><b>09</b></div>
      <div class="toc-item"><span>11 · 财务与融资</span><b>10</b></div>
    </div>
  </div>
  <div class="glance">
    <div class="glance-h">公司速览 · At a Glance</div>
    <div class="grow"><span>项目</span><b>Argus —— 自主、可信、并产出独家「过程数据」的 AI 研究引擎</b></div>
    <div class="grow"><span>阶段</span><b>种子轮 · 产品化前期(Pre-product)</b></div>
    <div class="grow"><span>团队</span><b>3 位联合创始人 · 上海交通大学 / 中山大学 · 多篇顶会论文 + 微软实习</b></div>
    <div class="grow"><span>当前进展</span><b>真机不间断运行数月,已沉淀 24,000+ 条完整研究记录</b></div>
    <div class="grow"><span>本轮融资</span><b>US$4–6M · 18 个月</b></div>
  </div>
</section>"""

EXEC = """
<section class="sec execsec">
  <div class="sechead"><div class="secnum">·</div><div class="sectitles"><div class="seczh">执行摘要</div><div class="secen">Executive Summary</div></div></div>
  <p class="lead">Argus 是一套能够 7×24 不间断、<b>自主进行 AI 研究</b>的系统:给定一个目标,它会自行检索资料、设计实验、在真实测试中反复验证、编写代码,并得出可复现的结论 —— 全程无需人工逐步介入。</p>
  """ + stat3([
    ("数月", "真机不间断连续运行"),
    ("24,000+", "已沉淀的完整研究记录"),
    ("US$4–6M", "本轮融资 · 18 个月"),
  ]) + """
  <div class="cols">
    <div>
      <div class="mini-h">两项独特优势</div>
      <p>① <b>结果可验证、不可作弊</b>,可被独立复现;② 更稀缺的是 —— 它<b>完整保留「一项研究如何一步步完成」的全过程</b>。这类「过程数据」长期处于市场空白,而它恰恰是训练下一代 AI 最关键的稀缺燃料。</p>
    </div>
    <div>
      <div class="mini-h">当前进展</div>
      <p>系统已在真实服务器上<b>连续运行数月</b>,沉淀了 24,000+ 条完整、高质量的研究记录。本轮融资用于将其产品化,并签下首批付费客户。</p>
    </div>
  </div>
  """ + fig("fig1_three_lines.png", "一套引擎,三条产品线:可信检测、高质量数据(含完整过程)、研究成品。") + """
  <div class="askbox">
    <div class="askbox-h">融资请求 · The Ask</div>
    <div class="askrow"><span>金额 / 跑道</span><b>US$4–6M · 18 个月</b></div>
    <div class="askrow"><span>资金用途</span><b>数据与检测产品化 · 组建商业化团队 · 合规与法务 · 算力</b></div>
  </div>
</section>"""

S1 = section("1", "为什么是现在", "Why Now",
  """<p class="lead">AI 行业正从「让 AI 对话、生成内容」,转向「让 AI <b>自主完成任务、进行研究</b>」。这一转变,恰好需要我们已经掌握的两样东西。</p>
  <ul>
    <li>新方向最稀缺两项能力:一是<b>确保 AI 不作弊、结果可信</b>;二是<b>教会 AI「如何一步步完成任务」的过程数据</b>。</li>
    <li>前沿机构正为此重金投入 —— 头部 AI 实验室一年仅在「训练环境与评测」上,据公开报道即投入<b>十亿美元量级</b>。</li>
    <li>这两项能力,正是 Argus 与生俱来的核心。<b>时机已经成熟。</b></li>
  </ul>""")

S2 = section("2", "痛点与机会", "Problem &amp; Opportunity",
  """<p class="lead">训练大模型的公司,正被三个高成本、难解决的问题所困。</p>
  <ul>
    <li><b>① 成绩难以采信(刷分):</b> AI 在自动执行任务时,容易学会「让成绩好看」而非「把事做对」—— 利用评测规则的漏洞。结果难以直接采信。</li>
    <li><b>② 高质量数据短缺:</b> 大模型训练日益受限于优质数据,企业只能高价聘请专家逐字撰写(单位成本约 $70–200 / 小时)。</li>
    <li><b>③ 「过程」从未被记录:</b> 世界只留下了「成果」(论文、代码),却几乎无人保存「成果如何产生」。要训练 AI 自主研究,这恰是最关键、也最匮乏的一环。</li>
  </ul>
  """ + callout("机会", "这三个问题,Argus 可一并解决 —— 尤其第三项,对应一个<b>长期空白、尚无人占据的市场</b>(详见第 4 节)。"))

S3 = section("3", "方案与已验证战绩", "Solution &amp; Traction",
  """<p class="lead">Argus 是一个<b>能够自主进行研究的 AI 系统</b>。</p>
  <p>给定目标后,它会自行检索资料、设计并运行实验、在真实测试中反复验证、编写代码,最终给出可复现的结论。系统内部职责清晰:一部分负责执行,另一部分<b>专职审核把关</b> —— 逐项核验,确保结果未经作弊、可被复现。它还能<b>连续运行数日而不退化</b>,这正是「让 AI 自主研究」最难、也最具价值之处。</p>
  <div class="mini-h">已验证的研究战绩(每个分数均由冻结验证器在真机重测,绝不自报)</div>
  """ + table(["任务", "战绩", "说明"], [
    ["训练提速 · nanoGPT(8×H100)", "<b>79.77s · 追平公开 SOTA</b>", "从公开榜近-SOTA 记录起步;同协议 like-for-like 下优于多个独立重测(80.18s / 80.61s)"],
    ["5 分钟预训练 · nanochat(B200)", "<b>floor 0.9636 · 贴平人类强基线</b>", "426 次自治搜索、全程禁读已发表解,自主贴平 karpathy 最佳已知基线(0.9646)"],
    ["自主发明能力", "<b>自创 37 个机制</b>", "在禁读答案前提下,首次证明系统「会发明」,而非仅调参"],
    ["自动写论文", "<b>端到端产出 2 篇</b>", "自行设计 benchmark、跑评测、写 LaTeX、出 PDF(MMR-Trap / v2)"]]) + """
  """ + callout("可公开核验", "以上每个分数均由<b>冻结验证器在真机上重新测量、绝不自报</b>,完整报告与实时看板公开于 <b>argusbot.cn</b> —— 这既是「结果可信、防作弊」最直接的证明,也是投资人可自行核验的战绩。"))

S4 = section("4", "过程数据:一个空白市场", "Process Data — a Blank Market",
  """<p class="lead">这是我们最独特、也最被低估的一项资产。</p>
  <ul>
    <li>世界拥有<b>海量「成果」</b>:论文、代码、报告。但几乎<b>无人记录「成果如何一步步完成」</b> —— 研究者的思考路径、尝试过的方向、失败的尝试、取舍的依据、验证的方式。</li>
    <li>论文只呈现打磨后的<b>最终结果</b>;真实的试错过程从未被完整记录,更未被规模化保存。<b>这是一片空白。</b></li>
    <li>其重要性在于:训练 AI <b>自主研究、自主解决难题</b>,最需要的正是这类「过程数据」—— 只看最终答案,模型无法学会如何<b>得到</b>答案。</li>
    <li><b>Argus 与生俱来、规模化地产出此类数据:</b> 每完成一项研究,都会自动留存从选题到结论的完整过程 —— 每一步推理、每一次实验、每一处失败与修正。<b>我们不仅拥有论文,更拥有论文背后的全过程。</b></li>
  </ul>
  """ + fig("fig_process.png", "世界拥有「最终成果」,却没有「成果如何产生」—— Argus 规模化地补上这片空白。") + """
  """ + callout("空白市场", "他人没有,因为从未有人记录;我们拥有,因为我们的系统本就在持续、诚实地进行研究。<b>运行越久,这份独有数据越深厚</b> —— 这是一项随时间复利的先发优势。"))

S5 = section("5", "产品线", "Product Lines",
  """<p class="lead">同一套系统,衍生三条产品线,按战略优先级排列:</p>
  """ + table(
    ["", "产品线", "核心价值", "目标客户"],
    [["<b>主线 A</b>", "可信检测服务", "为客户证明「该成绩未经作弊、可被复现」", "AI 测评平台、训练环境厂商、大模型团队"],
     ["<b>主线 B</b>", "过程数据 + 高质量成果", "不止论文成果,更含「如何完成」的全过程 —— 别处无法获得的数据", "训练大模型的企业"],
     ["<b>现金流 C</b>", "研究成品", "论文 / 算法优化 / 量化因子,按项目交付", "按项目付费的客户"]]) + """
  <p>关键点:A 与 B 由同一系统在研究过程中<b>自然产生</b>,每多交付一份,边际成本仅约等于存储;C 用于补充早期现金流。</p>""")

S6 = section("6", "市场规模与客户", "Market &amp; Customers",
  """<p class="lead">我们瞄准一个体量可观、且仍在高速增长的市场。</p>
  <ul>
    <li><b>市场规模:</b> AI 训练数据是一个<b>数十亿美元、年增速超 20%</b> 的市场;头部 AI 实验室一年仅「训练环境与评测」一项,据报道即投入<b>十亿美元量级</b>。</li>
    <li><b>目标客户:</b> 训练大模型的企业、AI 测评平台、量化机构。</li>
  </ul>
  <div class="mini-h">市场分层(TAM / SAM / SOM)</div>
  """ + table(["层级", "含义", "体量(量级)"], [
    ["总市场 TAM", "全球 AI 训练数据 + 评测 / 环境完整性", "百亿美元级 · 年增 20%+"],
    ["可服务市场 SAM", "训练大模型的企业、评测 / 环境平台对「可信数据与检测」的需求", "数十亿美元级"],
    ["初期可获取 SOM", "首批 design-partner 与数据授权(18–36 个月目标)", "数百万美元级"]]) + """
  <div class="mini-h">对标公司(印证需求真实且巨大)</div>
  """ + table(
    ["公司", "业务", "规模"],
    [["Scale AI", "为 AI 企业提供训练数据与评测", "估值约 $290 亿"],
     ["Surge AI", "高端 AI 训练数据", "年收入超 $10 亿"],
     ["Mercor", "组织专家为 AI 企业生产数据", "估值约 $100 亿"]]) + """
  """ + callout("差异化定位", "这些公司依赖大规模人力,模式重、成本高,且只提供「成果数据」,<b>不含「过程数据」</b>。我们走的是一条它们难以复制的路 —— 机器自动产出、成本极低、内建防作弊,并独占过程数据这片空白。"))

S7 = section("7", "商业模式与单位经济", "Business Model &amp; Unit Economics",
  """<div class="mini-h">收入模型</div>
  """ + table(
    ["产品线", "计价方式", "毛利特征"],
    [["A 可信检测", "年度服务合同 / 按次审计计费", "软件型,毛利高"],
     ["B 过程数据", "数据集授权,独家 / 半独家分级", "边际成本≈存储,毛利随规模上升"],
     ["C 研究成品", "项目制交付", "一次性,作现金流补充"]]) + """
  <div class="mini-h">单位经济</div>
  """ + table(["项", "说明"], [
    ["单次研究成本", "算力 + 模型调用,约数十美元量级"],
    ["数据可复用", "同一份过程数据可多次、分级授权"],
    ["毛利结构", "边际成本≈存储,规模化后毛利持续走高"]]) + """
  """ + fig("fig2_data_flywheel.png", "数据飞轮:运行越多 → 数据越多、能力越强 → 产品越具价值 → 收入再投入,持续放大。") + """
  """ + callout("核心优势", "这是一台<b>自我强化的飞轮</b>:数据在研究过程中自然产生,每多交付一份的成本几乎仅为存储,因此规模扩大后,<b>毛利将持续走高</b>。"))

S8 = section("8", "竞争与壁垒", "Competition &amp; Moat",
  """<p class="lead">我们的护城河不在于单一功能,而在于三项竞争对手短期难以补足的能力。</p>
  <div class="mini-h">竞争格局</div>
  """ + table(["公司 / 类型", "在做什么", "我们的差异"], [
    ["Scale AI · Surge · Mercor", "训练数据 / 高端标注", "机器自产、近零成本,且额外含过程数据"],
    ["Patronus · Vals", "AI 模型评测", "检测内建于研究流程、可被独立复现"],
    ["Mechanize · Prime Intellect", "训练环境 / 评测工具", "额外独占「过程数据」这一层"]]) + """
  <div class="mini-h">三项壁垒</div>
  <ul>
    <li><b>独占的过程数据:</b> 该市场长期空白,我们具备先发优势;数据<b>随时间复利</b> —— 运行越久越深厚,而过去的时间无法用资金买回。</li>
    <li><b>机器自产、近零成本:</b> 对手依赖大规模人力,模式重、成本高;我们由系统自动产出,规模越大、毛利越高。</li>
    <li><b>防作弊为底层设计:</b> 并非事后附加的功能,而是自第一行代码起即内建于系统 —— 竞争对手若要复制,需整套重做。</li>
  </ul>
  <p>此外,大型企业多为「自用自建」,通常不将此类能力作为对外产品销售 —— 这为我们独立经营这门业务留出了空间。</p>""")

S9 = section("9", "团队", "Team",
  """<p class="lead">一支年轻而战绩扎实的技术团队 —— <b>3 位联合创始人,均为计算机系科班出身</b>,来自<b>上海交通大学</b>(2 位)与<b>中山大学</b>(1 位),本科大三在读。</p>
  <ul>
    <li><b>学术能力:</b> 均发表过<b>多篇顶级国际会议(顶会)论文</b>,具备扎实的前沿研究功底。</li>
    <li><b>多元产业背景:</b> 实习经历涵盖<b>微软(Microsoft)、商汤(SenseTime)、私募基金量化研究</b>等 —— 横跨顶级科技公司、AI 企业与量化金融,背景互补,恰好对应我们三条产品线(检测、数据、量化成品)。</li>
    <li><b>这正是 Argus 的底色:</b> 团队亲手从零构建了这套「能自主研究、且防作弊」的系统,并在顶级 GPU 上完成调通与优化。</li>
    <li><b>本轮关键招募:</b> 商业化 / 销售负责人(主导对 AI 企业的销售)、数据工程、产品工程,以及若干业务拓展。</li>
  </ul>""")

S10 = section("10", "风险与应对", "Risks &amp; Mitigation",
  table(["风险", "应对"], [
     ["商业化处于早期,尚无付费客户", "本轮即以签下首批付费客户为核心目标,并锁定明确里程碑"],
     ["数据合规与授权", "设立合规 / 法务职能,确保数据可授权、可转售"],
     ["团队年轻、商业化经验待补", "本轮优先引入资深商业化 / 销售负责人(理想升联创)"],
     ["大厂自建与竞品跟进", "以过程数据先发 + 时间复利 + 客户绑定构建壁垒;大厂多为自用、不对外销售,留出空间"]]))

S11 = section("11", "财务与融资", "Financials &amp; The Ask",
  """<div class="mini-h">未来 12 个月里程碑</div>
  """ + table(
    ["阶段", "目标"],
    [["0–3 个月", "将核心能力打磨为可演示的产品;与首批潜在客户开展定价沟通"],
     ["3–6 个月", "签下首个付费客户(可信检测)与首个付费数据试点"],
     ["6–12 个月", "将试点转化为可复制的合同;现金流开始覆盖部分运营开支"]]) + """
  <div class="mini-h">资金用途</div>
  """ + table(
    ["方向", "占比", "说明"],
    [["数据与检测产品化", "35%", "数据脱敏、整理、质量管控与工程化"],
     ["商业化与销售团队", "25%", "销售负责人 + 业务拓展"],
     ["合规与法务", "15%", "数据合规、知识产权与授权"],
     ["算力与基础设施", "15%", "GPU 与运行环境"],
     ["储备", "10%", "机动资金"]]) + """
  <div class="askbox wide">
    <div class="askbox-h">融资请求 · The Ask</div>
    <div class="askrow"><span>金额 / 跑道</span><b>US$4–6M · 18 个月</b></div>
    <div class="askrow"><span>同类种子轮</span><b>同赛道公司种子轮普遍处于 US$9M–20M 区间</b></div>
    <div class="askrow"><span>本轮目标</span><b>把已在真机运行、结果可信的引擎与独占的过程数据,转化为产品并签下首批客户,让这台飞轮真正转动起来。</b></div>
  </div>""")

BODY = COVER + TOC + EXEC + S1 + S2 + S3 + S4 + S5 + S6 + S7 + S8 + S9 + S10 + S11

CSS = r"""
@page { size: A4 portrait; margin: 18mm 17mm 16mm 17mm;
  @top-left { content: "Argus · 商业计划书"; font-family:"Noto Sans CJK SC"; font-size:7.5pt; color:#9aa7b2; }
  @top-right { content: "机密 / Confidential"; font-family:"Noto Sans CJK SC"; font-size:7.5pt; color:#9aa7b2; }
  @bottom-right { content: counter(page); font-family:"Noto Sans CJK SC"; font-size:8pt; color:#7a8694; }
}
@page cover { margin:0; @top-left{content:none} @top-right{content:none} @bottom-right{content:none} }
* { box-sizing:border-box; margin:0; padding:0; }
body { font-family:"Noto Serif CJK SC","Noto Sans CJK SC",serif; font-size:10.4pt; line-height:1.65; color:#23303c; }
b,strong { color:#10212e; }

.cover { page:cover; width:210mm; height:297mm; background:#0f1f2c; color:#eef3f7; position:relative; page-break-after:always; }
.coverinner { position:absolute; left:24mm; right:24mm; top:84mm; }
.cv-logo { font-family:"Noto Sans CJK SC"; font-size:56pt; font-weight:900; letter-spacing:7px; color:#fbfaf7; }
.cv-rule { width:54mm; height:3px; background:#ffd76a; margin:9mm 0 8mm; }
.cv-title { font-family:"Noto Sans CJK SC"; font-size:31pt; font-weight:800; color:#dcecff; }
.cv-sub { font-family:"Noto Sans CJK SC"; font-size:12.5pt; color:#8aa0b5; letter-spacing:2px; margin-top:3mm; }
.cv-purpose { font-size:12.5pt; line-height:1.85; color:#c4d0db; margin-top:15mm; }
.cv-purpose b { color:#ffe08a; }
.cv-meta { position:absolute; left:24mm; right:24mm; top:168mm; font-family:"Noto Sans CJK SC"; font-size:9.5pt; color:#8090a0; }
.cv-meta > div { margin:2mm 0; }

.toc-page { page-break-before: always; page-break-after: always; }
.page-h2 { font-family:"Noto Sans CJK SC"; font-size:20pt; font-weight:800; color:#10212e; border-bottom:2.5px solid #10212e; padding-bottom:3mm; margin-bottom:7mm; }
.page-h2 span { font-size:11pt; color:#9aa7b2; font-weight:400; }
.toc-grid { display:flex; gap:10mm; }
.toc-col { flex:1; }
.toc-item { display:flex; justify-content:space-between; align-items:baseline; border-bottom:1px dotted #c2ccd5; padding:2.8mm 0; font-family:"Noto Sans CJK SC"; font-size:10pt; }
.toc-item b { color:#6b8cae; }
.glance { background:#10212e; color:#dfe9f1; border-radius:10px; padding:6mm 7mm; margin-top:12mm; }
.glance-h { font-family:"Noto Sans CJK SC"; font-size:12pt; font-weight:800; color:#ffd76a; margin-bottom:4mm; border-bottom:1px solid #2a3e4f; padding-bottom:2.5mm; }
.grow { display:flex; gap:4mm; margin:2.6mm 0; font-size:9.6pt; line-height:1.5; }
.grow span { min-width:24mm; color:#8aa0b5; font-family:"Noto Sans CJK SC"; }
.grow b { color:#eef3f7; flex:1; }

.sec { margin:8mm 0 4mm; }
.sec:first-of-type { margin-top:0; }
.execsec { page-break-before: always; margin-top:0; }
.sechead { display:flex; align-items:flex-start; gap:5mm; border-bottom:2.5px solid #10212e; padding-bottom:3mm; margin-bottom:5mm; page-break-after:avoid; }
.secnum { font-family:"Noto Sans CJK SC"; font-size:25pt; font-weight:900; color:#c6d4e2; line-height:0.9; min-width:13mm; }
.seczh { font-family:"Noto Sans CJK SC"; font-size:16.5pt; font-weight:800; color:#10212e; }
.secen { font-family:"Noto Sans CJK SC"; font-size:8.5pt; color:#8a96a3; letter-spacing:1px; text-transform:uppercase; margin-top:1mm; }

.lead { font-size:11.2pt; line-height:1.7; color:#1d2935; margin:0 0 4mm; }
p { margin:3mm 0; }
ul { margin:3mm 0; padding-left:6mm; }
li { margin:2.6mm 0; }
.mini-h { font-family:"Noto Sans CJK SC"; font-size:10.5pt; font-weight:800; color:#10212e; margin:6mm 0 2.5mm; padding-left:3mm; border-left:3px solid #6b8cae; page-break-after:avoid; }
.cols { display:flex; gap:8mm; margin:3mm 0; }
.cols > div { flex:1; }
.closing { margin-top:6mm; font-size:11pt; font-weight:600; color:#10212e; border-top:1.5px solid #c5cdd4; padding-top:4mm; }

.statrow { display:flex; gap:5mm; margin:4mm 0; }
.stat { flex:1; background:#fbfaf7; border:1.5px solid #10212e; border-radius:9px; padding:4mm 4.5mm; }
.statbig { font-family:"Noto Sans CJK SC"; font-size:17pt; font-weight:800; color:#10212e; line-height:1.05; }
.statcap { font-size:8.4pt; color:#3a4754; margin-top:1.8mm; line-height:1.4; }

table { border-collapse:collapse; width:100%; margin:3mm 0; font-size:9.4pt; page-break-inside:avoid; }
th,td { border:1px solid #c2ccd5; padding:4px 8px; text-align:left; vertical-align:top; }
th { background:#10212e; color:#eef3f7; font-family:"Noto Sans CJK SC"; font-weight:700; font-size:9pt; }
tbody tr:nth-child(even) td { background:#f5f8fb; }
td:first-child { font-family:"Noto Sans CJK SC"; }

.fig { margin:5mm 0 5mm; page-break-inside:avoid; text-align:center; }
.fig img { max-width:100%; max-height:90mm; border:1px solid #e0e3e6; border-radius:8px; }
.fig figcaption { font-size:8.6pt; color:#56636f; margin-top:2.5mm; }

.callout { background:#eef7f0; border-left:4px solid #3f9c5a; border-radius:0 8px 8px 0; padding:4mm 5mm; margin:4mm 0; font-size:10pt; line-height:1.6; page-break-inside:avoid; }
.callout .ctag { display:inline-block; font-family:"Noto Sans CJK SC"; font-weight:800; font-size:8pt; padding:1px 7px; border-radius:10px; margin-right:6px; background:#3f9c5a; color:#fff; vertical-align:1.5px; }

.askbox { background:#10212e; color:#dfe9f1; border-radius:10px; padding:5mm 6mm; margin:5mm 0; page-break-inside:avoid; }
.askbox-h { font-family:"Noto Sans CJK SC"; font-size:11.5pt; font-weight:800; color:#ffd76a; margin-bottom:3mm; border-bottom:1px solid #2a3e4f; padding-bottom:2mm; }
.askrow { display:flex; gap:4mm; margin:2.4mm 0; font-size:10pt; }
.askrow span { min-width:30mm; color:#8aa0b5; font-family:"Noto Sans CJK SC"; }
.askrow b { color:#eef3f7; flex:1; }
"""

html = "<!doctype html><html><head><meta charset='utf-8'><style>" + CSS + "</style></head><body>" + BODY + "</body></html>"
(DOCS/"_bp_doc.html").write_text(html, encoding="utf-8")
weasyprint.HTML(string=html, base_url=str(DOCS)).write_pdf(str(DOCS/"Argus_商业计划书.pdf"))
import os
print("investor-grade BP PDF bytes:", os.path.getsize(DOCS/"Argus_商业计划书.pdf"))
