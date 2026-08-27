import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { CounterexampleLab } from '../components/CounterexampleLab';
import type { CounterexampleConjecture } from '../lib/counterexampleProgress';

const conjectures: CounterexampleConjecture[] = [
  {
    id: 'toeplitz',
    title: 'Berger–Coburn boundedness conjecture',
    shortTitle: 'Berger–Coburn',
    field: 'Operator theory',
    statement: 'Boundedness of the Toeplitz operator is equivalent to boundedness of its heat transform.',
    status: 'refuted',
    progress: 100,
    stages: [{ id: 'review', label: 'Published refutation review', status: 'completed' }],
    evidence: [{ id: 'paper', title: 'Published construction', status: 'verified', kind: 'paper' }],
  },
  {
    id: 'jacobian',
    title: 'Jacobian conjecture candidate',
    field: 'Algebraic geometry',
    statement: 'Search for a low-dimensional polynomial-map witness.',
    status: 'active',
    active: true,
    live: true,
    updatedAt: '2026-08-27T12:00:00Z',
    currentStageId: 'construct',
    activity: {
      actor: 'Argus Engineer',
      label: 'Testing polynomial map families',
      detail: 'Jacobian is checking determinant and generic-degree constraints.',
    },
    stages: [
      { id: 'align', label: 'Statement alignment', status: 'completed' },
      { id: 'construct', label: 'Witness construction', status: 'running', progress: 35 },
      { id: 'review', label: 'Reviewer certification', status: 'pending' },
    ],
    evidence: [
      { id: 'catalog', title: 'Jacobian operation catalog', status: 'verified', kind: 'computation' },
      { id: 'family', title: 'Degree-three map family', status: 'candidate', kind: 'counterexample' },
    ],
  },
];

describe('CounterexampleLab', () => {
  it('renders a switchable list with live, active, progress, stage, and evidence state', () => {
    const markup = renderToStaticMarkup(
      <CounterexampleLab
        conjectures={conjectures}
        selectedConjectureId="jacobian"
        now={Date.parse('2026-08-27T12:00:30Z')}
      />,
    );

    expect(markup).toContain('Counterexample Lab');
    expect(markup).toContain('Berger–Coburn');
    expect(markup).toContain('Jacobian conjecture candidate');
    expect(markup).toContain('aria-pressed="true"');
    expect(markup).toContain('data-live="true"');
    expect(markup).toContain('Live feed');
    expect(markup).toContain('Testing polynomial map families');
    expect(markup).toContain('Witness construction');
    expect(markup).toContain('Degree-three map family');
    expect(markup).toContain('role="progressbar"');
  });

  it('uses the controlled selection to show a different conjecture evidence ledger', () => {
    const markup = renderToStaticMarkup(
      <CounterexampleLab conjectures={conjectures} selectedConjectureId="toeplitz" />,
    );

    expect(markup).toContain('Published refutation review');
    expect(markup).toContain('Published construction');
    expect(markup).not.toContain('Testing polynomial map families');
    expect(markup).not.toContain('Degree-three map family');
  });

  it('renders an explicit empty state without requiring an API', () => {
    const markup = renderToStaticMarkup(
      <CounterexampleLab conjectures={[]} emptyMessage="Waiting for the first candidate." />,
    );

    expect(markup).toContain('Waiting for the first candidate.');
    expect(markup).toContain('Snapshot');
  });
});
