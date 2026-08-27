import assert from 'node:assert/strict';
import { test } from 'node:test';

import { renderEvent } from '../src/eventRender.js';
import type { EventMsg } from '../src/api.js';

test('renderEvent reports truthful terminal mission outcomes for new and legacy events', () => {
  assert.deepEqual(
    renderEvent({
      type: 'life.mission.completed',
      status: 'done',
      success: true,
      summary: 'Created RESULT.txt and verified its contents.',
    } as EventMsg),
    {
      role: 'engineer',
      label: 'Engineer',
      glyph: '🎉',
      text: 'Task completed · Created RESULT.txt and verified its contents.',
      tone: 'ok',
      rule: true,
    },
  );

  assert.deepEqual(
    renderEvent({
      type: 'life.mission.completed',
      status: 'done',
      success: true,
      final_submission_certified: true,
    } as EventMsg),
    {
      role: 'engineer',
      label: 'Engineer',
      glyph: '🎉',
      text: 'Submission certified',
      tone: 'ok',
      rule: true,
    },
  );

  assert.deepEqual(
    renderEvent({
      type: 'life.mission.completed',
      status: 'research_incomplete',
      success: false,
    } as EventMsg),
    {
      role: 'engineer',
      label: 'Engineer',
      glyph: '◌',
      text: 'Mission incomplete',
      tone: 'warn',
      rule: true,
    },
  );

  assert.deepEqual(
    renderEvent({
      type: 'life.mission.completed',
      outcome_class: 'blocked',
      status: 'done',
      success: true,
    } as EventMsg),
    {
      role: 'engineer',
      label: 'Engineer',
      glyph: '⛔',
      text: 'Mission blocked',
      tone: 'err',
      rule: true,
    },
  );

  assert.deepEqual(
    renderEvent({
      type: 'life.mission.completed',
      status: 'legacy_weird_status',
      success: false,
    } as EventMsg),
    {
      role: 'engineer',
      label: 'Engineer',
      glyph: '■',
      text: 'Mission ended · legacy_weird_status',
      tone: 'info',
      rule: true,
    },
  );
});

test('agent speech and task handoffs remain fully readable', () => {
  const message = `Implemented the harness.\n${'verification detail '.repeat(40)}`;
  const speech = renderEvent({
    type: 'engineer.progress',
    kind: 'agent_message',
    agent_layer: 'engineer',
    text: message,
  } as EventMsg);
  const task = renderEvent({
    type: 'loop.start',
    text: `task: ${'full task detail '.repeat(40)}`,
  } as EventMsg);

  assert.equal(speech?.text, message.trim());
  assert.equal(speech?.expand, true);
  assert.doesNotMatch(speech?.text ?? '', /…$/);
  assert.equal(task?.expand, true);
  assert.doesNotMatch(task?.text ?? '', /…$/);
});

test('Manager routing failures lead with structured facts and retain raw error', () => {
  const rendered = renderEvent({
    type: 'life.manager.intent.failed',
    phase: 'contract',
    cause: 'research_target_level got "phd", expected exploratory|publishable|doctoral',
    attempts: 2,
    error: 'ManagerClassificationContractError: raw contract failure',
  } as EventMsg);

  assert.match(
    rendered?.text ?? '',
    /^分流失败 · 契约： research_target_level got "phd", expected exploratory\|publishable\|doctoral \(第2次尝试\)/,
  );
  assert.match(rendered?.text ?? '', /原始错误: ManagerClassificationContractError/);
  assert.equal(rendered?.expand, true);
});

test('agent speech hides internal handoff fields', () => {
  const speech = renderEvent({
    type: 'engineer.progress',
    kind: 'agent_message',
    agent_layer: 'engineer',
    text: (
      'Waiting for the operator choice.\n'
      + 'MILESTONE_STATUS=continue\n'
      + 'OPERATOR_QUESTION=Which format?\n'
      + 'OPERATOR_OPTIONS=json :: false :: JSON :: Structured report'
    ),
  } as EventMsg);

  assert.equal(speech?.text, 'Waiting for the operator choice.');
});

test('commands and tools show their real details instead of generic summaries', () => {
  const command = renderEvent({
    type: 'engineer.progress',
    kind: 'command_execution',
    agent_layer: 'engineer',
    text: 'npm test -- --runInBand',
    action_summary: 'running project command',
    status: 'running',
  } as EventMsg);
  const tool = renderEvent({
    type: 'engineer.progress',
    kind: 'tool_use',
    agent_layer: 'engineer',
    text: 'read: {"path":"src/harness.ts","offset":1,"limit":2000}',
    action_summary: 'using a tool',
  } as EventMsg);

  assert.equal(command?.text, 'npm test -- --runInBand');
  assert.equal(command?.expand, true);
  assert.equal(tool?.text, 'read: {"path":"src/harness.ts","offset":1,"limit":2000}');
  assert.equal(tool?.expand, true);
});

test('manager routing shows topology, vertical, workflow, and lifetime', () => {
  const routed = renderEvent({
    type: 'life.manager.intent.completed',
    route: 'team',
    vertical: 'software',
    workflow_mode: 'staged',
    lifetime: 'standing',
    continuous: true,
    open_ended: true,
  } as EventMsg);

  assert.equal(routed?.text, '→ TEAM · software · STAGED · STANDING · OPEN-ENDED');
});
