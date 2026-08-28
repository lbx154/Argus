import assert from 'node:assert/strict';
import { PassThrough, Readable } from 'node:stream';
import { test } from 'node:test';

import { renderEvent } from '../src/eventRender.js';
import type { EventMsg } from '../src/api.js';
import { EVENT_CORPUS } from '../../core/src/eventCorpus.generated.js';
import {
  renderEvent as renderSemanticEvent,
  renderText,
  type RenderModel,
} from '../../core/src/eventRender/index.js';
import { parseRenderEventsArgs, runRenderEvents } from '../src/renderEvents.js';

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

function semanticProjection(value: ReturnType<typeof renderEvent> | RenderModel) {
  if (value === null || 'visibility' in value && value.visibility === 'hidden') {
    return { visibility: 'hidden', role: '', tone: '', text: '' };
  }
  if ('visibility' in value) {
    return { visibility: value.visibility, role: value.role, tone: value.tone, text: renderText(value) };
  }
  return {
    visibility: value.tone === 'err' || value.tone === 'warn' ? 'alert' : 'normal',
    role: value.role,
    tone: value.tone,
    text: value.text,
  };
}

test('semantic renderer shadows current TUI with full-density policy and triaged corrections', () => {
  const context = { locale: 'en', showReasoning: true, unknownEventPolicy: 'hide', density: 'full' } as const;
  const oldRendererBugs: Record<string, Partial<ReturnType<typeof semanticProjection>>> = {
    // The old TUI hard-codes Chinese for only three event families instead of honoring one locale policy.
    'life.manager.intent.started': { text: 'classifying request…' },
    'life.manager.intent.failed': { text: 'routing failed · backend 401 Missing bearer (attempt 2) · raw: VerticalDecisionError: routing failed' },
    'life.phase.started': { text: 'entering implementation' },
    // The old TUI leaks secrets and lags Python follow's complete handoff-field stripping.
    'engineer.progress.secret-redaction': { text: 'using token <REDACTED:github-token>' },
    'engineer.progress.handoff-fields': { text: 'Artifact complete.' },
    // These are semantic distinctions/events that the old whitelist currently loses.
    'life.planner.task_skipped.review-purchase-deferred': { text: 'review purchase deferred Purchase another paper review' },
    'life.planner.normalized': { text: 'normalized · removed duplicate planner task' },
    // The old renderer leaves a trailing space when this schema has no objective field.
    'life.planner.start': { text: 'planning' },
    'life.planner.waiting': { role: 'planner', visibility: 'normal' },
    'life.planner.waiting.waiting-resource': { text: 'waiting · subagent state waiting_resource is a healthy resource wait' },
    'life.planner.waiting_woken': { role: 'planner', visibility: 'normal' },
    'life.planner.terminal_idle': { role: 'planner', visibility: 'normal' },
    'life.planner.verification_probe': { role: 'planner', visibility: 'normal' },
  };

  for (const fixture of EVENT_CORPUS.fixtures) {
    const current = semanticProjection(renderEvent(fixture.event as EventMsg));
    const semantic = semanticProjection(renderSemanticEvent(fixture.event, context));
    const correction = oldRendererBugs[fixture.id];
    if (correction) {
      assert.partialDeepStrictEqual(semantic, correction, fixture.id);
      assert.notDeepEqual(current, semantic, fixture.id);
    } else {
      assert.deepEqual(semantic, current, fixture.id);
    }
  }
});

test('render-events streams semantic-core corpus events as one plain line per NDJSON record', async () => {
  const started = EVENT_CORPUS.fixtures.find((fixture) => fixture.id === 'life.manager.intent.started');
  const blocked = EVENT_CORPUS.fixtures.find((fixture) => fixture.id === 'life.lifecycle.block');
  assert.ok(started && blocked);
  const input = Readable.from([
    `${JSON.stringify(started.event)}\n`,
    `${JSON.stringify({ type: 'future.event', text: 'kept for grep' })}\n`,
    `${JSON.stringify(blocked.event)}\n`,
  ]);
  const output = new PassThrough();
  let rendered = '';
  output.on('data', (chunk) => { rendered += chunk.toString(); });

  await runRenderEvents(
    input,
    output,
    parseRenderEventsArgs([
      '--locale', 'zh-CN',
      '--unknown-event-policy', 'greppable',
      '--density', 'compact',
    ]),
  );

  assert.equal(
    rendered,
    '🧭 [Manager] 判断任务归属…\n• [Argus] [future.event] kept for grep\n\n',
  );
});
