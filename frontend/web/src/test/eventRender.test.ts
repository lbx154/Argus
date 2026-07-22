import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it, expect } from 'vitest';
import { renderEvent, isReasoning, eventKey, mergeFragment } from '../lib/eventRender';
import { parseSSEFrames } from '../api';
import { activeGuardianAlert } from '../lib/guardian';
import type { EventMsg } from '../api';
import { EventStream } from '../components/EventStream';

/** The clean whitelist renderer — noise is hidden, meaningful events get a
 *  role + glyph + line, matching the terminal cockpit. */
describe('renderEvent', () => {
  it('hides raw CLI framing + telemetry (the noise)', () => {
    expect(renderEvent({ type: 'agent.io.stream', text: 'raw' })).toBeNull();
    expect(renderEvent({ type: 'agent.io.start' })).toBeNull();
    expect(renderEvent({ type: 'life.telemetry', running: true } as EventMsg)).toBeNull();
    expect(renderEvent({ type: 'some.unknown.internal' })).toBeNull();
  });

  it('renders reasoning summaries as pale role context', () => {
    expect(renderEvent({
      type: 'engineer.progress',
      kind: 'reasoning',
      text: 'I should verify the smallest failing case first.',
      agent_layer: 'planner',
    } as EventMsg)).toMatchObject({
      role: 'planner',
      glyph: '∴',
      tone: 'dim',
      reasoning: true,
    });
  });

  it('renders an assistant message as a bright role line', () => {
    const r = renderEvent({ type: 'engineer.progress', kind: 'assistant_message', text: '你好', agent_layer: 'engineer' } as EventMsg);
    expect(r).not.toBeNull();
    expect(r!.role).toBe('engineer');
    expect(r!.text).toContain('你好');
    expect(r!.tone).toBe('bright');
  });

  it('renders operator and Manager conversation turns in Activity', () => {
    const operator = renderEvent({ type: 'ui.operator', text: '继续实验' } as EventMsg);
    const manager = renderEvent({ type: 'ui.argus', text: '已开始运行' } as EventMsg);
    expect(operator).toMatchObject({ role: 'operator', label: 'You', text: '继续实验' });
    expect(manager).toMatchObject({ role: 'manager', label: 'Argus', text: '已开始运行' });
  });

  it('colours a review verdict by status', () => {
    expect(renderEvent({ type: 'round.review.completed', status: 'done', reason: 'ok' } as EventMsg)!.tone).toBe('ok');
    expect(renderEvent({ type: 'round.review.completed', status: 'blocked', reason: 'x' } as EventMsg)!.tone).toBe('err');
    expect(renderEvent({ type: 'round.review.completed', status: 'continue', reason: 'x' } as EventMsg)!.tone).toBe('warn');
  });

  it('shows an engineer-requested bounded review deferral', () => {
    const rendered = renderEvent({
      type: 'round.review.deferred',
      round_index: 1,
      next_step: 'wire the parser into the runner',
    } as EventMsg);
    expect(rendered).toMatchObject({ role: 'engineer', tone: 'info' });
    expect(rendered!.text).toContain('wire the parser into the runner');
  });

  it('accepts round and round_index lifecycle schemas', () => {
    expect(renderEvent({ type: 'round.start', round: 1 } as EventMsg)!.text).toBe('round 1');
    expect(renderEvent({ type: 'round.started', round_index: 2 } as EventMsg)!.text).toBe('round 2');
  });

  it('surfaces the guardian (监视守护) signals that actually persist to the feed', () => {
    // These are the signals the daemon keeps in "signal" verbosity — the ones the
    // operator must see the guardian catch.
    const stall = renderEvent({ type: 'round.stall', text: 'no forward progress 2/3 rounds' } as EventMsg);
    expect(stall!.label).toBe('Notice');
    expect(stall!.tone).toBe('warn');
    const rbf = renderEvent({ type: 'round.reviewer_backend_failure', text: 'backend down' } as EventMsg);
    expect(rbf!.tone).toBe('err');
    expect(renderEvent({ type: 'life.lifecycle.block', reason: 'needs creds' } as EventMsg)).toBeNull();
  });

  it('surfaces ANY operator_alert event loud, even an unknown type', () => {
    const r = renderEvent({ type: 'some.new.guardian.signal', operator_alert: true, text: 'look here' } as EventMsg);
    expect(r).not.toBeNull();
    expect(r!.label).toBe('Notice');
    expect(r!.tone).toBe('err');
    expect(r!.text).toContain('look here');
  });

  it('raises a persistent budget alarm for denied provider spend', () => {
    const event = {
      type: 'budget.reservation.denied',
      reason: 'daily budget exhausted ($0.000000 available)',
    } as EventMsg;
    expect(renderEvent(event)).toMatchObject({
      label: 'Budget',
      tone: 'err',
      rule: true,
    });
    expect(activeGuardianAlert([event])).toEqual({
      tone: 'block',
      kind: 'budget',
      text: 'Budget exhausted or blocked — daily budget exhausted ($0.000000 available)',
    });
    expect(activeGuardianAlert([
      event,
      { type: 'ui.operator', text: 'retry' } as EventMsg,
      { type: 'round.start', round: 2 } as EventMsg,
    ])?.kind).toBe('budget');
    expect(activeGuardianAlert([
      event,
      { type: 'provider.request.started' } as EventMsg,
    ])).toBeNull();
  });

  it('hides reviewer protocol JSON and empty phase markers', () => {
    expect(renderEvent({
      type: 'engineer.progress', kind: 'agent_message', agent_layer: 'reviewer',
      text: '{"status":"done","reason":"verified"}',
    } as EventMsg)).toBeNull();
    expect(renderEvent({
      type: 'engineer.progress', kind: 'agent_message', agent_layer: 'reviewer',
      text: 'I am rerunning the tests.',
    } as EventMsg)!.text).toBe('I am rerunning the tests.');
    expect(renderEvent({ type: 'life.phase.started', agent_layer: 'reviewer' } as EventMsg)).toBeNull();
    expect(renderEvent({
      type: 'engineer.progress', kind: 'agent_message', agent_layer: 'planner',
      text: '{"steps":[{"title":"draft"}]}',
    } as EventMsg)).toBeNull();
  });

  it('renders the manager target_stage field', () => {
    const row = renderEvent({
      type: 'life.manager.stage_decision', action: 'advance',
      current_stage: 'inspect', target_stage: 'implement_cli', reason: 'verified',
    } as EventMsg);
    expect(row!.text).toContain('advance → implement_cli');
  });

  it('renders mission terminal outcomes truthfully for new and legacy events', () => {
    expect(renderEvent({
      type: 'life.mission.completed',
      status: 'research_incomplete',
      success: false,
    } as EventMsg)).toMatchObject({
      text: 'Mission incomplete',
      tone: 'warn',
    });
    expect(renderEvent({
      type: 'life.mission.completed',
      outcome_class: 'blocked',
      status: 'done',
      success: true,
    } as EventMsg)).toMatchObject({
      text: 'Mission blocked',
      tone: 'err',
    });
    expect(renderEvent({
      type: 'life.mission.completed',
      status: 'legacy_weird_status',
      success: false,
    } as EventMsg)).toMatchObject({
      text: 'Mission ended · legacy_weird_status',
      tone: 'info',
    });
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

    expect(html).toContain('Autonomous activity');
    expect(html).toContain('data-role="planner"');
    expect(html).toContain('data-role="engineer"');
    expect(html).toContain('data-role="reviewer"');
    expect(html).toContain('aria-expanded="true"');
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
});

describe('eventKey', () => {
  it('is stable per event and distinguishes different events', () => {
    const a: EventMsg = { type: 'mission.started', ts: 1, seq: 1 };
    const b: EventMsg = { type: 'mission.started', ts: 1, seq: 2 };
    expect(eventKey(a, 0)).toBe(eventKey(a, 0));
    expect(eventKey(a, 0)).not.toBe(eventKey(b, 0));
  });
  it('does not use REST/WS array position as identity', () => {
    const event: EventMsg = { type: 'mission.completed', ts: 2, status: 'done' };
    expect(eventKey(event, 0)).toBe(eventKey({ ...event }, 99));
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
