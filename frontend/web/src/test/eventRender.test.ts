import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it, expect } from 'vitest';
import { renderEvent, isReasoning, eventKey, mergeFragment } from '../lib/eventRender';
import { parseSSEFrames } from '../api';
import { activeGuardianAlert } from '../lib/guardian';
import type { EventMsg } from '../api';
import { EventStream } from '../components/EventStream';
import { EVENT_CORPUS } from '../../../core/src/eventCorpus.generated';
import {
  renderEvent as renderSemanticEvent,
  renderText,
  type RenderContext,
  type RenderModel,
} from '../../../core/src/eventRender';
import type { TypedArgusEvent } from '../../../core/src/eventPayloads.generated';

/** The clean whitelist renderer — noise is hidden, meaningful events get a
 *  role + glyph + line, matching the terminal cockpit. */
describe('renderEvent', () => {
  it('hides raw CLI framing + telemetry (the noise)', () => {
    expect(renderEvent({ type: 'agent.io.stream', text: 'raw' })).toBeNull();
    expect(renderEvent({ type: 'agent.io.start' })).toBeNull();
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

  it('keeps complete multi-line agent speech', () => {
    const text = `Implementation update\\n${'verification detail '.repeat(80)}`;
    const rendered = renderEvent({
      type: 'engineer.progress',
      kind: 'agent_message',
      text,
      agent_layer: 'engineer',
    } as EventMsg);

    expect(rendered?.text).toBe(text.trim());
    expect(rendered?.text.endsWith('…')).toBe(false);
  });

  it('hides role handoff fields from agent speech', () => {
    const rendered = renderEvent({
      type: 'engineer.progress',
      kind: 'agent_message',
      text: (
        'I need the operator to choose the report format.\n'
        + 'MILESTONE_STATUS=continue\n'
        + 'OPERATOR_QUESTION=Which format?\n'
        + 'OPERATOR_OPTIONS=markdown :: false :: Markdown :: Human-readable report'
      ),
      agent_layer: 'engineer',
    } as EventMsg);

    expect(rendered?.text).toBe('I need the operator to choose the report format.');
  });

  it('shows real command and tool details instead of generic summaries', () => {
    expect(renderEvent({
      type: 'engineer.progress',
      kind: 'command_execution',
      text: 'npm test -- --runInBand',
      action_summary: 'running project command',
      agent_layer: 'engineer',
    } as EventMsg)?.text).toBe('npm test -- --runInBand');
    expect(renderEvent({
      type: 'engineer.progress',
      kind: 'tool_use',
      text: 'read: {"path":"src/harness.ts","offset":1,"limit":2000}',
      action_summary: 'using a tool',
      agent_layer: 'engineer',
    } as EventMsg)?.text).toBe(
      'read: {"path":"src/harness.ts","offset":1,"limit":2000}',
    );
  });

  it('shows all Manager routing axes', () => {
    expect(renderEvent({
      type: 'life.manager.intent.completed',
      route: 'team',
      vertical: 'software',
      workflow_mode: 'staged',
      lifetime: 'bounded',
      continuous: true,
      open_ended: false,
    } as EventMsg)?.text).toBe(
      '→ TEAM · software · STAGED · BOUNDED · FINITE CONTINUOUS',
    );
  });

  it('leads Manager routing failures with structured facts and keeps raw error', () => {
    const event = {
      type: 'life.manager.intent.failed',
      phase: 'backend',
      cause: '401 Missing bearer',
      attempts: 2,
      error: 'VerticalDecisionError: routing failed [backend]: 401 Missing bearer',
    } as EventMsg;

    expect(renderEvent(event)?.text).toBe(
      'routing failed · backend 401 Missing bearer (attempt 2) · raw: '
      + 'VerticalDecisionError: routing failed [backend]: 401 Missing bearer',
    );
    expect(renderEvent(event, 'zh-CN')?.text).toContain(
      '分流失败 · 后端 401 Missing bearer (第2次尝试)',
    );
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

  it('hides legacy reviewer protocol payloads and empty phase markers', () => {
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
      status: 'done',
      success: true,
      summary: 'Created RESULT.txt and verified its contents.',
    } as EventMsg)).toMatchObject({
      text: 'Task completed · Created RESULT.txt and verified its contents.',
      tone: 'ok',
    });
    expect(renderEvent({
      type: 'life.mission.completed',
      status: 'done',
      success: true,
      final_submission_certified: true,
    } as EventMsg)).toMatchObject({
      text: 'Submission certified',
      tone: 'ok',
    });
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

    expect(html).toContain('Background activity');
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

const WEB_RENDER_CONTEXT: RenderContext = {
  locale: 'en',
  showReasoning: true,
  unknownEventPolicy: 'hide',
  density: 'compact',
};

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

describe('semantic renderer shadow comparison', () => {
  it('makes reasoning, locale, density, and unknown-event behavior explicit policies', () => {
    const reasoning = EVENT_CORPUS.fixtures.find((row) => row.id === 'engineer.progress.reasoning')!.event;
    expect(renderSemanticEvent(reasoning, { ...WEB_RENDER_CONTEXT, showReasoning: false }).visibility).toBe('hidden');

    const secret = EVENT_CORPUS.fixtures.find((row) => row.id === 'engineer.progress.secret-redaction')!.event;
    expect(renderSemanticEvent(secret, WEB_RENDER_CONTEXT).sensitive).toBe(true);

    const blocked = EVENT_CORPUS.fixtures.find((row) => row.id === 'life.lifecycle.block')!.event;
    expect(renderSemanticEvent(blocked, WEB_RENDER_CONTEXT).visibility).toBe('hidden');
    expect(renderSemanticEvent(blocked, { ...WEB_RENDER_CONTEXT, density: 'full' }).visibility).toBe('alert');

    const started = EVENT_CORPUS.fixtures.find((row) => row.id === 'life.manager.intent.started')!.event;
    expect(renderText(renderSemanticEvent(started, { ...WEB_RENDER_CONTEXT, locale: 'zh-CN' }))).toBe('判断任务归属…');

    const unknown = { type: 'future.event', text: 'kept for grep' } as unknown as TypedArgusEvent;
    expect(renderSemanticEvent(unknown, WEB_RENDER_CONTEXT).visibility).toBe('hidden');
    expect(renderText(renderSemanticEvent(unknown, { ...WEB_RENDER_CONTEXT, unknownEventPolicy: 'greppable' }))).toBe('[future.event] kept for grep');
  });

  it('matches current web semantics across the generated corpus except triaged old-renderer bugs', () => {
    const oldRendererBugs: Record<string, Partial<ReturnType<typeof semanticProjection>>> = {
      // The old web renderer leaks recognized credentials; the semantic core redacts them.
      'engineer.progress.secret-redaction': { text: 'using token <REDACTED:github-token>' },
      // Python follow already strips NEXT_OWNER; the old web renderer strips only the other handoff fields.
      'engineer.progress.handoff-fields': { text: 'Artifact complete.' },
      // A failed command was incorrectly dim in web even though TUI marks it as an error.
      'engineer.progress.failed-command': { tone: 'err', visibility: 'alert' },
      // The old label says duplicate even when policy deferred an unnecessary review purchase.
      'life.planner.task_skipped.review-purchase-deferred': { text: 'review purchase deferred Purchase another paper review' },
      // These catalog events currently fall through the web whitelist despite carrying operator meaning.
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
      const semantic = semanticProjection(renderSemanticEvent(fixture.event, WEB_RENDER_CONTEXT));
      const correction = oldRendererBugs[fixture.id];
      if (correction) {
        expect(semantic, fixture.id).toMatchObject(correction);
        expect(current, fixture.id).not.toEqual(semantic);
      } else {
        expect(semantic, fixture.id).toEqual(current);
      }
    }
  });
});
