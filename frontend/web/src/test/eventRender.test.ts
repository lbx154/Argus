import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it, expect } from 'vitest';
import { parseSSEFrames } from '../api';
import { activeGuardianAlert } from '../lib/guardian';
import type { EventMsg } from '../api';
import { EventStream } from '../components/EventStream';
import { EVENT_CORPUS } from '../../../core/src/eventCorpus.generated';
import { renderLine, type RenderContext } from '../../../core/src/eventRender';
import { eventKey, isReasoning, mergeFragment } from '../../../core/src/events';

/** The web feed reads every event through the shared renderer, in the
 *  compact density: one line per event, noise hidden. */
const WEB: RenderContext = {
  locale: 'en',
  showReasoning: true,
  unknownEventPolicy: 'hide',
  density: 'compact',
};
const line = (event: Record<string, unknown>, context: Partial<RenderContext> = {}) =>
  renderLine(event as EventMsg, { ...WEB, ...context });
const zh = (event: Record<string, unknown>) => line(event, { locale: 'zh-CN' });

describe('the web feed line', () => {
  it('hides raw CLI framing, telemetry and unknown types (the noise)', () => {
    expect(line({ type: 'agent.io.stream', text: 'raw' })).toBeNull();
    expect(line({ type: 'agent.io.start' })).toBeNull();
    expect(line({ type: 'usage.recorded' })).toBeNull();
    expect(line({ type: 'some.unknown.internal', text: 'kept for grep' })).toBeNull();
  });

  it('shows reasoning summaries as pale role context, and drops them on request', () => {
    const reasoning = {
      type: 'engineer.progress',
      kind: 'reasoning',
      text: 'I should verify the smallest failing case first.',
      agent_layer: 'planner',
    };
    expect(line(reasoning)).toMatchObject({ role: 'planner', label: 'Planner', glyph: '∴', tone: 'dim', reasoning: true });
    expect(line(reasoning, { showReasoning: false })).toBeNull();
  });

  it('renders an assistant message as a bright role line', () => {
    const r = line({ type: 'engineer.progress', kind: 'assistant_message', text: '你好', agent_layer: 'engineer' });
    expect(r).toMatchObject({ role: 'engineer', label: 'Engineer', tone: 'bright', reasoning: false });
    expect(r!.text).toContain('你好');
  });

  it('keeps complete multi-line agent speech', () => {
    const text = `Implementation update\n${'verification detail '.repeat(80)}`;
    const rendered = line({ type: 'engineer.progress', kind: 'agent_message', text, agent_layer: 'engineer' });
    expect(rendered?.text).toBe(text.trim());
    expect(rendered?.text.endsWith('…')).toBe(false);
  });

  it('hides role handoff fields from agent speech', () => {
    const rendered = line({
      type: 'engineer.progress',
      kind: 'agent_message',
      text: (
        'I need the operator to choose the report format.\n'
        + 'Decision:\n'
        + 'MILESTONE_STATUS=continue\n'
        + 'NEXT_OWNER=operator\n'
        + 'ROLE_DECISION=ask\n'
        + 'OPERATOR_QUESTION=Which format?\n'
        + 'OPERATOR_OPTIONS=markdown :: false :: Markdown :: Human-readable report'
      ),
      agent_layer: 'engineer',
    });
    expect(rendered?.text).toBe('I need the operator to choose the report format.');
  });

  it('redacts a credential that slipped into agent speech and says so', () => {
    const rendered = line({
      type: 'engineer.progress', kind: 'agent_message', agent_layer: 'engineer',
      text: 'using token ghp_abcdefghijklmnopqrstuvwxyz0123456789',
    });
    expect(rendered).toMatchObject({ text: 'using token <REDACTED:github-token>', sensitive: true });
  });

  it('shows real command and tool details instead of generic summaries', () => {
    expect(line({
      type: 'engineer.progress', kind: 'command_execution',
      text: 'npm test -- --runInBand', action_summary: 'running project command', agent_layer: 'engineer',
    })?.text).toBe('npm test -- --runInBand');
    expect(line({
      type: 'engineer.progress', kind: 'tool_use',
      text: 'read: {"path":"src/harness.ts","offset":1,"limit":2000}', action_summary: 'using a tool', agent_layer: 'engineer',
    })?.text).toBe('read: {"path":"src/harness.ts","offset":1,"limit":2000}');
    expect(line({
      type: 'engineer.progress', kind: 'command_execution', text: 'pytest -q', status: 'failed', agent_layer: 'engineer',
    })?.tone).toBe('err');
  });

  it('shows all Manager routing axes', () => {
    expect(line({
      type: 'life.manager.intent.completed',
      route: 'team', vertical: 'software', workflow_mode: 'staged', lifetime: 'bounded',
      continuous: true, open_ended: false,
    })?.text).toBe('→ TEAM · software · STAGED · BOUNDED · FINITE CONTINUOUS');
  });

  it('leads Manager routing failures with structured facts and keeps the raw error', () => {
    const event = {
      type: 'life.manager.intent.failed',
      phase: 'backend',
      cause: '401 Missing bearer',
      attempts: 2,
      error: 'VerticalDecisionError: routing failed [backend]: 401 Missing bearer',
    };
    expect(line(event)?.text).toBe(
      'could not work out where this request belongs · model service 401 Missing bearer (attempt 2) · error text: '
      + 'VerticalDecisionError: routing failed [backend]: 401 Missing bearer',
    );
    expect(zh(event)?.text).toContain('没能判断这个请求该归谁 · 模型服务 401 Missing bearer (第2次尝试)');
  });

  it('renders operator and Argus conversation turns as boundaries of the feed', () => {
    expect(line({ type: 'ui.operator', text: '继续实验' })).toMatchObject({ label: 'You', text: '继续实验', rule: true });
    expect(zh({ type: 'ui.operator', text: '继续实验' })?.label).toBe('你');
    expect(line({ type: 'ui.argus', text: '已开始运行' })).toMatchObject({ role: 'manager', label: 'Argus', text: '已开始运行', rule: true });
    // A reply still being worked on has steps before it has words: keep the row.
    expect(line({ type: 'ui.argus', text: '', steps: [{ label: 'reading' }] })).toMatchObject({ role: 'manager', text: '' });
    expect(line({ type: 'ui.argus', text: '' })).toBeNull();
    expect(line({ type: 'ui.operator', text: '   ' })).toBeNull();
  });

  it('colours a review verdict by status', () => {
    expect(line({ type: 'round.review.completed', status: 'done', reason: 'ok' })!.tone).toBe('ok');
    expect(line({ type: 'round.review.completed', status: 'blocked', reason: 'x' })!.tone).toBe('err');
    expect(line({ type: 'round.review.completed', status: 'continue', reason: 'x' })!.tone).toBe('warn');
  });

  it('presents an explicitly skipped review as an informational interruption, in both languages', () => {
    const event = { type: 'round.review.completed', status: 'continue', reason: 'Turn allowance reached.', review_skipped: true };
    expect(line(event)).toMatchObject({ tone: 'info', text: 'no review this round · Turn allowance reached.' });
    expect(zh(event)).toMatchObject({ tone: 'info', text: '这一轮没有审阅 · Turn allowance reached.' });
  });

  it('shows an engineer-requested bounded review deferral', () => {
    const rendered = line({ type: 'round.review.deferred', round_index: 1, next_step: 'wire the parser into the runner' });
    expect(rendered).toMatchObject({ role: 'engineer', tone: 'info' });
    expect(rendered!.text).toContain('wire the parser into the runner');
  });

  it('accepts both lifecycle schemas and the legacy type names', () => {
    expect(line({ type: 'round.start', round: 1 })).toMatchObject({ text: 'round 1', rule: true });
    expect(line({ type: 'round.started', round_index: 2 })!.text).toBe('round 2');
    expect(zh({ type: 'round.start', round: 2 })!.text).toBe('第 2 轮');
    expect(line({ type: 'mission.completed', status: 'done', success: true })).toMatchObject({
      text: 'The task was completed.', tone: 'ok', rule: true,
    });
    expect(line({ type: 'mission.error', reason: 'disk full' })).toMatchObject({ tone: 'err', text: 'this task failed disk full' });
  });

  it('surfaces the guardian signals that persist to the feed, blocks included', () => {
    const stall = line({ type: 'round.stall', text: 'no forward progress 2/3 rounds' });
    expect(stall).toMatchObject({ label: 'Notice', tone: 'warn' });
    expect(zh({ type: 'round.stall' })?.text).toBe('这一轮没有进展');
    expect(line({ type: 'round.reviewer_backend_failure', text: 'backend down' })).toMatchObject({ tone: 'err', rule: true });
    // A block that needs a person is never hidden from the feed.
    const blocked = line({ type: 'life.lifecycle.block', reason: 'needs creds' });
    expect(blocked).toMatchObject({ label: 'Watch', tone: 'err', rule: true, text: 'blocked — needs you · needs creds' });
    expect(zh({ type: 'life.lifecycle.block', reason: 'needs creds' })?.text).toBe('卡住了 — 需要你来处理 · needs creds');
  });

  it('names a failing model service instead of leaving the round anonymous', () => {
    expect(line({ type: 'round.backend_failure.backoff', round_index: 9, seconds: 15, text: 'Copilot CLI exited with code 1 · waiting 15s' }))
      .toMatchObject({ role: 'engineer', tone: 'warn', text: 'Copilot CLI exited with code 1 · waiting 15s' });
    expect(line({ type: 'round.backend_failure.backoff', round_index: 9, seconds: 15.2 })?.text)
      .toBe('the model service failed — waiting 15s before trying again');
    expect(zh({ type: 'round.backend_failure.backoff', seconds: 15 })?.text).toBe('模型服务出错 — 等待 15 秒后再试');
  });

  it('surfaces ANY operator_alert event loud, even an unknown type', () => {
    const r = line({ type: 'some.new.guardian.signal', operator_alert: true, text: 'look here' });
    expect(r).toMatchObject({ label: 'Notice', tone: 'err', rule: true });
    expect(r!.text).toContain('look here');
  });

  it('raises a persistent budget alarm for denied provider spend', () => {
    const event = { type: 'budget.reservation.denied', reason: 'daily budget exhausted ($0.000000 available)' };
    expect(line(event)).toMatchObject({ label: 'Budget', tone: 'err', rule: true });
    expect(zh(event)?.label).toBe('预算');
    expect(activeGuardianAlert([event as EventMsg])).toEqual({
      tone: 'block',
      kind: 'budget',
      text: 'Budget exhausted or blocked — daily budget exhausted ($0.000000 available)',
    });
    expect(activeGuardianAlert([
      event as EventMsg,
      { type: 'ui.operator', text: 'retry' } as EventMsg,
      { type: 'round.start', round: 2 } as EventMsg,
    ])?.kind).toBe('budget');
    expect(activeGuardianAlert([event as EventMsg, { type: 'provider.request.started' } as EventMsg])).toBeNull();
  });

  it('hides reviewer and planner protocol payloads and empty phase markers', () => {
    expect(line({
      type: 'engineer.progress', kind: 'agent_message', agent_layer: 'reviewer',
      text: '{"status":"done","reason":"verified"}',
    })).toBeNull();
    expect(line({
      type: 'engineer.progress', kind: 'agent_message', agent_layer: 'reviewer',
      text: 'I am rerunning the tests.',
    })!.text).toBe('I am rerunning the tests.');
    expect(line({ type: 'life.phase.started', agent_layer: 'reviewer' })).toBeNull();
    expect(line({
      type: 'engineer.progress', kind: 'agent_message', agent_layer: 'planner',
      text: '{"steps":[{"title":"draft"}]}',
    })).toBeNull();
  });

  it('renders the manager target_stage field', () => {
    const row = line({
      type: 'life.manager.stage_decision', action: 'advance',
      current_stage: 'inspect', target_stage: 'implement_cli', reason: 'verified',
    });
    expect(row!.text).toContain('advance → implement_cli');
  });

  it('renders mission terminal outcomes truthfully', () => {
    expect(line({
      type: 'life.mission.completed', status: 'done', success: true,
      summary: 'Created RESULT.txt and verified its contents.',
    })).toMatchObject({ text: 'The task was completed. · Created RESULT.txt and verified its contents.', tone: 'ok', rule: true });
    expect(line({ type: 'life.mission.completed', status: 'done', success: true, final_submission_certified: true }))
      .toMatchObject({ text: 'The final submission was checked and approved.', tone: 'ok' });
    expect(line({ type: 'life.mission.completed', status: 'research_incomplete', success: false }))
      .toMatchObject({ text: 'The task stopped with work still remaining.', tone: 'warn' });
    expect(line({ type: 'life.mission.completed', outcome_class: 'blocked', status: 'done', success: true }))
      .toMatchObject({ text: 'The task cannot continue until something outside it is resolved.', tone: 'err' });
    expect(line({ type: 'life.mission.completed', status: 'legacy_weird_status', success: false }))
      .toMatchObject({ text: 'The task ended without a recorded outcome.', tone: 'info' });
  });

  it('gives every catalog fixture the web shows a role label and a sentence', () => {
    for (const fixture of EVENT_CORPUS.fixtures) {
      const rendered = renderLine(fixture.event, WEB);
      if (!rendered) continue;
      expect(rendered.label, fixture.id).not.toBe('');
      expect(rendered.text, fixture.id).not.toMatch(/^\[[a-z.]+\]/);
      expect(renderLine(fixture.event, { ...WEB, locale: 'zh-CN' })?.label, fixture.id).not.toBe('');
    }
  });
});

describe('EventStream role grouping', () => {
  it('keeps autonomous work in per-role collapsible groups', () => {
    const html = renderToStaticMarkup(createElement(EventStream, {
      events: [
        { type: 'life.planner.task_added', title: 'Choose conjecture', ts: 1 },
        {
          type: 'engineer.progress', kind: 'agent_message', agent_layer: 'engineer',
          text: 'Checking authoritative sources.', ts: 2,
        },
        { type: 'round.review.started', round_index: 1, ts: 3 },
      ] as EventMsg[],
      connected: true,
      showReasoning: true,
      onToggleReasoning: () => undefined,
    }));

    expect(html).toContain('Background activity');
    expect(html).toContain('data-role="planner"');
    expect(html).toContain('data-role="engineer"');
    expect(html).toContain('data-role="reviewer"');
    expect(html).toContain('aria-expanded="true"');
  });

  it('does not mount details for collapsed role and system groups', () => {
    const html = renderToStaticMarkup(createElement(EventStream, {
      events: [
        {
          type: 'life.manager.intent.started', agent_layer: 'manager',
          text: 'collapsed manager history sentinel', ts: 1,
        },
        {
          type: 'life.manager.intent.completed', agent_layer: 'manager',
          text: 'manager latest summary', ts: 2,
        },
        {
          type: 'life.planner.task_added', agent_layer: 'planner',
          title: 'active planner detail sentinel', ts: 3,
        },
        {
          type: 'life.daemon.idle_timeout',
          text: 'collapsed system detail sentinel', ts: 4,
        },
      ] as EventMsg[],
      connected: true,
      showReasoning: true,
      onToggleReasoning: () => undefined,
    }));

    expect(html).toContain('active planner detail sentinel');
    expect(html).not.toContain('collapsed manager history sentinel');
    expect(html).not.toContain('collapsed system detail sentinel');
  });
});

describe('isReasoning', () => {
  it('only matches engineer.progress reasoning', () => {
    expect(isReasoning({ type: 'engineer.progress', kind: 'reasoning' })).toBe(true);
    expect(isReasoning({ type: 'engineer.progress', kind: 'assistant_message' })).toBe(false);
    expect(isReasoning({ type: 'mission.started' })).toBe(false);
  });
});

describe('mergeFragment', () => {
  it('appends new blocks (no content lost — the streaming fix)', () => {
    let acc = '';
    acc = mergeFragment(acc, 'block one');
    acc = mergeFragment(acc, 'block two');
    expect(acc).toBe('block one\nblock two');
  });
  it('replaces on a cumulative resend and skips duplicates', () => {
    expect(mergeFragment('你好', '你好！需要帮忙吗')).toBe('你好！需要帮忙吗'); // cumulative
    expect(mergeFragment('full message here', 'message')).toBe('full message here'); // dup/substring
  });
  it('uses protocol modes instead of guessing whether a block is cumulative', () => {
    expect(mergeFragment('old paragraph', 'corrected answer', 'snapshot')).toBe('corrected answer');
    expect(mergeFragment('final answer with stale tail', 'final answer', 'snapshot'))
      .toBe('final answer');
    expect(mergeFragment('same heading', 'same heading with details', 'append'))
      .toBe('same heading\nsame heading with details');
  });
  it('removes overlap from legacy fragments', () => {
    expect(mergeFragment(
      'first paragraph\nrepeated transition',
      'repeated transition\nfinal paragraph',
    )).toBe('first paragraph\nrepeated transition\nfinal paragraph');
  });
});

describe('eventKey', () => {
  it('is stable per event and distinguishes different events', () => {
    const a: EventMsg = { type: 'mission.started', ts: 1, seq: 1 };
    const b: EventMsg = { type: 'mission.started', ts: 1, seq: 2 };
    expect(eventKey(a)).toBe(eventKey(a));
    expect(eventKey(a)).not.toBe(eventKey(b));
  });
  it('is the same for the same row seen through REST replay and the live socket', () => {
    const event: EventMsg = { type: 'mission.completed', ts: 2, status: 'done' };
    expect(eventKey(event)).toBe(eventKey({ ...event }));
  });
});

describe('parseSSEFrames', () => {
  it('decodes whole frames and buffers the partial tail', () => {
    const { frames, rest } = parseSSEFrames(
      'data: {"type":"phase","label":"Manager · reading"}\n\n' +
        'data: {"type":"delta","text":"你好","message_id":"m1"}\n\n' +
        'data: {"type":"delta","text":"需要', // partial — no terminating blank line
    );
    expect(frames.map((f) => f.type)).toEqual(['phase', 'delta']);
    expect(frames[1].text).toBe('你好');
    expect(rest).toContain('需要');
  });

  it('reassembles a frame split across two chunks', () => {
    const a = parseSSEFrames('data: {"type":"del');
    expect(a.frames).toHaveLength(0);
    const b = parseSSEFrames(a.rest + 'ta","text":"hi"}\n\n');
    expect(b.frames).toHaveLength(1);
    expect(b.frames[0].text).toBe('hi');
  });

  it('skips a malformed data line without throwing', () => {
    const { frames } = parseSSEFrames('data: nope\n\ndata: {"type":"done","result":{"kind":"chat"}}\n\n');
    expect(frames).toHaveLength(1);
    expect(frames[0].type).toBe('done');
  });
});

describe('activeGuardianAlert', () => {
  const ev = (o: Record<string, unknown>) => o as EventMsg;
  it('pins the latest unresolved alert and clears it when work resumes', () => {
    expect(activeGuardianAlert([ev({ type: 'round.main.completed' })])).toBeNull();
    const blocked = activeGuardianAlert([ev({ type: 'life.lifecycle.block', reason: 'needs creds' })]);
    expect(blocked?.tone).toBe('block');
    expect(blocked?.text).toContain('needs creds');
    // any operator_alert:true surfaces
    expect(activeGuardianAlert([ev({ type: 'x.y', operator_alert: true, text: 'look' })])?.tone).toBe('block');
    // cleared once the mission moves on
    expect(
      activeGuardianAlert([ev({ type: 'round.stall', text: 's' }), ev({ type: 'round.main.completed' })]),
    ).toBeNull();
  });
});
