import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { operatorDecisionCards } from '../../../core/src/decisions';
import { PendingReplyDialog } from '../components/PendingReplyDialog';

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
    { id: 'recommended', label: 'Use local fallback', description: 'Continue locally.', requires_note: false },
    { id: 'custom', label: 'Other guidance', description: 'Describe another route.', requires_note: true },
    { id: 'stop', label: 'Stop this campaign', description: 'Stop.', requires_note: false },
  ],
  selected_option: '',
  note: '',
};

describe('operator decision cards', () => {
  it('projects typed and legacy pending questions', () => {
    const rows = operatorDecisionCards(
      [{ id: 'item-1', operator_decision: card }],
      [{ id: 'legacy', title: 'Legacy', pending_question: 'What now?' }],
    );
    expect(rows.map((row) => row.id)).toEqual(['decision-item-1', 'legacy-legacy']);
    expect(rows[0].options[1].requires_note).toBe(true);
    expect(rows[1].legacy).toBe(true);
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

  it('drops old Host-authored choices and renders an honest freeform answer', () => {
    const [row] = operatorDecisionCards([{
      id: 'item-1',
      operator_decision: {
        ...card,
        options_source: undefined,
        evidence: [{ label: 'Acceptance check', path: '', summary: 'internal process detail' }],
        options: [
          { id: 'recommended', label: '按建议继续', description: 'Resume after the operator answers the pending question.', requires_note: false },
          { id: 'custom', label: '给出其他指示', description: card.question, requires_note: true },
          { id: 'stop', label: '保留当前结果并停止', description: 'Stop.', requires_note: false },
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
    expect(html).toContain('Write my own answer');
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
    expect(html).toContain('Why work is blocked');
    expect(html).toContain('Provider log');
    expect(html).toContain('Use local fallback');
    expect(html).toContain('Stop this campaign');
  });

  it('keeps note-required choices clickable so validation can explain the requirement', () => {
    const html = renderToStaticMarkup(
      <PendingReplyDialog
        reply={{
          ...card,
          options: [{
            id: 'needs-note',
            label: 'Use another format',
            description: 'Describe the format.',
            requires_note: true,
          }],
        }}
        open
        busy={false}
        onClose={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    expect(html).toContain('Use this option');
    expect(html).not.toContain('disabled=""');
  });
});
