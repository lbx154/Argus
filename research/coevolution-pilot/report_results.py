import csv
import datetime as dt
import gzip
import json

import yaml
from paths import ROOT

protocol = json.loads((ROOT / 'protocol.json').read_text())
results = [json.loads(path.read_text()) for path in sorted((ROOT / 'runs').glob('*/result.json'))]
evaluations = [row for row in results if row['run'].startswith('eval-')]
training = [row for row in results if row['run'].startswith('train-')]
invoice_diagnostics = []
truth = json.loads((ROOT / 'benchmark/tasks/invoice-fraud-detection/verifier/ground_truth.json').read_text())
expected = {row['invoice_page_number']: row for row in truth if row['reason'] != 'Clean'}
for result in evaluations:
    if result['task'] != 'invoice-fraud-detection':
        continue
    output = ROOT / 'runs' / result['run'] / 'fraud_report.json'
    if not output.exists():
        continue
    actual = {row['invoice_page_number']: row for row in json.loads(output.read_text())}
    mismatches = []
    for number in sorted(set(actual) & set(expected)):
        for key in ['po_number', 'reason', 'vendor_name', 'invoice_amount', 'iban']:
            if actual[number].get(key) != expected[number].get(key):
                mismatches.append({'page': number, 'field': key, 'actual': actual[number].get(key), 'expected': expected[number].get(key)})
    invoice_diagnostics.append({'condition': result['condition'], 'expected_flagged': len(expected),
        'missing': len(set(expected)-set(actual)), 'extra': len(set(actual)-set(expected)),
        'correct_reasons': sum(actual[n].get('reason') == expected[n]['reason'] for n in set(actual)&set(expected)),
        'field_mismatches': mismatches})
knowledge = ROOT / 'learning' / ('frozen' if (ROOT / 'learning/frozen').exists() else 'current')
events = []
observations = set()
for path in sorted((ROOT / 'runs').glob('*/agent/runtime-events.jsonl')):
    for line in path.read_text().splitlines():
        row = json.loads(line)
        row['run'] = path.parent.parent.name
        if row.get('toolCallId'):
            observations.add(row['toolCallId'].split('|')[0])
        if row['kind'] in ['runtime_revision_activated', 'runtime_revision_rejected', 'runtime_revision_not_admitted', 'evolved_tool_used']:
            events.append(row)
events.sort(key=lambda event: event['timestamp'])

documents = []
for kind, folder in [('skill', knowledge / 'skills'), ('wiki', knowledge / 'wiki/pages')]:
    for path in sorted(folder.rglob('*.md')) if folder.exists() else []:
        text = path.read_text()
        front, separator, body = text[4:].partition('\n---\n') if text.startswith('---\n') else ('', '', text)
        metadata = yaml.safe_load(front) or {}
        expected = {'name', 'description'} if kind == 'skill' else {'title', 'description'}
        duplicate_frontmatter = body.lstrip().startswith('---\n') and ('\nname:' in body[:500] or '\ntitle:' in body[:500])
        documents.append({'kind': kind, 'path': str(path.relative_to(ROOT)),
                          'title': metadata.get('name', metadata.get('title', path.stem)),
                          'frontmatter_valid': bool(separator) and set(metadata) == expected and not duplicate_frontmatter,
                          'duplicate_frontmatter_in_body': duplicate_frontmatter,
                          'characters': len(body), 'text': text})

provenance = []
history = knowledge / 'pi/history'
for path in sorted(history.glob('v*.py')) if history.exists() else []:
    matched = []
    for trajectory in (ROOT / 'runs').glob('train-*/trajectory.jsonl*'):
        raw = gzip.decompress(trajectory.read_bytes()) if trajectory.suffix == '.gz' else trajectory.read_bytes()
        for line in raw.decode(errors='replace').splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('type') == 'tool_execution_start' and event.get('toolName') == 'evolve_runtime':
                if event.get('args', {}).get('source') == path.read_text():
                    matched.append({'run': trajectory.parent.name, 'toolCallId': event['toolCallId'].split('|')[0]})
    provenance.append({'path': str(path.relative_to(ROOT)), 'matches_agent_generated_source': bool(matched), 'sources': matched})

aggregate = {}
for condition in protocol['conditions']:
    rows = [row for row in evaluations if row['condition'] == condition]
    aggregate[condition] = {'completed_tasks': len(rows), 'tasks_passed': sum(row['score']['reward'] for row in rows),
                            'cost_usd': sum(row['tokens'].get('cost_usd', 0) for row in rows),
                            'tokens': sum(row['tokens'].get('totalTokens', 0) for row in rows),
                            'model_calls': sum(row['tokens'].get('model_calls', 0) for row in rows),
                            'agent_seconds': sum(row.get('agent_seconds', row['wall_seconds']) for row in rows),
                            'inspection_tool_calls': sum(row['tools'].get('inspect_data', 0) for row in rows)}

usage = [json.loads(line) for line in (ROOT / 'gateway-usage.jsonl').read_text().splitlines()] if (ROOT / 'gateway-usage.jsonl').exists() else []
for row in usage:
    item = row.get('usage', {})
    if item:
        inputs = item.get('input_tokens', 0)
        cached = item.get('input_tokens_details', {}).get('cached_tokens', 0)
        written = item.get('input_tokens_details', {}).get('cache_write_tokens', 0)
        long = inputs > 272000
        row['recomputed_cost_usd'] = (max(0, inputs-cached-written)*(8 if long else 4)
            + cached*(.8 if long else .4) + written*(10 if long else 5)
            + item.get('output_tokens', 0)*(30 if long else 20))/1e6
    else:
        row['recomputed_cost_usd'] = row.get('cost_usd', 0)
quality_path = knowledge / 'quality-review.json'
quality = json.loads(quality_path.read_text()) if quality_path.exists() else None
probes_path = ROOT / 'quality-probes.json'
probes = json.loads(probes_path.read_text()) if probes_path.exists() else None
benchmark_audit_path = ROOT / 'benchmark-audit.json'
benchmark_audit = json.loads(benchmark_audit_path.read_text()) if benchmark_audit_path.exists() else None
data = {'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'protocol': protocol,
        'training': training, 'evaluations': evaluations, 'aggregate': aggregate,
        'documents': documents, 'runtime_events': events, 'runtime_source_provenance': provenance,
        'manager_quality_review': quality, 'gateway_requests': len(usage),
        'artifact_quality_probes': probes,
        'benchmark_consistency_audit': benchmark_audit,
        'invoice_diagnostics': invoice_diagnostics,
        'gateway_estimated_usd': sum(row['recomputed_cost_usd'] for row in usage),
        'cost_note': 'Recomputed from original usage including separate cache-write tokens; original gateway log preserved. All totals remain well below the $20 cap.',
        'heldout_complete': len(evaluations) == len(protocol['heldout_tasks']) * len(protocol['conditions'])}
if data['heldout_complete']:
    baseline, joint = aggregate['baseline'], aggregate['joint']
    data['joint_change_percent'] = {key: 100*(joint[key]/baseline[key]-1)
                                    for key in ['cost_usd', 'tokens', 'model_calls', 'agent_seconds']}
(ROOT / 'results.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')

with (ROOT / 'scores.csv').open('w') as stream:
    writer = csv.writer(stream)
    writer.writerow(['run', 'task', 'condition', 'training', 'reward', 'passed', 'failed', 'skipped', 'model_calls', 'tokens', 'estimated_usd', 'agent_seconds', 'inspect_data_calls'])
    for row in training + evaluations:
        writer.writerow([row['run'], row['task'], row['condition'], row['training'], row['score']['reward'],
                         row['score']['passed'], row['score']['failed'], row['score'].get('skipped', 0),
                         row['tokens'].get('model_calls', 0), row['tokens'].get('totalTokens', 0),
                         row['tokens'].get('cost_usd', 0), row.get('agent_seconds', row['wall_seconds']), row['tools'].get('inspect_data', 0)])

labels = {'baseline': '基线', 'knowledge': 'Skill + Wiki', 'pi_runtime': 'Pi 运行时工具', 'joint': '联合'}
table = '\n'.join(f"| {labels[key]} | {value['tasks_passed']}/{value['completed_tasks']} | {value['model_calls']} | {value['tokens']:,} | ${value['cost_usd']:.3f} | {value['inspection_tool_calls']} |"
                  for key, value in aggregate.items())
tasks = []
for task in protocol['heldout_tasks']:
    line = [task]
    for condition in protocol['conditions']:
        row = next((r for r in evaluations if r['task'] == task and r['condition'] == condition), None)
        line.append('—' if row is None else f"{'通过' if row['score']['reward'] else '未通过'} ({row['score']['passed']}/{row['score']['passed'] + row['score']['failed']})")
    tasks.append('| ' + ' | '.join(line) + ' |')
document_links = '\n'.join(f"- {d['kind']}：[{d['title']}]({d['path']})；格式{'通过' if d['frontmatter_valid'] else '有问题'}，正文 {d['characters']} 字符。" for d in documents)
training_lines = '\n'.join(f"| {r['run']} | {r['score']['passed']}/{r['score']['passed']+r['score']['failed']} | {r['score']['reward']} | ${r['tokens'].get('cost_usd',0):.3f} |" for r in training)
activations = [event for event in events if event['kind'] == 'runtime_revision_activated']
runtime_lines = '\n'.join(f"- {event['timestamp']}，{event['run']} 激活 v{event['version']}：{event['rationale']}" for event in activations)
markdown = f'''本报告是 SkillsBench 的五任务、单次重复初版实验。状态：{'未见任务全部完成' if data['heldout_complete'] else '仍在运行，结果尚不完整'}。

初版结论：运行中产生、验证并使用新工具的机制已经跑通，Skill/Wiki 也有真实更新；本轮没有显示原始验收通过数提升。联合组的模型调用从 33 次降到 23 次，但总 token 仅降低约 2.8%，费用估算仅降低约 0.9%，不能将少调用等同于显著成本收益。学习产物存在具体质量缺陷，不能直接自动推广。

公开来源：[SkillsBench](https://github.com/benchflow-ai/skillsbench/tree/{protocol['revision']})，版本 `{protocol['revision']}`。使用原始任务、输入和验收断言，五道任务的官方参考解都通过验收。评测使用共同 Docker 环境和较短的固定预算，因此这些分数不是官方排行榜分数。

四个条件使用同一模型 gpt-5.6-sol/high：基线、仅学习得到的 Skill/Wiki、仅学习得到的 Pi 工具、两者联合。开发任务是 sales-pivot-analysis、manufacturing-codebook-normalization；下面三道任务不参与学习。各条件每题最多 20 次模型请求、240 秒，独立容器、相同输入；Skill/Wiki 和运行时工具在评测前冻结。所有结果保留，不挑选最佳运行。

| 条件 | 任务通过数/已完成数 | 模型事件数 | 总 token | Pi 费用估算 | 新检查工具调用 |
|---|---:|---:|---:|---:|---:|
{table}

| 未见任务 | 基线 | Skill + Wiki | Pi 工具 | 联合 |
|---|---|---|---|---|
{chr(10).join(tasks)}

主要结果是原始验收整体通过的任务数。括号内为非跳过测试的通过项；不能把格式测试通过等同于业务答案正确。每条件只有三题、一次运行，没有统计显著性保证。

invoice-fraud-detection 的补充逐记录诊断保存在 results.json 的 invoice_diagnostics 中；它只解释原始验收为什么失败，不替换原始得分。已完成的结果中，欺诈页及原因全部识别正确，但 `PO-INVALID` 占位符被保留，参考验收要求这些位置为 null。这个输出契约边界使整体任务仍然判失败。

此外，xlsx-recover-data 存在可复核的参考口径不一致。四组都填入 Science 平均预算 7610.3，原始验收要求 7444.4。独立计算确认前者对应 2019–2024 六个年度，后者只取 2019–2023 五个年度；工作簿其他五个可直接核对的已知平均值均使用六个年度。详见 `benchmark-audit.json`。原始分数没有修改，这个 1/3 不应直接解释为真实业务正确率；此审计也不能证明任何进化条件提高了准确率。

实际进化内容：

{runtime_lines or '尚无成功激活的运行时修订。'}

运行时源码逐字匹配原始模型工具调用的 source 字段：{all(item['matches_agent_generated_source'] for item in provenance) if provenance else False}。每个候选经过 CSV/XLSX/PDF、缺失文件错误、输入只读检查后才激活。激活和后续 inspect_data 调用位于同一 Pi 运行轨迹中。此次进化对象是实际执行的工具层 Python 代码，未训练模型权重，也未自动修改生产 Pi 内核。

学习产物：

{document_links or '尚无生成的 Skill/Wiki 页面。'}

Manager 的文档审阅保存在 `learning/frozen/quality-review.json`（尚未冻结时位于 current）。其主观评分来自同模型的新会话，不作为准确率证明。最终仍应结合原始文档、来源证据、工具契约和未见任务成绩判断质量。

冻结后只读反例检查另存于 `quality-probes.json`，没有反馈给评测代理或改变验收分数。检查发现：CSV 含多行引号字段时，检查器的行号是逻辑记录序号，Wiki 把它写成物理行号过强；重复 CSV 列名会覆盖值，Wiki 只明确说明了 XLSX 的同类限制。XLSX 未缓存公式返回 null、全文件行数上限会省略后续工作表，这两点与 Wiki 描述一致。Manager Skill 的 content 自带头部，写入器又生成了头部，留下重复 frontmatter，说明模型输出与持久化接口还需要更严格的契约检查。原始冻结文档未被人工润色。

开发过程：

| 开发运行 | 非跳过测试通过项 | 整体通过 | Pi 费用估算 |
|---|---:|---:|---:|
{training_lines}

开发阶段保留了失败和修订。最早一次控制器错误地使用 continuation 提示、没有传入原始任务，已按基础设施错误隔离在 setup-invalid-runs；随后修正并加入原始任务与知识确实进入提示的断言。发布器对带签名工具 ID 的匹配、对验证失败证据的接纳也由实验实现者修正；这些人工桥接修复不算模型自进化成果。最初的工具式 Manager 审阅触及请求上限，随后用同一开发证据进行了一次无工具的收尾审阅，内容由模型生成，再由原生 SkillStore/WikiStore 持久化。模型生成的检查器源代码、其线程限制调整及 Skill/Wiki 修改有独立原始轨迹。

实验网关共记录 {len(usage)} 次模型请求，累计费用估算 ${data['gateway_estimated_usd']:.3f}，包括开发、评审和基础设施调试；上表仅为评测条件的 Pi 使用量估算。助手对话本身不在该网关内。请求上限 360、估算费用上限 $20。账户实际计费以 GitHub 为准。

适用边界：本实验复用 Argus 的 Engineer/Manager 提示协议、SkillStore、Wiki 初始化和格式约定，通过实验控制器驱动 Argus-Pi；未将完整生产 daemon 的所有调度、传播路径纳入评测。知识条件把冻结的全部 Skill/Wiki 文本注入提示，没有测试生产中的选择性检索或按需加载，也不能单独归因 Wiki 的收益。公共 benchmark 可能存在模型预训练污染；固定任务顺序和缓存也可能影响一次运行的费用。未使用任何上游预制 Skill 或 oracle 给代理解题。费用按原始 usage 中的普通输入、缓存读取、缓存写入和输出分别重新核算，旧网关日志保留；所有实验代理费用均远低于 $20 上限。

原始结果见 `results.json`、`scores.csv`、每个 runs 子目录内的 trajectory.jsonl.gz、agent/runtime-events.jsonl 和 grading/pytest.txt。发布版轨迹经无损 gzip 压缩，原始字节哈希在 archive-manifest.json；源代码、协议和参考解验收均已保留。基准输入和验收源码由 prepare.py 按固定版本下载，复跑环境与发布调整见 README.md。学习产物未写入生产全局 Skill/Wiki。
'''
(ROOT / 'report.md').write_text(markdown)

encoded = json.dumps(data, ensure_ascii=False).replace('<', '\\u003c')
page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Argus × Argus-Pi 协同进化实验</title>
<style>body{font:16px/1.6 system-ui,sans-serif;background:#f4f6fa;color:#182338;margin:0}main{max-width:1180px;margin:auto;padding:32px}h1{font-size:30px;margin-bottom:8px}h2{margin-top:32px}.sub{color:#536176}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.card,section{background:white;border:1px solid #dce2ec;border-radius:12px;padding:18px;margin-bottom:16px}.big{font-size:30px;font-weight:700}.bar{height:9px;background:#e7edf5;border-radius:8px;margin:10px 0}.bar i{display:block;background:#3878db;height:9px;border-radius:8px}table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:10px;border-bottom:1px solid #e7edf5;text-align:left}button,select{font:inherit;padding:7px 12px;border:1px solid #c8d2e3;background:white;border-radius:6px;margin:4px;cursor:pointer}.good{color:#116d42}.bad{color:#aa3e30}pre{font:13px/1.6 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f8;padding:16px;border-radius:8px;max-height:600px;overflow:auto}details{margin:10px 0}summary{cursor:pointer;font-weight:600}.note{padding:12px;border-left:4px solid #c48725;background:#fff8e9}a{color:#2869bb}@media(max-width:700px){main{padding:16px}.cards{grid-template-columns:repeat(2,1fr)}table{font-size:12px}td,th{padding:6px}}</style>
<main><h1>Argus × Argus-Pi：协同进化初版</h1><p class="sub">SkillsBench · 两道开发任务 → 冻结学习产物 → 三道未见任务 · 单次运行</p><p id="status"></p><p id="conclusion" class="note"></p><div class="cards" id="cards"></div><section><h2>未见任务对照</h2><p class="sub">主指标是原始验收整体通过。括号显示测试项通过情况，费用为模型报告的估算。</p><table id="scores"></table><p class="note">原始评分保留。发票题均识别对 50 张欺诈发票及原因，但 PO 占位符输出与验收不同。预算恢复题的参考平均值取五个年度，与工作簿其他已知平均值的六年度口径不一致。<a href="benchmark-audit.json">独立核算</a>。</p></section>
<section><h2>Pi 在运行中改了什么</h2><div id="timeline"></div><div id="code"></div></section>
<section><h2>实际生成的 Skill 与 Wiki</h2><p class="sub">可以直接查看原文；格式通过不等于内容正确，文档主观审阅与任务评分分开。</p><div id="docs"></div><p class="note">冻结后反例检查发现：CSV 逻辑记录号被 Wiki 写成物理行号；重复 CSV 列名会覆盖值；Manager Skill 因输出与写入约定未对齐而出现重复头部。原始文档保留，没有人工润色后再测。<a href="quality-probes.json">查看反例数据</a></p></section>
<section><h2>开发失败、修订与实验边界</h2><table id="training"></table><details><summary>Manager 文档审阅原文</summary><pre id="quality"></pre></details><p class="note">这里的自进化是工具层代码与 Skill/Wiki 更新，不是模型权重训练。控制器和发布器的人工修复没有计入模型自进化。样本只有三道未见任务，没有显著性结论；Skill 与 Wiki 合并评测。</p><p><a href="report.md">完整报告</a> · <a href="protocol.json">实验协议</a> · <a href="scores.csv">评分 CSV</a> · <a href="results.json">完整结构化结果</a></p></section></main>
<script>const D=__DATA__;const names={baseline:'基线',knowledge:'Skill + Wiki',pi_runtime:'Pi 运行时',joint:'联合'};const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const money=n=>'$'+Number(n||0).toFixed(3);document.querySelector('#status').textContent=(D.heldout_complete?'评测已完成':'实验运行中：未完成项显示为 —')+' · 网关请求 '+D.gateway_requests+' · 网关估算 '+money(D.gateway_estimated_usd);document.querySelector('#conclusion').textContent=D.heldout_complete?'机制跑通，尚未证明整体质量提升。联合组调用 33 → 23，总 token −2.8%，费用估算 −0.9%；学习产物仍有可复核缺陷。':'原始产物和失败记录可查看；不对未完成的对照下结论。';
document.querySelector('#cards').innerHTML=Object.entries(D.aggregate).map(([k,v])=>`<div class="card"><b>${names[k]}</b><div class="big">${v.tasks_passed} / ${v.completed_tasks}</div><div class="bar"><i style="width:${v.completed_tasks?v.tasks_passed/v.completed_tasks*100:0}%"></i></div><span>${money(v.cost_usd)} · ${v.model_calls} 模型事件</span></div>`).join('');
document.querySelector('#scores').innerHTML='<tr><th>任务</th>'+Object.values(names).map(n=>'<th>'+n+'</th>').join('')+'</tr>'+D.protocol.heldout_tasks.map(t=>'<tr><td>'+esc(t)+'</td>'+Object.keys(names).map(c=>{let r=D.evaluations.find(x=>x.task===t&&x.condition===c);return r?`<td><span class="${r.score.reward?'good':'bad'}">${r.score.reward?'通过':'未通过'}</span> (${r.score.passed}/${r.score.passed+r.score.failed})<br>${money(r.tokens.cost_usd)}<br><a href="runs/${esc(r.run)}/grading/pytest.txt">验收日志</a></td>`:'<td>—</td>'}).join('')+'</tr>').join('');
document.querySelector('#timeline').innerHTML=D.runtime_events.filter(e=>['runtime_revision_activated','runtime_revision_rejected','evolved_tool_used'].includes(e.kind)).map(e=>`<details><summary>${esc(e.timestamp)} · ${e.kind==='runtime_revision_activated'?'激活 v'+e.version:e.kind==='evolved_tool_used'?'使用 v'+e.version:'候选未通过'} ${esc(e.path||'')}</summary><p>${esc(e.rationale||e.reason||e.run)}</p><small>${esc(e.run)}</small></details>`).join('')||'<p>尚无激活记录。</p>';
document.querySelector('#code').innerHTML=D.runtime_source_provenance.map(p=>`<p><a href="${esc(p.path)}">${esc(p.path.split('/').pop())}：模型生成的执行代码</a> · 与原始工具调用 source 逐字匹配：${p.matches_agent_generated_source}</p>`).join('');
document.querySelector('#docs').innerHTML=D.documents.map(d=>`<details><summary>${d.kind==='skill'?'Skill':'Wiki'} · ${esc(d.title)} · ${d.characters} 字符</summary><p><a href="${esc(d.path)}">打开原文</a> · frontmatter ${d.frontmatter_valid?'通过':'有问题'}</p><pre>${esc(d.text)}</pre></details>`).join('')||'<p>尚无学习文档。</p>';
document.querySelector('#training').innerHTML='<tr><th>开发运行</th><th>测试项</th><th>任务通过</th><th>估算</th></tr>'+D.training.map(r=>`<tr><td>${esc(r.run)}</td><td>${r.score.passed}/${r.score.passed+r.score.failed}</td><td>${r.score.reward?'是':'否'}</td><td>${money(r.tokens.cost_usd)}</td></tr>`).join('');document.querySelector('#quality').textContent=JSON.stringify(D.manager_quality_review,null,2);</script></html>'''
(ROOT / 'report.html').write_text(page.replace('__DATA__', encoded))
print(json.dumps({'heldout_runs': len(evaluations), 'learning_documents': len(documents), 'runtime_versions': len(provenance),
                  'agent_source_provenance_verified': all(p['matches_agent_generated_source'] for p in provenance),
                  'report': str(ROOT / 'report.html')}, ensure_ascii=False))
