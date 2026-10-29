import { renderToStaticMarkup } from 'react-dom/server';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { describe, expect, it, vi } from 'vitest';
import { operatorDecisionCards } from '../../../core/src/decisions';
import { PendingReplyDialog } from '../components/PendingReplyDialog';
import { PendingBanner } from '../components/PendingBanner';

const card = {
  id: 'decision-item-1',
  item_id: 'item-1',
  revision: 1,
  status: 'pending' as const,
  title: 'Choose a fallback',
  reason: 'The primary provider refused the request.',
  question: 'Use the local implementation?',
  evidence: [{ label: 'Provider log', path: 'logs/run.txt', summary: 'refused' }],
  options_source: 'agent' as const,
  options: [
    { id: 'recommended', label: 'Use local fallback', description: 'Continue locally.' },
    { id: 'custom', label: 'Other guidance', description: 'Describe another route.' },
    { id: 'stop', label: 'Stop this campaign', description: 'Stop.' },
  ],
  selected_option: '',
  note: '',
};

describe('operator decision cards', () => {
  it('projects only server decision IDs', () => {
    const rows = operatorDecisionCards(
      [{ id: 'item-1', operator_decision: card }],
      [{ id: 'legacy', title: 'Legacy', pending_question: 'What now?' }],
    );
    expect(rows.map((row) => row.id)).toEqual(['decision-item-1']);
  });

  it('hides legacy control-plane filler while preserving the actionable question', () => {
    const [row] = operatorDecisionCards([{
      id: 'item-1',
      operator_decision: {
        ...card,
        reason: 'Engineer requires an operator-owned decision before continuing.',
        question: 'Enable Accessibility for Terminal, then retry.',
      },
    }], []);

    expect(row.reason).toBe('');
    expect(row.question).toBe('Enable Accessibility for Terminal, then retry.');
  });

  it('attributes a generic decision to its actual backlog task by item ID', () => {
    const [row] = operatorDecisionCards([{
      id: 'pending-row', title: 'Generic pending label', operator_decision: card,
    }], [{ id: 'item-1', title: 'Prepare the sleep-study manuscript', status: 'paused_operator' }], 'item-1');
    expect(row.title).toBe('Choose a fallback');
    expect(row.task_title).toBe('Prepare the sleep-study manuscript');
    const html = renderToStaticMarkup(<PendingReplyDialog reply={row} open busy={false} onClose={vi.fn()} onSubmit={vi.fn()} />);
    expect(html).toContain('Task: Prepare the sleep-study manuscript');
    expect(html).toContain('title="Prepare the sleep-study manuscript"');
    expect(html).not.toContain('Task: Generic pending label');
  });

  it('drops old Host-authored choices and renders an honest freeform answer', () => {
    const [row] = operatorDecisionCards([{
      id: 'item-1',
      operator_decision: {
        ...card,
        options_source: undefined,
        evidence: [{ label: 'Acceptance check', path: '', summary: 'internal process detail' }],
        options: [
          { id: 'recommended', label: '按建议继续', description: 'Resume after the operator answers the pending question.' },
          { id: 'custom', label: '给出其他指示', description: card.question },
          { id: 'stop', label: '保留当前结果并停止', description: 'Stop.' },
        ],
      },
    }], []);
    const html = renderToStaticMarkup(
      <PendingReplyDialog
        reply={row}
        open
        busy={false}
        onClose={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    expect(row.options.map((option) => option.id)).toEqual(['custom']);
    expect(row.evidence).toEqual([]);
    expect(html).toContain('Send answer');
    expect(html).toContain('Answer in your own words');
    expect(html).not.toContain('按建议继续');
    expect(html).not.toContain('保留当前结果并停止');
  });

  it('renders reason, evidence, options, and stop action', () => {
    const html = renderToStaticMarkup(
      <PendingReplyDialog
        reply={card}
        open
        busy={false}
        onClose={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );
    expect(html).toContain('Why we&#x27;re asking');
    expect(html).toContain('Provider log');
    expect(html).toContain('Use local fallback');
    expect(html).toContain('Stop this campaign');
    // The written answer is the text box, not one more button.
    expect(html).not.toContain('Other guidance');
  });

  it('prioritizes the current task while retaining other unresolved questions unchanged', () => {
    const pending = [
      { id: 'old', title: 'Old planner question', status: 'paused_operator', pending_question: 'Recorded 429: trial_tpm_exceeded' },
      { id: 'another', title: 'Another question', status: 'paused_operator', pending_question: 'Choose a route' },
      { id: 'current', title: 'Submission facts', status: 'paused_operator', pending_question: 'Supply real author details' },
    ];
    for (const row of pending) Object.assign(row, { operator_decision: { id: `decision-${row.id}`, item_id: row.id, title: row.title, question: row.pending_question, status: 'pending' } });
    const before = JSON.stringify(pending);
    const rows = operatorDecisionCards(pending, [], 'current');

    expect(rows.map(row => row.item_id)).toEqual(['current', 'old', 'another']);
    expect(rows.map(row => row.status)).toEqual(['pending', 'pending', 'pending']);
    expect(rows[1].question).toContain('trial_tpm_exceeded');
    expect(JSON.stringify(pending)).toBe(before);
    expect(operatorDecisionCards(pending, [], 'missing').map(row => row.item_id)).toEqual(['old', 'another', 'current']);

    const html = renderToStaticMarkup(<PendingBanner questions={pending} backlog={[]} currentTaskId="current" onAnswer={vi.fn()} />);
    expect(html).toContain('Submission facts');
    expect(html).not.toContain('Current task');
    expect(html).not.toContain('Awaiting your reply');
    expect(html).not.toContain('Paused');
    expect(html).toContain('+2');
    expect(html).not.toContain('trial_tpm_exceeded');
  });

  it('uses recorded question time in the shared banner and dialog without inferring it from task dates', () => {
    const askedAt = 1789158761;
    const pending = [{
      id: 'item-1', title: card.title, status: 'paused_operator', ts: 100,
      started_ts: 200, finished_ts: 300, operator_decision: { ...card, asked_at: askedAt },
    }];
    const running = [{ id: 'another-task', title: 'Another task', objective: '', status: 'running', priority: 0 }];
    const [row] = operatorDecisionCards(pending, running, 'another-task');
    expect(row.asked_at).toBe(askedAt);
    const dialog = renderToStaticMarkup(<PendingReplyDialog reply={row} open busy={false} onClose={vi.fn()} onSubmit={vi.fn()} />);
    const banner = renderToStaticMarkup(<PendingBanner questions={pending} backlog={running} currentTaskId="another-task" onAnswer={vi.fn()} />);
    for (const html of [dialog, banner]) {
      expect(html).toContain(card.title);
      expect(html).not.toContain('Another task awaiting input');
      expect(html).not.toContain('Awaiting your reply');
      expect(html).not.toContain('Paused');
      expect(html).toContain(`dateTime="${new Date(askedAt * 1000).toISOString()}"`);
    }
    expect(dialog).not.toContain('before work resumes');
    const [unknown] = operatorDecisionCards([{ ...pending[0], operator_decision: card }], []);
    expect(unknown.asked_at).toBeUndefined();
    const unknownHtml = renderToStaticMarkup(<PendingReplyDialog reply={unknown} open busy={false} onClose={vi.fn()} onSubmit={vi.fn()} />);
    expect(unknownHtml).not.toContain('<time');
  });

  it('asks the question in the banner and leaves the reason to the card it opens', () => {
    // 2026-10-21: the banner showed why the team asked, and the question itself only in the card.
    const html = renderToStaticMarkup(<PendingBanner questions={[{ id: 'item-1', operator_decision: card }]} backlog={[]} onAnswer={vi.fn()} />);
    expect(html).toContain(card.question);
    expect(html).not.toContain(card.reason);
  });

  it('answers with one click on an option, or with the words typed instead', () => {
    const [row] = operatorDecisionCards([{ id: 'item-1', operator_decision: {
      ...card,
      options: [
        { id: 'option-1', label: 'Keep the v1 API', description: '' },
        { id: 'option-2', label: 'Ship the v2 schema', description: '' },
      ],
    } }], []);
    const onSubmit = vi.fn();
    let view!: ReactTestRenderer;
    act(() => { view = create(<PendingReplyDialog reply={row} open busy={false} onClose={vi.fn()} onSubmit={onSubmit} />); });
    const said = (node: ReactTestInstance): string => node.children
      .map((child) => typeof child === 'string' ? child : said(child)).join('');
    const button = (label: string) => view.root.findAllByType('button').find((node) => said(node) === label)!;

    act(() => button('Ship the v2 schema').props.onClick());
    expect(onSubmit).toHaveBeenLastCalledWith('option-2', '');

    // A written answer needs words; nothing is sent without them.
    act(() => button('Send answer').props.onClick());
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(view.root.findByProps({ role: 'alert' })).toBeTruthy();

    act(() => view.root.findByType('textarea').props.onChange({ target: { value: 'Keep v1 until June' } }));
    act(() => button('Send answer').props.onClick());
    expect(onSubmit).toHaveBeenLastCalledWith('custom', 'Keep v1 until June');
    act(() => view.unmount());
  });
});

it('renders a workflow choice as options with optional details and no blocked-task language', () => {
  const [row] = operatorDecisionCards([{ operator_decision: {
    ...card, id: 'intake-1', kind: 'domain_intake', item_id: '', options_source: 'workflow',
    title: 'Choose how to proceed', task_title: 'Interpret a calendar date', reason: '', evidence: [],
    options: [{ id: 'direct', label: 'Do it directly', description: 'One agent.' },
      { id: 'build', label: 'Build a specialist workflow', description: 'Clarify and research.' }],
  } }], [], 'old-task');
  const html = renderToStaticMarkup(<PendingReplyDialog reply={row} open busy={false} error="Please retry" onClose={vi.fn()} onSubmit={vi.fn()} />);
  expect(row.kind).toBe('domain_intake');
  expect(html).toContain('Choose how to proceed');
  expect(html).toContain('Do it directly');
  expect(html).toContain('Build a specialist workflow');
  expect(html).toContain('Additional details (optional)');
  expect(html).toContain('Please retry');
  expect(html).not.toContain('Decision required');
  expect(html).not.toContain('Paused');
});
