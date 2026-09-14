import datetime as dt
import json
import shutil

from paths import CODE_ROOT
from run_experiment import CURRENT, FROZEN, PROTOCOL, ROOT, execute, knowledge_text

from argus_skill.skills.store import Skill, SkillStore
from argus_skill.wiki.schema import WikiPage
from argus_skill.wiki.store import WikiStore
from argus_skill.wiki.validate import validate_wiki_structure

assert ROOT != CODE_ROOT, 'Set PILOT_ROOT to a new directory; published learning is read-only'
assert not list((ROOT / 'runs').glob('eval-*/result.json')), 'Never revise frozen learning after seeing heldout results'
if FROZEN.exists():
    FROZEN.rename(ROOT / 'learning/frozen-incomplete-review')
summaries = []
for file in sorted((ROOT / 'runs').glob('train-*/result.json')):
    result = json.loads(file.read_text())
    summaries.append({key: result[key] for key in ['run', 'task', 'score', 'tokens', 'tools']})
runtime = (CURRENT / 'pi/inspect_data.py').read_text()
history = [json.loads(file.read_text()) for file in sorted((CURRENT / 'pi/history').glob('v*.json'))]
prompt = '''You are the Argus Manager finishing the development review from supplied evidence. A prior tool-based review exhausted its call budget while reading files; no review documents were completed. All needed documents and runtime source are provided below, so do not request tools.
Return ONLY one valid JSON object with keys:
- quality_review: object with documents (one assessment per existing Skill/Wiki, each including path, specificity/evidence/transferability/limitations scores 1–5, concrete issues, and verdict), overall_verdict, and uncertainties. Do not claim unseen-task improvements.
- manager_skill: {path: a semantic filename ending .md, name, description, content}. Write a concise reusable orchestration/review procedure grounded in this development: distinguish validation from real-task use, keep evidence and scope, include a counterexample. Do not embed benchmark answers or score thresholds.
- runtime_wiki: {path: a semantic filename ending .md, title, description, content}. Record verified facts about the learned Argus-Pi inspection runtime, its input/output contract and limitations. Cite the source and version metadata under pi/history. State assumptions such as XLSX first-row headers and cached formula values where applicable. Facts and limits belong here; procedures belong in the Skill. No task history or evaluator score ledger.
Do not rewrite Engineer-owned Skills; flag their defects in the review. The executor will persist your exact fields using Argus SkillStore and WikiStore.\n'''
prompt += '\nDevelopment measurements:\n' + json.dumps(summaries)
prompt += '\nExisting documents:\n' + knowledge_text(CURRENT)
prompt += '\nRuntime source (/learning/pi/inspect_data.py):\n' + runtime
prompt += '\nRuntime validated activations:\n' + json.dumps(history)
result = execute(PROTOCOL['training_tasks'][-1], 'joint', training=True, review_prompt=prompt,
                 cycle='final', no_tools=True)
assert result['returncode'] == 0, result['last_reply']
text = result['last_reply'].strip()
if text.startswith('```'):
    text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
document = json.loads(text)
(CURRENT / 'quality-review.json').write_text(json.dumps(document['quality_review'], ensure_ascii=False, indent=2) + '\n')
skill = document['manager_skill']
if skill['path'].startswith('skills/manager/'):
    skill['path'] = skill['path'][len('skills/manager/'):]
elif skill['path'].startswith('manager/'):
    skill['path'] = skill['path'][len('manager/'):]
skill_path = SkillStore(CURRENT / 'skills/manager').save(Skill(**skill))
page = document['runtime_wiki']
if page['path'].startswith('pages/'):
    page['path'] = page['path'][len('pages/'):]
wiki_path = WikiStore(CURRENT / 'wiki').write_page(page['path'], WikiPage(
    title=page['title'], description=page['description'], content=page['content']))
index = CURRENT / 'wiki/INDEX.md'
index.write_text(index.read_text().rstrip() + f"\n- [{page['title']}](pages/{page['path']})\n")
validate_wiki_structure(WikiStore(CURRENT / 'wiki'))
shutil.copytree(CURRENT, FROZEN, symlinks=True)
(ROOT / 'freeze.json').write_text(json.dumps({
    'frozen_at': dt.datetime.now(dt.timezone.utc).isoformat(),
    'before_heldout_evaluation': True, 'manager_skill': str(skill_path), 'runtime_wiki': str(wiki_path),
    'final_review_run': result['run'],
    'note': 'Tool-free completion uses the same development evidence after the initial Manager review hit its budget; initial failed review retained.',
}, indent=2) + '\n')
print(json.dumps({'frozen': True, 'manager_skill': str(skill_path), 'runtime_wiki': str(wiki_path)}), flush=True)
