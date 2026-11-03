import { roleSnapshot } from './serverSnapshot';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it, expect } from 'vitest';
import { parseSSEFrames } from '../api';
import type { EventMsg } from '../api';
import { EventStream } from './serverSnapshot';
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
  it('hides the developer log', () => {
    expect(line({ type: 'life.status', text: 'planner idle' })).toBeNull();
  });
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
    // A finished record: no typing cursor after the name.
    expect(r).toMatchObject({ role: 'engineer', label: 'Engineer', glyph: '', tone: 'bright', reasoning: false });
    expect(r!.text).toContain('你好');
  });

  it('keeps complete multi-line agent speech', () => {
    const text = `Implementation update\n${'verification detail '.repeat(80)}`;
    const rendered = line({ type: 'engineer.progress', kind: 'agent_message', text, agent_layer: 'engineer' });
    expect(rendered?.text).toBe(text.trim());
    expect(rendered?.text.endsWith('…')).toBe(false);
  });

  it('hides the baton line from agent speech', () => {
    const rendered = line({
      type: 'engineer.progress',
      kind: 'agent_message',
      text: (
        'I need the operator to choose the report format.\n'
        + 'Decision:\n'
        + '→ operator: Which format?'
      ),
      agent_layer: 'planner',
    });
    expect(rendered?.text).toBe('I need the operator to choose the report format.');
  });

  it('hides the host actions under Decision: and keeps a RESULT= sentence', () => {
    const say = (text: string) => line({ type: 'engineer.progress', kind: 'agent_message', text, agent_layer: 'manager' })?.text;
    expect(say('Not ready.\n\nDecision:\nACTION=hold\nTARGET_STAGE=delivery\nREASON=work remains')).toBe('Not ready.');
    expect(say('Built it.\nDecision:\nRESULT=Parser fixed.\n→ reviewer')).toBe('Built it.\nRESULT=Parser fixed.');
  });

  it('shows a credential-shaped token as written and flags it', () => {
    const rendered = line({
      type: 'engineer.progress', kind: 'agent_message', agent_layer: 'engineer',
      text: 'using token ghp_abcdefghijklmnopqrstuvwxyz0123456789',
    });
    // Masked text cannot be verified; ARGUS_SKILL_REDACT_SECRETS masks upstream.
    expect(rendered).toMatchObject({ text: 'using token ghp_abcdefghijklmnopqrstuvwxyz0123456789', sensitive: true });
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


  it('renders operator and Argus conversation turns as boundaries of the feed', () => {
    expect(line({ type: 'ui.operator', text: '继续实验' })).toMatchObject({ label: 'You', text: '继续实验', rule: true });
    expect(zh({ type: 'ui.operator', text: '继续实验' })?.label).toBe('你');
    expect(line({ type: 'ui.argus', text: '已开始运行' })).toMatchObject({ role: 'manager', label: 'Argus', text: '已开始运行', rule: true });
    // A pending or stopped reply has a state before it has final words.
    expect(line({ type: 'ui.argus', text: '', status: 'stopped' })).toMatchObject({ role: 'manager', text: '' });
    expect(line({ type: 'ui.argus', text: '', status: 'replaced' })).toMatchObject({ role: 'manager', text: '' });
    expect(line({ type: 'ui.argus', text: '', steps: ['Checking the result'] })).toMatchObject({ role: 'manager', text: '' });
    expect(line({ type: 'ui.argus', text: '', process: ['Checking the result'] })).toMatchObject({ role: 'manager', text: '' });
    expect(line({ type: 'ui.argus', text: '', steps: [] })).toBeNull();
    expect(line({ type: 'ui.argus', text: '' })).toBeNull();
    expect(line({ type: 'ui.operator', text: '   ' })).toBeNull();
  });


  it('says a task parked on a question waits on the operator, not that the work is stuck', () => {
    expect(zh({ type: 'life.mission.completed', status: 'blocked', outcome_class: 'blocked', success: false, operator_question: 'A or B?' }))
      .toMatchObject({ text: '任务已暂停，因为有个问题在等你回答；进度已保存，可以继续。' });
  });







  it('surfaces ANY operator_alert event loud, even an unknown type', () => {
    const r = line({ type: 'some.new.guardian.signal', operator_alert: true, text: 'look here' });
    expect(r).toMatchObject({ label: 'Notice', tone: 'err', rule: true });
    expect(r!.text).toContain('look here');
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



  it('renders mission terminal outcomes truthfully', () => {
    // Without an independent review the summary is the only account of the result.
    expect(line({
      type: 'life.mission.completed', status: 'done', success: true,
      summary: 'Created RESULT.txt and verified its contents.',
    })).toMatchObject({ text: 'The task was completed. · Created RESULT.txt and verified its contents.', tone: 'ok', rule: true });
    // A reviewed task's conclusion is on the review row; this row says it once.
    expect(line({
      type: 'life.mission.completed', status: 'done', success: true, review_source: 'reviewer',
      summary: 'Created RESULT.txt and verified its contents.',
    })).toMatchObject({ text: 'The task was completed.', tone: 'ok', rule: true });
    expect(line({ type: 'life.mission.completed', status: 'done', success: true, final_submission_certified: true }))
      .toMatchObject({ text: 'The final submission was checked and approved.', tone: 'ok' });
    expect(line({ type: 'life.mission.completed', status: 'research_incomplete', success: false }))
      .toMatchObject({ text: 'The task stopped with work still remaining.', tone: 'warn' });
    expect(line({ type: 'life.mission.completed', outcome_class: 'blocked', status: 'done', success: true }))
      .toMatchObject({ text: 'The task cannot continue until something outside it is resolved.', tone: 'err' });
    expect(line({ type: 'life.mission.completed', status: 'legacy_weird_status', success: false }))
      .toMatchObject({ text: 'The task ended.', tone: 'info' });
  });

});

describe('EventStream role grouping', () => {
  it('keeps autonomous work in per-role collapsible groups', () => {
    const html = renderToStaticMarkup(createElement(EventStream, {
      events: [], snapshot: roleSnapshot([
        { type: 'engineer.progress', kind: 'agent_message', agent_layer: 'planner', text: 'Choose conjecture', ts: 1 },
        {
          type: 'engineer.progress', kind: 'agent_message', agent_layer: 'engineer',
          text: 'Checking authoritative sources.', ts: 2,
        },
        { type: 'engineer.progress', kind: 'agent_message', agent_layer: 'reviewer', text: 'Reviewing round 1.', ts: 3 },
      ] as EventMsg[]),
      connected: true,
      showReasoning: true,
    }));

    expect(html).toContain('Team progress');
    expect(html).toContain('data-role="planner"');
    expect(html).toContain('data-role="engineer"');
    expect(html).toContain('data-role="reviewer"');
    expect(html).not.toContain('aria-expanded="true"');
  });

  it('does not mount details for collapsed role and system groups', () => {
    const html = renderToStaticMarkup(createElement(EventStream, {
      events: [], snapshot: roleSnapshot([
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
      ] as EventMsg[]),
      connected: true,
      showReasoning: true,
    }));

    expect(html).not.toContain('active planner detail sentinel');
    expect(html).not.toContain('manager latest summary');
    expect(html).not.toContain('data-role-headline');
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
