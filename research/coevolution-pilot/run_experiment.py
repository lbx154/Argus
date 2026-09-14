import argparse
import collections
import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

from bench_common import OUTPUTS, ROOT, command, cwd, grade, start, stop
from paths import CODE_ROOT

sys.path.insert(0, os.environ.get('PILOT_ARGUS_SOURCE') or str(CODE_ROOT.parent.parent))
from types import SimpleNamespace

from argus_skill.roles.prompts.engineer import build_mission_prompt
from argus_skill.skills.role_memory import role_skill_maintenance_block
from argus_skill.skills.store import SkillStore
from argus_skill.wiki.bootstrap import init_wiki

PROTOCOL = json.loads((ROOT / 'protocol.json').read_text())
CURRENT = ROOT / 'learning/current'
FROZEN = ROOT / 'learning/frozen'

def initialize():
    (CURRENT / 'skills/engineer').mkdir(parents=True, exist_ok=True)
    (CURRENT / 'skills/manager').mkdir(parents=True, exist_ok=True)
    wiki = init_wiki('coevolution', base=CURRENT)
    if not (CURRENT / 'wiki').exists():
        (CURRENT / 'wiki').symlink_to(wiki.relative_to(CURRENT), target_is_directory=True)
    (CURRENT / 'pi').mkdir(exist_ok=True)

def task_body(task):
    text = (ROOT / 'benchmark/tasks' / task / 'task.md').read_text()
    return text.split('\n---\n', 1)[1].strip()

def knowledge_text(directory):
    parts = []
    for path in SkillStore(directory / 'skills').iter_paths():
        parts.append(f'### Learned Skill: {path.relative_to(directory)}\n{path.read_text()}')
    wiki = directory / 'wiki'
    for path in sorted((wiki / 'pages').rglob('*.md')) if wiki.exists() else []:
        parts.append(f'### Wiki: {path.relative_to(wiki)}\n{path.read_text()}')
    return '\n\n'.join(parts)

def prepare_learning(run, condition, training):
    if training:
        return CURRENT
    selected = run / 'learning'
    selected.mkdir(exist_ok=True)
    if condition in ['knowledge', 'joint']:
        shutil.copytree(FROZEN / 'skills', selected / 'skills')
        shutil.copytree(FROZEN / 'wiki', selected / 'wiki')
    if condition in ['pi_runtime', 'joint']:
        shutil.copytree(FROZEN / 'pi', selected / 'pi')
    return selected

def parse_usage(log):
    counts = collections.Counter()
    usage = collections.Counter()
    replies = []
    for line in log.read_text(errors='replace').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('type') == 'tool_execution_start':
            counts[event.get('toolName', 'unknown')] += 1
        message = event.get('message', {})
        if event.get('type') == 'message_end' and message.get('role') == 'assistant':
            for key in ['input', 'output', 'cacheRead', 'cacheWrite', 'reasoning', 'totalTokens']:
                usage[key] += message.get('usage', {}).get(key, 0)
            usage['cost_usd'] += message.get('usage', {}).get('cost', {}).get('total', 0)
            usage['model_calls'] += 1
            text = ''.join(item.get('text', '') for item in message.get('content', []) if item.get('type') == 'text')
            if text:
                replies.append(text)
    return {'tokens': dict(usage), 'tools': dict(counts), 'last_reply': replies[-1] if replies else ''}

def configure_pi(run, run_id):
    target = run / 'agent/pi-config'
    target.mkdir(parents=True, exist_ok=True)
    model = json.loads((CODE_ROOT / 'model.json').read_text())
    assert model['id'] == PROTOCOL['model'], 'Model configuration must match the protocol'
    (target / 'models.json').write_text(json.dumps({'providers': {'argus': {
        'name': 'Isolated experiment Copilot gateway', 'baseUrl': 'http://127.0.0.1:18765/v1',
        'apiKey': 'experiment-local', 'headers': {'X-Experiment-Run': run_id}, 'models': [model],
    }}}, indent=2))

def execute(task, condition, training=False, review_prompt=None, cycle='', no_tools=False):
    run_id = ('review-' if review_prompt else 'train-' if training else 'eval-') + task + '-' + condition
    if cycle:
        run_id += '-' + cycle
    run = ROOT / 'runs' / run_id
    if (run / 'result.json').exists():
        return json.loads((run / 'result.json').read_text())
    run.mkdir(parents=True, exist_ok=True)
    configure_pi(run, run_id)
    learning = prepare_learning(run, condition, training)
    text = task_body(task)
    learned = knowledge_text(learning) if training or condition in ['knowledge', 'joint'] else ''
    prompt = build_mission_prompt(task=text, skill_text=learned, next_action=None,
                                  include_static=True, compact_team=True, require_post_task_learning=training,
                                  project_root=None,
                                  project_skill_dir=Path('/learning/skills/engineer'))
    common = '''You are executing a bounded Argus Engineer experiment through Argus-Pi. Work on the supplied original benchmark task using its real input files. Do not launch other agents or services. The verifier and reference answers are not available. Do not seek them. Validate your output against the task's stated requirements. The original benchmark instructions below are authoritative for the deliverable.
You have a limited number of model turns: inspect inputs, implement, validate, then finish. Avoid dumping entire data files.
'''
    if review_prompt:
        common = 'You are performing a bounded Argus Manager review of development artifacts. Do not launch other agents, access heldout tasks or alter the evaluation protocol.\n'
    budget_calls = 12 if review_prompt else PROTOCOL['training_model_calls'] if training else PROTOCOL['heldout_model_calls']
    common += f"Budget: at most {budget_calls} model requests and {PROTOCOL['training_wall_seconds'] if training else PROTOCOL['heldout_wall_seconds']} seconds; reserve time for validation and requested learning artifacts.\n"
    if training:
        prompt += '''\nThis training run also studies co-evolution while working. Keep reusable procedures in /learning/skills/engineer/ with exactly name and description YAML frontmatter; update existing relevant Skills rather than duplicating them. Keep measured facts, support limits and known uncertainties in /learning/wiki/pages/ with exactly title and description frontmatter, cite observation IDs or source input paths, and update /learning/wiki/INDEX.md. Do not store expected benchmark answers, IDs or record mappings as reusable knowledge. At most two concise Skills and two Wiki pages per task.
After at least three useful tool results, inspect the execution friction. If a reusable CSV/XLSX/PDF inspection helper would remove repeated inspection code, use evolve_runtime to propose its actual Python source, citing two observation IDs. The runtime validates it before installing it. An accepted helper must be used via inspect_data on a task input during this same run. One accepted revision per training task; extend the earlier helper only when observed evidence justifies the change. All source inspection belongs in tools; do not claim a tool improved unless it was actually activated and used.
'''
        if cycle:
            prompt += '\nThis is a further development cycle on a training task. Earlier runtime candidates are under /learning/pi/history and measured development feedback is under /learning/feedback. Inspect them and improve an existing candidate if useful; do not copy benchmark-specific answers into reusable tools or Skills. The publisher now accepts both work observations and validator failures as evidence.\n'
    if review_prompt:
        store = SimpleNamespace(skills_dir=Path('/learning/skills'))
        prompt = review_prompt + '\n' + role_skill_maintenance_block(store, 'manager', enabled=True)
    else:
        assert text in prompt, 'A new benchmark task must receive its full original instructions'
        if learned:
            assert learned in prompt, 'Knowledge treatment must receive its frozen documents'
    (run / 'prompt.txt').write_text(common + prompt)
    name = 'coevo-' + run_id
    mounts = [(CODE_ROOT / 'harness', '/opt/pilot', True), (run / 'agent', '/experiment', False),
              (learning, '/learning', not training), (ROOT / 'socket', '/provider-socket', True)]
    if review_prompt:
        mounts.append((learning / 'pi', '/learning/pi', True))
    started = time.time()
    returncode = None
    timeout = False
    try:
        start(name, task, mounts)
        command(['docker', 'exec', '-d', name, 'python', '/opt/argus/argus_skill/trial/socket_forward.py',
                 '18765', '/provider-socket/model.sock'])
        args = ['docker', 'exec', '-i', '-w', cwd(task),
                '-e', 'PI_CODING_AGENT_DIR=/experiment/pi-config', '-e', 'PI_HARNESS_PROFILE=argus',
                '-e', 'PI_OFFLINE=1', '-e', f'PILOT_LEARNING={int(training and not review_prompt)}',
                '-e', f'PILOT_CONDITION={condition}', '-e', 'ARGUS_SKILL_ALLOW_NESTED_TEAM=0',
                '-e', 'ARGUS_SKILL_HOME=/experiment/argus-state', name,
                '/usr/local/bin/argus-pi', '--mode', 'json', '--model', 'argus/' + PROTOCOL['model'],
                '--thinking', PROTOCOL['reasoning'], '--no-session', '--no-extensions', '--no-skills',
                '--no-prompt-templates', '--no-themes', '--no-context-files', '--no-approve']
        args += ['--no-tools'] if no_tools else ['--extension', '/opt/pilot/runtime-extension.mjs']
        with (run / 'trajectory.jsonl').open('w') as stdout, (run / 'stderr.txt').open('w') as stderr:
            agent_started = time.time()
            process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr, text=True)
            try:
                process.communicate(common + prompt, timeout=PROTOCOL['training_wall_seconds'] if training else PROTOCOL['heldout_wall_seconds'])
                returncode = process.returncode
            except subprocess.TimeoutExpired:
                timeout = True
                process.kill()
                process.wait()
            agent_seconds = time.time() - agent_started
        command(['docker', 'stop', '-t', '1', name])
        output = run / Path(OUTPUTS[task]).name
        copied = subprocess.run(['docker', 'cp', name + ':' + OUTPUTS[task], str(output)], capture_output=True)
    finally:
        stop(name)
    result = {'run': run_id, 'task': task, 'condition': condition, 'training': training,
              'returncode': returncode, 'timed_out': timeout, 'wall_seconds': time.time() - started,
              'agent_seconds': agent_seconds,
              'output_present': copied.returncode == 0, **parse_usage(run / 'trajectory.jsonl')}
    if not review_prompt:
        grader = 'coevo-grade-' + run_id
        try:
            start(grader, task, [(ROOT / 'benchmark/tasks' / task / 'verifier', '/verifier', True)])
            if result['output_present']:
                command(['docker', 'cp', str(output), grader + ':' + OUTPUTS[task]])
            result['score'] = grade(grader, task, run / 'grading')
        finally:
            stop(grader)
    (run / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    if training:
        snapshot = ROOT / 'learning/snapshots' / run_id
        shutil.copytree(CURRENT, snapshot, symlinks=True, dirs_exist_ok=True)
    print(json.dumps({key: result.get(key) for key in ['run', 'returncode', 'timed_out', 'score', 'wall_seconds', 'tokens', 'tools']}), flush=True)
    return result

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['train', 'train-repair', 'review', 'evaluate'])
    args = parser.parse_args()
    if ROOT == CODE_ROOT:
        parser.error('Set PILOT_ROOT to a new directory; published evidence is read-only')
    initialize()
    if args.stage in ['train', 'train-repair']:
        if args.stage == 'train-repair':
            feedback = CURRENT / 'feedback'
            feedback.mkdir(exist_ok=True)
            for result_path in sorted((ROOT / 'runs').glob('train-*/result.json')):
                result = json.loads(result_path.read_text())
                trace = result_path.parent / 'agent/runtime-events.jsonl'
                failures = [json.loads(line) for line in trace.read_text().splitlines()
                            if json.loads(line).get('kind') == 'runtime_revision_rejected']
                (feedback / (result['run'] + '.json')).write_text(json.dumps({
                    'run': result['run'], 'score': result['score'], 'runtime_validation_failures': failures,
                    'development_verifier_diagnostics': (result_path.parent / 'grading/pytest.txt').read_text()[-14000:],
                }, indent=2))
        for task in PROTOCOL['training_tasks']:
            execute(task, 'joint', training=True, cycle='cycle2' if args.stage == 'train-repair' else '')
    elif args.stage == 'review':
        summaries = []
        for result_path in sorted((ROOT / 'runs').glob('train-*/result.json')):
            result = json.loads(result_path.read_text())
            summaries.append({key: result[key] for key in ['task', 'score', 'tokens', 'tools', 'timed_out']})
        prompt = '''You are the Argus Manager reviewing only the development experience before freezing knowledge for unseen tasks. Review /learning/skills and /learning/wiki, plus the active runtime helper and its history metadata under /learning/pi. Keep procedures in Skills and sourced facts/support limits in Wiki. Do not edit Engineer-owned Skills: record their needed corrections in your review. You may correct shared Wiki claims only when the source evidence supports the correction; preserve uncertainty.
Write /learning/quality-review.json with a per-document assessment of specificity, evidence, transferability, limitations (1–5 each), concrete issues, and verdict. Write one concise Manager Skill on a reusable orchestration/review lesson supported by the development traces; include its scope and one counterexample. Also maintain one scoped Wiki page about Argus-Pi's actually verified runtime capabilities and limitations, with links to the learned helper/history evidence, so Argus retains knowledge about its own execution tools. This Wiki page must describe verified facts, not task history or evaluator scores.
Do not invent accuracy gains or unseen-task results. Do not modify /learning/pi or call evolve_runtime.\nDevelopment summaries:\n''' + json.dumps(summaries) + '\nLearned documents:\n' + knowledge_text(CURRENT)
        execute(PROTOCOL['training_tasks'][-1], 'joint', training=True, review_prompt=prompt)
        if FROZEN.exists():
            raise RuntimeError('Heldout knowledge already frozen')
        shutil.copytree(CURRENT, FROZEN, symlinks=True)
        print('Knowledge and Pi runtime frozen before heldout evaluation', flush=True)
    else:
        assert FROZEN.exists(), 'Run training and review first'
        order = []
        randomizer = random.Random(20260914)
        for task in PROTOCOL['heldout_tasks']:
            conditions = list(PROTOCOL['conditions'])
            randomizer.shuffle(conditions)
            order.extend((task, condition) for condition in conditions)
        (ROOT / 'evaluation-order.json').write_text(json.dumps(order, indent=2) + '\n')
        for task, condition in order:
            execute(task, condition)

if __name__ == '__main__':
    main()
