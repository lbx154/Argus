import itertools
import json

from paths import ROOT

root = ROOT
data = json.loads((root / 'results.json').read_text())
assert data['heldout_complete']
expected = set(itertools.product(data['protocol']['heldout_tasks'], data['protocol']['conditions']))
assert {(r['task'], r['condition']) for r in data['evaluations']} == expected
assert len(data['evaluations']) == 12
assert all(r['score']['tests'] > 0 for r in data['evaluations'])
assert all(r['returncode'] == 0 and not r['timed_out'] for r in data['evaluations'])
assert data['gateway_requests'] <= data['protocol']['total_request_cap']
assert data['gateway_estimated_usd'] < data['protocol']['total_cost_cap_usd']
assert all(r['matches_agent_generated_source'] for r in data['runtime_source_provenance'])
assert len(data['documents']) == 6
assert sum(not r['frontmatter_valid'] for r in data['documents']) == 1
assert all(r['correct_reasons'] == 50 and r['missing'] == r['extra'] == 0 for r in data['invoice_diagnostics'])
audit = data['benchmark_consistency_audit']
assert audit['science_five_year_average'] == audit['official_expected_science_average']
assert audit['science_six_year_average'] == audit['all_four_agents_science_average']
assert all(row['supplied_average'] == row['six_year_average'] != row['five_year_average'] for row in audit['other_supplied_averages'])
for row in data['evaluations']:
    run = root / 'runs' / row['run']
    prompt = (run / 'prompt.txt').read_text()
    body = (root / 'benchmark/tasks' / row['task'] / 'task.md').read_text().split('\n---\n', 1)[1].strip()
    assert body in prompt
    events = [json.loads(line) for line in (run / 'agent/runtime-events.jsonl').read_text().splitlines()]
    assert not any(event['kind'] == 'runtime_revision_activated' for event in events)
    if row['condition'] in ['baseline', 'knowledge']:
        assert not any(event['kind'] == 'evolved_tool_used' for event in events)
    if row['condition'] in ['knowledge', 'joint']:
        for document in data['documents']:
            assert document['text'] in prompt
result = {'verified': True, 'heldout_runs': 12, 'runtime_code_agent_authorship': True,
          'frozen_conditions_respected': True, 'original_scores_retained': True,
          'known_document_quality_defect_retained': True, 'reference_consistency_issue_verified': True,
          'agent_gateway_budget_respected': True}
(root / 'final-verification.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result))
