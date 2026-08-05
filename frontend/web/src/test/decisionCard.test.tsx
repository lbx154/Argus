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
    expect(rows[1].legacy).toBe(true);
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
});
