import assert from 'node:assert/strict';
import test from 'node:test';
import { workflowActorLabel, workflowClient } from '../../core/src/applicationWorkflows.js';

test('execution labels preserve the historical protocol without adding an agent role', () => {
  assert.equal(workflowActorLabel('host'), 'Tool execution');
  assert.equal(workflowActorLabel('host', '工具执行'), '工具执行');
  for (const role of ['coordinator', 'designer', 'verifier', 'reviewer']) {
    assert.equal(workflowActorLabel(role), role);
  }
});

test('shared workflow client preserves explicit authority and encodes project/document paths', async () => {
  const requests: { path: string; body?: unknown }[] = [];
  const get = async <T>(path: string): Promise<T> => {
    requests.push({ path });
    return JSON.parse('{}');
  };
  const post = async <T>(path: string, body: unknown): Promise<T> => {
    requests.push({ path, body });
    return JSON.parse('{}');
  };
  const client = workflowClient(get, post);
  await client.open('/work/my project');
  await client.action('work/1', { action: 'run', allow_model: true });
  await client.action('work/1', { action: 'recover' });
  await client.document('work/1', 'rtl/a #1.sv');
  assert.deepEqual(requests, [
    { path: '/api/application-workflows/open', body: { project: '/work/my project' } },
    { path: '/api/application-workflows/work%2F1', body: { action: 'run', allow_model: true } },
    { path: '/api/application-workflows/work%2F1', body: { action: 'recover' } },
    { path: '/api/application-workflows/work%2F1/document?path=rtl%2Fa%20%231.sv' },
  ]);
});
