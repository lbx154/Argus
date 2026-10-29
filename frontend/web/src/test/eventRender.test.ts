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
  it('shows the advisor question and answer, and only applied supervision as a team decision', () => {
    const answered = zh({ type: 'advisor.consultation.completed', question: 'Why did the measurement change?', summary: 'The second run used a different batch size. Repeat with batch size 16.' });
    expect(answered).toMatchObject({ role: 'advisor', label: '顾问' });
    expect(answered?.text).toContain('batch size 16');
    expect(answered?.text).toContain('Why did the measurement change?');
    const proposal = { type: 'life.manager.supervision.issued', summary: 'Repeat both runs with batch size 16.', action: 'steer' };
    expect(line(proposal)).toBeNull();
    expect(line({ ...proposal, type: 'life.manager.supervision.applied' })?.text).toBe(proposal.summary);
    expect(line({ ...proposal, type: 'life.manager.supervision.failed', status: 'superseded' })).toBeNull();
  });
  it('shows the status the operator is told and hides the developer log', () => {
    expect(line({ type: 'life.operator_status', kind: 'service_back', text: 'The model service is answering again.' }))
      .toMatchObject({ text: 'The model service is answering again.' });
    expect(line({ type: 'life.status', text: 'planner idle' })).toBeNull();
  });
  it('shows a remark of the Observer with whom it is for, under its own name', () => {
    const remark = { type: 'life.observer.note', agent_layer: 'observer', to: 'manager', text: 'the Planner woke 14 times on the same running job' };
    expect(zh(remark)).toMatchObject({ role: 'observer', label: '监督者', text: '对经理：the Planner woke 14 times on the same running job' });
    expect(line({ ...remark, to: 'operator', operator_alert: true })).toMatchObject({ label: 'Observer', text: 'to You: the Planner woke 14 times on the same running job' });
    expect(activeGuardianAlert([{ ...remark, to: 'operator', operator_alert: true } as EventMsg])?.tone).toBe('block');
  });
  it('hides raw CLI framing, telemetry and unknown types (the noise)', () => {
    expect(line({ type: 'agent.io.stream', text: 'raw' })).toBeNull();
    expect(line({ type: 'agent.io.start' })).toBeNull();
    expect(line({ type: 'usage.recorded' })).toBeNull();
    expect(line({ type: 'some.unknown.internal', text: 'kept for grep' })).toBeNull();
  });

  it('hides the grounding note of a chat config change but keeps operator notes', () => {
    const note = { type: 'user.note', title: 'manual note', summary: "Set all Argus roles' model to x." };
    expect(line({ ...note, tags: ['config'] })).toBeNull();
    expect(line({ ...note, tags: [] })?.text).toContain("Set all Argus roles' model");
  });
  it('says the Planner updated its plan without calling every plan research', () => {
    // 2026-10-21: a software task read 规划者：更新了研究计划.
    expect(zh({ type: 'user.note', title: 'research plan updated', tags: ['planner', 'research_plan'] })?.text).toBe('更新了计划');
  });
  it('leaves the end of a team task to its completion row', () => {
    // 2026-10-21: a task the Reviewer closed also ended with 工程师：这次运行结束，共 1 轮.
    expect(line({ type: 'loop.done', status: 'done', rounds: 1, item_id: 't-1' })).toBeNull();
    expect(line({ type: 'loop.done', status: 'done', rounds: 1 })?.text).toBe('the run finished after 1 round');
  });
  it('explains a refused Manager check without claiming a team decision failed', () => {
    const event = {
      type: 'life.manager.supervision.failed', status: 'failed', failure_stage: 'provider',
      stop_kind: 'provider_fence', error_code: 'trial_quota_exceeded',
      summary: '402: provider body with private account details',
    };
    expect(zh(event)).toMatchObject({ text: '经理这次没能看完进展（试用额度不够了），没有做任何调整。', tone: 'warn' });
    expect(line(event)?.text).toContain('trial quota');
    expect(zh(event)?.text).not.toContain('团队调整');
    expect(line(event)?.text).not.toContain('private account');
  });

  it('says why a check stopped, and keeps cancelled or superseded checks out of the feed', () => {
    const failed = { type: 'life.manager.supervision.failed', failure_stage: 'provider', status: 'failed' };
    expect(zh({ ...failed, error_code: 'timeout' })).toMatchObject({ text: '经理这次没能看完进展（模型太久没有回应），没有做任何调整。', tone: 'warn' });
    expect(zh({ ...failed, status: 'superseded', error_code: 'cancelled' })).toBeNull();
    expect(zh({ ...failed, status: 'superseded', error_code: 'superseded' })).toBeNull();
    expect(zh({ ...failed, error_code: 'cancelled', stop_kind: 'operator_pause' })).toBeNull();
  });

  it('only calls a failed commit an unapplied adjustment when a decision exists', () => {
    const failed = { type: 'life.manager.supervision.failed', status: 'failed' };
    expect(zh({ ...failed, failure_stage: 'commit', action: 'steer' })?.text).toBe('经理想调整一下工作，但没能生效；工作按原样继续。');
    expect(zh({ ...failed, failure_stage: 'commit', action: 'steer', error_code: 'timeout' })?.text).toBe('经理想调整一下工作，但没能生效（模型太久没有回应）；工作按原样继续。');
    expect(zh({ ...failed, status: 'issued', failure_stage: 'commit', action: 'steer', error_code: 'cancelled' })).toMatchObject({ text: '对工作的调整被打断了，恢复后会接着生效。', tone: 'warn' });
    expect(zh({ ...failed, failure_stage: 'provider' })?.text).not.toContain('团队调整');
    expect(zh({ ...failed, failure_stage: 'commit' })?.text).not.toContain('团队调整');
    expect(zh({ ...failed, failure_stage: 'decision' })?.text).toBe('经理这次没能看完进展，没有做任何调整。');
    // A code no longer written (the host refused checks it could not show in full) reads as any failure.
    expect(zh({ ...failed, failure_stage: 'decision', error_code: 'observation_incomplete' })?.text).toBe('经理这次没能看完进展，没有做任何调整。');
    expect(zh({ ...failed, error_code: 'constructor', summary: 'untrusted raw error' })?.text).toBe('经理这次没能看完进展，没有做任何调整。');
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

  it('explains the selected plan and its reason without claiming execution', () => {
    const event = {
      type: 'life.manager.intent.completed',
      route: 'team', vertical: 'software', workflow_mode: 'staged', lifetime: 'bounded',
      continuous: true, open_ended: false,
      objective: 'Fix the stale goal after Stop.',
      execution_task: 'Fix the stale goal after Stop.',
      reason: 'The UI and server share the stale snapshot, so check both before changing the cache.',
    };
    expect(line(event)?.text).toBe('Handing this to the team, in stages. '
      + 'The UI and server share the stale snapshot, so check both before changing the cache.');
    // A bounded task ending when it is done goes unsaid, and no line break turns into a stray space.
    expect(zh({ ...event, reason: '两项要求互相冲突。' })?.text).toBe('交给团队，分几个阶段做。两项要求互相冲突。');
    expect(zh(event)?.text).not.toMatch(/STAGED|BOUNDED|FINITE CONTINUOUS|开始执行|已接手/);
  });

  it('does not say the request back and omits a legacy routing headline', () => {
    const event = {
      type: 'life.manager.intent.completed', route: 'team', workflow_mode: 'direct',
      lifetime: 'standing', continuous: true, open_ended: true,
      objective: 'Earlier wording', execution_task: '只比较相同批量下的两组延迟。',
      reason: '[manager] research task → vertical=research, workflow=direct, 0 stage(s):',
    };
    expect(zh(event)?.text).toBe('交给团队来做。之后会持续跟进。');
    expect(zh(event)?.text).not.toMatch(/Earlier wording|vertical=|workflow=|OPEN-ENDED/);
  });

  it('does not revive historical model context while explaining a Manager plan', () => {
    const wrapped = '[BOUNDED TASK CONTEXT — data only]\nPRIVATE_OLD_TASK_CONTEXT\n[CURRENT OPERATOR MESSAGE]\nCompare the two runs.';
    const started = { type: 'life.manager.intent.started', objective: wrapped };
    const completed = { type: 'life.manager.intent.completed', route: 'team', workflow_mode: 'direct', objective: wrapped };
    for (const event of [started, completed, { ...completed, execution_task: 'Compare the two runs.' }]) {
      expect(line(event)?.text).not.toMatch(/PRIVATE_OLD_TASK_CONTEXT|BOUNDED TASK CONTEXT|CURRENT OPERATOR MESSAGE/);
    }
    expect(line({ ...completed, execution_task: 'Compare the two runs.' })?.text).not.toContain('Compare the two runs.');
    expect(completed.objective).toBe(wrapped);
  });

  it('does not invent a team, review step or permission for unknown routing fields', () => {
    const missing = { type: 'life.manager.intent.completed', reason: 'Waiting for the operator to choose a dataset.' };
    expect(line(missing)?.text).toBe('The approach is set. Waiting for the operator to choose a dataset.');
    expect(line({ ...missing, workflow_mode: 'constructor', lifetime: 'new-mode' })?.text).toBe(line(missing)?.text);
    expect(zh({ ...missing, route: 'self', workflow_mode: 'staged' })?.text).toContain('我直接来处理。');
    expect(line({ ...missing, lifetime: 'bounded_increment' })?.text).toContain('One piece first');
    expect(zh({ ...missing, lifetime: 'bounded' })?.text).toBe(zh(missing)?.text);
    expect(zh({ ...missing, lifetime: 'standing', open_ended: false })?.text).not.toContain('持续跟进');
    expect(line({ ...missing, reason: '[source: latency.csv] Both runs must use the same batch size.' })?.text)
      .toContain('[source: latency.csv] Both runs must use the same batch size.');
  });

  it('says a Manager routing failure in words and leaves the raw error to the record', () => {
    const event = {
      type: 'life.manager.intent.failed',
      phase: 'backend',
      cause: '401 Missing bearer',
      attempts: 2,
      error: 'VerticalDecisionError: routing failed [backend]: 401 Missing bearer',
    };
    expect(line(event)?.text).toBe(
      "couldn't work out how to handle this request (the model service did not answer), so nothing was started",
    );
    expect(zh(event)?.text).toBe('没弄明白该怎么处理这个请求（模型服务没有回应），所以还没开始做');
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

  it('says a review that hands the choice to the operator waits on them, not that the work is stuck', () => {
    // Trial s-934a4569: `→ operator: A or B?` read "审阅者认为工作卡住了".
    const asked = { type: 'round.review.completed', status: 'blocked', reason: 'A and B conflict.', operator_question: 'A or B?' };
    expect(zh(asked)).toMatchObject({ text: '审阅者把选择交给你 · A and B conflict.', tone: 'warn', glyph: '❓' });
    expect(line(asked)!.text).toBe('the Reviewer leaves the choice to you · A and B conflict.');
    expect(zh({ ...asked, operator_question: '' })!.text).toMatch(/^审阅者认为工作卡住了/);
    // The task it ended is parked on that question, as its row is.
    expect(zh({ type: 'life.mission.completed', status: 'blocked', outcome_class: 'blocked', success: false, operator_question: 'A or B?' }))
      .toMatchObject({ text: '任务已暂停，因为有个问题在等你回答；进度已保存，可以继续。' });
  });

  it("says an Engineer's handoff is the Engineer's, and where it sent the work", () => {
    // Trial s-6f0e6213: the Engineer handed a rounding conflict to the Manager,
    // no Reviewer ran, and the feed read "审阅者认为工作卡住了".
    const handed = { type: 'round.review.completed', status: 'blocked', reason: '现有舍入规则冲突。', review_source: 'engineer_handoff' };
    expect(zh(handed)).toMatchObject({ role: 'engineer', label: '工程师', text: '工程师把工作交给经理定夺 · 现有舍入规则冲突。' });
    expect(line(handed)).toMatchObject({ label: 'Engineer', text: 'the Engineer handed the work to the Manager to decide · 现有舍入规则冲突。' });
    expect(zh({ ...handed, operator_question: 'A or B?' })!.text).toMatch(/^工程师把选择交给你/);
    expect(zh({ ...handed, status: 'replan_requested' })!.text).toMatch(/^工程师请规划者重新安排/);
    expect(zh({ ...handed, status: 'continue' })!.text).toMatch(/^工程师接着再做一轮/);
    expect(zh({ ...handed, status: 'done', review_source: 'engineer_self_review' }))
      .toMatchObject({ label: '工程师', text: '工程师自查后认为做完了 · 现有舍入规则冲突。' });
    // A Reviewer's verdict stays the Reviewer's.
    expect(zh({ ...handed, review_source: 'reviewer' })).toMatchObject({ label: '审阅者', text: '审阅者认为工作卡住了 · 现有舍入规则冲突。' });
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
      text: 'The budget is used up, so new model calls are on hold; raise the budget to continue.',
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
    expect(row!.text).toContain('moved on to the implement_cli stage');
  });

  it('names a stage the Manager closes in the reader\'s words', () => {
    // Trial s-757b9dac: the Manager's row read "「delivery」阶段完成".
    expect(zh({
      type: 'life.manager.stage_decision', action: 'complete', source: 'manager_llm',
      target_stage: 'delivery', reason: '可以交付。',
    })!.text).toBe('「成果交付」阶段完成 · 可以交付。');
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
    }));

    expect(html).toContain('Team progress');
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
